#define _POSIX_C_SOURCE 200809L

#include "gui_native.h"

#include <ctype.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#define zltk_popen _popen
#define zltk_pclose _pclose
#else
#include <dlfcn.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#define zltk_popen popen
#define zltk_pclose pclose
#endif

#ifndef ZLTK_PATH_CAPACITY
#define ZLTK_PATH_CAPACITY 4096
#endif

enum {
    ZLTK_COMMAND_RAW = 1,
    ZLTK_COMMAND_CODEVIEW_TEXT = 2,
    ZLTK_COMMAND_COMPLETION = 3,
    ZLTK_EVENT_CAPACITY = 2048,
    ZLTK_IMAGE_CAPACITY = 64,
    ZLTK_FONT_CAPACITY = 64,
    ZLTK_KEY_ESCAPE = 256,
    ZLTK_KEY_ENTER = 257,
    ZLTK_KEY_TAB = 258,
    ZLTK_KEY_BACKSPACE = 259,
    ZLTK_KEY_DELETE = 261,
    ZLTK_KEY_RIGHT = 262,
    ZLTK_KEY_LEFT = 263,
    ZLTK_KEY_DOWN = 264,
    ZLTK_KEY_UP = 265,
    ZLTK_KEY_PAGE_UP = 266,
    ZLTK_KEY_PAGE_DOWN = 267,
    ZLTK_KEY_HOME = 268,
    ZLTK_KEY_END = 269,
    ZLTK_KEY_LEFT_SHIFT = 340,
    ZLTK_KEY_LEFT_CONTROL = 341,
    ZLTK_KEY_LEFT_ALT = 342,
    ZLTK_KEY_RIGHT_SHIFT = 344,
    ZLTK_KEY_RIGHT_CONTROL = 345,
    ZLTK_KEY_RIGHT_ALT = 346,
    ZLTK_MOUSE_LEFT = 0,
    ZLTK_FLAG_WINDOW_RESIZABLE = 0x00000004
};

typedef struct {
    float x;
    float y;
} Zltk_Vector2;

typedef struct {
    float x;
    float y;
    float width;
    float height;
} Zltk_Rectangle;

typedef struct {
    unsigned char r;
    unsigned char g;
    unsigned char b;
    unsigned char a;
} Zltk_Color;

typedef struct {
    unsigned int id;
    int width;
    int height;
    int mipmaps;
    int format;
} Zltk_Texture;

typedef struct {
    int baseSize;
    int glyphCount;
    int glyphPadding;
    Zltk_Texture texture;
    Zltk_Rectangle* recs;
    void* glyphs;
} Zltk_Font;

typedef struct {
    void (*SetConfigFlags)(unsigned int flags);
    void (*SetTraceLogLevel)(int level);
    void (*InitWindow)(int width, int height, const char* title);
    bool (*IsWindowReady)(void);
    void (*CloseWindow)(void);
    bool (*WindowShouldClose)(void);
    void (*SetTargetFPS)(int fps);
    void (*SetExitKey)(int key);
    void (*BeginDrawing)(void);
    void (*EndDrawing)(void);
    void (*ClearBackground)(Zltk_Color color);
    void (*DrawLineEx)(Zltk_Vector2 start, Zltk_Vector2 end, float thick, Zltk_Color color);
    void (*DrawRectangle)(int x, int y, int width, int height, Zltk_Color color);
    void (*DrawRectangleLinesEx)(Zltk_Rectangle rect, float thick, Zltk_Color color);
    void (*DrawCircle)(int x, int y, float radius, Zltk_Color color);
    void (*DrawCircleLines)(int x, int y, float radius, Zltk_Color color);
    void (*DrawText)(const char* text, int x, int y, int size, Zltk_Color color);
    int (*MeasureText)(const char* text, int size);
    Zltk_Font (*LoadFontEx)(const char* path, int font_size, int* codepoints, int codepoint_count);
    bool (*IsFontValid)(Zltk_Font font);
    void (*UnloadFont)(Zltk_Font font);
    void (*DrawTextEx)(Zltk_Font font, const char* text, Zltk_Vector2 position, float font_size, float spacing, Zltk_Color tint);
    Zltk_Vector2 (*MeasureTextEx)(Zltk_Font font, const char* text, float font_size, float spacing);
    Zltk_Texture (*LoadTexture)(const char* path);
    bool (*IsTextureValid)(Zltk_Texture texture);
    void (*UnloadTexture)(Zltk_Texture texture);
    void (*DrawTexture)(Zltk_Texture texture, int x, int y, Zltk_Color tint);
    int (*GetKeyPressed)(void);
    int (*GetCharPressed)(void);
    bool (*IsKeyDown)(int key);
    bool (*IsMouseButtonPressed)(int button);
    bool (*IsMouseButtonDown)(int button);
    bool (*IsMouseButtonReleased)(int button);
    Zltk_Vector2 (*GetMousePosition)(void);
    Zltk_Vector2 (*GetMouseWheelMoveV)(void);
    double (*GetTime)(void);
    void (*WaitTime)(double seconds);
    void (*PollInputEvents)(void);
} Zltk_Raylib;

typedef struct {
    int kind;
    char* raw;
    char* text;
    int x;
    int y;
    int width;
    int height;
    int first_line;
    int line_height;
    int char_width;
    int size;
    int stamp;
    int selection_start_line;
    int selection_start_column;
    int selection_end_line;
    int selection_end_column;
} Zltk_Command;

typedef struct {
    Zltk_Command* items;
    int count;
    int capacity;
} Zltk_Frame;

typedef struct {
    char path[ZLTK_PATH_CAPACITY];
    Zltk_Texture texture;
} Zltk_Image;

typedef struct {
    char path[ZLTK_PATH_CAPACITY];
    int base_size;
    int valid;
    Zltk_Font font;
} Zltk_FontEntry;

typedef struct {
    char script_path[ZLTK_PATH_CAPACITY];
    int is_session;
    int window_open;
    int frame_active;
    int closed;
    int quit_sent;
    Zltk_Frame building_frame;
    Zltk_Frame display_frame;
    char events[ZLTK_EVENT_CAPACITY][512];
    int event_head;
    int event_count;
    Zltk_Image images[ZLTK_IMAGE_CAPACITY];
    int image_count;
    Zltk_FontEntry fonts[ZLTK_FONT_CAPACITY];
    int font_count;
    Zltk_Vector2 last_mouse;
    double last_motion_time;
    double last_drag_time;
    int repeat_key;
    double repeat_started;
    double repeat_last;
} Zltk_App;

static Zltk_Raylib g_rl;
static Zltk_App g_zltk = { .script_path = "zyen_tk_scene.ztk" };
static void* g_raylib_handle = NULL;

static Zltk_Color zltk_rgba(int r, int g, int b, int a) {
    Zltk_Color color;
    color.r = (unsigned char)r;
    color.g = (unsigned char)g;
    color.b = (unsigned char)b;
    color.a = (unsigned char)a;
    return color;
}

static char* zltk_strdup(const char* text) {
    const char* value = text ? text : "";
    size_t size = strlen(value) + 1;
    char* copy = (char*)malloc(size);
    if (copy) memcpy(copy, value, size);
    return copy;
}

static void zltk_clean_copy(char* out, size_t cap, const char* text) {
    size_t j = 0;
    if (cap == 0) return;
    if (!text) text = "";
    for (size_t i = 0; text[i] && j + 1 < cap; i++) {
        char ch = text[i];
        if (ch == '\n' || ch == '\r' || ch == '\t') ch = ' ';
        out[j++] = ch;
    }
    out[j] = 0;
}

static void zltk_dirname(char* path) {
    char* slash = strrchr(path, '/');
    char* backslash = strrchr(path, '\\');
    char* cut = slash;
    if (backslash && (!cut || backslash > cut)) cut = backslash;
    if (cut) *cut = 0;
    else strcpy(path, ".");
}

