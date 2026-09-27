#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif

#include "os_native.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <limits.h>
#include <sys/types.h>
#include <unistd.h>
#endif

#define ZY3_OS_BUFFER 32768u
static _Thread_local char zy3_os_message[512];

static void zy3_os_clear_error(void) {
    zy3_os_message[0] = '\0';
}

static void zy3_os_set_error(const char* action) {
#ifdef _WIN32
    snprintf(zy3_os_message, sizeof(zy3_os_message), "%s failed (Windows error %lu)", action, (unsigned long)GetLastError());
#else
    snprintf(zy3_os_message, sizeof(zy3_os_message), "%s failed: %s", action, strerror(errno));
#endif
}

static int zy3_os_valid_env_name(const char* value) {
    if (!value || !value[0] || strchr(value, '=') != NULL) {
        snprintf(zy3_os_message, sizeof(zy3_os_message), "environment variable name is invalid");
        return 0;
    }
    return 1;
}

#ifdef _WIN32
static wchar_t* zy3_os_to_wide(const char* value) {
    int length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value ? value : "", -1, NULL, 0);
    wchar_t* result;
    if (length <= 0) {
        snprintf(zy3_os_message, sizeof(zy3_os_message), "value is not valid UTF-8");
        return NULL;
    }
    result = (wchar_t*)malloc((size_t)length * sizeof(wchar_t));
    if (!result) {
        snprintf(zy3_os_message, sizeof(zy3_os_message), "operating-system query ran out of memory");
        return NULL;
    }
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value ? value : "", -1, result, length) <= 0) {
        free(result);
        snprintf(zy3_os_message, sizeof(zy3_os_message), "value is not valid UTF-8");
        return NULL;
    }
    return result;
}

static ZL_String zy3_os_from_wide(const wchar_t* value) {
    int length;
    char* buffer;
    ZL_String result;
    if (!value) return zl_string_copy("");
    length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, NULL, 0, NULL, NULL);
    if (length <= 0) {
        zy3_os_set_error("UTF-8 conversion");
        return zl_string_copy("");
    }
    buffer = (char*)malloc((size_t)length);
    if (!buffer) {
        snprintf(zy3_os_message, sizeof(zy3_os_message), "operating-system query ran out of memory");
        return zl_string_copy("");
    }
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, buffer, length, NULL, NULL) <= 0) {
        free(buffer);
        zy3_os_set_error("UTF-8 conversion");
        return zl_string_copy("");
    }
    result = zl_string_copy(buffer);
    free(buffer);
    return result;
}
#endif

ZL_String zy3_os_name(void) {
#ifdef _WIN32
    return zl_string_copy("windows");
#elif defined(__APPLE__)
    return zl_string_copy("macos");
#elif defined(__linux__)
    return zl_string_copy("linux");
#else
    return zl_string_copy("unix");
#endif
}

ZL_String zy3_os_architecture(void) {
#if defined(_M_X64) || defined(__x86_64__)
    return zl_string_copy("x86_64");
#elif defined(_M_ARM64) || defined(__aarch64__)
    return zl_string_copy("arm64");
#elif defined(_M_IX86) || defined(__i386__)
    return zl_string_copy("x86");
#elif defined(__arm__)
    return zl_string_copy("arm");
#else
    return zl_string_copy("unknown");
#endif
}

int64_t zy3_os_process_id(void) {
#ifdef _WIN32
    return (int64_t)GetCurrentProcessId();
#else
    return (int64_t)getpid();
#endif
}

ZL_String zy3_os_current_dir(void) {
    zy3_os_clear_error();
#ifdef _WIN32
    DWORD length = GetCurrentDirectoryW(0u, NULL);
    wchar_t* buffer;
    ZL_String result;
    if (length == 0u || length > ZY3_OS_BUFFER) {
        zy3_os_set_error("current directory query");
        return zl_string_copy("");
    }
    buffer = (wchar_t*)malloc((size_t)length * sizeof(wchar_t));
    if (!buffer) return zl_string_copy("");
    if (GetCurrentDirectoryW(length, buffer) == 0u) {
        free(buffer);
        zy3_os_set_error("current directory query");
        return zl_string_copy("");
    }
    result = zy3_os_from_wide(buffer);
    free(buffer);
    return result;
#else
    char* value = getcwd(NULL, 0u);
    ZL_String result;
    if (!value) {
        zy3_os_set_error("current directory query");
        return zl_string_copy("");
    }
    result = zl_string_copy(value);
    free(value);
    return result;
#endif
}

ZL_String zy3_os_home_dir(void) {
    zy3_os_clear_error();
#ifdef _WIN32
    wchar_t buffer[ZY3_OS_BUFFER];
    DWORD length = GetEnvironmentVariableW(L"USERPROFILE", buffer, ZY3_OS_BUFFER);
    if (length == 0u || length >= ZY3_OS_BUFFER) {
        zy3_os_set_error("home directory query");
        return zl_string_copy("");
    }
    return zy3_os_from_wide(buffer);
#else
    const char* value = getenv("HOME");
    if (!value || !value[0]) {
        snprintf(zy3_os_message, sizeof(zy3_os_message), "HOME is not set");
        return zl_string_copy("");
    }
    return zl_string_copy(value);
#endif
}

