# std/tk cross-platform native session

This filename is retained for old links. The file-based Python/Tk IPC protocol
has been removed from the active `std/tk` session implementation.

## Runtime model

- `tk.session_open(...)` opens a raylib 6.0 window inside the generated native
  executable on Windows, Linux, or macOS.
- `tk.session_begin_frame()` clears an in-memory command buffer.
- Drawing functions append commands to that buffer without opening files.
- `tk.codeview_text(...)` copies visible editor text directly into the frame.
- `tk.session_redraw()` swaps the completed frame and renders it with raylib.
- Window events enter a bounded C ring buffer. `session_next_event(timeout_ms)`
  pumps native events and waits in C without a Python process.
- `tk.session_pickdir()` uses the platform folder picker when available and
  returns the selected path as a `pickdir` event.

The `session_dir` argument remains in the public function signature for source
compatibility, but the native backend ignores it. No `state.txt`, `scene.ztk`,
`events.txt`, or `request.txt` files are created by session mode.

The older scene-file API used by `tk.begin(...)` plus `tk.show(...)` remains a
separate compatibility path for one-shot scenes. Both paths use the same
cross-platform C renderer; neither path launches Python/Tk.
