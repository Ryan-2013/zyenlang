#ifndef ZYENLANG_V2_RUNTIME_H
#define ZYENLANG_V2_RUNTIME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef ZYENLANG_API
#if defined(_WIN32) && defined(ZYENLANG_BUILD_SHARED)
#define ZYENLANG_API __declspec(dllexport)
#elif defined(_WIN32) && defined(ZYENLANG_USE_SHARED)
#define ZYENLANG_API __declspec(dllimport)
#elif defined(__GNUC__) || defined(__clang__)
#define ZYENLANG_API __attribute__((visibility("default")))
#else
#define ZYENLANG_API
#endif
#endif

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <io.h>
#else
#include <unistd.h>
#endif

#if defined(__GNUC__) || defined(__clang__)
#define ZY2_MAYBE_UNUSED __attribute__((unused))
#else
#define ZY2_MAYBE_UNUSED
#endif

#define ZY2_FORMAT_BUFFER_COUNT 16u
#define ZY2_FORMAT_BUFFER_SIZE 4096u

static _Thread_local char zy2_format_buffers[ZY2_FORMAT_BUFFER_COUNT][ZY2_FORMAT_BUFFER_SIZE];
static _Thread_local unsigned int zy2_format_buffer_index;

static inline bool zy2_stream_color_enabled(FILE* stream) {
    const char* setting;
    int descriptor = stream == stderr ? 2 : 1;
    if (getenv("NO_COLOR") != NULL) return false;
    setting = getenv("ZYEN_COLOR");
    if (setting && strcmp(setting, "always") == 0) return true;
    if (setting && strcmp(setting, "never") == 0) return false;
#ifdef _WIN32
    if (!_isatty(descriptor)) return false;
    {
        HANDLE handle = GetStdHandle(stream == stderr ? STD_ERROR_HANDLE : STD_OUTPUT_HANDLE);
        DWORD mode = 0;
        if (handle != INVALID_HANDLE_VALUE && GetConsoleMode(handle, &mode)) {
            SetConsoleMode(handle, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING);
        }
    }
    return true;
#else
    return isatty(descriptor) != 0;
#endif
}

static inline bool zy2_stderr_color_enabled(void) {
    return zy2_stream_color_enabled(stderr);
}

static inline void zy2_error_begin(void) {
    if (zy2_stderr_color_enabled()) fputs("\x1b[31m", stderr);
}

static inline void zy2_error_end(void) {
    if (zy2_stderr_color_enabled()) fputs("\x1b[0m", stderr);
}

static inline char* zy2_format_begin(void) {
    char* buffer = zy2_format_buffers[zy2_format_buffer_index++ % ZY2_FORMAT_BUFFER_COUNT];
    buffer[0] = '\0';
    return buffer;
}

static inline void zy2_format_append(char* buffer, const char* value) {
    size_t used;
    size_t available;
    size_t length;
    if (!buffer || !value) return;
    used = strlen(buffer);
    if (used >= ZY2_FORMAT_BUFFER_SIZE - 1u) return;
    available = ZY2_FORMAT_BUFFER_SIZE - used - 1u;
    length = strlen(value);
    if (length > available) length = available;
    memcpy(buffer + used, value, length);
    buffer[used + length] = '\0';
}

static inline void zy2_format_append_i64(char* buffer, int64_t value) {
    char text[64];
    snprintf(text, sizeof(text), "%lld", (long long)value);
    zy2_format_append(buffer, text);
}

static inline void zy2_format_append_u64(char* buffer, uint64_t value) {
    char text[64];
    snprintf(text, sizeof(text), "%llu", (unsigned long long)value);
    zy2_format_append(buffer, text);
}

static inline void zy2_format_append_f64(char* buffer, double value, int precision) {
    char text[64];
    snprintf(text, sizeof(text), "%.*g", precision, value);
    zy2_format_append(buffer, text);
}

static inline void zy2_format_append_bool(char* buffer, bool value) {
    zy2_format_append(buffer, value ? "true" : "false");
}

