#include "native_abi.h"

int zlcm_abi_list_len(ZL_List* values) {
    return values ? values->len : 0;
}

ZL_List zlcm_abi_make_list(void) {
    static ZL_Value items[2];
    items[0] = (ZL_Value){ .kind = ZL_VALUE_INT, .i = 20 };
    items[1] = (ZL_Value){ .kind = ZL_VALUE_INT, .i = 22 };
    return (ZL_List){ .items = items, .len = 2, .cap = 2 };
}

ZL_ptr zlcm_abi_make_ptr(int value) {
    static int storage;
    storage = value;
    return (ZL_ptr){ .addr = &storage, .type_name = "int", .mem_id = 0, .owned = false };
}

int zlcm_abi_read_ptr(ZL_ptr value) {
    if (!value.addr) {
        return 0;
    }
    return *((int*)value.addr);
}

NativePoint zlcm_abi_move_point(NativePoint point, double dx, double dy) {
    point.x += dx;
    point.y += dy;
    return point;
}
