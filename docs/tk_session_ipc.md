# std/tk native session

This filename is retained for old links. The file-based Python/Tk IPC protocol
has been removed from the active `std/tk` session implementation.

## Runtime model

- `tk.session_open(...)` starts a Win32/GDI window thread inside the generated
  native executable.
- `tk.session_begin_frame()` clears an in-memory command buffer.
- Drawing functions append commands to that buffer without opening files.
- `tk.codeview_text(...)` copies visible editor text directly into the frame.
- `tk.session_redraw()` atomically swaps the completed frame and invalidates the
  native window.
- Window events enter a bounded C ring buffer. `session_next_event(timeout_ms)`
  waits on a Windows event object rather than polling.
- `tk.session_pickdir()` posts a native folder-picker request to the window
  thread and returns the selected path as a `pickdir` event.

The `session_dir` argument remains in the public function signature for source
compatibility, but the native backend ignores it. No `state.txt`, `scene.ztk`,
`events.txt`, or `request.txt` files are created by session mode.

The older scene-file API used by `tk.begin(...)` plus `tk.show(...)` remains a
separate compatibility path for one-shot scenes. The IDE does not use it.
