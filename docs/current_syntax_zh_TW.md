# ZyenLang 目前語法總覽

狀態：`v0.1.73`（Python 套件版本 `0.1.73`，原生互通 ABI v2）
更新日期：2026-07-18

這份文件集中記錄目前編譯器實際支援的語法、型別、ownership 規則與
`c_module` 寫法。若舊 README、舊手冊或歷史範例與本文件衝突，以目前
編譯器、測試及本文件為準。

## 1. 最小程式

```zy
fn add(a: int, b: int) -> int {
    return a + b;
}

fn main() -> int {
    print((str)(add(20, 22)));
    return 0;
}
```

常用指令：

```powershell
zy check main.zy
zy run main.zy
zy build main.zy -o main.c
zy build main.zy -o main.exe
zy c-module gen native.zlcm.h
zy version
```

`zy check` 會解析、展開 import、做型別檢查並在記憶體中產生 C，但不執行
GCC。`zy run` 會產生暫存 C、編譯並執行。`zy build` 依輸出副檔名產生 C
或 EXE。

## 2. 檔案、註解與語句

檔案頂層只允許：

```text
import
struct
fn
```

目前不允許 top-level `let`、`const` 或 `set`。需要常數時可使用零參數
函式：

```zy
fn max_speed() -> int {
    return 100;
}
```

單行註解使用 `//`：

```zy
let speed: int = 10; // motor speed
```

一般語句以 `;` 結束。函式簽章、呼叫、條件、`for` 表頭與 List literal
可在成對的 `()`、`[]`、`{}` 內跨行：

```zy
let total: int = add(
    20,
    22
);
```

## 3. 型別

| ZyenLang 型別 | 用途 |
|---|---|
| `int` | 目前為有號 32-bit 整數，算術會檢查 overflow |
| `float` | C `double` |
| `bool` | `true` / `false` |
| `str` | 唯讀字串值，C 端對應 `const char*` |
| `List` | 異質動態容器，元素在 runtime 以內部 `Any` 表示 |
| `ptr<T>` | 帶型別標籤與可選 ARC owner 的 managed/borrowed pointer |
| `ptr<void>` | 擦除靜態 target 型別的 opaque pointer |
| `fn(P...)->R` | 第一級函式值 |
| struct 名稱 | 使用者定義的 value struct |
| `void` | 函式沒有回傳值 |
| `None` | 空的 pointer 或空的函式值 |

`Any` 不是公開型別，只能存在於 List runtime。要把 `List.get()` 的值存入
具體變數，必須顯式 cast。

函式型別、pointer 型別可以遞迴巢狀：

```zy
let chain: ptr<ptr<int>> = None;
let factory: fn(str)->fn(int,int)->int = None;
```

## 4. 變數、常數與修改

宣告可使用型別推斷或顯式型別：

```zy
let count = 10;
let speed: float = 3.5;
let enabled: bool = true;
let name: str = "ZyenLang";
const limit: int = 100;
```

沒有 initializer 時，基本型別使用預設值：

```zy
let count: int;     // 0
let ratio: float;   // 0.0
let enabled: bool;  // false
let text: str;      // ""
let values: List;   // empty List
```

所有一般修改都必須顯式寫 `set`：

```zy
let x: int = 1;
set x = 2;
set x += 3;
set x -= 1;
set x *= 2;
set x /= 2;
```

以下寫法會被拒絕：

```zy
x = 2;
x += 1;
```

`const` 必須有 initializer，之後不能用 `set` 修改。

## 5. Literal、運算子與 cast

```zy
let i: int = 10;
let f: float = 2.5;
let ok: bool = true;
let text: str = "hello";
let empty: ptr<int> = None;
let values: List = [1, 2.5, "hello", true];
```

目前常用運算子：

```text
+  -  *  /
==  !=  <  <=  >  >=
!  &&  ||
```

`int` 運算會在 runtime 檢查 overflow 與除以零。`str == str` 和
`str != str` 比較內容，不比較 C 位址。`str + value` 會做字串串接：

