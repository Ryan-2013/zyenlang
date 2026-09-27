# ZyenLang 0.3.1 遊戲引擎整合規格

這份文件是交給遊戲引擎開發者或實作引擎的 AI 使用的技術交接。請以本文和
[C Interop ABI v3](c_interop.zh_TW.md) 為準，不要從舊版 ZyenLang 範例推測語法。
尚未熟悉 ZyenLang 語法時，請先閱讀
[給遊戲引擎開發者的語言手冊](game_engine_language_handoff_zh_TW.md)。

## 目標

讓 ZyenLang 可以安全且高效地負責：

- 遊戲規則、場景流程和 UI 邏輯；
- entity/component 的批次更新；
- input、audio、render、asset 和 physics API 呼叫；
- callback 與事件處理；
- class、List、closure 和 ARC 管理的遊戲端狀態。

引擎本體可以使用 C、C++、Rust 或其他語言，但必須提供穩定的 C11 façade。
ZyenLang 不直接解析 C++ class、template、union、bitfield、varargs 或 C++ ABI。

## 建議架構

目前最完整、最安全的模式是：

```text
ZyenLang game executable
    -> native module namespace
    -> engine_zy.c adapter
    -> stable engine C API
    -> C++/Rust engine internals
```

也就是由 ZyenLang 擁有 `main`、遊戲狀態與 frame loop，引擎提供 window、render、
audio、input、asset、physics 等 native API。這個模式可以完整使用 ZyenLang
class、List、closure、error handling 和 ARC。

反方向也可將 ZyenLang 建成 `staticlib` 或 `sharedlib` 讓引擎呼叫，但目前公開
export ABI 只支援固定寬度數字、`bool`、`void` 與 `ZL_String`。class、List、
Handle、reference、closure、generic 與 throwing function 不能直接 export。
因此 shared-library plugin 第一版應使用簡單入口，或讓持久狀態留在引擎側。

## 專案配置

建議目錄：

```text
game/
  zyproject.toml
  src/
    main.zy
    game.zy
    native/
      engine.zlcm.h
      engine_zy.c
      engine_zy.h
  vendor/
    include/
    windows-x64/
    linux-x64/
    macos-arm64/
```

`zyproject.toml`：

```toml
[package]
name = "my-game"
version = "0.1.0"
zyen = ">=0.3.1"

[build]
default-target = "game"
target-dir = "target"

[targets.game]
kind = "bin"
entry = "src/main.zy"
output-name = "my-game"

[dependencies]
```

建置與執行：

```powershell
zy check game
zy build game --release
zy run game -- [game arguments]
```

## 引擎 C API 設計規則

引擎需提供窄而穩定的 C API，不可把 C++ object layout 暴露給 ZyenLang：

```c
#ifndef ZE_ENGINE_H
#define ZE_ENGINE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ZE_Engine ZE_Engine;
typedef struct ZE_Texture ZE_Texture;

typedef struct ZE_Vec2 {
    float x;
    float y;
} ZE_Vec2;

ZE_Engine* ze_engine_create(const char* title, int32_t width, int32_t height);
void ze_engine_destroy(ZE_Engine* engine);
int32_t ze_engine_begin_frame(ZE_Engine* engine, float delta_seconds);
int32_t ze_engine_present(ZE_Engine* engine);
bool ze_engine_running(const ZE_Engine* engine);
const char* ze_engine_last_error(void);

#ifdef __cplusplus
}
#endif

#endif
```

固定規則：

- C header 必須可由 C11 compiler 解析。
- 每個公開 struct 都要有固定欄位順序與固定寬度型別。
- C++ object 一律以 opaque pointer 表示，再由 `ZL_Handle` 包裝。
- 不在 ABI 使用 `long`、compiler-specific enum size 或 STL container。
- 不直接傳 C++ exception；轉成 status、NULL 或負數錯誤碼。
- 每個 owned resource 都要有唯一、可重複推理的 destructor。
- API version 必須可查詢，啟動時拒絕不相容版本。
- 一個函式是否保存字串、callback 或 buffer 必須明確寫入契約。

