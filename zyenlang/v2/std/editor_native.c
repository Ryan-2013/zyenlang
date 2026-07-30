#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

enum {
    EDITOR_VIEW_X = 16,
    EDITOR_VIEW_Y = 58,
    EDITOR_TEXT_X = 78,
    EDITOR_LINE_HEIGHT = 22,
    EDITOR_CHAR_WIDTH = 10,
    EDITOR_VISIBLE_ROWS = 31,
    EDITOR_COMPLETION_MAX = 8,
    EDITOR_COMPLETION_CAPACITY = 96
};

typedef struct {
    char* data;
    char* path;
    size_t length;
    size_t capacity;
    size_t cursor;
    size_t selection_anchor;
    size_t completion_prefix_start;
    int first_line;
    int preferred_column;
    int selecting;
    int dirty;
    int revision;
    int completion_count;
    int completion_selected;
    char completions[EDITOR_COMPLETION_MAX][EDITOR_COMPLETION_CAPACITY];
    char completion_text[EDITOR_COMPLETION_MAX * EDITOR_COMPLETION_CAPACITY];
    char message[160];
} Zy2Editor;

static Zy2Editor g_editor;

static FILE* editor_fopen(const char* path, const char* mode) {
#ifdef _WIN32
    wchar_t wide_path[4096];
    wchar_t wide_mode[16];
    if (!path || MultiByteToWideChar(CP_UTF8, 0, path, -1, wide_path, 4096) <= 0) return NULL;
    if (MultiByteToWideChar(CP_UTF8, 0, mode, -1, wide_mode, 16) <= 0) return NULL;
    return _wfopen(wide_path, wide_mode);
#else
    return fopen(path, mode);
#endif
}

static char* editor_copy_string(const char* value) {
    size_t length = strlen(value ? value : "");
    char* copy = (char*)malloc(length + 1);
    if (!copy) return NULL;
    memcpy(copy, value ? value : "", length + 1);
    return copy;
}

static int editor_reserve(size_t needed) {
    if (needed <= g_editor.capacity) return 0;
    size_t capacity = g_editor.capacity > 0 ? g_editor.capacity : 256;
    while (capacity < needed) {
        if (capacity > ((size_t)-1) / 2) return -1;
        capacity *= 2;
    }
    char* grown = (char*)realloc(g_editor.data, capacity);
    if (!grown) return -1;
    g_editor.data = grown;
    g_editor.capacity = capacity;
    return 0;
}

static size_t editor_previous_character(size_t offset) {
    if (offset == 0) return 0;
    offset--;
    while (offset > 0 && ((unsigned char)g_editor.data[offset] & 0xc0u) == 0x80u) offset--;
    return offset;
}

static size_t editor_next_character(size_t offset) {
    if (offset >= g_editor.length) return g_editor.length;
    offset++;
    while (offset < g_editor.length && ((unsigned char)g_editor.data[offset] & 0xc0u) == 0x80u) offset++;
    return offset;
}

static int editor_line_at(size_t offset) {
    int line = 0;
    if (offset > g_editor.length) offset = g_editor.length;
    for (size_t index = 0; index < offset; index++) {
        if (g_editor.data[index] == '\n') line++;
    }
    return line;
}

static size_t editor_line_start(size_t offset) {
    if (offset > g_editor.length) offset = g_editor.length;
    while (offset > 0 && g_editor.data[offset - 1] != '\n') offset--;
    return offset;
}

static size_t editor_line_end(size_t offset) {
    if (offset > g_editor.length) offset = g_editor.length;
    while (offset < g_editor.length && g_editor.data[offset] != '\n') offset++;
    return offset;
}

static int editor_visual_column(size_t offset) {
    size_t start = editor_line_start(offset);
    int column = 0;
    while (start < offset) {
        unsigned char byte = (unsigned char)g_editor.data[start];
        if (byte == '\t') column += 4 - (column % 4);
        else if ((byte & 0xc0u) != 0x80u) column++;
        start++;
    }
    return column;
}

static size_t editor_offset_for_column(size_t line_start, int wanted_column) {
    size_t cursor = line_start;
    int column = 0;
    while (cursor < g_editor.length && g_editor.data[cursor] != '\n') {
        size_t next = editor_next_character(cursor);
        int width = g_editor.data[cursor] == '\t' ? 4 - (column % 4) : 1;
        if (column + width > wanted_column) break;
        column += width;
        cursor = next;
    }
    return cursor;
}

