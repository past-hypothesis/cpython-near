#!/usr/bin/env python3
"""
CPython-NEAR WASM optimizer tool

Allows packaging arbitray Python modules together with the contract source files into a self-contained WASM file ready for
running on the NEAR blockchain.

Inputs:
  python.wasm -- pre-compiled CPython-NEAR WASM
  python-stdlib.zip -- pre-compiled CPython-NEAR stdlib .pyc files
  lib/ -- directory with contract source files and arbitrary Python modules to package into the WASM file
  
Optimizations:  
  Optimizer runs the supplied contract with default arguments (or ones specified via @near.optimizer_inputs() decorator) and traces
  which Python modules and WASM functions get loaded/called during the runtime, removing unreferenced ones according to the 
  optimization settings specified via -Ox, --module-tracing=1/0, --function-tracing=off/safe/aggressive cmdline arguments.
  
  --function-tracing=safe (-O2 and lower) tries to only remove WASM functions which belong to non-referenced builtin Python modules, 
  which is safer than --function-tracing=aggressive (-O3 and higher), which removes all unreferenced WASM functions except those pinned 
  via DEFAULT_PINNED_FUNCTIONS or --pinned-functions=<name1>,<name2>,...
  
  Optimized out WASM functions are replaced with a panic handler, which will, in case such a function has still been called during 
  the contract runtime, print a message including the missing function name, which then can be added to the pinned function name list
  to ensure it is retained.
  
  Additionally, LZ4 compression can be applied to WASM data initializer, which allows up to 500KiB WASM size reduction while consuming
  ~20Tgas for decompression.
  
  Typical WASM sizes after optimization with json module included (-O4/3/2): 530/560/1363KiB.
  
Dependencies: 
  wasmtime-py https://github.com/bytecodealliance/wasmtime-py
  binaryen https://github.com/WebAssembly/binaryen
"""

import wasmtime
from collections import defaultdict
import subprocess
import zipfile
import argparse
import struct
from pathlib import Path
from copy import deepcopy
import sys
import lz4.frame
import ast
import json
import shutil

DEFAULT_PINNED_FUNCTIONS = ["_Py_Dealloc", "PyObject_ClearWeakRefs", "PyObject_ClearManagedDict", "clear_inline_values",
                            "is_basic_ref_or_proxy", "_PyWeakref_GetWeakrefCount", "set_len", "_weakref__remove_dead_weakref",
                            "is_dead_weakref", "clear_slots", "PyObject_DelItem", "lock_dealloc", "_PySuper_Lookup",
                            "wrapperdescr_call", "_PyObject_RealIsSubclass", "recursive_issubclass", "Py_Exit", "exit",
                            "close_file", "_Exit", "__wasi_proc_exit", "setvbuf", "_PyErr_NoMemory", 
                            "optimized_out_function_panic_handler", "snprintf", "log_utf8_c", "strlen", "log_utf8", "abort",
                            "decompress_data_initializer", "pymain_init", "_Py_GetErrorHandler", "siprintf", "vfiprintf", "iprintf",
                            # these are necessary for exception/traceback printing (~8KiB WASM size impact)
                            "PyErr_SetString", "_PyErr_SetString", "BaseException_vectorcall",
                            "PyErr_PrintEx", "_PyErr_PrintEx", "handle_system_exit", "_PySys_GetOptionalAttr",
                            "_PySys_Audit", "cfunction_vectorcall_FASTCALL", "sys_excepthook", "PyErr_Display",
                            "_PyErr_Display", "PyImport_ImportModuleAttrString", "PyImport_ImportModuleAttr",
                            "_PyErr_Format", "meth_repr", "puts", "fputs", "fwrite", "__overflow", "BaseException_dealloc",
                            "PySet_New", "PySet_Add", "print_exception_recursive", "_Py_EnterRecursiveCall",
                            "PyException_GetCause", "PyException_GetContext", "PyObject_GetOptionalAttr", "_Py_type_getattro",
                            "_Py_type_getattro_impl", "PyDescr_IsData", "getset_get", "type_get_module", "PyType_GetQualName",
                            "PyFile_WriteObject", "PyObject_GenericGetAttr", "method_get", "stdprinter_write", "_Py_write",
                            "_Py_write_impl", "_PySys_SetAttr", "PyEval_SaveThread", "_PyThreadState_Detach", "detach_thread",
                            "_PyEval_ReleaseLock", "drop_gil", "drop_gil_impl", "write", "PyEval_RestoreThread",
                            "BaseException_str", "PyUnicode_GetLength", "PyFile_WriteString", "set_dealloc", "_PyFile_Flush",
                            "PyObject_CallMethodNoArgs", "PyObject_VectorcallMethod", "method_vectorcall_NOARGS"]

BUILTIN_MODULE_FUNCTION_NAME_PREFIXES = {
    "array": ["array"],
    "_bisect": ["_bisect"],
    "_contextvars": ["_contextvars"],
    "_heapq": ["_heapq"],
    "_json": ["_json"],
    "_queue": ["_queue"],
    "_random": ["_random"],
    "_struct": ["Struct_", "unpackiter_"],
    "math": ["math"],
    "cmath": ["cmath"],
    "_statistics": ["_statistics"],
    "_decimal": ["_decimal"],
    "binascii": ["binascii"],
    "_md5": ["md5_", "MD5_", "MD5Type_", "Hacl_Hash_MD5", "python_hashlib_Hacl_Hash_MD5"],
    "_sha1": ["_sha1", "SHA1", "Hacl_Hash_SHA1", "python_hashlib_Hacl_Hash_SHA1"],
    "_sha2": ["_sha2", "SHA2", "SHA256", "SHA512", "Hacl_Hash_SHA2", "python_hashlib_Hacl_Hash_SHA2"],
    "_sha3": ["_sha3", "SHA3", "py_sha3", "Hacl_Hash_SHA3", "python_hashlib_Hacl_Hash_SHA3"],
    "termios": ["termios"],
    "atexit": ["atexit"],
    "faulthandler": ["faulthandler"],
    "posix": ["posix"],
    "_signal": ["_signal_", "signal_", "signaldict"],
    "_codecs": ["_codecs"],
    "_collections": ["_collections"],
    "errno": ["errno"],
    "_io": ["_io"],
    "itertools": ["itertools"],
    "_sre": ["_sre_", "sre_"],
    "_sysconfig": ["_sysconfig"],
    "_thread": ["_thread"],
    "time": ["time", "pytime"],
    "_typing": ["_typing"],
    "_weakref": ["_weakref"],
    "_abc": ["_abc"],
    "_functools": ["_functools"],
    "_operator": ["_operator"],
    "marshal": ["marshal_", "PyMarshal_"],
    "_ast": ["_PyAST", "PyAST", "obj2ast", "ast2obj", "_ast", "ast_", "astfold_", "astmodule_"],
    "_asyncio": ["_asyncio", "task_", "Task", "FutureIter"],
    "_tokenize": ["_tokenize", "tokenize"],
    "_warnings": ["_PyWarnings"],
    "_string": ["_string", "_PyUnicode", "PyUnicode", "unicode"],
}

