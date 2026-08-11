#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif

#include "fs_native.h"

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#else
#include <dirent.h>
#include <sys/stat.h>
#include <sys/types.h>
#endif

#define ZY2_FS_ERROR_SIZE 1024u
#define ZY2_FS_MAX_TEXT (64u * 1024u * 1024u)
#define ZY2_FS_MAX_TREE (16u * 1024u * 1024u)
#define ZY2_FS_MAX_DEPTH 256u

typedef struct {
    char* data;
    size_t length;
    size_t capacity;
} zy2_fs_buffer;

typedef struct {
    char* name;
    bool is_directory;
    bool recurse;
} zy2_fs_entry;

static _Thread_local char zy2_fs_message[ZY2_FS_ERROR_SIZE];
static _Thread_local zy2_fs_buffer zy2_fs_read_buffer;
static _Thread_local zy2_fs_buffer zy2_fs_tree_buffer;

static void zy2_fs_clear_error(void) {
    zy2_fs_message[0] = '\0';
}

static void zy2_fs_set_error(const char* action, const char* path) {
    const char* detail = strerror(errno);
    snprintf(
        zy2_fs_message,
        sizeof(zy2_fs_message),
        "%s `%s` failed: %s",
        action,
        path ? path : "",
        detail ? detail : "unknown error"
    );
}

static void zy2_fs_set_message(const char* message) {
    snprintf(zy2_fs_message, sizeof(zy2_fs_message), "%s", message ? message : "filesystem error");
}

static bool zy2_fs_buffer_reserve(zy2_fs_buffer* buffer, size_t needed, size_t maximum) {
    char* next;
    size_t capacity;
    if (needed > maximum + 1u) {
        zy2_fs_set_message("filesystem result exceeds its safety limit");
        return false;
    }
    if (needed <= buffer->capacity) return true;
    capacity = buffer->capacity ? buffer->capacity : 256u;
    while (capacity < needed) {
        if (capacity > (maximum + 1u) / 2u) {
            capacity = maximum + 1u;
            break;
        }
        capacity *= 2u;
    }
    next = (char*)realloc(buffer->data, capacity);
    if (!next) {
        zy2_fs_set_message("filesystem operation ran out of memory");
        return false;
    }
    buffer->data = next;
    buffer->capacity = capacity;
    return true;
}

static bool zy2_fs_buffer_reset(zy2_fs_buffer* buffer, size_t maximum) {
    if (!zy2_fs_buffer_reserve(buffer, 1u, maximum)) return false;
    buffer->length = 0u;
    buffer->data[0] = '\0';
    return true;
}

static bool zy2_fs_buffer_append(zy2_fs_buffer* buffer, const char* value, size_t maximum) {
    size_t length = value ? strlen(value) : 0u;
    size_t needed = buffer->length + length + 1u;
    if (!zy2_fs_buffer_reserve(buffer, needed, maximum)) return false;
    if (length > 0u) memcpy(buffer->data + buffer->length, value, length);
    buffer->length += length;
    buffer->data[buffer->length] = '\0';
    return true;
}

#ifndef _WIN32
static char* zy2_fs_duplicate(const char* value) {
    size_t length;
    char* result;
    if (!value) return NULL;
    length = strlen(value);
    result = (char*)malloc(length + 1u);
    if (!result) {
        zy2_fs_set_message("filesystem operation ran out of memory");
        return NULL;
    }
    memcpy(result, value, length + 1u);
    return result;
}
#endif

static bool zy2_fs_is_separator(char value) {
    return value == '/' || value == '\\';
}

