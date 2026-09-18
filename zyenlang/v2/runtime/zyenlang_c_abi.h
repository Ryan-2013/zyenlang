#ifndef ZYENLANG_C_ABI_H
#define ZYENLANG_C_ABI_H

#define ZYENLANG_C_ABI_VERSION 2

#ifdef __cplusplus
#include <cstddef>
#include <cstring>
#else
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#endif

#ifndef ZYENLANG_API
#if defined(_WIN32) && defined(ZYENLANG_BUILD_SHARED)
#define ZYENLANG_API __declspec(dllexport)
#elif defined(_WIN32) && defined(ZYENLANG_USE_SHARED)
#define ZYENLANG_API __declspec(dllimport)
#elif defined(__GNUC__) || defined(__clang__)
#define ZYENLANG_API __attribute__((visibility("default")))
#else
#define ZYENLANG_API
#endif
#endif

typedef struct zy2_ArcControl ZL_ArcControl;
typedef void (*ZL_GenericCall)(void);

typedef struct ZL_Function {
    ZL_GenericCall call;
    void* env;
    ZL_ArcControl* owner;
    const char* signature;
} ZL_Function;

typedef struct ZL_String {
    const char* data;
    size_t byte_len;
    ZL_ArcControl* owner;
} ZL_String;

#ifdef __cplusplus
extern "C" {
#endif
ZYENLANG_API ZL_String zl_abi_string_copy_n(const char* data, size_t byte_len);
ZYENLANG_API ZL_String zl_abi_string_retain(ZL_String value);
ZYENLANG_API void zl_abi_string_release(ZL_String value);
ZYENLANG_API const char* zl_abi_string_data(ZL_String value);
ZYENLANG_API size_t zl_abi_string_byte_len(ZL_String value);
ZYENLANG_API ZL_Function zl_abi_fn_retain(ZL_Function value);
ZYENLANG_API void zl_abi_fn_release(ZL_Function value);
ZYENLANG_API void zl_abi_fn_assign(ZL_Function* target, ZL_Function value);
ZYENLANG_API void zl_abi_fn_clear(ZL_Function* target);
ZYENLANG_API bool zl_abi_fn_is_none(ZL_Function value);
ZYENLANG_API bool zl_abi_fn_matches(ZL_Function value, const char* signature);
#ifdef __cplusplus
}
#endif

static inline ZL_String zl_string_borrow_n(const char* data, size_t byte_len) {
#ifdef __cplusplus
    return ZL_String{data ? data : "", data ? byte_len : 0u, nullptr};
#else
    return (ZL_String){ .data = data ? data : "", .byte_len = data ? byte_len : 0u, .owner = NULL };
#endif
}

static inline ZL_String zl_string_borrow(const char* data) {
    return zl_string_borrow_n(data, data ? strlen(data) : 0u);
}

static inline ZL_String zl_string_copy_n(const char* data, size_t byte_len) {
    return zl_abi_string_copy_n(data, byte_len);
}

static inline ZL_String zl_string_copy(const char* data) {
    return zl_abi_string_copy_n(data, data ? strlen(data) : 0u);
}

static inline ZL_String zl_string_retain(ZL_String value) { return zl_abi_string_retain(value); }
static inline void zl_string_release(ZL_String value) { zl_abi_string_release(value); }
static inline const char* zl_string_data(ZL_String value) { return zl_abi_string_data(value); }
static inline size_t zl_string_byte_len(ZL_String value) { return zl_abi_string_byte_len(value); }
static inline ZL_Function zl_fn_retain(ZL_Function value) { return zl_abi_fn_retain(value); }
static inline void zl_fn_release(ZL_Function value) { zl_abi_fn_release(value); }
static inline void zl_fn_assign(ZL_Function* target, ZL_Function value) {
    zl_abi_fn_assign(target, value);
}
static inline void zl_fn_clear(ZL_Function* target) { zl_abi_fn_clear(target); }
static inline bool zl_fn_is_none(ZL_Function value) { return zl_abi_fn_is_none(value); }
static inline bool zl_fn_matches(ZL_Function value, const char* signature) {
    return zl_abi_fn_matches(value, signature);
}

#ifdef __cplusplus
#define ZL_FN_CALL_AS(TYPE, VALUE) reinterpret_cast<TYPE>((VALUE).call)
#else
#define ZL_FN_CALL_AS(TYPE, VALUE) ((TYPE)((VALUE).call))
#endif

#endif
