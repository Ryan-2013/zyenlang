# 用 ZyenLang 寫安全指標與函式值

這是一篇可直接照著錄影的教學，也可以單獨當成文章發布。完整程式位於
`examples/pointer_function_tutorial.zy`。

## 影片定位

- 建議標題：**一個編譯成 C 的新語言，如何處理指標與函式指標？**
- 建議長度：8 至 10 分鐘
- 觀眾：懂一點 C、C++、Rust、Zig 或遊戲開發，第一次接觸 ZyenLang 的人
- 核心訊息：ZyenLang 保留 C-like 指標能力，但用 checked fat pointer 與自動 ARC
  管理 owned 資源；函式也能成為值、參數與回傳值。

## 開場旁白（0:00 至 0:25）

> 這是 ZyenLang，一個會編譯成 C 的實驗性語言。它可以直接呼叫 C 模組，
> 也支援型別化指標、巢狀指標、函式值與 closure。今天不做 Hello World，
> 我們直接看最容易出錯的兩件事：記憶體與函式指標。

畫面先快速顯示：

```zy
let *p: ptr<ptr<int>> = 10;
print(**p);
print(pick("add")(20, 22));
```

接著執行：

```powershell
zy run examples\pointer_function_tutorial.zy
```

預期輸出：

```text
10
21
110
7
42
```

## 第一章：普通區域變數不是 heap 配置（0:25 至 1:15）

```zy
fn main() -> int {
    let value: int = 10;
    print(value);
    return 0;
}
```

`let value: int = 10;` 使用 C automatic storage。它不會為每次宣告呼叫
`malloc`。函式結束後，這個區域變數的語意生命週期結束；C 編譯器也可能把它
放在 register，或重用 stack slot。

取得普通區域變數的位址時，得到 borrowed pointer：

```zy
let value: int = 10;
let borrowed: ptr<int> = &value;
print(*borrowed);
```

`borrowed` 沒有 ARC owner，只能在 `value` 仍存活時使用。不要把 `&local`
直接保存到會跨出 scope 的容器、closure 或 C library。

## 第二章：`let *` 建立 owned cell（1:15 至 2:20）

```zy
let *owned: ptr<int> = 10;
print(*owned);
set *owned = 20;
```

這裡的 `let *` 不是 C 宣告語法。它代表：

1. 配置一個保存 `int` 的 cell。
2. 建立 ARC control block。
3. 把 `10` 寫入 cell。
4. 讓 `owned` 持有一個 owned reference。

複製 pointer 會共享同一個 owner：

```zy
let alias: ptr<int> = owned;
set *alias = 30;
print(*owned); // 30
```

最後一個 reference 離開 scope 時，payload 才會釋放一次。

## 第三章：雙重與多重指標（2:20 至 3:25）

```zy
let *p: ptr<ptr<int>> = 10;
print(**p); // 10
```

編譯器會遞迴建立完整 chain。`p` 指向一個 `ZL_ptr` cell，內層 pointer 再指向
保存 `int` 的 cell。這不是 raw C `int**`，而是兩層都帶 owner 與 runtime type
tag 的 managed pointer。

更深的寫法也成立：

```zy
let *deep: ptr<ptr<ptr<int>>> = 42;
print(***deep); // 42
```

通常不應為了炫技堆很多層。巢狀 pointer 最適合 handle slot、可替換資源與 C
相容層；一般資料建議使用 struct。

## 第四章：`&**p` 與 owner 保留（3:25 至 4:15）

```zy
let *p: ptr<ptr<int>> = 10;
let *p2: ptr<ptr> = &**p;
set **p2 = 21;
print(**p); // 21
```

`&**p` 不是製造一個失去 owner 的 raw interior address。對 managed pointer 而言，
`&*pointer` 會化成同一個 pointer alias，保留 address、owner、ownership 與 type
tag。`ptr<ptr>` 的缺少部分會由初始化式推導，因此這裡會補成
`ptr<ptr<int>>`。

可讀性允許時，仍建議直接寫完整型別：

```zy
let *p2: ptr<ptr<int>> = &**p;
```

## 第五章：回傳 pointer 不會提早釋放（4:15 至 5:25）

```zy
fn ret_ptr() -> ptr<int> {
    let *a: ptr<int> = 110;
    return a;
}

fn main() -> int {
    let a: ptr<int> = ret_ptr();
    print(*a); // 110
    return 0;
}
```

回傳時編譯器會先保留要回傳的 owned reference，再清理函式內的區域 alias，
最後把 ownership 交給呼叫端。區域名稱是否都叫 `a` 不重要；每次呼叫都有自己
的 storage 與 ARC reference。

規則不是「函式裡配置的 pointer 永遠不釋放」，而是：