static void zltk_join(char* out, size_t cap, const char* base, const char* relative) {
    size_t base_length;
    size_t relative_length;
    if (!out || cap == 0) return;
    if (!base) base = ".";
    if (!relative) relative = "";
    base_length = strlen(base);
    relative_length = strlen(relative);
    if (base_length >= cap || relative_length >= cap - base_length
            || base_length + 1 + relative_length >= cap) {
        out[0] = 0;
        return;
    }
    memcpy(out, base, base_length);
    out[base_length] = '/';
    memcpy(out + base_length + 1, relative, relative_length + 1);
}

static const char* zltk_raylib_relative_path(void) {
#ifdef _WIN32
    return "windows-x64/raylib.dll";
#elif defined(__APPLE__)
    return "macos-universal/libraylib.dylib";
#elif defined(__aarch64__) || defined(__arm64__)
    return "linux-arm64/libraylib.so";
#else
    return "linux-x64/libraylib.so";
#endif
}

static const char* zltk_raylib_filename(void) {
#ifdef _WIN32
    return "raylib.dll";
#elif defined(__APPLE__)
    return "libraylib.dylib";
#else
    return "libraylib.so";
#endif
}

static int zltk_executable_dir(char* out, size_t cap) {
#ifdef _WIN32
    DWORD length = GetModuleFileNameA(NULL, out, (DWORD)cap);
    if (length == 0 || length >= cap) return -1;
#elif defined(__APPLE__)
    uint32_t size = (uint32_t)cap;
    if (_NSGetExecutablePath(out, &size) != 0) return -1;
#else
    ssize_t length = readlink("/proc/self/exe", out, cap - 1);
    if (length <= 0 || (size_t)length >= cap) return -1;
    out[length] = 0;
#endif
    zltk_dirname(out);
    return 0;
}

static void* zltk_library_open(const char* path) {
#ifdef _WIN32
    return (void*)LoadLibraryA(path);
#else
    return dlopen(path, RTLD_NOW | RTLD_LOCAL);
#endif
}

static void* zltk_library_symbol(const char* name) {
#ifdef _WIN32
    return (void*)GetProcAddress((HMODULE)g_raylib_handle, name);
#else
    return dlsym(g_raylib_handle, name);
#endif
}

static int zltk_try_raylib_path(const char* path) {
    if (!path || !path[0]) return -1;
    g_raylib_handle = zltk_library_open(path);
    return g_raylib_handle ? 0 : -1;
}

static int zltk_open_raylib(void) {
    if (g_raylib_handle) return 0;
    const char* env_path = getenv("ZYENLANG_RAYLIB");
    if (zltk_try_raylib_path(env_path) == 0) return 0;

    char source_dir[ZLTK_PATH_CAPACITY];
    char candidate[ZLTK_PATH_CAPACITY];
    strncpy(source_dir, __FILE__, sizeof(source_dir) - 1);
    source_dir[sizeof(source_dir) - 1] = 0;
    zltk_dirname(source_dir);

    char relative[ZLTK_PATH_CAPACITY];
    snprintf(relative, sizeof(relative), "../../vendor/raylib/%s", zltk_raylib_relative_path());
    zltk_join(candidate, sizeof(candidate), source_dir, relative);
    if (zltk_try_raylib_path(candidate) == 0) return 0;

    snprintf(relative, sizeof(relative), "../zyenlang/vendor/raylib/%s", zltk_raylib_relative_path());
    zltk_join(candidate, sizeof(candidate), source_dir, relative);
    if (zltk_try_raylib_path(candidate) == 0) return 0;

    char executable_dir[ZLTK_PATH_CAPACITY];
    if (zltk_executable_dir(executable_dir, sizeof(executable_dir)) == 0) {
        zltk_join(candidate, sizeof(candidate), executable_dir, zltk_raylib_filename());
        if (zltk_try_raylib_path(candidate) == 0) return 0;
        snprintf(relative, sizeof(relative), "runtime/%s", zltk_raylib_filename());
        zltk_join(candidate, sizeof(candidate), executable_dir, relative);
        if (zltk_try_raylib_path(candidate) == 0) return 0;
    }

#ifdef _WIN32
    if (zltk_try_raylib_path("raylib.dll") == 0) return 0;
#elif defined(__APPLE__)
    if (zltk_try_raylib_path("libraylib.dylib") == 0) return 0;
#else
    if (zltk_try_raylib_path("libraylib.so.600") == 0) return 0;
    if (zltk_try_raylib_path("libraylib.so") == 0) return 0;
#endif
    fprintf(stderr, "ZyenLang GUI: bundled raylib 6.0 could not be loaded; set ZYENLANG_RAYLIB to its full path\n");
    return -1;
}

