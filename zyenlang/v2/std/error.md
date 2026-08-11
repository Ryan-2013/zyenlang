# std/error 使用說明

`std/error` 提供一個小型前置條件 helper：

```zy
import <std/error> as error

fn divide(a: i32, b: i32) i32 throws Error {
    error.require(b != 0, "division by zero")
    return a / b
}
```

`require(condition, message) throws Error` 在條件為 false 時執行 `stop message`。
呼叫端必須傳遞 `throws Error`，或使用 `catch err { ... }`。`Error` 欄位包含
`message`、`file`、`line`、`column`；未捕捉錯誤會以紅字輸出精確來源位置後結束。