## ABI v3 模板

`src/native/engine.zlcm.h`：

```c
ZLC_ABI(3)
ZLC_MODULE(engine)

ZLC_HEADER("engine_zy.h")
ZLC_SOURCE("engine_zy.c")

ZLC_INCLUDE_DIR("../../vendor/include")

ZLC_LIB_DIR_WINDOWS("../../vendor/windows-x64")
ZLC_LIB_WINDOWS("zyengine")

ZLC_LIB_DIR_LINUX("../../vendor/linux-x64")
ZLC_LIB_LINUX("zyengine")

ZLC_LIB_DIR_MACOS("../../vendor/macos-arm64")
ZLC_LIB_MACOS("zyengine")

ZLC_HANDLE(Engine, ZLC_DROP(zy_engine_drop))
ZLC_HANDLE(Texture, ZLC_DROP(zy_texture_drop))

ZLC_ENUM(Key, i32,
    ZLC_CASE(escape, 256),
    ZLC_CASE(space, 32))

ZLC_FLAGS(TextureFlags, u32,
    ZLC_CASE(filtered, 1),
    ZLC_CASE(repeat, 2))

ZLC_CONST(ENGINE_ABI_VERSION, u32, 3)

ZLC_STRUCT(Vec2,
    ZLC_FIELD(x, f32),
    ZLC_FIELD(y, f32))

ZLC_FN(create, zy_engine_create, optional<owned<Engine>>,
    ZLC_PARAM(title, str),
    ZLC_PARAM(width, i32),
    ZLC_PARAM(height, i32),
    ZLC_FAIL(null, zy_engine_last_error))

ZLC_FN(begin_frame, zy_engine_begin_frame, i32,
    ZLC_PARAM(engine, borrowed<Engine>),
    ZLC_PARAM(delta_seconds, f32),
    ZLC_FAIL(nonzero, zy_engine_last_error))

ZLC_FN(present, zy_engine_present, i32,
    ZLC_PARAM(engine, borrowed<Engine>),
    ZLC_FAIL(nonzero, zy_engine_last_error))

ZLC_FN(running, zy_engine_running, bool,
    ZLC_PARAM(engine, borrowed<Engine>))

ZLC_FN(upload_bytes, zy_engine_upload_bytes, void,
    ZLC_PARAM(engine, borrowed<Engine>),
    ZLC_PARAM(data, Slice<u8>))
```

使用端：

```zy
import std::c_module as c
import std::io as io

native module engine = c::load("native/engine.zlcm.h")

fn main() i32 {
    let app: engine::Engine = engine::create("My Game", 1280, 720) catch err {
        io::eprint(err.message)
        return 1
    }

    while engine::running(app) {
        engine::begin_frame(app, 0.016) catch err {
            io::eprint(err.message)
            return 2
        }

        engine::present(app) catch err {
            io::eprint(err.message)
            return 3
        }
    }

    return 0
}
```

`engine` 是 namespace，不是物件。正確寫法是 `engine::present(app)`，不是
`engine.present(app)` 或 `app.present()`。如果要提供物件式 API，可在 ZyenLang
內用 class 包一層，但底層 native ABI 仍維持 module function。

## C adapter 寫法

`engine_zy.c` 負責把原始 pointer 轉成 ABI v3 Handle：

