# ZyenLang v0.1.83

[English](README.md) | **繁體中文**

> **相關 repo**
> - **規範與慣例(ZEPs)**:[zyenlang-zeps](https://github.com/Ryan-2013/zyenlang-zeps)
> - **IDE(dogfood)**:[zyenlang-ide](https://github.com/Ryan-2013/zyenlang-ide)

ZyenLang 是一個實驗性的 C-like 程式語言,搭配 Python 風格的工具體驗。
`.zy` 原始碼會被轉譯成 C,再交給 Zig、gcc 或 clang 編譯。

目前核心語法、ARC pointer、函式值、closure 與原生 C bridge 的完整寫法，
集中在[目前語法總覽](docs/current_syntax_zh_TW.md)。

想快速認識 pointer 與函式值，可直接閱讀或照著錄製
[安全指標與函式值影片教學](docs/tutorial_pointer_function_video_zh_TW.md)。

定位:機器人、電腦視覺、控制系統、嵌入式風格實驗,以及小型引擎原型。

## Portable 下載

GitHub Release 提供 Windows、Linux 與 macOS 的可直接執行壓縮檔，內含
獨立 `zy` CLI、Zig C toolchain、跨平台 raylib GUI runtime、範例、文件與
預先編譯的 GUI demo。不必另外安裝 Python、`pip` 或 C 編譯器。

v0.1.83 請下載對應平台的 `zyv183` 壓縮檔。解壓後的根資料夾也叫
`zyv183`，而且 `zy` 就放在根目錄。Windows 解壓後可直接雙擊
`add-to-user-path.cmd` 一鍵加入使用者 `PATH`；Linux 與 macOS 執行一次
`add-to-user-path.sh` 即可：

```powershell
cd zyv183
.\add-to-user-path.cmd
.\zy.exe run examples\hello.zy
.\zy.exe run examples\tk_portable_smoke.zy
```

Linux 與 macOS 執行 `./add-to-user-path.sh`，並將 `zy.exe` 改成 `./zy`。詳見
[portable 發行指南](docs/portable_release.md)。

## 從原始碼安裝

```powershell
cd <repo-root>
python tools\check_layout.py
python -m pip uninstall zyenlang -y
python -m pip install -e .
python tools\install_vscode_extension.py
```

## CLI 指令

```powershell
zy check main.zy             # 只做語法 + 型別檢查
zy run main.zy               # 轉譯 + 編譯 + 執行
zy build main.zy -o main.c   # 只輸出 C
zy build main.zy -o main.exe # 輸出 .exe
```

## Hello world

```zy
fn add(a: int, b: int) -> int {
    return a + b;
}

fn main() -> int {
    let x: int = 10;
    let y: int = 20;
    print((str)add(x, y));
    return 0;
}
```

## 語言表面（v0.1.83）

- **變數**:`let a = v;`、`let a: T = v;`、`const a = v;`
- **修改一律寫 `set`**:`set a = v;`、`set a += v;`、`set *p = v;`
- **流程控制**:`if (...) {}`、`else { ... }`、`for (init; cond; step) {}`,無限迴圈 `for (;;) {}`。**沒有 `while`**。
- **函式**:`fn name(args) -> T { ... }`。支援尾端預設參數、前置宣告、第一級 `fn(...) -> T` 函式值，以及 managed `ptr<fn(...)>` 函式 cell 指標。
- **結構**:`struct S { let this.field: T; fn method() -> T { ... } }`
- **型別**:`int`、`float`、`bool`、`str`、`List`、`ptr<T>`、`ptr<void>`、`fn(...) -> T`、`ptr<fn(...)>`、struct、`void`、`None`。(`Any` 只用在 `List` 內部,使用者拿不到。)
- **輸出**:`print(expr)` 只接受 `str`；其他型別使用 `print((str)value)` 或 f-string。
- **f-string**:`f"i={i}"` 的結果永遠是 `str`，插值內容會自動格式化支援的型別。
- **import**:`import <std/math>;`、`import <std/math> as m;`、`import "lib.zy" as lib;`。相對路徑以「寫 import 的那個 `.zy` 檔案所在資料夾」為基準,不是終端機目前目錄。
- **限定型別**:`let car: test.Car = test.Car { model: "Honda" };` —— 被 import 的 user struct 仍在全域命名空間,前綴只是被 strip 掉。
- **多行語句**:函式簽章、函式呼叫、`if` 條件、`for` 表頭、list literal 可在它們的 `(...)`、`[...]`、`{...}` 內換行。

## 檔案頂層規則

頂層只允許 `import`、`struct`、`fn`。**不能寫頂層 `let` 或 `const`**。
若需要常數,用零參數函式包起來:

```zy
fn max_pwm() -> int {
    return 1000;
}
```

## 修改規則(mutation)

```zy
let x: int = 1;
set x += 1;     // 正確
set x = x + 1;  // 正確

x += 1;         // 錯誤 —— 任何修改都要 `set`
```

`for` 表頭第三段支援兩種寫法:

```zy
for (let i: int = 0; i < 10; i += 1) {
}

for (let i: int = 0; i < 10; set i += 1) {
}
```

## 函式 cell 指標

`ptr<fn(P...)->R>` 指向受檢查的 `ZL_Function` 記憶體 cell。`&add` 會借用
編譯器建立的靜態 cell；`let *` 則建立可保存 closure 的 ARC owned cell：

```zy
let pointer: ptr<fn(int,int)->int> = &add;
print(f"{(*pointer)(20, 22)}");
print(f"{*pointer(20, 22)}");

let opaque: ptr<void> = pointer;
print(f"{*(ptr<fn(int,int)->int>)opaque(12, 10)}");
```

呼叫前會檢查 pointer 狀態、runtime cell tag 與完整函式簽章。不同函式指標
簽章不能互相 cast。

## 預設參數與前置宣告(v0.1.49 新增)

### 尾端預設參數

```zy
fn add(a: int, b: int = 10) -> int {
    return a + b;
}

fn main() -> int {
    print((str)(add(5)));     // 15
    print((str)(add(5, 2)));  // 7
    return 0;
}
```

規則:

- 預設參數**必須**在尾端。
- `fn bad(a: int = 1, b: int) -> int` 會被拒絕。
- 預設值由轉譯器在呼叫端展開,C 簽章不會有預設值。
- 預設值應該是字面值或呼叫端看得到的單純表達式。

### 前置函式宣告

ZyenLang 本來就會自動生 C prototype,v0.1.49 也接受顯式宣告:

```zy
fn scale(a: int, factor: int = 2) -> int;

fn main() -> int {
    print((str)(scale(7)));    // 14
    print((str)(scale(7, 3))); // 21
    return 0;
}

fn scale(a: int, factor: int) -> int {
    return a * factor;
}
```

規則:

- 每個宣告**必須**有對應的定義。
- 定義可省略已經在宣告寫過的預設值。
- 若宣告與定義都給同一個參數預設值,必須完全相同。
- 簽章不對就直接拒絕。

### 方法的預設參數

struct 的 method 一樣可以用預設參數:

```zy
struct Counter {
    let this.base: int;

    fn add(x: int = 1) -> int {
        return this.base + x;
    }
}
```

## 多行語句範例

```zy
fn add(
    a: int,
    b: int
) -> int {
    return a + b;
}

fn main() -> int {
    let n: int = add(
        10,
        20
    );

    for (
        let i: int = 0;
        i < 10;
        i += 1
    ) {
        print((str)(i));
    }

    return 0;
}
```

## List 的結構式方法呼叫

使用者定義的 struct 可以直接放入同一個 `List`。只要所有可能的元素型別
都有同名且簽章完全相同的方法，就能直接從 `get()` 或 `pop()` 呼叫，不必
另外宣告 interface、trait 或繼承關係：

```zy
struct Dog {
    fn bark() -> void {
        print("dog");
    }
}

struct Cat {
    fn bark() -> void {
        print("cat");
    }
}

fn main() -> int {
    let animals: List = [Dog {}, Cat {}];
    for (let i = 0; i < animals.len(); set i += 1) {
        animals.get(i).bark();
    }
    return 0;
}
```

可能的元素型別由編譯器保存在隱藏 metadata，`Any` 仍不是公開語法。共同
方法只接受位置參數，因為參數名稱不屬於共同形狀。當 List 經過一般 `List`
參數而失去靜態型別集合時，runtime 會檢查實際 boxed struct，型別不符會產生
明確錯誤。

List 會複製 struct 值並放入 ARC box。`get()` 借用 box，`set()` 與
`clear()` 釋放移除的 box，`pop()` 則轉移引用。需要取回具體值時可寫
`let dog: Dog = (Dog)animals.pop();`。

## 標準函式庫

### Round 3:集合(collections)
`std/list`、`std/stack`、`std/queue`、`std/map`、`std/set`

### Round 2:工具(tooling)
`std/path`、`std/text`、`std/log`、`std/test`、`std/config`、`std/csv`

### 核心 / runtime
`std/string`、`std/char`、`std/fs`、`std/cmd`、`std/term`、`std/time`、
`std/math`、`std/mem`、`std/ptr`、`std/random`、`std/stats`

### 領域模組
`std/tk`(Tk 風格 GUI bridge)、`std/cv`、`std/gpu`、`std/units`、
`std/filter`、`std/trajectory`、`std/robot`、`std/pid`、`std/motor`、
`std/control`、`std/thread`、`std/coroutine`、`std/geometry`、
`std/buffer`、`std/bit`、`std/range`、`std/ease`、`std/check`

### 集合範例

```zy
import <std/stack>;
import <std/queue>;
import <std/map>;
import <std/set>;

fn main() -> int {
    let s: Stack = stack.new();
    s.push_str("first");
    s.push_str("second");
    print((str)(s.pop_str()));

    let q: Queue = queue.new();
    q.push_str("first");
    q.push_str("second");
    print((str)(q.pop_str()));

    let m: StringMap = map.new();
    m.put("name", "ZyenLang");
    print((str)(m.get("name", "none")));

    let tags: StringSet = set.new();
    tags.add("robotics");
    tags.add("cv");
    print((str)(tags.len()));

    return 0;
}
```

### 測試 harness 範例

```zy
import <std/test>;

fn main() -> int {
    let t: TestStats = test.new_stats("demo");
    t.eq_int("math", 1 + 1, 2);
    t.eq_str("name", "Zyen", "Zyen");
    return t.summary();
}
```

## 範例與測試

```powershell
zy run examples\add.zy
zy run tests\text_test.zy
```

- `examples/` —— 93 支單檔範例
- `tests/` —— 70 支 `.zy` 測試 / 錯誤 fixture 與 7 組 Python 整合測試
- `apps/` —— 完整應用程式(`apps/zyide.zy`、`apps/zyide_gui.zy`、`apps/zytk_demo.zy`)

## 專案結構

```
zyenlang/             # Python 轉譯器 + 內附 std
zyenlang/std/         # 標準函式庫正本(import <std/...> 會載這份)
std/                  # 標準函式庫的源樹鏡像(方便瀏覽)
examples/             # 93 支 .zy 範例
tests/                # 70 支 .zy fixture + 7 組 Python 整合測試
docs/                 # 規格與筆記
apps/                 # 完整應用程式
tools/                # 安裝 / 修補腳本
ide/                  # VSCode + Zed 編輯器設定
```

## 狀態

v0.1.50 是第一個跨平台 portable 正式版。語法表面（語法 + stdlib API）**故意保持小** ——
不打算加 lambda、spread / destructuring、`**kwargs`、comprehension,
或其他 JS / Python 風格的語法糖。後續精力會花在修小毛病,不會擴張表面。

## 授權

MIT。見 [`LICENSE`](LICENSE)。
