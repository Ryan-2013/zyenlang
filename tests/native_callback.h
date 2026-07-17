#ifndef ZLCM_NATIVE_CALLBACK_TEST_H
#define ZLCM_NATIVE_CALLBACK_TEST_H

#include <zyenlang_c_abi.h>

typedef struct NativeCallbackBox {
    ZL_Function callback;
} NativeCallbackBox;

void zlcm_callback_set(ZL_Function callback);
void zlcm_callback_clear(void);
int zlcm_callback_invoke(int value);
ZL_Function zlcm_callback_echo(ZL_Function callback);
NativeCallbackBox zlcm_callback_make_box(ZL_Function callback);
int zlcm_callback_call_box(NativeCallbackBox box, int value);
void zlcm_arc_reset_drop_count(void);
int zlcm_arc_drop_count(void);
ZL_ptr zlcm_arc_make_owned(int value);
ZL_ptr zlcm_arc_make_borrowed(int value);
int zlcm_arc_read_pointer(ZL_ptr value);
void zlcm_arc_assign_pointer(ZL_ptr slot, ZL_ptr value);

#endif
