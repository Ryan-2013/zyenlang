# 給遊戲引擎開發者的 ZyenLang 0.3.1 語言手冊

這份文件可以直接交給負責遊戲引擎的開發者或 AI。它描述的是目前編譯器真的
支援的 ZyenLang 0.3.1，不是未來提案，也不是已淘汰的 0.1/0.2 語法。

若讀者要實作引擎和 ZyenLang 之間的 C 邊界，讀完本文後再閱讀
[遊戲引擎整合規格](game_engine_integration_zh_TW.md)與
[C Interop ABI v3](c_interop.zh_TW.md)。

## 1. 語言定位

ZyenLang 是一門精簡、強型別、原生編譯的語言。目前後端把 Typed IR 轉成 C11，
再交給 Zig cc、GCC、Clang 或系統 C compiler 建置。

設計核心只有幾個主要概念：

- function：計算、流程和 module API；
- struct：純資料、值語意；
- class：有身份、有方法、由 ARC 管理的物件；
- module：每個 `.zy` 檔案形成 namespace；
- `List<T>`、`str`、函式值和安全引用：常用 managed value。

遊戲端建議分工：

- component、座標、顏色、渲染命令使用 struct；
- world、scene、asset manager、UI controller 使用 class；
- GPU texture、window、audio stream 等原生資源使用 ABI v3 Handle；
- 大量 vertex、pixel、command 使用 `List<T>` 加 Slice 批次傳遞；
- engine API 放在 native module namespace。

## 2. 最小程式

```zy
import std::io as io

fn main() i32 {
    io::print("hello from ZyenLang")
    return 0
}
```

基本規則：

- 原始碼是 UTF-8，副檔名為 `.zy`。
- statement 由換行結束，不寫分號。
- `//` 是單行註解。
- 程式入口是 `fn main() i32`。
- 區塊使用 `{}`。
- 一般名稱區分大小寫。
- top-level declaration、struct field 和 class member 預設都是 `private`。
- `let` 建立 binding，但 binding 可在之後直接用 `=` 或 `+=` 等重新賦值。
- 沒有 `set value = ...` 這套舊語法。

## 3. 專案結構

建議遊戲專案：

```text
my-game/
  zyproject.toml
  src/
    main.zy
    game.zy
    scene.zy
    components.zy
    native/
      engine.zlcm.h
      engine_zy.c
      engine_zy.h
  assets/
  tests/
```

`zyproject.toml`：

```toml
[package]
name = "my-game"
version = "0.1.0"
zyen = ">=0.3.1"
entry = "src/main.zy"

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
zy new my-game
zy init .
zy check
zy check game
zy build game
zy build game --release
zy run game -- first-argument second-argument
zy test
zy doctor
zy clean game
zy metadata
```

`bin` 產生執行檔。另有 `c-source`、`staticlib`、`sharedlib` target，供 C/C++
程式反向使用 ZyenLang。預設輸出在 `target/debug/<target>/` 或
`target/release/<target>/`。

## 4. Module、import、`::` 與 `.`

```zy
import std::io as io
import crate::components as components
import physics
import renderer::color as color
```

- `std::io`：標準庫。
- `crate::components`：目前專案的 `src/components.zy`。
- `physics`：`zyproject.toml` 中名為 `physics` 的 dependency package entry。
- `renderer::color`：dependency package 內的子 module。
- `as name` 可省略；省略時以最後一段作為 namespace。

固定符號規則：

```zy
io::print("text")                 // module function
components::Transform             // module type
TextureCache<i32>::empty()        // class static function
scene.update(0.016)               // class instance method
transform.x                       // instance field
```

`::` 只用於 module、type 和 static path。`.` 只用於 instance field 和 class
instance method。`io.print()`、`components.Transform` 都是編譯錯誤。

不支援：

- `import "file.zy"`；
- `import <std/io>`；
- wildcard import；
- selective import；
- module re-export；
- package root 之外的相對路徑穿越。

跨 module 使用的 struct、class、function 和 field 必須標示 `public`。

## 5. 基礎型別

整數：

```text
i8 i16 i32 i64
u8 u16 u32 u64
isize usize
```

其他基礎型別：

```text
f32 f64 bool str void Error
```

