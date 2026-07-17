#include "tk_native.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <windowsx.h>
#include <shlobj.h>

enum {
    ZLTK_COMMAND_RAW = 1,
    ZLTK_COMMAND_CODEVIEW_TEXT = 2,
    ZLTK_EVENT_CAPACITY = 2048,
    ZLTK_FONT_CACHE_CAPACITY = 8
};

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
} Zltk_Command;

typedef struct {
    Zltk_Command* items;
    int count;
    int capacity;
} Zltk_Frame;

typedef struct {
    char script_path[MAX_PATH];
    char title[256];
    HWND hwnd;
    HANDLE thread;
    HANDLE event_signal;
    int width;
    int height;
    int show_for_ms;
    int is_session;
    int scene_version;
    int frame_active;
    Zltk_Frame building_frame;
    Zltk_Frame display_frame;
    char events[ZLTK_EVENT_CAPACITY][512];
    int event_head;
    int event_count;
    DWORD last_motion_ms;
    DWORD last_drag_ms;
    HDC back_dc;
    HBITMAP back_bmp;
    HBITMAP back_old;
    int back_width;
    int back_height;
    CRITICAL_SECTION lock;
    int lock_ready;
    int ready;
    int closed;
} Zltk_App;

static Zltk_App g_zltk = { .script_path = "zyen_tk_scene.ztk", .width = 800, .height = 600 };
static HFONT g_zltk_fonts[ZLTK_FONT_CACHE_CAPACITY];
static int g_zltk_font_sizes[ZLTK_FONT_CACHE_CAPACITY];
static int g_zltk_font_count = 0;

static void zltk_lock(void) {
    if (!g_zltk.lock_ready) {
        InitializeCriticalSection(&g_zltk.lock);
        g_zltk.lock_ready = 1;
    }
    EnterCriticalSection(&g_zltk.lock);
}

static void zltk_unlock(void) {
    LeaveCriticalSection(&g_zltk.lock);
}

static char* zltk_strdup(const char* text) {
    size_t size = strlen(text ? text : "") + 1;
    char* copy = (char*)malloc(size);
    if (copy) memcpy(copy, text ? text : "", size);
    return copy;
}

static void zltk_command_clear(Zltk_Command* command) {
    if (!command) return;
    free(command->raw);
    free(command->text);
    ZeroMemory(command, sizeof(*command));
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
    ZeroMemory(command, sizeof(*command));
    return command;
}

static void zltk_clean_copy(char* out, size_t cap, const char* s) {
    size_t j = 0;
    if (cap == 0) return;
    if (!s) s = "";
    for (size_t i = 0; s[i] && j + 1 < cap; i++) {
        char ch = s[i];
        if (ch == '\n' || ch == '\r' || ch == '\t') ch = ' ';
        out[j++] = ch;
    }
    out[j] = 0;
}

static int zltk_append_raw(const char* line) {
    if (g_zltk.is_session) {
        int result = 0;
        zltk_lock();
        if (!g_zltk.frame_active) {
            result = -1;
        } else {
            Zltk_Command* command = zltk_frame_push(&g_zltk.building_frame);
            if (!command) {
                result = -1;
            } else {
                command->kind = ZLTK_COMMAND_RAW;
                command->raw = zltk_strdup(line ? line : "");
                if (!command->raw) {
                    g_zltk.building_frame.count--;
                    result = -1;
                }
            }
        }
        zltk_unlock();
        return result;
    }
    FILE* f = fopen(g_zltk.script_path, "ab");
    if (!f) return -1;
    fputs(line ? line : "", f);
    fputc('\n', f);
    fclose(f);
    return 0;
}

static int zltk_to_int(const char* s, int defv) {
    if (!s || !s[0]) return defv;
    return atoi(s);
}

static COLORREF zltk_color(const char* s, COLORREF fallback) {
    if (!s) return fallback;
    if (s[0] == '#' && strlen(s) >= 7) {
        char r[3] = { s[1], s[2], 0 };
        char g[3] = { s[3], s[4], 0 };
        char b[3] = { s[5], s[6], 0 };
        return RGB((int)strtol(r, NULL, 16), (int)strtol(g, NULL, 16), (int)strtol(b, NULL, 16));
    }
    if (strcmp(s, "white") == 0) return RGB(255, 255, 255);
    if (strcmp(s, "black") == 0) return RGB(0, 0, 0);
    if (strcmp(s, "red") == 0) return RGB(220, 40, 40);
    if (strcmp(s, "green") == 0) return RGB(40, 180, 80);
    if (strcmp(s, "blue") == 0) return RGB(60, 130, 240);
    return fallback;
}