#define ZLTK_LOAD(name) do { \
    void* symbol = zltk_library_symbol(#name); \
    if (!symbol) { fprintf(stderr, "ZyenLang GUI: raylib symbol %s is missing\n", #name); return -1; } \
    memcpy(&g_rl.name, &symbol, sizeof(symbol)); \
} while (0)

static int zltk_load_raylib(void) {
    if (g_rl.InitWindow) return 0;
    if (zltk_open_raylib() != 0) return -1;
    ZLTK_LOAD(SetConfigFlags);
    ZLTK_LOAD(SetTraceLogLevel);
    ZLTK_LOAD(InitWindow);
    ZLTK_LOAD(IsWindowReady);
    ZLTK_LOAD(CloseWindow);
    ZLTK_LOAD(WindowShouldClose);
    ZLTK_LOAD(SetTargetFPS);
    ZLTK_LOAD(SetExitKey);
    ZLTK_LOAD(BeginDrawing);
    ZLTK_LOAD(EndDrawing);
    ZLTK_LOAD(ClearBackground);
    ZLTK_LOAD(DrawLineEx);
    ZLTK_LOAD(DrawRectangle);
    ZLTK_LOAD(DrawRectangleLinesEx);
    ZLTK_LOAD(DrawCircle);
    ZLTK_LOAD(DrawCircleLines);
    ZLTK_LOAD(DrawText);
    ZLTK_LOAD(MeasureText);
    ZLTK_LOAD(LoadFontEx);
    ZLTK_LOAD(IsFontValid);
    ZLTK_LOAD(UnloadFont);
    ZLTK_LOAD(DrawTextEx);
    ZLTK_LOAD(MeasureTextEx);
    ZLTK_LOAD(LoadTexture);
    ZLTK_LOAD(IsTextureValid);
    ZLTK_LOAD(UnloadTexture);
    ZLTK_LOAD(DrawTexture);
    ZLTK_LOAD(GetKeyPressed);
    ZLTK_LOAD(GetCharPressed);
    ZLTK_LOAD(IsKeyDown);
    ZLTK_LOAD(IsMouseButtonPressed);
    ZLTK_LOAD(IsMouseButtonDown);
    ZLTK_LOAD(IsMouseButtonReleased);
    ZLTK_LOAD(GetMousePosition);
    ZLTK_LOAD(GetMouseWheelMoveV);
    ZLTK_LOAD(GetTime);
    ZLTK_LOAD(WaitTime);
    ZLTK_LOAD(PollInputEvents);
    return 0;
}

static void zltk_command_clear(Zltk_Command* command) {
    if (!command) return;
    free(command->raw);
    free(command->text);
    memset(command, 0, sizeof(*command));
}

static void zltk_frame_clear(Zltk_Frame* frame) {
    if (!frame) return;
    for (int i = 0; i < frame->count; i++) zltk_command_clear(&frame->items[i]);
    frame->count = 0;
}

static Zltk_Command* zltk_frame_push(Zltk_Frame* frame) {
    if (!frame) return NULL;
    if (frame->count >= frame->capacity) {
        int next_capacity = frame->capacity > 0 ? frame->capacity * 2 : 128;
        Zltk_Command* next = (Zltk_Command*)realloc(frame->items, sizeof(Zltk_Command) * (size_t)next_capacity);
        if (!next) return NULL;
        frame->items = next;
        frame->capacity = next_capacity;
    }
    Zltk_Command* command = &frame->items[frame->count++];
    memset(command, 0, sizeof(*command));
    return command;
}

static void zltk_append_event(const char* line) {
    if (!g_zltk.is_session) return;
    if (g_zltk.event_count == ZLTK_EVENT_CAPACITY) {
        g_zltk.event_head = (g_zltk.event_head + 1) % ZLTK_EVENT_CAPACITY;
        g_zltk.event_count--;
    }
    int tail = (g_zltk.event_head + g_zltk.event_count) % ZLTK_EVENT_CAPACITY;
    strncpy(g_zltk.events[tail], line ? line : "", sizeof(g_zltk.events[tail]) - 1);
    g_zltk.events[tail][sizeof(g_zltk.events[tail]) - 1] = 0;
    g_zltk.event_count++;
}

static int zltk_pop_event(char* out, size_t cap) {
    if (g_zltk.event_count <= 0) return 0;
    strncpy(out, g_zltk.events[g_zltk.event_head], cap - 1);
    out[cap - 1] = 0;
    g_zltk.event_head = (g_zltk.event_head + 1) % ZLTK_EVENT_CAPACITY;
    g_zltk.event_count--;
    return 1;
}

static int zltk_to_int(const char* text, int fallback) {
    if (!text || !text[0]) return fallback;
    return atoi(text);
}

static Zltk_Color zltk_color(const char* text, Zltk_Color fallback) {
    if (!text) return fallback;
    size_t length = strlen(text);
    if (text[0] == '#' && length >= 7) {
        char part[3] = {0};
        part[0] = text[1]; part[1] = text[2]; int r = (int)strtol(part, NULL, 16);
        part[0] = text[3]; part[1] = text[4]; int g = (int)strtol(part, NULL, 16);
        part[0] = text[5]; part[1] = text[6]; int b = (int)strtol(part, NULL, 16);
        int a = 255;
        if (length >= 9) { part[0] = text[7]; part[1] = text[8]; a = (int)strtol(part, NULL, 16); }
        return zltk_rgba(r, g, b, a);
    }
    if (strcmp(text, "white") == 0) return zltk_rgba(255, 255, 255, 255);
    if (strcmp(text, "black") == 0) return zltk_rgba(0, 0, 0, 255);
    if (strcmp(text, "red") == 0) return zltk_rgba(220, 40, 40, 255);
    if (strcmp(text, "green") == 0) return zltk_rgba(40, 180, 80, 255);
    if (strcmp(text, "blue") == 0) return zltk_rgba(60, 130, 240, 255);
    return fallback;
}

static int zltk_split_tabs(char* line, char** columns, int max_columns) {
    int count = 0;
    char* cursor = line;
    while (count < max_columns) {
        columns[count++] = cursor;
        char* tab = strchr(cursor, '\t');
        if (!tab) break;
        *tab = 0;
        cursor = tab + 1;
    }
    return count;
}

static Zltk_Texture* zltk_image_texture(const char* path) {
    if (!path || !path[0]) return NULL;
    for (int i = 0; i < g_zltk.image_count; i++) {
        if (strcmp(g_zltk.images[i].path, path) == 0) return &g_zltk.images[i].texture;
    }
    if (g_zltk.image_count >= ZLTK_IMAGE_CAPACITY) return NULL;
    Zltk_Image* image = &g_zltk.images[g_zltk.image_count];
    image->texture = g_rl.LoadTexture(path);
    if (!g_rl.IsTextureValid(image->texture)) return NULL;
    strncpy(image->path, path, sizeof(image->path) - 1);
    image->path[sizeof(image->path) - 1] = 0;
    g_zltk.image_count++;
    return &image->texture;
}

static void zltk_images_clear(void) {
    if (!g_rl.UnloadTexture) return;
    for (int i = 0; i < g_zltk.image_count; i++) g_rl.UnloadTexture(g_zltk.images[i].texture);
    memset(g_zltk.images, 0, sizeof(g_zltk.images));
    g_zltk.image_count = 0;
}

static Zltk_Font* zltk_font_resource(const char* path, int base_size) {
    if (!path || !path[0]) return NULL;
    if (base_size <= 0) base_size = 32;
    for (int i = 0; i < g_zltk.font_count; i++) {
        Zltk_FontEntry* entry = &g_zltk.fonts[i];
        if (entry->base_size == base_size && strcmp(entry->path, path) == 0) return entry->valid ? &entry->font : NULL;
    }
    if (g_zltk.font_count >= ZLTK_FONT_CAPACITY) return NULL;
    Zltk_FontEntry* entry = &g_zltk.fonts[g_zltk.font_count++];
    memset(entry, 0, sizeof(*entry));
    strncpy(entry->path, path, sizeof(entry->path) - 1);
    entry->base_size = base_size;
    entry->font = g_rl.LoadFontEx(path, base_size, NULL, 0);
    entry->valid = g_rl.IsFontValid(entry->font) ? 1 : 0;
    return entry->valid ? &entry->font : NULL;
}

static int zltk_file_exists(const char* path) {
    FILE* file = fopen(path, "rb");
    if (!file) return 0;
    fclose(file);
    return 1;
}

static const char* zltk_system_font(int monospace) {
#ifdef _WIN32
    static const char* mono_fonts[] = {
        "C:/Windows/Fonts/CascadiaMono.ttf",
        "C:/Windows/Fonts/CascadiaCode.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/lucon.ttf"
    };
    static const char* ui_fonts[] = {
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf"
    };
#elif defined(__APPLE__)
    static const char* mono_fonts[] = {
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/Library/Fonts/Andale Mono.ttf"
    };
    static const char* ui_fonts[] = {
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc"
    };
#else
    static const char* mono_fonts[] = {
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf"
    };
    static const char* ui_fonts[] = {
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf"
    };
#endif
    const char** candidates = monospace ? mono_fonts : ui_fonts;
    size_t count = monospace
        ? sizeof(mono_fonts) / sizeof(mono_fonts[0])
        : sizeof(ui_fonts) / sizeof(ui_fonts[0]);
    for (size_t index = 0; index < count; index++) {
        if (zltk_file_exists(candidates[index])) return candidates[index];
    }
    return NULL;
}

static Zltk_Font* zltk_system_font_resource(int monospace) {
    const char* path = zltk_system_font(monospace);
    return path ? zltk_font_resource(path, 32) : NULL;
}

static void zltk_fonts_clear(void) {
    if (g_rl.UnloadFont) {
        for (int i = 0; i < g_zltk.font_count; i++) {
            if (g_zltk.fonts[i].valid) g_rl.UnloadFont(g_zltk.fonts[i].font);
        }
    }
    memset(g_zltk.fonts, 0, sizeof(g_zltk.fonts));
    g_zltk.font_count = 0;
}

static void zltk_draw_font_text(const char* path, int base_size, int x, int y, const char* text, const char* color, int size) {
    Zltk_Color tint = zltk_color(color, zltk_rgba(255, 255, 255, 255));
    Zltk_Font* font = zltk_font_resource(path, base_size);
    if (font) {
        Zltk_Vector2 position = { (float)x, (float)y };
        g_rl.DrawTextEx(*font, text ? text : "", position, (float)(size > 0 ? size : 16), 0.0f, tint);
    } else {
        g_rl.DrawText(text ? text : "", x, y, size > 0 ? size : 16, tint);
    }
}

static void zltk_draw_system_text(int x, int y, const char* text, const char* color, int size) {
    int font_size = size > 0 ? size : 16;
    Zltk_Color tint = zltk_color(color, zltk_rgba(255, 255, 255, 255));
    Zltk_Font* font = zltk_system_font_resource(0);
    if (font) {
        Zltk_Vector2 position = { (float)x, (float)y };
        g_rl.DrawTextEx(*font, text ? text : "", position, (float)font_size, 0.25f, tint);
    } else {
        g_rl.DrawText(text ? text : "", x, y, font_size, tint);
    }
}

static int zltk_keyword(const char* word) {
    static const char* words[] = {
        "as", "await", "break", "catch", "continue", "else", "false", "fn", "for",
        "FILE__", "if", "import", "let", "native", "null", "private", "public", "recover",
        "return", "spawn", "stop", "struct", "throws", "true", "TYPEOF__", "while"
    };
    for (size_t index = 0; index < sizeof(words) / sizeof(words[0]); index++) {
        if (strcmp(word, words[index]) == 0) return 1;
    }
    return 0;
}

static int zltk_type_word(const char* word) {
    static const char* words[] = {
        "bool", "Error", "f32", "f64", "i8", "i16", "i32", "i64", "isize", "List",
        "str", "u8", "u16", "u32", "u64", "usize", "void"
    };
    for (size_t index = 0; index < sizeof(words) / sizeof(words[0]); index++) {
        if (strcmp(word, words[index]) == 0) return 1;
    }
    return 0;
}

static int zltk_utf8_columns(const char* text, size_t bytes) {
    int columns = 0;
    for (size_t index = 0; index < bytes; index++) {
        if (((unsigned char)text[index] & 0xc0u) != 0x80u) columns++;
    }
    return columns;
}

static void zltk_draw_code_token(
    const char* line,
    size_t start,
    size_t length,
    int x,
    int y,
    int char_width,
    int size,
    float spacing,
    Zltk_Font* font,
    Zltk_Color color
) {
    char* token = (char*)malloc(length + 1);
    if (!token) return;
    memcpy(token, line + start, length);
    token[length] = 0;
    int token_x = x + zltk_utf8_columns(line, start) * char_width;
    if (font) {
        Zltk_Vector2 position = { (float)token_x, (float)y };
        g_rl.DrawTextEx(*font, token, position, (float)size, spacing, color);
    } else {
        g_rl.DrawText(token, token_x, y, size, color);
    }
    free(token);
}

static void zltk_draw_highlighted_line(
    const char* line,
    int x,
    int y,
    int char_width,
    int size,
    float spacing,
    Zltk_Font* font
) {
    size_t length = strlen(line);
    size_t cursor = 0;
    while (cursor < length) {
        size_t start = cursor;
        Zltk_Color color = zltk_rgba(216, 212, 207, 255);
        if (line[cursor] == '/' && cursor + 1 < length && line[cursor + 1] == '/') {
            zltk_draw_code_token(line, cursor, length - cursor, x, y, char_width, size, spacing, font, zltk_rgba(111, 143, 114, 255));
            break;
        }
        if (line[cursor] == '"') {
            cursor++;
            while (cursor < length) {
                if (line[cursor] == '\\' && cursor + 1 < length) cursor += 2;
                else if (line[cursor++] == '"') break;
            }
            color = zltk_rgba(214, 176, 110, 255);
        } else if (isdigit((unsigned char)line[cursor])) {
            while (cursor < length && (isalnum((unsigned char)line[cursor]) || line[cursor] == '.')) cursor++;
            color = zltk_rgba(197, 168, 128, 255);
        } else if (isalpha((unsigned char)line[cursor]) || line[cursor] == '_') {
            while (cursor < length && (isalnum((unsigned char)line[cursor]) || line[cursor] == '_')) cursor++;
            size_t word_length = cursor - start;
            char word[64];
            size_t copy = word_length < sizeof(word) - 1 ? word_length : sizeof(word) - 1;
            memcpy(word, line + start, copy);
            word[copy] = 0;
            size_t lookahead = cursor;
            while (lookahead < length && isspace((unsigned char)line[lookahead])) lookahead++;
            if (zltk_keyword(word)) color = zltk_rgba(217, 119, 87, 255);
            else if (zltk_type_word(word) || isupper((unsigned char)word[0])) color = zltk_rgba(127, 175, 155, 255);
            else if (lookahead < length && line[lookahead] == '(') color = zltk_rgba(230, 192, 122, 255);
            else color = zltk_rgba(216, 212, 207, 255);
        } else {
            cursor++;
            if (strchr("{}()[]<>=:+-*|!", line[start])) color = zltk_rgba(184, 178, 173, 255);
        }
        zltk_draw_code_token(line, start, cursor - start, x, y, char_width, size, spacing, font, color);
    }
}

static char* zltk_expand_tabs(const char* text, size_t length) {
    char* expanded = (char*)malloc(length * 4 + 1);
    if (!expanded) return NULL;
    size_t output = 0;
    int column = 0;
    for (size_t index = 0; index < length; index++) {
        if (text[index] == '\t') {
            int spaces = 4 - (column % 4);
            while (spaces-- > 0) {
                expanded[output++] = ' ';
                column++;
            }
        } else {
            expanded[output++] = text[index];
            if (((unsigned char)text[index] & 0xc0u) != 0x80u) column++;
        }
    }
    expanded[output] = 0;
    return expanded;
}

static void zltk_draw_code_lines(const Zltk_Command* command) {
    if (!command || !command->text) return;
    int row_height = command->line_height > 0 ? command->line_height : 20;
    int glyph_width = command->char_width > 0 ? command->char_width : 10;
    int font_size = command->size > 0 ? command->size : 16;
    int max_rows = command->height > 0 ? command->height / row_height + 1 : 100;
    int first_line = command->first_line > 0 ? command->first_line : 0;
    const char* start = command->text;
    Zltk_Font* font = zltk_system_font_resource(1);
    float spacing = 0.0f;
    if (font) {
        Zltk_Vector2 measured = g_rl.MeasureTextEx(*font, "M", (float)font_size, 0.0f);
        spacing = (float)glyph_width - measured.x;
        if (spacing < -2.0f) spacing = -2.0f;
        if (spacing > 4.0f) spacing = 4.0f;
    }
    for (int skipped = 0; skipped < first_line && *start; skipped++) {
        const char* end = strchr(start, '\n');
        start = end ? end + 1 : start + strlen(start);
    }
    for (int row = 0; row < max_rows; row++) {
        if (row > 0 && !*start) break;
        const char* end = strchr(start, '\n');
        size_t length = end ? (size_t)(end - start) : strlen(start);
        while (length > 0 && start[length - 1] == '\r') length--;
        char* line = zltk_expand_tabs(start, length);
        if (!line) return;
        char number[24];
        snprintf(number, sizeof(number), "%5d", first_line + row + 1);
        int row_y = command->y + row * row_height + 2;
        int source_line = first_line + row;
        if (
            command->selection_start_line >= 0 &&
            source_line >= command->selection_start_line &&
            source_line <= command->selection_end_line
        ) {
            int left = source_line == command->selection_start_line ? command->selection_start_column : 0;
            int right = source_line == command->selection_end_line
                ? command->selection_end_column
                : zltk_utf8_columns(line, strlen(line));
            if (right <= left) right = left + 1;
            g_rl.DrawRectangle(
                command->x + 62 + left * glyph_width,
                command->y + row * row_height,
                (right - left) * glyph_width,
                row_height,
                zltk_rgba(217, 119, 87, 72)
            );
        }
        if (font) {
            Zltk_Vector2 position = { (float)command->x, (float)row_y };
            g_rl.DrawTextEx(*font, number, position, (float)font_size, spacing, zltk_rgba(112, 118, 130, 255));
        } else {
            g_rl.DrawText(number, command->x, row_y, font_size, zltk_rgba(112, 118, 130, 255));
        }
        zltk_draw_highlighted_line(line, command->x + 62, row_y, glyph_width, font_size, spacing, font);
        free(line);
        if (!end) break;
        start = end + 1;
    }
}

static void zltk_draw_completion(const Zltk_Command* command) {
    if (!command || !command->text || !command->text[0]) return;
    int row_height = command->line_height > 0 ? command->line_height : 24;
    int font_size = command->size > 0 ? command->size : 16;
    int rows = 1;
    for (const char* cursor = command->text; *cursor; cursor++) {
        if (*cursor == '\n') rows++;
    }
    int height = rows * row_height + 8;
    g_rl.DrawRectangle(command->x, command->y, command->width, height, zltk_rgba(42, 40, 39, 255));
    Zltk_Rectangle border = { (float)command->x, (float)command->y, (float)command->width, (float)height };
    g_rl.DrawRectangleLinesEx(border, 1.0f, zltk_rgba(217, 119, 87, 220));
    const char* start = command->text;
    for (int row = 0; row < rows; row++) {
        const char* end = strchr(start, '\n');
        size_t length = end ? (size_t)(end - start) : strlen(start);
        char item[128];
        size_t copy = length < sizeof(item) - 1 ? length : sizeof(item) - 1;
        memcpy(item, start, copy);
        item[copy] = 0;
        int item_y = command->y + 4 + row * row_height;
        if (row == command->stamp) {
            g_rl.DrawRectangle(command->x + 1, item_y, command->width - 2, row_height, zltk_rgba(217, 119, 87, 54));
            g_rl.DrawRectangle(command->x + 1, item_y, 3, row_height, zltk_rgba(217, 119, 87, 255));
        }
        zltk_draw_system_text(command->x + 12, item_y + 3, item, row == command->stamp ? "#f3e8e3" : "#c9c4c1", font_size);
        if (!end) break;
        start = end + 1;
    }
}

static void zltk_draw_codeview_file(char** columns, int count) {
    if (count < 11) return;
    FILE* file = fopen(columns[10], "rb");
    if (!file) return;
    int x = zltk_to_int(columns[1], 0);
    int y = zltk_to_int(columns[2], 0);
    int height = zltk_to_int(columns[4], 300);
    int line_height = zltk_to_int(columns[6], 20);
    int size = zltk_to_int(columns[8], 16);
    int max_rows = line_height > 0 ? height / line_height + 1 : 100;
    char line[4096];
    for (int row = 0; row < max_rows && fgets(line, sizeof(line), file); row++) {
        line[strcspn(line, "\r\n")] = 0;
        g_rl.DrawText(line, x, y + row * line_height + 2, size, zltk_rgba(235, 233, 224, 255));
    }
    fclose(file);
}

static void zltk_draw_raw_command(char* line) {
    if (!line || !line[0] || line[0] == '#') return;
    char* columns[16];
    int count = zltk_split_tabs(line, columns, 16);
    if (count <= 0) return;
    const char* operation = columns[0];
    Zltk_Color white = zltk_rgba(255, 255, 255, 255);
    if ((strcmp(operation, "bg") == 0 || strcmp(operation, "clear") == 0) && count >= 2) {
        g_rl.ClearBackground(zltk_color(columns[1], zltk_rgba(32, 33, 36, 255)));
    } else if (strcmp(operation, "line") == 0 && count >= 7) {
        Zltk_Vector2 start = { (float)zltk_to_int(columns[1], 0), (float)zltk_to_int(columns[2], 0) };
        Zltk_Vector2 end = { (float)zltk_to_int(columns[3], 0), (float)zltk_to_int(columns[4], 0) };
        g_rl.DrawLineEx(start, end, (float)zltk_to_int(columns[6], 1), zltk_color(columns[5], white));
    } else if (strcmp(operation, "rect") == 0 && count >= 6) {
        g_rl.DrawRectangle(zltk_to_int(columns[1], 0), zltk_to_int(columns[2], 0), zltk_to_int(columns[3], 0), zltk_to_int(columns[4], 0), zltk_color(columns[5], white));
    } else if (strcmp(operation, "rect_outline") == 0 && count >= 7) {
        Zltk_Rectangle rect = { (float)zltk_to_int(columns[1], 0), (float)zltk_to_int(columns[2], 0), (float)zltk_to_int(columns[3], 0), (float)zltk_to_int(columns[4], 0) };
        g_rl.DrawRectangleLinesEx(rect, (float)zltk_to_int(columns[6], 1), zltk_color(columns[5], white));
    } else if (strcmp(operation, "circle") == 0 && count >= 5) {
        g_rl.DrawCircle(zltk_to_int(columns[1], 0), zltk_to_int(columns[2], 0), (float)zltk_to_int(columns[3], 0), zltk_color(columns[4], white));
    } else if (strcmp(operation, "circle_outline") == 0 && count >= 6) {
        int width = zltk_to_int(columns[5], 1);
        float radius = (float)zltk_to_int(columns[3], 0);
        for (int i = 0; i < width; i++) g_rl.DrawCircleLines(zltk_to_int(columns[1], 0), zltk_to_int(columns[2], 0), radius - (float)i, zltk_color(columns[4], white));
    } else if (strcmp(operation, "text") == 0 && count >= 6) {
        zltk_draw_system_text(zltk_to_int(columns[1], 0), zltk_to_int(columns[2], 0), columns[3], columns[4], zltk_to_int(columns[5], 16));
    } else if (strcmp(operation, "font_text") == 0 && count >= 8) {
        zltk_draw_font_text(columns[1], zltk_to_int(columns[2], 32), zltk_to_int(columns[3], 0), zltk_to_int(columns[4], 0), columns[5], columns[6], zltk_to_int(columns[7], 16));
    } else if (strcmp(operation, "codeview") == 0) {
        zltk_draw_codeview_file(columns, count);
    } else if (strcmp(operation, "image") == 0 && count >= 4) {
        Zltk_Texture* texture = zltk_image_texture(columns[1]);
        if (texture) g_rl.DrawTexture(*texture, zltk_to_int(columns[2], 0), zltk_to_int(columns[3], 0), white);
    }
}

static void zltk_render_frame(const Zltk_Frame* frame) {
    g_rl.BeginDrawing();
    g_rl.ClearBackground(zltk_rgba(32, 32, 31, 255));
    if (frame) {
        for (int i = 0; i < frame->count; i++) {
            const Zltk_Command* command = &frame->items[i];
            if (command->kind == ZLTK_COMMAND_CODEVIEW_TEXT) {
                zltk_draw_code_lines(command);
            } else if (command->kind == ZLTK_COMMAND_COMPLETION) {
                zltk_draw_completion(command);
            } else if (command->kind == ZLTK_COMMAND_RAW && command->raw) {
                char line[4096];
                strncpy(line, command->raw, sizeof(line) - 1);
                line[sizeof(line) - 1] = 0;
                zltk_draw_raw_command(line);
            }
        }
    }
    g_rl.EndDrawing();
}

static void zltk_render_scene_file(void) {
    g_rl.BeginDrawing();
    g_rl.ClearBackground(zltk_rgba(32, 32, 31, 255));
    FILE* file = fopen(g_zltk.script_path, "rb");
    if (file) {
        char line[4096];
        while (fgets(line, sizeof(line), file)) {
            line[strcspn(line, "\r\n")] = 0;
            zltk_draw_raw_command(line);
        }
        fclose(file);
    }
    g_rl.EndDrawing();
}

static const char* zltk_key_name(int key, int ctrl) {
    static char buffer[32];
    if (ctrl && key >= 'A' && key <= 'Z') {
        buffer[0] = (char)(key - 'A' + 'a');
        buffer[1] = 0;
        return buffer;
    }
    if (key >= '0' && key <= '9') {
        buffer[0] = (char)key;
        buffer[1] = 0;
        return buffer;
    }
    switch (key) {
        case ZLTK_KEY_LEFT: return "Left";
        case ZLTK_KEY_RIGHT: return "Right";
        case ZLTK_KEY_UP: return "Up";
        case ZLTK_KEY_DOWN: return "Down";
        case ZLTK_KEY_HOME: return "Home";
        case ZLTK_KEY_END: return "End";
        case ZLTK_KEY_PAGE_UP: return "Prior";
        case ZLTK_KEY_PAGE_DOWN: return "Next";
        case ZLTK_KEY_ESCAPE: return "Escape";
        case ZLTK_KEY_ENTER: return "Return";
        case ZLTK_KEY_BACKSPACE: return "BackSpace";
        case ZLTK_KEY_DELETE: return "Delete";
        case ZLTK_KEY_TAB: return "Tab";
        case '=': return "equal";
        case '-': return "minus";
        default: break;
    }
    snprintf(buffer, sizeof(buffer), "%d", key);
    return buffer;
}

static void zltk_utf8(char out[8], int codepoint) {
    memset(out, 0, 8);
    if (codepoint <= 0x7f) {
        out[0] = (char)codepoint;
    } else if (codepoint <= 0x7ff) {
        out[0] = (char)(0xc0 | (codepoint >> 6));
        out[1] = (char)(0x80 | (codepoint & 0x3f));
    } else if (codepoint <= 0xffff) {
        out[0] = (char)(0xe0 | (codepoint >> 12));
        out[1] = (char)(0x80 | ((codepoint >> 6) & 0x3f));
        out[2] = (char)(0x80 | (codepoint & 0x3f));
    } else {
        out[0] = (char)(0xf0 | (codepoint >> 18));
        out[1] = (char)(0x80 | ((codepoint >> 12) & 0x3f));
        out[2] = (char)(0x80 | ((codepoint >> 6) & 0x3f));
        out[3] = (char)(0x80 | (codepoint & 0x3f));
    }
}

static int zltk_modifier_down(int left, int right) {
    return g_rl.IsKeyDown(left) || g_rl.IsKeyDown(right);
}

static int zltk_repeatable_key(int key) {
    return key == ZLTK_KEY_LEFT || key == ZLTK_KEY_RIGHT || key == ZLTK_KEY_UP || key == ZLTK_KEY_DOWN ||
        key == ZLTK_KEY_HOME || key == ZLTK_KEY_END || key == ZLTK_KEY_PAGE_UP || key == ZLTK_KEY_PAGE_DOWN ||
        key == ZLTK_KEY_BACKSPACE || key == ZLTK_KEY_DELETE;
}

static void zltk_append_key_event(int key, int ctrl, int shift) {
    const char* name = zltk_key_name(key, ctrl);
    char event[96];
    if (ctrl) snprintf(event, sizeof(event), "ctrl\t%s", name);
    else if (shift && (key >= ZLTK_KEY_RIGHT && key <= ZLTK_KEY_END)) snprintf(event, sizeof(event), "shift\t%s", name);
    else snprintf(event, sizeof(event), "key\t%s", name);
    zltk_append_event(event);
}

static void zltk_collect_events(void) {
    if (!g_zltk.is_session || !g_zltk.window_open) return;
    if (g_rl.WindowShouldClose()) {
        if (!g_zltk.quit_sent) zltk_append_event("quit");
        g_zltk.quit_sent = 1;
        g_zltk.closed = 1;
    }

    int ctrl = zltk_modifier_down(ZLTK_KEY_LEFT_CONTROL, ZLTK_KEY_RIGHT_CONTROL);
    int shift = zltk_modifier_down(ZLTK_KEY_LEFT_SHIFT, ZLTK_KEY_RIGHT_SHIFT);
    int key = 0;
    int pressed_repeat_key = 0;
    while ((key = g_rl.GetKeyPressed()) != 0) {
        if (key == ZLTK_KEY_LEFT_CONTROL || key == ZLTK_KEY_RIGHT_CONTROL || key == ZLTK_KEY_LEFT_SHIFT || key == ZLTK_KEY_RIGHT_SHIFT || key == ZLTK_KEY_LEFT_ALT || key == ZLTK_KEY_RIGHT_ALT) continue;
        zltk_append_key_event(key, ctrl, shift);
        if (!ctrl && zltk_repeatable_key(key)) {
            double now = g_rl.GetTime();
            g_zltk.repeat_key = key;
            g_zltk.repeat_started = now;
            g_zltk.repeat_last = now;
            pressed_repeat_key = key;
        }
    }
    if (g_zltk.repeat_key != 0) {
        if (!g_rl.IsKeyDown(g_zltk.repeat_key)) {
            g_zltk.repeat_key = 0;
        } else if (pressed_repeat_key == 0) {
            double now = g_rl.GetTime();
            if (now - g_zltk.repeat_started >= 0.32 && now - g_zltk.repeat_last >= 0.035) {
                zltk_append_key_event(g_zltk.repeat_key, 0, shift);
                g_zltk.repeat_last = now;
            }
        }
    }

    int codepoint = 0;
    while ((codepoint = g_rl.GetCharPressed()) != 0) {
        if (ctrl) continue;
        if (codepoint < 32 || codepoint == 127) continue;
        char utf8[8];
        char event[32];
        zltk_utf8(utf8, codepoint);
        snprintf(event, sizeof(event), "keychar\t%s", utf8);
        zltk_append_event(event);
    }

    Zltk_Vector2 mouse = g_rl.GetMousePosition();
    char event[96];
    if (g_rl.IsMouseButtonPressed(ZLTK_MOUSE_LEFT)) {
        snprintf(event, sizeof(event), "%s\t%d\t%d", ctrl ? "ctrl_mouse" : "mouse", (int)mouse.x, (int)mouse.y);
        zltk_append_event(event);
    }
    if (g_rl.IsMouseButtonReleased(ZLTK_MOUSE_LEFT)) {
        snprintf(event, sizeof(event), "release\t%d\t%d", (int)mouse.x, (int)mouse.y);
        zltk_append_event(event);
    }
    if ((int)mouse.x != (int)g_zltk.last_mouse.x || (int)mouse.y != (int)g_zltk.last_mouse.y) {
        double now = g_rl.GetTime();
        if (g_rl.IsMouseButtonDown(ZLTK_MOUSE_LEFT)) {
            if (now - g_zltk.last_drag_time >= 0.03) {
                snprintf(event, sizeof(event), "drag\t%d\t%d", (int)mouse.x, (int)mouse.y);
                zltk_append_event(event);
                g_zltk.last_drag_time = now;
            }
        } else if (now - g_zltk.last_motion_time >= 0.08) {
            snprintf(event, sizeof(event), "motion\t%d\t%d", (int)mouse.x, (int)mouse.y);
            zltk_append_event(event);
            g_zltk.last_motion_time = now;
        }
        g_zltk.last_mouse = mouse;
    }
    Zltk_Vector2 wheel = g_rl.GetMouseWheelMoveV();
    if (wheel.y != 0.0f) {
        int notches = wheel.y > 0.0f ? 1 : -1;
        snprintf(event, sizeof(event), "wheel\t%d\t%d\t%d", notches, (int)mouse.x, (int)mouse.y);
        zltk_append_event(event);
    }
}

static int zltk_open_window(const char* title, int width, int height) {
    if (zltk_load_raylib() != 0) return -2;
    if (g_zltk.window_open) return -1;
    g_rl.SetTraceLogLevel(4);
    g_rl.SetConfigFlags(ZLTK_FLAG_WINDOW_RESIZABLE);
    g_rl.InitWindow(width > 0 ? width : 800, height > 0 ? height : 600, title ? title : "ZyenLang GUI");
    if (!g_rl.IsWindowReady()) return -3;
    g_rl.SetExitKey(0);
    g_rl.SetTargetFPS(0);
    g_zltk.window_open = 1;
    g_zltk.closed = 0;
    g_zltk.quit_sent = 0;
    g_zltk.last_mouse = g_rl.GetMousePosition();
    return 0;
}

static void zltk_close_window(void) {
    if (!g_zltk.window_open) return;
    zltk_images_clear();
    zltk_fonts_clear();
    g_rl.CloseWindow();
    g_zltk.window_open = 0;
    g_zltk.closed = 1;
}

static void zltk_preset_from_scene(const char* path, char* title, size_t title_capacity, int* width, int* height) {
    FILE* file = fopen(path, "rb");
    if (!file) return;
    char line[4096];
    while (fgets(line, sizeof(line), file)) {
        line[strcspn(line, "\r\n")] = 0;
        char* columns[8];
        int count = zltk_split_tabs(line, columns, 8);
        if (count >= 4 && strcmp(columns[0], "window") == 0) {
            zltk_clean_copy(title, title_capacity, columns[1]);
            *width = zltk_to_int(columns[2], *width);
            *height = zltk_to_int(columns[3], *height);
        }
    }
    fclose(file);
}

static int zltk_append_raw(const char* line) {
    if (g_zltk.is_session) {
        if (!g_zltk.frame_active) return -1;
        Zltk_Command* command = zltk_frame_push(&g_zltk.building_frame);
        if (!command) return -1;
        command->kind = ZLTK_COMMAND_RAW;
        command->raw = zltk_strdup(line);
        if (!command->raw) {
            g_zltk.building_frame.count--;
            return -1;
        }
        return 0;
    }
    FILE* file = fopen(g_zltk.script_path, "ab");
    if (!file) return -1;
    fputs(line ? line : "", file);
    fputc('\n', file);
    fclose(file);
    return 0;
}

int zl_tk_begin(const char* path) {
    if (path && path[0]) {
        strncpy(g_zltk.script_path, path, sizeof(g_zltk.script_path) - 1);
        g_zltk.script_path[sizeof(g_zltk.script_path) - 1] = 0;
    }
    FILE* file = fopen(g_zltk.script_path, "wb");
    if (!file) return -1;
    fputs("# ZyenLang GUI scene\n", file);
    fclose(file);
    return 0;
}

int zl_tk_window(const char* title, int width, int height) {
    char clean[1024];
    char line[1400];
    zltk_clean_copy(clean, sizeof(clean), title);
    snprintf(line, sizeof(line), "window\t%s\t%d\t%d", clean, width, height);
    return zltk_append_raw(line);
}

int zl_tk_open(const char* title, int width, int height) {
    int result = zl_tk_begin("zyen_tk_scene.ztk");
    return result == 0 ? zl_tk_window(title, width, height) : result;
}

int zl_tk_bg(const char* color) {
    char clean[256];
    char line[512];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "bg\t%s", clean);
    return zltk_append_raw(line);
}

int zl_tk_clear(const char* color) {
    char clean[256];
    char line[512];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "clear\t%s", clean);
    return zltk_append_raw(line);
}

