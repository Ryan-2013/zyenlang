# ZyenLang 0.3.7

**繁體中文** | [English](README.md)

ZyenLang 是一門精簡、強型別並以 C11 為第一個後端的原生語言。0.3 是刻意的
不相容升級：模組與 static member 使用 `::`，struct 只保存資料，class 負責
方法與 ARC 身份，而一般程式改用安全引用，不再直接操作裸指標。

編譯器、專案管理器、相依解析器、runtime、標準庫與 VS Code 支援統一由 `zy`
提供。

## 版本狀態

0.3.7 將 class 欄位改為預設 public、統一不分可見性的 `init` 建構規則，並明確
區分 struct 借用與 class ARC handle 的直接傳遞。0.3.6 補完整體型別標註的
多結果解構宣告與解構賦值。
0.3.5 在官方套件、系統庫與 C Interop ABI v3 上加入 `List<T, Rank>`、
執行期 shape 的 `LIST_FILLED__()`、跨行 postfix chain 與矩形 List shape
查詢。本輪不建立 tag、GitHub Release、MSI 或 portable 壓縮包；安裝資產會在
各平台個別驗證後再發布。

ZyenLang 仍是實驗性語言，適合驗證語言設計與撰寫小型原生程式，但目前不承諾
長期語法相容或 Rust 等級的安全性。

## 從原始碼安裝

```powershell
git clone https://github.com/Ryan-2013/zyenlang.git
cd zyenlang
python -m pip install -e .
zy --version
zy doctor
```

建置原生產物需要 C11 編譯器。`zy` 會依序尋找隨附 Zig、GCC、Clang 或 `cc`，
也能以 `ZY_CC` 指定工具鏈。

## 第一個專案

```powershell
zy new hello
cd hello
zy run
```

`src/main.zy`：

```zy
import std::io as io

public struct Point {
    public x: i32
    public y: i32
}

public class Counter {
    private value: i32

    public init(value: i32) {
        this.value = value
    }

    public mut fn add(amount: i32) void {
        this.value += amount
    }

    public fn get() i32 {
        return this.value
    }
}

fn main() i32 {
    let point = Point{x: 3, y: 4}
    let counter = Counter(point.x * point.x + point.y * point.y)
    counter.add(17)
    io::print(f"answer={counter.get()}")
    return 0
}
```

符號規則固定：

- `io::print()` 是 module function。
- `Cache<i32>::create()` 是 class static function。
- `counter.get()` 是 class instance method。
- `point.x` 是實例欄位。

## 語言能力

- 固定寬度數字、`bool` 與 ARC UTF-8 `str`。
- 純資料值語意 `struct`；具有 ARC 身份的 `class`，支援 `init`、`deinit`、
  唯讀 `fn`、可變 `mut fn` 與 `static fn`。
- 泛型函式、泛型 class 與 reachable monomorphization。
- 強型別 `List<T>`，backing buffer 使用 ARC 與 copy-on-write。
- 強型別多結果值、解構宣告與解構賦值，以及 null check 後縮窄的
  `T | null`。
- 具名函式、closure、callback、回傳函式與連續呼叫。
- local/parameter 可用的 `&T` 與唯一 `&mut T` 安全引用。
- 自動清理、`CLONE__()`、`CLONE_REF__()`、`REF_SET__()`、提早
  `DROP__()` 與 LIFO `defer`。
- `throws Error`、`stop`、`catch`、型別正確的 `recover`，以及未處理錯誤的
  原始碼位置與函式 stack。
- `spawn`/`await`、跨平台標準庫、native 宣告與編譯器原生 `.zlcm.h` 模板。

所有 intrinsic 都有括號，例如 `TYPEOF__()`、`FILE__()`、`GET_ARGS__()`、
`LIST_LEN__()`、`STR_SLICE__()`、`CLONE__()` 與 `DROP__()`。

完整說明請看[語言指南](docs/language_guide_zh_TW.md)、
[編譯器架構](docs/architecture.md)、[標準庫](docs/standard_library.md)與
[0.2 遷移指南](docs/migration_v0_3.md)。
全部入口整理在[文件索引](docs/README.md)；原始碼目錄分工請看
[專案結構](PROJECT_STRUCTURE.md)。

## 專案與產物

`zyproject.toml` 可以定義 `bin`、`c-source`、`staticlib`、`sharedlib` 多個 target：

```toml
[package]
name = "hello"
version = "0.3.0"
zyen = ">=0.3.0"

[build]
default-target = "app"
target-dir = "target"

[targets.app]
kind = "bin"
entry = "src/main.zy"
```

主要命令為 `zy new/init/install/uninstall/list/show/check/build/run/test/clean/metadata/emit`。
相依來源支援 registry、本機 path 或固定 revision 的 Git；`zy.lock` 記錄 commit、
digest 與 transitive graph。package 安裝不執行 build script 或 install hook。

第一批官方套件可直接安裝：

```powershell
zy install numpy
zy install opencv
```

GUI、HTTP client/server、fs、os、time、thread 與 process 屬於 `std::*`；數值
Array 與 OpenCV 則獨立版本化。編譯 OpenCV 程式時仍需安裝系統 OpenCV 開發檔。

`c-source` target 會產生 C11 source、C/C++ header、runtime/native source bundle
與 JSON build metadata。在函式上一行寫 `#symbol_name`，即可產生名稱穩定的
公開 C wrapper 並進入 header；ZyenLang 函式本體仍使用內部 mangled 名稱。
`export fn` 仍作為直接 symbol 匯出的相容語法。

## C 模組

```zy
import std::c_module as c

native module math = c::load("native/math.zlcm.h")

fn main() i32 { return math::add(20, 22) - 42 }
```

`c::load()` 只存在於編譯期。ABI v3 支援路徑雜湊 ARC Handle、同步 List Slice、
enum、flags、constant、out/inout、錯誤策略、字串、POD struct 與 callback。
`zy bindgen` 透過 Clang JSON AST 產生可審查模板及 C adapter；模糊 pointer 在
ownership 被明確設定前不會成為可呼叫函式。不執行 Python CLI，也沒有 runtime
dynamic loader。native adapter 可使用 C11 或 C++17。詳見
[c_module](docs/c_interop.zh_TW.md)。

若要用 ZyenLang 開發自製遊戲引擎，先閱讀
[遊戲引擎開發者語言手冊](docs/game_engine_language_handoff_zh_TW.md)，再使用
[遊戲引擎整合規格](docs/game_engine_integration_zh_TW.md)完成 C11 façade、ABI v3
Handle/Slice/callback、static/shared library、執行緒與資源生命週期。

## VS Code 與安全邊界

`ide/vscode/zyenlang` 提供 v0.3 語法高亮、自動補全、outline、hover、signature
help、定義跳轉、可取消的即時檢查、專案 Run/Build 與單檔 C emission。未信任
workspace 不會執行編譯器。

`zy check` 不會呼叫 C 編譯器；`zy build` 與 `zy run` 會編譯 native source，
所以陌生專案必須像陌生 C 專案一樣先審查。ARC 不處理循環，也不會讓外部 C
資源自動 thread-safe；native code 可以破壞語言的記憶體模型。

授權：MIT。