```zy
let age: int = 12;
let message: str = "age=" + (str)age;
```

f-string：

```zy
let rpm: int = 1200;
let ready: bool = true;
print(f"rpm={rpm} ready={ready}");
```

`print` 只接受 `str`。`Any` 不再是 `print` 的公開參數型別：

```zy
print("ready");
print((str)rpm);
print(f"rpm={rpm}");
```

以下會在 `zy check` 階段被拒絕：

```zy
print(rpm); // error: print expects str, got int
```

f-string expression 的結果永遠是 `str`，但 `{...}` 插值可放入支援字串轉換的
值，由編譯器完成格式化。Pointer 必須直接 cast 或放進 f-string：

```zy
let pointer: ptr<int> = None;
print((str)pointer);       // None、Freed 或 0x... address
print(f"pointer={pointer}");
```

`(str)pointer` 只格式化 pointer 本身；要格式化它指向的值，使用
`print((str)*pointer);`。若 pointer expression 產生 ARC owned temporary，轉換
完成後仍會在 full-expression 結尾自動 release。

C-like cast 的右括號必須寫在型別之後：

```zy
let i: int = 12;
let f: float = (float)i;
let text: str = (str)i;
let parsed: int = (int)"34";
let truth: bool = (bool)i;
```

Pointer cast 的正確拼法：

```zy
let typed: ptr<int> = (ptr<int>)opaque;
print((str)(*((ptr<int>)opaque)));
```

錯誤拼法 `(ptr<int>opaque)` 會在 `zy check` 階段被拒絕。

## 6. 流程控制與 scope

### if / else if / else

```zy
if (score >= 90) {
    print("A");
} else if (score >= 80) {
    print("B");
} else {
    print("C");
}
```

### for

```zy
for (let i = 0; i < 10; set i += 1) {
    print((str)(i));
}
```

`for` step 也相容簡短的 loop-control 寫法：

```zy
for (let i = 0; i < 10; i++) {
}

for (let i = 0; i < 10; i += 1) {
}
```

無限迴圈使用：

```zy
for (;;) {
    break;
}
```

目前沒有 `while`。迴圈支援 `break;`、`continue;`，空操作使用 `pass;`。

目前新版以 lexical scope 管理區域變數。區塊內的普通值在 `}` 後失效，
managed local 會在 normal exit、`return`、`break` 與 `continue` 路徑插入
對應 release。

## 7. 函式

### 定義與回傳

```zy
fn square(value: int) -> int {
    return value * value;
}

fn line() -> void {
    print("---");
}
```

省略 `-> T` 時目前等同 `-> void`，但公開 API 建議寫完整回傳型別。

### 前置宣告

```zy
fn scale(value: int, factor: int = 2) -> int;

fn main() -> int {
    return scale(21);
}

fn scale(value: int, factor: int) -> int {
    return value * factor;
}
```

每個宣告都必須有簽章相同的定義。定義可以省略宣告已提供的預設值。

### 預設參數與具名參數

```zy
fn mix(a: int, b: int = 2, c: int = 3) -> int {
    return (a * 100) + (b * 10) + c;
}

fn main() -> int {
    print((str)(mix(1)));
    print((str)(mix(1, 4, 5)));
    print((str)(mix(c: 9, a: 1)));
    print((str)(mix(1, c: 9)));
    return 0;
}
```

規則：

- 有預設值的參數必須位於尾端。
- 位置參數可以放在具名參數之前。
- 使用具名參數後不能再接位置參數。
- 重複名稱、未知名稱與缺少必要參數都是 compile error。
- 普通函式與 struct method 支援具名參數。
- 透過 `fn(...) -> T` 值呼叫時只支援位置參數，因為函式型別不保存參數名。

## 8. Struct 與 method

Field 必須寫成 `let this.name: Type;`：

```zy
struct Counter {
    let this.value: int;
    let this.step: int = 1;

    fn inc(times: int = 1) -> int {
        set this.value += this.step * times;
        return this.value;
    }
}
```