int zl_tk_line(int x1, int y1, int x2, int y2, const char* color, int width) {
    char clean[256];
    char line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "line\t%d\t%d\t%d\t%d\t%s\t%d", x1, y1, x2, y2, clean, width);
    return zltk_append_raw(line);
}

int zl_tk_rect(int x, int y, int width, int height, const char* color) {
    char clean[256];
    char line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "rect\t%d\t%d\t%d\t%d\t%s", x, y, width, height, clean);
    return zltk_append_raw(line);
}

int zl_tk_rect_outline(int x, int y, int width, int height, const char* color, int line_width) {
    char clean[256];
    char line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "rect_outline\t%d\t%d\t%d\t%d\t%s\t%d", x, y, width, height, clean, line_width);
    return zltk_append_raw(line);
}

int zl_tk_circle(int x, int y, int radius, const char* color) {
    char clean[256];
    char line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "circle\t%d\t%d\t%d\t%s", x, y, radius, clean);
    return zltk_append_raw(line);
}

int zl_tk_circle_outline(int x, int y, int radius, const char* color, int line_width) {
    char clean[256];
    char line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "circle_outline\t%d\t%d\t%d\t%s\t%d", x, y, radius, clean, line_width);
    return zltk_append_raw(line);
}

int zl_tk_text(int x, int y, const char* text, const char* color, int size) {
    char clean_text[1024];
    char clean_color[256];
    char line[1800];
    zltk_clean_copy(clean_text, sizeof(clean_text), text);
    zltk_clean_copy(clean_color, sizeof(clean_color), color);
    snprintf(line, sizeof(line), "text\t%d\t%d\t%s\t%s\t%d", x, y, clean_text, clean_color, size);
    return zltk_append_raw(line);
}

