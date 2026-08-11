# std/gui 使用說明

`std/gui` 是 ZyenLang v2 的跨平台即時繪圖模組。底層使用隨發行版提供的
raylib，不會啟動 Python 或 Tk。相同的 `.zy` 程式可在 Windows、Linux 與
macOS 編譯。

## 最小程式

```zy
import <std/gui> as gui
import <std/thread> as thread

fn main() i32 {
    let window = gui.window_colored("My game", 800, 480, "#20201F")
    if window.open() != 0 {
        return 1
    }

    let running: bool = true
    while running {
        if window.begin_frame() != 0 {
            break
        }

        gui.text(40, 32, "Hello ZyenLang", "#F4F1EA", 28)
        gui.rect(40, 88, 240, 80, "#D97757")
        gui.line(40, 190, 280, 190, "#7AC7B7", 3)
        gui.circle(360, 128, 40, "#79A8D8")

        if window.present() != 0 {
            break
        }

        let event: str = window.next_event(0)
        if event == "quit" {
            running = false
        }
        thread.sleep_ms(16)
    }

    return window.close()
}
```

執行：

```powershell
zy run main.zy --release
```

## Window

建立視窗有兩種方式：

```zy
let normal = gui.window("title", 800, 480)
let colored = gui.window_colored("title", 800, 480, "#20201F")
```

`Window` 的公開欄位是 `title`、`width`、`height`、`background` 與 `fps`。
方法必須依下列生命週期呼叫：

1. `window.open()` 建立 native 視窗，成功回傳 `0`。
2. 每一幀先呼叫 `window.begin_frame()`，它也會清成背景色。
3. 呼叫 `gui.line`、`gui.rect`、`gui.circle` 或 `gui.text` 建立該幀內容。
4. `window.present()` 顯示完整畫面並收集輸入事件。
5. `window.next_event(timeout_ms)` 取得下一個事件；沒有事件時回傳空字串。
6. 離開迴圈後呼叫 `window.close()`。

## 繪圖函式

```text
gui.line(x1, y1, x2, y2, color, width) i32
gui.rect(x, y, width, height, color) i32
gui.circle(x, y, radius, color) i32
gui.text(x, y, value, color, size) i32
```

座標與尺寸使用 `i32`，顏色使用 `"#RRGGBB"` 字串。所有繪圖函式成功時
回傳 `0`。繪圖是 immediate mode：每一幀都要重新送出想顯示的內容。

## 事件

關閉視窗會產生 `"quit"`。其他事件是以 tab 分隔的字串，例如滑鼠按下、
移動、拖曳與滾輪。`gui.event_kind(event)` 可辨識座標事件：

| 回傳值 | 事件 |
|---|---|
| `1` | mouse / ctrl_mouse |
| `2` | release |
| `3` | motion |
| `4` | drag |
| `5` | wheel |

使用 `gui.event_x(event)` 與 `gui.event_y(event)` 取得座標。鍵盤事件目前提供給
編輯器層使用；一般遊戲的高階鍵盤、按鈕與資源 API 仍待後續封裝。

## 編輯器元件

`gui.code_view`、`gui.code_editor` 與 `gui.completion` 是 ZyenLang IDE 使用的
低階繪圖函式。一般視窗或遊戲不需要呼叫它們。

可直接執行的版本位於 `examples/v2_gui_basic.zy`。