SAFELY_REMOVABLE_FUNCTION_NAME_PREFIXES = [
    "_complex", "complex_", "PyComplex", "ucs4lib", "ucs2lib", "anextawaitable_", "coro_", "async_", "gen_", "ag_",
    "SyntaxError", "compiler", "_PyPegen", "_Pypegen", "_PyTokenizer", "_PyLexer", "builtin_compile", "_parser",
    "_PyParser", "InstructionSequence", "_PyInstructionSequence", "code_", "_PyCode", "_PyCompile", "PyCompile",
    "Py_Compile", "assemble_", "PyEval", "_PyEval", "builtin_eval", "validate_", "_PyMem_Debug", "_Py_Dump",
    "PySys", "PyOS", "OSError_", "oserror_", "symtable_", "PySymtable_", "_PySymtable", "sys_trace", 
    "_PyMonitoring", "monitoring", "force_instrument", "_Py_call_instr", "_Py_Instr", "_start", "PyPickle", "pickle", "xml"
]

SAFELY_REMOVABLE_FUNCTION_NAME_SUFFIXES = [
    "_rule"
]

_LPAREN = object()
_RPAREN = object()

def tokenize_sexp(s):
    tokens = []
    current_atom = []
    in_atom = False
    in_string = False
    in_comment = False
    i = 0
    n = len(s)

    while i < n:
        char = s[i]
        if in_string:
            if char == '\\':
                # Handle escape sequences, preserving them as-is except for \" and \\
                if i + 1 >= n:
                    raise ValueError("Unclosed escape sequence at end of string")
                next_char = s[i+1]
                if next_char in ('"', '\\'):
                    # Preserve the escape sequence as-is (e.g., \" becomes \", \\ becomes \\)
                    current_atom.append(char + next_char)
                    i += 2
                else:
                    # Preserve the backslash and next character (e.g., \n becomes \n)
                    current_atom.append(char)
                    current_atom.append(next_char)
                    i += 2
            elif char == '"':
                # End of string, add the collected content as a single token
                tokens.append('"' + (''.join(current_atom)) + '"')
                current_atom = []
                in_string = False
                i += 1
            else:
                current_atom.append(char)
                i += 1
        elif in_comment:
            if char == '\n':
                in_comment = False
            i += 1
        else:
            if char == '"':
                # Start of string
                in_string = True
                current_atom = []
                i += 1
            elif char.isspace():
                if in_atom:
                    tokens.append(''.join(current_atom))
                    current_atom = []
                    in_atom = False
                i += 1
            elif char in '()':
                if in_atom:
                    tokens.append(''.join(current_atom))
                    current_atom = []
                    in_atom = False
                tokens.append(_LPAREN if char == '(' else _RPAREN)
                i += 1
            elif char == ';':
                in_comment = True
                i += 1
            else:
                in_atom = True
                current_atom.append(char)
                i += 1
    if in_string:
        raise ValueError("Unclosed string")
    if current_atom:
        tokens.append(''.join(current_atom))
    return tokens

def parse_sexp(tokens) -> list:
    stack = []
    root = []
    for token in tokens:
        if token is _LPAREN:
            new_list = []
            if stack:
                stack[-1].append(new_list)
            else:
                root = new_list
            stack.append(new_list)
        elif token is _RPAREN:
            if not stack:
                raise ValueError("Unexpected ')'")
            stack.pop()
        else:
            if not stack:
                raise ValueError("Atom outside of list")
            stack[-1].append(token)
    if stack:
        raise ValueError("Unclosed '('")
    return root

def read_sexp(filename) -> list:
    with open(filename, 'r', encoding='ascii') as f:
        data = f.read()
    tokens = tokenize_sexp(data)
    return parse_sexp(tokens)

def write_sexp_to_string(expr):
    def helper(e):
        if isinstance(e, list):
            return '(' + ' '.join(helper(child) for child in e) + ')'
        else:
            return str(e)
    return helper(expr)

def write_sexp(expr, filename):
    with open(filename, 'w', encoding='ascii') as f:
        sexp_str = write_sexp_to_string(expr)
        f.write(sexp_str)


def fnv1a_32(data):
    hash_val = 0x811c9dc5  # FNV offset basis
    for byte in data.encode('ascii'):
        hash_val ^= byte
        hash_val = (hash_val * 0x01000193) & 0xffffffff  # FNV prime
    return hash_val & 0xffffffff