建立 struct：

```zy
let a: Counter = Counter;
let b: Counter = Counter {};
let c: Counter = Counter { value: 10 };
let d = Counter { value: 10, step: 2 };
```

Struct literal 只支援具名 field，不支援 `Counter {10, 2}`。未提供的 field
先套用宣告上的 default；沒有 default 的 field 由 C zero-initialize。

Method 內以 `this.field` 讀取欄位，以 `set this.field = ...;` 修改。呼叫：

```zy
print((str)(d.inc(times: 3)));
print((str)(d.value));
```

Struct field 可以是 `fn`、owned pointer 或另一個含 managed field 的 struct；
複製、覆寫與離開 scope 時編譯器會產生對應 ARC 操作。

## 9. Import 與 module alias

Standard module：

```zy
import <std/math>;
import <std/string> as text;
```

User module：

```zy
import "helper.zy";
import "../lib/math.zy" as math2;
```

相對路徑以寫下 import 的 `.zy` 檔案所在資料夾為基準。Alias 用於函式與
型別限定：

```zy
let result: int = text.len("hello");
let car: model.Car = model.Car { speed: 10 };
```

目前 imported user struct 最終仍進入共用 struct table；型別前綴會在編譯
時正規化。

## 10. List

```zy
fn main() -> int {
    let xs: List = [1, 2, "hello", true];
    xs.append(3.5);
    print((str)(xs.len()));
    print((str)(xs.get(0)));
    xs.set(0, 99);
    print((str)(xs.pop()));
    xs.clear();
    return 0;
}
```

`List.get()` 與 `List.pop()` 的靜態型別是內部 `Any`。要存入變數時需 cast：

```zy
let number: int = (int)xs.get(0);
let text: str = (str)xs.get(1);

let *owned: ptr<int> = 42;
let pointers: List = [];
pointers.append(owned);
let pointer: ptr<int> = (ptr<int>)pointers.get(0);
```

List 可保存 owned pointer：

- `append` / `set` 會 retain 放入的 owned pointer。
- `set` / `clear` 會 release 被移除的 pointer。
- `pop` 會把該引用的 ownership 轉移給呼叫端。
- ABI v2 暫不允許 List 保存 `fn` 值。

List 與動態 `str` 本身目前尚未全面改為 ARC。需要確定釋放 List 中的 managed
元素時，應主動呼叫 `clear()`。

## 11. 第一級函式值

函式型別格式：

```zy
fn(int,int)->int
fn(str)->fn(int)->int
```

參數名稱不是函式型別的一部分，簽章必須完全相同，不提供 covariance 或任意
function cast。

```zy
fn add(a: int, b: int) -> int {
    return a + b;
}

fn sub(a: int, b: int) -> int {
    return a - b;
}

fn apply(op: fn(int,int)->int, x: int, y: int) -> int {
    return op(x, y);
}

fn pick(name: str) -> fn(int,int)->int {
    if (name == "add") {
        return add;
    }
    return sub;
}
```

支援保存、傳入、回傳與連續呼叫：

```zy
let op: fn(int,int)->int = pick("sub");
print((str)(op(10, 3)));
print((str)pick("sub")(10, 3));
print((str)make_picker()("sub")(10, 3));
```

Fn field：

```zy
struct Reducer {
    let this.op: fn(int,int)->int;
    let this.seed: int;
}

let reducer: Reducer = Reducer { op: add, seed: 0 };
```

Fn 值可以是 `None`：

```zy
let callback: fn(int)->int = None;
if (callback != None) {
    print((str)(callback(1)));
}
set callback = None;
```

呼叫 `None` 函式值會產生明確 runtime error，不會跳入空 C function pointer。
函式值不能 cast 成另一個 fn 簽章，也不能 cast 成 `ptr<T>`。

### `ptr<fn(...)>` 函式指標