```c
#include "engine_zy.h"
#include "zyenlang_c_abi.h"
#include "ze_engine.h"

#define ZY_ENGINE_TAG "my-engine:Engine:v1"

void zy_engine_drop(void* raw) {
    ze_engine_destroy((ZE_Engine*)raw);
}

ZL_Handle zy_engine_create(ZL_String title, int32_t width, int32_t height) {
    ZE_Engine* engine = ze_engine_create(zl_string_data(title), width, height);
    return zl_handle_adopt(engine, ZY_ENGINE_TAG, zy_engine_drop);
}

int32_t zy_engine_begin_frame(ZL_Handle handle, float delta_seconds) {
    ZE_Engine* engine = (ZE_Engine*)zl_handle_data(handle, ZY_ENGINE_TAG);
    return ze_engine_begin_frame(engine, delta_seconds);
}

int32_t zy_engine_present(ZL_Handle handle) {
    ZE_Engine* engine = (ZE_Engine*)zl_handle_data(handle, ZY_ENGINE_TAG);
    return ze_engine_present(engine);
}

bool zy_engine_running(ZL_Handle handle) {
    ZE_Engine* engine = (ZE_Engine*)zl_handle_data(handle, ZY_ENGINE_TAG);
    return ze_engine_running(engine);
}

void zy_engine_upload_bytes(ZL_Handle handle, ZL_Slice bytes) {
    ZE_Engine* engine = (ZE_Engine*)zl_handle_data(handle, ZY_ENGINE_TAG);
    ze_engine_upload(engine, bytes.data, bytes.len);
}

ZL_String zy_engine_last_error(void) {
    return zl_string_borrow(ze_engine_last_error());
}
```

不要把 `handle.data` 直接 cast。必須使用 `zl_handle_data()`，它會檢查 NULL、
type tag 和 consumed 狀態。

## Handle 與遊戲資源生命週期

Handle 規則：

- `owned<T>`：C 建立新資源，ZyenLang 接管一個引用。
- `borrowed<T>`：只借用傳入 Handle，不改變擁有權。
- `borrowed<T, parent>`：回傳的 view 保留 parent，parent 不會提前析構。
- `borrowed_static<T>`：資源由引擎全域持有，保證到 process 結束。
- `consumed<T>`：引擎接管 payload，所有 ZyenLang alias 立即失效。
- `optional<T>`：C NULL 對應 `T | null`。

Handle 可以放入 struct、class field 和 `List<T>`。複製 Handle 只增加 atomic
ARC refcount，不複製 GPU resource。最後一個引用離開 scope 或被 `DROP__()` 時，
destructor 執行一次。

Texture、Mesh、Sound 等獨立資源通常使用 `owned<T>`。只在 Engine/Scene 存活時
有效的 view 使用 `borrowed<View, parent>`。不要用 `borrowed_static` 包裝實際上會
在換場景、重載或關閉 device 時消失的物件。

ARC 不處理循環。若兩個引擎資源互相擁有，循環必須由引擎 API 主動解除。

## Buffer、List 與大量資料

ZyenLang `List<T>` 不能直接作為 C ABI 型別。同步呼叫時使用：

```zy
let vertices: List<f32> = [0.0, 0.0, 1.0, 1.0]
engine::upload_vertices(app, c::slice(&vertices))
```

需要由 C 寫回時：

```zy
let samples: List<f32> = [0.0, 0.0, 0.0, 0.0]
engine::read_audio(app, c::mut_slice(&mut samples))
```

- `Slice<T>` 是 `{data, len, element_size}` 的唯讀同步借用。
- `MutSlice<T>` 是唯一可變借用，建立前會做 List copy-on-write 分離。
- C 不可保存 Slice data、不可在 callback 晚點使用、不可交給其他執行緒。
- 空 Slice 合法；`len == 0` 時 C 不可解參考 data。
- 若引擎需要非同步上傳，adapter 必須在呼叫內複製資料，或建立 owned Buffer
  Handle。不可偷偷保存 Slice pointer。

大量 entity 或 draw call 不應每個物件跨 ABI 呼叫一次。建議提供 POD command
buffer 或 typed Buffer Handle：

