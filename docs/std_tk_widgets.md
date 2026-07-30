# std/tk widgets

`std/tk` has two layers:

- The canvas layer keeps the existing `rect`, `circle`, `text`, `image`, and
  native session functions.
- The widget layer provides `FONT`, `Window`, `Text`, `Button`, and `Renderer`.

Both layers use the in-process raylib backend on Windows, Linux, and macOS.
The widget layer never creates or reads a `.ztk` file. The old
`begin`/`open`/`show` scene API keeps `.ztk` only for source compatibility.

`std/tk` has no compiler-only native privilege. Its ZyenLang source imports
`std/c_module` and loads `tk.zlcm.h`, the same public template format available
to third-party packages. That template declares `tk_native.h`, `tk_native.c`,
every `zl_tk_*` function, and the Linux-only `dl` dependency. The compiler
contains no hard-coded `std/tk` C prototypes or source paths.

## Complete example

```zy
import <std/tk>;

fn button_click() -> void {
    print("hi");
}

fn main() -> int {
    let font: tk.FONT = tk.load_font("");
    let window: tk.Window = tk.Window { title: "ZyenLang widgets", width: 640, height: 360, background: "#17191d" };
    let heading: tk.Text = tk.Text { text: "Hello from ZyenLang.", x: 32, y: 34, size: 28, font: font };
    let button: tk.Button = tk.Button { text: "Say hi", x: 32, y: 104, width: 160, font: font, if_click: button_click };
    let ui: tk.Renderer = tk.Renderer { window: window, children: [heading, button] };
    return ui.run();
}
```

ZyenLang struct construction uses braces, so the canonical button form is
`tk.Button { ..., if_click: button_click }`. Omitted fields use their declared
defaults. The callback has exact type `fn()->void` and may be a named function
or a closure.

## Manual frame loop

Use `run()` for a complete application loop, or call `frame()` when game or
tool state must update once per frame:

```zy
for (;;) {
    if (!ui.frame()) {
        break;
    }
}
ui.close();
```

`frame()` opens the window on its first call, builds one in-memory command
buffer, renders it, drains pending native events, updates button state, and
invokes click callbacks. `Window.fps` defaults to 60; use `0` for an uncapped
loop.

## Types and defaults

- `FONT`: `path = ""`, `base_size = 32`. An empty path selects raylib's
  default font. `load_font(path, base_size)` creates a descriptor; a custom
  font is loaded lazily after the native window exists and cached until close.
- `Window`: title, dimensions, background, and target FPS.
- `Text`: text, position, color, size, font, and visibility.
- `Button`: text, bounds, colors, font, `if_click`, visibility, enabled state,
  hover state, and pressed state.
- `Renderer`: one `Window`, a heterogeneous `children: List`, and session
  state.

`Renderer.children` uses ZyenLang structural List dispatch. A user struct can
be rendered alongside built-in widgets when it implements these exact methods:

```zy
fn draw() -> void
fn handle_event(kind: int, x: int, y: int) -> void
```

Mouse event kinds are `1` press, `2` release, `3` motion, `4` drag, and `5`
wheel. The low-level `event_kind`, `event_x`, and `event_y` helpers parse native
session event strings without allocating a List or launching another process.

## Performance model

- There is no Python or Tk process.
- Widget objects are ordinary ZyenLang structs.
- Function callbacks use the shared `ZL_Function` ABI and ARC ownership.
- Frames use C memory buffers and events use a bounded C ring buffer.
- Images and custom fonts are cached native resources and unloaded when the
  session closes.
- `.ztk` file I/O exists only in the legacy one-shot scene API.

See `examples/tk_widgets.zy` for the runnable example.