`fn(...)` 是函式值；`ptr<fn(...)>` 則是指向 `ZL_Function` 記憶體 cell 的
managed pointer。top-level 函式的位址指向編譯器建立的靜態 cell：

```zy
fn add(a: int, b: int) -> int {
    return a + b;
}

let func_ptr: ptr<fn(int,int)->int> = &add;
print(f"{(*func_ptr)(20, 22)}");
print(f"{*func_ptr(20, 22)}"); // ZyenLang 簡寫，結果同上
```

使用 `let *` 可以建立 ARC 管理的 owned function cell。若存入 closure，cell
會 retain closure environment：

```zy
let operation: fn(int,int)->int = make_operation();
let *owned_ptr: ptr<fn(int,int)->int> = operation;
print(f"{*owned_ptr(20, 22)}");
```

函式指標可以隱式擦除成 `ptr<void>`，還原時需要顯式 cast：

```zy
let opaque: ptr<void> = func_ptr;
let restored: ptr<fn(int,int)->int> = (ptr<fn(int,int)->int>)opaque;
print(f"{*restored(12, 10)}");
print(f"{*(ptr<fn(int,int)->int>)opaque(12, 10)}");
```

這裡是新宣告，所以使用 `let opaque: ptr<void> = func_ptr;`。`set` 只用來修改
已經宣告的變數，不能寫成 `set opaque: ptr<void> = ...;`。

cast 不改變 address、owner 或 runtime type tag。呼叫前會檢查 pointer tag 與
函式簽章；`None`、`Freed`、錯誤原始型別及不同 fn 簽章都不會進入無效 C
位址。不同 `ptr<fn(...)>` 簽章之間禁止直接 cast。

`ptr<fn(...)>` 指向的是 `ZL_Function` data cell，不是把 C function pointer
強制轉成 `void*`。raw C callback 仍由 c_module 相容層負責轉接。

## 12. Closure

Closure 使用巢狀具名函式建立，目前沒有匿名 lambda：

```zy
fn make_adder(delta: int) -> fn(int)->int {
    fn add_delta(value: int) -> int {
        return value + delta;
    }
    return add_delta;
}

fn main() -> int {
    let add2: fn(int)->int = make_adder(2);
    print((str)(add2(40)));
    print((str)make_adder(10)(32));
    return 0;
}
```

Capture 是建立 closure 當下的唯讀 snapshot。巢狀函式內若 `set capture += 1`，
修改的是該次呼叫的 local copy，不會讓 environment 變成可變狀態。需要可變
狀態時，使用 struct 明確保存狀態。

Closure environment 使用原子 ARC，會遞迴 retain/release 捕捉到的 fn、owned
pointer 與含 managed field 的 struct。原子的是引用計數，不代表 captured data
自動 thread-safe。ARC 不處理循環引用。

## 13. Pointer 基本模型

`ptr<T>` 在 C ABI 中不是裸 `T*`，而是：

```c
typedef struct ZL_ptr {
    void* addr;
    const char* type_name;
    int mem_id;
    bool owned;
    ZL_ArcControl* owner;
} ZL_ptr;
```

`owner == NULL` 代表 borrowed。非 NULL 代表 owned，alias 以 ARC 共用控制塊。

### None pointer

```zy
let p: ptr<int>;
let q: ptr<int> = None;
print((str)(p)); // None
```

Bare `ptr` 只有在 initializer 能推斷 target 時相容：

```zy
let value: int = 10;
let p: ptr = &value;
```

公開 API 建議永遠寫完整的 `ptr<int>`。

### Borrowed pointer

```zy
let value: int = 10;
let p: ptr<int> = &value;
print((str)(*p));
set *p = 20;
print((str)(value));
```

`&local` 不配置 heap，也不建立 ARC owner。Pointer 只在 local 所屬 lexical scope
內有效。編譯器會拒絕明顯的 `return &local;`，但目前不是完整 borrow checker，
不要把 stack pointer 保存到會逃出 scope 的 closure、List、struct 或 C library。

### Owned pointer

