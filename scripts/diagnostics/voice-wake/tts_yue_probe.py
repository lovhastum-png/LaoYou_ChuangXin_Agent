"""生成用于「粤语唤醒」验证的 TTS 音频（粤语 + 普通话对照组）。

输出 mp3，再用 ffmpeg 转成 Chrome 假麦克风需要的 16kHz 单声道 WAV。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "tmp"

CASES = [
    ("yue_wake", "你好通通，今日天气点样", "zh-HK-HiuMaanNeural"),
    ("yue_wake2", "你好通通，我想知今日天气", "zh-HK-WanLungNeural"),
    ("cmn_wake", "你好通通，今天天气怎么样", "zh-CN-XiaoxiaoNeural"),
]


async def render(name: str, text: str, voice: str) -> None:
    out = TMP / f"{name}.mp3"
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(out))
            if out.is_file() and out.stat().st_size > 1000:
                print(f"OK   {name:10s} {voice:26s} {out.stat().st_size} bytes")
                return
            raise RuntimeError("empty audio")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"retry{attempt} {name}: {type(exc).__name__}")
            await asyncio.sleep(1.5 * attempt)
    raise SystemExit(f"failed {name}: {last_error}")


async def main() -> int:
    for name, text, voice in CASES:
        await render(name, text, voice)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
