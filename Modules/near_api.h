#ifndef NEAR_API_H
#define NEAR_API_H

#include <stdint.h>

#define NORETURN __attribute((noreturn))
#define NEAR_API_IMPORT(NAME) __attribute__((import_module("env"), import_name(#NAME))) 

// NEAR API reference: https://github.com/near/near-sdk-rs/blob/master/near-sys/src/lib.rs

// Registers
NEAR_API_IMPORT(read_register) void read_register(uint64_t register_id, uint64_t ptr);
NEAR_API_IMPORT(register_len) uint64_t register_len(uint64_t register_id);
NEAR_API_IMPORT(write_register) void write_register(uint64_t register_id, uint64_t data_len, uint64_t data_ptr);

// Context API
NEAR_API_IMPORT(current_account_id) void current_account_id(uint64_t register_id);
NEAR_API_IMPORT(signer_account_id) void signer_account_id(uint64_t register_id);
NEAR_API_IMPORT(signer_account_pk) void signer_account_pk(uint64_t register_id);
NEAR_API_IMPORT(predecessor_account_id) void predecessor_account_id(uint64_t register_id);
NEAR_API_IMPORT(input) void input(uint64_t register_id);
NEAR_API_IMPORT(block_index) uint64_t block_index(void);
NEAR_API_IMPORT(block_height) uint64_t block_height(void);
NEAR_API_IMPORT(block_timestamp) uint64_t block_timestamp(void);
NEAR_API_IMPORT(epoch_height) uint64_t epoch_height(void);
NEAR_API_IMPORT(storage_usage) uint64_t storage_usage(void);

// Economics API
NEAR_API_IMPORT(account_balance) void account_balance(uint64_t balance_ptr);
NEAR_API_IMPORT(account_locked_balance) void account_locked_balance(uint64_t balance_ptr);
NEAR_API_IMPORT(attached_deposit) void attached_deposit(uint64_t balance_ptr);
NEAR_API_IMPORT(prepaid_gas) uint64_t prepaid_gas(void);
NEAR_API_IMPORT(used_gas) uint64_t used_gas(void);

// Math API
NEAR_API_IMPORT(random_seed) void random_seed(uint64_t register_id);
NEAR_API_IMPORT(sha256) void sha256(uint64_t value_len, uint64_t value_ptr, uint64_t register_id);
NEAR_API_IMPORT(keccak256) void keccak256(uint64_t value_len, uint64_t value_ptr, uint64_t register_id);
NEAR_API_IMPORT(keccak512) void keccak512(uint64_t value_len, uint64_t value_ptr, uint64_t register_id);
NEAR_API_IMPORT(ripemd160) void ripemd160(uint64_t value_len, uint64_t value_ptr, uint64_t register_id);
NEAR_API_IMPORT(ecrecover) uint64_t ecrecover(
  uint64_t hash_len, uint64_t hash_ptr, uint64_t sig_len, uint64_t sig_ptr,
  uint64_t v, uint64_t malleability_flag, uint64_t register_id
);
NEAR_API_IMPORT(ed25519_verify) uint64_t ed25519_verify(
  uint64_t sig_len, uint64_t sig_ptr, uint64_t msg_len, uint64_t msg_ptr,
  uint64_t pub_key_len, uint64_t pub_key_ptr
);

// Miscellaneous API
NEAR_API_IMPORT(value_return) void value_return(uint64_t value_len, uint64_t value_ptr);
NEAR_API_IMPORT(panic) NORETURN void panic(void);
NEAR_API_IMPORT(panic_utf8) NORETURN void panic_utf8(uint64_t len, uint64_t ptr);
NEAR_API_IMPORT(log_utf8) void log_utf8(uint64_t len, uint64_t ptr);
NEAR_API_IMPORT(log_utf16) void log_utf16(uint64_t len, uint64_t ptr);
NEAR_API_IMPORT(abort) NORETURN void near_abort(uint32_t msg_ptr, uint32_t filename_ptr, uint32_t line, uint32_t col);

// Promises API
NEAR_API_IMPORT(promise_create) uint64_t promise_create(
  uint64_t account_id_len, uint64_t account_id_ptr, uint64_t function_name_len,
  uint64_t function_name_ptr, uint64_t arguments_len, uint64_t arguments_ptr,
  uint64_t amount_ptr, uint64_t gas
);
NEAR_API_IMPORT(promise_then) uint64_t promise_then(
  uint64_t promise_index, uint64_t account_id_len, uint64_t account_id_ptr,
  uint64_t function_name_len, uint64_t function_name_ptr, uint64_t arguments_len,
  uint64_t arguments_ptr, uint64_t amount_ptr, uint64_t gas
);
NEAR_API_IMPORT(promise_and) uint64_t promise_and(uint64_t promise_idx_ptr, uint64_t promise_idx_count);
NEAR_API_IMPORT(promise_batch_create) uint64_t promise_batch_create(uint64_t account_id_len, uint64_t account_id_ptr);
NEAR_API_IMPORT(promise_batch_then) uint64_t promise_batch_then(uint64_t promise_index, uint64_t account_id_len, uint64_t account_id_ptr);

