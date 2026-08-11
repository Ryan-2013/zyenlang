# ZyenLang 0.2.1 語言完整入門

這份文件只描述目前 `zy` 編譯器已經實作並測試的 ZyenLang 0.2 語法。

## 1. 執行程式

安裝版使用 `zy`：

```powershell
zy check main.zy
zy run main.zy
zy run main.zy --release
zy build main.zy -o build\main.c
zy build main.zy -o build\main.exe --release
```

直接從原始碼目錄執行時：

```powershell
python -m zyenlang check main.zy
python -m zyenlang run main.zy
```

最小程式：

```zy
import <std/io> as io

fn main() i32 {
    io.print("hello world")
    return 0
}
```

`main` 不接受參數，而且必須回傳 `i32`。命令列參數由 `GET_ARGS__` 取得。

## 2. 基本規則

- 換行代表敘述結束，不使用分號。
- `{}` 表示區塊。
- `//` 是單行註解，`/* ... */` 是區塊註解。
- 字串使用雙引號，支援 JSON 形式的跳脫，例如 `"\n"`、`"\t"`、`"\\"`。
- 目前沒有全域變數與 `const` 宣告。
- 頂層只能放 `import`、`struct`、`fn` 與 `native` 宣告。
- 函式呼叫目前只接受位置參數，不接受具名參數。

## 3. 型別

整數型別：

| 型別 | 範圍 |
|---|---|
| `i8`, `i16`, `i32`, `i64` | 有號固定寬度整數 |
| `u8`, `u16`, `u32`, `u64` | 無號固定寬度整數 |
| `isize`, `usize` | 指標寬度整數 |

其他基礎型別：

| 型別 | 用途 |
|---|---|
| `f32`, `f64` | 浮點數；小數與科學記號 literal 預設為 `f64` |
| `bool` | `true` 或 `false` |
| `str` | 目前是借用的 UTF-8 字串 |
| `void` | 沒有回傳值 |
| `Error` | 帶訊息、檔案、行與列的錯誤值 |

複合型別：

```zy
let numbers: List<i32> = [1, 2, 3]
let pair: (i32, str) = (12, "hello")
let maybe: i32 | null = null
let owned: Box<i32> = Box(12)
```

整數 literal 預設是 `i32`，但有型別上下文時會直接依目標寬度檢查。小數
literal 與 `1e3` 這類科學記號預設是 `f64`，明確的 `f32` 上下文會直接產生
`f32`：

```zy
let small: i8 = 127
let large: u64 = 10_000_000
let values = [1.0, 2.5, 1e3] // List<f64>
let small_float: f32 = 3.25
```

不同數字型別不會隱式互轉。

### f-string

在普通字串前加 `f`，可把型別化 expression 插入字串。每個 expression 只求值一次：

```zy
let name: str = "ZyenLang"
let count: i32 = 42
let ready: bool = true
io.print(f"{name}: count={count}, ready={ready}")
io.print(f"literal braces: {{ok}}")
```

插值支援 `str`、所有數字、`bool` 與 `str | null`。其他 struct、List、Box 必須先
明確轉成受支援的值，否則 `zy check` 直接報錯。`{{` 和 `}}` 產生普通大括號。
f-string 結果是 thread-local borrowed `str`，使用 16 個輪替的 4096-byte buffer；
適合立即輸出或傳給不保存字串的 API，超長結果會截斷。owned string 完成後會取代
這項暫時的生命週期限制。

## 4. 變數與賦值

`let` 一定要有初始值，可明寫型別或讓編譯器推斷：

```zy
let count: i32 = 10
let inferred = 20

count = count + 1
count += 1
```

v2 賦值直接使用 `=`，也支援 `+=`、`-=`、`*=`、`/=`、`%=`，不再使用
舊版的 `set`。目前 `let` 變數仍可重新賦值，`mut` 尚未形成完整的可變性規則，
正式程式先不要依賴它。

變數採 lexical scope。外層宣告可以在內層區塊讀寫，離開區塊後仍存在；只在
`if`、`while` 或 `catch` 的 `{}` 內宣告的名稱，會在對應 `}` 後失效。這也讓
編譯器能在區塊結束時釋放該區塊擁有的 ARC 資源：

```zy
let outer: i32 = 1
if true {
    outer = 2
    let inside: i32 = 3
}
// outer 仍是 2；inside 在這裡已不存在
```

