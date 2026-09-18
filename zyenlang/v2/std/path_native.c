#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include "zyenlang_c_abi.h"

#ifdef _WIN32
#include <ctype.h>
#define zy2_stat _stat64
#define zy2_stat_info struct _stat64
#else
#include <ctype.h>
#define zy2_stat stat
#define zy2_stat_info struct stat
#endif

#define ZY2_PATH_BUFFER_COUNT 16u
#define ZY2_PATH_BUFFER_SIZE 4096u

static _Thread_local char zy2_path_buffers[ZY2_PATH_BUFFER_COUNT][ZY2_PATH_BUFFER_SIZE];
static _Thread_local unsigned int zy2_path_buffer_index;

static char* zy2_path_next_buffer(void) {
    char* result = zy2_path_buffers[zy2_path_buffer_index++ % ZY2_PATH_BUFFER_COUNT];
    result[0] = '\0';
    return result;
}

static bool zy2_path_is_separator(char value) {
    return value == '/' || value == '\\';
}

static const char* zy2_path_copy_range(const char* value, size_t start, size_t length) {
    char* result = zy2_path_next_buffer();
    if (!value || length >= ZY2_PATH_BUFFER_SIZE) return result;
    memcpy(result, value + start, length);
    result[length] = '\0';
    return result;
}

static const char* zy2_path_separator_raw(void) {
    return "/";
}

static bool zy2_path_is_absolute_raw(const char* value) {
    if (!value || !value[0]) return false;
    if (zy2_path_is_separator(value[0])) return true;
    return isalpha((unsigned char)value[0]) && value[1] == ':' && zy2_path_is_separator(value[2]);
}

static const char* zy2_path_normalize_raw(const char* value) {
    char* result = zy2_path_next_buffer();
    size_t input = 0;
    size_t output = 0;
    bool previous_separator = false;
    bool unc = value && zy2_path_is_separator(value[0]) && zy2_path_is_separator(value[1]);

    if (!value) return result;
    if (unc) {
        result[output++] = '/';
        result[output++] = '/';
        input = 2;
        while (zy2_path_is_separator(value[input])) input += 1;
        previous_separator = true;
    }

    while (value[input]) {
        char current = value[input++];
        if (zy2_path_is_separator(current)) {
            if (previous_separator) continue;
            current = '/';
            previous_separator = true;
        } else {
            previous_separator = false;
        }
        if (output + 1 >= ZY2_PATH_BUFFER_SIZE) {
            result[0] = '\0';
            return result;
        }
        result[output++] = current;
    }

    if (output > 1 && result[output - 1] == '/' && !(output == 3 && result[1] == ':')) output -= 1;
    result[output] = '\0';
    return result;
}

static const char* zy2_path_join_raw(const char* left, const char* right) {
    char* joined;
    size_t left_length;
    size_t right_length;
    bool needs_separator;

    if (!left || !left[0]) return zy2_path_normalize_raw(right);
    if (!right || !right[0]) return zy2_path_normalize_raw(left);
    if (zy2_path_is_absolute_raw(right)) return zy2_path_normalize_raw(right);

    joined = zy2_path_next_buffer();
    left_length = strlen(left);
    right_length = strlen(right);
    needs_separator = !zy2_path_is_separator(left[left_length - 1]);
    if (left_length + right_length + (needs_separator ? 1u : 0u) >= ZY2_PATH_BUFFER_SIZE) return joined;
    memcpy(joined, left, left_length);
    if (needs_separator) joined[left_length++] = '/';
    memcpy(joined + left_length, right, right_length + 1);
    return zy2_path_normalize_raw(joined);
}

static const char* zy2_path_basename_raw(const char* value) {
    size_t end;
    size_t start;
    if (!value || !value[0]) return zy2_path_copy_range("", 0, 0);
    end = strlen(value);
    while (end > 1 && zy2_path_is_separator(value[end - 1])) end -= 1;
    start = end;
    while (start > 0 && !zy2_path_is_separator(value[start - 1])) start -= 1;
    return zy2_path_copy_range(value, start, end - start);
}

static const char* zy2_path_dirname_raw(const char* value) {
    size_t end;
    size_t start;
    size_t separator;
    if (!value || !value[0]) return zy2_path_copy_range(".", 0, 1);
    end = strlen(value);
    while (end > 1 && zy2_path_is_separator(value[end - 1])) end -= 1;
    start = end;
    while (start > 0 && !zy2_path_is_separator(value[start - 1])) start -= 1;
    if (start == 0) return zy2_path_copy_range(".", 0, 1);
    separator = start - 1;
    while (separator > 0 && zy2_path_is_separator(value[separator - 1])) separator -= 1;
    if (separator == 0) return zy2_path_copy_range("/", 0, 1);
    if (separator == 2 && value[1] == ':') return zy2_path_copy_range(value, 0, 3);
    return zy2_path_copy_range(value, 0, separator);
}

