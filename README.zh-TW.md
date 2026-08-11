# ZyenLang 0.2.1

**繁體中文** | [English](README.md)

ZyenLang 是一門精簡、具有靜態檢查並編譯成 C 的語言。設計目標是用少量
語法建立有結構的程式：整個核心圍繞值、結構體與函式。

0.2 版帶來全新的 lexer、parser、typed AST/IR、語意檢查器、模組載入器與
C backend。新語法正式使用 `zy`，`zy2` 保留為相同編譯器的相容名稱；舊版
v0.1 程式使用 `zy1` 或 `zy legacy`。

## 下載

[GitHub Releases](https://github.com/Ryan-2013/zyenlang/releases) 提供：

- `zyv201-windows-x64.msi`：Windows 使用者層級安裝程式，自動設定 PATH；
- `zyv201-windows-x64.zip`：Windows 免安裝版；
- Linux x64、Linux arm64、macOS x64 與 macOS arm64 壓縮檔；
- `zyenlang-vscode-0.2.1.vsix`：VS Code 補全、跳轉、診斷、執行與建置；
- Python wheel 與原始碼套件。

portable 與 MSI 都內含編譯器 runtime 和固定版本、經 SHA-256 驗證的 Zig
0.16.0，不必另外安裝 Python、pip、GCC 或 MSYS2。

MSI 會安裝到 `%LOCALAPPDATA%\Programs\ZyenLang`，只修改目前使用者的
PATH；解除安裝時只移除自己的 PATH 項目。正式版附 SHA-256 checksums 與
GitHub artifact provenance。0.2.1 的 MSI 尚未使用付費 Authenticode 憑證
簽章，因此 Windows 仍可能顯示 SmartScreen 提示。

## 第一個程式

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
    let answer: i32 = counter.add(2)
    if answer == 42 {
        io.print("hello from ZyenLang 0.2")
    }
    return 0
}
```

```powershell
zy check main.zy
zy run main.zy
zy build main.zy -o main.exe --release
zy build main.zy -o main.c
```

## 目前語言能力

- `i8` 到 `i64`、無號整數、浮點數、`bool`、`str`；
- 傳統 `let name: Type = value` 與自動型別推斷；
- 以換行結束敘述，不使用分號；
- struct 預設值、public/private 欄位與 receiver method；
- 函式預設參數，包含匯入函式與預設參數後接必要參數；
- 具名頂層函式值、受檢查的間接呼叫與 struct callback 欄位；
- 多回傳值與 tuple 解構；
- 泛型函式與強型別 `List<T>`；
- `T | null` 與 `if let`；
- `throws Error`、`stop`、`catch`、`recover`；
- `while`、賦值、`break`、`continue`；
- `spawn` 與只能 `await` 一次的線性 `Task<T>`；
- `TYPEOF__ value Type` 編譯期型別判斷；
- 一般模組、標準模組、套件模組，以及受檢查的 native C 宣告。

完整內容請看 [0.2 編譯器架構](docs/v2_architecture.md)、
[標準庫說明](docs/v2_stdlib.md) 與
[語言範例](examples/v2_language_tour.zy)。

## 套件管理器

ZEP-0017 第一階段支援本機路徑依賴、固定 lockfile 與 SHA-256 內容快取：

```powershell
zy pkg init --name my-app
zy pkg add ../math-lib
zy pkg install --locked
zy pkg list
```

套件從 `src/` 匯入，例如 `import <math-lib/math> as math`。完整格式、
安全規則與第一版限制請看[套件管理器說明](docs/package_manager.md)。

## VS Code

在 VS Code 執行 **Extensions: Install from VSIX...**，選擇
`zyenlang-vscode-0.2.1.vsix`。擴充套件具有語法高亮、自動補全、Hover、
參數提示、F12 定義跳轉、尋找參考、文件大綱、即時 `zy2 check`、Run 與
Build。

未受信任的 workspace 不會執行編譯器。即時檢查具有 debounce、取消、檔案
大小、輸出大小與逾時限制；Run/Build 使用 VS Code process task，不把路徑
拼成 shell 指令。

## 安全邊界

`zy2 check` 只解析與型別檢查，不會編譯或執行 native C。`zy2 build` 和
`zy2 run` 會依設計編譯 `native source`，所以陌生的 ZyenLang 專案應視同
陌生的 C 專案，先審查再建置。

編譯器會限制原始碼大小、import 深度、模組數量，並驗證 native symbol 與
link library 名稱。`zyenv` 和工具鏈下載器拒絕 ZIP path traversal、symlink
與特殊封存項目。

ZyenLang 仍是實驗性語言。ARC 不會自動讓 borrowed native resource 或外部
C 函式庫變得安全，本專案不宣稱具備 Rust 等級的記憶體安全。

## 從原始碼安裝

```powershell
git clone https://github.com/Ryan-2013/zyenlang.git
cd zyenlang
python -m pip install -e .
zy2 --version
python -m pytest -q
```

既有 v0.1 程式使用 `zy1` 或 `zy legacy`。舊語法與 ZEP 保留在
[v0.1 語法文件](docs/current_syntax_zh_TW.md) 與
[zyenlang-zeps](https://github.com/Ryan-2013/zyenlang-zeps)。

編譯器原始碼已依世代分開：`zyenlang/v1` 保存 v0.1 transpiler 與標準庫，
`zyenlang/v2` 則放目前的強型別編譯器及其標準庫。像
`zyenlang.transpiler` 這類根層模組只保留為既有 Python 工具的相容別名。

授權：MIT。