```zy
let lives: i32 = 3
let frame: u64 = 0
let delta: f32 = 0.016
let title: str = "My Game"
let running: bool = true
```

數字 literal 會依 context 檢查範圍，小數預設是 `f64`。混合數值運算會提升到
共同型別，例如 `i32 + f64` 得到 `f64`。不能安全共同表示時必須明確 cast。

```zy
let count: i32 = 12
let value: f64 = count + 0.5
let narrow: i16 = (i16)count
let text: str = (str)value
```

常用運算子：

```text
+ - * / %
< <= > >= == !=
&& || !
= -= *= /= %=
```

`str` 的 `==` 比較文字內容。struct 在所有 field 都可比較時採遞迴值比較。
class 比較身份。`List<T>` 尚未定義整體值相等，必須比較長度和元素。

## 6. 變數、作用域與生命週期

```zy
let score = 0
let health: i32 = 100
health -= 10
```

`let` 是 lexical binding。離開 `{}` 時，編譯器會清理該作用域內的 managed
value。同一作用域不能重新宣告同名 binding，但離開內層 scope 後可以重新使用
名稱。

ZyenLang 0.3 沒有可變 top-level global。長生命週期狀態應放在 `main` 建立的
class、引擎 Handle 或明確的 native subsystem 裡。

```zy
if health > 0 {
    let alive = true
    io::print((str)alive)
}
// alive 在此處不存在
```

## 7. String、f-string 與輸出

`str` 是不可變、ARC 管理的 UTF-8 字串。

```zy
let player = "Ryan"
let score: i32 = 42
let message = f"player={player}, score={score}"
io::print(message)
io::eprint("this is an error")
```

`std::io::print` 和 `eprint` 只接受 `str`，數字或 bool 必須使用 f-string 或
`(str)value`。

```zy
let chars: List<str> = STR_TO_LIST__(message)
let scalar_count: usize = STR_LEN__(message)
let byte_count: usize = STR_BYTE_LEN__(message)
let first: str = STR_GET__(message, 0) catch err { recover "" }
let part: str = STR_SLICE__(message, 0, 6) catch err { recover "" }
```

字元索引使用 Unicode scalar；byte length 是另一個 API。`str` 不是任意 byte
buffer，圖片、頂點和音訊資料請使用 `List<u8>` 或 native Buffer Handle。

## 8. Nullable 與縮窄

一般型別不能是 null。可空值明確寫成 `T | null`。

```zy
let selected: i32 | null = null
selected = 7

if selected != null {
    let index: i32 = selected
}

if let index = selected {
    io::print(f"selected={index}")
}
```

null check 的有效分支會自動把值縮窄成 `T`。不要使用 `null` 代表所有錯誤；會
失敗且需要訊息的操作使用 `throws Error`。

## 9. 控制流程

```zy
if score >= 100 {
    io::print("high score")
} else if score > 0 {
    io::print("playing")
} else {
    io::print("game over")
}

let index: usize = 0
while index < 10 {
    index += 1
    if index == 3 {
        continue
    }
    if index == 8 {
        break
    }
}
```

目前沒有 `for`。遊戲物件更新請使用 `while` 加索引，或把批次工作交給 native
engine API。

## 10. Function、預設參數與多回傳值

```zy
fn add(left: i32, right: i32) i32 {
    return left + right
}

fn log_message(text: str) void {
    io::print(text)
}

fn greet(name: str, prefix: str = "hello") str {
    return f"{prefix} {name}"
}
```

回傳型別省略時為 `void`。呼叫參數是位置參數，目前沒有具名參數呼叫。預設值
由編譯器在缺少 argument 時填入；建議只把預設參數放在尾端，避免 API 難懂。

泛型函式：

```zy
fn identity<T>(value: T) T {
    return value
}

let answer: i32 = identity(42)
```

泛型會對 reachable concrete call site 單態化。若函式內寫 `left + right`，每個
實例化的 `T` 都必須真的支援 `+`；語言目前沒有 trait constraint。

多回傳值使用固定長度的多結果值：

```zy
fn window_size() (i32, i32) {
    return 1280, 720
}

let (width: i32, height: i32) = window_size()
```

## 11. Struct：純資料 component