`let *name` 是 ZyenLang 的 owned-cell 配置語法，不是 C 的 raw pointer 宣告：

```zy
let *p: ptr<int> = 10;
print((str)(*p));
set *p = 20;
```

它會配置一個 int payload 與 ARC control block。離開 scope 時自動 release。

也可使用 `std/mem`：

```zy
import <std/mem>;

let p: ptr<int> = mem.alloc_int(10);
let alias: ptr<int> = p;
print((str)(*alias));
```

Alias 會 retain 同一 owner；最後一個引用離開後 payload 只釋放一次。

### None pointer 的 lazy cell

```zy
let p: ptr<int>;
set *p = 5;
print((str)(*p));
```

若 `p` 是 None，第一次 `set *p = value;` 會建立一個 owned cell。

## 14. ptr<void> 與顯式 pointer cast

`ptr<T>` 可隱式轉成 `ptr<void>`。轉回具體型別或在不同 concrete pointer 間
轉換時必須顯式 cast：

```zy
let value: int = 10;
let typed: ptr<int> = &value;
let opaque: ptr<void> = typed;
let restored: ptr<int> = (ptr<int>)opaque;
print((str)(*restored));
print((str)(*((ptr<int>)opaque)));
```

Cast 不改變 address、owner、ownership 或 runtime type tag。`ptr<void>` 不能直接
解參考或 indexing。

## 15. 巢狀 pointer

`ptr<ptr<int>>` 指向一個 `ZL_ptr` 值，不等同裸 C `int**`。

使用既有 pointer 初始化：

```zy
let *value: ptr<int> = 10;
let *slot: ptr<ptr<int>> = value;
let loaded: ptr<int> = *slot;
print((str)(*loaded));
```

也可從最內層 scalar 遞迴建立完整 chain：

```zy
let *chain: ptr<ptr<int>> = 10;
print((str)(**chain));

let *deep: ptr<ptr<ptr<int>>> = 42;
print((str)(***deep));
```

每層都有自己的 owner，釋放最外層時 destructor 會遞迴釋放內層。

`&*managed_pointer` 會保留同一個 address、owner 與 runtime type tag，因此可以
安全地為巢狀 pointer 建立 alias：

```zy
let *source: ptr<ptr<int>> = 10;
let *slot: ptr<ptr> = &**source;
set **slot = 20;
print((str)(**source)); // 20
```

`ptr<ptr>` 可作為相容寫法；owned declaration 會從初始化式補出缺少的內層
型別。為了讓 API 與診斷更清楚，公開介面仍應優先寫完整 `ptr<ptr<int>>`。

## 16. ptr<str> 與字串

```zy
let *text_cell: ptr<str> = "hello";
print((str)(text_cell));  // cell 位址
print((str)(*text_cell)); // hello
```

`ptr<str>` 指向的是「保存一個 `str` 值的 cell」，不是直接表示字元陣列的
`char*`。擦除與恢復型別：

```zy
let opaque: ptr<void> = text_cell;
print((str)(*((ptr<str>)opaque))); // hello
```

Managed `ZL_ptr` 不支援 `pointer + offset` 或 `pointer - offset`。以下會被拒絕：

```zy
let shifted: ptr<void> = text_cell + 1;
```

取得字元或 substring 使用字串 API：

```zy
import <std/string>;

let text: str = *text_cell;
print((str)(string.char_at(text, 1)));
print((str)(string.substring(text, 1, 4))); // ello
```

Pointer indexing 只應用在確實指向陣列的 pointer。單一 `let *p` cell 沒有第二個
元素。需要安全陣列位移時應使用 `std/buffer`，raw C pointer arithmetic 放在
c_module compatibility layer。

## 17. None、Freed 與 mem.free

解參考 None 或已釋放 pointer 會產生明確 runtime error：

```text
None pointer dereference: p
Freed pointer dereference: p
```

`mem.free(p)` 可強制處置 owned payload：