// Promise API actions
NEAR_API_IMPORT(promise_batch_action_create_account) void promise_batch_action_create_account(uint64_t promise_index);
NEAR_API_IMPORT(promise_batch_action_deploy_contract) void promise_batch_action_deploy_contract(
  uint64_t promise_index, uint64_t code_len, uint64_t code_ptr);
NEAR_API_IMPORT(promise_batch_action_function_call) void promise_batch_action_function_call(
  uint64_t promise_index, uint64_t function_name_len, uint64_t function_name_ptr,
  uint64_t arguments_len, uint64_t arguments_ptr, uint64_t amount_ptr, uint64_t gas
);
NEAR_API_IMPORT(promise_batch_action_function_call_weight) void promise_batch_action_function_call_weight(
  uint64_t promise_index, uint64_t function_name_len, uint64_t function_name_ptr,
  uint64_t arguments_len, uint64_t arguments_ptr, uint64_t amount_ptr, uint64_t gas, uint64_t weight
);
NEAR_API_IMPORT(promise_batch_action_transfer) void promise_batch_action_transfer(uint64_t promise_index, uint64_t amount_ptr);
NEAR_API_IMPORT(promise_batch_action_stake) void promise_batch_action_stake(
  uint64_t promise_index, uint64_t amount_ptr, uint64_t public_key_len, uint64_t public_key_ptr
);
NEAR_API_IMPORT(promise_batch_action_add_key_with_full_access) void promise_batch_action_add_key_with_full_access(
  uint64_t promise_index, uint64_t public_key_len, uint64_t public_key_ptr, uint64_t nonce
);
NEAR_API_IMPORT(promise_batch_action_add_key_with_function_call) void promise_batch_action_add_key_with_function_call(
  uint64_t promise_index, uint64_t public_key_len, uint64_t public_key_ptr, uint64_t nonce,
  uint64_t allowance_ptr, uint64_t receiver_id_len, uint64_t receiver_id_ptr,
  uint64_t function_names_len, uint64_t function_names_ptr
);
NEAR_API_IMPORT(promise_batch_action_delete_key) void promise_batch_action_delete_key(
  uint64_t promise_index, uint64_t public_key_len, uint64_t public_key_ptr);
NEAR_API_IMPORT(promise_batch_action_delete_account) void promise_batch_action_delete_account(
  uint64_t promise_index, uint64_t beneficiary_id_len, uint64_t beneficiary_id_ptr
);
NEAR_API_IMPORT(promise_yield_create) uint64_t promise_yield_create(
  uint64_t function_name_len, uint64_t function_name_ptr, uint64_t arguments_len,
  uint64_t arguments_ptr, uint64_t gas, uint64_t gas_weight, uint64_t register_id
);
NEAR_API_IMPORT(promise_yield_resume) uint32_t promise_yield_resume(
  uint64_t data_id_len, uint64_t data_id_ptr, uint64_t payload_len, uint64_t payload_ptr
);

// Promise API results
NEAR_API_IMPORT(promise_results_count) uint64_t promise_results_count(void);
NEAR_API_IMPORT(promise_result) uint64_t promise_result(uint64_t result_idx, uint64_t register_id);
NEAR_API_IMPORT(promise_return) void promise_return(uint64_t promise_id);

// Storage API
NEAR_API_IMPORT(storage_write) uint64_t storage_write(
  uint64_t key_len, uint64_t key_ptr, uint64_t value_len, uint64_t value_ptr, uint64_t register_id
);
NEAR_API_IMPORT(storage_read) uint64_t storage_read(uint64_t key_len, uint64_t key_ptr, uint64_t register_id);
NEAR_API_IMPORT(storage_remove) uint64_t storage_remove(uint64_t key_len, uint64_t key_ptr, uint64_t register_id);
NEAR_API_IMPORT(storage_has_key) uint64_t storage_has_key(uint64_t key_len, uint64_t key_ptr);

// Validator API
NEAR_API_IMPORT(validator_stake) void validator_stake(uint64_t account_id_len, uint64_t account_id_ptr, uint64_t stake_ptr);
NEAR_API_IMPORT(validator_total_stake) void validator_total_stake(uint64_t stake_ptr);

// Alt BN128
NEAR_API_IMPORT(alt_bn128_g1_multiexp) void alt_bn128_g1_multiexp(uint64_t value_len, uint64_t value_ptr, uint64_t register_id);
NEAR_API_IMPORT(alt_bn128_g1_sum) void alt_bn128_g1_sum(uint64_t value_len, uint64_t value_ptr, uint64_t register_id);
NEAR_API_IMPORT(alt_bn128_pairing_check) uint64_t alt_bn128_pairing_check(uint64_t value_len, uint64_t value_ptr);

// helper macros for passing context to NEAR API abort() call

NORETURN void near_abort_impl(const char *msg, const char *func, const char *filename, uint32_t line, uint32_t col);

#define NEAR_ABORT() near_abort_impl(NULL, __func__, __FILE__, __LINE__, 0)
#define NEAR_ABORT_MSG(msg) near_abort_impl(msg, __func__, __FILE__, __LINE__, 0)

#endif // NEAR_API_H
