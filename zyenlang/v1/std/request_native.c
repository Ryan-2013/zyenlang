#define _POSIX_C_SOURCE 200809L

#include "request_native.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char* zl_request_data = NULL;
static size_t zl_request_size = 0;
static int zl_request_code = 0;
static char zl_request_message[512] = "";

static void zl_request_reset(void) {
    free(zl_request_data);
    zl_request_data = (char*)malloc(1);
    if (zl_request_data) {
        zl_request_data[0] = '\0';
    }
    zl_request_size = 0;
    zl_request_code = 0;
    zl_request_message[0] = '\0';
}

static void zl_request_set_error(const char* format, ...) {
    va_list args;
    va_start(args, format);
    vsnprintf(zl_request_message, sizeof(zl_request_message), format, args);
    va_end(args);
}

static int zl_request_append(const void* bytes, size_t length) {
    char* grown;
    if (length == 0) {
        return 0;
    }
    if (length > (size_t)-1 - zl_request_size - 1) {
        zl_request_set_error("response body is too large");
        return -1;
    }
    grown = (char*)realloc(zl_request_data, zl_request_size + length + 1);
    if (!grown) {
        zl_request_set_error("out of memory while reading response");
        return -1;
    }
    zl_request_data = grown;
    memcpy(zl_request_data + zl_request_size, bytes, length);
    zl_request_size += length;
    zl_request_data[zl_request_size] = '\0';
    return 0;
}

#ifdef _WIN32

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

static wchar_t* zl_request_utf8_to_wide(const char* text) {
    int count;
    wchar_t* result;
    if (!text) {
        text = "";
    }
    count = MultiByteToWideChar(CP_UTF8, 0, text, -1, NULL, 0);
    if (count <= 0) {
        return NULL;
    }
    result = (wchar_t*)malloc((size_t)count * sizeof(wchar_t));
    if (!result || MultiByteToWideChar(CP_UTF8, 0, text, -1, result, count) <= 0) {
        free(result);
        return NULL;
    }
    return result;
}

static wchar_t* zl_request_wide_slice(const wchar_t* start, DWORD length, int leading_slash) {
    size_t extra = leading_slash && (length == 0 || start[0] != L'/') ? 1u : 0u;
    wchar_t* result = (wchar_t*)malloc(((size_t)length + extra + 1u) * sizeof(wchar_t));
    if (!result) {
        return NULL;
    }
    if (extra) {
        result[0] = L'/';
    }
    if (length) {
        memcpy(result + extra, start, (size_t)length * sizeof(wchar_t));
    }
    result[length + extra] = L'\0';
    return result;
}

