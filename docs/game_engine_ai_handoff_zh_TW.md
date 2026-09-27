# 給遊戲引擎實作 AI 的 ZyenLang 工作說明

這份文件可以直接交給負責設計或實作遊戲引擎的 AI。目標是讓它正確使用
ZyenLang 0.3.1，而不是從 C、C++、Go、Rust 或 ZyenLang 0.1/0.2 的習慣猜語法。

本文描述已實作的能力。若文件、範例與編譯器診斷不同，以目前安裝版本的
`zy --version`、`zy check` 和編譯器診斷為準。

## 給接收端 AI 的任務

你正在為 ZyenLang 製作遊戲引擎或引擎相容層。請遵守以下邊界：

1. 遊戲規則、scene、world、UI 流程和 component 更新可以使用 ZyenLang。
2. window、renderer、audio、input、GPU resource 和 physics 核心可以使用
   C、C++ 或 Rust，但必須提供穩定的 C11 façade。
3. ZyenLang 與 native engine 之間使用 C Interop ABI v3，不可暴露 C++ class、
   template、exception、STL layout 或 Rust ABI。
4. 不要修改 ZyenLang compiler 來遷就引擎內部 layout；轉接工作放在 C adapter。
5. 每個 pointer、resource、buffer 和 callback 都必須有明確 ownership 與生命週期。
6. 先完成可驗證的最小引擎，再逐步加入 renderer、asset、audio、physics 和工具。

推薦架構：

```text
ZyenLang game
    -> native module namespace
    -> engine_zy.c C adapter
    -> stable engine C API
    -> C/C++/Rust engine internals
```

ZyenLang 也能產生 C source、static library 或 shared library，讓既有引擎反向
呼叫遊戲邏輯。這個方向使用 `#name` 產生穩定 C wrapper。

## 語言最小規則

```zy
import std::io as io

fn main() i32 {
    io::print("hello from ZyenLang")
    return 0
}
```

- 原始碼是 UTF-8，副檔名為 `.zy`。
- statement 由換行結束，不寫分號。
- 使用 `//` 註解與 `{}` 區塊。
- 執行檔入口為 `fn main() i32`。
- `let` 建立 lexical binding，離開 scope 後自動清理 managed value。
- module、type 和 static function 使用 `::`。
- instance field 和 class method 使用 `.`。
- `io::print` 只接受 `str`；數值使用 f-string 或 `(str)value`。
- 目前沒有 `for`，索引迴圈使用 `while`。

基礎型別：

```text
i8 i16 i32 i64
u8 u16 u32 u64
isize usize
f32 f64 bool str void Error
```

```zy
let health: i32 = 100
let delta: f32 = 0.016
let title: str = "Demo"
let running: bool = true

while running {
    health -= 1
    if health <= 0 {
        running = false
    }
}
```

混合數值運算會提升到共同型別，例如 `i32 + f64 -> f64`。縮窄轉型必須明確
寫出，例如 `(i16)value`。

## Module 與專案

一個 `.zy` 檔案是一個 module：

```zy
import std::io as io
import crate::components as components
import physics

fn update_frame() void {
    let body: components::Body = components::create()
    physics::step()
    io::print("frame")
}
```

- `std::name` 是標準庫。
- `crate::path` 對應目前 package 的 `src/path.zy`。
- dependency alias 可直接作為 import root。
- import alias 是 namespace，不是普通變數。
- 跨 module API 必須標示 `public`。
- 不使用字串 import、wildcard import、selective import 或 re-export。

建議專案：

```text
my-game/
  zyproject.toml
  src/
    main.zy
    game.zy
    components.zy
    native/
      engine.zlcm.h
      engine_zy.c
      engine_zy.h
  assets/
  tests/
```

```toml
[package]
name = "my-game"
version = "0.1.0"
zyen = ">=0.3.1"

[build]
default-target = "game"
target-dir = "target"

[targets.game]
kind = "bin"
entry = "src/main.zy"
output-name = "my-game"

[dependencies]
```

常用命令：

```powershell
zy check game
zy run game -- player-name
zy build game
zy build game --release
zy test
zy doctor
zy metadata
zy clean game
```

## Struct、class 與遊戲狀態