int zl_tk_font_text(const char* font_path, int base_size, int x, int y, const char* text, const char* color, int size) {
    char clean_path[ZLTK_PATH_CAPACITY];
    char clean_text[1024];
    char clean_color[256];
    char line[ZLTK_PATH_CAPACITY + 1600];
    zltk_clean_copy(clean_path, sizeof(clean_path), font_path);
    zltk_clean_copy(clean_text, sizeof(clean_text), text);
    zltk_clean_copy(clean_color, sizeof(clean_color), color);
    snprintf(line, sizeof(line), "font_text\t%s\t%d\t%d\t%d\t%s\t%s\t%d", clean_path, base_size, x, y, clean_text, clean_color, size);
    return zltk_append_raw(line);
}

int zl_tk_font_text_width(const char* font_path, int base_size, const char* text, int size) {
    const char* value = text ? text : "";
    int font_size = size > 0 ? size : 16;
    if (!g_zltk.window_open) return (int)strlen(value) * font_size / 2;
    Zltk_Font* font = zltk_font_resource(font_path, base_size);
    if (!font) return g_rl.MeasureText(value, font_size);
    Zltk_Vector2 measured = g_rl.MeasureTextEx(*font, value, (float)font_size, 0.0f);
    return (int)(measured.x + 0.5f);
}