static int zl_request_windows(const char* method, const char* url, const char* body, const char* content_type, int timeout_ms) {
    wchar_t* wide_url = NULL;
    wchar_t* wide_method = NULL;
    wchar_t* host = NULL;
    wchar_t* path = NULL;
    wchar_t* headers = NULL;
    HINTERNET session = NULL;
    HINTERNET connection = NULL;
    HINTERNET request = NULL;
    URL_COMPONENTS parts;
    DWORD status_size = sizeof(DWORD);
    DWORD status = 0;
    int result = -1;
    const char* payload = body ? body : "";
    DWORD payload_size = (DWORD)strlen(payload);

    wide_url = zl_request_utf8_to_wide(url);
    wide_method = zl_request_utf8_to_wide(method);
    if (!wide_url || !wide_method) {
        zl_request_set_error("URL or method is not valid UTF-8");
        goto done;
    }

    memset(&parts, 0, sizeof(parts));
    parts.dwStructSize = sizeof(parts);
    parts.dwHostNameLength = (DWORD)-1;
    parts.dwUrlPathLength = (DWORD)-1;
    parts.dwExtraInfoLength = (DWORD)-1;
    if (!WinHttpCrackUrl(wide_url, 0, 0, &parts)) {
        zl_request_set_error("invalid URL (WinHTTP error %lu)", (unsigned long)GetLastError());
        goto done;
    }
    if (parts.nScheme != INTERNET_SCHEME_HTTP && parts.nScheme != INTERNET_SCHEME_HTTPS) {
        zl_request_set_error("request URL must use http:// or https://");
        goto done;
    }

    host = zl_request_wide_slice(parts.lpszHostName, parts.dwHostNameLength, 0);
    {
        DWORD combined = parts.dwUrlPathLength + parts.dwExtraInfoLength;
        path = zl_request_wide_slice(parts.lpszUrlPath, combined, 1);
    }
    if (!host || !path) {
        zl_request_set_error("out of memory while parsing URL");
        goto done;
    }

    session = WinHttpOpen(L"ZyenLang-request/1", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                          WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) {
        zl_request_set_error("WinHttpOpen failed (%lu)", (unsigned long)GetLastError());
        goto done;
    }
    if (timeout_ms <= 0) {
        timeout_ms = 30000;
    }
    WinHttpSetTimeouts(session, timeout_ms, timeout_ms, timeout_ms, timeout_ms);
    connection = WinHttpConnect(session, host, parts.nPort, 0);
    if (!connection) {
        zl_request_set_error("WinHttpConnect failed (%lu)", (unsigned long)GetLastError());
        goto done;
    }
    request = WinHttpOpenRequest(
        connection, wide_method, path, NULL, WINHTTP_NO_REFERER,
        WINHTTP_DEFAULT_ACCEPT_TYPES,
        parts.nScheme == INTERNET_SCHEME_HTTPS ? WINHTTP_FLAG_SECURE : 0
    );
    if (!request) {
        zl_request_set_error("WinHttpOpenRequest failed (%lu)", (unsigned long)GetLastError());
        goto done;
    }

    if (content_type && content_type[0]) {
        size_t header_size = strlen(content_type) + 32;
        char* header_utf8 = (char*)malloc(header_size);
        if (!header_utf8) {
            zl_request_set_error("out of memory while creating headers");
            goto done;
        }
        snprintf(header_utf8, header_size, "Content-Type: %s", content_type);
        headers = zl_request_utf8_to_wide(header_utf8);
        free(header_utf8);
        if (!headers) {
            zl_request_set_error("content type is not valid UTF-8");
            goto done;
        }
    }

    if (!WinHttpSendRequest(
            request,
            headers ? headers : WINHTTP_NO_ADDITIONAL_HEADERS,
            headers ? (DWORD)-1L : 0,
            payload_size ? (LPVOID)payload : WINHTTP_NO_REQUEST_DATA,
            payload_size, payload_size, 0)) {
        zl_request_set_error("WinHttpSendRequest failed (%lu)", (unsigned long)GetLastError());
        goto done;
    }
    if (!WinHttpReceiveResponse(request, NULL)) {
        zl_request_set_error("WinHttpReceiveResponse failed (%lu)", (unsigned long)GetLastError());
        goto done;
    }
    if (!WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                             WINHTTP_HEADER_NAME_BY_INDEX, &status, &status_size,
                             WINHTTP_NO_HEADER_INDEX)) {
        zl_request_set_error("cannot read HTTP status (%lu)", (unsigned long)GetLastError());
        goto done;
    }
    zl_request_code = (int)status;

    for (;;) {
        DWORD available = 0;
        DWORD read = 0;
        char* chunk;
        if (!WinHttpQueryDataAvailable(request, &available)) {
            zl_request_set_error("cannot read response size (%lu)", (unsigned long)GetLastError());
            goto done;
        }
        if (available == 0) {
            break;
        }
        chunk = (char*)malloc(available);
        if (!chunk) {
            zl_request_set_error("out of memory while reading response");
            goto done;
        }
        if (!WinHttpReadData(request, chunk, available, &read)) {
            free(chunk);
            zl_request_set_error("cannot read response body (%lu)", (unsigned long)GetLastError());
            goto done;
        }
        if (zl_request_append(chunk, read) != 0) {
            free(chunk);
            goto done;
        }
        free(chunk);
    }
    result = 0;

