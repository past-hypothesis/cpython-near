#include <Python.h>
#include <longobject.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include "near_api.h"

#if defined(__EMSCRIPTEN__) || defined(__wasi__)

#define DEFAULT_TEMP_REGISTER_ID 0

typedef struct
{
    uint64_t len;
    uint64_t ptr;
} near_api_ptr_t;

typedef struct
{
    uint64_t lo;
    uint64_t hi;
} u128_t;

// Helper functions
static near_api_ptr_t get_py_bytes(PyObject* obj)
{
    near_api_ptr_t ptr = { 0, 0 };
    if (PyBytes_Check(obj)) {
        ptr.ptr = (uint64_t)PyBytes_AsString(obj);
        ptr.len = PyBytes_Size(obj);
    }
    else {
        PyErr_SetString(PyExc_TypeError, "Expected bytes");
    }
    return ptr;
}

static near_api_ptr_t get_py_str(PyObject* obj)
{
    near_api_ptr_t ptr = { 0, 0 };
    if (PyUnicode_Check(obj)) {
        Py_ssize_t len;
        const char* str = PyUnicode_AsUTF8AndSize(obj, &len);
        if (str) {
            ptr.ptr = (uint64_t)str;
            ptr.len = (uint64_t)len;
        }
    }
    else {
        PyErr_SetString(PyExc_TypeError, "Expected string");
    }
    return ptr;
}

static near_api_ptr_t get_py_str_or_bytes(PyObject* obj)
{
    near_api_ptr_t ptr = { 0, 0 };
    if (PyUnicode_Check(obj)) {
        Py_ssize_t len;
        const char* str = PyUnicode_AsUTF8AndSize(obj, &len);
        if (str) {
            ptr.ptr = (uint64_t)str;
            ptr.len = (uint64_t)len;
        }
    }
    else if (PyBytes_Check(obj)) {
        ptr.ptr = (uint64_t)PyBytes_AsString(obj);
        ptr.len = PyBytes_Size(obj);
    }
    else {
        PyErr_SetString(PyExc_TypeError, "Expected string or bytes");
    }
    return ptr;
}

static u128_t py_int_to_u128(PyObject* value)
{
    u128_t result = { 0, 0 };
    unsigned char bytes[16] = { 0 };
    if (!PyLong_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "Expected integer");
        return result;
    }
    if (PyLong_AsLongLong(value) < 0) {
        PyErr_SetString(PyExc_ValueError, "Value must be non-negative");
        return result;
    }
    if (_PyLong_AsByteArray((PyLongObject*)value, bytes, 16, 1, 0, 0) == -1) {
        PyErr_SetString(PyExc_OverflowError, "Value exceeds 128 bits");
        return result;
    }
    for (int i = 0; i < 8; i++) {
        result.lo |= (uint64_t)bytes[i] << (i * 8);
        result.hi |= (uint64_t)bytes[i + 8] << (i * 8);
    }
    return result;
}

static PyObject* u128_to_py_int(const u128_t* u128)
{
    unsigned char bytes[16];
    for (int i = 0; i < 8; i++) {
        bytes[i] = (u128->lo >> (i * 8)) & 0xFF;
        bytes[i + 8] = (u128->hi >> (i * 8)) & 0xFF;
    }
    return _PyLong_FromByteArray(bytes, 16, 1, 0);
}

// Register operations
static PyObject* read_register_as_bytes(uint64_t register_id)
{
    uint64_t len = register_len(register_id);
    // printf("read_register_as_bytes(%d): %d bytes\n", (int)register_id, (int)len);
    char* data = (char*)malloc(len);
    if (!data) {
        PyErr_NoMemory();
        return NULL;
    }
    read_register(register_id, (uint64_t)data);
    // printf("read_register_as_bytes(%d): calling PyBytes_FromStringAndSize()\n", (int)register_id, (int)register_id);
    PyObject* result = PyBytes_FromStringAndSize(data, len);
    // printf("read_register_as_bytes(%d): PyBytes_FromStringAndSize() returned %08x\n", (int)register_id, (int)result);
    free(data);
    // printf("read_register_as_bytes(%d): PyBytes_FromStringAndSize(): ok\n", (int)register_id);
    return result;
}

static PyObject* read_register_as_str(uint64_t register_id)
{
    uint64_t len = register_len(register_id);
    char* data = (char*)malloc(len + 1);
    if (!data) {
        PyErr_NoMemory();
        return NULL;
    }
    read_register(register_id, (uint64_t)data);
    data[len] = '\0';
    PyObject* result = PyUnicode_FromString(data);
    free(data);
    return result;
}

