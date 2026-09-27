ZLC_ABI(3)
ZLC_MODULE(counter)
ZLC_SOURCE("counter_zy.c")

ZLC_HANDLE(Counter, ZLC_DROP(counter_drop_adapter))
ZLC_CONST(INITIAL_VALUE, i32, 40)

ZLC_FN(create, counter_create_adapter, optional<owned<Counter>>,
    ZLC_PARAM(value, i32),
    ZLC_FAIL(null, counter_last_error_adapter))

ZLC_FN(add_bytes, counter_add_bytes_adapter, void,
    ZLC_PARAM(counter, borrowed<Counter>),
    ZLC_PARAM(bytes, Slice<u8>))

ZLC_FN(get, counter_get_adapter, i32,
    ZLC_PARAM(counter, borrowed<Counter>))