`struct` 是純資料值型別，適合 component、座標、render command 和事件：

```zy
public struct Transform {
    public x: f32
    public y: f32
    public rotation: f32 = 0.0
}

public fn moved(value: Transform, dx: f32, dy: f32) Transform {
    return Transform{
        x: value.x + dx,
        y: value.y + dy,
        rotation: value.rotation,
    }
}
```

struct 不能定義 method、`init` 或 `deinit`。行為放 module function。純資料
struct 直接複製；含 `str`、`List<T>`、class 或函式值時，編譯器會產生遞迴
retain/release 與 cleanup。

`class` 有共享身份、method、封裝與 atomic ARC，適合 world、scene、asset manager
和 UI controller：

```zy
public class World {
    private transforms: List<Transform>

    public init() {
        this.transforms = []
    }

    public mut fn spawn(x: f32, y: f32) void {
        LIST_PUSH__(this.transforms, Transform{x: x, y: y})
    }

    public fn count() usize {
        return LIST_LEN__(this.transforms)
    }

    deinit {
        // 最後一個 ARC reference 消失時執行
    }
}
```

- `fn` 取得唯讀 `this`。
- `mut fn` 才能修改 field。
- `static fn` 使用 `Type::function()` 呼叫。
- `Type(arguments)` 呼叫 `init`。
- class assignment 複製 ARC reference，不複製完整 object。
- class 沒有 inheritance、interface、trait 或 cycle collector。
- ARC count 是 atomic，不代表 object payload 自動 thread-safe。

## List、函式值與 Error

`List<T>` 是編譯器內建的強型別、ARC backing buffer、copy-on-write 容器：

```zy
let values: List<i32> = [10, 20]
LIST_PUSH__(values, 30)
let count: usize = LIST_LEN__(values)
let first: i32 = LIST_GET__(values, 0) catch err {
    recover 0
}
LIST_SET__(values, 1, 42) catch err {
    recover
}
```

大量 sprite、vertex、pixel 或 command 應先收集到 `List<T>`，再用 Slice 一次
送入 native renderer，避免每個物件跨一次 ABI。

函式值與 closure：

```zy
fn add(a: i32, b: i32) i32 {
    return a + b
}

fn pick() fn(i32, i32) i32 {
    return add
}

let operation: fn(i32, i32) i32 = pick()
let answer: i32 = pick()(20, 22)

let amount: i32 = 2
let callback: fn(i32) i32 = fn(value: i32) i32 {
    return value + amount
}
```

函式值底層是 `{call, env, owner, signature}`，closure environment 由 ARC 管理。
C 若保存 callback，adapter 必須 retain；替換或解除註冊時必須 release。

錯誤：

```zy
fn load_level(path: str) str throws Error {
    if path == "" {
        stop "empty level path"
    }
    return path
}

let level: str = load_level("level01") catch err {
    io::eprint(err.message)
    recover "fallback"
}
```

`recover value` 必須符合原 expression 型別。void expression 可使用裸
`recover`。不要把 `Error` 直接 cast 成字串；使用 `err.message`。

## Reference 與生命週期

```zy
let value: i32 = 10

{
    let read: &i32 = &value
    let copy: i32 = CLONE_REF__(read)
}

{
    let write: &mut i32 = &mut value
    REF_SET__(write, 20)
}
```

- `&T` 是唯讀借用，可同時存在多個。
- `&mut T` 是唯一可變借用。
- reference 不可為 null、不擁有 pointee。
- 禁止 nested reference。
- reference 不能存進 struct/class/List、不能回傳、capture 或跨執行緒。
- 一般 ZyenLang 程式不使用裸 pointer arithmetic。
- `DROP__(local)` 可提前執行正常清理並使 binding 暫時不可用。
- `defer function_call()` 會在 scope exit 時依 LIFO 執行。

GPU、window、texture、audio stream 等 native resource 使用 ABI v3 Handle，不使用
ZyenLang reference 假裝擁有原生資源。

## ZyenLang 呼叫引擎

ZyenLang 使用 ABI v3 native module：

