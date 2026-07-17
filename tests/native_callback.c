#include "native_callback.h"

typedef int (*ZL_IntCallback)(void* env, int value);

static ZL_Function stored_callback;
static const char* callback_signature = "fn(int)->int";
static int pointer_drop_count;
static int borrowed_storage;

static void count_pointer_drop(void* payload) {
    pointer_drop_count += 1;
    free(payload);
}

static int call_callback(ZL_Function callback, int value) {
    if (!zl_fn_matches(callback, callback_signature)) {
        return -777;
    }
    return ZL_FN_CALL_AS(callback, ZL_IntCallback, value);
}

void zlcm_callback_set(ZL_Function callback) {
    zl_fn_assign(&stored_callback, callback);
}

void zlcm_callback_clear(void) {
    zl_fn_clear(&stored_callback);
}

int zlcm_callback_invoke(int value) {
    return call_callback(stored_callback, value);
}

ZL_Function zlcm_callback_echo(ZL_Function callback) {
    return zl_fn_retain(callback);
}

NativeCallbackBox zlcm_callback_make_box(ZL_Function callback) {
    return (NativeCallbackBox){ .callback = zl_fn_retain(callback) };
}

int zlcm_callback_call_box(NativeCallbackBox box, int value) {
    return call_callback(box.callback, value);
}

void zlcm_arc_reset_drop_count(void) {
    pointer_drop_count = 0;
}

int zlcm_arc_drop_count(void) {
    return pointer_drop_count;
}

ZL_ptr zlcm_arc_make_owned(int value) {
    int* payload = (int*)malloc(sizeof(int));
    if (!payload) {
        return zl_ptr_borrow(NULL, "int");
    }
    *payload = value;
    return zl_ptr_adopt(payload, "int", count_pointer_drop);
}

ZL_ptr zlcm_arc_make_borrowed(int value) {
    borrowed_storage = value;
    return zl_ptr_borrow(&borrowed_storage, "int");
}

int zlcm_arc_read_pointer(ZL_ptr value) {
    return value.addr ? *((int*)value.addr) : -1;
}

void zlcm_arc_assign_pointer(ZL_ptr slot, ZL_ptr value) {
    if (!slot.addr) {
        return;
    }
    zl_ptr_assign((ZL_ptr*)slot.addr, value);
}
