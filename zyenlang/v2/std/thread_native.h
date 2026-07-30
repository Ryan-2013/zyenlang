#ifndef ZYENLANG_V2_THREAD_NATIVE_H
#define ZYENLANG_V2_THREAD_NATIVE_H

#include <stdint.h>

int32_t zy2_thread_sleep_ms(int32_t milliseconds);
int32_t zy2_thread_yield_now(void);
int32_t zy2_thread_cpu_count(void);

#endif