`SKIP__(local)` 可以把目前 block 內宣告的 local 提升到上一層 lexical scope；
如果該 block 沒有執行，提升後的名稱保留其型別零值。它一次只跨一層，不能把
名稱移出函式，也不能替換已存在的外層同名變數：

```zy
if found {
    let answer = 42
    SKIP__(answer)
}
// found 為 false 時 answer 是 0
io.print((str)answer)
```

`FREE__(local)` 立即結束目前 block 所宣告的 binding。若值由 ARC 管理會立刻
release，之後同一個 block 可再次使用相同名稱：

```zy
let value = Box(1)
FREE__(value)
let value = Box(2)
```

兩者都只接受 local 名稱，不能用在 expression 中，也不能作用於 `Task<T>`。

多回傳值使用 tuple 與解構：

```zy
fn pair() (i32, str) {
    return 12, "twelve"
}

fn use_pair() {
    let (number: i32, text: str) = pair()
}
```

## 5. 運算子與轉型

支援的運算子：

- 算術：`+`、`-`、`*`、`/`、`%`
- 比較：`<`、`<=`、`>`、`>=`
- 相等：`==`、`!=`
- 邏輯：`&&`、`||`、`!`
- 一元負號：`-value`

混合數字型別的算術會自動提升：任一側是 `f64` 時結果是 `f64`；其餘含
`f32` 的運算結果是 `f32`。純整數運算會選擇可同時容納兩側完整範圍的最小
固定寬度型別，例如 `i8 + u8` 是 `i16`，`i32 + u32` 是 `i64`：

```zy
let integer: i32 = 7
let floating: f64 = (f64)2
let result: f64 = integer + floating // 9
```

若不存在無損的共同整數型別，例如 `i64 + u64`，編譯器會要求明確 cast。
`%` 只接受整數。不同數字型別使用 `<`、`<=`、`>`、`>=`、`==`、`!=` 時
也會產生安全的比較形式；signed/unsigned 比較不採用 C 會把負數轉成巨大
無號數的規則。轉型寫成 `(Type)value`：

`==` 與 `!=` 支援數字、`bool`、`str`、tuple、optional，以及欄位都可比較的
struct。Struct 採值相等，會遞迴比較所有 public/private 欄位，而不是比較位址：

```zy
struct Car {
    public value: i32
}

let first = Car{value: 12}
let second = Car{value: 12}
let same: bool = first == second // true
```

`Box<T>` 的相等仍表示同一個 ARC owner。`List<T>` 尚未定義值相等；請明確
比較長度與元素，直接使用 `==` 會在 `zy check` 報錯。

```zy
let wide: i64 = (i64)42
let decimal: f64 = (f64)wide
let truth: bool = (bool)wide
let error_text: str = (str)error
```

目前支援數字互轉、數字與 bool 互轉，以及所有數字／bool 轉 `str`：

```zy
io.print((str)12)
io.print((str)(i64)-42)
io.print((str)(f64)10)
```

不能把整個 `Error` 轉成字串。錯誤訊息應明確寫成 `(str)err.message`；檔案、
行與列則分別由 `err.file`、`err.line`、`err.column` 取得。

## 6. 條件與迴圈

```zy
if score > 90 {
    io.print("great")
} else if score > 60 {
    io.print("pass")
} else {
    io.print("retry")
}

let index: i32 = 0
while index < 10 {
    index = index + 1
    if index == 3 {
        continue
    }
    if index == 8 {
        break
    }
}
```

目前支援 `while`、`break`、`continue`，尚未加入 `for`。

## 7. 函式

```zy
fn add(left: i32, right: i32) i32 {
    return left + right
}

fn notify(message: str) {
    io.print(message)
}
```

省略回傳型別時是 `void`。函式預設是 `public`，也可以明確寫成：

```zy
public fn exported() i32 {
    return 42
}

private fn helper() i32 {
    return 1
}
```

參數可以提供預設值。省略的值會在呼叫處補入，而且每次呼叫都會重新求值：

```zy
fn add(a: i32 = 10, b: i32 = 20) i32 {
    return a + b
}

let first: i32 = add()       // 30
let second: i32 = add(1)     // 21
let third: i32 = add(1, 2)   // 3
```

預設參數也可以放在必要參數之前。編譯器由左到右配對；當剩餘引數剛好都要
留給後面的必要參數時，會使用目前參數的預設值：

```zy
fn scale(base: i32 = 10, value: i32) i32 {
    return base * value
}

let implicit_base: i32 = scale(5)     // scale(10, 5)
let explicit_base: i32 = scale(2, 5)  // scale(2, 5)
```