class WasmRunner:        
    def __init__(self, wasm_bytes: bytes):
        self.wasm_bytes = wasm_bytes
        self.input_bytes = b''
        self.called_functions = set[int]()
        self.loaded_frozen_modules = set[str]()
        self.loaded_builtin_modules = set[str]()
        self.engine = wasmtime.Engine()
        self.store = wasmtime.Store(self.engine)
        self.module = wasmtime.Module(self.engine, self.wasm_bytes)
        self.memory = None
        imports = self.create_host_imports()
        self.linker = wasmtime.Linker(self.engine)
        for name, func in imports.items():
            self.linker.define(self.store, "env", name, func)
        self.reset()
        
    def reset(self):
        self.instance = self.linker.instantiate(self.store, self.module)
        self.memory = self.instance.exports(self.store)["memory"]

    def set_input_bytes(self, input_bytes: bytes):
        self.input_bytes = input_bytes
    
    def export(self, name):
        exported_func = self.instance.exports(self.store)[name]
        def exported_func_wrapper(*args):
            return exported_func(self.store, *args)
        return exported_func_wrapper
    
    def data_ptr(self):
        return self.memory.data_ptr(self.store)

    def create_host_imports(self) -> dict:
        # Auxilary functions which are not part of the NEAR API
        def c_str_from_ptr(ptr: int) -> str:
            chars = []
            data = self.memory.data_ptr(self.store)
            for i in range(ptr, ptr + 4096):
                if data[i] == 0:
                    break
                chars.append(data[i])
            return bytes(chars).decode()

        def bytes_from_ptr(ptr: int, length: int) -> bytes:
            data = self.memory.data_ptr(self.store)
            return bytes(data[ptr:ptr+length])

        def str_from_ptr(ptr: int, length: int) -> str:
            return bytes_from_ptr(ptr, length).decode()

        def trace_function_call(function_name_hash: int) -> None:
            self.called_functions.add(function_name_hash & 0xffffffff)

        def trace_frozen_module_load(module_path_ptr: int) -> None:
            module_path = c_str_from_ptr(module_path_ptr)
            self.loaded_frozen_modules.add(module_path)

        def trace_builtin_module_load(module_name_ptr: int) -> None:
            module_name = c_str_from_ptr(module_name_ptr)
            self.loaded_builtin_modules.add(module_name)

        # NEAR API impl
        registers = defaultdict(bytes)
        storage = defaultdict(bytes)

        # Registers
        def read_register(register_id: int, ptr: int) -> None:
            data_bytes = registers[register_id]
            data = self.memory.data_ptr(self.store)
            for i, byte in enumerate(data_bytes):
                data[ptr + i] = byte

        def register_len(register_id: int) -> int:
            return len(registers[register_id])

        def write_register(register_id: int, length: int, ptr: int) -> None:
            registers[register_id] = bytes_from_ptr(ptr, length)
            
        # Context API
        def current_account_id(register_id: int) -> None:
            registers[register_id] = b'wasm.optimizer'
            
        def signer_account_pk(register_id: int) -> None:
            registers[register_id] = b'wasm.optimizer_pk=='

        def signer_account_id(register_id: int) -> None:
            registers[register_id] = b'wasm.optimizer_signer'

        def predecessor_account_id(register_id: int) -> None:
            registers[register_id] = b'wasm.optimizer_predecessor'

        def input(register_id: int) -> None:
            registers[register_id] = self.input_bytes

        def block_index() -> int:
            return 200000000

        def block_timestamp() -> int:
            return 1700000000000000000

        def epoch_height() -> int:
            return 200000000

        def storage_usage() -> int:
            return 100000

        # Economics API
        def account_balance(register_id: int) -> None:
            registers[register_id] = (99800000000000000000000000).to_bytes(16, 'little')

        def account_locked_balance(register_id: int) -> None:
            registers[register_id] = (100000000000000).to_bytes(16, 'little')

        def attached_deposit(register_id: int) -> None:
            registers[register_id] = (100000000000000).to_bytes(16, 'little')

        def prepaid_gas() -> int:
            return 300000000000000

        def used_gas() -> int:
            return 10000000000000

        # Math API
        def random_seed(register_id: int) -> None:
            registers[register_id] = (100000000000000000000000000).to_bytes(32, 'little')

        def sha256(length: int, ptr: int, register_id: int) -> None:
            pass

        def keccak256(value_len: int, value_ptr: int, register_id: int) -> None:
            pass

        def keccak512(value_len: int, value_ptr: int, register_id: int) -> None:
            pass

        def ripemd160(value_len: int, value_ptr: int, register_id: int) -> None:
            pass

        def ecrecover(hash_len: int, hash_ptr: int, sig_len: int, sig_ptr: int, v: int, malleability_flag: int, register_id: int) -> int:
            pass

        def ed25519_verify(sig_len: int, sig_ptr: int, msg_len: int, msg_ptr: int, pub_key_len: int, pub_key_ptr: int) -> int:
            pass

        # Miscellaneous API
        def value_return(value_len: int, value_ptr: int) -> None:
            print(f"value_return: {value_len} bytes: {bytes_from_ptr(value_ptr, value_len)}")

        def panic() -> None:
            print(">>panic")

        def panic_utf8(length: int, ptr: int) -> None:
            print(f">>panic: {str_from_ptr(ptr, length)}")

        def log_utf8(length: int, ptr: int) -> None:
            msg = str_from_ptr(ptr, length).replace('\n', '\n>>')
            print(f">>{msg}")

        def log_utf16(length: int, ptr: int) -> None:
            print(f"log_utf16: not implemented")

        def abort(msg_ptr: int, filename_ptr: int, line: int, col: int) -> None:
            print(f"abort: {c_str_from_ptr(msg_ptr)} at {c_str_from_ptr(filename_ptr)}@{line}:{col}")

        # Promises API
        def promise_create(account_id_len: int, account_id_ptr: int, function_name_len: int, function_name_ptr: int, arguments_len: int, arguments_ptr: int, amount_ptr: int, gas: int) -> int:
            return 0

        def promise_then(promise_index: int, account_id_len: int, account_id_ptr: int, function_name_len: int, function_name_ptr: int, arguments_len: int, arguments_ptr: int, amount_ptr: int, gas: int) -> int:
            return 0

        def promise_and(promise_idx_ptr: int, promise_idx_count: int) -> int:
            return 0

        def promise_batch_create(account_id_len: int, account_id_ptr: int) -> int:
            return 0

        def promise_batch_then(promise_index: int, account_id_len: int, account_id_ptr: int) -> int:
            return 0

        # Promise API actions
        def promise_batch_action_create_account(promise_index: int) -> None:
            pass

        def promise_batch_action_deploy_contract(promise_index: int, code_len: int, code_ptr: int) -> None:
            pass

        def promise_batch_action_function_call(promise_index: int, function_name_len: int, function_name_ptr: int, arguments_len: int, arguments_ptr: int, amount_ptr: int, gas: int) -> None:
            pass

        def promise_batch_action_function_call_weight(promise_index: int, function_name_len: int, function_name_ptr: int, arguments_len: int, arguments_ptr: int, amount_ptr: int, gas: int, weight: int) -> None:
            pass

        def promise_batch_action_transfer(promise_index: int, amount_ptr: int) -> None:
            pass

        def promise_batch_action_stake(promise_index: int, amount_ptr: int, public_key_len: int, public_key_ptr: int) -> None:
            pass

        def promise_batch_action_add_key_with_full_access(promise_index: int, public_key_len: int, public_key_ptr: int, nonce: int) -> None:
            pass

        def promise_batch_action_add_key_with_function_call(promise_index: int, public_key_len: int, public_key_ptr: int, nonce: int, allowance_ptr: int, receiver_id_len: int, receiver_id_ptr: int, function_names_len: int, function_names_ptr: int) -> None:
            pass

        def promise_batch_action_delete_key(promise_index: int, public_key_len: int, public_key_ptr: int) -> None:
            pass

        def promise_batch_action_delete_account(promise_index: int, beneficiary_id_len: int, beneficiary_id_ptr: int) -> None:
            pass

        def promise_yield_create(function_name_len: int, function_name_ptr: int, arguments_len: int, arguments_ptr: int, gas: int, gas_weight: int, register_id: int) -> int:
            return 0

        def promise_yield_resume(data_id_len: int, data_id_ptr: int, payload_len: int, payload_ptr: int) -> int:
            return 0

        # Promise API results
        def promise_results_count() -> int:
            return 0

        def promise_result(result_idx: int, register_id: int) -> int:
            return 0

        def promise_return(promise_id: int) -> None:
            pass

        # Storage API
        def storage_write(key_len: int, key_ptr: int, value_len: int, value_ptr: int, register_id: int) -> int:
            storage[bytes_from_ptr(key_ptr, key_len)] = bytes_from_ptr(value_ptr, value_len)
            return 1

        def storage_read(key_len: int, key_ptr: int, register_id: int) -> int:
            key = bytes_from_ptr(key_ptr, key_len)
            if key in storage:
                registers[register_id] = storage[key]
                return 1
            return 0

        def storage_remove(key_len: int, key_ptr: int, register_id: int) -> int:
            key = bytes_from_ptr(key_ptr, key_len)
            if key in storage:
                del storage[key]
            return 1

        def storage_has_key(key_len: int, key_ptr: int) -> int:
            key = bytes_from_ptr(key_ptr, key_len)
            return 1 if key in storage else 0

        # Validator API
        def validator_stake(account_id_len: int, account_id_ptr: int, stake_ptr: int) -> None:
            pass

        def validator_total_stake(stake_ptr: int) -> None:
            pass

        # Alt BN128
        def alt_bn128_g1_multiexp(value_len: int, value_ptr: int, register_id: int) -> None:
            pass

        def alt_bn128_g1_sum(value_len: int, value_ptr: int, register_id: int) -> None:
            pass

        def alt_bn128_pairing_check(value_len: int, value_ptr: int) -> int:
            return 0

        func = wasmtime.Func
        ft = wasmtime.FuncType
        vt = wasmtime.ValType

        env_imports = {
            # Auxilary functions which are not part of the NEAR API
            "trace_function_call": func(self.store, ft([vt.i32()], []), trace_function_call),
            "trace_frozen_module_load": func(self.store, ft([vt.i32()], []), trace_frozen_module_load),
            "trace_builtin_module_load": func(self.store, ft([vt.i32()], []), trace_builtin_module_load),            

            # Registers
            "read_register": func(self.store, ft([vt.i64(), vt.i64()], []), read_register),
            "register_len": func(self.store, ft([vt.i64()], [vt.i64()]), register_len),
            "write_register": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), write_register),

            # Context API
            "current_account_id": func(self.store, ft([vt.i64()], []), current_account_id),
            "signer_account_pk": func(self.store, ft([vt.i64()], []), signer_account_pk),
            "signer_account_id": func(self.store, ft([vt.i64()], []), signer_account_id),
            "predecessor_account_id": func(self.store, ft([vt.i64()], []), predecessor_account_id),
            "input": func(self.store, ft([vt.i64()], []), input),
            "block_index": func(self.store, ft([], [vt.i64()]), block_index),
            "block_timestamp": func(self.store, ft([], [vt.i64()]), block_timestamp),
            "epoch_height": func(self.store, ft([], [vt.i64()]), epoch_height),
            "storage_usage": func(self.store, ft([], [vt.i64()]), storage_usage),

            # Economics API        
            "account_balance": func(self.store, ft([vt.i64()], []), account_balance),
            "account_locked_balance": func(self.store, ft([vt.i64()], []), account_locked_balance),
            "attached_deposit": func(self.store, ft([vt.i64()], []), attached_deposit),
            "prepaid_gas": func(self.store, ft([], [vt.i64()]), prepaid_gas),
            "used_gas": func(self.store, ft([], [vt.i64()]), used_gas),

            # Math API        
            "random_seed": func(self.store, ft([vt.i64()], []), random_seed),
            "sha256": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), sha256),
            "keccak256": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), keccak256),
            "keccak512": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), keccak512),
            "ripemd160": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), ripemd160),
            "ecrecover": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64()], [vt.i64()]), ecrecover),
            "ed25519_verify": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64()], [vt.i64()]), ed25519_verify),

            # Miscellaneous API
            "value_return": func(self.store, ft([vt.i64(), vt.i64()], []), value_return),
            "panic": func(self.store, ft([], []), panic),
            "panic_utf8": func(self.store, ft([vt.i64(), vt.i64()], []), panic_utf8),
            "log_utf8": func(self.store, ft([vt.i64(), vt.i64()], []), log_utf8),
            "log_utf16": func(self.store, ft([vt.i64(), vt.i64()], []), log_utf16),
            "abort": func(self.store, ft([vt.i32(), vt.i32(), vt.i32(), vt.i32()], []), abort),
            
            # Promises API
            "promise_create": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64()], [vt.i64()]), promise_create),
            "promise_then": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64()], [vt.i64()]), promise_then),
            "promise_and": func(self.store, ft([vt.i64(), vt.i64()], [vt.i64()]), promise_and),
            "promise_batch_create": func(self.store, ft([vt.i64(), vt.i64()], [vt.i64()]), promise_batch_create),
            "promise_batch_then": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], [vt.i64()]), promise_batch_then),

            # Promise API actions
            "promise_batch_action_create_account": func(self.store, ft([vt.i64()], []), promise_batch_action_create_account),
            "promise_batch_action_deploy_contract": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), promise_batch_action_deploy_contract),
            "promise_batch_action_function_call": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64()], []), promise_batch_action_function_call),
            "promise_batch_action_function_call_weight": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64()], []), promise_batch_action_function_call_weight),
            "promise_batch_action_transfer": func(self.store, ft([vt.i64(), vt.i64()], []), promise_batch_action_transfer),
            "promise_batch_action_stake": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64()], []), promise_batch_action_stake),
            "promise_batch_action_add_key_with_full_access": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64()], []), promise_batch_action_add_key_with_full_access),
            "promise_batch_action_add_key_with_function_call": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64()], []), promise_batch_action_add_key_with_function_call),
            "promise_batch_action_delete_key": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), promise_batch_action_delete_key),
            "promise_batch_action_delete_account": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), promise_batch_action_delete_account),
            "promise_yield_create": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64()], [vt.i64()]), promise_yield_create),
            "promise_yield_resume": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64()], [vt.i32()]), promise_yield_resume),

            # Promise API results
            "promise_results_count": func(self.store, ft([], [vt.i64()]), promise_results_count),
            "promise_result": func(self.store, ft([vt.i64(), vt.i64()], [vt.i64()]), promise_result),
            "promise_return": func(self.store, ft([vt.i64()], []), promise_return),

            # Storage API
            "storage_write": func(self.store, ft([vt.i64(), vt.i64(), vt.i64(), vt.i64(), vt.i64()], [vt.i64()]), storage_write),
            "storage_read": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], [vt.i64()]), storage_read),
            "storage_remove": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], [vt.i64()]), storage_remove),
            "storage_has_key": func(self.store, ft([vt.i64(), vt.i64()], [vt.i64()]), storage_has_key),

            # Validator API
            "validator_stake": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), validator_stake),
            "validator_total_stake": func(self.store, ft([vt.i64()], []), validator_total_stake),

            # Alt BN128
            "alt_bn128_g1_multiexp": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), alt_bn128_g1_multiexp),
            "alt_bn128_g1_sum": func(self.store, ft([vt.i64(), vt.i64(), vt.i64()], []), alt_bn128_g1_sum),
            "alt_bn128_pairing_check": func(self.store, ft([vt.i64(), vt.i64()], [vt.i64()]), alt_bn128_pairing_check),
        }
        return env_imports