static wchar_t* zltk_wide(const char* s) {
    if (!s) s = "";
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    if (n <= 0) n = MultiByteToWideChar(CP_ACP, 0, s, -1, NULL, 0);
    if (n <= 0) return NULL;
    wchar_t* w = (wchar_t*)malloc(sizeof(wchar_t) * (size_t)n);
    if (!w) return NULL;
    if (!MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n)) {
        MultiByteToWideChar(CP_ACP, 0, s, -1, w, n);
    }
    return w;
}

static HFONT zltk_font_for_size(int size) {
    int px = size == 0 ? 16 : size;
    for (int i = 0; i < g_zltk_font_count; i++) {
        if (g_zltk_font_sizes[i] == px) return g_zltk_fonts[i];
    }
    HFONT font = CreateFontW(-abs(px), 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                             OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                             FIXED_PITCH | FF_MODERN, L"Cascadia Mono");
    if (!font) return NULL;
    if (g_zltk_font_count < ZLTK_FONT_CACHE_CAPACITY) {
        g_zltk_font_sizes[g_zltk_font_count] = px;
        g_zltk_fonts[g_zltk_font_count] = font;
        g_zltk_font_count++;
    }
    return font;
}

static void zltk_release_fonts(void) {
    for (int i = 0; i < g_zltk_font_count; i++) DeleteObject(g_zltk_fonts[i]);
    ZeroMemory(g_zltk_fonts, sizeof(g_zltk_fonts));
    ZeroMemory(g_zltk_font_sizes, sizeof(g_zltk_font_sizes));
    g_zltk_font_count = 0;
}

static void zltk_draw_text_utf8(HDC dc, int x, int y, const char* text, COLORREF color, int size) {
    wchar_t* w = zltk_wide(text);
    if (!w) return;
    HFONT font = zltk_font_for_size(size);
    HFONT old_font = NULL;
    if (font) old_font = (HFONT)SelectObject(dc, font);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, color);
    TextOutW(dc, x, y, w, (int)wcslen(w));
    if (old_font) SelectObject(dc, old_font);
    free(w);
}

static int zltk_split_tabs(char* line, char** cols, int max_cols) {
    int n = 0;
    char* p = line;
    while (n < max_cols) {
        cols[n++] = p;
        char* tab = strchr(p, '\t');
        if (!tab) break;
        *tab = 0;
        p = tab + 1;
    }
    return n;
}

static void zltk_preset_from_scene(const char* path, char* title, size_t title_cap, int* w, int* h) {
    FILE* f = fopen(path, "rb");
    if (!f) return;
    char line[4096];
    while (fgets(line, sizeof(line), f)) {
        line[strcspn(line, "\r\n")] = 0;
        char* cols[8];
        int n = zltk_split_tabs(line, cols, 8);
        if (n >= 4 && strcmp(cols[0], "window") == 0) {
            zltk_clean_copy(title, title_cap, cols[1]);
            *w = zltk_to_int(cols[2], *w);
            *h = zltk_to_int(cols[3], *h);
        }
    }
    fclose(f);
}

static void zltk_draw_codeview_file(HDC dc, char** cols, int n) {
    if (n < 11) return;
    int x = zltk_to_int(cols[1], 0);
    int y = zltk_to_int(cols[2], 0);
    int h = zltk_to_int(cols[4], 300);
    int line_h = zltk_to_int(cols[6], 20);
    int size = zltk_to_int(cols[8], 16);
    const char* lines_path = cols[10];
    FILE* f = fopen(lines_path, "rb");
    if (!f) return;
    char line[4096];
    int row = 0;
    int max_rows = line_h > 0 ? h / line_h + 1 : 100;
    while (row < max_rows && fgets(line, sizeof(line), f)) {
        line[strcspn(line, "\r\n")] = 0;
        zltk_draw_text_utf8(dc, x, y + row * line_h + 2, line, RGB(235, 233, 224), size);
        row++;
    }
    fclose(f);
}

static void zltk_draw_codeview_text(HDC dc, const Zltk_Command* command) {
    if (!command || !command->text) return;
    int line_h = command->line_height > 0 ? command->line_height : 20;
    int max_rows = command->height > 0 ? command->height / line_h + 1 : 100;
    const char* start = command->text;
    for (int row = 0; row < max_rows && *start; row++) {
        const char* end = strchr(start, '\n');
        size_t length = end ? (size_t)(end - start) : strlen(start);
        while (length > 0 && start[length - 1] == '\r') length--;
        char* line = (char*)malloc(length + 1);
        if (!line) return;
        memcpy(line, start, length);
        line[length] = 0;
        zltk_draw_text_utf8(dc, command->x, command->y + row * line_h + 2, line,
                            RGB(235, 233, 224), command->size);
        free(line);
        if (!end) break;
        start = end + 1;
    }
}

