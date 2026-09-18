# ZyenLang 0.3 語言指南

本文件描述 0.3.0 實作。0.3 與 0.2 不相容，不提供雙語法模式。

## 基本規則

- 原始碼是 UTF-8，副檔名為 `.zy`。
- statement 以換行結束，不寫分號。
- `//` 是單行註解。
- 每個檔案就是一個 module。
- `::` 只表示 module/type/static path；`.` 只表示 instance field/method。
- top-level 與 type member 預設為 `private`。
- 程式入口固定是 `fn main() i32`。

```zy
import std::io as io

fn main() i32 {
    io::print("hello")
    return 0
}
```

## Module

```zy
import std::io as io
import crate::model as model
import utils::text as text
```

- `std::` 從標準庫解析。
- `crate::net::http` 對應目前專案的 `src/net/http.zy`。
- 其他 root 必須是 `zyproject.toml` 宣告的 dependency alias。
- import 必須有 alias；alias 是 namespace，不是值。
- 不支援字串路徑、`<std/...>`、wildcard、selective import 或 re-export。

```zy
let point: model::Point = model::create(10, 20)
io::print(text::format_point(point))
```

`io.print()` 與 `model.Point` 都是編譯錯誤。

## 型別與數值

整數型別：

```text
i8 i16 i32 i64
u8 u16 u32 u64
isize usize
```

浮點型別為 `f32`、`f64`，另外有 `bool`、`str`、`void`、`Error`。
整數 literal 會依型別 context 檢查範圍；小數預設為 `f64`。混合數值運算先
提升至共同型別，例如 `i32 + f64` 的結果是 `f64`。無法無損共同表示時必須明確
cast。

```zy
let count: i32 = 12
let ratio: f64 = count + 0.5
let narrowed: i16 = (i16)count
```

## 變數與作用域

```zy
let inferred = 10
let typed: i64 = 10
typed = 20
```

`let` 建立 lexical binding。離開 `{}` 時 managed value 會自動清理；同一作用域
不能重複宣告同名 binding。`DROP__(value)` 可以提早清理 managed local，之後讀取
或再次 drop 會報錯；賦值可重新初始化原 binding。

## String 與 f-string

`str` 是不可變、ARC 管理的 UTF-8 字串。字元 API 使用 Unicode scalar，byte
length 另行計算。

```zy
let name = "Zyen"
let message = f"hello {name}, answer={20 + 22}"
let chars: List<str> = STR_TO_LIST__(message)
let scalar_count: usize = STR_LEN__(message)
let bytes: usize = STR_BYTE_LEN__(message)
let first: str = STR_GET__(message, 0) catch err { recover "" }
let slice: str = STR_SLICE__(message, 0, 5) catch err { recover "" }
```

## Nullable

一般型別不可為 null。需要空值時明確寫 `T | null`。

```zy
let result: i32 | null = null
result = 42

if result != null {
    // result 在這個分支縮窄成 i32
    let answer: i32 = result
}

if let answer = result {
    // answer: i32
}
```

## Struct

struct 是純資料、值語意型別，不能包含任何方法、`init` 或 `deinit`。

```zy
public struct Point {
    public x: i32
    public y: i32
    private label: str = "point"
}

public fn create(x: i32, y: i32) Point {
    return Point{x: x, y: y}
}

public fn length_squared(point: Point) i32 {
    return point.x * point.x + point.y * point.y
}
```

宣告 struct 的 module 可讀寫 private field；外部 module 只能使用 public field。
省略的欄位使用宣告 default 或型別零值。純 Copy struct 直接複製；包含 `str`、
`List<T>`、class、closure 等 managed field 時，編譯器產生遞迴 retain/release。
遞迴 by-value layout 會在編譯期拒絕。

## Class 與泛型

class 具有身份並由 atomic ARC 管理，不支援繼承或隱式 subtype。

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

    public static fn same(value: T) T {
        return value
    }

    deinit {
        // 最後一個 ARC reference 離開時執行
    }
}

let cache: Cache<i32> = Cache<i32>(10)
cache.set(Cache<i32>::same(42))
```

- `fn` 的 `this` 唯讀；只有 `mut fn` 能修改 field 或呼叫 mut method。
- `static fn` 沒有 `this`，以 `Type::method()` 呼叫。
- 泛型 class 建構時必須明確寫 type argument。
- `deinit` 無參數、不能手動呼叫、不能 `throws`。
- ARC 不處理循環；互相持有的 class 可能洩漏，但不會提早釋放。

## List

`List<T>` 是強型別、managed、copy-on-write 容器，可以放在 struct/class field。

```zy
let values: List<i32> = [10, 20]
LIST_PUSH__(values, 30)
LIST_SET__(values, 0, 11) catch err { recover }
let first: i32 = LIST_GET__(values, 0) catch err { recover 0 }
let last: i32 = LIST_POP__(values) catch err { recover 0 }
let count: usize = LIST_LEN__(values)
LIST_CLEAR__(values)
```

`values[index]` 是 `LIST_GET__()` 的語法糖並進行 bounds check。List clone 先共用
backing storage，第一次 mutation 才 detach。Managed element 會正確 retain/release，
`LIST_POP__()` 將該 element 的 ownership 轉移給 caller。

## 函式、泛型與多回傳值

```zy
fn add<T>(left: T, right: T) T {
    return left + right
}