def trace_wasm(wasm_runner: WasmRunner, entry_points: dict) -> tuple[set[int], set[str]]:
    for entry_point in entry_points.keys():
        [entry_point_module, entry_point_name] = entry_point.rsplit('.', 1)
        # test_inputs = entry_points[entry_point] if len(entry_points[entry_point]) > 0 else [b'{"key1": "value1", "key2": true, "key3": false, "key4": -20000.00001, "key5": 100000000}']
        test_inputs = entry_points[entry_point] if len(entry_points[entry_point]) > 0 else [b'{}']
        for input_bytes in test_inputs:
            print(f"running method {entry_point_name}({input_bytes})")
            wasm_runner.set_input_bytes(input_bytes)
            entry_point = wasm_runner.export(entry_point_name)
            try:
                entry_point()
            except Exception as e:
                print(f"trace_wasm(): exception {e}")
            wasm_runner.reset()
    return wasm_runner.called_functions, wasm_runner.loaded_frozen_modules, wasm_runner.loaded_builtin_modules


def get_function_names(wat: list) -> set[str]:
    names = set()
    for item in wat:
        if item[0] == "func":
            names.add(str(item[1]).lstrip("$"))
    return names


def instrument_wat(wat):
    instrumented_wat = []
    for item in wat:
        if item[0] == "func":
            func = deepcopy(item)
            func_name = str(func[1]).lstrip("$")
            func_name_hash = fnv1a_32(func_name)
            pos = 2
            for func_item in func[2:]:
                if func_item[0] not in ('param', 'result', 'local'):
                    break
                pos += 1
            func.insert(pos, ["call", "$trace_function_call", ["i32.const", func_name_hash]])
            if func_name == "load_frozen_module":
                func.insert(pos, ["call", "$trace_frozen_module_load", ["local.get", "$0"]])
            if func_name == "notify_builtin_module_load":
                func.insert(pos, ["call", "$trace_builtin_module_load", ["local.get", "$0"]])
            instrumented_wat.append(func)
        else:
            instrumented_wat.append(item)
    instrumented_wat.append(["import", "env", "trace_function_call", ["func", "$trace_function_call", ["param", "i32"]]])
    instrumented_wat.append(["import", "env", "trace_frozen_module_load", ["func", "$trace_frozen_module_load", ["param", "i32"]]])
    instrumented_wat.append(["import", "env", "trace_builtin_module_load", ["func", "$trace_builtin_module_load", ["param", "i32"]]])
    return instrumented_wat