static void zltk_draw_raw_command(HDC dc, RECT rc, char* line, COLORREF* bg) {
    if (!line || !line[0] || line[0] == '#') return;
    char* cols[16];
    int n = zltk_split_tabs(line, cols, 16);
    if (n <= 0) return;
    const char* op = cols[0];
    if ((strcmp(op, "bg") == 0 || strcmp(op, "clear") == 0) && n >= 2) {
        *bg = zltk_color(cols[1], *bg);
        HBRUSH brush = CreateSolidBrush(*bg);
        FillRect(dc, &rc, brush);
        DeleteObject(brush);
    } else if (strcmp(op, "line") == 0 && n >= 7) {
        HPEN pen = CreatePen(PS_SOLID, zltk_to_int(cols[6], 1), zltk_color(cols[5], RGB(255, 255, 255)));
        HPEN old = (HPEN)SelectObject(dc, pen);
        MoveToEx(dc, zltk_to_int(cols[1], 0), zltk_to_int(cols[2], 0), NULL);
        LineTo(dc, zltk_to_int(cols[3], 0), zltk_to_int(cols[4], 0));
        SelectObject(dc, old);
        DeleteObject(pen);
    } else if (strcmp(op, "rect") == 0 && n >= 6) {
        int x = zltk_to_int(cols[1], 0), y = zltk_to_int(cols[2], 0);
        int w = zltk_to_int(cols[3], 0), h = zltk_to_int(cols[4], 0);
        HBRUSH brush = CreateSolidBrush(zltk_color(cols[5], RGB(255, 255, 255)));
        RECT r = { x, y, x + w, y + h };
        FillRect(dc, &r, brush);
        DeleteObject(brush);
    } else if (strcmp(op, "rect_outline") == 0 && n >= 7) {
        int x = zltk_to_int(cols[1], 0), y = zltk_to_int(cols[2], 0);
        int w = zltk_to_int(cols[3], 0), h = zltk_to_int(cols[4], 0);
        HPEN pen = CreatePen(PS_SOLID, zltk_to_int(cols[6], 1), zltk_color(cols[5], RGB(255, 255, 255)));
        HBRUSH old_brush = (HBRUSH)SelectObject(dc, GetStockObject(NULL_BRUSH));
        HPEN old_pen = (HPEN)SelectObject(dc, pen);
        Rectangle(dc, x, y, x + w, y + h);
        SelectObject(dc, old_pen);
        SelectObject(dc, old_brush);
        DeleteObject(pen);
    } else if (strcmp(op, "circle") == 0 && n >= 5) {
        int x = zltk_to_int(cols[1], 0), y = zltk_to_int(cols[2], 0), r = zltk_to_int(cols[3], 0);
        HBRUSH brush = CreateSolidBrush(zltk_color(cols[4], RGB(255, 255, 255)));
        HBRUSH old = (HBRUSH)SelectObject(dc, brush);
        HPEN old_pen = (HPEN)SelectObject(dc, GetStockObject(NULL_PEN));
        Ellipse(dc, x - r, y - r, x + r, y + r);
        SelectObject(dc, old_pen);
        SelectObject(dc, old);
        DeleteObject(brush);
    } else if (strcmp(op, "circle_outline") == 0 && n >= 6) {
        int x = zltk_to_int(cols[1], 0), y = zltk_to_int(cols[2], 0), r = zltk_to_int(cols[3], 0);
        HPEN pen = CreatePen(PS_SOLID, zltk_to_int(cols[5], 1), zltk_color(cols[4], RGB(255, 255, 255)));
        HBRUSH old_brush = (HBRUSH)SelectObject(dc, GetStockObject(NULL_BRUSH));
        HPEN old_pen = (HPEN)SelectObject(dc, pen);
        Ellipse(dc, x - r, y - r, x + r, y + r);
        SelectObject(dc, old_pen);
        SelectObject(dc, old_brush);
        DeleteObject(pen);
    } else if (strcmp(op, "text") == 0 && n >= 6) {
        zltk_draw_text_utf8(dc, zltk_to_int(cols[1], 0), zltk_to_int(cols[2], 0), cols[3],
                            zltk_color(cols[4], RGB(255, 255, 255)), zltk_to_int(cols[5], 16));
    } else if (strcmp(op, "codeview") == 0) {
        zltk_draw_codeview_file(dc, cols, n);
    }
}