static size_t editor_start_of_numbered_line(int target_line) {
    if (target_line <= 0) return 0;
    int line = 0;
    for (size_t index = 0; index < g_editor.length; index++) {
        if (g_editor.data[index] == '\n' && ++line == target_line) return index + 1;
    }
    return g_editor.length;
}

static int editor_line_count_value(void) {
    int lines = 1;
    for (size_t index = 0; index < g_editor.length; index++) {
        if (g_editor.data[index] == '\n') lines++;
    }
    return lines;
}

static int editor_has_selection_value(void) {
    return g_editor.selection_anchor != g_editor.cursor;
}

static void editor_selection_range(size_t* start, size_t* end) {
    if (g_editor.selection_anchor < g_editor.cursor) {
        *start = g_editor.selection_anchor;
        *end = g_editor.cursor;
    } else {
        *start = g_editor.cursor;
        *end = g_editor.selection_anchor;
    }
}

static void editor_clear_completion(void) {
    g_editor.completion_count = 0;
    g_editor.completion_selected = 0;
    g_editor.completion_text[0] = 0;
}

static void editor_keep_cursor_visible(void) {
    int line = editor_line_at(g_editor.cursor);
    if (line < g_editor.first_line) g_editor.first_line = line;
    if (line >= g_editor.first_line + EDITOR_VISIBLE_ROWS) {
        g_editor.first_line = line - EDITOR_VISIBLE_ROWS + 1;
    }
    if (g_editor.first_line < 0) g_editor.first_line = 0;
}

static int editor_replace_range(size_t start, size_t end, const char* value, size_t value_length) {
    if (start > end || end > g_editor.length) return -1;
    size_t removed = end - start;
    size_t new_length = g_editor.length - removed + value_length;
    if (editor_reserve(new_length + 1) != 0) return -1;
    memmove(g_editor.data + start + value_length, g_editor.data + end, g_editor.length - end + 1);
    if (value_length > 0) memcpy(g_editor.data + start, value, value_length);
    g_editor.length = new_length;
    g_editor.cursor = start + value_length;
    g_editor.selection_anchor = g_editor.cursor;
    g_editor.dirty = 1;
    g_editor.revision++;
    g_editor.preferred_column = editor_visual_column(g_editor.cursor);
    editor_keep_cursor_visible();
    return 1;
}

static int editor_insert(const char* value, size_t length) {
    size_t start = g_editor.cursor;
    size_t end = g_editor.cursor;
    if (editor_has_selection_value()) editor_selection_range(&start, &end);
    return editor_replace_range(start, end, value, length);
}

static int editor_delete_selection(void) {
    if (!editor_has_selection_value()) return 0;
    size_t start = 0;
    size_t end = 0;
    editor_selection_range(&start, &end);
    return editor_replace_range(start, end, "", 0);
}

static int editor_identifier_byte(unsigned char value) {
    return isalnum(value) || value == '_';
}

static int editor_completion_exists(const char* value) {
    for (int index = 0; index < g_editor.completion_count; index++) {
        if (strcmp(g_editor.completions[index], value) == 0) return 1;
    }
    return 0;
}

static void editor_add_completion(const char* value, size_t length, const char* prefix, size_t prefix_length) {
    if (g_editor.completion_count >= EDITOR_COMPLETION_MAX || length == 0 || length >= EDITOR_COMPLETION_CAPACITY) return;
    if (length <= prefix_length || strncmp(value, prefix, prefix_length) != 0) return;
    char candidate[EDITOR_COMPLETION_CAPACITY];
    memcpy(candidate, value, length);
    candidate[length] = 0;
    if (editor_completion_exists(candidate)) return;
    memcpy(g_editor.completions[g_editor.completion_count], candidate, length + 1);
    g_editor.completion_count++;
}

