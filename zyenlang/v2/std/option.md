# std/option 使用說明

`T | null` 是語言內建的受限 optional 型別；`std/option` 提供可讀性 helper：

```zy
import <std/option> as option

let name: str | null = null
if option.is_null(name) {
    // empty
}
```

- `is_null<T>(value: T | null) bool`
- `is_some<T>(value: T | null) bool`

要取出非 null 值，使用 `if let value = optional { ... }`。普通 `(str)optional`
不會偷偷忽略 null；f-string 會把 null 格式化為 `"null"`。