def unescape_data_str(s):
    if not (s.startswith('"') and s.endswith('"')): raise ValueError("String must be doubly-quoted")
    content, result, i = s[1:-1], [], 0
    while i < len(content):
        if content[i] == '\\' and i + 1 < len(content):
            next_char = content[i + 1]
            if next_char == 't': result.append(ord('\t')); i += 2
            elif next_char == 'n': result.append(ord('\n')); i += 2
            elif next_char == 'r': result.append(ord('\r')); i += 2
            elif next_char == '"': result.append(ord('"')); i += 2
            elif next_char == "'": result.append(ord("'")); i += 2
            elif next_char == '\\': result.append(ord('\\')); i += 2
            elif i + 2 < len(content) and all(c in '0123456789abcdefABCDEF' for c in content[i+1:i+3]):
                result.append(int(content[i+1:i+3], 16)); i += 3
            else: result.append(ord('\\')); i += 1
        else: result.append(ord(content[i])); i += 1
    return bytes(result)


def escape_data_str(data):
    assert(type(data) == bytes)
    result = []
    for b in data:
        # if b == ord('\t'): result.append('\\t')
        # elif b == ord('\n'): result.append('\\n')
        # elif b == ord('\r'): result.append('\\r')
        if b == ord('"'): result.append('\\"')
        elif b == ord("'"): result.append("\\'")
        elif b == ord('\\'): result.append('\\\\')
        elif 32 <= b <= 126: result.append(chr(b))
        else: result.append(f'\\{b:02x}')
    # assert(unescape_data_str('"' + ''.join(result) + '"') == data)
    return '"' + ''.join(result) + '"'


def get_wasm_data_initializer(wat):
    initialized_memory_length = 0
    initializer = bytearray(20000000)
    for item in wat:
        if item[0] == "data":
            data = unescape_data_str(item[-1])
            offset = int(item[-2][1])
            length = len(data)
            initialized_memory_length = max(initialized_memory_length, offset + length)
            initializer[offset:offset + length] = data
    return bytes(initializer[0:initialized_memory_length])


def compress_wasm_data_initializer(wat):
    FROZEN_MODULE_BASE_ADDR = 1048576
    WASM_DATA_BASE_ADDR = 8388608
    COMPRESSION_TYPE_LZ4 = 0x00347a6c
    COMPRESSED_BLOCK_HEADER_ADDR = 1024
    wasm_data = get_wasm_data_initializer(wat)
    frozen_module_data_last_addr = 0
    for i in range(FROZEN_MODULE_BASE_ADDR, min(WASM_DATA_BASE_ADDR, len(wasm_data))):
        if wasm_data[i] != 0:
            frozen_module_data_last_addr = i + 1
    wasm_data0 = wasm_data[FROZEN_MODULE_BASE_ADDR:frozen_module_data_last_addr]
    wasm_data1 = wasm_data[WASM_DATA_BASE_ADDR:]
    compressed_data0 = lz4.frame.compress(wasm_data0, compression_level=lz4.frame.COMPRESSIONLEVEL_MAX, block_size=lz4.frame.BLOCKSIZE_MAX4MB)
    compressed_data1 = lz4.frame.compress(wasm_data1, compression_level=lz4.frame.COMPRESSIONLEVEL_MAX, block_size=lz4.frame.BLOCKSIZE_MAX4MB)
    compressed_data_addr0 = COMPRESSED_BLOCK_HEADER_ADDR + 1024
    compressed_data_addr1 = (compressed_data_addr0 + len(compressed_data0) + 1023) & ~1023
    print("applying lz4 compression to wasm data initializers:")
    print(f" data0: {len(wasm_data0)} bytes @{FROZEN_MODULE_BASE_ADDR} -> {len(compressed_data0)} bytes @{compressed_data_addr0}")
    print(f" data1: {len(wasm_data1)} bytes @{WASM_DATA_BASE_ADDR} -> {len(compressed_data1)} bytes @{compressed_data_addr1}")
    modified_wat = []
    for item in wat:
        if item[0] != "data":
            modified_wat.append(item)
    modified_wat.append(["data", f"$.compressed_data.header", ["i32.const", COMPRESSED_BLOCK_HEADER_ADDR], 
                         escape_data_str(struct.pack('<L', COMPRESSION_TYPE_LZ4) + struct.pack('<L', compressed_data_addr0) + 
                                         struct.pack('<L', len(compressed_data0)) + struct.pack('<L', FROZEN_MODULE_BASE_ADDR) + 
                                         struct.pack('<L', COMPRESSION_TYPE_LZ4) + struct.pack('<L', compressed_data_addr1) + 
                                         struct.pack('<L', len(compressed_data1)) + struct.pack('<L', WASM_DATA_BASE_ADDR))])
    modified_wat.append(["data", f"$.compressed_data.0", ["i32.const", compressed_data_addr0], escape_data_str(compressed_data0)])
    modified_wat.append(["data", f"$.compressed_data.1", ["i32.const", compressed_data_addr1], escape_data_str(compressed_data1)])
    return modified_wat