done:
    if (request) WinHttpCloseHandle(request);
    if (connection) WinHttpCloseHandle(connection);
    if (session) WinHttpCloseHandle(session);
    free(headers);
    free(path);
    free(host);
    free(wide_method);
    free(wide_url);
    return result;
}

#else

#include <dlfcn.h>

typedef void CURL;
typedef int CURLcode;
typedef int CURLoption;
typedef int CURLINFO;
struct curl_slist { char* data; struct curl_slist* next; };

enum {
    ZLCURLE_OK = 0,
    ZLCURLOPT_WRITEDATA = 10001,
    ZLCURLOPT_URL = 10002,
    ZLCURLOPT_ERRORBUFFER = 10010,
    ZLCURLOPT_WRITEFUNCTION = 20011,
    ZLCURLOPT_POSTFIELDS = 10015,
    ZLCURLOPT_USERAGENT = 10018,
    ZLCURLOPT_HTTPHEADER = 10023,
    ZLCURLOPT_CUSTOMREQUEST = 10036,
    ZLCURLOPT_POSTFIELDSIZE = 60,
    ZLCURLOPT_FOLLOWLOCATION = 52,
    ZLCURLOPT_NOSIGNAL = 99,
    ZLCURLOPT_ACCEPT_ENCODING = 10102,
    ZLCURLOPT_TIMEOUT_MS = 155,
    ZLCURLINFO_RESPONSE_CODE = 0x200002
};

typedef struct {
    void* library;
    CURL* (*easy_init)(void);
    void (*easy_cleanup)(CURL*);
    CURLcode (*easy_setopt)(CURL*, CURLoption, ...);
    CURLcode (*easy_perform)(CURL*);
    CURLcode (*easy_getinfo)(CURL*, CURLINFO, ...);
    const char* (*easy_strerror)(CURLcode);
    struct curl_slist* (*slist_append)(struct curl_slist*, const char*);
    void (*slist_free_all)(struct curl_slist*);
    CURLcode (*global_init)(long);
} ZL_CurlApi;

static ZL_CurlApi zl_curl;

static int zl_request_load_curl(void) {
    const char* names[] = {
#ifdef __APPLE__
        "libcurl.4.dylib", "/usr/lib/libcurl.4.dylib", "libcurl.dylib",
#else
        "libcurl.so.4", "libcurl.so",
#endif
        NULL
    };
    int i;
    if (zl_curl.library) {
        return 0;
    }
    for (i = 0; names[i]; ++i) {
        zl_curl.library = dlopen(names[i], RTLD_NOW | RTLD_LOCAL);
        if (zl_curl.library) break;
    }
    if (!zl_curl.library) {
        zl_request_set_error("system libcurl is not installed");
        return -1;
    }
#define ZL_CURL_LOAD(field, symbol) do { *(void**)(&zl_curl.field) = dlsym(zl_curl.library, symbol); if (!zl_curl.field) { zl_request_set_error("libcurl is missing %s", symbol); return -1; } } while (0)
    ZL_CURL_LOAD(easy_init, "curl_easy_init");
    ZL_CURL_LOAD(easy_cleanup, "curl_easy_cleanup");
    ZL_CURL_LOAD(easy_setopt, "curl_easy_setopt");
    ZL_CURL_LOAD(easy_perform, "curl_easy_perform");
    ZL_CURL_LOAD(easy_getinfo, "curl_easy_getinfo");
    ZL_CURL_LOAD(easy_strerror, "curl_easy_strerror");
    ZL_CURL_LOAD(slist_append, "curl_slist_append");
    ZL_CURL_LOAD(slist_free_all, "curl_slist_free_all");
    ZL_CURL_LOAD(global_init, "curl_global_init");
#undef ZL_CURL_LOAD
    if (zl_curl.global_init(3L) != ZLCURLE_OK) {
        zl_request_set_error("curl_global_init failed");
        return -1;
    }
    return 0;
}

static size_t zl_request_curl_write(char* data, size_t size, size_t count, void* unused) {
    size_t length = size * count;
    (void)unused;
    return zl_request_append(data, length) == 0 ? length : 0;
}

