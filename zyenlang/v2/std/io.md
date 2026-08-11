# std/io 使用說明

```zy
import <std/io> as io

io.print("normal output")
io.eprint("error output")
```

- `print(value: str)` 以白色寫入終端並換行。
- `eprint(value: str)` 以紅色寫入終端並換行。

兩者都是 ZyenLang facade，底層分別呼叫 `PRINT_CMD__(value, "#FFFFFF")` 與
`PRINT_CMD__(value, "#ff0000")`。也可以直接使用任意 `#RRGGBB` 顏色：

```zy
PRINT_CMD__("ember", "#D97757")
```

兩者只接收 `str`。數字與 bool 可用 `(str)value` 或 f-string：

```zy
let count: i32 = 42
io.print(f"count={count}")
```

重新導向或被測試工具捕捉時不輸出 ANSI 色碼。`NO_COLOR` 關閉顏色；
`ZYEN_COLOR=always` 或 `ZYEN_COLOR=never` 可明確控制。