static inline size_t zy2_utf8_character_size(const unsigned char* text, size_t remaining) {
    unsigned char first;
    if (!text || remaining == 0u) return 0u;
    first = text[0];
    if (first <= 0x7fu) return 1u;
    if (first >= 0xc2u && first <= 0xdfu) {
        return remaining >= 2u && (text[1] & 0xc0u) == 0x80u ? 2u : 0u;
    }
    if (first >= 0xe0u && first <= 0xefu) {
        if (remaining < 3u || (text[1] & 0xc0u) != 0x80u || (text[2] & 0xc0u) != 0x80u) return 0u;
        if (first == 0xe0u && text[1] < 0xa0u) return 0u;
        if (first == 0xedu && text[1] >= 0xa0u) return 0u;
        return 3u;
    }
    if (first >= 0xf0u && first <= 0xf4u) {
        if (remaining < 4u || (text[1] & 0xc0u) != 0x80u || (text[2] & 0xc0u) != 0x80u || (text[3] & 0xc0u) != 0x80u) return 0u;
        if (first == 0xf0u && text[1] < 0x90u) return 0u;
        if (first == 0xf4u && text[1] >= 0x90u) return 0u;
        return 4u;
    }
    return 0u;
}

typedef void (*zy2_ArcDrop)(void* payload);

typedef struct zy2_ArcControl {
    atomic_size_t refs;
    void* payload;
    zy2_ArcDrop drop;
} zy2_ArcControl;

#ifdef ZY2_RUNTIME_IMPLEMENTATION
atomic_size_t zy2_arc_live_controls = 0;
#else
extern atomic_size_t zy2_arc_live_controls;
#endif

static inline void zy2_arc_panic(const char* message) {
    zy2_error_begin();
    fprintf(stderr, "<runtime>:0:0: %s\n", message);
    zy2_error_end();
    exit(1);
}

static inline void zy2_panic_at(
    const char* source_file,
    uint32_t line,
    uint32_t column,
    const char* message
) {
    zy2_error_begin();
    fprintf(
        stderr,
        "%s:%u:%u: %s\n",
        source_file ? source_file : "<runtime>",
        (unsigned int)line,
        (unsigned int)column,
        message
    );
    zy2_error_end();
    exit(1);
}

static inline zy2_ArcControl* zy2_arc_new(void* payload, zy2_ArcDrop drop) {
    zy2_ArcControl* owner = (zy2_ArcControl*)malloc(sizeof(zy2_ArcControl));
    if (!owner) {
        if (drop && payload) drop(payload);
        zy2_arc_panic("cannot allocate ARC control block");
    }
    atomic_init(&owner->refs, 1u);
    owner->payload = payload;
    owner->drop = drop;
    atomic_fetch_add_explicit(&zy2_arc_live_controls, 1u, memory_order_relaxed);
    return owner;
}

static inline void zy2_arc_retain(zy2_ArcControl* owner) {
    if (owner) atomic_fetch_add_explicit(&owner->refs, 1u, memory_order_relaxed);
}

static inline void zy2_arc_release(zy2_ArcControl* owner) {
    if (!owner) return;
    if (atomic_fetch_sub_explicit(&owner->refs, 1u, memory_order_acq_rel) == 1u) {
        if (owner->drop && owner->payload) owner->drop(owner->payload);
        atomic_fetch_sub_explicit(&zy2_arc_live_controls, 1u, memory_order_relaxed);
        free(owner);
    }
}

static inline size_t zy2_arc_strong_count(zy2_ArcControl* owner) {
    return owner ? atomic_load_explicit(&owner->refs, memory_order_relaxed) : 0u;
}

typedef zy2_ArcControl ZL_ArcControl;
typedef void (*ZL_GenericCall)(void);

typedef struct ZL_Function {
    ZL_GenericCall call;
    void* env;
    ZL_ArcControl* owner;
    const char* signature;
} ZL_Function;