static void zltk_draw_scene(HDC dc, RECT rc) {
    COLORREF bg = RGB(32, 33, 36);
    HBRUSH bg_brush = CreateSolidBrush(bg);
    FillRect(dc, &rc, bg_brush);
    DeleteObject(bg_brush);

    if (g_zltk.is_session) {
        zltk_lock();
        for (int i = 0; i < g_zltk.display_frame.count; i++) {
            Zltk_Command* command = &g_zltk.display_frame.items[i];
            if (command->kind == ZLTK_COMMAND_CODEVIEW_TEXT) {
                zltk_draw_codeview_text(dc, command);
            } else if (command->kind == ZLTK_COMMAND_RAW && command->raw) {
                char line[4096];
                strncpy(line, command->raw, sizeof(line) - 1);
                line[sizeof(line) - 1] = 0;
                zltk_draw_raw_command(dc, rc, line, &bg);
            }
        }
        zltk_unlock();
        return;
    }

    FILE* f = fopen(g_zltk.script_path, "rb");
    if (!f) return;
    char line[4096];
    while (fgets(line, sizeof(line), f)) {
        line[strcspn(line, "\r\n")] = 0;
        zltk_draw_raw_command(dc, rc, line, &bg);
    }
    fclose(f);
}

static void zltk_append_event(const char* line) {
    if (!g_zltk.is_session) return;
    zltk_lock();
    if (g_zltk.event_count == ZLTK_EVENT_CAPACITY) {
        g_zltk.event_head = (g_zltk.event_head + 1) % ZLTK_EVENT_CAPACITY;
        g_zltk.event_count--;
    }
    int tail = (g_zltk.event_head + g_zltk.event_count) % ZLTK_EVENT_CAPACITY;
    strncpy(g_zltk.events[tail], line ? line : "", sizeof(g_zltk.events[tail]) - 1);
    g_zltk.events[tail][sizeof(g_zltk.events[tail]) - 1] = 0;
    g_zltk.event_count++;
    if (g_zltk.event_signal) SetEvent(g_zltk.event_signal);
    zltk_unlock();
}

static const char* zltk_key_name(WPARAM vk, int ctrl) {
    static char buf[32];
    if (ctrl && vk >= 'A' && vk <= 'Z') {
        buf[0] = (char)(vk - 'A' + 'a');
        buf[1] = 0;
        return buf;
    }
    if (vk >= '0' && vk <= '9') {
        buf[0] = (char)vk;
        buf[1] = 0;
        return buf;
    }
    switch (vk) {
        case VK_LEFT: return "Left";
        case VK_RIGHT: return "Right";
        case VK_UP: return "Up";
        case VK_DOWN: return "Down";
        case VK_HOME: return "Home";
        case VK_END: return "End";
        case VK_PRIOR: return "Prior";
        case VK_NEXT: return "Next";
        case VK_ESCAPE: return "Escape";
        case VK_RETURN: return "Return";
        case VK_BACK: return "BackSpace";
        case VK_DELETE: return "Delete";
        case VK_TAB: return "Tab";
        case VK_OEM_PLUS: return (GetKeyState(VK_SHIFT) & 0x8000) ? "plus" : "equal";
        case VK_OEM_MINUS: return "minus";
        default: break;
    }
    snprintf(buf, sizeof(buf), "%lu", (unsigned long)vk);
    return buf;
}

static void zltk_pick_directory(void) {
    BROWSEINFOA bi;
    ZeroMemory(&bi, sizeof(bi));
    bi.hwndOwner = g_zltk.hwnd;
    bi.lpszTitle = "Open folder";
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    LPITEMIDLIST pidl = SHBrowseForFolderA(&bi);
    char path[MAX_PATH] = "";
    if (pidl) {
        SHGetPathFromIDListA(pidl, path);
        CoTaskMemFree(pidl);
    }
    char ev[MAX_PATH + 16];
    snprintf(ev, sizeof(ev), "pickdir\t%s", path);
    zltk_append_event(ev);
}

static int zltk_ensure_backbuffer(HDC dc, int width, int height) {
    if (width <= 0 || height <= 0) return -1;
    if (!g_zltk.back_dc) {
        g_zltk.back_dc = CreateCompatibleDC(dc);
        if (!g_zltk.back_dc) return -1;
    }
    if (g_zltk.back_bmp && g_zltk.back_width == width && g_zltk.back_height == height) return 0;
    if (g_zltk.back_bmp) {
        SelectObject(g_zltk.back_dc, g_zltk.back_old);
        DeleteObject(g_zltk.back_bmp);
        g_zltk.back_bmp = NULL;
    }
    g_zltk.back_bmp = CreateCompatibleBitmap(dc, width, height);
    if (!g_zltk.back_bmp) return -1;
    g_zltk.back_old = (HBITMAP)SelectObject(g_zltk.back_dc, g_zltk.back_bmp);
    g_zltk.back_width = width;
    g_zltk.back_height = height;
    return 0;
}

