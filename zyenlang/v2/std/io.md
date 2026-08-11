# std/io 使用說明

```zy
import <std/io> as io

io.print("normal output")
io.eprint("error output")
```

- `print(value: str)` 寫入 stdout 並換行。
- `eprint(value: str)` 寫入 stderr 並換行；互動終端預設顯示紅色。

兩者只接收 `str`。數字與 bool 可用 `(str)value` 或 f-string：

```zy
let count: i32 = 42
io.print(f"count={count}")
```

重新導向或被測試工具捕捉時不輸出 ANSI 色碼。`NO_COLOR` 關閉顏色；
`ZYEN_COLOR=always` 或 `ZYEN_COLOR=never` 可明確控制。