目前函式呼叫只支援位置引數，不支援具名引數。預設值不能引用該函式的參數，
而且宣告即使沒有被呼叫，預設值仍會接受型別檢查。

普通函式、互相遞迴與第一版函式值已支援。函式型別沿用 v2 的「回傳型別放在
參數列後面」規則，寫成 `fn(P...) R`，不使用 `->`：

```zy
fn add(a: i32, b: i32) i32 {
    return a + b
}

fn pick() fn(i32, i32) i32 {
    return add
}

let operation: fn(i32, i32) i32 = add
let first: i32 = operation(20, 22)
let second: i32 = pick()(12, 10)
```

函式值目前只引用已命名的頂層函式，不配置 heap，也不捕捉區域變數。參數與
回傳簽章必須完全相同；透過函式值呼叫時不套用原函式的預設參數。struct
可以保存 callback，並可從 method 呼叫欄位：

```zy
struct Button {
    public when_click_func: fn() void
}

fn (button: Button) click() void {
    button.when_click_func()
}
```

未初始化的函式欄位是空 callback；呼叫時會以 `.zy` 呼叫位置產生 runtime
error，而不會跳入空 C 指標。closure、綁定 receiver 的 method value、throwing
callback 與 native callback ABI 仍待後續里程碑。

## 8. Struct、public 與 private

以下寫法合法：

```zy
public struct Dict {
}
```

因為預設可見性就是 `public`，它等同於：

```zy
struct Dict {
}
```

有欄位的 struct：

```zy
public struct User {
    public id: i32
    public name: str = "unknown"
    private token: str = ""
}
```

欄位前面的 `let` 可以省略。建議採用上面的短寫法。建立值時使用具名欄位：

```zy
let user = User{id: 7, name: "Ryan"}
let default_name = User{id: 8}
```

未提供且沒有預設值的欄位會由 C 後端零初始化。`str` 與其他需要有效值的欄位，
建議總是提供明確預設值。

方法寫在 struct 外面，receiver 放在函式名稱前：

```zy
public fn (user: User) display_name() str {
    return user.name
}

private fn (user: User) token_is_empty() bool {
    return user.token == ""
}
```

呼叫方式：

```zy
let name: str = user.display_name()
```

private 欄位只能從同一 struct 的 method 存取；private method 也只能由同一
struct 的 method 呼叫。struct、field、method、普通函式如果沒有標記，預設都
是 public。

目前 receiver 是值傳遞。method 內修改 receiver 只會修改函式中的副本，若要把
結果帶回呼叫端，請回傳新的 struct。

每個具體 struct 都能讀取共享的唯讀 metadata：

```zy
let attributes: List<str> = user.__attributes__
let methods: List<str> = user.__methods__
```

它們依宣告順序包含 public 與 private 成員名稱，但不會繞過 private 存取限制。
metadata 是每種 struct 共用的靜態表，不會替每個實例配置兩個 List。

`public struct Dict<K, V>` 的宣告目前可以被解析，但泛型 struct 還不能建立實例。
現階段泛型字典應等待正式 `Dict<K, V>` collection 實作，不要用空 struct 假裝成
已可儲存鍵值的容器。

## 9. 泛型函式

泛型普通函式已支援，編譯器會依實際型別產生具體版本：

```zy
fn identity<T>(value: T) T {
    return value
}

let number: i32 = identity(12)
let text: str = identity("hello")
```

泛型函式本體會先以 `T` 的抽象型別獨立檢查，再依呼叫型別產生具體版本。
目前沒有 `T: Numeric` 之類的型別約束，因此不能假設 `T` 可以直接與 `i32`
運算：

```zy
fn add<T>(a: i32 = 10, b: T) T {
    return a + b
}
```

上例會在 `b` 報錯：

```text
generic value `b: T` must be explicitly cast to `i32`
```

若這個函式的契約確實要求可轉成 `i32`，要把兩個方向都明確寫出來：

```zy
fn add<T>(a: i32 = 10, b: T) T {
    return (T)(a + (i32)b)
}
```

模板中的顯式 cast 會在每個具體實例再次檢查；例如不支援的 `str` 到 `i32`
轉型仍會在 `zy check` 被拒絕，不會變成不安全的 C cast。

泛型中的 `a == b` 也會延後到具體型別確認。`is_same<Car>` 會使用 Car 的
結構相等；`is_same<List<i32>>` 則會在 `zy check` 明確指出 List 尚未支援
相等運算，不會產生無效 C。

泛型 method 與泛型 struct 實例化尚未支援。

