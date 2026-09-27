# `std::time`

Cross-platform wall-clock and monotonic time:

```text
unix_seconds() i64
unix_milliseconds() i64
monotonic_milliseconds() i64
utc_iso8601() str
local_iso8601() str
sleep_ms(milliseconds: i32) i32
```

Use `monotonic_milliseconds` for elapsed durations. Wall-clock values can move
when the system clock changes. `utc_iso8601` ends in `Z`; `local_iso8601`
contains local civil time without claiming a UTC offset.