static bool zy2_fs_valid_utf8(const char* value, size_t length) {
    const unsigned char* cursor = (const unsigned char*)value;
    const unsigned char* end = cursor + length;
    while (cursor < end) {
        unsigned char first = *cursor++;
        if (first == 0u) return false;
        if (first < 0x80u) continue;
        if (first >= 0xC2u && first <= 0xDFu) {
            if (cursor >= end || (*cursor & 0xC0u) != 0x80u) return false;
            cursor += 1;
            continue;
        }
        if (first >= 0xE0u && first <= 0xEFu) {
            if ((size_t)(end - cursor) < 2u
                    || (cursor[0] & 0xC0u) != 0x80u
                    || (cursor[1] & 0xC0u) != 0x80u
                    || (first == 0xE0u && cursor[0] < 0xA0u)
                    || (first == 0xEDu && cursor[0] >= 0xA0u)) return false;
            cursor += 2;
            continue;
        }
        if (first >= 0xF0u && first <= 0xF4u) {
            if ((size_t)(end - cursor) < 3u
                    || (cursor[0] & 0xC0u) != 0x80u
                    || (cursor[1] & 0xC0u) != 0x80u
                    || (cursor[2] & 0xC0u) != 0x80u
                    || (first == 0xF0u && cursor[0] < 0x90u)
                    || (first == 0xF4u && cursor[0] >= 0x90u)) return false;
            cursor += 3;
            continue;
        }
        return false;
    }
    return true;
}

static char* zy2_fs_join(const char* left, const char* right) {
    size_t left_length = strlen(left);
    size_t right_length = strlen(right);
    bool separator = left_length > 0u && !zy2_fs_is_separator(left[left_length - 1u]);
    char* result = (char*)malloc(left_length + right_length + (separator ? 2u : 1u));
    if (!result) {
        zy2_fs_set_message("filesystem operation ran out of memory");
        return NULL;
    }
    memcpy(result, left, left_length);
    if (separator) result[left_length++] = '/';
    memcpy(result + left_length, right, right_length + 1u);
    return result;
}

#ifdef _WIN32
static wchar_t* zy2_fs_utf8_to_wide(const char* value) {
    int length;
    wchar_t* result;
    if (!value) return NULL;
    length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, NULL, 0);
    if (length <= 0) {
        zy2_fs_set_message("path is not valid UTF-8");
        return NULL;
    }
    result = (wchar_t*)malloc((size_t)length * sizeof(wchar_t));
    if (!result) {
        zy2_fs_set_message("filesystem operation ran out of memory");
        return NULL;
    }
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, result, length) <= 0) {
        free(result);
        zy2_fs_set_message("path is not valid UTF-8");
        return NULL;
    }
    return result;
}

static char* zy2_fs_wide_to_utf8(const wchar_t* value) {
    int length;
    char* result;
    length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, NULL, 0, NULL, NULL);
    if (length <= 0) {
        zy2_fs_set_message("filesystem returned an invalid Unicode name");
        return NULL;
    }
    result = (char*)malloc((size_t)length);
    if (!result) {
        zy2_fs_set_message("filesystem operation ran out of memory");
        return NULL;
    }
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, result, length, NULL, NULL) <= 0) {
        free(result);
        zy2_fs_set_message("filesystem returned an invalid Unicode name");
        return NULL;
    }
    return result;
}

static FILE* zy2_fs_open_file(const char* path, const wchar_t* mode) {
    wchar_t* wide_path = zy2_fs_utf8_to_wide(path);
    FILE* file;
    if (!wide_path) return NULL;
    file = _wfopen(wide_path, mode);
    free(wide_path);
    return file;
}
#else
static FILE* zy2_fs_open_file(const char* path, const char* mode) {
    return fopen(path, mode);
}
#endif

static FILE* zy2_fs_open_read(const char* path) {
#ifdef _WIN32
    return zy2_fs_open_file(path, L"rb");
#else
    return zy2_fs_open_file(path, "rb");
#endif
}

static FILE* zy2_fs_open_write(const char* path, bool append) {
#ifdef _WIN32
    return zy2_fs_open_file(path, append ? L"ab" : L"wb");
#else
    return zy2_fs_open_file(path, append ? "ab" : "wb");
#endif
}