struct 是純資料、值語意，不能包含 method、`init` 或 `deinit`。

```zy
public struct Transform {
    public x: f32
    public y: f32
    public rotation: f32 = 0.0
}

public struct SpriteCommand {
    public texture_id: u32
    public transform: Transform
    public color: u32 = 4294967295
}

public fn translated(value: Transform, dx: f32, dy: f32) Transform {
    return Transform{
        x: value.x + dx,
        y: value.y + dy,
        rotation: value.rotation,
    }
}
```

建立 struct：

```zy
let position = Transform{x: 10.0, y: 20.0}
position.x += 4.0
```

省略 field 會使用宣告 default 或型別零值。field 預設 private；外部 module 只能
讀寫 public field。

struct assignment 是值複製。純數字 struct 直接複製；包含 `str`、`List<T>`、
class 或函式值時，編譯器會產生遞迴 retain/release 和 cleanup。遞迴 by-value
struct 會被拒絕。

不要在 struct 裡寫 method：

```zy
// 錯誤
struct Transform {
    fn move() void {}
}
```

純資料行為放在 module function；需要封裝狀態和 method 時使用 class。

## 12. Class：scene、world 與 manager

class 有共享身份，實例由 atomic ARC 管理，不支援繼承。

```zy
public class World {
    private transforms: List<Transform>
    private speed: f32 = 80.0

    public init() {
        this.transforms = []
    }

    public mut fn spawn(x: f32, y: f32) void {
        LIST_PUSH__(this.transforms, Transform{x: x, y: y})
    }

    public mut fn update(delta: f32) void {
        let index: usize = 0
        while index < LIST_LEN__(this.transforms) {
            let value: Transform = LIST_GET__(this.transforms, index) catch err {
                recover Transform{}
            }
            value.x += this.speed * delta
            LIST_SET__(this.transforms, index, value) catch err {
                recover
            }
            index += 1
        }
    }

    public fn count() usize {
        return LIST_LEN__(this.transforms)
    }

    deinit {
        // 最後一個 ARC reference 消失時執行
    }
}
```

使用：

```zy
let world = World()
world.spawn(10.0, 20.0)
world.update(0.016)
```

class member 規則：

- `fn` 得到唯讀 `this`，不能修改 field 或呼叫 `mut fn`。
- `mut fn` 可以修改 field。
- `static fn` 沒有 `this`，以 `Type::function()` 呼叫。
- `init` 是 constructor body，使用 `Type(arguments)` 建立。
- `deinit` 無參數、不能手動呼叫、不能 `throws`。
- field 和 method 預設 private。
- class assignment 複製 ARC reference，不複製整個 object。
- ARC 不會處理循環；兩個 class 互相強持有可能洩漏。

泛型 class：

```zy
public class Cache<T> {
    private value: T

    public init(value: T) {
        this.value = value
    }

    public fn get() T {
        return CLONE__(this.value)
    }

    public mut fn set(value: T) void {
        this.value = value
    }
}

let cache = Cache<i32>(10)
cache.set(20)
```

泛型 class 建構必須明確寫 type argument。沒有 inheritance、interface、trait、
variance 或 method-level 額外泛型。

## 13. `List<T>`：強型別動態陣列

`List<T>` 是編譯器內建容器，不是普通 struct。它使用 ARC backing buffer 和
copy-on-write，可以放在 struct/class field。

```zy
let values: List<i32> = [10, 20, 30]
LIST_PUSH__(values, 40)
LIST_SET__(values, 0, 11) catch err { recover }

let first: i32 = values[0] catch err { recover 0 }
let also_first: i32 = values[0] catch err { recover 0 }
let last: i32 = LIST_POP__(values) catch err { recover 0 }
let count: usize = LIST_LEN__(values)

LIST_CLEAR__(values)
```

所有會越界的操作都必須處理 Error。List assignment 或 `CLONE__()` 先共用 backing
buffer；第一次 mutation 時才 detach。managed element 會正確 retain/release，
`LIST_POP__()` 把移除元素的 ownership 交給 caller。

函式參數的差異：

```zy
fn snapshot(values: List<i32>) usize {
    return LIST_LEN__(values)
}

fn inspect(values: &List<i32>) usize {
    return LIST_LEN__(values)
}

fn append(values: &mut List<i32>, value: i32) void {
    LIST_PUSH__(values, value)
}
```

