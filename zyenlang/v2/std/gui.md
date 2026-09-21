# `std::gui`

`std::gui` is the cross-platform retained/drawing facade backed by the bundled
native GUI runtime. It does not start Python or Tk. Windows, Linux, and macOS
use the same ZyenLang API.

The ARC classes are `Application`, `Panel`, `Label`, `Button`, `ButtonGroup`,
and `Column`. Create an `Application` through the module, then
create widgets through that Application. Each widget retains its target
Application, so `draw()` has an explicit owner instead of relying on an
implicit current window.

```zy
import std::gui as gui
import std::io as io

fn clicked() void {
    io::print("clicked")
}

fn main() i32 {
    let app = gui::application_colored("Widgets", 800, 480, "#20201F")
    let button = app.button(40, 48, 220, 48, "Save", clicked)
    if app.open() != 0 { return 1 }

    while true {
        if app.begin_frame() != 0 { break }
        button.draw()
        if app.present() != 0 { break }
        let event = app.next_event(16)
        button.handle(event)
        if event == "quit" { break }
    }
    return app.close()
}
```

Application drawing methods are `line`, `rect`, `circle`, `text`, `code_view`,
`code_editor`, and `completion`. Colors use `#RRGGBB`. Event parsing helpers
are `event_kind`, `event_x`, and `event_y`. Button callbacks have type
`fn() void`; closures use the same `ZL_Function` representation as all other
function values.

Raylib supports one process window in the current backend. A second
Application cannot open concurrently. Close the Application explicitly or
register `defer app.close()` after a successful open. Widgets tied to a closed
Application return an error from drawing instead of drawing into another
window. The final Application reference also closes an open native session
during ARC cleanup. Platform display and graphics runtime requirements still
apply.