```zy
import std::c_module as c
import std::io as io

native module engine = c::load("native/engine.zlcm.h")

fn main() i32 {
    let app: engine::Engine = engine::create("Demo", 1280, 720) catch err {
        io::eprint(err.message)
        return 1
    }

    let commands: List<u8> = [0, 1, 2, 3]
    engine::submit(app, c::slice(&commands))
    return 0
}
```

`native module` 必須位於 module 頂層，模板路徑是 package 內的字串常值。
namespace 成員使用 `engine::create`，不能寫 `engine.create`。

最小 `.zlcm.h`：

```c
ZLC_ABI(3)
ZLC_MODULE(engine)
ZLC_SOURCE("engine_zy.c")

ZLC_HANDLE(Engine, ZLC_DROP(zy_engine_drop))
ZLC_CONST(DEFAULT_WIDTH, i32, 1280)

ZLC_FN(create, zy_engine_create, optional<owned<Engine>>,
    ZLC_PARAM(title, str),
    ZLC_PARAM(width, i32),
    ZLC_PARAM(height, i32),
    ZLC_FAIL(null, zy_engine_last_error))

ZLC_FN(submit, zy_engine_submit, void,
    ZLC_PARAM(engine, borrowed<Engine>),
    ZLC_PARAM(commands, Slice<u8>))
```

Handle ownership：

- `owned<T>`：新的擁有引用，最後一個 alias 離開時執行 destructor。
- `borrowed<T>`：只在本次呼叫期間借用。
- `borrowed<T, parent>`：view 會透過 ARC 保留 parent。
- `borrowed_static<T>`：process-lifetime。
- `consumed<T>`：轉移 payload，所有 ZyenLang alias 失效。
- `optional<T>`：C NULL 對應 `T | null`。

`c::slice(&list)` 建立同步唯讀 Slice；`c::mut_slice(&mut list)` 建立唯一可變
Slice 並先完成 List COW。Slice 不可保存、回傳、capture 或跨執行緒。

可先用 bindgen 建立可審核骨架：

```powershell
zy bindgen vendor/engine.h --module engine --out-dir src/native -I vendor/include
```

模糊 pointer 必須保留 TODO 並維持 disabled，直到 ownership、長度、nullable 與
錯誤規則被人工確認。不要用 regex 猜 C prototype。

## 引擎呼叫 ZyenLang

在函式上一行寫 `#C_symbol`，C backend 會保留內部 mangled implementation，
並產生名稱固定的公開 wrapper：

```zy
#game_api_version
fn api_version() u32 {
    return 1
}

#game_update
fn update(delta_seconds: f32) i32 {
    return 0
}

#game_shutdown
fn shutdown() void {
}
```

概念上的輸出：

```c
static int32_t zy2_fn_update(float delta_seconds) {
    /* ZyenLang implementation */
}

ZYENLANG_API int32_t game_update(float delta_seconds) {
    return zy2_fn_update(delta_seconds);
}
```

`#name` 只有 C wrapper 這個作用：

- 不改變函式在 ZyenLang module 裡的可見性。
- 不是一般 macro 或 attribute system。
- marker 必須單獨一行並直接標記 top-level function。
- 不支援 generic、`throws Error`、class、List、reference、closure 或 Handle。
- 支援固定寬度數字、`bool`、`void` 與 `ZL_String`。
- 產生的 `.h` 與 metadata 會列出 wrapper symbol。
- 重名、C keyword、`main` 和不支援的 ABI 型別會在 `zy check` 報錯。

舊 `export fn` 仍相容，但它直接使用公開 C symbol；新引擎 API 優先使用
`#name`，讓內部 mangling 與公開 ABI 分離。

## 輸出 C、static library 與 shared library

```toml
[targets.game-c]
kind = "c-source"
entry = "src/lib.zy"
output-name = "zy_game"

[targets.game-static]
kind = "staticlib"
entry = "src/lib.zy"
output-name = "zy_game"

[targets.game-shared]
kind = "sharedlib"
entry = "src/lib.zy"
output-name = "zy_game"
```

```powershell
zy build game-c --release
zy build game-static --release
zy build game-shared --release
```

- `c-source` 產生 `.c`、`.h`、runtime/native bundle 和 metadata JSON。
- `staticlib` 產生平台 static library 與 header。
- `sharedlib` 產生 DLL、SO 或 dylib、必要 import library 與 header。
- header 可由 C11 或 C++ include；C++ 模式使用 `extern "C"`。
- metadata 提供 sources、headers、include paths、flags、libraries 和 exports。