- `List<T>`：傳入值語意 snapshot，mutation 走 COW。
- `&List<T>`：零拷貝唯讀借用。
- `&mut List<T>`：修改呼叫端 list slot。

遊戲每 frame 的大量更新，優先把 POD struct 收集成 `List<Command>`，再一次用
Slice 交給 native renderer，不要每個 sprite 跨一次 ABI。

## 14. 函式值、回傳函式與 callback

函式值型別寫成 `fn(P...) R`。

```zy
fn add(a: i32, b: i32) i32 {
    return a + b
}

fn sub(a: i32, b: i32) i32 {
    return a - b
}

fn pick(addition: bool) fn(i32, i32) i32 {
    if addition {
        return add
    }
    return sub
}

let operation: fn(i32, i32) i32 = pick(false)
let first: i32 = operation(10, 3)
let second: i32 = pick(true)(20, 22)
```

closure：

```zy
let amount: i32 = 5
let increase: fn(i32) i32 = fn(value: i32) i32 {
    return value + amount
}
```

closure 以唯讀 snapshot capture，environment 使用 ARC。函式值可作為參數、
回傳值、struct/class field 與 C callback。底層統一是
`{call, env, owner, signature}`。

```zy
public struct ButtonAction {
    public callback: fn() void
}
```

函式簽章必須完全相同。函式值可為 `null` 時要明確使用 `fn(...) R | null`，並在
呼叫前縮窄。不要把 callback 保存到 C 端卻不 retain；ABI v3 adapter 必須使用
`zl_fn_assign` 和 `zl_fn_clear`。

## 15. 安全引用 `&T` 與 `&mut T`

```zy
let health: i32 = 100
let read: &i32 = &health
let copied: i32 = CLONE_REF__(read)

let write: &mut i32 = &mut health
REF_SET__(write, 80)
```

- `&T` 是唯讀借用，可同時存在多個。
- `&mut T` 是唯一可變借用。
- mutable borrow 存在時，不能讀寫 owner 或建立其他 borrow。
- reference 不可為 null，也不擁有 pointee。
- 禁止 `&&T`、`&mut &T` 等 nested reference。
- 第一版 reference 只能是 local 或 function parameter。
- reference 不能放進 struct/class/List。
- reference 不能從函式回傳、被 closure capture 或跨 thread 保存。
- field 不會自動解引用。
- receiver 位置會自動解引用，因此 `&Class` 可直接呼叫 readonly method。

List element 也可借用：

```zy
let numbers: List<i32> = [10, 20]
let item: &mut i32 = &mut numbers[0]
REF_SET__(item, 42)
```

List element reference 會 pin backing storage。可變 element borrow 的 owner 必須是
owned local List 或 value-struct field。

對 class 而言，`&mut Class` 表示目前這個 handle slot 可呼叫 mut method，不保證
整個 object 沒有其他 ARC alias。多執行緒同步仍由程式或引擎負責。

## 16. Ownership、ARC、clone、drop 與 defer

自動管理規則：

- primitive 和純 Copy struct 直接複製。
- struct 含 managed field 時遞迴複製和清理。
- class、native Handle 和 closure owner 使用 ARC。
- `str` 使用 ARC immutable storage。
- `List<T>` 使用 ARC backing buffer 和 COW。
- scope、return、break、continue 和 Error 路徑共用 cleanup planner。

```zy
let first = Cache<i32>(10)
let second = CLONE__(first)
DROP__(second)
```

`CLONE__(value)` 明確複製該型別的值語意。`DROP__(local)` 提早執行正常清理，並
使 binding 進入未初始化狀態。drop 後再次讀取或再次 drop 是編譯錯誤；可以用
賦值重新初始化。primitive 不能 `DROP__()`。

```zy
class Resource {
    public init() {
    }

    public fn close() void {
    }
}

let resource = Resource()
defer resource.close()
```

`defer` 只接受不會 throws 的 function 或 class method call。receiver 和 argument
在註冊時各求值一次，離開 scope 時依 LIFO 執行。