class WasmDataStore:
    BASE_ADDR = 1048576
    MAX_ADDR = 8388608
    FROZEN_MODULE_HEADER_LENGTH = 64
    FROZEN_MODULE_MAX_PATH_LENGTH = 56
    MAX_FROZEN_MODULES = 512
    STRINGS_OFFSET = FROZEN_MODULE_HEADER_LENGTH * MAX_FROZEN_MODULES
    
    # memory layout: 
    #   BASE_ADDR: <frozen module header:64 bytes> * MAX_FROZEN_MODULES (currently 64k - 1024)
    #              <allocated null-terminated strings>
    #              <frozen module data chunks linked from the header>
    #              ...
    #   MAX_ADDR: start of the regular WASM data area (-sGLOBAL_BASE=<MAX_ADDR> Emscripten option)
    
    # frozen module header (64 bytes):
    #   data addr:4 bytes (le)
    #   data length:4 bytes (le)
    #   path: 56 bytes (null-terminated)
    #   both data addr and size fields is a empty header/end of valid headers marker
    
    def __init__(self):
        self.frozen_modules = []
        self.packed_strings = bytearray()
        self.packed_strings_cache = dict()
        
    def add_frozen_module(self, path: str, bytecode: bytes):
        self.frozen_modules.append((path, bytecode))
        
    def allocate_string(self, value: str):
        if value in self.packed_strings_cache:
            return self.packed_strings_cache[value]
        addr = self.BASE_ADDR + self.STRINGS_OFFSET + len(self.packed_strings)
        self.packed_strings += (value.encode("utf-8") + b'\0')
        self.packed_strings_cache[value] = addr
        return addr

    def to_bytes(self):
        "Produces the raw bytes to be put into WASM data section starting from BASE_ADDR, excluding any of the trailing zeroes"
        data = bytearray(self.MAX_ADDR - self.BASE_ADDR)
        data[self.STRINGS_OFFSET:self.STRINGS_OFFSET + len(self.packed_strings)] = self.packed_strings
        alloc_offset = self.STRINGS_OFFSET + len(self.packed_strings)
        frozen_module_index = 0
        for (path, bytecode) in self.frozen_modules:
            data[frozen_module_index * self.FROZEN_MODULE_HEADER_LENGTH:(frozen_module_index + 1) * self.FROZEN_MODULE_HEADER_LENGTH] = struct.pack('<L', self.BASE_ADDR + alloc_offset) + struct.pack('<L', len(bytecode)) + path.encode("utf-8").ljust(self.FROZEN_MODULE_MAX_PATH_LENGTH, b'\0')  
            assert(len(path.encode("utf-8")) < self.FROZEN_MODULE_MAX_PATH_LENGTH)
            data[alloc_offset:alloc_offset + len(bytecode)] = bytecode
            alloc_offset += len(bytecode)
            frozen_module_index += 1
        assert(alloc_offset < self.MAX_ADDR)
        del data[alloc_offset:]
        return bytes(data)
    
    def add_to_wasm_runner(self, wasm_runner: WasmRunner):
        data = self.to_bytes()
        data_ptr = wasm_runner.data_ptr()
        for i in range(0, len(data)):
            data_ptr[self.BASE_ADDR + i] = data[i]
        
    def add_to_wat(self, wat):
        modified_wat = wat.copy()
        modified_wat.append(["data", f"$.data.frozen", ["i32.const", self.BASE_ADDR], escape_data_str(self.to_bytes())])
        return modified_wat
        
    
def add_contract_entry_points_to_wat(wat, wasm_data: WasmDataStore, entry_points):
    modified_wat = wat.copy()
    for entry_point in entry_points:
        [module_name, func_name] = entry_point.rsplit('.', 1) if '.' in entry_point else ['contract', entry_point]
        module_name_address = wasm_data.allocate_string(module_name)
        func_name_address = wasm_data.allocate_string(func_name)
        modified_wat.append(["export", f'"{func_name}"', ["func", f"${module_name}_{func_name}"]])
        modified_wat.append(["func", f"${module_name}_{func_name}", 
                             ["call", "$contract_entry_point", ["i32.const", module_name_address], ["i32.const", func_name_address]]])
    return modified_wat

def compile_to_bytecode(wasm_runner: WasmRunner, wasm_data: WasmDataStore, source_code, filename):
    alloc_buffer = wasm_runner.export("_alloc_buffer")
    compile_contract_source = wasm_runner.export("_compile_contract_source")
    wasm_data.add_to_wasm_runner(wasm_runner)
    data_ptr = wasm_runner.data_ptr()
    def alloc_null_terminated_string(string, encoding = "utf-8"):
        string_bytes = string.encode(encoding)
        buffer_ptr = alloc_buffer(len(string_bytes) + 1)
        for i in range(0, len(string_bytes)):
            data_ptr[buffer_ptr + i] = string_bytes[i]
        data_ptr[buffer_ptr + len(string_bytes)] = 0
        return buffer_ptr
    bytecode_len_ptr = alloc_buffer(4)
    bytecode_ptr = compile_contract_source(alloc_null_terminated_string(source_code), alloc_null_terminated_string(filename), bytecode_len_ptr);
    bytecode_len = struct.unpack('<L', bytes(data_ptr[bytecode_len_ptr:bytecode_len_ptr + 4]))[0]
    return bytes(data_ptr[bytecode_ptr:bytecode_ptr + bytecode_len]);

def should_include_lib_path(pinned_module_paths, path: str):
    return (pinned_module_paths is None or path in pinned_module_paths)

def add_frozen_modules(wasm_data: WasmDataStore, pinned_module_paths, stdlib_zip_path, user_lib_path):
    with zipfile.ZipFile(stdlib_zip_path, 'r') as zip_file:
        for info in zip_file.infolist():
            frozen_path = info.filename.lstrip('/')
            if not info.is_dir() and frozen_path.endswith('.pyc') and should_include_lib_path(pinned_module_paths, frozen_path):
                wasm_data.add_frozen_module(frozen_path, zip_file.read(info.filename))
    for path in Path(user_lib_path).glob("**/*.pyc"):
        frozen_path = str(path.relative_to(user_lib_path))
        if should_include_lib_path(pinned_module_paths, frozen_path):
            print(f"add_frozen_modules_from_dir(): including {path}, rel_path: {frozen_path}")
            with open(path, "rb") as f:
                wasm_data.add_frozen_module(frozen_path, f.read())
    
