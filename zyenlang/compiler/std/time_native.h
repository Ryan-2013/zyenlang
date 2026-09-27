#ifndef ZYENLANG_TIME_NATIVE_H
#define ZYENLANG_TIME_NATIVE_H

#include <stdint.h>
#include "zyenlang_c_abi.h"

int64_t zy3_time_unix_seconds(void);
int64_t zy3_time_unix_milliseconds(void);
int64_t zy3_time_monotonic_milliseconds(void);
ZL_String zy3_time_utc_iso8601(void);
ZL_String zy3_time_local_iso8601(void);
int32_t zy3_time_sleep_ms(int32_t milliseconds);

#endif
