# ZyenLang 0.2.1

**繁體中文** | [English](README.md)

ZyenLang 是一門精簡、強型別並編譯成 C 的程式語言。設計目標是用少量規則，
以變數、結構體與函式組成清楚且高效的程式。正式編譯器統一使用 `zy`。

## 下載

[GitHub Releases](https://github.com/Ryan-2013/zyenlang/releases) 提供：

- Windows x64 MSI 與 portable ZIP；
- Linux x64、Linux arm64、macOS x64、macOS arm64 portable 壓縮包；
- VS Code 擴充套件；
- Python wheel 與原始碼套件。

portable 與 MSI 內含 Zig 0.16.0 C 工具鏈，不要求另外安裝 Python、pip、GCC
或 MSYS2。Windows MSI 安裝於目前使用者目錄並設定使用者 PATH。

## Hello World

```zy
import <std/io> as io

struct Counter {
    public value: i32 = 0
}

public fn (counter: Counter) add(amount: i32) i32 {
    return counter.value + amount
}

fn main() i32 {
    let counter: Counter = Counter{value: 40}
    io.print(f"answer={counter.add(2)}")
    return 0
}
```

```powershell
zy check main.zy
zy run main.zy
zy run main.zy -- first-argument second-argument
zy build main.zy -o main.exe --release
zy build main.zy -o main.c
```

## 語言能力

- `i8` 到 `i64`、無號整數、浮點數、`bool`、`str` 與明確型別推斷；
- f-string、結構體、public/private 欄位與 receiver method；
- 泛型函式、強型別 `List<T>`、tuple 多回傳值；
- `T | null`、`if let`、`throws Error`、`stop`、`catch`、`recover`；
- `while`、`break`、`continue`、`Task<T>`、`spawn` 與 `await`；
- `TYPEOF__`、`FILE__`、`GET_ARGS__`、`GET_EXE__`；
- 標準模組、套件模組、相對模組與受檢查的 native C 宣告；
- ARC 管理的 `Box<T>`、`List<T>` 與含 managed 欄位的結構體。

完整內容請看[語言指南](docs/v2_language_guide_zh_TW.md)、
[標準庫指南](docs/v2_stdlib.md)與[語言範例](examples/v2_language_tour.zy)。

## 套件管理

```powershell
zy pkg init --name my-app
zy pkg add ../math-lib
zy pkg install --locked
zy pkg list
```

套件從 `src/` 匯入，例如 `import <math-lib/math> as math`。目前支援本機路徑
相依、固定 lockfile、SHA-256 內容快取與安全的套件根目錄檢查。

## 標準庫

v0.2 標準庫包括 typed List/error/option、檔案系統與路徑、行程參數、執行緒、
HTTP client/server，以及以 raylib 為底層的跨平台 GUI。標準庫與第三方模組使用
相同的 import 與 native 宣告規則。

```powershell
zy run examples/v2_file_tree.zy -- . tree.txt
zy run examples/v2_gui_basic.zy --release
```

## VS Code

在 VS Code 執行 **Extensions: Install from VSIX...** 安裝
`zyenlang-vscode-0.2.1.vsix`。擴充套件提供語法高亮、自動補全、Hover、參數提示、
定義跳轉、參考搜尋、文件大綱、即時 `zy check`、Run 與 Build。

## 原始碼安裝

```powershell
git clone https://github.com/Ryan-2013/zyenlang.git
cd zyenlang
python -m pip install -e .
zy --version
python -m pytest -q
```

## 安全邊界

`zy check` 只解析與型別檢查，不會編譯或執行 native C；`zy build` 與 `zy run`
會編譯 `native source`，因此陌生專案仍應像 C 專案一樣先審查。ARC 也不會讓
外部 C 函式庫或 borrowed native resource 自動獲得 Rust 等級的記憶體安全。

編譯器與標準庫實作位於 `zyenlang/v2`。正式倉庫和發行包只包含 ZyenLang 0.2。

授權：MIT。