int zl_tk_codeview(int x, int y, int width, int height, int first_line, int line_height, int char_width, int size, int stamp, const char* lines_path) {
    char clean[1024];
    char line[2200];
    zltk_clean_copy(clean, sizeof(clean), lines_path);
    snprintf(line, sizeof(line), "codeview\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s", x, y, width, height, first_line, line_height, char_width, size, stamp, clean);
    return zltk_append_raw(line);
}

int zl_tk_codeview_text(int x, int y, int width, int height, int first_line, int line_height, int char_width, int size, int stamp, const char* lines) {
    return zl_tk_codeview_editor_text(x, y, width, height, first_line, line_height, char_width, size, stamp, lines, -1, 0, -1, 0);
}

int zl_tk_codeview_editor_text(
    int x,
    int y,
    int width,
    int height,
    int first_line,
    int line_height,
    int char_width,
    int size,
    int stamp,
    const char* lines,
    int selection_start_line,
    int selection_start_column,
    int selection_end_line,
    int selection_end_column
) {
    if (!g_zltk.is_session || !g_zltk.frame_active) return -1;
    Zltk_Command* command = zltk_frame_push(&g_zltk.building_frame);
    if (!command) return -1;
    command->kind = ZLTK_COMMAND_CODEVIEW_TEXT;
    command->text = zltk_strdup(lines);
    command->x = x;
    command->y = y;
    command->width = width;
    command->height = height;
    command->first_line = first_line;
    command->line_height = line_height;
    command->char_width = char_width;
    command->size = size;
    command->stamp = stamp;
    command->selection_start_line = selection_start_line;
    command->selection_start_column = selection_start_column;
    command->selection_end_line = selection_end_line;
    command->selection_end_column = selection_end_column;
    if (!command->text) {
        g_zltk.building_frame.count--;
        return -1;
    }
    return 0;
}

