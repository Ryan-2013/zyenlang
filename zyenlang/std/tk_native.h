#ifndef ZYENLANG_TK_NATIVE_H
#define ZYENLANG_TK_NATIVE_H

int zl_tk_begin(const char* path);
int zl_tk_open(const char* title, int width, int height);
int zl_tk_window(const char* title, int width, int height);
int zl_tk_bg(const char* color);
int zl_tk_clear(const char* color);
int zl_tk_line(int x1, int y1, int x2, int y2, const char* color, int width);
int zl_tk_rect(int x, int y, int width, int height, const char* color);
int zl_tk_rect_outline(int x, int y, int width, int height, const char* color, int line_width);
int zl_tk_circle(int x, int y, int radius, const char* color);
int zl_tk_circle_outline(int x, int y, int radius, const char* color, int line_width);
int zl_tk_text(int x, int y, const char* text, const char* color, int size);
int zl_tk_codeview(int x, int y, int width, int height, int first_line, int line_height, int char_width, int size, int stamp, const char* lines_path);
int zl_tk_codeview_text(int x, int y, int width, int height, int first_line, int line_height, int char_width, int size, int stamp, const char* lines);
int zl_tk_image(const char* path, int x, int y);
const char* zl_tk_script(void);
int zl_tk_show(void);
int zl_tk_show_for(int ms);

int zl_tk_session_open(const char* title, int width, int height, const char* session_dir);
int zl_tk_session_begin_frame(void);
int zl_tk_session_redraw(void);
const char* zl_tk_session_next_event(int timeout_ms);
int zl_tk_session_pickdir(void);
int zl_tk_session_close(void);
int zl_tk_session_char_w(void);
int zl_tk_session_line_h(void);

#endif
