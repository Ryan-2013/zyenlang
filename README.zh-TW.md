# ZyenLang 0.3.0

**繁體中文** | [English](README.md)

ZyenLang 是一門精簡、強型別並以 C11 為第一個後端的原生語言。0.3 是刻意的
不相容升級：模組與 static member 使用 `::`，struct 只保存資料，class 負責
方法與 ARC 身份，而一般程式改用安全引用，不再直接操作裸指標。

編譯器、專案管理器、相依解析器、runtime、標準庫與 VS Code 支援統一由 `zy`
提供。

## 版本狀態

`v0.3.0` 是原始碼版本。本輪不建立 GitHub Release，也不發布 MSI 或 portable
壓縮包；安裝資產會在各平台個別驗證後再發布。

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
- tuple、多回傳值、解構，以及 null check 後縮窄的 `T | null`。
- 具名函式、closure、callback、回傳函式與連續呼叫。
- local/parameter 可用的 `&T` 與唯一 `&mut T` 安全引用。
- 自動清理、`CLONE__()`、`CLONE_REF__()`、`REF_SET__()`、提早
  `DROP__()` 與 LIFO `defer`。
- `throws Error`、`stop`、`catch`、型別正確的 `recover`，以及未處理錯誤的
  原始碼位置與函式 stack。
- `spawn`/`await`、跨平台標準庫、native 宣告與編譯器原生 `.zlcm.h` 模板。

所有 intrinsic 都有括號，例如 `TYPEOF__()`、`FILE__()`、`GET_ARGS__()`、
`LIST_LEN__()`、`STR_SLICE__()`、`CLONE__()` 與 `DROP__()`。

完整說明請看[語言指南](docs/v3_language_guide_zh_TW.md)、
[編譯器架構](docs/v3_architecture.md)、[標準庫](docs/v3_stdlib.md)與
[0.2 遷移指南](docs/migration_v0_3.md)。

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

主要命令為 `zy new/init/add/remove/fetch/check/build/run/test/clean/metadata/emit`。
第一版相依來源是本機 path 或固定 revision 的 Git；`zy.lock` 記錄 commit、digest
與 transitive graph。目前沒有 registry 或 build script hook。

`c-source` target 會產生 C11 source、C/C++ header、runtime/native source bundle
與 JSON build metadata。只有 `export fn` 進入公開 C header。

## C 模組

```zy
import std::c_module as c_module

fn main() i32 {
    let math: c_module::Module = c_module::load("math.zlcm.h")
    return math.add(20, 22) - 42
}
```

`c_module::load()` 只存在於編譯期。模板會成為按絕對路徑雜湊的隱藏強型別，
並把 C source、header、library 與 flags 交給同一次 native build；不執行 Python
CLI，也沒有 runtime dynamic loader。0.3 ABI 支援固定寬度數字、字串、
`ZLC_STRUCT` 與 `ZL_Function` callback。裸指標與 `List<T>` 不屬於穩定 C ABI，
應由相容層封裝成 opaque handle。詳見 [c_module](docs/c_module.md)。

## VS Code 與安全邊界

`ide/vscode/zyenlang` 提供 v0.3 語法高亮、自動補全、outline、hover、signature
help、定義跳轉、可取消的即時檢查、專案 Run/Build 與單檔 C emission。未信任
workspace 不會執行編譯器。

`zy check` 不會呼叫 C 編譯器；`zy build` 與 `zy run` 會編譯 native source，
所以陌生專案必須像陌生 C 專案一樣先審查。ARC 不處理循環，也不會讓外部 C
資源自動 thread-safe；native code 可以破壞語言的記憶體模型。

授權：MIT。
