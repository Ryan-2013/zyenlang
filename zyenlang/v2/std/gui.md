# `std::gui`

`std::gui` is the cross-platform retained/drawing facade backed by the bundled
native GUI runtime. It does not start Python or Tk. Windows, Linux, and macOS
use the same ZyenLang API.

The ARC classes are `Window`, `Application`, `Panel`, `Label`, `Button`,
`ButtonGroup`, and `Column`. Create them through module functions such as
`window`, `application`, `panel`, `label`, `button`, `button_group`, and
`column`; call behavior with instance `.` methods.

```zy
import std::gui as gui
import std::io as io

fn clicked() void {
    io::print("clicked")
}

fn main() i32 {
    let app = gui::application_colored("Widgets", 800, 480, "#20201F")
    let button = gui::button(40, 48, 220, 48, "Save", clicked)
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

Drawing module functions are `line`, `rect`, `circle`, `text`, `code_view`,
`code_editor`, and `completion`. Colors use `#RRGGBB`. Event parsing helpers
are `event_kind`, `event_x`, and `event_y`. Button callbacks have type
`fn() void`; closures use the same `ZL_Function` representation as all other
function values.

Window ownership is process-global in the current backend. Close it explicitly
or register `defer app.close()` after a successful open. Platform display and
graphics runtime requirements still apply.