int zl_tk_completion(int x, int y, int width, int row_height, int size, int selected, const char* items) {
    if (!g_zltk.is_session || !g_zltk.frame_active || !items || !items[0]) return -1;
    Zltk_Command* command = zltk_frame_push(&g_zltk.building_frame);
    if (!command) return -1;
    command->kind = ZLTK_COMMAND_COMPLETION;
    command->text = zltk_strdup(items);
    command->x = x;
    command->y = y;
    command->width = width;
    command->line_height = row_height;
    command->size = size;
    command->stamp = selected;
    if (!command->text) {
        g_zltk.building_frame.count--;
        return -1;
    }
    return 0;
}

int zl_tk_image(const char* path, int x, int y) {
    char clean[ZLTK_PATH_CAPACITY];
    char line[ZLTK_PATH_CAPACITY + 64];
    zltk_clean_copy(clean, sizeof(clean), path);
    snprintf(line, sizeof(line), "image\t%s\t%d\t%d", clean, x, y);
    return zltk_append_raw(line);
}

const char* zl_tk_script(void) {
    return g_zltk.script_path;
}

int zl_tk_show(void) {
    return zl_tk_show_for(0);
}

int zl_tk_show_for(int ms) {
    char title[256] = "ZyenLang GUI";
    int width = 800;
    int height = 600;
    zltk_preset_from_scene(g_zltk.script_path, title, sizeof(title), &width, &height);
    g_zltk.is_session = 0;
    int result = zltk_open_window(title, width, height);
    if (result != 0) return result;
    double start = g_rl.GetTime();
    while (!g_rl.WindowShouldClose()) {
        zltk_render_scene_file();
        if (ms > 0 && (g_rl.GetTime() - start) * 1000.0 >= (double)ms) break;
    }
    zltk_close_window();
    return 0;
}

