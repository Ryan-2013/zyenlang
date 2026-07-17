#ifndef ZLCM_NATIVE_ABI_TEST_H
#define ZLCM_NATIVE_ABI_TEST_H

#include <zyenlang_c_abi.h>

typedef struct NativePoint {
    double x;
    double y;
} NativePoint;

int zlcm_abi_list_len(ZL_List* values);
ZL_List zlcm_abi_make_list(void);
ZL_ptr zlcm_abi_make_ptr(int value);
int zlcm_abi_read_ptr(ZL_ptr value);
NativePoint zlcm_abi_move_point(NativePoint point, double dx, double dy);

#endif
