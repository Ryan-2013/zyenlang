#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#else
#define _POSIX_C_SOURCE 200809L
#endif

#include "time_native.h"

#include <stdio.h>
#include <time.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <errno.h>
#endif

static ZL_String zy3_time_format(int utc) {
    time_t value = time(NULL);
    struct tm parts;
    char text[40];
    int ok;
#ifdef _WIN32
    ok = utc ? gmtime_s(&parts, &value) == 0 : localtime_s(&parts, &value) == 0;
#else
    ok = utc ? gmtime_r(&value, &parts) != NULL : localtime_r(&value, &parts) != NULL;
#endif
    if (!ok) return zl_string_copy("");
    if (strftime(text, sizeof(text), utc ? "%Y-%m-%dT%H:%M:%SZ" : "%Y-%m-%dT%H:%M:%S", &parts) == 0u) {
        return zl_string_copy("");
    }
    return zl_string_copy(text);
}

int64_t zy3_time_unix_milliseconds(void) {
#ifdef _WIN32
    FILETIME value;
    ULARGE_INTEGER ticks;
    GetSystemTimeAsFileTime(&value);
    ticks.LowPart = value.dwLowDateTime;
    ticks.HighPart = value.dwHighDateTime;
    return (int64_t)((ticks.QuadPart - UINT64_C(116444736000000000)) / UINT64_C(10000));
#else
    struct timespec value;
    if (clock_gettime(CLOCK_REALTIME, &value) != 0) return -1;
    return (int64_t)value.tv_sec * INT64_C(1000) + (int64_t)(value.tv_nsec / 1000000L);
#endif
}

int64_t zy3_time_unix_seconds(void) {
    int64_t milliseconds = zy3_time_unix_milliseconds();
    return milliseconds < 0 ? -1 : milliseconds / INT64_C(1000);
}

int64_t zy3_time_monotonic_milliseconds(void) {
#ifdef _WIN32
    LARGE_INTEGER counter;
    LARGE_INTEGER frequency;
    if (!QueryPerformanceFrequency(&frequency) || !QueryPerformanceCounter(&counter) || frequency.QuadPart <= 0) {
        return -1;
    }
    return (int64_t)((counter.QuadPart * INT64_C(1000)) / frequency.QuadPart);
#else
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return -1;
    return (int64_t)value.tv_sec * INT64_C(1000) + (int64_t)(value.tv_nsec / 1000000L);
#endif
}

ZL_String zy3_time_utc_iso8601(void) {
    return zy3_time_format(1);
}

ZL_String zy3_time_local_iso8601(void) {
    return zy3_time_format(0);
}

int32_t zy3_time_sleep_ms(int32_t milliseconds) {
    if (milliseconds < 0) return -1;
#ifdef _WIN32
    Sleep((DWORD)milliseconds);
    return 0;
#else
    struct timespec delay;
    delay.tv_sec = milliseconds / 1000;
    delay.tv_nsec = (long)(milliseconds % 1000) * 1000000L;
    while (nanosleep(&delay, &delay) != 0) {
        if (errno != EINTR) return -1;
    }
    return 0;
#endif
}