static void zltk_release_backbuffer(void) {
    if (g_zltk.back_dc && g_zltk.back_bmp) {
        SelectObject(g_zltk.back_dc, g_zltk.back_old);
        DeleteObject(g_zltk.back_bmp);
    }
    if (g_zltk.back_dc) DeleteDC(g_zltk.back_dc);
    g_zltk.back_dc = NULL;
    g_zltk.back_bmp = NULL;
    g_zltk.back_old = NULL;
    g_zltk.back_width = 0;
    g_zltk.back_height = 0;
}

#define ZLTK_WM_PICKDIR (WM_APP + 41)

static LRESULT CALLBACK zltk_wndproc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_CREATE:
            g_zltk.hwnd = hwnd;
            if (g_zltk.show_for_ms > 0) SetTimer(hwnd, 2, (UINT)g_zltk.show_for_ms, NULL);
            return 0;
        case WM_TIMER:
            if (wp == 2) DestroyWindow(hwnd);
            return 0;
        case ZLTK_WM_PICKDIR:
            zltk_pick_directory();
            return 0;
        case WM_ERASEBKGND:
            return 1;
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(hwnd, &ps);
            RECT rc;
            GetClientRect(hwnd, &rc);
            int width = rc.right - rc.left;
            int height = rc.bottom - rc.top;
            if (zltk_ensure_backbuffer(dc, width, height) == 0) {
                zltk_draw_scene(g_zltk.back_dc, rc);
                BitBlt(dc, 0, 0, width, height, g_zltk.back_dc, 0, 0, SRCCOPY);
            }
            EndPaint(hwnd, &ps);
            return 0;
        }
        case WM_KEYDOWN: {
            int ctrl = (GetKeyState(VK_CONTROL) & 0x8000) != 0;
            int shift = (GetKeyState(VK_SHIFT) & 0x8000) != 0;
            const char* name = zltk_key_name(wp, ctrl);
            char ev[96];
            if (ctrl && wp != VK_CONTROL) snprintf(ev, sizeof(ev), "ctrl\t%s", name);
            else if (shift && (wp == VK_LEFT || wp == VK_RIGHT || wp == VK_UP || wp == VK_DOWN || wp == VK_HOME || wp == VK_END || wp == VK_PRIOR || wp == VK_NEXT)) snprintf(ev, sizeof(ev), "shift\t%s", name);
            else snprintf(ev, sizeof(ev), "key\t%s", name);
            zltk_append_event(ev);
            return 0;
        }
        case WM_CHAR:
            if (wp >= 32 && wp != 127) {
                wchar_t wc[2] = { (wchar_t)wp, 0 };
                char utf8[16] = {0};
                WideCharToMultiByte(CP_UTF8, 0, wc, -1, utf8, sizeof(utf8), NULL, NULL);
                char ev[64];
                snprintf(ev, sizeof(ev), "keychar\t%s", utf8);
                zltk_append_event(ev);
            }
            return 0;
        case WM_LBUTTONDOWN: {
            SetCapture(hwnd);
            char ev[96];
            snprintf(ev, sizeof(ev), "%s\t%d\t%d", (GetKeyState(VK_CONTROL) & 0x8000) ? "ctrl_mouse" : "mouse", GET_X_LPARAM(lp), GET_Y_LPARAM(lp));
            zltk_append_event(ev);
            return 0;
        }
        case WM_LBUTTONUP: {
            ReleaseCapture();
            char ev[96];
            snprintf(ev, sizeof(ev), "release\t%d\t%d", GET_X_LPARAM(lp), GET_Y_LPARAM(lp));
            zltk_append_event(ev);
            return 0;
        }
        case WM_MOUSEMOVE: {
            DWORD now = GetTickCount();
            char ev[96];
            if (wp & MK_LBUTTON) {
                if (now - g_zltk.last_drag_ms < 30) return 0;
                g_zltk.last_drag_ms = now;
                snprintf(ev, sizeof(ev), "drag\t%d\t%d", GET_X_LPARAM(lp), GET_Y_LPARAM(lp));
            } else {
                if (now - g_zltk.last_motion_ms < 80) return 0;
                g_zltk.last_motion_ms = now;
                snprintf(ev, sizeof(ev), "motion\t%d\t%d", GET_X_LPARAM(lp), GET_Y_LPARAM(lp));
            }
            zltk_append_event(ev);
            return 0;
        }
        case WM_MOUSEWHEEL: {
            POINT p = { GET_X_LPARAM(lp), GET_Y_LPARAM(lp) };
            ScreenToClient(hwnd, &p);
            int delta = GET_WHEEL_DELTA_WPARAM(wp);
            int notches = delta > 0 ? 1 : -1;
            char ev[96];
            snprintf(ev, sizeof(ev), "wheel\t%d\t%d\t%d", notches, (int)p.x, (int)p.y);
            zltk_append_event(ev);
            return 0;
        }
        case WM_CLOSE:
            zltk_append_event("quit");
            DestroyWindow(hwnd);
            return 0;
        case WM_DESTROY:
            g_zltk.closed = 1;
            PostQuitMessage(0);
            return 0;
        default:
            return DefWindowProc(hwnd, msg, wp, lp);
    }
}