```c
ZLC_STRUCT(SpriteCommand,
    ZLC_FIELD(texture_id, u32),
    ZLC_FIELD(x, f32),
    ZLC_FIELD(y, f32),
    ZLC_FIELD(rotation, f32))

ZLC_FN(draw_sprites, zy_draw_sprites, void,
    ZLC_PARAM(engine, borrowed<Engine>),
    ZLC_PARAM(commands, Slice<SpriteCommand>))
```

效能原則：一次傳一批資料，避免每個 entity、pixel、vertex 都呼叫 native API。

## out 與 inout

原始 C：

```c
int32_t ze_mouse_position(ZE_Engine* engine, float* x, float* y);
void ze_clamp_volume(float* volume);
```

模板：

```c
ZLC_FN(mouse_position, zy_mouse_position, i32,
    ZLC_PARAM(engine, borrowed<Engine>),
    ZLC_PARAM(x, out<f32>),
    ZLC_PARAM(y, out<f32>),
    ZLC_FAIL(nonzero, zy_engine_last_error))

ZLC_FN(clamp_volume, zy_clamp_volume, void,
    ZLC_PARAM(volume, inout<f32>))
```

ZyenLang：

```zy
let (status, x, y) = engine::mouse_position(app) catch err {
    return 1
}

let volume: f32 = 1.5
engine::clamp_volume(&mut volume)
```

`out<T>` 不出現在呼叫參數中，會附加到回傳值。`inout<T>` 必須使用 `&mut T`。

## 字串

`str` 對應 `ZL_String`，內容是 UTF-8：

- 只在呼叫期間讀取：直接使用 `zl_string_data()` 和
  `zl_string_byte_len()`。
- C 若要在呼叫後保存字串，使用 `zl_string_retain()` 保存完整
  `ZL_String`，不再需要時呼叫 `zl_string_release()`。
- 傳給只接受 NUL-terminated C string 的引擎前，確認內容沒有內嵌 NUL；若要
  支援任意 bytes，使用 `Slice<u8>`。
- C 回傳靜態字串可用 `zl_string_borrow()`；動態字串使用
  `zl_string_copy()` 或 `zl_string_copy_n()`。

不要保存 `zl_string_data()` 的裸 pointer 而不保留 owner。

## Callback 與事件

模板可以直接使用函式值：

```c
ZLC_FN(set_event_callback, zy_set_event_callback, void,
    ZLC_PARAM(engine, borrowed<Engine>),
    ZLC_PARAM(callback, fn(i32) void))
```

同步呼叫 callback：

```c
typedef void (*ZY_EventFn)(void* env, int32_t event_code);

static void call_event(ZL_Function callback, int32_t event_code) {
    if (!zl_fn_matches(callback, "fn(i32) void")) return;
    ZL_FN_CALL_AS(ZY_EventFn, callback)(callback.env, event_code);
}
```

若引擎要保存 callback：

```c
static ZL_Function stored_callback;

void replace_callback(ZL_Function callback) {
    zl_fn_assign(&stored_callback, callback);
}

void clear_callback(void) {
    zl_fn_clear(&stored_callback);
}
```

函式參數在呼叫期間是 borrowed。保存時必須 `zl_fn_assign()`，替換或 shutdown
時必須 `zl_fn_clear()`。不可只保存 `call` 或 `env` pointer。

ARC retain/release 是 atomic，但 closure 捕捉的遊戲資料不因此 thread-safe。
除非 API 明確要求且遊戲端資料本身可跨執行緒，callback 應回到建立它的遊戲
執行緒呼叫。

## 錯誤處理

Native API 以 `ZLC_FAIL` 轉成 `throws Error`：

- `null`：Handle 回傳 NULL。
- `nonzero`：整數回傳非零。
- `negative`：有號整數回傳負數。
- `equal(value)`：整數等於指定值。

錯誤訊息函式必須是：

```c
ZL_String error_message(void);
```

遊戲端使用 `catch/recover`，不要讓 C++ exception 穿過 C ABI：