- 從回傳值可達的 managed fields 會遞迴保留並轉移。
- 沒有逃逸的 managed locals 仍在 scope 結束時釋放。
- Struct 回傳也套用同一規則。

```zy
struct ScoreBox {
    let this.value: ptr<int>;
}

fn make_score() -> ScoreBox {
    let *score: ptr<int> = 110;
    let box: ScoreBox = ScoreBox { value: score };
    return box;
}
```

`box.value` 會跟著回傳的 struct 存活。回傳 `box.value` 或 `&**chain` 時也會把
對應 owner 轉移給呼叫端。

## 第六章：函式值與函式指標（5:25 至 7:10）

先定義兩個具名函式：

```zy
fn add(a: int, b: int) -> int {
    return a + b;
}

fn sub(a: int, b: int) -> int {
    return a - b;
}
```

函式型別寫成 `fn(參數型別...) -> 回傳型別`：

```zy
let op: fn(int,int)->int = sub;
print(op(10, 3)); // 7
```

函式值可以作為參數：

```zy
fn apply(op: fn(int,int)->int, a: int, b: int) -> int {
    return op(a, b);
}
```

也可以作為回傳值：

```zy
fn pick(name: str) -> fn(int,int)->int {
    if (name == "add") {
        return add;
    }
    return sub;
}
```

回傳後呼叫：

```zy
let op: fn(int,int)->int = pick("sub");
print(op(10, 3));
```

或直接連續呼叫：

```zy
print(pick("add")(20, 22)); // 42
```

函式值使用位置參數。第二段連續呼叫沒有參數名稱資訊，因此不要寫具名參數。
具名 top-level function 不需要配置 owner；closure 則會用 ARC 管理捕捉環境。

真正需要「pointer 指向函式」時，使用 `ptr<fn(...)>`。它指向一個
`ZL_Function` 記憶體 cell：

```zy
let func_ptr: ptr<fn(int,int)->int> = &add;
print((*func_ptr)(20, 22));
print(*func_ptr(20, 22)); // ZyenLang 簡寫
```

`&add` 指向編譯器為 top-level function 建立的靜態 cell。要建立 ARC 管理的
heap cell，使用 owned pointer 寫法：

```zy
let *owned_ptr: ptr<fn(int,int)->int> = add;
```

函式指標也能先擦除成 `ptr<void>`，再以相同簽章還原：

```zy
let opaque: ptr<void> = func_ptr;
print(*(ptr<fn(int,int)->int>)opaque(12, 10));
```

呼叫前會檢查 pointer runtime tag 與 fn signature。不同函式簽章禁止互相
cast；`ptr<fn(...)>` 是 pointer to `ZL_Function` data cell，不是把 raw C
function pointer 塞進 `void*`。

## 第七章：安全邊界（7:10 至 8:10）

ZyenLang pointer 的 runtime 會檢查 `None` 與 `Freed`：

```text
None pointer dereference: p
Freed pointer dereference: p
```

仍要記住四個邊界：

- `&local` 是 borrowed，不是完整 borrow checker。
- ARC 不會自動處理 reference cycle。
- 單一 cell 沒有陣列 bounds metadata，不應做 pointer arithmetic。
- Raw C callback 或 pointer 被外部 library 保存時，相容層必須遵守 retain/release
  ABI。

## 結尾旁白（8:10 至 8:35）

> ZyenLang 想保留 C 的直接感，但把最常見的空指標、釋放後使用與 callback
> ownership 變成可檢查的規則。它還很年輕，不過已經能在 Windows、Linux 和
> macOS 上用同一份程式編譯執行。原始碼、可攜版與教學都在 GitHub。

畫面最後顯示：

```text
github.com/Ryan-2013/zyenlang
```

## 30 秒短版宣傳稿

> 如果一個新語言最後會編譯成 C，它還能讓指標更安全嗎？ZyenLang 支援
> `ptr<T>`、多層指標、函式值與 closure，owned pointer 使用自動 ARC，
> `None` 和釋放後使用會在 runtime 明確報錯。你甚至可以寫
> `pick("add")(20, 22)`，或用 c_module 接上既有 C library。Windows、Linux、
> macOS 可攜版已經可以直接下載。

## 貼文文案

**ZyenLang：編譯成 C，但不必把所有生命週期都交給運氣。**

這次用一個完整範例介紹：

- `let *p: ptr<int>` owned cell
- `ptr<ptr<int>>` 與 `**p`
- `&**p` 如何保留 ARC owner
- pointer 與含 pointer struct 的安全回傳
- `fn(int,int)->int` 函式值
- `pick("add")(20, 22)` 連續呼叫

專案與跨平台下載：<https://github.com/Ryan-2013/zyenlang>