ARC 的 retain/release 使用 atomic count，但 payload 本身不會因此 thread-safe。
ARC 也不處理循環引用。

## 17. Error、stop、catch 與 recover

```zy
fn require_health(value: i32) i32 throws Error {
    if value <= 0 {
        stop "health must be positive"
    }
    return value
}

let health: i32 = require_health(-1) catch err {
    io::eprint(err.message)
    recover 1
}
```

- `throws Error` 宣告函式可能失敗。
- `stop "message"` 建立並傳播 Error。
- `catch err { ... }` 接在 throwing expression 後面。
- `recover value` 提供該 expression 的替代值。
- `recover` 的值必須符合原 expression 型別。
- 原 expression 是 void 或結果被丟棄時可以寫裸 `recover`。
- catch 中每條路徑都必須 `recover`、`return` 或再次 `stop`。

Error 欄位包含 `message`、`file`、`line`、`column` 和 stack frame。不要寫
`(str)err`，使用 `err.message`。未處理 Error 會以紅色顯示真實 ZyenLang 來源
位置和函式 stack。

## 18. Task 與執行緒

```zy
fn load_level() i32 {
    return 42
}

let task: Task<i32> = spawn load_level()
let result: i32 = await task
```

目前 `spawn` 使用 OS thread，不是單執行緒 coroutine。`Task<T>` 是 linear value，
必須在建立它的 lexical scope 內恰好 `await` 一次，不能任意複製或放進 aggregate。
第一版最適合零參數的直接 function/method call 和簡單回傳型別。

```zy
import std::thread as thread

thread::sleep_ms(16)
let count: i32 = thread::cpu_count()
thread::yield_now()
```

render context、window 和大部分 GPU Handle 應固定在 render thread。background
task 把結果送回 engine queue，不要直接從 worker 操作同一份 mutable scene state。

## 19. Reflection metadata 與 intrinsic

```zy
let fields: List<str> = transform.__attributes__
let methods: List<str> = world.__methods__
let is_transform: bool = TYPEOF__(transform, Transform)
```

struct 的 method list 永遠為空。class method list 包含可見 method 名稱。這是基本
metadata，不是可任意動態呼叫 method 的完整 reflection system。

常用 intrinsic：

```text
TYPEOF__(value, Type) -> bool
FILE__() -> str
GET_ARGS__() -> List<str>
GET_EXE__() -> str
CLONE__(value) -> T
CLONE_REF__(reference) -> T
REF_SET__(reference, value) -> void
DROP__(local) -> void
LIST_LEN__/LIST_SHAPE__/LIST_FILLED__/LIST_SET__/LIST_PUSH__/LIST_POP__/LIST_CLEAR__
STR_TO_LIST__/STR_LEN__/STR_BYTE_LEN__/STR_GET__/STR_SLICE__
PRINT_CMD__(text, "#RRGGBB") -> void
```

所有 intrinsic 都要寫括號。`GET_ARGS__()` 和 `GET_EXE__()` 只能在 `main` 使用：

- `GET_ARGS__()[0]` 是目前執行檔路徑；
- 使用者參數從索引 1 開始；
- `GET_EXE__()` 等同索引 0；
- `FILE__()` 是寫下它的 `.zy` 原始 module 絕對路徑。

## 20. 標準庫

### `std::io`

```zy
import std::io as io
io::print("normal")
io::eprint("error")
```

### `std::fs`

```zy
import std::fs as fs

let text: str = fs::read_text("assets/config.txt") catch err {
    recover ""
}
fs::write_text("save/data.txt", text) catch err { recover 1 }
```

提供 `read_text`、`write_text`、`append_text`、`tree`。相對路徑以建置後執行檔
所在目錄為基準，不是 shell 的 current directory。

### `std::path`

`path::parent(value)` 取得跨平台 lexical parent path。

### `std::list`

`list::length<T>()`、`list::is_empty<T>()` 是小型泛型 helper；mutation 仍建議使用
`LIST_*__()`，讓 ownership/COW 分析清楚看見變更。

### `std::option`

`option::is_null()`、`option::is_some()`。需要使用縮窄值時優先寫 `if let`。

### `std::process`

包裝 `GET_ARGS__()` 和 `GET_EXE__()`。

### `std::thread`