static inline ZL_Function zl_fn_retain(ZL_Function value) {
    zy2_arc_retain(value.owner);
    return value;
}

static inline void zl_fn_release(ZL_Function value) {
    zy2_arc_release(value.owner);
}

static inline void zl_fn_assign(ZL_Function* target, ZL_Function value) {
    ZL_Function retained;
    if (!target) return;
    retained = zl_fn_retain(value);
    zl_fn_release(*target);
    *target = retained;
}

static inline void zl_fn_clear(ZL_Function* target) {
    if (!target) return;
    zl_fn_release(*target);
    *target = (ZL_Function){0};
}

static inline bool zl_fn_is_none(ZL_Function value) {
    return value.call == NULL;
}

static inline bool zl_fn_matches(ZL_Function value, const char* signature) {
    return value.call != NULL && value.signature != NULL && signature != NULL
        && strcmp(value.signature, signature) == 0;
}

static inline bool zl_fn_equal(ZL_Function left, ZL_Function right) {
    return left.call == right.call && left.env == right.env && left.owner == right.owner;
}

#define ZL_FN_CALL_AS(TYPE, VALUE) ((TYPE)((VALUE).call))

typedef struct ZL_String {
    const char* data;
    size_t byte_len;
    ZL_ArcControl* owner;
} ZL_String;

static inline ZL_String zl_string_borrow_n(const char* data, size_t byte_len) {
    return (ZL_String){ .data = data ? data : "", .byte_len = data ? byte_len : 0u, .owner = NULL };
}

static inline ZL_String zl_string_borrow(const char* data) {
    return zl_string_borrow_n(data, data ? strlen(data) : 0u);
}

static inline ZL_String zl_string_copy_n(const char* data, size_t byte_len) {
    char* owned = (char*)malloc(byte_len + 1u);
    if (!owned) zy2_arc_panic("cannot allocate str data");
    if (byte_len > 0u && data) memcpy(owned, data, byte_len);
    owned[byte_len] = '\0';
    return (ZL_String){ .data = owned, .byte_len = byte_len, .owner = zy2_arc_new(owned, free) };
}

static inline ZL_String zl_string_copy(const char* data) {
    return zl_string_copy_n(data, data ? strlen(data) : 0u);
}

static inline ZL_String zl_string_retain(ZL_String value) {
    zy2_arc_retain(value.owner);
    return value;
}

static inline void zl_string_release(ZL_String value) {
    zy2_arc_release(value.owner);
}

static inline const char* zl_string_data(ZL_String value) {
    return value.data ? value.data : "";
}

static inline size_t zl_string_byte_len(ZL_String value) {
    return value.byte_len;
}

static inline bool zl_string_equal(ZL_String left, ZL_String right) {
    if (left.byte_len != right.byte_len) return false;
    if (left.byte_len == 0u) return true;
    return memcmp(zl_string_data(left), zl_string_data(right), left.byte_len) == 0;
}

static inline size_t zl_string_scalar_len(ZL_String value) {
    const unsigned char* bytes = (const unsigned char*)zl_string_data(value);
    size_t offset = 0u;
    size_t count = 0u;
    while (offset < value.byte_len) {
        size_t width = zy2_utf8_character_size(bytes + offset, value.byte_len - offset);
        if (width == 0u) zy2_arc_panic("str contains invalid UTF-8");
        offset += width;
        count += 1u;
    }
    return count;
}

static inline bool zl_string_scalar_get(ZL_String value, size_t index, ZL_String* result) {
    const unsigned char* bytes = (const unsigned char*)zl_string_data(value);
    size_t offset = 0u;
    size_t scalar = 0u;
    if (!result) return false;
    while (offset < value.byte_len) {
        size_t width = zy2_utf8_character_size(bytes + offset, value.byte_len - offset);
        if (width == 0u) return false;
        if (scalar == index) {
            *result = zl_string_copy_n((const char*)bytes + offset, width);
            return true;
        }
        offset += width;
        scalar += 1u;
    }
    return false;
}

