# std/gui 使用說明

`std/gui` 是 ZyenLang v2 的跨平台 GUI 與繪圖模組。底層使用發行版內的
raylib native backend，不啟動 Python 或 Tk。Windows、Linux 與 macOS 使用同一套
ZyenLang API。

## Retained widgets

第一層元件包含 `Application`、`Panel`、`Label`、`Button`、`ButtonGroup` 與 `Column`。
元件是普通、強型別的 struct，可以保存、傳參或組合；`draw()` 負責呈現，
`Button.handle(event)` 負責命中測試並呼叫 `fn() void` callback。
`ButtonGroup` 直接以 `List<Button>` struct field 保存元件，提供 `add`、`draw`、
`handle`；後兩者因 checked List indexing 而宣告 `throws Error`。

```zy
import <std/gui> as gui
import <std/io> as io

fn clicked() void {
    io.print("clicked")
}

fn main() i32 {
    let app = gui.application_colored("Widgets", 800, 480, "#20201F")
    if app.open() != 0 {
        return 1
    }
    let layout = gui.column(40, 48, 220, 54, 14)
    let heading = layout.label_at(0, "Settings")
    let save = layout.button_at(1, "Save", clicked)
    let buttons = gui.button_group()
    buttons.add(save)
    let running = true
    while running {
        if app.begin_frame() != 0 {
            break
        }
        heading.draw()
        let draw_result = buttons.draw() catch err {
            io.eprint(err.message)
            return 2
        }
        if draw_result != 0 {
            return draw_result
        }
        if app.present() != 0 {
            break
        }
        let event = app.next_event(0)
        if event == "quit" {
            running = false
        } else if event != "" {
            let handled = buttons.handle(event) catch err {
                io.eprint(err.message)
                return 3
            }
        }
    }
    return app.close()
}
```

工廠函式：

```text
application(title, width, height) Application
application_colored(title, width, height, background) Application
panel(x, y, width, height) Panel
label(x, y, value) Label
button(x, y, width, height, label, on_click) Button
button_group() ButtonGroup
column(x, y, width, row_height, gap) Column
```

`Application` 提供 `open`、`begin_frame`、`present`、`next_event`、`close`。
`Panel`、`Label`、`Button` 提供 `draw()`；`Button` 另有 `contains(x, y)` 與
`handle(event)`。`Column.item_y(index)` 計算列位置，`button_at` 與 `label_at`
建立對齊後的元件。

目前 callback 是已命名的頂層函式，不是 closure；應用程式仍明寫 frame/event
loop。這保留可預測的每幀成本，也讓遊戲與工具可以混用 retained widget 和即時繪圖。

## Window 與低階繪圖

`window`、`window_colored` 直接建立 `Window`。其生命週期和 `Application`
相同。每一幀在 `begin_frame()` 與 `present()` 之間呼叫：

```text
line(x1, y1, x2, y2, color, width) i32
rect(x, y, width, height, color) i32
circle(x, y, radius, color) i32
text(x, y, value, color, size) i32
```

顏色使用 `"#RRGGBB"`。繪圖函式成功時回傳 `0`。

## 事件

`next_event(timeout_ms)` 回傳 allocation-free 的借用字串；沒有事件時是空字串，
關閉視窗時是 `"quit"`。其他事件以 tab 分隔，可用 `event_kind`、`event_x`、
`event_y` 解析。

| kind | 事件 |
|---|---|
| `1` | mouse / ctrl_mouse |
| `2` | release |
| `3` | motion |
| `4` | drag |
| `5` | wheel |

## Editor rendering

`code_view`、`code_editor` 與 `completion` 是 IDE 使用的高密度繪圖 API。
一般應用程式通常不需要直接呼叫。完整範例見
`examples/v2_gui_basic.zy` 與 `examples/v2_gui_button.zy`。