ZL_String zy3_os_temp_dir(void) {
    zy3_os_clear_error();
#ifdef _WIN32
    wchar_t buffer[ZY3_OS_BUFFER];
    DWORD length = GetTempPathW(ZY3_OS_BUFFER, buffer);
    if (length == 0u || length >= ZY3_OS_BUFFER) {
        zy3_os_set_error("temporary directory query");
        return zl_string_copy("");
    }
    return zy3_os_from_wide(buffer);
#else
    const char* value = getenv("TMPDIR");
    return zl_string_copy(value && value[0] ? value : "/tmp");
#endif
}

ZL_String zy3_os_hostname(void) {
    zy3_os_clear_error();
#ifdef _WIN32
    wchar_t buffer[256];
    DWORD length = (DWORD)(sizeof(buffer) / sizeof(buffer[0]));
    if (!GetComputerNameW(buffer, &length)) {
        zy3_os_set_error("hostname query");
        return zl_string_copy("");
    }
    buffer[length] = L'\0';
    return zy3_os_from_wide(buffer);
#else
    char buffer[256];
    if (gethostname(buffer, sizeof(buffer)) != 0) {
        zy3_os_set_error("hostname query");
        return zl_string_copy("");
    }
    buffer[sizeof(buffer) - 1u] = '\0';
    return zl_string_copy(buffer);
#endif
}

ZL_String zy3_os_env(ZL_String name_value) {
    const char* name = zl_string_data(name_value);
    zy3_os_clear_error();
    if (!zy3_os_valid_env_name(name)) return zl_string_copy("");
#ifdef _WIN32
    wchar_t* wide_name = zy3_os_to_wide(name);
    DWORD length;
    wchar_t* buffer;
    ZL_String result;
    if (!wide_name) return zl_string_copy("");
    SetLastError(ERROR_SUCCESS);
    length = GetEnvironmentVariableW(wide_name, NULL, 0u);
    if (length == 0u) {
        free(wide_name);
        return zl_string_copy("");
    }
    buffer = (wchar_t*)malloc((size_t)length * sizeof(wchar_t));
    if (!buffer) {
        free(wide_name);
        return zl_string_copy("");
    }
    if (GetEnvironmentVariableW(wide_name, buffer, length) == 0u) {
        free(wide_name);
        free(buffer);
        return zl_string_copy("");
    }
    result = zy3_os_from_wide(buffer);
    free(wide_name);
    free(buffer);
    return result;
#else
    const char* value = getenv(name);
    return zl_string_copy(value ? value : "");
#endif
}

bool zy3_os_has_env(ZL_String name_value) {
    const char* name = zl_string_data(name_value);
    zy3_os_clear_error();
    if (!zy3_os_valid_env_name(name)) return false;
#ifdef _WIN32
    wchar_t* wide_name = zy3_os_to_wide(name);
    DWORD length;
    DWORD error;
    if (!wide_name) return false;
    SetLastError(ERROR_SUCCESS);
    length = GetEnvironmentVariableW(wide_name, NULL, 0u);
    error = GetLastError();
    free(wide_name);
    return length > 0u || error != ERROR_ENVVAR_NOT_FOUND;
#else
    return getenv(name) != NULL;
#endif
}

int32_t zy3_os_set_env(ZL_String name_value, ZL_String value_value) {
    const char* name = zl_string_data(name_value);
    const char* value = zl_string_data(value_value);
    zy3_os_clear_error();
    if (!zy3_os_valid_env_name(name)) return -1;
#ifdef _WIN32
    wchar_t* wide_name = zy3_os_to_wide(name);
    wchar_t* wide_value = zy3_os_to_wide(value);
    int ok;
    if (!wide_name || !wide_value) {
        free(wide_name);
        free(wide_value);
        return -1;
    }
    ok = SetEnvironmentVariableW(wide_name, wide_value) != 0;
    free(wide_name);
    free(wide_value);
    if (!ok) {
        zy3_os_set_error("set environment variable");
        return -1;
    }
#else
    if (setenv(name, value ? value : "", 1) != 0) {
        zy3_os_set_error("set environment variable");
        return -1;
    }
#endif
    return 0;
}

int32_t zy3_os_unset_env(ZL_String name_value) {
    const char* name = zl_string_data(name_value);
    zy3_os_clear_error();
    if (!zy3_os_valid_env_name(name)) return -1;
#ifdef _WIN32
    wchar_t* wide_name = zy3_os_to_wide(name);
    int ok;
    if (!wide_name) return -1;
    ok = SetEnvironmentVariableW(wide_name, NULL) != 0;
    free(wide_name);
    if (!ok && GetLastError() != ERROR_ENVVAR_NOT_FOUND) {
        zy3_os_set_error("unset environment variable");
        return -1;
    }
#else
    if (unsetenv(name) != 0) {
        zy3_os_set_error("unset environment variable");
        return -1;
    }
#endif
    return 0;
}

ZL_String zy3_os_error(void) {
    return zl_string_copy(zy3_os_message);
}
