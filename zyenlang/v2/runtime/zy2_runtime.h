#ifndef ZYENLANG_V2_RUNTIME_H
#define ZYENLANG_V2_RUNTIME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(__GNUC__) || defined(__clang__)
#define ZY2_MAYBE_UNUSED __attribute__((unused))
#else
#define ZY2_MAYBE_UNUSED
#endif

typedef void (*zy2_ArcDrop)(void* payload);

typedef struct zy2_ArcControl {
    atomic_size_t refs;
    void* payload;
    zy2_ArcDrop drop;
} zy2_ArcControl;

static atomic_size_t zy2_arc_live_controls = 0;

static inline void zy2_arc_panic(const char* message) {
    fprintf(stderr, "<runtime>:0:0: %s\n", message);
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

static inline int zy2_arc_assert_clean(void) {
    size_t live = atomic_load_explicit(&zy2_arc_live_controls, memory_order_relaxed);
    if (live == 0u) return 1;
    fprintf(stderr, "<runtime>:0:0: ARC leak: %zu control block(s) still live\n", live);
    return 0;
}

typedef struct zy2_Error {
    const char* message;
    const char* source_file;
    uint32_t line;
    uint32_t column;
} zy2_Error;

static int zy2_process_argc = 0;
static char** zy2_process_argv = NULL;

static inline void zy2_set_process_args(int argc, char** argv) {
    zy2_process_argc = argc;
    zy2_process_argv = argv;
}

static inline void zy2_print(const char* value) {
    puts(value ? value : "null");
}

static inline void zy2_eprint(const char* value) {
    fprintf(stderr, "%s\n", value ? value : "null");
}

static inline void zy2_unhandled_error(zy2_Error error) {
    fprintf(
        stderr,
        "%s:%u:%u: %s\n",
        error.source_file ? error.source_file : "<runtime>",
        error.line,
        error.column,
        error.message ? error.message : "unhandled Error"
    );
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
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
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
    fprintf(stderr, "<runtime>:0:0: %s\n", message);
    exit(1);
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