| 回傳值 | 意義 |
|---|---|
| `0` | 成功處置 owned payload |
| `-1` | pointer 是 None |
| `-2` | pointer 是 borrowed，不能由 `mem.free` 釋放 |
| `-3` | payload 已經被處置 |

成功後所有 alias 都會觀察到 `Freed`。Control block 會保留到最後一個 alias
離開 scope，payload destructor 只執行一次。

## 18. Scope、函式與 ownership

```zy
fn work() -> int {
    let stack_value: int = 10;
    let *owned: ptr<int> = 20;
    return *owned;
}
```

- `stack_value` 使用 C automatic storage，不會呼叫 `malloc`。
- 普通 local 的語意生命週期到 lexical scope 結束；C 編譯器可重用 stack slot
  或把值放進 register。
- Owned pointer、closure 及 managed struct local 在 scope exit 自動 release。
- 函式參數預設 borrowed，只保證在該次呼叫期間有效。
- 回傳 managed value 會轉移一個 owned reference 給呼叫端。
- 回傳 struct 或 nested pointer 時，從回傳值可達的 managed fields 會遞迴保留。
- 沒有從回傳值逃逸的 managed locals 仍會在 scope exit 釋放。
- Closure capture 會 retain managed snapshot。

Hot loop 中每圈宣告 owned pointer 不會洩漏，但會配置與釋放很多次：

```zy
for (let i = 0; i < 10000; set i += 1) {
    let a: int = 10;          // automatic storage，非 heap allocation
    let *b: ptr<int> = 10;    // 每圈配置，圈尾 release
}
```

若不需要每圈獨立 owner，移到迴圈外重用：

```zy
let *b: ptr<int> = 0;
for (let i = 0; i < 10000; set i += 1) {
    set *b = i;
}
```

目前安全模型是「checked fat pointer + compiler-inserted ARC」，不是 Rust 等級
的完整記憶體安全。仍需由開發者負責：

- Borrowed pointer 的跨 scope 使用。
- Raw C library 保存 pointer/callback 的生命週期。
- 沒有 bounds metadata 的 pointer indexing。
- ARC reference cycle。
- Captured data 的 thread synchronization。
- 尚未全面納入 ARC 的 List 與動態 `str`。

## 19. c_module 公開介面

`c_module` 是 compiler-native std module。正常 `check`、`run`、`build` 不會啟動
Python wrapper CLI。

### 區域載入

```zy
import <std/c_module> as c_module;

fn main() -> int {
    let math: c_module.Module = c_module.load("native_math.zlcm.h");
    print((str)(math.add(b: 22, a: 20)));
    return 0;
}
```

Manifest 路徑以寫下 `c_module.load()` 的 `.zy` 檔案為基準，而且必須是字串
literal。

### Facade struct

```zy
import <std/c_module> as c_module;

struct Math {
    let this.source: c_module.Module = c_module.load("native_math.zlcm.h");

    fn add(a: int, b: int) -> int {
        return this.source.add(a, b);
    }
}

fn main() -> int {
    let math: Math = Math {};
    print((str)(math.add(20, 22)));
    return 0;
}
```

`c_module.Module` 是 dependent compiler type，具體型別由同一宣告右側模板的
絕對路徑決定。只允許直接初始化 local 或 struct field；暫不允許裸
`c_module.Module` 作為函式參數或回傳型別。不同模板的 module 不可互相賦值。

已移除以下舊語法：

```zy
import c_module.load("math.zlcm.h") as math;
```

## 20. .zlcm.h 模板

最小模板：

```c
ZLC_MODULE(native_math)
ZLC_HEADER("native_math.h")
ZLC_SOURCE("native_math.c")

ZLC_FN(add, zlcm_native_add, int,
    ZLC_PARAM(a, int),
    ZLC_PARAM(b, int))
```

所有支援的 metadata macro：