static int zltk_run_window(const char* title, int width, int height, int session) {
    HRESULT com_result = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    HINSTANCE inst = GetModuleHandle(NULL);
    WNDCLASSA wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = zltk_wndproc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursor(NULL, IDC_IBEAM);
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wc.lpszClassName = "ZyenLangNativeTk";
    RegisterClassA(&wc);
    wchar_t* wtitle = zltk_wide(title ? title : "ZyenLang TK");
    HWND hwnd = CreateWindowExW(0, L"ZyenLangNativeTk", wtitle ? wtitle : L"ZyenLang TK",
                                WS_OVERLAPPEDWINDOW | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT,
                                width, height, NULL, NULL, inst, NULL);
    if (wtitle) free(wtitle);
    if (!hwnd) {
        if (SUCCEEDED(com_result)) CoUninitialize();
        return -1;
    }
    g_zltk.hwnd = hwnd;
    ShowWindow(hwnd, SW_SHOW);
    UpdateWindow(hwnd);
    if (session) {
        zltk_append_event("ready");
        g_zltk.ready = 1;
    }
    MSG msg;
    while (GetMessage(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }
    zltk_release_backbuffer();
    zltk_release_fonts();
    g_zltk.hwnd = NULL;
    if (SUCCEEDED(com_result)) CoUninitialize();
    return 0;
}

static DWORD WINAPI zltk_session_thread(LPVOID unused) {
    (void)unused;
    return (DWORD)zltk_run_window(g_zltk.title, g_zltk.width, g_zltk.height, 1);
}

int zl_tk_begin(const char* path) {
    if (path && path[0]) {
        strncpy(g_zltk.script_path, path, sizeof(g_zltk.script_path) - 1);
        g_zltk.script_path[sizeof(g_zltk.script_path) - 1] = 0;
    }
    FILE* f = fopen(g_zltk.script_path, "wb");
    if (!f) return -1;
    fputs("# ZyenLang tk scene\n", f);
    fclose(f);
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
    int code = zl_tk_begin("zyen_tk_scene.ztk");
    if (code != 0) return code;
    return zl_tk_window(title, width, height);
}

int zl_tk_bg(const char* color) {
    char clean[256], line[512];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "bg\t%s", clean);
    return zltk_append_raw(line);
}

int zl_tk_clear(const char* color) {
    char clean[256], line[512];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "clear\t%s", clean);
    return zltk_append_raw(line);
}

int zl_tk_line(int x1, int y1, int x2, int y2, const char* color, int width) {
    char clean[256], line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "line\t%d\t%d\t%d\t%d\t%s\t%d", x1, y1, x2, y2, clean, width);
    return zltk_append_raw(line);
}

int zl_tk_rect(int x, int y, int width, int height, const char* color) {
    char clean[256], line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "rect\t%d\t%d\t%d\t%d\t%s", x, y, width, height, clean);
    return zltk_append_raw(line);
}

int zl_tk_rect_outline(int x, int y, int width, int height, const char* color, int line_width) {
    char clean[256], line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "rect_outline\t%d\t%d\t%d\t%d\t%s\t%d", x, y, width, height, clean, line_width);
    return zltk_append_raw(line);
}

int zl_tk_circle(int x, int y, int radius, const char* color) {
    char clean[256], line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "circle\t%d\t%d\t%d\t%s", x, y, radius, clean);
    return zltk_append_raw(line);
}

int zl_tk_circle_outline(int x, int y, int radius, const char* color, int line_width) {
    char clean[256], line[1400];
    zltk_clean_copy(clean, sizeof(clean), color);
    snprintf(line, sizeof(line), "circle_outline\t%d\t%d\t%d\t%s\t%d", x, y, radius, clean, line_width);
    return zltk_append_raw(line);
}

int zl_tk_text(int x, int y, const char* text, const char* color, int size) {
    char clean_text[1024], clean_color[256], line[1800];
    zltk_clean_copy(clean_text, sizeof(clean_text), text);
    zltk_clean_copy(clean_color, sizeof(clean_color), color);
    snprintf(line, sizeof(line), "text\t%d\t%d\t%s\t%s\t%d", x, y, clean_text, clean_color, size);
    return zltk_append_raw(line);
}