static void editor_refresh_completion(void) {
    static const char* language_words[] = {
        "as", "await", "bool", "break", "catch", "continue", "else", "Error", "f32", "f64",
        "false", "fn", "GET_ARGS__", "GET_EXE__", "i8", "i16", "i32", "i64", "if", "import", "isize", "let", "List",
        "native", "null", "private", "public", "recover", "return", "spawn", "stop", "str",
        "struct", "throws", "true", "TYPEOF__", "u8", "u16", "u32", "u64", "usize", "void", "while",
        "begin_frame", "close", "eprint", "get", "len", "next_event", "open", "present", "print", "save", "window"
    };
    editor_clear_completion();
    if (!g_editor.data || g_editor.cursor == 0) return;
    size_t start = g_editor.cursor;
    while (start > 0 && editor_identifier_byte((unsigned char)g_editor.data[start - 1])) start--;
    size_t prefix_length = g_editor.cursor - start;
    if (prefix_length == 0 || prefix_length >= EDITOR_COMPLETION_CAPACITY) return;
    const char* prefix = g_editor.data + start;
    g_editor.completion_prefix_start = start;
    for (size_t index = 0; index < sizeof(language_words) / sizeof(language_words[0]); index++) {
        editor_add_completion(language_words[index], strlen(language_words[index]), prefix, prefix_length);
    }
    size_t cursor = 0;
    while (cursor < g_editor.length && g_editor.completion_count < EDITOR_COMPLETION_MAX) {
        while (cursor < g_editor.length && !editor_identifier_byte((unsigned char)g_editor.data[cursor])) cursor++;
        size_t word_start = cursor;
        while (cursor < g_editor.length && editor_identifier_byte((unsigned char)g_editor.data[cursor])) cursor++;
        editor_add_completion(g_editor.data + word_start, cursor - word_start, prefix, prefix_length);
    }
    size_t output = 0;
    for (int index = 0; index < g_editor.completion_count; index++) {
        size_t length = strlen(g_editor.completions[index]);
        if (output + length + 2 >= sizeof(g_editor.completion_text)) break;
        if (index > 0) g_editor.completion_text[output++] = '\n';
        memcpy(g_editor.completion_text + output, g_editor.completions[index], length);
        output += length;
    }
    g_editor.completion_text[output] = 0;
}

static int editor_accept_completion(void) {
    if (g_editor.completion_count <= 0) return 0;
    const char* value = g_editor.completions[g_editor.completion_selected];
    size_t start = g_editor.completion_prefix_start;
    int result = editor_replace_range(start, g_editor.cursor, value, strlen(value));
    editor_clear_completion();
    return result;
}

static void editor_move_vertical(int direction) {
    int current_line = editor_line_at(g_editor.cursor);
    int target_line = current_line + direction;
    if (target_line < 0 || target_line >= editor_line_count_value()) return;
    size_t target_start = editor_start_of_numbered_line(target_line);
    g_editor.cursor = editor_offset_for_column(target_start, g_editor.preferred_column);
    editor_keep_cursor_visible();
}

static int editor_begin_movement(int extend, const char* key) {
    if (extend) return 0;
    if (editor_has_selection_value() && (strcmp(key, "Left") == 0 || strcmp(key, "Right") == 0)) {
        size_t start = 0;
        size_t end = 0;
        editor_selection_range(&start, &end);
        g_editor.cursor = strcmp(key, "Left") == 0 ? start : end;
        g_editor.selection_anchor = g_editor.cursor;
        return 1;
    }
    g_editor.selection_anchor = g_editor.cursor;
    return 0;
}

static void editor_finish_movement(int extend) {
    if (!extend) g_editor.selection_anchor = g_editor.cursor;
    g_editor.preferred_column = editor_visual_column(g_editor.cursor);
    editor_keep_cursor_visible();
    editor_clear_completion();
}

static int editor_handle_key(const char* key, int extend) {
    if (!extend && g_editor.completion_count > 0 && strcmp(key, "Up") == 0) {
        g_editor.completion_selected--;
        if (g_editor.completion_selected < 0) g_editor.completion_selected = g_editor.completion_count - 1;
        return 1;
    }
    if (!extend && g_editor.completion_count > 0 && strcmp(key, "Down") == 0) {
        g_editor.completion_selected = (g_editor.completion_selected + 1) % g_editor.completion_count;
        return 1;
    }
    if (!extend && g_editor.completion_count > 0 && (strcmp(key, "Return") == 0 || strcmp(key, "Tab") == 0)) {
        return editor_accept_completion();
    }
    if (strcmp(key, "Escape") == 0) {
        editor_clear_completion();
        return 1;
    }
    if (strcmp(key, "BackSpace") == 0) {
        if (!editor_delete_selection()) {
            editor_replace_range(editor_previous_character(g_editor.cursor), g_editor.cursor, "", 0);
        }
        editor_refresh_completion();
        return 1;
    }
    if (strcmp(key, "Delete") == 0) {
        if (!editor_delete_selection()) {
            editor_replace_range(g_editor.cursor, editor_next_character(g_editor.cursor), "", 0);
        }
        editor_refresh_completion();
        return 1;
    }
    if (strcmp(key, "Return") == 0) {
        editor_clear_completion();
        return editor_insert("\n", 1);
    }
    if (strcmp(key, "Tab") == 0) {
        editor_clear_completion();
        return editor_insert("    ", 4);
    }

    if (editor_begin_movement(extend, key)) {
        editor_finish_movement(extend);
        return 1;
    }
    if (strcmp(key, "Left") == 0) g_editor.cursor = editor_previous_character(g_editor.cursor);
    else if (strcmp(key, "Right") == 0) g_editor.cursor = editor_next_character(g_editor.cursor);
    else if (strcmp(key, "Up") == 0) editor_move_vertical(-1);
    else if (strcmp(key, "Down") == 0) editor_move_vertical(1);
    else if (strcmp(key, "Home") == 0) g_editor.cursor = editor_line_start(g_editor.cursor);
    else if (strcmp(key, "End") == 0) g_editor.cursor = editor_line_end(g_editor.cursor);
    else if (strcmp(key, "Prior") == 0) {
        for (int row = 0; row < EDITOR_VISIBLE_ROWS - 2; row++) editor_move_vertical(-1);
    } else if (strcmp(key, "Next") == 0) {
        for (int row = 0; row < EDITOR_VISIBLE_ROWS - 2; row++) editor_move_vertical(1);
    } else {
        return 0;
    }
    editor_finish_movement(extend);
    return 1;
}