## 10. List

`List<T>` 是語言內建的強型別泛型容器，不是使用者宣告的 struct，也不是
`std/list.zy` 裡名為 `List` 的物件。`List` 是型別建構器，尖括號內的 `T`
是元素型別，所以
`List<str>` 只能放 `str`，`List<i32>` 只能放 `i32`：

```zy
let values: List<i32> = [10, 20, 30]
let matrix: List<List<i16>> = [[1, 2], [3, 4]]
let empty: List<str> = []
```

這不是一個接收 `Any` 的普通物件。編譯器看到 `List<str>` 時，會在編譯期為 `str`
特化出一個 C handle；`List<i32>` 會得到另一個型別。每個 handle 指向具有
`items`、`len`、`capacity` 和 atomic ARC owner 的共享 storage。不同元素型別
不能混用，空 List 因為沒有元素可供推斷，所以必須明寫 `List<T>`。

```zy
let size: usize = LIST_LEN__(values)
let first: i32 = values[0] catch err {
    recover 0
}

LIST_PUSH__(values, 40)
LIST_PUSH__(values, 50)
LIST_SET__(values, 0, 11) catch err {
    recover
}

let last: i32 = values.pop() catch err {
    recover 0
}
let removed: i32 = values.remove(0) catch err {
    recover 0
}

values.clear()
```

公開操作如下。`LIST_LEN__` 和 `[]` 是編譯器操作，不是 struct method：

| 語法 | 結果 |
|---|---|
| `LIST_LEN__(list)` | 目前元素數量，`usize` |
| `list[index]` | 邊界檢查後讀取，`T throws Error` |
| `capacity()` | 不重新配置時可容納的數量，`usize` |
| `is_empty()` | 是否沒有元素 |
| `LIST_PUSH__(list, value)` | 在尾端新增，value 必須是 `T` |
| `LIST_SET__(list, index, value)` | 邊界檢查後替換，`void throws Error` |
| `pop()` | 移除並回傳最後元素，`T throws Error` |
| `remove(i32)` | 移除並回傳指定元素，`T throws Error` |
| `clear()` | 釋放所有元素並清空 |

List 複製採共享 ARC 語意：

```zy
let first = [1, 2]
let alias = first
LIST_PUSH__(alias, 3)
// LIST_LEN__(first) 與 LIST_LEN__(alias) 都是 3
```

`.len()`、`.get(index)`、`.push(value)` 與 `.set(index, value)` 暫時保留作為
舊程式的相容別名；新程式使用 `LIST_LEN__(list)`、`list[index]`、
`LIST_PUSH__(list, value)` 和 `LIST_SET__(list, index, value)`，清楚表達 List 是語言
內建容器，而不是普通 struct。

List literal 是 owned storage。`GET_ARGS__` 與 `value.__attributes__` 則先是
borrowed view；把它存進區域變數後第一次修改會 copy-on-write，不會改動作業系統
參數或 struct metadata。`List<List<T>>` 與 `List<Box<T>>` 會遞迴 retain/release。

`STR_TO_LIST__(text)` 依 UTF-8 Unicode 字元切成 `List<str>`，中文與 emoji 各算
一個元素：

```zy
let text = "I am your father."
let chars: List<str> = STR_TO_LIST__(text)
```

這份 List 的 ARC owner 同時持有字元 backing storage，最後一個 List alias 離開
scope 時會自動釋放。從 List 取出的單一 `str` 是借用值，不可比原 List 活得更久；
必須先把結果存入區域變數，編譯器會拒絕 `STR_TO_LIST__(text)[index]` 這種直接取值。

List 可以直接成為 struct field。含 List 的 struct 也會成為 managed value；
編譯器會替它產生遞迴 retain/release，套用到複製、整體或欄位賦值、參數、回傳、
`stop`、`break`、`continue` 與 scope exit：

```zy
struct Inventory {
    public items: List<i32> = []
}

struct Store {
    public inventory: Inventory = Inventory{}
}

let store = Store{}
LIST_PUSH__(store.inventory.items, 10)
let alias = store
// alias 與 store 的 items 共用同一份 ARC storage
```

也支援 `List<Inventory>` 及跨模組 public struct。`Node { children: List<Node> }`
這類遞迴 managed type 目前會在 `zy check` 明確拒絕；List 放進 tuple 或 optional
也仍待對應 destructor。`str` 本身仍是 borrowed，因此一般 List 只保存字串位址，
不會複製字串內容；`STR_TO_LIST__` 的字元 backing storage 是上述的特殊 owned
情況。完整範例見 `examples/v2_struct_list.zy`。