def get_near_exports_from_file(file_path: str, module_name: str) -> set[str]:
    with open(file_path, "r") as file:
        content = file.read()
        tree = ast.parse(content, filename=file_path)

    # Track custom decorators that eventually use near.export
    custom_exporters = set(["export", "view", "call", "init", "callback"])
    # Track functions that are exported
    near_exports = defaultdict(list)
    
    # First pass: identify custom decorators that use near.export
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # Check if this is a potential custom decorator function
            if node.name in custom_exporters:
                for body_node in ast.walk(node):
                    # Check function bodies for return statements that use near.export
                    if isinstance(body_node, ast.Return) and body_node.value is not None:
                        # Look for near.export in the return expression
                        for return_node in ast.walk(body_node.value):
                            if (isinstance(return_node, ast.Attribute) and 
                                isinstance(return_node.value, ast.Name) and 
                                return_node.value.id == "near" and 
                                return_node.attr == "export"):
                                custom_exporters.add(node.name)
                                break
    
    # Second pass: find functions with near.export or custom exporters as decorators
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                # Case 1: Direct @near.export
                if ((isinstance(decorator, ast.Attribute) and isinstance(decorator.value, ast.Name) and 
                     decorator.value.id == "near" and decorator.attr == "export") or
                    (isinstance(decorator, ast.Name) and decorator.id == "near.export")):
                    near_exports[f"{module_name}.{node.name}"] = []

                # Case 2: Using a custom exporter decorator like @init, @view, etc.
                if isinstance(decorator, ast.Name) and decorator.id in custom_exporters:
                    near_exports[f"{module_name}.{node.name}"] = []
                    
                # Case 3: @near.optimizer_inputs()
                if isinstance(decorator, ast.Call) and decorator.args:
                    if ((isinstance(decorator.func, ast.Attribute) and isinstance(decorator.func.value, ast.Name) and 
                            decorator.func.value.id == "near" and decorator.func.attr == "optimizer_inputs") or
                        (isinstance(decorator.func, ast.Name) and decorator.func.id == "near.optimizer_inputs")):                    
                        for elt in decorator.args[0].elts:
                            if isinstance(elt, ast.Constant):
                                value = None
                                if isinstance(elt.value, str):
                                    value = elt.value.encode("utf-8")
                                elif isinstance(elt.value, bytes):
                                    value = elt.value
                                elif isinstance(elt.value, (dict, list)):
                                    value = json.dumps(elt.value).encode("utf-8")
                                else:
                                    value = str(elt.value).encode("utf-8")
                                near_exports[f"{module_name}.{node.name}"].append(value)
                            
    return near_exports
    

def optimize_wasm_file(build_dir, input_file, output_file, module_opt=True, function_opt="aggressive", compression=True, 
                       debug_info=True, pinned_functions=[], user_lib_dir = "lib", stdlib_zip="python-stdlib.zip", 
                       contract_exports=[], verify_optimized_wasm=True):
    build_path = Path(build_dir)
    wasm_path = Path(input_file)
    wat_path = build_path / "python.wat"
    instrumented_wasm_path = build_path / "python-instrumented.wasm"
    instrumented_wat_path = build_path / "python-instrumented.wat"
    
    build_path.mkdir(parents=True, exist_ok=True)
    
    sys.setrecursionlimit(10000)

    print(f"running wasm-dis {wasm_path}..")
    subprocess.run(["wasm-dis", wasm_path, "-o", wat_path], check=True)
    print(f"reading {wat_path}..")
    wat = read_sexp(wat_path)
    
    function_names = get_function_names(wat)
    function_name_hashes = {fnv1a_32(s) for s in function_names}

    with open(wasm_path, "rb") as f:
        wasm_bytes = f.read()

    wasm_data = WasmDataStore()
    add_frozen_modules(wasm_data, None, stdlib_zip, user_lib_dir)
    
    entry_points = dict()
    
    compiler = WasmRunner(wasm_bytes)
    for path in Path(user_lib_dir).glob("**/*.py"):
        module_name = str(Path(path).relative_to(user_lib_dir).with_suffix('')).replace('/', '.')
        entry_points.update({key: [] for key in contract_exports} if len(contract_exports) > 0 else get_near_exports_from_file(path, module_name))
        pyc_path = path.with_suffix(".pyc")
        print(f"compiling {path} to {pyc_path}..")
        with open(path, "r") as source_file:
            with open(pyc_path, "wb") as pyc_file:
                pyc_file.write(compile_to_bytecode(compiler, wasm_data, source_file.read(), path.name))
                compiler.reset()
                
    print(f"entry points names: {list(entry_points.keys())}")
    
    wasm_data = WasmDataStore()
    add_frozen_modules(wasm_data, None, stdlib_zip, user_lib_dir)

    instrumented_wat = wasm_data.add_to_wat(instrument_wat(add_contract_entry_points_to_wat(wat, wasm_data, entry_points.keys())))
    print(f"writing {instrumented_wat_path}..")
    write_sexp(instrumented_wat, instrumented_wat_path)

    print(f"running wasm-as {instrumented_wat_path}..")
    subprocess.run(["wasm-as", "-g", instrumented_wat_path, "-o", instrumented_wasm_path, "--enable-nontrapping-float-to-int", "--enable-sign-ext"], check=True)

    print(f"tracing called functions and loaded modules in {instrumented_wasm_path}..")
    with open(instrumented_wasm_path, "rb") as f:
        instrumented_wasm_bytes = f.read()
        
    called_function_name_hashes, loaded_frozen_modules, loaded_builtin_modules  = trace_wasm(WasmRunner(instrumented_wasm_bytes), entry_points)
    print(f"loaded frozen/builtin modules: {loaded_frozen_modules}, {loaded_builtin_modules}")
    
    pinned_function_names = set(DEFAULT_PINNED_FUNCTIONS + pinned_functions)
    
    pinned_function_name_hashes = set()
    pinned_function_name_hashes = pinned_function_name_hashes.union({fnv1a_32(s.strip()) for s in pinned_function_names})
    unreferenced_function_name_hashes = function_name_hashes.difference(called_function_name_hashes).difference(pinned_function_name_hashes)    
    non_loaded_builtin_modules_removable_function_name_prefixes = [v for module, prefixes in BUILTIN_MODULE_FUNCTION_NAME_PREFIXES.items() for v in prefixes if module not in loaded_builtin_modules]
    
    def removing_function_allowed(func_name):
        if function_opt == 'aggressive':
            return True
        elif function_opt == 'safe':
            for prefix in SAFELY_REMOVABLE_FUNCTION_NAME_PREFIXES + non_loaded_builtin_modules_removable_function_name_prefixes:
                if func_name.startswith(prefix):
                    return True
            for suffix in SAFELY_REMOVABLE_FUNCTION_NAME_SUFFIXES:
                if func_name.endswith(suffix):
                    return True
        return False
        
    wasm_data = WasmDataStore()

    def replace_removed_function_calls(items):
        for item in items:
            if isinstance(item, list):
                if len(item) > 1 and item[0] == 'call':
                    func_name = str(item[1]).lstrip("$")
                    if fnv1a_32(func_name) in unreferenced_function_name_hashes and removing_function_allowed(func_name):
                        # print(f"optimizing out {func_name}")
                        del item[0:]
                        item += ["block", ["call", "$optimized_out_function_panic_handler", ["i32.const", wasm_data.allocate_string(func_name)]], ["unreachable"]]
                else:
                    replace_removed_function_calls(item)

    if function_opt != 'off':
        removed_function_names = set()
        for item in wat:
            if item[0] == "func":
                replace_removed_function_calls(item)
                func_name = str(item[1]).lstrip("$")
                if fnv1a_32(func_name) in unreferenced_function_name_hashes and removing_function_allowed(func_name):
                    removed_function_names.add(func_name)
                    pos = 2
                    for func_item in item[2:]:
                        if func_item[0] not in ('param', 'result', 'local'):
                            break
                        pos += 1
                    item.insert(pos, ['unreachable'])
                    del item[pos + 1:]
                    
        with open(build_path / "removed_functions.txt", "w") as f:
            for fn in sorted(removed_function_names):
                f.write(f"{fn}\n")

        with open(build_path / "retained_functions.txt", "w") as f:
            for fn in sorted(function_names):
                if fn not in removed_function_names:
                    f.write(f"{fn}\n")

    add_frozen_modules(wasm_data, loaded_frozen_modules if module_opt else None, stdlib_zip, user_lib_dir)
    modified_wat = wasm_data.add_to_wat(add_contract_entry_points_to_wat(wat, wasm_data, entry_points.keys()))
    
    modified_wat_path = build_path / "python-modified.wat"
    optimized_wasm_path = build_path / "python-optimized.wasm"
    optimized_wat_path = build_path / "python-optimized.wat"
    compressed_optimized_wasm_path = build_path / "python-compressed.wasm"
    compressed_optimized_wat_path = build_path / "python-compressed.wat"

    print(f"writing {modified_wat_path}..")
    write_sexp(modified_wat, modified_wat_path)
    
    print(f"running wasm-opt {modified_wat_path}")

    subprocess.run(
        ["wasm-opt", "-Oz", modified_wat_path, "-o", optimized_wasm_path, 
         "--enable-nontrapping-float-to-int", "--enable-sign-ext"] + (["-g"] if debug_info else []), check=True)

    print(f"running wasm-dis {optimized_wasm_path}..")
    subprocess.run(["wasm-dis", optimized_wasm_path, "-o", optimized_wat_path], check=True)
    print(f"reading {optimized_wat_path}..")
    optimized_wat = read_sexp(optimized_wat_path)

    if compression:    
        compressed_optimized_wat = compress_wasm_data_initializer(optimized_wat)
        print(f"writing {compressed_optimized_wat_path}..")
        write_sexp(compressed_optimized_wat, compressed_optimized_wat_path)
    
        print(f"running wasm-as {compressed_optimized_wat_path}..")
        subprocess.run(["wasm-as", compressed_optimized_wat_path, "-o", compressed_optimized_wasm_path, 
                        "--enable-nontrapping-float-to-int", "--enable-sign-ext"] + (["-g"] if debug_info else []), check=True)

    final_wasm_path = compressed_optimized_wasm_path if compression else optimized_wasm_path
    
    if verify_optimized_wasm:
        with open(final_wasm_path, "rb") as f:
            wasm_bytes = f.read()
        print(f"Verifying optimized WASM at {final_wasm_path} ({len(wasm_bytes)} bytes)..")
        trace_wasm(WasmRunner(wasm_bytes), entry_points)
    
    shutil.copy(final_wasm_path, output_file)

    