static inline bool zl_string_scalar_slice(
    ZL_String value,
    size_t start,
    size_t end,
    ZL_String* result
) {
    const unsigned char* bytes = (const unsigned char*)zl_string_data(value);
    size_t offset = 0u;
    size_t scalar = 0u;
    size_t byte_start = 0u;
    size_t byte_end = 0u;
    bool found_start = start == 0u;
    bool found_end = end == 0u;
    if (!result || start > end) return false;
    while (offset < value.byte_len) {
        size_t width;
        if (scalar == start) {
            byte_start = offset;
            found_start = true;
        }
        if (scalar == end) {
            byte_end = offset;
            found_end = true;
            break;
        }
        width = zy2_utf8_character_size(bytes + offset, value.byte_len - offset);
        if (width == 0u) return false;
        offset += width;
        scalar += 1u;
    }
    if (scalar == start) {
        byte_start = offset;
        found_start = true;
    }
    if (scalar == end) {
        byte_end = offset;
        found_end = true;
    }
    if (!found_start || !found_end) return false;
    *result = zl_string_copy_n((const char*)bytes + byte_start, byte_end - byte_start);
    return true;
}

#ifdef ZY2_RUNTIME_IMPLEMENTATION
ZYENLANG_API ZL_String zl_abi_string_copy_n(const char* data, size_t byte_len) {
    return zl_string_copy_n(data, byte_len);
}

ZYENLANG_API ZL_String zl_abi_string_retain(ZL_String value) {
    return zl_string_retain(value);
}

ZYENLANG_API void zl_abi_string_release(ZL_String value) {
    zl_string_release(value);
}

ZYENLANG_API const char* zl_abi_string_data(ZL_String value) {
    return zl_string_data(value);
}

ZYENLANG_API size_t zl_abi_string_byte_len(ZL_String value) {
    return zl_string_byte_len(value);
}

ZYENLANG_API ZL_Function zl_abi_fn_retain(ZL_Function value) {
    return zl_fn_retain(value);
}

ZYENLANG_API void zl_abi_fn_release(ZL_Function value) {
    zl_fn_release(value);
}

ZYENLANG_API void zl_abi_fn_assign(ZL_Function* target, ZL_Function value) {
    zl_fn_assign(target, value);
}

ZYENLANG_API void zl_abi_fn_clear(ZL_Function* target) {
    zl_fn_clear(target);
}

ZYENLANG_API bool zl_abi_fn_is_none(ZL_Function value) {
    return zl_fn_is_none(value);
}

ZYENLANG_API bool zl_abi_fn_matches(ZL_Function value, const char* signature) {
    return zl_fn_matches(value, signature);
}
#endif

static inline int zy2_arc_assert_clean(void) {
    size_t live = atomic_load_explicit(&zy2_arc_live_controls, memory_order_relaxed);
    if (live == 0u) return 1;
    zy2_error_begin();
    fprintf(stderr, "<runtime>:0:0: ARC leak: %zu control block(s) still live\n", live);
    zy2_error_end();
    return 0;
}

#define ZY2_ERROR_MAX_FRAMES 32u

typedef struct zy2_ErrorFrame {
    ZL_String function_name;
    ZL_String source_file;
    uint32_t line;
    uint32_t column;
} zy2_ErrorFrame;

typedef struct zy2_Error {
    ZL_String message;
    ZL_String source_file;
    uint32_t line;
    uint32_t column;
    uint32_t frame_count;
    bool stack_truncated;
    zy2_ErrorFrame frames[ZY2_ERROR_MAX_FRAMES];
} zy2_Error;

static inline zy2_Error zy2_error_retain(zy2_Error value) {
    value.message = zl_string_retain(value.message);
    value.source_file = zl_string_retain(value.source_file);
    for (uint32_t index = 0u; index < value.frame_count; ++index) {
        value.frames[index].function_name = zl_string_retain(value.frames[index].function_name);
        value.frames[index].source_file = zl_string_retain(value.frames[index].source_file);
    }
    return value;
}