const char* zy2_fs_read_text(const char* path) {
    FILE* file;
    long length;
    size_t read_length;
    zy2_fs_clear_error();
    if (!path || !path[0]) {
        zy2_fs_set_message("read_text requires a non-empty path");
        return "";
    }
    file = zy2_fs_open_read(path);
    if (!file) {
        if (!zy2_fs_message[0]) zy2_fs_set_error("read", path);
        return "";
    }
    if (fseek(file, 0, SEEK_END) != 0 || (length = ftell(file)) < 0 || fseek(file, 0, SEEK_SET) != 0) {
        zy2_fs_set_error("measure", path);
        fclose(file);
        return "";
    }
    if ((unsigned long)length > (unsigned long)ZY2_FS_MAX_TEXT) {
        zy2_fs_set_message("file exceeds the 64 MiB read_text safety limit");
        fclose(file);
        return "";
    }
    if (!zy2_fs_buffer_reserve(&zy2_fs_read_buffer, (size_t)length + 1u, ZY2_FS_MAX_TEXT)) {
        fclose(file);
        return "";
    }
    read_length = fread(zy2_fs_read_buffer.data, 1u, (size_t)length, file);
    if (read_length != (size_t)length) {
        zy2_fs_set_error("read", path);
        fclose(file);
        return "";
    }
    if (!zy2_fs_valid_utf8(zy2_fs_read_buffer.data, read_length)) {
        zy2_fs_set_message("read_text requires UTF-8 text without embedded NUL bytes");
        fclose(file);
        return "";
    }
    if (fclose(file) != 0) {
        zy2_fs_set_error("close", path);
        return "";
    }
    zy2_fs_read_buffer.length = read_length;
    zy2_fs_read_buffer.data[read_length] = '\0';
    return zy2_fs_read_buffer.data;
}

static int32_t zy2_fs_write_mode(const char* path, const char* value, bool append) {
    FILE* file;
    size_t length;
    zy2_fs_clear_error();
    if (!path || !path[0]) {
        zy2_fs_set_message("write requires a non-empty path");
        return -1;
    }
    value = value ? value : "";
    file = zy2_fs_open_write(path, append);
    if (!file) {
        if (!zy2_fs_message[0]) zy2_fs_set_error(append ? "append" : "write", path);
        return -1;
    }
    length = strlen(value);
    if (length > 0u && fwrite(value, 1u, length, file) != length) {
        zy2_fs_set_error(append ? "append" : "write", path);
        fclose(file);
        return -1;
    }
    if (fclose(file) != 0) {
        zy2_fs_set_error("close", path);
        return -1;
    }
    return 0;
}

int32_t zy2_fs_write_text(const char* path, const char* value) {
    return zy2_fs_write_mode(path, value, false);
}

int32_t zy2_fs_append_text(const char* path, const char* value) {
    return zy2_fs_write_mode(path, value, true);
}

static void zy2_fs_free_entries(zy2_fs_entry* entries, size_t count) {
    size_t index;
    if (!entries) return;
    for (index = 0u; index < count; index += 1u) free(entries[index].name);
    free(entries);
}

static bool zy2_fs_push_entry(zy2_fs_entry** entries, size_t* count, size_t* capacity, zy2_fs_entry entry) {
    zy2_fs_entry* next;
    size_t next_capacity;
    if (*count < *capacity) {
        (*entries)[(*count)++] = entry;
        return true;
    }
    next_capacity = *capacity ? *capacity * 2u : 16u;
    next = (zy2_fs_entry*)realloc(*entries, next_capacity * sizeof(zy2_fs_entry));
    if (!next) {
        zy2_fs_set_message("filesystem operation ran out of memory");
        return false;
    }
    *entries = next;
    *capacity = next_capacity;
    (*entries)[(*count)++] = entry;
    return true;
}

static int zy2_fs_compare_entries(const void* left, const void* right) {
    const zy2_fs_entry* first = (const zy2_fs_entry*)left;
    const zy2_fs_entry* second = (const zy2_fs_entry*)right;
    return strcmp(first->name, second->name);
}