## 11. null 與 optional

v2 不允許所有型別任意變成 null，只允許明確的 `T | null`：

```zy
let value: i32 | null = null

if value == null {
    io.print("none")
}

value = 10
if value != null {
    io.print((str)value) // 此分支中 value 會縮窄為 i32
}

let another: i32 | null = 7
if let number = another {
    io.print("some")
}

let maybe_text: str | null = null
let display: str = (str)maybe_text // "null"
```

`(str)(str | null)` 是安全的顯示轉換：有值時回傳原字串，沒有值時回傳字面
`"null"`，不會把 null 當成有效指標解參考。目前不支援 `i32 | str` 這類一般
union。`value != null` 的 true 分支與 `value == null` 的 else 分支會做區域
flow narrowing；離開該分支後，變數仍保持原本的 `T | null` 型別。

## 12. 錯誤處理

可能失敗的函式宣告 `throws Error`，用 `stop` 建立錯誤：

```zy
fn require_positive(value: i32) i32 throws Error {
    if value <= 0 {
        stop "value must be positive"
    }
    return value
}
```

呼叫端用 `catch` 與 `recover`：

```zy
let value: i32 = require_positive(-1) catch err {
    io.eprint((str)err.message)
    recover 0
}
```

catch 區塊的每條路徑必須以 `recover`、`return` 或 `stop` 結束。`recover value`
提供這次呼叫的替代回傳值；void 呼叫使用裸 `recover`。

`Error` 提供：

```zy
err.message
err.file
err.line
err.column
```

未處理錯誤會顯示 `.zy` 來源檔、行、列與訊息，並以非零狀態結束。互動終端中的
編譯錯誤、未捕捉 `stop` 與 `io.eprint` 預設為紅色；重新導向時不放 ANSI 色碼。
可用 `NO_COLOR` 關閉，或以 `ZYEN_COLOR=always|never` 明確控制。

## 13. Box 與目前的 ARC

`Box<T>` 是 v2 第一個由編譯器管理的 heap value：

```zy
let value: Box<i32> = Box(12)
let alias = value

alias.value = 20
let current: i32 = value.value
let references: usize = value.__strong_count__
```

Box 使用 C11 atomic ARC。複製、傳參、賦值、回傳，以及正常離開 scope、
`return`、`stop`、`break`、`continue` 時，都會自動 retain/release。

目前限制：

- Box 可以保存 primitive、借用 `str` 或沒有 managed field 的具體 struct。
- struct field、List、tuple、optional 或另一個 Box 內不能再放 Box。
- `Box<str>` 只擁有 Box cell，不擁有底下的字串資料。
- owned UTF-8 string、tuple/optional managed destructor、`Ref<T>` 與 `Raw<T>` 尚未完成。

不支援的 managed 組合會在編譯期報錯，不會靜默產生錯誤釋放程式。

## 14. 編譯器特殊形式

全大寫並以 `__` 結尾的名稱由編譯器保留，使用者不能宣告同名變數或函式。
使用規則只有兩種：

- 不接收輸入的特殊值不加括號，例如 `FILE__`、`GET_ARGS__`、`GET_EXE__`。
- 接收輸入的特殊形式一律使用括號與逗號，例如 `LIST_LEN__(list)`、
  `LIST_PUSH__(list, value)`、`LIST_SET__(list, index, value)`、
  `STR_TO_LIST__(text)`、`PRINT_CMD__(text, "#RRGGBB")`、
  `TYPEOF__(expression, Type)`、`SKIP__(local)`、`FREE__(local)`。

這些名稱不是普通函式，不能取函式值、覆寫或作為 callback。

`PRINT_CMD__` 在支援顏色的互動終端輸出 ANSI truecolor；重新導向時自動輸出
純文字。`NO_COLOR`、`ZYEN_COLOR=always` 與 `ZYEN_COLOR=never` 可控制色彩。

### TYPEOF__

`TYPEOF__(expression, Type)` 是編譯期型別比較，結果是 bool。expression 會被
型別檢查，但不會在執行期求值：

```zy
let value: i32 = 12
if TYPEOF__(value, i32) {
    io.print("i32")
}
```

它比較靜態型別，不是執行期反射或繼承判斷。

在泛型函式中，`TYPEOF__` 的 true 分支會縮窄 local 的具體型別；單態化後確定
不可能執行的分支不再參與型別檢查。因此 `List<List<T>>` 在對應分支可直接當成
`List<List<f64>>` 使用，不需要逐元素複製或虛假的 cast。