int zl_tk_codeview(int x, int y, int width, int height, int first_line, int line_height, int char_width, int size, int stamp, const char* lines_path) {
    char clean[1024], line[2200];
    (void)first_line;
    zltk_clean_copy(clean, sizeof(clean), lines_path);
    snprintf(line, sizeof(line), "codeview\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s", x, y, width, height, first_line, line_height, char_width, size, stamp, clean);
    return zltk_append_raw(line);
}

int zl_tk_codeview_text(int x, int y, int width, int height, int first_line, int line_height, int char_width, int size, int stamp, const char* lines) {
    if (!g_zltk.is_session) return -1;
    int result = 0;
    zltk_lock();
    if (!g_zltk.frame_active) {
        result = -1;
    } else {
        Zltk_Command* command = zltk_frame_push(&g_zltk.building_frame);
        if (!command) {
            result = -1;
        } else {
            command->kind = ZLTK_COMMAND_CODEVIEW_TEXT;
            command->text = zltk_strdup(lines ? lines : "");
            command->x = x;
            command->y = y;
            command->width = width;
            command->height = height;
            command->first_line = first_line;
            command->line_height = line_height;
            command->char_width = char_width;
            command->size = size;
            command->stamp = stamp;
            if (!command->text) {
                g_zltk.building_frame.count--;
                result = -1;
            }
        }
    }
    zltk_unlock();
    return result;
}

int zl_tk_image(const char* path, int x, int y) {
    char clean[1024], line[1800];
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
    char title[256] = "ZyenLang TK";
    int w = 800, h = 600;
    zltk_preset_from_scene(g_zltk.script_path, title, sizeof(title), &w, &h);
    g_zltk.show_for_ms = ms;
    g_zltk.is_session = 0;
    return zltk_run_window(title, w, h, 0);
}

int zl_tk_session_open(const char* title, int width, int height, const char* session_dir) {
    (void)session_dir;
    if (g_zltk.thread) return -1;
    zltk_lock();
    zltk_frame_clear(&g_zltk.building_frame);
    zltk_frame_clear(&g_zltk.display_frame);
    g_zltk.frame_active = 0;
    g_zltk.event_head = 0;
    g_zltk.event_count = 0;
    zltk_unlock();
    zltk_clean_copy(g_zltk.title, sizeof(g_zltk.title), title ? title : "ZyenLang TK");
    g_zltk.width = width;
    g_zltk.height = height;
    g_zltk.ready = 0;
    g_zltk.closed = 0;
    g_zltk.is_session = 1;
    g_zltk.scene_version = 0;
    g_zltk.event_signal = CreateEventA(NULL, FALSE, FALSE, NULL);
    if (!g_zltk.event_signal) {
        g_zltk.is_session = 0;
        return -1;
    }
    g_zltk.thread = CreateThread(NULL, 0, zltk_session_thread, NULL, 0, NULL);
    if (!g_zltk.thread) {
        CloseHandle(g_zltk.event_signal);
        g_zltk.event_signal = NULL;
        g_zltk.is_session = 0;
        return -1;
    }
    if (WaitForSingleObject(g_zltk.event_signal, 5000) == WAIT_TIMEOUT || !g_zltk.ready) {
        PostMessage(g_zltk.hwnd, WM_CLOSE, 0, 0);
        WaitForSingleObject(g_zltk.thread, 2000);
        CloseHandle(g_zltk.thread);
        CloseHandle(g_zltk.event_signal);
        g_zltk.thread = NULL;
        g_zltk.event_signal = NULL;
        g_zltk.is_session = 0;
        return -1;
    }
    return 0;
}

int zl_tk_session_begin_frame(void) {
    if (!g_zltk.is_session) return -1;
    zltk_lock();
    zltk_frame_clear(&g_zltk.building_frame);
    g_zltk.frame_active = 1;
    zltk_unlock();
    return 0;
}

int zl_tk_session_redraw(void) {
    if (!g_zltk.is_session) return -1;
    zltk_lock();
    Zltk_Frame old_display = g_zltk.display_frame;
    g_zltk.display_frame = g_zltk.building_frame;
    g_zltk.building_frame = old_display;
    g_zltk.frame_active = 0;
    g_zltk.scene_version++;
    zltk_unlock();
    if (g_zltk.hwnd) InvalidateRect(g_zltk.hwnd, NULL, FALSE);
    return 0;
}

