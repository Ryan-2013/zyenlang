ZLC_MODULE(native_abi)
ZLC_HEADER("native_abi.h")
ZLC_SOURCE("native_abi.c")

ZLC_STRUCT(NativePoint, ZLC_FIELD(x, float), ZLC_FIELD(y, float))

ZLC_FN(list_len, zlcm_abi_list_len, int, ZLC_PARAM(values, ZL_List))
ZLC_FN(make_list, zlcm_abi_make_list, ZL_list)
ZLC_FN(make_ptr, zlcm_abi_make_ptr, ZL_ptr<int>, ZLC_PARAM(value, int))
ZLC_FN(read_ptr, zlcm_abi_read_ptr, int, ZLC_PARAM(value, ZL_ptr))
ZLC_FN(move_point, zlcm_abi_move_point, NativePoint, ZLC_PARAM(point, NativePoint), ZLC_PARAM(dx, float), ZLC_PARAM(dy, float))