static int zl_request_curl(const char* method, const char* url, const char* body, const char* content_type, int timeout_ms) {
    CURL* handle;
    CURLcode code;
    long status = 0;
    char curl_error[256] = "";
    struct curl_slist* headers = NULL;
    char content_header[512];
    const char* payload = body ? body : "";
    if (zl_request_load_curl() != 0) {
        return -1;
    }
    handle = zl_curl.easy_init();
    if (!handle) {
        zl_request_set_error("curl_easy_init failed");
        return -1;
    }
    if (timeout_ms <= 0) timeout_ms = 30000;
    zl_curl.easy_setopt(handle, ZLCURLOPT_URL, url);
    zl_curl.easy_setopt(handle, ZLCURLOPT_WRITEFUNCTION, zl_request_curl_write);
    zl_curl.easy_setopt(handle, ZLCURLOPT_WRITEDATA, NULL);
    zl_curl.easy_setopt(handle, ZLCURLOPT_ERRORBUFFER, curl_error);
    zl_curl.easy_setopt(handle, ZLCURLOPT_USERAGENT, "ZyenLang-request/1");
    zl_curl.easy_setopt(handle, ZLCURLOPT_FOLLOWLOCATION, 1L);
    zl_curl.easy_setopt(handle, ZLCURLOPT_NOSIGNAL, 1L);
    zl_curl.easy_setopt(handle, ZLCURLOPT_ACCEPT_ENCODING, "");
    zl_curl.easy_setopt(handle, ZLCURLOPT_TIMEOUT_MS, (long)timeout_ms);
    if (strcmp(method, "GET") != 0) {
        zl_curl.easy_setopt(handle, ZLCURLOPT_CUSTOMREQUEST, method);
        zl_curl.easy_setopt(handle, ZLCURLOPT_POSTFIELDS, payload);
        zl_curl.easy_setopt(handle, ZLCURLOPT_POSTFIELDSIZE, (long)strlen(payload));
    }
    if (content_type && content_type[0]) {
        snprintf(content_header, sizeof(content_header), "Content-Type: %s", content_type);
        headers = zl_curl.slist_append(headers, content_header);
        if (headers) zl_curl.easy_setopt(handle, ZLCURLOPT_HTTPHEADER, headers);
    }
    code = zl_curl.easy_perform(handle);
    if (code == ZLCURLE_OK) {
        zl_curl.easy_getinfo(handle, ZLCURLINFO_RESPONSE_CODE, &status);
        zl_request_code = (int)status;
    } else if (zl_request_message[0] == '\0') {
        zl_request_set_error("%s", curl_error[0] ? curl_error : zl_curl.easy_strerror(code));
    }
    if (headers) zl_curl.slist_free_all(headers);
    zl_curl.easy_cleanup(handle);
    return code == ZLCURLE_OK ? 0 : -1;
}

#endif

int zl_request_perform(const char* method, const char* url, const char* body, const char* content_type, int timeout_ms) {
    zl_request_reset();
    if (!zl_request_data) {
        zl_request_set_error("out of memory before request");
        return -1;
    }
    if (!method || !method[0] || !url || !url[0]) {
        zl_request_set_error("method and URL are required");
        return -1;
    }
#ifdef _WIN32
    return zl_request_windows(method, url, body, content_type, timeout_ms);
#else
    return zl_request_curl(method, url, body, content_type, timeout_ms);
#endif
}

int zl_request_status(void) {
    return zl_request_code;
}

const char* zl_request_body(void) {
    return zl_request_data ? zl_request_data : "";
}

const char* zl_request_error(void) {
    return zl_request_message;
}

int zl_request_save(const char* path) {
    FILE* file;
    size_t written;
    if (!path || !path[0] || !zl_request_data) {
        return -1;
    }
    file = fopen(path, "wb");
    if (!file) {
        return -1;
    }
    written = fwrite(zl_request_data, 1, zl_request_size, file);
    if (fclose(file) != 0 || written != zl_request_size) {
        return -1;
    }
    return 0;
}