## 15. 命令列與來源路徑

```zy
let args: List<str> = GET_ARGS__
let executable: str = GET_EXE__
let source_file: str = FILE__
```

`GET_ARGS__` 不包含執行檔名稱，`GET_EXE__` 是 `argv[0]`。兩者是保留的特殊值，
不能被區域變數覆蓋，而且只能直接出現在 `main`。需要交給 helper 時，必須像
普通資料一樣透過參數傳入。`TYPEOF__` 是純編譯期表達式，不受這項限制。

`FILE__` 是寫下這個表達式的 `.zy` 模組絕對路徑，不是執行檔路徑；它可出現在
任何函式。匯入模組內的 `FILE__` 會得到該匯入檔自己的路徑，因此可用來定位
隨模組部署的資源。

## 16. 執行緒與 Task

```zy
fn calculate() i32 {
    return 42
}

fn run_task() i32 {
    let task: Task<i32> = spawn calculate()
    let answer: i32 = await task
    return answer
}
```

目前 `spawn` 使用 OS thread。第一版限制如下：

- 只能 spawn 零參數的直接函式或 method call。
- 函式不能 `throws Error`。
- 只能回傳數字、bool 或 void。
- Task 必須在建立它的同一 lexical scope 中恰好 await 一次。
- Task 不能複製、重新賦值、放入 struct 或從函式回傳。

## 17. 模組與可見性

標準庫：

```zy
import <std/io> as io
```

相對路徑模組：

```zy
import "Dict.zy" as model

let value: model.Dict = model.Dict{}
```

套件模組：

```zy
import <math-lib/math> as math
```

只有 public struct、field、function 與 method 能從其他模組使用。相對 import
不能逃離套件 root，套件 import 必須在 `zyproject.toml` 與 `zy.lock` 中宣告。

套件管理器：

```powershell
zy pkg init --name my-app
zy pkg add ..\math-lib
zy pkg install --locked
zy pkg list
zy pkg remove math-lib
```

目前套件管理器只支援本機 path dependency，尚未加入 registry 與 Git dependency。

## 18. Native C 模組

標準庫與第三方模組使用完全相同的 native 宣告：

```zy
native source "bridge.c"
native link windows "user32"
native link linux "dl"

private native fn native_add(left: i32, right: i32) i32 = "my_add"
```

- `.c` 路徑相對於宣告它的 `.zy` 檔案。
- C symbol 與 library 名稱會驗證，不能放入 compiler flags。
- native 參數目前只支援 scalar 與 `str`；回傳支援 scalar、`str`、void。
- native function 暫時不能直接宣告 `throws Error`。
- native C 會與產生的 C 一起編譯；載入 C 仍等於信任該模組能取得程式權限。

## 19. 標準庫現況

| import | 已有功能 |
|---|---|
| `<std/io>` | `print(str)`、`eprint(str)` |
| `<std/list>` | 過渡期泛型 helper；List 本身是語言內建型別 |
| `<std/path>` | 路徑組合、拆分、正規化與檔案類型查詢 |
| `<std/option>` | `is_null(T | null)`、`is_some(T | null)` |
| `<std/error>` | `require(bool, str) throws Error` |
| `<std/process>` | `args(GET_ARGS__)`、`executable(GET_EXE__)` |
| `<std/thread>` | `sleep_ms`、`yield_now`、`cpu_count` |
| `<std/request>` | HTTP GET/POST/PUT/DELETE/download |
| `<std/server>` | blocking `serve`、`serve_once` |
| `<std/gui>` | `Application`、Panel/Label/Button/Column widget、raylib drawing/event |
| `<std/editor>` | UTF-8 文件、開啟、儲存、選取、游標、補全狀態 |

`std/gui` 是跨平台 raylib native 模組，不使用 Python 或 Tk。`std/request` 在
Windows 使用 WinHTTP，在 Linux/macOS 使用動態載入的 libcurl。

尚未完成：owned `std/string`、`Ref<T>`、`Raw<T>`、`Channel<T>`，以及 v2
closure、綁定 method value 與 native callback ABI。具名頂層函式值和 struct
callback 欄位已可使用。

## 20. 可直接執行的完整範例

請看 `examples/v2_current_language_guide.zy`。它同時示範 public struct、private
field/method、tuple、optional、Error、List、Box ARC、metadata、`TYPEOF__`、
命令列參數與 Task。