```c
ZLC_MODULE(name)
ZLC_HEADER("header.h")
ZLC_SOURCE("source.c")
ZLC_INCLUDE_DIR("include")
ZLC_LIB_DIR("lib")
ZLC_LIB("raylib")
ZLC_CFLAG("-DMY_FLAG")
ZLC_LDFLAG("-mwindows")
```

Struct 與函式：

```c
ZLC_STRUCT(NativePoint,
    ZLC_FIELD(x, float),
    ZLC_FIELD(y, float))

ZLC_FN(move_point, native_move_point, NativePoint,
    ZLC_PARAM(point, NativePoint),
    ZLC_PARAM(dx, float),
    ZLC_PARAM(dy, float))
```

模板可用型別：

```text
int
float
bool
str
void                 // 僅回傳位置
ZL_List / ZL_list
ZL_ptr / ZL_ptr<T>
fn(P...)->R
ZLC_STRUCT 宣告的 value struct
```

Compiler 會依模板自動把 header、source、include dir、library 與 flags 傳給
GCC。同一模板載入多次會重用隱藏型別及 native metadata；路徑不同的同名
`ZLC_MODULE` 不會碰撞。

## 21. C callback ABI

模板直接使用 ZyenLang 函式型別：

```c
ZLC_FN(set_callback, native_set_callback, void,
    ZLC_PARAM(callback, fn(int)->int))
```

C header 使用 `ZL_Function`：

```c
#include <zyenlang_c_abi.h>

void native_set_callback(ZL_Function callback);
```

Callback 參數在呼叫期間是 borrowed。C 若要保存 callback，必須 retain：

```c
static ZL_Function stored;

void native_set_callback(ZL_Function callback) {
    zl_fn_assign(&stored, callback);
}

void native_clear_callback(void) {
    zl_fn_clear(&stored);
}
```

呼叫前可用 `zl_fn_matches` 驗證 canonical signature，實際 dispatch 使用精確
typedef 與 `ZL_FN_CALL_AS`。Raw callback API 若有 `user_data`，相容層可保存
retained `ZL_Function` 作為 context；沒有 `user_data` 時由相容層維護 callback
slot。Compiler 不替任意 C library 自動合成 trampoline。

C pointer ownership adapter：

```c
ZL_ptr borrowed = zl_ptr_borrow(address, "int");
ZL_ptr owned = zl_ptr_adopt(address, "NativeHandle", destroy_handle);
```

`zl_ptr_borrow` 不接管資源；`zl_ptr_adopt` 把 destructor 與資源交給 ARC。

## 22. 目前明確不支援

- Top-level `let` / `const` / `set`。
- `while`；使用 `for`。
- 匿名 lambda；使用巢狀具名函式 closure。
- Spread、destructuring、comprehension、`**kwargs`。
- Struct positional literal，例如 `Point {1, 2}`。
- 任意 fn signature cast 或 fn-to-ptr cast。
- 直接解參考 `ptr<void>`。
- Managed pointer arithmetic，例如 `p + 1`。
- 把 `ptr<ptr<int>>` 當成 raw C `int**`。
- List 保存第一級函式值。
- `c_module.Module` 作為一般參數或回傳型別。
- 完整 borrow checker、GC、weak reference 與 ARC cycle collection。

## 23. 平台狀態

目前發行包與開發測試以 Windows 為主：

- Compiler/runtime：Windows tested。
- `std/tk` 與目前 GUI：Windows only。
- `c_module` 產物能否跨平台取決於模板使用的 C library、flags 與相容層。
- 未經 CI 驗證前不宣稱 Linux/macOS 完整支援。

## 24. 對應測試與深入規格

主要測試：

```text
tests/named_args_test.zy
tests/fn_value_test.zy
tests/fn_chain_test.zy
tests/closure_test.zy
tests/ptr_void_test.zy
tests/nested_ptr_init_test.zy
tests/c_module_facade_test.zy
tests/c_module_callback_test.zy
```

深入 ABI 文件：

- `docs/ZEP-0010-function-values.md`
- `docs/ZEP-0013-closures.md`
- `docs/arc_callback_abi.md`
