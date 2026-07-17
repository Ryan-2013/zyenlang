#ifndef ZYENLANG_C_ABI_H
#define ZYENLANG_C_ABI_H

#include <stdbool.h>
#include <stddef.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ZYENLANG_C_ABI_VERSION 2
#define ZYENLANG_C_ABI_TYPES 1

typedef struct ZL_ArcControl ZL_ArcControl;
typedef void (*ZL_ArcDrop)(void* payload);

struct ZL_ArcControl {
    atomic_uint refs;
    atomic_bool disposed;
    void* payload;
    ZL_ArcDrop drop;
};

static inline ZL_ArcControl* zl_arc_new(void* payload, ZL_ArcDrop drop) {
    ZL_ArcControl* owner = (ZL_ArcControl*)malloc(sizeof(ZL_ArcControl));
    if (!owner) {
        fprintf(stderr, "managed allocation failed\n");
        exit(1);
    }
    atomic_init(&owner->refs, 1u);
    atomic_init(&owner->disposed, false);
    owner->payload = payload;
    owner->drop = drop;
    return owner;
}

static inline void zl_arc_retain(ZL_ArcControl* owner) {
    if (owner) {
        atomic_fetch_add_explicit(&owner->refs, 1u, memory_order_relaxed);
    }
}

static inline bool zl_arc_dispose(ZL_ArcControl* owner) {
    if (!owner || atomic_exchange_explicit(&owner->disposed, true, memory_order_acq_rel)) {
        return false;
    }
    void* payload = owner->payload;
    owner->payload = NULL;
    if (owner->drop && payload) {
        owner->drop(payload);
    }
    return true;
}

static inline void zl_arc_release(ZL_ArcControl* owner) {
    if (!owner) {
        return;
    }
    if (atomic_fetch_sub_explicit(&owner->refs, 1u, memory_order_acq_rel) == 1u) {
        zl_arc_dispose(owner);
        free(owner);
    }
}

typedef void (*ZL_GenericFn)(void);

typedef struct ZL_Function {
    ZL_GenericFn call;
    void* env;
    ZL_ArcControl* owner;
    const char* signature;
} ZL_Function;

static inline ZL_Function zl_fn_none(const char* signature) {
    return (ZL_Function){ NULL, NULL, NULL, signature };
}

static inline bool zl_fn_is_none(ZL_Function fn) {
    return fn.call == NULL;
}

static inline bool zl_fn_matches(ZL_Function fn, const char* signature) {
    return fn.call != NULL && fn.signature != NULL && signature != NULL && strcmp(fn.signature, signature) == 0;
}

static inline ZL_Function zl_fn_retain(ZL_Function fn) {
    zl_arc_retain(fn.owner);
    return fn;
}

static inline void zl_fn_release(ZL_Function fn) {
    zl_arc_release(fn.owner);
}

static inline bool zl_fn_is_none_owned(ZL_Function fn) {
    bool result = zl_fn_is_none(fn);
    zl_fn_release(fn);
    return result;
}

static inline void zl_fn_assign(ZL_Function* target, ZL_Function value) {
    if (!target) {
        return;
    }
    ZL_Function retained = zl_fn_retain(value);
    zl_fn_release(*target);
    *target = retained;
}

static inline void zl_fn_clear(ZL_Function* target) {
    if (!target) {
        return;
    }
    const char* signature = target->signature;
    zl_fn_release(*target);
    *target = zl_fn_none(signature);
}

static inline void zl_fn_require(ZL_Function fn, const char* signature) {
    if (!fn.call) {
        fprintf(stderr, "None function call: %s\n", signature ? signature : "fn");
        exit(1);
    }
    if (!zl_fn_matches(fn, signature)) {
        fprintf(stderr, "function signature mismatch: expected %s, got %s\n",
            signature ? signature : "fn", fn.signature ? fn.signature : "unknown");
        exit(1);
    }
}

#define ZL_FN_CALL_AS(fn_value, call_type, ...) \
    (((call_type)((fn_value).call))((fn_value).env, ##__VA_ARGS__))

typedef enum ZL_ValueKind {
    ZL_VALUE_NONE = 0,
    ZL_VALUE_INT = 1,
    ZL_VALUE_FLOAT = 2,
    ZL_VALUE_STR = 3,
    ZL_VALUE_BOOL = 4,
    ZL_VALUE_PTR = 5,
    ZL_VALUE_LIST = 6
} ZL_ValueKind;

typedef struct ZL_ptr {
    void* addr;
    const char* type_name;
    int mem_id;
    bool owned;
    ZL_ArcControl* owner;
} ZL_ptr;

/* ptr remains the generated-C spelling; native modules should use ZL_ptr. */
typedef ZL_ptr ptr;

typedef struct ZL_List ZL_List;
typedef ZL_List ZL_list;

typedef struct ZL_Value {
    int kind;
    long long i;
    double f;
    const char* s;
    bool b;
    ZL_ptr p;
    ZL_List* l;
} ZL_Value;

/* Any is runtime-internal in ZyenLang source but layout-compatible in C. */
typedef ZL_Value Any;

struct ZL_List {
    ZL_Value* items;
    int len;
    int cap;
};

static inline ZL_ptr zl_ptr_borrow(void* addr, const char* type_name) {
    return (ZL_ptr){ addr, type_name, 0, false, NULL };
}

static inline void zl_ptr_default_drop(void* payload) {
    free(payload);
}

static inline ZL_ptr zl_ptr_adopt(void* addr, const char* type_name, ZL_ArcDrop drop) {
    if (!addr) {
        return (ZL_ptr){ NULL, type_name, 0, false, NULL };
    }
    ZL_ArcControl* owner = zl_arc_new(addr, drop ? drop : zl_ptr_default_drop);
    return (ZL_ptr){ addr, type_name, 0, true, owner };
}

static inline ZL_ptr zl_ptr_retain(ZL_ptr value) {
    zl_arc_retain(value.owner);
    return value;
}

static inline void zl_ptr_release(ZL_ptr value) {
    zl_arc_release(value.owner);
}

static inline void zl_ptr_assign(ZL_ptr* target, ZL_ptr value) {
    if (!target) {
        return;
    }
    ZL_ptr retained = zl_ptr_retain(value);
    zl_ptr_release(*target);
    *target = retained;
}

#ifdef __cplusplus
}
#endif

#endif