def resolve_defaults(args):
    # defaults by optimization level: [module_tracing, function_tracing, compression, debug_info]
    defaults = {0: [False, 'off', False, True], 
                1: [True, 'off', True, True], 
                2: [True, 'safe', True, True], 
                3: [True, 'aggressive', True, True],
                4: [True, 'aggressive', True, False]}[args.opt_level]

    args.module_tracing = defaults[0] if args.module_tracing is None else args.module_tracing
    args.function_tracing = args.function_tracing or defaults[1]
    args.compression = defaults[2] if args.compression is None else args.compression
    args.debug_info = defaults[3] if args.debug_info is None else args.debug_info

    return args
    
def main():
    parser = argparse.ArgumentParser(description=__doc__.strip(), formatter_class=argparse.RawDescriptionHelpFormatter)
    
    parser.add_argument('build_dir', nargs='?', default='./build', help='Build directory (default: ./build)')
    parser.add_argument('-i', '--input-file', default='python.wasm', help='Input WASM file name (default: python.wasm)')
    parser.add_argument('-o', '--output-file', default='python-optimized.wasm', help='Output WASM file name (default: python-optimized.wasm)')
    parser.add_argument('-l', '--user-lib-dir', default='lib', help='User Python library directory (default: lib)')
    parser.add_argument('--python-stdlib-zip', default='python-stdlib.zip', help='Python standard library zip (default: python-stdlib.zip)')
    parser.add_argument('-O', '--opt-level', type=int, default=3, choices=[0,1,2,3,4], help='Optimization level 0-4 (default: 3)')
    
    def bool_arg(v):
        if isinstance(v, str):
            return v.lower() in ['1', 'true', 'yes', 'on']
        return bool(v)
    
    parser.add_argument('--module-tracing', type=bool_arg, help='Enable Python module tracing (1/0)')
    parser.add_argument('--function-tracing', choices=['off','safe','aggressive'], help='Function tracing mode')
    parser.add_argument('--compression', type=bool_arg, help='Enable WASM data initializer compression (1/0)')
    parser.add_argument('--debug-info', type=bool_arg, help='Include WASM debug information (1/0)')
    parser.add_argument('--verify-optimized-wasm', type=bool_arg, default=True, help='Run/verify optimized WASM after building (1/0)')    
    parser.add_argument('-p', '--pinned-functions', help='Comma-separated list of function names to pin (case-sensitive)')
    parser.add_argument('-e', '--exports', help='Comma-separated list of function names to be exported from the WASM (will be auto-detected via @near.export search if omitted)')
        
    args = resolve_defaults(parser.parse_args())
    args.pinned_functions = [f.strip() for f in (args.pinned_functions or '').split(',') if f.strip()]
    args.exports = [f.strip() for f in (args.exports or '').split(',') if f.strip()]
    
    optimize_wasm_file(args.build_dir, args.input_file, args.output_file, module_opt=args.module_tracing, 
                       function_opt=args.function_tracing, compression=args.compression, debug_info=args.debug_info,
                       pinned_functions=args.pinned_functions, user_lib_dir=args.user_lib_dir, stdlib_zip=args.python_stdlib_zip,
                       contract_exports=args.exports, verify_optimized_wasm=args.verify_optimized_wasm)

if __name__ == "__main__":
    main()
