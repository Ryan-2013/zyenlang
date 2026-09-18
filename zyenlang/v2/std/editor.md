# std/editor 使用說明

`std/editor` 是 ZyenLang IDE 使用的 UTF-8 文件核心。它維護單一 process-wide
文件狀態，並使用普通 `native source "editor_native.c"` 宣告接入 C；沒有標準庫特權。

```zy
import std::editor as editor

let status: i32 = editor::open("main.zy")
let source: str = editor::text()
```

主要命令：

```text
open(path) i32       save() i32       dispose() void
text() str           path() str       status() str
handle(event) i32    revision() i32   dirty() i32
```

位置與選取查詢包含 `first_line`、`cursor_line`、`cursor_column`、`line_count`、
`has_selection` 及四個 `selection_*` 函式。補全查詢包含 `completion_text`、
`completion_count`、`completion_selected`、`completion_x`、`completion_y`。

回傳字串會轉成 ZyenLang 的 ARC `str`。`handle(event)` 接受 `std::gui` 的事件字串，支援鍵盤 repeat、滑鼠拖曳選取與
補全狀態更新。