static PyObject* read_default_temp_register_as_bytes(void)
{
    return read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* read_default_temp_register_as_str(void)
{
    return read_register_as_str(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_export(PyObject* self, PyObject* func)
{
    Py_INCREF(func);
    return func;
}

static PyObject* near_optimizer_inputs(PyObject *self, PyObject *func)
{
    Py_INCREF(func);
    return func;
}

static PyObject* near_optimizer_inputs_decorator_factory(PyObject *self, PyObject *arg)
{
    static PyMethodDef inner_method = {
        "_inner_optimizer_inputs", 
        (PyCFunction)near_optimizer_inputs, 
        METH_O, 
        "Inner no-op decorator for supplying CPython WASM optimizer inputs"
    };
    return PyCFunction_New(&inner_method, NULL);
}

static PyObject* near_read_register(PyObject* self, PyObject* args)
{
    uint64_t register_id;
    if (!PyArg_ParseTuple(args, "K", &register_id)) {
        PyErr_SetString(PyExc_TypeError, "Expected integer register ID");
        return NULL;
    }
    return read_register_as_bytes(register_id);
}

static PyObject* near_read_register_as_str(PyObject* self, PyObject* args)
{
    uint64_t register_id;
    if (!PyArg_ParseTuple(args, "K", &register_id)) {
        PyErr_SetString(PyExc_TypeError, "Expected integer register ID");
        return NULL;
    }
    return read_register_as_str(register_id);
}

static PyObject* near_register_len(PyObject* self, PyObject* args)
{
    uint64_t register_id;
    if (!PyArg_ParseTuple(args, "K", &register_id)) {
        PyErr_SetString(PyExc_TypeError, "Expected integer register ID");
        return NULL;
    }
    return PyLong_FromUnsignedLongLong(register_len(register_id));
}

static PyObject* near_write_register(PyObject* self, PyObject* args)
{
    uint64_t register_id;
    PyObject* data;
    if (!PyArg_ParseTuple(args, "KO", &register_id, &data)) {
        PyErr_SetString(PyExc_TypeError, "Expected (int, str/bytes)");
        return NULL;
    }
    near_api_ptr_t data_ptr = get_py_str_or_bytes(data);
    if (PyErr_Occurred()) return NULL;
    write_register(register_id, data_ptr.len, data_ptr.ptr);
    Py_RETURN_NONE;
}


static PyObject* near_input(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    input(DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_input_as_str(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    input(DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_str(DEFAULT_TEMP_REGISTER_ID);
}

// Context API
static PyObject* near_current_account_id(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    current_account_id(DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_str(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_signer_account_id(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    signer_account_id(DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_str(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_signer_account_pk(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    signer_account_pk(DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_predecessor_account_id(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    predecessor_account_id(DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_str(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_block_height(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    return PyLong_FromUnsignedLongLong(block_index());
}

static PyObject* near_block_timestamp(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    return PyLong_FromUnsignedLongLong(block_timestamp());
}

static PyObject* near_epoch_height(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    return PyLong_FromUnsignedLongLong(epoch_height());
}

static PyObject* near_storage_usage(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    return PyLong_FromUnsignedLongLong(storage_usage());
}

// Economics API
static PyObject* near_account_balance(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    u128_t balance = { 0, 0 };
    account_balance((uint64_t)&balance);
    return u128_to_py_int(&balance);
}

static PyObject* near_account_locked_balance(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    u128_t locked = { 0, 0 };
    account_locked_balance((uint64_t)&locked);
    return u128_to_py_int(&locked);
}

static PyObject* near_attached_deposit(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    u128_t deposit = { 0, 0 };
    attached_deposit((uint64_t)&deposit);
    return u128_to_py_int(&deposit);
}

static PyObject* near_prepaid_gas(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    return PyLong_FromUnsignedLongLong(prepaid_gas());
}

static PyObject* near_used_gas(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    return PyLong_FromUnsignedLongLong(used_gas());
}


// Math API
static PyObject* near_random_seed(PyObject *self, PyObject *Py_UNUSED(dummy))
{
    random_seed(DEFAULT_TEMP_REGISTER_ID);
    return read_default_temp_register_as_bytes();
}

__wasi_errno_t __wasi_random_get(uint8_t *buf, __wasi_size_t buf_len)
{
    random_seed(DEFAULT_TEMP_REGISTER_ID);
    uint64_t len = register_len(DEFAULT_TEMP_REGISTER_ID);
    void *temp = malloc(len);
    read_register(DEFAULT_TEMP_REGISTER_ID, (uint64_t)temp);
    memcpy(buf, temp, Py_MIN(len, buf_len));
    free(temp);
    return 0;
}

static PyObject* near_sha256(PyObject* self, PyObject* value)
{
    near_api_ptr_t value_ptr = get_py_bytes(value);
    if (PyErr_Occurred()) return NULL;
    sha256(value_ptr.len, value_ptr.ptr, DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_keccak256(PyObject* self, PyObject* value)
{
    near_api_ptr_t value_ptr = get_py_bytes(value);
    if (PyErr_Occurred()) return NULL;
    keccak256(value_ptr.len, value_ptr.ptr, DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_keccak512(PyObject* self, PyObject* value)
{
    near_api_ptr_t value_ptr = get_py_bytes(value);
    if (PyErr_Occurred()) return NULL;
    keccak512(value_ptr.len, value_ptr.ptr, DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_ripemd160(PyObject* self, PyObject* value)
{
    near_api_ptr_t value_ptr = get_py_bytes(value);
    if (PyErr_Occurred()) return NULL;
    ripemd160(value_ptr.len, value_ptr.ptr, DEFAULT_TEMP_REGISTER_ID);
    return read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID);
}

static PyObject* near_ecrecover(PyObject* self, PyObject* args)
{
    PyObject *hash, *sig, *v, *malleability_flag;
    if (!PyArg_ParseTuple(args, "OOOO", &hash, &sig, &v, &malleability_flag)) return NULL;
    if (!PyBytes_Check(hash) || !PyBytes_Check(sig) || !PyBool_Check(malleability_flag)) {
        PyErr_SetString(PyExc_TypeError, "hash and sig must be bytes and malleability_flag must be bool");
        return NULL;
    }
    near_api_ptr_t hash_ptr = get_py_bytes(hash);
    near_api_ptr_t sig_ptr = get_py_bytes(sig);
    uint64_t v_val = PyLong_AsUnsignedLongLong(v);
    if (hash_ptr.ptr == 0 || sig_ptr.ptr == 0) return NULL;
    uint64_t result = ecrecover(hash_ptr.len, hash_ptr.ptr, sig_ptr.len, sig_ptr.ptr,
        v_val, PyObject_IsTrue(malleability_flag), DEFAULT_TEMP_REGISTER_ID);
    return result ? read_default_temp_register_as_bytes() : Py_None;
}

static PyObject* near_ed25519_verify(PyObject* self, PyObject* args)
{
    PyObject* sig, * msg, * pub_key;
    if (!PyArg_ParseTuple(args, "OOO", &sig, &msg, &pub_key)) return NULL;
    if (!PyBytes_Check(sig) || !PyBytes_Check(msg) || !PyBytes_Check(pub_key)) {
        PyErr_SetString(PyExc_TypeError, "sig, msg and pub_key must be bytes");
        return NULL;
    }
    near_api_ptr_t sig_ptr = get_py_bytes(sig);
    near_api_ptr_t msg_ptr = get_py_bytes(msg);
    near_api_ptr_t pub_key_ptr = get_py_bytes(pub_key);    
    if (sig_ptr.ptr == 0 || msg_ptr.ptr == 0 || pub_key_ptr.ptr == 0) return NULL;
    int result = ed25519_verify(sig_ptr.len, sig_ptr.ptr, msg_ptr.len, msg_ptr.ptr, 
        pub_key_ptr.len, pub_key_ptr.ptr);
    return PyBool_FromLong(result);
}

// Miscellaneous API
static PyObject* near_value_return(PyObject* self, PyObject* value)
{
    near_api_ptr_t value_ptr = get_py_str_or_bytes(value);
    if (PyErr_Occurred()) return NULL;
    value_return(value_ptr.len, value_ptr.ptr);
    Py_RETURN_NONE;
}

static PyObject* near_panic(PyObject* self, PyObject* Py_UNUSED(dummy))
{
    panic();
    Py_RETURN_NONE;  // Unreachable
}

static PyObject* near_panic_utf8(PyObject* self, PyObject* msg)
{
    if (PyUnicode_Check(msg)) {
        near_api_ptr_t msg_ptr = get_py_str(msg);
        if (!PyErr_Occurred()) {
            panic_utf8(msg_ptr.len, msg_ptr.ptr);
        }
    }
    panic();
    Py_RETURN_NONE;  // Unreachable
}

static PyObject* near_log_utf8(PyObject* self, PyObject* msg)
{
    near_api_ptr_t msg_ptr = get_py_str_or_bytes(msg);
    if (PyErr_Occurred()) return NULL;
    // printf("near_log_utf8(): %s\n", (const char*)msg_ptr.ptr);
    log_utf8(msg_ptr.len, msg_ptr.ptr);
    Py_RETURN_NONE;
}

static PyObject* near_log(PyObject* self, PyObject* msg)
{
    return near_log_utf8(self, msg);
}

static PyObject* near_log_utf16(PyObject* self, PyObject* msg)
{
    near_api_ptr_t msg_ptr = get_py_bytes(msg);
    if (PyErr_Occurred()) return NULL;
    log_utf16(msg_ptr.len, msg_ptr.ptr);
    Py_RETURN_NONE;
}

static PyObject* near_abort_(PyObject* self, PyObject* msg)
{
    if (PyUnicode_Check(msg)) {
        near_api_ptr_t msg_ptr = get_py_str(msg);
        if (!PyErr_Occurred()) {
            panic_utf8(msg_ptr.len, msg_ptr.ptr);
        }
    }
    panic();
    Py_RETURN_NONE;  // Unreachable
}

// Promises API
static PyObject* near_promise_create(PyObject* self, PyObject* args)
{
    PyObject* account_id, * function_name, * arguments, * amount, * gas;
    if (!PyArg_ParseTuple(args, "OOOOO", &account_id, &function_name, &arguments, &amount, &gas)) return NULL;
    near_api_ptr_t acc_id = get_py_str(account_id);
    near_api_ptr_t fn = get_py_str(function_name);
    near_api_ptr_t args_ptr = get_py_bytes(arguments);
    u128_t u128_amount = py_int_to_u128(amount);
    uint64_t gas_val = PyLong_AsUnsignedLongLong(gas);
    if (PyErr_Occurred()) return NULL;
    uint64_t result = promise_create(acc_id.len, acc_id.ptr, fn.len, fn.ptr, args_ptr.len, args_ptr.ptr, (uint64_t)&u128_amount, gas_val);
    return PyLong_FromUnsignedLongLong(result);
}

static PyObject* near_promise_then(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * account_id, * function_name, * arguments, * amount, * gas;
    if (!PyArg_ParseTuple(args, "OOOOOO", &promise_index, &account_id, &function_name, &arguments, &amount, &gas)) return NULL;
    near_api_ptr_t acc_id_ptr = get_py_str(account_id);
    near_api_ptr_t fn_ptr = get_py_str(function_name);
    near_api_ptr_t args_ptr = get_py_str(arguments);
    u128_t u128_amount = py_int_to_u128(amount);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    uint64_t gas_val = PyLong_AsUnsignedLongLong(gas);
    if (PyErr_Occurred()) return NULL;
    uint64_t result = promise_then(promise_idx, acc_id_ptr.len, acc_id_ptr.ptr, 
        fn_ptr.len, fn_ptr.ptr, args_ptr.len, args_ptr.ptr, 
        (uint64_t)&u128_amount, gas_val);    
    return PyLong_FromUnsignedLongLong(result);
}

static PyObject* near_promise_and(PyObject* self, PyObject* promise_indices)
{
    if (!PyList_Check(promise_indices)) {
        PyErr_SetString(PyExc_TypeError, "promise_indices should be a list of promise indices");
        return NULL;
    }
    Py_ssize_t len = PyList_Size(promise_indices);
    uint64_t* promise_indices_buf = malloc(len * sizeof(uint64_t));
    if (!promise_indices_buf) {
        PyErr_NoMemory();
        return NULL;
    }
    for (Py_ssize_t i = 0; i < len; i++) {
        PyObject* item = PyList_GetItem(promise_indices, i);
        if (!PyLong_Check(item)) {
            PyErr_SetString(PyExc_TypeError, "Each promise index should be an integer");
            free(promise_indices_buf);
            return NULL;
        }
        promise_indices_buf[i] = PyLong_AsUnsignedLongLong(item);
    }
    if (PyErr_Occurred()) return NULL;
    uint64_t result = promise_and((uint64_t)promise_indices_buf, len);
    free(promise_indices_buf);    
    return PyLong_FromUnsignedLongLong(result);
}

static PyObject* near_promise_batch_create(PyObject* self, PyObject* account_id)
{
    near_api_ptr_t acc_id_ptr = get_py_str(account_id);
    if (PyErr_Occurred()) return NULL;
    uint64_t result = promise_batch_create(acc_id_ptr.len, acc_id_ptr.ptr);
    return PyLong_FromUnsignedLongLong(result);
}

static PyObject* near_promise_batch_then(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * account_id;
    if (!PyArg_ParseTuple(args, "OO", &promise_index, &account_id)) return NULL;
    near_api_ptr_t acc_id_ptr = get_py_str(account_id);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    if (PyErr_Occurred()) return NULL;
    uint64_t result = promise_batch_then(promise_idx, acc_id_ptr.len, acc_id_ptr.ptr);    
    return PyLong_FromUnsignedLongLong(result);
}

static PyObject* near_promise_batch_action_create_account(PyObject* self, PyObject* promise_index)
{
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_create_account(promise_idx);    
    Py_RETURN_NONE;
}

static PyObject* near_promise_batch_action_deploy_contract(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * code;
    if (!PyArg_ParseTuple(args, "OO", &promise_index, &code)) return NULL;
    near_api_ptr_t code_ptr = get_py_str_or_bytes(code);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_deploy_contract(promise_idx, code_ptr.len, code_ptr.ptr);    
    Py_RETURN_NONE;
}

static PyObject* near_promise_batch_action_function_call(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * function_name, * arguments, * amount, * gas;
    if (!PyArg_ParseTuple(args, "OOOOO", &promise_index, &function_name, &arguments, &amount, &gas)) return NULL;
    near_api_ptr_t fn_ptr = get_py_str(function_name);
    near_api_ptr_t args_ptr = get_py_str(arguments);
    u128_t u128_amount = py_int_to_u128(amount);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    uint64_t gas_val = PyLong_AsUnsignedLongLong(gas);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_function_call(promise_idx, fn_ptr.len, fn_ptr.ptr, args_ptr.len, args_ptr.ptr, (uint64_t)&u128_amount, gas_val);
    Py_RETURN_NONE;
}

static PyObject* near_promise_batch_action_function_call_weight(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * function_name, * arguments, * amount, * gas, * weight;
    if (!PyArg_ParseTuple(args, "OOOOOO", &promise_index, &function_name, &arguments, &amount, &gas, &weight)) return NULL;
    near_api_ptr_t fn_ptr = get_py_str(function_name);
    near_api_ptr_t args_ptr = get_py_str(arguments);
    u128_t u128_amount = py_int_to_u128(amount);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    uint64_t gas_val = PyLong_AsUnsignedLongLong(gas);
    uint64_t weight_val = PyLong_AsUnsignedLongLong(weight);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_function_call_weight(promise_idx, fn_ptr.len, fn_ptr.ptr, args_ptr.len, args_ptr.ptr, (uint64_t)&u128_amount, gas_val, weight_val);
    Py_RETURN_NONE;
}

static PyObject* near_promise_batch_action_transfer(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * amount;
    if (!PyArg_ParseTuple(args, "OO", &promise_index, &amount)) return NULL;
    u128_t u128_amount = py_int_to_u128(amount);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_transfer(promise_idx, (uint64_t)&u128_amount);
    Py_RETURN_NONE;
}

static PyObject* near_promise_batch_action_stake(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * amount, * pub_key;
    if (!PyArg_ParseTuple(args, "OOO", &promise_index, &amount, &pub_key)) return NULL;
    u128_t u128_amount = py_int_to_u128(amount);
    near_api_ptr_t public_key_ptr = get_py_str_or_bytes(pub_key);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_stake(promise_idx, (uint64_t)&u128_amount, public_key_ptr.len, public_key_ptr.ptr);
    Py_RETURN_NONE;
}

static PyObject* near_promise_batch_action_add_key_with_full_access(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * public_key, * nonce;
    if (!PyArg_ParseTuple(args, "OOO", &promise_index, &public_key, &nonce)) return NULL;
    near_api_ptr_t public_key_ptr = get_py_str_or_bytes(public_key);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    uint64_t nonce_val = PyLong_AsUnsignedLongLong(nonce);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_add_key_with_full_access(promise_idx, public_key_ptr.len, public_key_ptr.ptr, nonce_val);
    Py_RETURN_NONE;
}

static PyObject* near_promise_batch_action_add_key_with_function_call(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * public_key, * nonce, * allowance, * receiver_id, * function_names;
    if (!PyArg_ParseTuple(args, "OOOOOO", &promise_index, &public_key, &nonce, &allowance, &receiver_id, &function_names)) return NULL;
    near_api_ptr_t public_key_ptr = get_py_str_or_bytes(public_key);
    u128_t u128_allowance = py_int_to_u128(allowance);
    near_api_ptr_t receiver_id_ptr = get_py_str(receiver_id);
    near_api_ptr_t function_names_ptr = get_py_str(function_names);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    uint64_t nonce_val = PyLong_AsUnsignedLongLong(nonce);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_add_key_with_function_call(promise_idx,
        public_key_ptr.len, public_key_ptr.ptr, nonce_val, (uint64_t)&u128_allowance,
        receiver_id_ptr.len, receiver_id_ptr.ptr, function_names_ptr.len, function_names_ptr.ptr);
    Py_RETURN_NONE;
}

static PyObject* near_promise_batch_action_delete_key(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * public_key;
    if (!PyArg_ParseTuple(args, "OO", &promise_index, &public_key)) return NULL;
    near_api_ptr_t public_key_ptr = get_py_str_or_bytes(public_key);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_delete_key(promise_idx, public_key_ptr.len, public_key_ptr.ptr);
    Py_RETURN_NONE;
}

static PyObject* near_promise_batch_action_delete_account(PyObject* self, PyObject* args)
{
    PyObject* promise_index, * beneficiary_id;
    if (!PyArg_ParseTuple(args, "OO", &promise_index, &beneficiary_id)) return NULL;
    near_api_ptr_t beneficiary_id_ptr = get_py_str_or_bytes(beneficiary_id);
    uint64_t promise_idx = PyLong_AsUnsignedLongLong(promise_index);
    if (PyErr_Occurred()) return NULL;
    promise_batch_action_delete_account(promise_idx, beneficiary_id_ptr.len, beneficiary_id_ptr.ptr);
    Py_RETURN_NONE;
}

static PyObject* near_promise_yield_create(PyObject* self, PyObject* args)
{
    PyObject* function_name, * arguments, * gas, * gas_weight;
    if (!PyArg_ParseTuple(args, "OOOO", &function_name, &arguments, &gas, &gas_weight)) return NULL;
    near_api_ptr_t fn_ptr = get_py_str(function_name);
    near_api_ptr_t args_ptr = get_py_str(arguments);
    uint64_t gas_val = PyLong_AsUnsignedLongLong(gas);
    uint64_t gas_weight_val = PyLong_AsUnsignedLongLong(gas_weight);
    if (PyErr_Occurred()) return NULL;
    uint64_t promise_id = promise_yield_create(fn_ptr.len, fn_ptr.ptr, args_ptr.len, args_ptr.ptr, gas_val, gas_weight_val, DEFAULT_TEMP_REGISTER_ID);    
    return PyTuple_Pack(2, PyLong_FromUnsignedLongLong(promise_id), read_default_temp_register_as_str());
}

static PyObject* near_promise_yield_resume(PyObject* self, PyObject* args)
{
    PyObject* data_id, * payload;
    if (!PyArg_ParseTuple(args, "OO", &data_id, &payload)) return NULL;
    near_api_ptr_t data_id_ptr = get_py_str(data_id);
    near_api_ptr_t payload_ptr = get_py_str_or_bytes(payload);
    if (PyErr_Occurred()) return NULL;
    int result = promise_yield_resume(data_id_ptr.len, data_id_ptr.ptr, payload_ptr.len, payload_ptr.ptr);    
    return PyBool_FromLong(result);
}

static PyObject* near_promise_results_count(PyObject* self, PyObject *Py_UNUSED(dummy))
{
    return PyLong_FromUnsignedLongLong(promise_results_count());
}

static PyObject* near_promise_result(PyObject* self, PyObject* result_idx)
{
    uint64_t result = promise_result(PyLong_AsUnsignedLongLong(result_idx), DEFAULT_TEMP_REGISTER_ID);    
    return PyTuple_Pack(2, PyLong_FromUnsignedLongLong(result), read_default_temp_register_as_bytes());
}

static PyObject* near_promise_result_as_str(PyObject* self, PyObject* result_idx)
{
    uint64_t result = promise_result(PyLong_AsUnsignedLongLong(result_idx), DEFAULT_TEMP_REGISTER_ID);
    return PyTuple_Pack(2, PyLong_FromUnsignedLongLong(result), read_default_temp_register_as_str());
}

static PyObject* near_promise_return(PyObject* self, PyObject* promise_id)
{
    promise_return(PyLong_AsUnsignedLongLong(promise_id));    
    Py_RETURN_NONE;
}

// Storage API
static PyObject* near_storage_write(PyObject* self, PyObject* args)
{
    PyObject* key, * value;
    if (!PyArg_ParseTuple(args, "OO", &key, &value)) return NULL;
    near_api_ptr_t key_ptr = get_py_str_or_bytes(key);
    near_api_ptr_t value_ptr = get_py_str_or_bytes(value);    
    if (PyErr_Occurred()) return NULL;
    uint64_t result = storage_write(key_ptr.len, key_ptr.ptr, value_ptr.len, value_ptr.ptr, DEFAULT_TEMP_REGISTER_ID);    
    return (result == 1) ? read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID) : Py_None;
}

static PyObject* near_storage_read(PyObject* self, PyObject* key)
{
    near_api_ptr_t key_ptr = get_py_str_or_bytes(key);
    if (PyErr_Occurred()) return NULL;
    uint64_t result = storage_read(key_ptr.len, key_ptr.ptr, DEFAULT_TEMP_REGISTER_ID);
    return (result == 1) ? read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID) : Py_None;
}

static PyObject* near_storage_remove(PyObject* self, PyObject* key)
{
    near_api_ptr_t key_ptr = get_py_str_or_bytes(key);
    if (PyErr_Occurred()) return NULL;
    uint64_t result = storage_remove(key_ptr.len, key_ptr.ptr, DEFAULT_TEMP_REGISTER_ID);    
    return (result == 1) ? read_register_as_bytes(DEFAULT_TEMP_REGISTER_ID) : Py_None;
}

static PyObject* near_storage_has_key(PyObject* self, PyObject* key)
{
    near_api_ptr_t key_ptr = get_py_str_or_bytes(key);
    if (PyErr_Occurred()) return NULL;
    return PyLong_FromUnsignedLongLong(storage_has_key(key_ptr.len, key_ptr.ptr));
}

// Validator API
static PyObject* near_validator_stake(PyObject* self, PyObject* account_id)
{
    near_api_ptr_t acc_id_ptr = get_py_str(account_id);
    if (PyErr_Occurred()) return NULL;
    u128_t u128_stake = { 0, 0 };
    validator_stake(acc_id_ptr.len, acc_id_ptr.ptr, (uint64_t)&u128_stake);    
    return u128_to_py_int(&u128_stake);
}

static PyObject* near_validator_total_stake(PyObject* self, PyObject* Py_UNUSED(dummy))
{
    u128_t u128_stake = { 0, 0 };
    validator_total_stake((uint64_t)&u128_stake);    
    return u128_to_py_int(&u128_stake);
}

// Alt BN128 API
static PyObject* near_alt_bn128_g1_multiexp(PyObject* self, PyObject* value)
{
    near_api_ptr_t value_ptr = get_py_bytes(value);
    if (PyErr_Occurred()) return NULL;
    alt_bn128_g1_multiexp(value_ptr.len, value_ptr.ptr, DEFAULT_TEMP_REGISTER_ID);
    return read_default_temp_register_as_bytes();
}

static PyObject* near_alt_bn128_g1_sum(PyObject* self, PyObject* value)
{
    near_api_ptr_t value_ptr = get_py_bytes(value);
    if (PyErr_Occurred()) return NULL;
    alt_bn128_g1_sum(value_ptr.len, value_ptr.ptr, DEFAULT_TEMP_REGISTER_ID);
    return read_default_temp_register_as_bytes();
}

static PyObject* near_alt_bn128_pairing_check(PyObject* self, PyObject* value)
{
    near_api_ptr_t value_ptr = get_py_bytes(value);
    if (PyErr_Occurred()) return NULL;
    int result = alt_bn128_pairing_check(value_ptr.len, value_ptr.ptr);
    return PyBool_FromLong(result);
}

#endif // defined(__EMSCRIPTEN__) || defined(__wasi__)

// Module methods
static struct PyMethodDef NearMethods[] = {

#if defined(__EMSCRIPTEN__) || defined(__wasi__)

    {"export", near_export, METH_O},
    {"optimizer_inputs", near_optimizer_inputs_decorator_factory, METH_O},

    // Registers
    {"read_register", near_read_register, METH_VARARGS},
    {"read_register_as_str", near_read_register_as_str, METH_VARARGS},
    {"register_len", near_register_len, METH_VARARGS},
    {"write_register", near_write_register, METH_VARARGS},

    // Context API
    {"current_account_id", near_current_account_id, METH_NOARGS},
    {"signer_account_id", near_signer_account_id, METH_NOARGS},
    {"signer_account_pk", near_signer_account_pk, METH_NOARGS},
    {"predecessor_account_id", near_predecessor_account_id, METH_NOARGS},
    {"input", near_input, METH_NOARGS},
    {"input_as_str", near_input_as_str, METH_NOARGS},
    {"block_height", near_block_height, METH_NOARGS},
    {"block_index", near_block_height, METH_NOARGS}, // obsolete, todo: remove
    {"block_timestamp", near_block_timestamp, METH_NOARGS},
    {"epoch_height", near_epoch_height, METH_NOARGS},
    {"storage_usage", near_storage_usage, METH_NOARGS},

    // Economics API
    {"account_balance", near_account_balance, METH_NOARGS},
    {"account_locked_balance", near_account_locked_balance, METH_NOARGS},
    {"attached_deposit", near_attached_deposit, METH_NOARGS},
    {"prepaid_gas", near_prepaid_gas, METH_NOARGS},
    {"used_gas", near_used_gas, METH_NOARGS},

    // Math API
    {"random_seed", near_random_seed, METH_NOARGS},
    {"sha256", near_sha256, METH_O},
    {"keccak256", near_keccak256, METH_O},
    {"keccak512", near_keccak512, METH_O},
    {"ripemd160", near_ripemd160, METH_O},
    {"ecrecover", near_ecrecover, METH_VARARGS},
    {"ed25519_verify", near_ed25519_verify, METH_VARARGS},

    // Miscellaneous API
    {"value_return", near_value_return, METH_O},
    {"panic", near_panic, METH_NOARGS},
    {"panic_utf8", near_panic_utf8, METH_O},
    {"log_utf8", near_log_utf8, METH_O},
    {"log", near_log, METH_O},
    {"log_utf16", near_log_utf16, METH_O},
    {"abort", near_abort_, METH_O},

    // Promises API
    {"promise_create", near_promise_create, METH_VARARGS},
    {"promise_then", near_promise_then, METH_VARARGS},
    {"promise_and", near_promise_and, METH_O},
    {"promise_batch_create", near_promise_batch_create, METH_O},
    {"promise_batch_then", near_promise_batch_then, METH_VARARGS},
    {"promise_batch_action_create_account", near_promise_batch_action_create_account, METH_O},
    {"promise_batch_action_deploy_contract", near_promise_batch_action_deploy_contract, METH_VARARGS},
    {"promise_batch_action_function_call", near_promise_batch_action_function_call, METH_VARARGS},
    {"promise_batch_action_function_call_weight", near_promise_batch_action_function_call_weight, METH_VARARGS},
    {"promise_batch_action_transfer", near_promise_batch_action_transfer, METH_VARARGS},
    {"promise_batch_action_stake", near_promise_batch_action_stake, METH_VARARGS},
    {"promise_batch_action_add_key_with_full_access", near_promise_batch_action_add_key_with_full_access, METH_VARARGS},
    {"promise_batch_action_add_key_with_function_call", near_promise_batch_action_add_key_with_function_call, METH_VARARGS},
    {"promise_batch_action_delete_key", near_promise_batch_action_delete_key, METH_VARARGS},
    {"promise_batch_action_delete_account", near_promise_batch_action_delete_account, METH_VARARGS},
    {"promise_yield_create", near_promise_yield_create, METH_VARARGS},
    {"promise_yield_resume", near_promise_yield_resume, METH_VARARGS},
    {"promise_results_count", near_promise_results_count, METH_NOARGS},
    {"promise_result", near_promise_result, METH_O},
    {"promise_result_as_str", near_promise_result_as_str, METH_O},
    {"promise_return", near_promise_return, METH_O},

    // Storage API
    {"storage_write", near_storage_write, METH_VARARGS},
    {"storage_read", near_storage_read, METH_O},
    {"storage_remove", near_storage_remove, METH_O},
    {"storage_has_key", near_storage_has_key, METH_O},

    // Validator API
    {"validator_stake", near_validator_stake, METH_O},
    {"validator_total_stake", near_validator_total_stake, METH_NOARGS},

    // Alt BN128 API
    {"alt_bn128_g1_multiexp", near_alt_bn128_g1_multiexp, METH_O},
    {"alt_bn128_g1_sum", near_alt_bn128_g1_sum, METH_O},
    {"alt_bn128_pairing_check", near_alt_bn128_pairing_check, METH_O},
#endif // defined(__EMSCRIPTEN__) || defined(__wasi__)

    // Add all other functions following the same pattern
    {NULL, NULL, 0, NULL}
};

static PyModuleDef_Slot nearmodule_slots[] = {
    // {Py_mod_exec, nearmodule_exec},
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
    {0, NULL}
};

static struct PyModuleDef nearmodule = {
    PyModuleDef_HEAD_INIT,
    .m_name = "near",
    .m_size = 0,
    .m_methods = NearMethods,
    .m_slots = nearmodule_slots,
};


PyMODINIT_FUNC PyInit_near(void)
{
    return PyModuleDef_Init(&nearmodule);
}