static inline void zy2_error_release(zy2_Error value) {
    zl_string_release(value.message);
    zl_string_release(value.source_file);
    for (uint32_t index = 0u; index < value.frame_count; ++index) {
        zl_string_release(value.frames[index].function_name);
        zl_string_release(value.frames[index].source_file);
    }
}

static inline void zy2_error_push_frame(
    zy2_Error* error,
    ZL_String function_name,
    ZL_String source_file,
    uint32_t line,
    uint32_t column
) {
    zy2_ErrorFrame* frame;
    if (!error) return;
    if (error->frame_count >= ZY2_ERROR_MAX_FRAMES) {
        error->stack_truncated = true;
        return;
    }
    frame = &error->frames[error->frame_count++];
    frame->function_name = zl_string_retain(function_name);
    frame->source_file = zl_string_retain(source_file);
    frame->line = line;
    frame->column = column;
}

static inline ZL_String zy2_error_frame_format(zy2_ErrorFrame frame) {
    const char* function_name = zl_string_data(frame.function_name);
    const char* source_file = zl_string_data(frame.source_file);
    int required = snprintf(
        NULL,
        0,
        "%s (%s:%u:%u)",
        function_name,
        source_file,
        frame.line,
        frame.column
    );
    char* buffer;
    if (required < 0) return zl_string_copy("<invalid error frame>");
    buffer = (char*)malloc((size_t)required + 1u);
    if (!buffer) zy2_arc_panic("cannot allocate Error stack frame");
    (void)snprintf(
        buffer,
        (size_t)required + 1u,
        "%s (%s:%u:%u)",
        function_name,
        source_file,
        frame.line,
        frame.column
    );
    return (ZL_String){
        .data = buffer,
        .byte_len = (size_t)required,
        .owner = zy2_arc_new(buffer, free),
    };
}

static int zy2_process_argc = 0;
static char** zy2_process_argv = NULL;

static inline void zy2_set_process_args(int argc, char** argv) {
    zy2_process_argc = argc;
    zy2_process_argv = argv;
}

static inline int zy2_hex_digit(char value) {
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    if (value >= 'A' && value <= 'F') return value - 'A' + 10;
    return -1;
}

static inline bool zy2_parse_hex_color(const char* color, unsigned int* red, unsigned int* green, unsigned int* blue) {
    int digits[6];
    if (!color || strlen(color) != 7u || color[0] != '#') return false;
    for (size_t index = 0u; index < 6u; ++index) {
        digits[index] = zy2_hex_digit(color[index + 1u]);
        if (digits[index] < 0) return false;
    }
    *red = (unsigned int)(digits[0] * 16 + digits[1]);
    *green = (unsigned int)(digits[2] * 16 + digits[3]);
    *blue = (unsigned int)(digits[4] * 16 + digits[5]);
    return true;
}

static inline void zy2_print_cmd(ZL_String value, ZL_String color) {
    unsigned int red = 0u;
    unsigned int green = 0u;
    unsigned int blue = 0u;
    bool colored = zy2_parse_hex_color(zl_string_data(color), &red, &green, &blue) && zy2_stream_color_enabled(stdout);
    if (colored) fprintf(stdout, "\x1b[38;2;%u;%u;%um", red, green, blue);
    fprintf(stdout, "%s\n", zl_string_data(value));
    if (colored) fputs("\x1b[0m", stdout);
}

static inline void zy2_unhandled_error(zy2_Error error) {
    zy2_error_begin();
    fprintf(
        stderr,
        "%s:%u:%u: %s\n",
        error.source_file.data ? error.source_file.data : "<runtime>",
        error.line,
        error.column,
        error.message.data ? error.message.data : "unhandled Error"
    );
    for (uint32_t index = 0u; index < error.frame_count; ++index) {
        const zy2_ErrorFrame* frame = &error.frames[index];
        fprintf(
            stderr,
            "  at %s (%s:%u:%u)\n",
            zl_string_data(frame->function_name),
            zl_string_data(frame->source_file),
            frame->line,
            frame->column
        );
    }
    if (error.stack_truncated) fputs("  ... stack truncated\n", stderr);
    zy2_error_end();
    exit(1);
}