不要根據輸出副檔名猜 target kind；一律在 manifest 明確指定。

## 第一版引擎範圍

第一個 milestone 只需要：

1. Engine/Application Handle。
2. Window create、frame begin、present、close。
3. Input polling 與事件資料。
4. Texture Handle 與明確 destructor。
5. POD render command batch。
6. `Slice<T>` 或 `Slice<u8>` 批次提交。
7. last-error 到 `throws Error` 的轉換。
8. 一個 ZyenLang 可執行 demo。
9. 一個 C/C++ consumer 呼叫 `#name` wrapper 的 demo。

第二個 milestone 才加入：

- sprite/mesh/text batch；
- asset cache；
- audio buffer 和 stream；
- callback/event registration；
- profiler marker；
- background loading 與 render-thread queue。

不要在第一版聲稱支援 hot reload。除非 unload barrier、callback 清理、Handle
失效、state serialization 和 reload failure recovery 都有自動化測試。

## 效能與執行緒規則

- 每 frame 避免大量短生命週期 string 和 closure allocation。
- component 使用緊湊 POD struct。
- renderer 接收 command buffer，不做 per-sprite native call。
- readonly buffer 用 Slice，需要寫回才用 MutSlice。
- window、graphics context 和 GPU Handle 固定在 render thread。
- background worker 不直接修改同一個 mutable world。
- atomic ARC 只保證 control block 計數，不保證 payload data race 安全。
- release benchmark 使用 `zy build TARGET --release`。

## 禁止自行發明的能力

接收端 AI 不得假設：

- struct 可以有 method；
- class 有 inheritance、trait、interface 或 operator overload；
- 有一般用途的裸 `ptr<T>`、任意解參考或 pointer arithmetic；
- safe reference 可以 nested、逃逸或跨 thread；
- 有可變 top-level global；
- 有 named argument call；
- 有 wildcard/selective import；
- `print` 可以接收任意型別；
- `List<T>` 支援整體 `==`；
- 有 GC、weak reference 或 ARC cycle collector；
- `spawn/await` 是完整 coroutine runtime；
- `native module` 會在執行期 `dlopen` 或 `LoadLibrary`；
- C/C++ adapter 受到 ZyenLang 記憶體安全保護。

## 驗收清單

完成每個 milestone 前至少驗證：

1. `zy check` 通過。
2. debug 與 release build 都成功。
3. demo 能建立 frame loop 並乾淨關閉。
4. owned Handle 的 destructor 恰好執行一次。
5. Handle alias、`DROP__()`、borrowed parent 與 consumed alias 行為正確。
6. 空 Slice、一般 Slice、MutSlice COW 和大型 buffer 都正確。
7. callback 註冊、替換、解除註冊與 shutdown 不洩漏。
8. native failure 會得到正確 `Error.message` 與來源 stack。
9. `#name` wrapper 可被 C 與 C++ consumer 連結及呼叫。
10. static/shared/C-source artifact 的 header 與 metadata 正確。
11. Windows、Linux、macOS 分別驗證 library search path。
12. Linux CI 執行 AddressSanitizer 與 UndefinedBehaviorSanitizer。
13. profiler 證明 batch API 沒有退化成大量細碎 ABI calls。

## 實作時應查閱的檔案

- `docs/language_guide_zh_TW.md`：精簡語言規則。
- `docs/game_engine_language_handoff_zh_TW.md`：完整語言手冊。
- `docs/game_engine_integration_zh_TW.md`：引擎 C ABI 設計。
- `docs/c_interop.zh_TW.md`：ABI v3 template 與 ownership。
- `docs/package_manager.md`：manifest、target 與 dependency。
- `examples/native/counter_abi3/`：可建置的 Handle/Slice/Error 範例。

接收端 AI 每新增一個 public engine API，都必須同時提供：

1. C façade declaration；
2. C adapter implementation；
3. ABI v3 template declaration；
4. 最小 ZyenLang 使用範例；
5. ownership 與錯誤語意；
6. lifecycle 或 integration test。

