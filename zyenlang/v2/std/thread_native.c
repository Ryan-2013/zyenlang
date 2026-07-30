#define _POSIX_C_SOURCE 200809L

#include "thread_native.h"

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

int32_t zy2_thread_sleep_ms(int32_t milliseconds) {
    if (milliseconds < 0) return -1;
    Sleep((DWORD)milliseconds);
    return 0;
}

int32_t zy2_thread_yield_now(void) {
    SwitchToThread();
    return 0;
}

int32_t zy2_thread_cpu_count(void) {
    DWORD count = GetActiveProcessorCount(ALL_PROCESSOR_GROUPS);
    return count > INT32_MAX ? INT32_MAX : (int32_t)count;
}
#else
#include <errno.h>
#include <sched.h>
#include <time.h>
#include <unistd.h>

int32_t zy2_thread_sleep_ms(int32_t milliseconds) {
    struct timespec delay;
    if (milliseconds < 0) return -1;
    delay.tv_sec = milliseconds / 1000;
    delay.tv_nsec = (long)(milliseconds % 1000) * 1000000L;
    while (nanosleep(&delay, &delay) != 0) {
        if (errno != EINTR) return -1;
    }
    return 0;
}

int32_t zy2_thread_yield_now(void) {
    return sched_yield() == 0 ? 0 : -1;
}

int32_t zy2_thread_cpu_count(void) {
    long count = sysconf(_SC_NPROCESSORS_ONLN);
    if (count < 1) return 1;
    return count > INT32_MAX ? INT32_MAX : (int32_t)count;
}
#endif
