#!/usr/bin/env python3
"""Render the Traditional Chinese ZyenLang pointer tutorial as an MP4."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1920
HEIGHT = 1080
FPS = 30

BG = "#0b0f14"
PANEL = "#141b23"
PANEL_EDGE = "#2a3542"
TEXT = "#f2f5f7"
MUTED = "#9aa8b6"
CYAN = "#56d4dd"
GREEN = "#88d498"
YELLOW = "#f4c95d"
RED = "#ff6b6b"
BLUE = "#79a7ff"
CODE = "#dce4eb"

FONT_REGULAR = Path(r"C:\Windows\Fonts\msjh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msjhbd.ttc")
FONT_MONO = Path(r"C:\Windows\Fonts\CascadiaMono.ttf")


@dataclass(frozen=True)
class Scene:
    kicker: str
    title: str
    code: str
    points: tuple[str, ...]
    caption: str
    narration: str
    accent: str = CYAN
    roadmap: bool = False


SCENES = (
    Scene(
        "ZYENLANG v0.1.83",
        "指標、所有權與函式指標",
        'let *p: ptr<ptr<int>> = 10;\nprint(**p);\n\nlet fp: ptr<fn(int,int)->int> = &add;\nprint(*fp(20, 22));',
        ("編譯成 C", "自動 ARC", "可直接呼叫 C 模組"),
        "今天不從 Hello World 開始，直接處理最容易出錯的兩件事。",
        "這是 ZyenLang，一個會編譯成 C 的實驗性語言。今天不從 Hello World 開始，我們直接看指標、所有權，以及可以儲存在變數裡的函式值。",
    ),
    Scene(
        "01 / STORAGE",
        "普通區域變數與 owned cell",
        'let value: int = 10;\nlet borrowed: ptr<int> = &value;\n\nlet *owned: ptr<int> = 10;\nset *owned = 20;\nprint(*owned);',
        ("普通 let：automatic storage", "&value：borrowed pointer", "let *：ARC owned cell"),
        "只有 let * 會建立受 ARC 管理的 owned cell。",
        "普通的 let value 使用函式區域儲存，不代表每次都呼叫 malloc。取它的位址會得到 borrowed pointer。寫成 let 星號，才會建立由 ARC 管理的 owned cell，離開作用域時自動釋放。",
        GREEN,
    ),
    Scene(
        "02 / NESTED PTR",
        "巢狀指標可以直接解參考",
        'let *p: ptr<ptr<int>> = 10;\n\nprint(**p);       // 10\nset **p = 21;\nprint(**p);       // 21\n\nlet *deep: ptr<ptr<ptr<int>>> = 42;\nprint(***deep);   // 42',
        ("每個 * 移除一層 ptr", "每一層都有 runtime 檢查", "不是 raw C int **"),
        "`**p` 和 `***deep` 會逐層檢查 None、Freed 與 runtime type。",
        "ptr 型別可以遞迴。每一個星號只移除一層 pointer，所以雙星號和三星號都能直接使用。每層都是受管的 ZL ptr，不等同 raw C 的 int 雙指標，而且每次解參考都會檢查 None、Freed 與 runtime type。",
        BLUE,
    ),
    Scene(
        "03 / MANAGED ALIAS",
        "`&**p` 會保留 owner",
        'let *p: ptr<ptr<int>> = 10;\nlet *slot: ptr<ptr> = &**p;\n\nset **slot = 21;\nprint(**p);       // 21',
        ("保留相同 address", "保留 ARC owner", "ptr<ptr> 從初始化式補型別"),
        "這不是未追蹤的 interior address，而是保留所有權的 managed alias。",
        "對 managed pointer 而言，address of 再 dereference 會形成同一個 owner 的 alias。這不是未追蹤的 interior address。即使外層 chain 先離開 scope，slot 保留的 ARC reference 仍會讓內層資料存活。",
        YELLOW,
    ),
    Scene(
        "04 / ESCAPE",
        "回傳 pointer 會轉移所有權",
        'fn ret_ptr() -> ptr<int> {\n    let *value: ptr<int> = 110;\n    return value;\n}\n\nfn main() -> int {\n    let value: ptr<int> = ret_ptr();\n    print(*value);  // 110\n    return 0;\n}',
        ("回傳前 retain", "local cleanup release", "呼叫端接管 owned reference"),
        "函式結束不會把已回傳的 payload 提前釋放。",
        "回傳 managed pointer 時，編譯器先保留回傳 reference，再清理函式裡的 local reference，最後由呼叫端接管。區域變數剛好同名也沒有影響，因為每次函式呼叫都有獨立 storage 與 ARC reference。",
        GREEN,
    ),
    Scene(
        "05 / STRUCT METHOD",
        "Struct 方法用 `this.method()`",
        'struct Counter {\n    let this.value: int;\n\n    fn double() -> int {\n        return this.value * 2;\n    }\n\n    fn show() -> void {\n        print(this.double());\n    }\n}',
        ("this.field 存取欄位", "this.method() 呼叫同 struct 方法", "不是函式指標"),
        "普通 method call 與儲存在欄位裡的函式值是兩件不同的事。",
        "一個 struct 方法要呼叫同一個 struct 裡的其他方法，直接寫 this 點 method。這是帶有隱含 receiver 的普通 method call，不是函式指標，也不需要先取位址。",
        CYAN,
    ),
    Scene(
        "06 / FUNCTION VALUE",
        "目前支援的是安全函式值",
        'fn add(a: int, b: int) -> int {\n    return a + b;\n}\n\nlet op: fn(int,int)->int = add;\nprint(op(20, 22));              // 42\n\nprint(pick("sub")(10, 3));     // 7',
        ("可作為參數與回傳值", "可立即鏈式呼叫", "ABI：call、env、owner、簽章"),
        "`fn(...)` 是 ZL_Function 安全函式值，不是 `ptr<fn(...)>`。",
        "這裡要把術語說清楚。ZyenLang 現在的 fn 型別是安全函式值，它包含 call、environment、owner 和 signature。具名函式沒有 environment，closure 則由 ARC 管理。它不是 raw function pointer。",
        BLUE,
    ),
    Scene(
        "07 / FUNCTION FIELD",
        "函式值可以放進 Struct",
        'struct Calculator {\n    let this.op: fn(int,int)->int;\n\n    fn run(a: int, b: int) -> int {\n        return this.op(a, b);\n    }\n}\n\nlet calc: Calculator = Calculator { op: add };\nprint(calc.run(10, 20));',
        ("this.op 是函式值欄位", "this.op(a, b) 間接呼叫", "適合 strategy 與 callback"),
        "函式值欄位可替換行為，也能安全保存 closure callback。",
        "把 fn 型別放進 struct 欄位，就能替換物件使用的運算策略。this op 的呼叫是透過函式值間接執行；若欄位保存 closure，environment 的生命週期也會由 ARC 自動管理。",
        YELLOW,
    ),
    Scene(
        "08 / FUNCTION POINTER",
        "Pointer 指向函式 cell",
        'let p: ptr<fn(int,int)->int> = &add;\nprint(*p(10, 20));\n\nlet opaque: ptr<void> = p;\nprint(*(ptr<fn(int,int)->int>)opaque(12, 10));',
        ("指向 ZL_Function cell", "owned cell 可保存 closure", "呼叫前檢查 runtime 簽章"),
        "`ptr<fn(...)>` 是受管函式 cell 指標，可擦除成 `ptr<void>` 再安全還原。",
        "ptr 包住 fn signature 時，pointer 指向一個 ZL Function 記憶體 cell。address of 具名函式會取得靜態 cell；let 星號則建立 ARC owned cell，也能保存 closure。轉成 ptr void 不會丟失 runtime tag，還原後會先檢查簽章再呼叫。",
        CYAN,
    ),
    Scene(
        "BUILD SOMETHING",
        "解壓、加入 PATH、開始寫",
        'zy version\nzy doctor\nzy run examples\\pointer_function_tutorial.zy\n\n// Windows / Linux / macOS\n// github.com/Ryan-2013/zyenlang',
        ("portable：zyv183", "ZEP-0015：函式 cell 指標", "免裝 Python 與 C compiler"),
        "ZyenLang v0.1.83 提供多平台 portable release。",
        "ZyenLang 零點一點八三提供 Windows、Linux 和 macOS 的 portable release。下載 zyv183，將解壓資料夾加入 PATH，就能直接執行教學範例。完整函式 cell 指標語意記錄在 ZEP 零零一五。",
        GREEN,
    ),
)


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.is_file():
        raise SystemExit(f"font not found: {path}")
    return ImageFont.truetype(str(path), size)


FONTS = {
    "brand": font(FONT_BOLD, 38),
    "kicker": font(FONT_BOLD, 26),
    "title": font(FONT_BOLD, 60),
    "point": font(FONT_REGULAR, 29),
    "caption": font(FONT_BOLD, 31),
    "mono": font(FONT_MONO, 28),
    "mono_small": font(FONT_MONO, 23),
    "badge": font(FONT_BOLD, 27),
}


def wrap_text(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and draw.textbbox((0, 0), candidate, font=face)[2] > width:
                lines.append(current.rstrip())
                current = char.lstrip()
            else:
                current = candidate
        lines.append(current)
    return lines


def fitting_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    path: Path,
    maximum: int,
    minimum: int,
    width: int,
) -> ImageFont.FreeTypeFont:
    for size in range(maximum, minimum - 1, -2):
        candidate = font(path, size)
        if draw.textbbox((0, 0), text, font=candidate)[2] <= width:
            return candidate
    return font(path, minimum)


TOKEN_RX = re.compile(r'//.*|"(?:\\.|[^"\\])*"|\b\d+\b|\b[A-Za-z_]\w*\b|\s+|.')
KEYWORDS = {
    "fn", "let", "set", "struct", "return", "if", "else", "for", "import",
    "as", "this", "None", "true", "false", "ptr", "int", "str", "bool",
    "void", "print",
}


def token_color(token: str) -> str:
    if token.startswith("//"):
        return MUTED
    if token.startswith('"'):
        return YELLOW
    if token.isdigit():
        return GREEN
    if token in KEYWORDS:
        return CYAN
    if token in {"*", "&", "->"}:
        return RED
    return CODE


def draw_code(draw: ImageDraw.ImageDraw, code: str, x: int, y: int, width: int, height: int) -> None:
    draw.rounded_rectangle((x, y, x + width, y + height), radius=8, fill=PANEL, outline=PANEL_EDGE, width=2)
    draw.rectangle((x, y, x + width, y + 52), fill="#10161d")
    for index, color in enumerate((RED, YELLOW, GREEN)):
        cx = x + 28 + index * 30
        draw.ellipse((cx, y + 18, cx + 14, y + 32), fill=color)
    draw.text((x + 126, y + 14), "tutorial.zy", font=FONTS["mono_small"], fill=MUTED)

    lines = code.splitlines()
    line_height = 43 if len(lines) <= 10 else 37
    face = FONTS["mono"] if len(lines) <= 10 else FONTS["mono_small"]
    cursor_y = y + 74
    for line_number, line in enumerate(lines, 1):
        draw.text((x + 25, cursor_y), f"{line_number:>2}", font=face, fill="#5f6d7a")
        cursor_x = x + 90
        for match in TOKEN_RX.finditer(line):
            token = match.group(0)
            draw.text((cursor_x, cursor_y), token, font=face, fill=token_color(token))
            cursor_x += draw.textlength(token, font=face)
        cursor_y += line_height


def render_scene(scene: Scene, index: int, output: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, 18, HEIGHT), fill=scene.accent)
    draw.text((74, 44), "ZYENLANG", font=FONTS["brand"], fill=TEXT)
    draw.text((290, 54), "COMPILES TO C", font=FONTS["kicker"], fill=MUTED)
    draw.text((74, 128), scene.kicker, font=FONTS["kicker"], fill=scene.accent)
    title_face = fitting_font(draw, scene.title, FONT_BOLD, 60, 44, 1740)
    draw.text((74, 174), scene.title, font=title_face, fill=TEXT)

    if scene.roadmap:
        draw.rounded_rectangle((1390, 138, 1810, 204), radius=8, fill="#3a171b", outline=RED, width=2)
        draw.text((1430, 153), "規劃中 / 尚未支援", font=FONTS["badge"], fill=RED)

    draw_code(draw, scene.code, 74, 288, 1190, 610)

    point_y = 328
    for point in scene.points:
        draw.rounded_rectangle((1322, point_y, 1362, point_y + 40), radius=6, fill=scene.accent)
        if scene.roadmap:
            draw.text((1332, point_y + 1), "→", font=FONTS["point"], fill=BG)
        else:
            draw.line(
                ((1332, point_y + 21), (1340, point_y + 29), (1354, point_y + 12)),
                fill=BG,
                width=5,
                joint="curve",
            )
        for line in wrap_text(draw, point, FONTS["point"], 430):
            draw.text((1382, point_y), line, font=FONTS["point"], fill=TEXT)
            point_y += 40
        point_y += 40

    draw.rectangle((18, 928, WIDTH, HEIGHT), fill="#10171e")
    caption_lines = wrap_text(draw, scene.caption, FONTS["caption"], 1660)
    caption_y = 962 if len(caption_lines) == 1 else 944
    for line in caption_lines:
        draw.text((100, caption_y), line, font=FONTS["caption"], fill=TEXT)
        caption_y += 44

    progress_x = 1650
    draw.text((progress_x, 1000), f"{index + 1:02d} / {len(SCENES):02d}", font=FONTS["mono_small"], fill=MUTED)
    bar_width = 180
    draw.rectangle((progress_x, 1036, progress_x + bar_width, 1042), fill="#283441")
    draw.rectangle((progress_x, 1036, progress_x + int(bar_width * (index + 1) / len(SCENES)), 1042), fill=scene.accent)

    image.save(output, quality=95)


def run(command: list[str]) -> None:
    print("+", subprocess.list2cmdline(command))
    subprocess.run(command, check=True)


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def synthesize(text: str, text_path: Path, wav_path: Path, voice: str) -> None:
    text_path.write_text(text, encoding="utf-8")
    script = "\n".join((
        "Add-Type -AssemblyName System.Speech",
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
        f"$s.SelectVoice({ps_quote(voice)})",
        "$s.Rate = -1",
        "$s.Volume = 100",
        f"$text = Get-Content -LiteralPath {ps_quote(str(text_path))} -Raw -Encoding utf8",
        f"$s.SetOutputToWaveFile({ps_quote(str(wav_path))})",
        "$s.Speak($text)",
        "$s.Dispose()",
    ))
    run(["powershell", "-NoProfile", "-Command", script])


def audio_duration(ffprobe: str, path: Path) -> float:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def render_clip(ffmpeg: str, slide: Path, wav: Path, output: Path, duration: float) -> None:
    fade_out = max(duration - 0.35, 0.1)
    run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(slide), "-i", str(wav),
        "-vf", f"fade=t=in:st=0:d=0.25,fade=t=out:st={fade_out:.3f}:d=0.35,format=yuv420p",
        "-af", "afade=t=in:st=0:d=0.12,apad=pad_dur=0.8",
        "-t", f"{duration:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "medium",
        "-crf", "19", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(output),
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/tutorial-video")
    parser.add_argument("--voice", default="Microsoft Hanhan Desktop")
    args = parser.parse_args()

    if not sys.platform.startswith("win"):
        raise SystemExit("the narration builder currently requires Windows System.Speech")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise SystemExit("ffmpeg and ffprobe are required on PATH")

    output_dir = args.output_dir.resolve()
    work = output_dir / "work"
    slides = work / "slides"
    audio = work / "audio"
    clips = work / "clips"
    for directory in (slides, audio, clips):
        directory.mkdir(parents=True, exist_ok=True)

    clip_paths: list[Path] = []
    for index, scene in enumerate(SCENES):
        stem = f"scene-{index + 1:02d}"
        slide = slides / f"{stem}.png"
        text_path = audio / f"{stem}.txt"
        wav = audio / f"{stem}.wav"
        clip = clips / f"{stem}.mp4"
        render_scene(scene, index, slide)
        synthesize(scene.narration, text_path, wav, args.voice)
        duration = audio_duration(ffprobe, wav) + 0.75
        render_clip(ffmpeg, slide, wav, clip, duration)
        clip_paths.append(clip)

    concat = work / "concat.txt"
    concat.write_text("".join(f"file '{path.as_posix()}'\n" for path in clip_paths), encoding="utf-8")
    final = output_dir / "zyenlang_pointer_function_tutorial_zh-TW.mp4"
    run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy",
        "-movflags", "+faststart", "-metadata", "title=ZyenLang 指標、所有權與函式值",
        "-metadata", "language=zho", str(final),
    ])
    cover = output_dir / "zyenlang_pointer_function_tutorial_cover.png"
    shutil.copy2(slides / "scene-01.png", cover)

    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration,size", "-of", "json", str(final)],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(result.stdout)["format"]
    print(f"video: {final}")
    print(f"cover: {cover}")
    print(f"duration: {float(metadata['duration']):.1f}s")
    print(f"size: {int(metadata['size']) / (1024 * 1024):.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
