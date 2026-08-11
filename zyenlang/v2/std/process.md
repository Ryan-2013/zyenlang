# std/process 使用說明

```zy
import <std/process> as process

fn main() i32 {
    let args: List<str> = process.args(GET_ARGS__)
    let executable: str = process.executable(GET_EXE__)
    let source: str = FILE__
    return 0
}
```

- `GET_ARGS__` 是不含執行檔名稱的命令列參數，只能直接出現在 `main`。
- `GET_EXE__` 是作業系統提供的 `argv[0]`，同樣只在 `main` 可用。
- `FILE__` 是寫下該表達式的 `.zy` 模組絕對路徑，可在任何函式使用。

`args` 與 `executable` 是普通 facade，不享有標準庫特權；特殊值由呼叫端明確傳入。
這些值借用 process-lifetime 資料，`List` 在第一次修改時採 copy-on-write。