static size_t editor_offset_from_mouse(int x, int y) {
    int line = g_editor.first_line + (y - EDITOR_VIEW_Y) / EDITOR_LINE_HEIGHT;
    if (line < 0) line = 0;
    if (line >= editor_line_count_value()) line = editor_line_count_value() - 1;
    int column = x <= EDITOR_TEXT_X ? 0 : (x - EDITOR_TEXT_X) / EDITOR_CHAR_WIDTH;
    return editor_offset_for_column(editor_start_of_numbered_line(line), column);
}

static int editor_handle_mouse(const char* event, int dragging) {
    const char* payload = event + (dragging ? 5 : 6);
    int x = 0;
    int y = 0;
    if (sscanf(payload, "%d\t%d", &x, &y) != 2) return 0;
    if (y < EDITOR_VIEW_Y) return 0;
    if (!dragging) {
        g_editor.cursor = editor_offset_from_mouse(x, y);
        g_editor.selection_anchor = g_editor.cursor;
        g_editor.selecting = 1;
    } else if (g_editor.selecting) {
        g_editor.cursor = editor_offset_from_mouse(x, y);
    } else {
        return 0;
    }
    g_editor.preferred_column = editor_visual_column(g_editor.cursor);
    editor_keep_cursor_visible();
    editor_clear_completion();
    return 1;
}

static int editor_handle_wheel(const char* event) {
    int notches = 0;
    if (sscanf(event + 6, "%d", &notches) != 1) return 0;
    g_editor.first_line += notches > 0 ? -3 : 3;
    int maximum = editor_line_count_value() - EDITOR_VISIBLE_ROWS;
    if (maximum < 0) maximum = 0;
    if (g_editor.first_line < 0) g_editor.first_line = 0;
    if (g_editor.first_line > maximum) g_editor.first_line = maximum;
    editor_clear_completion();
    return 1;
}

void zy2_editor_dispose(void) {
    free(g_editor.data);
    free(g_editor.path);
    memset(&g_editor, 0, sizeof(g_editor));
}

int32_t zy2_editor_open(const char* path) {
    zy2_editor_dispose();
    g_editor.path = editor_copy_string(path && path[0] ? path : "main.zy");
    if (!g_editor.path || editor_reserve(256) != 0) {
        zy2_editor_dispose();
        return -1;
    }
    g_editor.data[0] = 0;
    FILE* file = editor_fopen(g_editor.path, "rb");
    if (!file) {
        snprintf(g_editor.message, sizeof(g_editor.message), "New file");
        return 0;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return -2;
    }
    long file_length = ftell(file);
    if (file_length < 0 || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return -2;
    }
    if (editor_reserve((size_t)file_length + 1) != 0) {
        fclose(file);
        return -1;
    }
    size_t read_length = fread(g_editor.data, 1, (size_t)file_length, file);
    if (ferror(file)) {
        fclose(file);
        return -2;
    }
    fclose(file);
    g_editor.length = read_length;
    g_editor.data[read_length] = 0;
    g_editor.selection_anchor = 0;
    snprintf(g_editor.message, sizeof(g_editor.message), "Opened");
    return 0;
}

int32_t zy2_editor_save(void) {
    if (!g_editor.path || !g_editor.data) return -1;
    FILE* file = editor_fopen(g_editor.path, "wb");
    if (!file) return -2;
    size_t written = fwrite(g_editor.data, 1, g_editor.length, file);
    int close_result = fclose(file);
    if (written != g_editor.length || close_result != 0) return -3;
    g_editor.dirty = 0;
    g_editor.revision++;
    snprintf(g_editor.message, sizeof(g_editor.message), "Saved");
    return 0;
}