static const char* zy2_path_extension_raw(const char* value) {
    size_t end;
    size_t base;
    size_t cursor;
    if (!value) return zy2_path_copy_range("", 0, 0);
    end = strlen(value);
    while (end > 1 && zy2_path_is_separator(value[end - 1])) end -= 1;
    base = end;
    while (base > 0 && !zy2_path_is_separator(value[base - 1])) base -= 1;
    cursor = end;
    while (cursor > base && value[cursor - 1] != '.') cursor -= 1;
    if (cursor <= base + 1 || cursor == end) return zy2_path_copy_range("", 0, 0);
    return zy2_path_copy_range(value, cursor - 1, end - cursor + 1);
}

static const char* zy2_path_stem_raw(const char* value) {
    const char* base = zy2_path_basename_raw(value);
    size_t length = strlen(base);
    size_t cursor = length;
    while (cursor > 0 && base[cursor - 1] != '.') cursor -= 1;
    if (cursor <= 1 || cursor == length) return zy2_path_copy_range(base, 0, length);
    return zy2_path_copy_range(base, 0, cursor - 1);
}

static const char* zy2_path_with_extension_raw(const char* value, const char* next_extension) {
    char* result = zy2_path_next_buffer();
    size_t length;
    size_t base;
    size_t cursor;
    size_t stem_length;
    size_t extension_length;
    bool add_dot;

    if (!value) return result;
    length = strlen(value);
    base = length;
    while (base > 0 && !zy2_path_is_separator(value[base - 1])) base -= 1;
    cursor = length;
    while (cursor > base && value[cursor - 1] != '.') cursor -= 1;
    stem_length = (cursor > base + 1 && cursor < length) ? cursor - 1 : length;
    next_extension = next_extension ? next_extension : "";
    extension_length = strlen(next_extension);
    add_dot = extension_length > 0 && next_extension[0] != '.';
    if (stem_length + extension_length + (add_dot ? 1u : 0u) >= ZY2_PATH_BUFFER_SIZE) return result;
    memcpy(result, value, stem_length);
    if (add_dot) result[stem_length++] = '.';
    memcpy(result + stem_length, next_extension, extension_length + 1);
    return result;
}

static bool zy2_path_stat_mode(const char* value, bool expect_directory) {
    zy2_stat_info info;
    if (!value || zy2_stat(value, &info) != 0) return false;
#ifdef _WIN32
    return expect_directory
        ? (info.st_mode & _S_IFMT) == _S_IFDIR
        : (info.st_mode & _S_IFMT) == _S_IFREG;
#else
    return expect_directory ? S_ISDIR(info.st_mode) : S_ISREG(info.st_mode);
#endif
}

static bool zy2_path_exists_raw(const char* value) {
    zy2_stat_info info;
    return value && zy2_stat(value, &info) == 0;
}

static bool zy2_path_is_file_raw(const char* value) {
    return zy2_path_stat_mode(value, false);
}

static bool zy2_path_is_dir_raw(const char* value) {
    return zy2_path_stat_mode(value, true);
}

ZL_String zy2_path_separator(void) {
    return zl_string_borrow(zy2_path_separator_raw());
}

bool zy2_path_is_absolute(ZL_String value) {
    return zy2_path_is_absolute_raw(zl_string_data(value));
}

ZL_String zy2_path_normalize(ZL_String value) {
    return zl_string_copy(zy2_path_normalize_raw(zl_string_data(value)));
}

ZL_String zy2_path_join(ZL_String left, ZL_String right) {
    return zl_string_copy(zy2_path_join_raw(zl_string_data(left), zl_string_data(right)));
}

ZL_String zy2_path_basename(ZL_String value) {
    return zl_string_copy(zy2_path_basename_raw(zl_string_data(value)));
}

ZL_String zy2_path_dirname(ZL_String value) {
    return zl_string_copy(zy2_path_dirname_raw(zl_string_data(value)));
}

ZL_String zy2_path_extension(ZL_String value) {
    return zl_string_copy(zy2_path_extension_raw(zl_string_data(value)));
}

ZL_String zy2_path_stem(ZL_String value) {
    return zl_string_copy(zy2_path_stem_raw(zl_string_data(value)));
}

ZL_String zy2_path_with_extension(ZL_String value, ZL_String next_extension) {
    return zl_string_copy(
        zy2_path_with_extension_raw(zl_string_data(value), zl_string_data(next_extension))
    );
}

bool zy2_path_exists(ZL_String value) {
    return zy2_path_exists_raw(zl_string_data(value));
}

bool zy2_path_is_file(ZL_String value) {
    return zy2_path_is_file_raw(zl_string_data(value));
}

bool zy2_path_is_dir(ZL_String value) {
    return zy2_path_is_dir_raw(zl_string_data(value));
}
