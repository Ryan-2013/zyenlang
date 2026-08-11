# std/thread 使用說明

```zy
import <std/thread> as thread

thread.sleep_ms(16)
thread.yield_now()
let cores: i32 = thread.cpu_count()
```

- `sleep_ms(milliseconds)` 暫停目前 OS thread。
- `yield_now()` 把執行權交還 scheduler。
- `cpu_count()` 回報可用的邏輯 CPU 數，至少為 1。

語言層並行使用 `spawn call()` 與 `await task`，不透過此模組建立 thread。
目前 `Task<T>` 是線性值：在同一 lexical scope 恰好 await 一次，且 worker 只能是
零參數、不拋錯、回傳 scalar/bool/void 的直接函式或 method call。