int zl_tk_session_open(const char* title, int width, int height, const char* session_dir) {
    (void)session_dir;
    if (g_zltk.window_open) return -1;
    zltk_frame_clear(&g_zltk.building_frame);
    zltk_frame_clear(&g_zltk.display_frame);
    g_zltk.frame_active = 0;
    g_zltk.event_head = 0;
    g_zltk.event_count = 0;
    g_zltk.is_session = 1;
    int result = zltk_open_window(title, width, height);
    if (result != 0) {
        g_zltk.is_session = 0;
        return result;
    }
    zltk_append_event("ready");
    return 0;
}

int zl_tk_session_begin_frame(void) {
    if (!g_zltk.is_session || !g_zltk.window_open) return -1;
    zltk_frame_clear(&g_zltk.building_frame);
    g_zltk.frame_active = 1;
    return 0;
}

int zl_tk_session_redraw(void) {
    if (!g_zltk.is_session || !g_zltk.window_open) return -1;
    Zltk_Frame old_display = g_zltk.display_frame;
    g_zltk.display_frame = g_zltk.building_frame;
    g_zltk.building_frame = old_display;
    g_zltk.frame_active = 0;
    zltk_render_frame(&g_zltk.display_frame);
    zltk_collect_events();
    return g_zltk.closed ? -2 : 0;
}

int zl_tk_session_set_fps(int fps) {
    if (!g_zltk.is_session || !g_zltk.window_open) return -1;
    g_rl.SetTargetFPS(fps > 0 ? fps : 0);
    return 0;
}

const char* zl_tk_session_next_event(int timeout_ms) {
    static char buffers[16][512];
    static int index = 0;
    char* out = buffers[index++ & 15];
    out[0] = 0;
    if (!g_zltk.is_session || !g_zltk.window_open) return out;
    if (zltk_pop_event(out, 512)) return out;
    double start = g_rl.GetTime();
    do {
        g_rl.PollInputEvents();
        zltk_collect_events();
        if (zltk_pop_event(out, 512)) return out;
        if (timeout_ms <= 0) break;
        g_rl.WaitTime(0.005);
    } while ((g_rl.GetTime() - start) * 1000.0 < (double)timeout_ms);
    return out;
}

int zl_tk_session_pickdir(void) {
    if (!g_zltk.is_session || !g_zltk.window_open) return -1;
#ifdef _WIN32
    const char* command = "powershell.exe -NoProfile -STA -Command \"Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; if($d.ShowDialog() -eq 'OK'){[Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Output $d.SelectedPath}\"";
#elif defined(__APPLE__)
    const char* command = "osascript -e 'POSIX path of (choose folder)' 2>/dev/null";
#else
    const char* command = "sh -c 'if command -v zenity >/dev/null 2>&1; then zenity --file-selection --directory; elif command -v kdialog >/dev/null 2>&1; then kdialog --getexistingdirectory .; fi'";
#endif
    FILE* pipe = zltk_popen(command, "r");
    char path[ZLTK_PATH_CAPACITY] = "";
    if (pipe) {
        if (!fgets(path, sizeof(path), pipe)) path[0] = 0;
        zltk_pclose(pipe);
    }
    path[strcspn(path, "\r\n")] = 0;
    char event[ZLTK_PATH_CAPACITY + 16];
    snprintf(event, sizeof(event), "pickdir\t%s", path);
    zltk_append_event(event);
    return 0;
}

int zl_tk_session_pickfile(void) {
    if (!g_zltk.is_session || !g_zltk.window_open) return -1;
    char path[ZLTK_PATH_CAPACITY] = "";
#ifdef _WIN32
    wchar_t wide_path[ZLTK_PATH_CAPACITY] = L"";
    OPENFILENAMEW dialog;
    memset(&dialog, 0, sizeof(dialog));
    dialog.lStructSize = sizeof(dialog);
    dialog.lpstrFile = wide_path;
    dialog.nMaxFile = ZLTK_PATH_CAPACITY;
    dialog.lpstrFilter = L"ZyenLang Files (*.zy)\0*.zy\0All Files (*.*)\0*.*\0";
    dialog.nFilterIndex = 1;
    dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_EXPLORER;
    if (!GetOpenFileNameW(&dialog)) return CommDlgExtendedError() == 0 ? 0 : -2;
    if (WideCharToMultiByte(CP_UTF8, 0, wide_path, -1, path, ZLTK_PATH_CAPACITY, NULL, NULL) <= 0) return -2;
#elif defined(__APPLE__)
    FILE* pipe = zltk_popen("osascript -e 'POSIX path of (choose file)' 2>/dev/null", "r");
    if (pipe) {
        if (!fgets(path, sizeof(path), pipe)) path[0] = 0;
        zltk_pclose(pipe);
    }
#else
    FILE* pipe = zltk_popen("sh -c 'if command -v zenity >/dev/null 2>&1; then zenity --file-selection --file-filter=\"ZyenLang | *.zy\"; elif command -v kdialog >/dev/null 2>&1; then kdialog --getopenfilename . \"*.zy\"; fi'", "r");
    if (pipe) {
        if (!fgets(path, sizeof(path), pipe)) path[0] = 0;
        zltk_pclose(pipe);
    }
#endif
    path[strcspn(path, "\r\n")] = 0;
    if (!path[0]) return 0;
    char event[ZLTK_PATH_CAPACITY + 16];
    snprintf(event, sizeof(event), "pickfile\t%s", path);
    zltk_append_event(event);
    return 0;
}

int zl_tk_session_close(void) {
    if (!g_zltk.is_session) return 0;
    zltk_close_window();
    zltk_frame_clear(&g_zltk.building_frame);
    zltk_frame_clear(&g_zltk.display_frame);
    g_zltk.frame_active = 0;
    g_zltk.is_session = 0;
    return 0;
}

int zl_tk_session_char_w(void) {
    if (g_zltk.window_open && g_rl.MeasureText) {
        int width = g_rl.MeasureText("M", 16);
        if (width > 0) return width;
    }
    return 9;
}

int zl_tk_session_line_h(void) {
    return 20;
}

int zl_tk_event_kind(const char* event) {
    if (!event) return 0;
    if (strncmp(event, "mouse\t", 6) == 0 || strncmp(event, "ctrl_mouse\t", 11) == 0) return 1;
    if (strncmp(event, "release\t", 8) == 0) return 2;
    if (strncmp(event, "motion\t", 7) == 0) return 3;
    if (strncmp(event, "drag\t", 5) == 0) return 4;
    if (strncmp(event, "wheel\t", 6) == 0) return 5;
    return 0;
}

static void zltk_event_coordinates(const char* event, int* x, int* y) {
    *x = 0;
    *y = 0;
    int kind = zl_tk_event_kind(event);
    if (kind == 0 || !event) return;
    const char* payload = strchr(event, '\t');
    if (!payload) return;
    payload++;
    if (kind == 5) {
        int wheel = 0;
        (void)sscanf(payload, "%d\t%d\t%d", &wheel, x, y);
    } else {
        (void)sscanf(payload, "%d\t%d", x, y);
    }
}

int zl_tk_event_x(const char* event) {
    int x = 0;
    int y = 0;
    zltk_event_coordinates(event, &x, &y);
    return x;
}

int zl_tk_event_y(const char* event) {
    int x = 0;
    int y = 0;
    zltk_event_coordinates(event, &x, &y);
    return y;
}