fn greet(prefix: str = "hello", name: str) str {
    return f"{prefix} {name}"
}

fn pair() (i32, str) {
    return 42, "answer"
}

let (number: i32, text: str) = pair()
```

泛型會在 reachable concrete call site monomorphize。泛型運算仍需對 specialization
成立；編譯器不假設任意 `T` 一定支援 `+` 或 `==`。

## 函式值與 closure

函式值型別為 `fn(P...) R`。

```zy
fn add(a: i32, b: i32) i32 { return a + b }
fn sub(a: i32, b: i32) i32 { return a - b }

fn pick(addition: bool) fn(i32, i32) i32 {
    if addition { return add }
    return sub
}

let direct: i32 = pick(false)(10, 3)

let amount = 5
let closure: fn(i32) i32 = fn(value: i32) i32 {
    return value + amount
}
```

函式值可作為參數、回傳值與 field，也能交給 native callback。closure 以 readonly
snapshot capture，environment 由 ARC 管理。底層統一是 `{call, env, owner,
signature}`；呼叫鏈的 callee 與每個 argument 都只求值一次。

## 安全引用

```zy
let value: i32 = 10
let read: &i32 = &value
let copied: i32 = CLONE_REF__(read)

let write: &mut i32 = &mut value
REF_SET__(write, 20)
```

- `&T` 可同時存在多個；`&mut T` 必須唯一。
- mutable borrow 存在時不能讀、改 owner 或再建立其他 reference。
- reference 不可 null、不擁有資料，也不執行 cleanup。
- 禁止 nested reference，例如 `&&T`、`&mut &T`。
- 第一版只能放在 local 或 function parameter。
- 禁止存入 struct/class/List、回傳、closure capture 或跨 thread 保存。

## Clone、Drop 與 defer

`CLONE__(value)` 明確複製：class/closure 增加 ARC count，struct 維持值語意，
List 使用 COW。一般 assignment 依型別語意保留有效 ownership。

```zy
class Resource {
    public init() {}
    public fn close() void {}
}

let resource = Resource()
defer resource.close()
```

`defer` 第一版只接受不會 throws 的 function/method call。receiver 與 argument 在
註冊時各求值一次，離開 scope 時 LIFO 執行；`return`、`break`、`continue`、
`catch/recover` 與錯誤傳播都走同一個 cleanup planner。

## Error

```zy
fn positive(value: i32) i32 throws Error {
    if value <= 0 {
        stop "value must be positive"
    }
    return value
}

let value: i32 = positive(-1) catch err {
    io::eprint(err.message)
    recover 0
}
```

`recover` 的 value 必須符合原 expression 型別；discarded void expression 可以寫
裸 `recover`。未處理 Error 會以紅色輸出 `message`、真實 source file、line、
column 與 function stack。使用 `err.message`，不支援舊 `(str)err` 特例。

## 控制流程與 Task

```zy
let index = 0
while index < 10 {
    index += 1
    if index == 3 { continue }
    if index == 8 { break }
}

let task: Task<i32> = spawn calculate()
let answer: i32 = await task
```

0.3 沒有 `for`。`Task<T>` 是 linear value，必須 exactly once `await`，不能任意
複製或保存到 aggregate。

## Reflection metadata

struct/class instance 可讀取編譯器提供的 metadata list：

```zy
let fields: List<str> = point.__attributes__
let methods: List<str> = cache.__methods__
```

struct 的 method list 永遠為空；class method list 包含可見 method 名稱。

## Native、export 與 c_module

低階 module 可直接宣告：

```zy
native source "bridge.c"
native link windows "user32"
private native fn open_native(path: str) i32 = "bridge_open"
```

路徑以宣告所在 `.zy` module 為基準，且不能逃出 package root。`native source`
只接受 `.c`。這些宣告不享有標準庫特權，第三方 package 使用完全相同機制。

`export fn` 進入 C/C++ header，目前支援固定寬度數字、bool、void 與 `ZL_String`，
且不能 throws 或 generic。

`.zlcm.h` 模板用法與 macro 規格見 [c_module.md](c_module.md)。

## Intrinsic 索引

```text
TYPEOF__(value, Type) -> bool
FILE__() -> str
GET_ARGS__() -> List<str>
GET_EXE__() -> str
CLONE__(value) -> T
CLONE_REF__(reference) -> T
REF_SET__(reference, value) -> void
DROP__(local) -> void
LIST_LEN__/GET__/SET__/PUSH__/POP__/CLEAR__
STR_TO_LIST__/LEN__/BYTE_LEN__/GET__/SLICE__
PRINT_CMD__(text, "#RRGGBB") -> void
```

`GET_ARGS__()` 與 `GET_EXE__()` 只能在 `main` 使用。`FILE__()` 可在任何 function
使用，結果是寫下它的 `.zy` module 絕對路徑。