const char* zy2_editor_text(void) { return g_editor.data ? g_editor.data : ""; }
const char* zy2_editor_path(void) { return g_editor.path ? g_editor.path : ""; }

const char* zy2_editor_status(void) {
    static char status[512];
    snprintf(
        status,
        sizeof(status),
        "%s   %s   Ln %d, Col %d   UTF-8",
        g_editor.dirty ? "Modified" : "Saved",
        g_editor.message,
        editor_line_at(g_editor.cursor) + 1,
        editor_visual_column(g_editor.cursor) + 1
    );
    return status;
}

int32_t zy2_editor_first_line(void) { return g_editor.first_line; }
int32_t zy2_editor_cursor_line(void) { return editor_line_at(g_editor.cursor); }
int32_t zy2_editor_cursor_column(void) { return editor_visual_column(g_editor.cursor); }
int32_t zy2_editor_revision(void) { return g_editor.revision; }
int32_t zy2_editor_dirty(void) { return g_editor.dirty; }
int32_t zy2_editor_line_count(void) { return editor_line_count_value(); }
int32_t zy2_editor_has_selection(void) { return editor_has_selection_value(); }

int32_t zy2_editor_selection_start_line(void) {
    size_t start = 0;
    size_t end = 0;
    editor_selection_range(&start, &end);
    (void)end;
    return editor_line_at(start);
}

int32_t zy2_editor_selection_start_column(void) {
    size_t start = 0;
    size_t end = 0;
    editor_selection_range(&start, &end);
    (void)end;
    return editor_visual_column(start);
}

int32_t zy2_editor_selection_end_line(void) {
    size_t start = 0;
    size_t end = 0;
    editor_selection_range(&start, &end);
    (void)start;
    return editor_line_at(end);
}

int32_t zy2_editor_selection_end_column(void) {
    size_t start = 0;
    size_t end = 0;
    editor_selection_range(&start, &end);
    (void)start;
    return editor_visual_column(end);
}

const char* zy2_editor_completion_text(void) { return g_editor.completion_text; }
int32_t zy2_editor_completion_count(void) { return g_editor.completion_count; }
int32_t zy2_editor_completion_selected(void) { return g_editor.completion_selected; }
int32_t zy2_editor_completion_x(void) {
    int x = EDITOR_TEXT_X + editor_visual_column(g_editor.cursor) * EDITOR_CHAR_WIDTH;
    return x > 920 ? 920 : x;
}
int32_t zy2_editor_completion_y(void) {
    int row = editor_line_at(g_editor.cursor) - g_editor.first_line + 1;
    int y = EDITOR_VIEW_Y + row * EDITOR_LINE_HEIGHT;
    int popup_height = g_editor.completion_count * 26 + 8;
    if (y + popup_height > 750) y -= popup_height + EDITOR_LINE_HEIGHT;
    return y;
}

int32_t zy2_editor_handle(const char* event) {
    if (!event || !event[0]) return 0;
    if (strcmp(event, "ctrl\ts") == 0) return zy2_editor_save() == 0 ? 1 : -1;
    if (strcmp(event, "ctrl\tq") == 0) return 2;
    if (strcmp(event, "ctrl\ta") == 0) {
        g_editor.selection_anchor = 0;
        g_editor.cursor = g_editor.length;
        g_editor.preferred_column = editor_visual_column(g_editor.cursor);
        editor_keep_cursor_visible();
        editor_clear_completion();
        return 1;
    }
    if (strncmp(event, "pickfile\t", 9) == 0) return zy2_editor_open(event + 9) == 0 ? 1 : -1;
    if (strncmp(event, "keychar\t", 8) == 0) {
        const char* value = event + 8;
        int result = editor_insert(value, strlen(value));
        editor_refresh_completion();
        return result;
    }
    if (strncmp(event, "key\t", 4) == 0) return editor_handle_key(event + 4, 0);
    if (strncmp(event, "shift\t", 6) == 0) return editor_handle_key(event + 6, 1);
    if (strncmp(event, "mouse\t", 6) == 0) return editor_handle_mouse(event, 0);
    if (strncmp(event, "drag\t", 5) == 0) return editor_handle_mouse(event, 1);
    if (strncmp(event, "release\t", 8) == 0) {
        g_editor.selecting = 0;
        return 1;
    }
    if (strncmp(event, "wheel\t", 6) == 0) return editor_handle_wheel(event);
    return 0;
}