const char* zl_tk_session_next_event(int timeout_ms) {
    static char bufs[16][512];
    static int idx = 0;
    char* out = bufs[idx++ & 15];
    out[0] = 0;
    DWORD start = GetTickCount();
    while (1) {
        zltk_lock();
        if (g_zltk.event_count > 0) {
            strncpy(out, g_zltk.events[g_zltk.event_head], 511);
            out[511] = 0;
            g_zltk.event_head = (g_zltk.event_head + 1) % ZLTK_EVENT_CAPACITY;
            g_zltk.event_count--;
            if (g_zltk.event_count > 0 && g_zltk.event_signal) SetEvent(g_zltk.event_signal);
            zltk_unlock();
            return out;
        }
        zltk_unlock();
        if (timeout_ms <= 0 || (int)(GetTickCount() - start) >= timeout_ms) {
            out[0] = 0;
            return out;
        }
        DWORD elapsed = GetTickCount() - start;
        DWORD remaining = elapsed >= (DWORD)timeout_ms ? 0 : (DWORD)timeout_ms - elapsed;
        if (!g_zltk.event_signal || WaitForSingleObject(g_zltk.event_signal, remaining) == WAIT_TIMEOUT) return out;
    }
}

int zl_tk_session_pickdir(void) {
    if (!g_zltk.is_session || !g_zltk.hwnd) return -1;
    return PostMessage(g_zltk.hwnd, ZLTK_WM_PICKDIR, 0, 0) ? 0 : -1;
}

int zl_tk_session_close(void) {
    if (g_zltk.hwnd) PostMessage(g_zltk.hwnd, WM_CLOSE, 0, 0);
    if (g_zltk.thread) {
        WaitForSingleObject(g_zltk.thread, 2000);
        CloseHandle(g_zltk.thread);
        g_zltk.thread = NULL;
    }
    if (g_zltk.event_signal) {
        CloseHandle(g_zltk.event_signal);
        g_zltk.event_signal = NULL;
    }
    zltk_lock();
    zltk_frame_clear(&g_zltk.building_frame);
    zltk_frame_clear(&g_zltk.display_frame);
    g_zltk.frame_active = 0;
    zltk_unlock();
    g_zltk.is_session = 0;
    return 0;
}

int zl_tk_session_char_w(void) {
    return 9;
}

int zl_tk_session_line_h(void) {
    return 20;
}

#else

int zl_tk_begin(const char* path) { (void)path; return -1; }
int zl_tk_open(const char* title, int width, int height) { (void)title; (void)width; (void)height; return -1; }
int zl_tk_window(const char* title, int width, int height) { (void)title; (void)width; (void)height; return -1; }
int zl_tk_bg(const char* color) { (void)color; return -1; }
int zl_tk_clear(const char* color) { (void)color; return -1; }
int zl_tk_line(int x1, int y1, int x2, int y2, const char* color, int width) { (void)x1; (void)y1; (void)x2; (void)y2; (void)color; (void)width; return -1; }
int zl_tk_rect(int x, int y, int width, int height, const char* color) { (void)x; (void)y; (void)width; (void)height; (void)color; return -1; }
int zl_tk_rect_outline(int x, int y, int width, int height, const char* color, int line_width) { (void)x; (void)y; (void)width; (void)height; (void)color; (void)line_width; return -1; }
int zl_tk_circle(int x, int y, int radius, const char* color) { (void)x; (void)y; (void)radius; (void)color; return -1; }
int zl_tk_circle_outline(int x, int y, int radius, const char* color, int line_width) { (void)x; (void)y; (void)radius; (void)color; (void)line_width; return -1; }
int zl_tk_text(int x, int y, const char* text, const char* color, int size) { (void)x; (void)y; (void)text; (void)color; (void)size; return -1; }
int zl_tk_codeview(int x, int y, int width, int height, int first_line, int line_height, int char_width, int size, int stamp, const char* lines_path) { (void)x; (void)y; (void)width; (void)height; (void)first_line; (void)line_height; (void)char_width; (void)size; (void)stamp; (void)lines_path; return -1; }
int zl_tk_codeview_text(int x, int y, int width, int height, int first_line, int line_height, int char_width, int size, int stamp, const char* lines) { (void)x; (void)y; (void)width; (void)height; (void)first_line; (void)line_height; (void)char_width; (void)size; (void)stamp; (void)lines; return -1; }
int zl_tk_image(const char* path, int x, int y) { (void)path; (void)x; (void)y; return -1; }
const char* zl_tk_script(void) { return ""; }
int zl_tk_show(void) { return -1; }
int zl_tk_show_for(int ms) { (void)ms; return -1; }
int zl_tk_session_open(const char* title, int width, int height, const char* session_dir) { (void)title; (void)width; (void)height; (void)session_dir; return -1; }
int zl_tk_session_begin_frame(void) { return -1; }
int zl_tk_session_redraw(void) { return -1; }
const char* zl_tk_session_next_event(int timeout_ms) { (void)timeout_ms; return ""; }
int zl_tk_session_pickdir(void) { return -1; }
int zl_tk_session_close(void) { return -1; }
int zl_tk_session_char_w(void) { return 9; }
int zl_tk_session_line_h(void) { return 20; }

#endif