提供 `sleep_ms`、`yield_now`、`cpu_count`。

### `std::request`

跨平台 HTTP client，提供 GET、POST、JSON、PUT、DELETE、download、timeout 和
general send。Windows 使用 WinHTTP，Linux/macOS 使用 system libcurl。

### `std::server`

提供小型同步 HTTP server，適合工具和測試，不是 production game backend。

### `std::gui`

提供跨平台 Application、Panel、Label、Button、ButtonGroup、Column 和 raw draw
helper。所有 widget 由 Application 建立，因此 widget 知道自己屬於哪個 drawing
target。現有 backend 是 Raylib；API 維持 backend-neutral。

### `std::editor`

提供文字 buffer、游標、selection、completion、open/save 等低階 editor API。

### `std::error`

`error::require(condition, message)` 在條件為 false 時拋出 Error。

### `std::c_module`

編譯期載入 C ABI v3 模板，建立 native namespace、Handle、Slice、callback 和
錯誤轉換。這是自製遊戲引擎最重要的標準 module。

## 21. Dependency 與類似 pip 的使用方式

```powershell
zy install requests
zy install requests==0.1.0
zy install ../physics --alias physics
zy install git+https://example.com/renderer.git@FULL_COMMIT_SHA
zy install --locked
zy list
zy show physics
zy uninstall physics
```

registry package、path dependency 和固定 Git revision 都會寫入 `zy.lock`。Git
dependency 必須使用完整 commit，不接受浮動 branch 或縮寫 hash。package 沒有
install hook，不會在安裝時偷偷執行程式。

使用：

```zy
import physics
import physics::collision as collision

let version: str = physics::version()
```

## 22. 呼叫遊戲引擎 C API

```zy
import std::c_module as c
import std::io as io

native module engine = c::load("native/engine.zlcm.h")

fn main() i32 {
    let app: engine::Engine = engine::create("My Game", 1280, 720) catch err {
        io::eprint(err.message)
        return 1
    }

    let world = World()
    world.spawn(10.0, 20.0)
    let commands: List<engine::SpriteCommand> = []

    while engine::running(app) {
        let delta: f32 = engine::delta_seconds(app)
        world.update(delta)

        engine::begin_frame(app) catch err {
            io::eprint(err.message)
            return 2
        }

        LIST_CLEAR__(commands)
        // 將 world 轉成 engine::SpriteCommand 並加入 commands。
        engine::draw_commands(app, c::slice(&commands)) catch err {
            io::eprint(err.message)
            return 3
        }

        engine::present(app) catch err {
            io::eprint(err.message)
            return 4
        }
    }

    return 0
}
```

上例中的 engine function 只是介面示意，實際名稱由 `.zlcm.h` 決定。native module
是 namespace，不是執行期 object，所以寫 `engine::present(app)`，不是
`engine.present(app)`。

ABI v3 原則：

- Window、Texture、Audio、Mesh 等資源使用 typed Handle。
- owned Handle 由 ARC 管理，最後引用消失時呼叫 C destructor。
- borrowed Handle 只在呼叫期間借用。
- `consumed<T>` 轉移資源並使所有 alias 失效。
- `c::slice(&list)` 建立同步唯讀 Slice。
- `c::mut_slice(&mut list)` 建立同步可變 Slice。
- Slice 不可保存、回傳、capture 或跨 thread。
- C 若保存 callback，必須 retain `ZL_Function`。
- C/C++ exception 不可穿越 C ABI。

詳細模板與 adapter 寫法請看遊戲引擎整合規格。

## 23. ZyenLang 產生 C、static library 和 shared library

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

`#name` 會產生名稱穩定的 C wrapper 並進入 C/C++ header：

```zy
#game_api_version
fn game_api_version() u32 {
    return 1
}

#game_update
fn game_update(delta: f32) i32 {
    return 0
}
```

函式本體保留 mangled 名稱，wrapper 只負責轉呼叫；舊 `export fn` 仍相容。
目前 C export 只接受固定寬度數字、`bool`、`void`、`ZL_String`。generic、class、
List、reference、closure、Handle 和 throwing function 不能直接 export。複雜狀態
應留在 ZyenLang 主程式，或由引擎持有並透過簡單 C façade 操作。