```zy
engine::present(app) catch err {
    io::eprint(err.message)
    return 1
}
```

未處理錯誤會包含 ZyenLang 來源檔案、行列與函式 stack。adapter 應保留原始
引擎錯誤內容，但不要回傳指向短生命週期 stack buffer 的字串。

## 使用 bindgen 建立初始 adapter

```powershell
zy bindgen vendor/include/ze_engine.h `
    --module engine `
    --out-dir src/native `
    -I vendor/include `
    --library zyengine
```

產物：

- `engine.zybind.toml`：人工審核 ownership、length 和 failure。
- `engine.zlcm.h`：ABI v3 模板。
- `engine_zy.c/.h`：C adapter。

模糊 pointer 預設不會變成可呼叫 binding。設定範例：

```toml
[module]
name = "engine"
library = "zyengine"

[handles.Engine]
c_type = "ZE_Engine *"
drop = "ze_engine_destroy"

[functions.ze_engine_create]
enabled = true
return_type = "optional<owned<Engine>>"
failure = "null"
error_message = "ze_engine_last_error"

[functions.ze_engine_create.params]
title = "str"
width = "i32"
height = "i32"

[functions.ze_engine_upload]
enabled = true
return_type = "void"

[functions.ze_engine_upload.params]
engine = "borrowed<Engine>"
data = "Slice<u8>"
length = "usize"

[functions.ze_engine_upload.lengths]
data = "length"
```

bindgen 使用 Clang JSON AST，不使用 regex 猜 C declaration。生成後仍必須人工
審查；它不會自動知道 pointer 是 owned、borrowed、array 還是 optional。

## Static、shared 與 C-source 輸出

如果引擎要反向呼叫 ZyenLang，可加入三種 target：

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

```zy
#game_api_version
fn game_api_version() u32 {
    return 1
}

#game_update
fn game_update(delta_seconds: f32) i32 {
    return 0
}

#game_shutdown
fn game_shutdown() void {
}
```

```powershell
zy build game-c --release
zy build game-static --release
zy build game-shared --release
```

輸出：

- `c-source`：`.c`、`.h`、runtime/native source bundle 和 metadata JSON。
- Windows static：`.lib`。
- Windows shared：`.dll`、MinGW import library `.dll.a` 和 `.h`。
- Linux static/shared：`.a`、`.so` 和 `.h`。
- macOS static/shared：`.a`、`.dylib` 和 `.h`。

C++ 可 include 產生的 header；header 會加入 `extern "C"`。

`#name` 會保留內部 mangled 函式並產生同名公開 C wrapper，wrapper 會進入
header。`export fn` 仍可作為直接 symbol 匯出的相容語法。兩者都不可 throws、
不可 generic，參數及回傳值目前只接受固定寬度數字、`bool`、`void`、
`ZL_String`。C 呼叫端收到
`ZL_String` 後可讀取 data/byte length，並應在完成後呼叫
`zl_string_release()`；borrowed/static string 的 release 是安全 no-op。

## 動態連結與熱重載限制

ZyenLang 目前沒有 source-level `LoadLibrary()` 或 `dlopen()`。`ZLC_LIB` 是
build-time linking。Windows 動態引擎需要 import `.lib`/`.dll.a`，執行時 DLL
必須位於 EXE 旁或 `PATH`。

引擎可以自行載入 ZyenLang 產生的 shared library，但第一版必須遵守：

- DLL unload 前清除所有來自該 DLL 的 `ZL_String`、`ZL_Function`、Handle 和
  callback。
- 不可在 unload 後呼叫舊 function pointer 或 destructor。
- class/List/closure 不能直接穿過 export ABI。
- 需要保留的遊戲狀態先序列化到引擎擁有的 buffer，再 unload/reload。
- 目前沒有內建 hot-reload state migration coordinator。

正式發行建議 static link 或把 ZyenLang C source 納入引擎 build；shared library
比較適合開發期 plugin，且必須由引擎實作 unload barrier。