static inline bool zy2_compare_i64_u64_eq(int64_t left, uint64_t right) {
    return left >= 0 && (uint64_t)left == right;
}

static inline bool zy2_compare_i64_u64_ne(int64_t left, uint64_t right) {
    return !zy2_compare_i64_u64_eq(left, right);
}

static inline bool zy2_compare_i64_u64_lt(int64_t left, uint64_t right) {
    return left < 0 || (uint64_t)left < right;
}

static inline bool zy2_compare_i64_u64_le(int64_t left, uint64_t right) {
    return left < 0 || (uint64_t)left <= right;
}

static inline bool zy2_compare_i64_u64_gt(int64_t left, uint64_t right) {
    return left >= 0 && (uint64_t)left > right;
}

static inline bool zy2_compare_i64_u64_ge(int64_t left, uint64_t right) {
    return left >= 0 && (uint64_t)left >= right;
}

#ifdef ZY2_ENABLE_THREADS
#ifdef _WIN32
#else
#include <pthread.h>
#endif

typedef void (*zy2_TaskEntry)(void* context, void* result);

typedef struct zy2_Task {
    zy2_TaskEntry entry;
    void* context;
    void* result;
    size_t result_size;
#ifdef _WIN32
    HANDLE thread;
#else
    pthread_t thread;
#endif
} zy2_Task;

static inline void zy2_task_panic(const char* message) {
    zy2_arc_panic(message);
}

#ifdef _WIN32
static DWORD WINAPI zy2_task_entry(void* raw_task) {
    zy2_Task* task = (zy2_Task*)raw_task;
    task->entry(task->context, task->result);
    return 0;
}
#else
static void* zy2_task_entry(void* raw_task) {
    zy2_Task* task = (zy2_Task*)raw_task;
    task->entry(task->context, task->result);
    return NULL;
}
#endif

static inline zy2_Task* zy2_task_spawn(zy2_TaskEntry entry, void* context, size_t result_size) {
    zy2_Task* task = (zy2_Task*)calloc(1, sizeof(zy2_Task));
    if (!task) zy2_task_panic("cannot allocate Task control block");
    task->entry = entry;
    task->context = context;
    task->result_size = result_size;
    if (result_size > 0) {
        task->result = calloc(1, result_size);
        if (!task->result) {
            free(task);
            zy2_task_panic("cannot allocate Task result");
        }
    }
#ifdef _WIN32
    task->thread = CreateThread(NULL, 0, zy2_task_entry, task, 0, NULL);
    if (!task->thread) {
        free(task->result);
        free(task);
        zy2_task_panic("cannot create OS thread");
    }
#else
    if (pthread_create(&task->thread, NULL, zy2_task_entry, task) != 0) {
        free(task->result);
        free(task);
        zy2_task_panic("cannot create OS thread");
    }
#endif
    return task;
}

static inline void zy2_task_await_and_destroy(zy2_Task** slot, void* output, size_t output_size) {
    zy2_Task* task;
    if (!slot || !*slot) zy2_task_panic("attempted to await an empty or consumed Task");
    task = *slot;
#ifdef _WIN32
    if (WaitForSingleObject(task->thread, INFINITE) != WAIT_OBJECT_0) {
        zy2_task_panic("waiting for OS thread failed");
    }
    CloseHandle(task->thread);
#else
    if (pthread_join(task->thread, NULL) != 0) zy2_task_panic("waiting for OS thread failed");
#endif
    if (output_size != task->result_size) zy2_task_panic("Task result ABI mismatch");
    if (output_size > 0) memcpy(output, task->result, output_size);
    free(task->result);
    free(task);
    *slot = NULL;
}
#endif

#endif