平台產物：

- Windows：`.lib`、`.dll`、`.dll.a`、`.h`；
- Linux：`.a`、`.so`、`.h`；
- macOS：`.a`、`.dylib`、`.h`；
- `c-source`：`.c`、`.h`、runtime/native bundle 和 metadata JSON。

## 24. 遊戲程式建議架構

```text
src/main.zy           啟動、遊戲迴圈、錯誤頂層
src/game.zy           Game class 與 scene 切換
src/components.zy     Transform、Velocity、Sprite 等 struct
src/systems.zy        純 module functions 或 system class
src/events.zy         function value callback 與事件資料
src/native/           引擎 C adapter 和 ABI v3 模板
```

推薦 frame 流程：

1. native engine 收集 OS/input event。
2. ZyenLang 更新 Game/World class。
3. system 操作 `List<Component>`。
4. 建立 `List<RenderCommand>`。
5. 以 Slice 一次交給 native renderer。
6. present。
7. scope cleanup 自動釋放暫存 List、str、closure 和 Handle alias。

效能原則：

- component 用緊密 struct，不要每個 component 都建 class。
- native resource 才使用 Handle。
- 每 frame 避免大量短字串和 closure allocation。
- renderer 使用 batch API，避免每個 sprite 一次 native call。
- readonly buffer 使用 Slice；真的要寫回才使用 MutSlice。
- release build 使用 `zy build game --release`。

## 25. 目前不能假設的功能

交給 AI 開發時，請明確禁止它自行發明以下語法或能力：

- 沒有 `for`，使用 `while`。
- 沒有 struct method，行為放 module function 或 class。
- 沒有 class inheritance、interface、trait 或 operator overload。
- 沒有一般程式可使用的裸 `ptr<T>`、`*` 解引用或任意 pointer arithmetic。
- 沒有 nested safe reference。
- 沒有可變 top-level global。
- 沒有 named argument call。
- 沒有 wildcard/selective import。
- 沒有自動把任意值傳給 `print`，print 只接受 str。
- 沒有 List 整體 `==`。
- 沒有 GC 或 ARC cycle collector。
- atomic ARC 不代表 object payload thread-safe。
- `spawn/await` 目前是受限 OS-thread task，不是完整 async runtime。
- `native module` 是 build-time linking，不是 `dlopen`/`LoadLibrary`。
- 沒有內建 hot-reload state migration。
- C adapter 不受 ZyenLang 記憶體安全保護，必須視為可信 native code。

## 26. 交給另一個 AI 的執行要求

當另一個 AI 使用 ZyenLang 實作遊戲引擎功能時，要求它遵守：

1. 先用 `zy check` 驗證每個階段，不可只憑語法猜測。
2. module function 一律使用 `::`，instance field/method 一律使用 `.`。
3. component 和 render command 使用 struct；有身份的 manager 使用 class。
4. class 中只有修改狀態的方法標示 `mut fn`。
5. 所有 List 越界操作都處理 Error。
6. 所有 throwing native call 都在合理層級 catch，或明確向上傳播。
7. C resource 一律宣告 owned/borrowed/consumed/optional 等 ownership。
8. 不把 Slice 或 safe reference 保存到下一 frame。
9. 不讓 C++ exception 穿越 C ABI。
10. 不以 per-object native call 設計大量渲染，改用 batch Slice。
11. 新增的 public API 同時提供一個最小 `.zy` 使用範例。
12. 完成後執行 `zy test`、debug build、release build 和 native lifecycle test。

## 27. 最小學習順序

建議開發者依序掌握：

1. `fn main()`、`let`、if、while、function。
2. module import 與 `::`/`.`。
3. struct component 與 class manager。
4. `List<T>`、nullable、多結果值和 Error。
5. function value、closure 與 callback。
6. `&T`、`&mut T`、ARC、`DROP__()` 和 defer。
7. 專案 target、dependency 和 release build。
8. native module、Handle、Slice 和 C adapter。
9. static/shared export 與引擎 plugin 限制。

本文描述的是實作現況。若語言版本改變，先執行 `zy --version`，再以同版本的
`docs/language_guide_zh_TW.md`、`docs/standard_library.md` 和 compiler diagnostics 為準。