## 執行緒規則

- `ZL_Handle`、`ZL_Function` control block 的 retain/release 是 atomic。
- Handle payload、class field、List 和 closure capture 不會自動 thread-safe。
- Slice/MutSlice 不可跨執行緒或超過 native call。
- render context、window 和大部分 GPU Handle 應限制在 render thread。
- background loader 應回傳 owned Handle 或把結果排入 thread-safe engine queue。
- callback 要在哪個執行緒執行，必須寫入引擎 API 契約。

不要因為 ARC 是 atomic 就把同一個 Texture、World 或 closure 同時交給多執行緒
修改。

## 效能準則

1. 每 frame 跨 ABI 次數要可預測，優先 batch API。
2. entity 使用 compact ID 或 POD array，不要每個 component 建一個 Handle。
3. GPU、audio、file、physics world 等真正 native resource 才使用 Handle。
4. render command、transform、vertex 和 sample 使用 Slice 或 owned Buffer Handle。
5. 避免每 frame 建立大量動態字串或 closure。
6. `MutSlice` 可能觸發 COW；不需要寫回時使用 `Slice`。
7. release build 使用 `zy build ... --release`。
8. profiler marker 由 engine C API 暴露，再以 native module 呼叫。

## 安全邊界

`zy check` 只解析 ZyenLang 和模板，不呼叫 C compiler。`zy build`、`zy run`、
static/shared target 會編譯並連結 native source。C adapter 擁有 process 的完整
權限，不受 ZyenLang 記憶體模型保護。

禁止：

- 在模板中使用 package 外的 source/header/include/lib path。
- 將任意 raw pointer 偽裝成另一個 Handle type。
- 保存 Slice data。
- 保存 callback 而不 retain。
- consume borrowed Handle。
- 在資源仍被引用時卸載 DLL。
- 讓 C++ exception 穿越 `extern "C"`。

## 驗收清單

引擎整合完成前至少驗證：

1. `zy doctor` 可找到 C compiler 與 bindgen parser。
2. `zy check` 可解析所有 native module。
3. debug/release 的 `zy run` 都能建立視窗並正常關閉。
4. 每個 owned Handle 的 destructor 恰好執行一次。
5. Handle alias、`DROP__()`、borrowed parent 和 consumed alias 行為正確。
6. 空 Slice、一般 Slice、MutSlice COW 和大型 buffer 正確。
7. callback 註冊、替換、解除註冊和 shutdown 不洩漏。
8. native failure 會得到正確 `Error.message`。
9. static/shared/C-source consumer 可在 C 與 C++ 編譯。
10. Windows、Linux、macOS 各自驗證 library search path 和 runtime library。
11. Linux CI 使用 AddressSanitizer 與 UndefinedBehaviorSanitizer。
12. profiler 確認 batch API 沒有產生大量細碎 ABI calls。

## 給遊戲引擎實作 AI 的硬性要求

- 先建立最小 C11 façade，再寫 `.zlcm.h`；不要讓 ZyenLang 依賴 C++ ABI。
- 所有 pointer 都必須分類成 owned、borrowed、borrowed-parent、static、consumed、
  optional、Slice 或 MutSlice；不能留下「大概是 pointer」的介面。
- 第一個 milestone 只做 Engine、Window、Input、Texture、Frame 和錯誤處理。
- 第二個 milestone 再加入 batch SpriteCommand、audio buffer 和 callback。
- 不修改 ZyenLang compiler 來配合引擎私有 layout；相容工作放在 adapter。
- 不使用 ABI v2 `c::Module` 寫新程式。
- 不聲稱支援 hot reload，除非 unload barrier、狀態序列化和 callback 清理都有
  自動化測試。
- 每加入一個 API，同時新增 ZyenLang 呼叫測試與 C adapter 生命週期測試。

可參考可執行範例：`examples/native/counter_abi3/`。
