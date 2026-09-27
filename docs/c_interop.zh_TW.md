# c_module：C Interop ABI v3

[English](c_interop.md) | **繁體中文**

`std::c_module` 會在編譯期把可審查的 `.zlcm.h` 模板展開成具名 native
namespace。它不會把 C 轉成 ZyenLang、不啟動 Python，也不在執行期解析模板。
模板指定的 adapter 與 C source 會和產生的 C11 程式一起編譯及連結。

## ZyenLang 介面

```zy
import std::c_module as c

native module graphics = c::load("native/graphics.zlcm.h")

fn main() i32 {
    let window: graphics::Window = graphics::open("Demo", 800, 480) catch err {
        return 1
    }
    let pixels: List<u8> = [0, 1, 2]
    graphics::upload(window, c::slice(&pixels))
    return 0
}
```

`native module` 只能寫在 module 頂層。模板路徑必須是相對於目前 `.zy`
檔案、位於 package 內的字串常值。型別、函式與常量使用
`graphics::Window`、`graphics::open`、`graphics::DEFAULT_WIDTH`；native
namespace 不是物件，不能寫 `graphics.open()`。

## ABI v3 模板

```c
ZLC_ABI(3)
ZLC_MODULE(graphics)
ZLC_SOURCE("graphics_zy.c")

ZLC_HANDLE(Window, ZLC_DROP(zy_window_drop))
ZLC_ENUM(WindowMode, i32,
    ZLC_CASE(windowed, 0),
    ZLC_CASE(fullscreen, 1))
ZLC_FLAGS(WindowFlags, u32,
    ZLC_CASE(resizable, 4),
    ZLC_CASE(high_dpi, 8))
ZLC_CONST(DEFAULT_WIDTH, i32, 800)

ZLC_FN(open, zy_window_open, optional<owned<Window>>,
    ZLC_PARAM(title, str),
    ZLC_PARAM(width, i32),
    ZLC_PARAM(height, i32),
    ZLC_FAIL(null, zy_window_last_error))
```

### Handle 所有權

- `owned<T>`：回傳一個獨立擁有的 ARC Handle。
- `borrowed<T>`：只在這次呼叫期間借用。
- `borrowed<T, parent>`：回傳 view，並由 ARC 保留指定 parent。
- `borrowed_static<T>`：C 保證 process-lifetime。
- `consumed<T>`：轉移 payload，所有 ZyenLang alias 同時失效。
- `optional<T>`：C `NULL` 對應 `T | null`。

adapter 使用 `zl_handle_adopt`、`zl_handle_borrow` 或
`zl_handle_borrow_from`。Handle control block 採用 atomic refcount；最後一個
引用離開時 destructor 只執行一次。`DROP__(handle)` 只釋放目前 binding。
錯誤型別、NULL 或已 consumed 的 Handle 會先產生明確 runtime error。

### Slice、out 與錯誤

`c::slice(&list)` 建立唯讀 `Slice<T>`；`c::mut_slice(&mut list)` 建立唯一
可變 `MutSlice<T>`，並先執行 List copy-on-write 分離。Slice 只能直接傳給
同步 native call，不可保存、回傳、capture 或跨執行緒。

`out<T>` 在 ZyenLang 呼叫端省略並加入回傳值；`inout<T>` 使用 `&mut T`。
`ZLC_FAIL` 支援 `null`、`nonzero`、`negative` 與 `equal(value)`，只有標記的
函式會生成 `throws Error`。訊息函式簽章是 `ZL_String fn(void)`。

## Bindgen

```text
zy bindgen vendor/library.h --module graphics --out-dir native \
    -I vendor/include --library raylib
```

bindgen 使用 Clang JSON AST，不用 regex 猜 C 宣告。它輸出
`.zybind.toml`、`.zlcm.h` 與 `_zy.c/.h`。模糊 pointer 會維持 disabled 並留下
TODO；只有設定 ownership、長度及錯誤規則後才會成為可呼叫 binding。手動修改
過的生成檔預設不會被覆寫，除非明確使用 `--force`。

`ZLC_SOURCE` 可使用 `.c`、`.cc`、`.cpp`、`.cxx`。C 使用 C11，C++ 使用
C++17，混合專案最後由 C++ linker 連結；adapter 對 ZyenLang 匯出的符號應寫
成 `extern "C"`。可用 `ZY_CC`／`ZY_CFLAGS`、`ZY_CXX`／`ZY_CXXFLAGS` 與
`ZY_LDFLAGS` 指定工具鏈參數。

## ABI v2 相容

舊的 `let value: c::Module = c::load("...")` 暫時保留，但每次編譯只會顯示
一次棄用警告。新 wrapper 應使用 ABI v3 `native module`。完整 macro、callback、
build metadata 與安全邊界說明見 [英文文件](c_interop.md)。