#ifdef _WIN32
static bool zy2_fs_root_kind(const char* path, bool* is_directory, bool* recurse) {
    wchar_t* wide_path = zy2_fs_utf8_to_wide(path);
    DWORD attributes;
    if (!wide_path) return false;
    attributes = GetFileAttributesW(wide_path);
    free(wide_path);
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        errno = ENOENT;
        zy2_fs_set_error("inspect", path);
        return false;
    }
    *is_directory = (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    *recurse = *is_directory && (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
    return true;
}

static bool zy2_fs_read_entries(const char* path, zy2_fs_entry** result, size_t* result_count) {
    char* pattern = zy2_fs_join(path, "*");
    wchar_t* wide_pattern;
    WIN32_FIND_DATAW data;
    HANDLE search;
    zy2_fs_entry* entries = NULL;
    size_t count = 0u;
    size_t capacity = 0u;
    if (!pattern) return false;
    wide_pattern = zy2_fs_utf8_to_wide(pattern);
    free(pattern);
    if (!wide_pattern) return false;
    search = FindFirstFileW(wide_pattern, &data);
    free(wide_pattern);
    if (search == INVALID_HANDLE_VALUE) {
        errno = ENOENT;
        zy2_fs_set_error("list directory", path);
        return false;
    }
    do {
        zy2_fs_entry entry;
        if (wcscmp(data.cFileName, L".") == 0 || wcscmp(data.cFileName, L"..") == 0) continue;
        entry.name = zy2_fs_wide_to_utf8(data.cFileName);
        if (!entry.name) {
            FindClose(search);
            zy2_fs_free_entries(entries, count);
            return false;
        }
        entry.is_directory = (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
        entry.recurse = entry.is_directory && (data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
        if (!zy2_fs_push_entry(&entries, &count, &capacity, entry)) {
            free(entry.name);
            FindClose(search);
            zy2_fs_free_entries(entries, count);
            return false;
        }
    } while (FindNextFileW(search, &data));
    if (GetLastError() != ERROR_NO_MORE_FILES) {
        errno = EIO;
        zy2_fs_set_error("list directory", path);
        FindClose(search);
        zy2_fs_free_entries(entries, count);
        return false;
    }
    FindClose(search);
    qsort(entries, count, sizeof(zy2_fs_entry), zy2_fs_compare_entries);
    *result = entries;
    *result_count = count;
    return true;
}
#else
static bool zy2_fs_root_kind(const char* path, bool* is_directory, bool* recurse) {
    struct stat info;
    if (lstat(path, &info) != 0) {
        zy2_fs_set_error("inspect", path);
        return false;
    }
    *is_directory = S_ISDIR(info.st_mode);
    *recurse = *is_directory;
    return true;
}

static bool zy2_fs_read_entries(const char* path, zy2_fs_entry** result, size_t* result_count) {
    DIR* directory = opendir(path);
    struct dirent* item;
    zy2_fs_entry* entries = NULL;
    size_t count = 0u;
    size_t capacity = 0u;
    if (!directory) {
        zy2_fs_set_error("list directory", path);
        return false;
    }
    errno = 0;
    while ((item = readdir(directory)) != NULL) {
        zy2_fs_entry entry;
        char* child;
        struct stat info;
        if (strcmp(item->d_name, ".") == 0 || strcmp(item->d_name, "..") == 0) continue;
        entry.name = zy2_fs_duplicate(item->d_name);
        if (!entry.name) {
            closedir(directory);
            zy2_fs_free_entries(entries, count);
            return false;
        }
        if (!zy2_fs_valid_utf8(entry.name, strlen(entry.name))) {
            free(entry.name);
            closedir(directory);
            zy2_fs_free_entries(entries, count);
            zy2_fs_set_message("directory contains a filename that is not valid UTF-8");
            return false;
        }
        child = zy2_fs_join(path, item->d_name);
        if (!child || lstat(child, &info) != 0) {
            if (child) zy2_fs_set_error("inspect", child);
            free(child);
            free(entry.name);
            closedir(directory);
            zy2_fs_free_entries(entries, count);
            return false;
        }
        free(child);
        entry.is_directory = S_ISDIR(info.st_mode);
        entry.recurse = entry.is_directory;
        if (!zy2_fs_push_entry(&entries, &count, &capacity, entry)) {
            free(entry.name);
            closedir(directory);
            zy2_fs_free_entries(entries, count);
            return false;
        }
        errno = 0;
    }
    if (errno != 0) {
        zy2_fs_set_error("list directory", path);
        closedir(directory);
        zy2_fs_free_entries(entries, count);
        return false;
    }
    closedir(directory);
    qsort(entries, count, sizeof(zy2_fs_entry), zy2_fs_compare_entries);
    *result = entries;
    *result_count = count;
    return true;
}
#endif

static bool zy2_fs_tree_walk(const char* path, const char* prefix, size_t depth) {
    zy2_fs_entry* entries = NULL;
    size_t count = 0u;
    size_t index;
    if (depth > ZY2_FS_MAX_DEPTH) {
        zy2_fs_set_message("directory tree exceeds the 256-level recursion limit");
        return false;
    }
    if (!zy2_fs_read_entries(path, &entries, &count)) return false;
    for (index = 0u; index < count; index += 1u) {
        bool last = index + 1u == count;
        char* child;
        char* next_prefix;
        size_t prefix_length;
        if (!zy2_fs_buffer_append(&zy2_fs_tree_buffer, "\n", ZY2_FS_MAX_TREE)
                || !zy2_fs_buffer_append(&zy2_fs_tree_buffer, prefix, ZY2_FS_MAX_TREE)
                || !zy2_fs_buffer_append(&zy2_fs_tree_buffer, last ? "`-- " : "|-- ", ZY2_FS_MAX_TREE)
                || !zy2_fs_buffer_append(&zy2_fs_tree_buffer, entries[index].name, ZY2_FS_MAX_TREE)
                || (entries[index].is_directory
                    && !zy2_fs_buffer_append(&zy2_fs_tree_buffer, "/", ZY2_FS_MAX_TREE))) {
            zy2_fs_free_entries(entries, count);
            return false;
        }
        if (!entries[index].recurse) continue;
        child = zy2_fs_join(path, entries[index].name);
        if (!child) {
            zy2_fs_free_entries(entries, count);
            return false;
        }
        prefix_length = strlen(prefix);
        next_prefix = (char*)malloc(prefix_length + 5u);
        if (!next_prefix) {
            free(child);
            zy2_fs_free_entries(entries, count);
            zy2_fs_set_message("filesystem operation ran out of memory");
            return false;
        }
        memcpy(next_prefix, prefix, prefix_length);
        memcpy(next_prefix + prefix_length, last ? "    " : "|   ", 5u);
        if (!zy2_fs_tree_walk(child, next_prefix, depth + 1u)) {
            free(next_prefix);
            free(child);
            zy2_fs_free_entries(entries, count);
            return false;
        }
        free(next_prefix);
        free(child);
    }
    zy2_fs_free_entries(entries, count);
    return true;
}

const char* zy2_fs_tree(const char* path) {
    bool is_directory;
    bool recurse;
    zy2_fs_clear_error();
    if (!path || !path[0]) {
        zy2_fs_set_message("tree requires a non-empty path");
        return "";
    }
    if (!zy2_fs_buffer_reset(&zy2_fs_tree_buffer, ZY2_FS_MAX_TREE)) return "";
    if (!zy2_fs_root_kind(path, &is_directory, &recurse)) return "";
    if (!zy2_fs_buffer_append(&zy2_fs_tree_buffer, path, ZY2_FS_MAX_TREE)) return "";
    if (is_directory && !zy2_fs_is_separator(path[strlen(path) - 1u])
            && !zy2_fs_buffer_append(&zy2_fs_tree_buffer, "/", ZY2_FS_MAX_TREE)) return "";
    if (recurse && !zy2_fs_tree_walk(path, "", 0u)) {
        zy2_fs_tree_buffer.length = 0u;
        zy2_fs_tree_buffer.data[0] = '\0';
        return "";
    }
    return zy2_fs_tree_buffer.data;
}

const char* zy2_fs_error(void) {
    return zy2_fs_message;
}
