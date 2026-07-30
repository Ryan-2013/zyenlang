#ifndef ZYENLANG_V2_RUNTIME_H
#define ZYENLANG_V2_RUNTIME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
