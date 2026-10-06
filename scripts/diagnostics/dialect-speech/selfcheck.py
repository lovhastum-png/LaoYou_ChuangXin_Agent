"""老友 · 方言语音链路自检（识别 + 播报）。

解决的问题：`/api/elders/{id}/speech` 与 `/speech/synthesize` 这两条路
"看起来配好了"和"真的能用"之间存在一段黑箱 —— capability 里
`configured=true` 只说明**读到了** QWEN_API_KEY，不代表 Key 有效、
模型名可用、额度没超。本脚本把这段黑箱打通：

  1. 报告配置文件（runtime/local.env）与**运行中的后端**是否一致
     —— 常见坑：先启动后端、后填 Key，capability 仍是 false。
  2. 未配置时验证降级路径给出的是不是可读的 503，而不是 500。
  3. 已配置时用 edge-tts 合成一句已知的话，ffmpeg 转成
     16k/mono/PCM16（正是 /speech 要求的格式），真实打过去，
     看转录结果对不对得上。
  4. 用粤语合成同一句话再打一次，验证"方言 → 普通话书面语"的归一化。
  5. 逐个方言打 /speech/synthesize，报告字节数与实际音色（X-Voice）。

用法（在后端已启动的前提下）：

    backend/.venv/Scripts/python.exe -X utf8 \
        scripts/diagnostics/dialect-speech/selfcheck.py

    --expect-configured  未配置方言识别时以退出码 2 结束（用于回归）
    --skip-synthetic     跳过第 3-5 步（不调用 edge-tts / ffmpeg，不额外花钱）
    --base-url URL       默认 http://127.0.0.1:8000

注意：第 3-5 步会真的调用上游语音服务并产生费用，属于显式自检行为。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from localenv import load_env_file  # noqa: E402  —— 与启动器共用同一份解析器

ENV_FILE = ROOT / "runtime" / "local.env"
FFMPEG = Path("D:/实验室项目/学生时代牌·工具/ffmpeg/ffmpeg.exe")
TMP = ROOT / "tmp"

# 用于验证"识别得对不对"的两句话。粤语那句刻意带上方言词，
# 归一化之后应当变成书面语（脑瓜子疼 -> 头疼 / 点算 -> 怎么办）。
CASES = [
    {
        "label": "普通话",
        "dialect": "zh-CN",
        "voice": "zh-CN-XiaoxiaoNeural",
        "sentence": "今天天气怎么样，我要不要去公园散步。",
        "keywords": ["天气", "公园"],
    },
    {
        "label": "粤语",
        "dialect": "yue-HK",
        "voice": "zh-HK-HiuMaanNeural",
        "sentence": "我个脑瓜子好痛，点算啊？",
        "keywords": ["痛"],
        "normalized_hint": "归一化后期望出现「头疼/头痛/怎么办」这类书面语，不应逐字保留「脑瓜子」「点算」",
    },
]

SYNTH_DIALECTS = ["zh-CN", "yue-HK", "sichuan", "northeast"]

_results: list[tuple[str, bool, str]] = []
# 记录"后端此刻是否已配置方言识别"。退出码判断用显式状态，不要靠去猜
# _results 里某一条的文案（那样一改文案就会静默失效）。
_state: dict[str, object] = {"configured": None, "file_has_key": False}


def record(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f" —— {detail}" if detail else ""))


def info(name: str, detail: str) -> None:
    print(f"[info] {name} —— {detail}")


def http_client() -> httpx.AsyncClient:
    # 本机调用必须绕开系统代理：环境里注入了 http_proxy=127.0.0.1:55640，
    # 直连会被转发，目标端口没服务时代理会返回 502，极易误判成"服务没起来"。
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(name, None)
    return httpx.AsyncClient(timeout=60.0, trust_env=False)


async def login(client: httpx.AsyncClient, base: str, username: str, password: str) -> str:
    response = await client.post(f"{base}/api/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return response.json()["token"]


async def first_elder(client: httpx.AsyncClient, base: str, token: str) -> dict:
    response = await client.get(f"{base}/api/elders", headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    elders = response.json()
    if not elders:
        raise SystemExit("后端里没有任何老人档案，无法自检。请先用演示账号跑一次流程。")
    return elders[0]


async def synth_pcm(voice: str, text: str) -> bytes:
    """edge-tts 合成 -> ffmpeg 转 16k/mono/PCM16，正是 /speech 要求的格式。"""
    import edge_tts

    TMP.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TMP) as workdir:
        mp3 = Path(workdir) / "line.mp3"
        pcm = Path(workdir) / "line.pcm"
        await edge_tts.Communicate(text, voice).save(str(mp3))
        if not mp3.is_file() or mp3.stat().st_size == 0:
            raise RuntimeError(f"edge-tts 没有产出音频（voice={voice}）")
        subprocess.run(
            [
                str(FFMPEG), "-y", "-loglevel", "error",
                "-i", str(mp3),
                "-ac", "1", "-ar", "16000", "-f", "s16le", str(pcm),
            ],
            check=True,
        )
        return pcm.read_bytes()


async def check_recognition(client: httpx.AsyncClient, base: str, token: str, elder_id: str) -> None:
    print()
    print("── 识别链路（合成已知句子 → 真实识别）" + " " + "─" * 20)
    headers = {"Authorization": f"Bearer {token}"}
    for case in CASES:
        try:
            pcm = await synth_pcm(case["voice"], case["sentence"])
        except Exception as exc:  # noqa: BLE001 - 自检脚本要把原因原样报出来
            record(f"{case['label']} 音频准备", False, f"{exc}")
            continue
        info(f"{case['label']} 素材", f"合成“{case['sentence']}” → {len(pcm)} 字节 PCM（{len(pcm)/32000:.1f} 秒）")

        response = await client.post(
            f"{base}/api/elders/{elder_id}/speech",
            params={"dialect": case["dialect"]},
            content=pcm,
            headers={**headers, "Content-Type": "audio/L16"},
        )
        if response.status_code != 200:
            detail = ""
            try:
                detail = response.json().get("detail", "")
            except ValueError:
                detail = response.text[:120]
            record(
                f"{case['label']} 识别",
                False,
                f"HTTP {response.status_code}：{detail}"
                + ("（Key 无效/额度不足/模型名不对都会落在这一层）" if response.status_code >= 500 else ""),
            )
            continue

        data = response.json()
        transcript = data.get("text", "")
        provider = data.get("provider", "?")
        hit = [word for word in case["keywords"] if word in transcript]
        record(
            f"{case['label']} 识别",
            bool(hit),
            f"provider={provider} 转录“{transcript}” 命中关键词 {hit or '无'}",
        )
        if case.get("normalized_hint"):
            info(f"{case['label']} 归一化", case["normalized_hint"])


async def check_unconfigured_copy(client: httpx.AsyncClient, base: str, token: str, elder_id: str) -> None:
    """未配置时，降级路径必须是可读的 4xx/503，而不是 500 或挂住。

    已配置时这个用例**不适用**：此时请求会真的打到上游，上游报错（例如
    Key 无效的 401）属于"识别链路"要处理的问题，不能在这里判成降级文案失败。
    """
    print()
    print("── 未配置时的降级文案 " + "─" * 30)
    if _state["configured"]:
        info("跳过", "后端已配置方言识别，该用例只在未配置时才说明问题")
        return
    # 3200 字节是最小合法长度，不产生任何有意义的语音，只为触发校验。
    response = await client.post(
        f"{base}/api/elders/{elder_id}/speech",
        params={"dialect": "yue-HK"},
        content=b"\x00" * 3200,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "audio/L16"},
    )
    try:
        detail = response.json().get("detail", "")
    except ValueError:
        detail = response.text[:120]
    ok = response.status_code in (422, 503) and bool(detail)
    record("降级文案", ok, f"HTTP {response.status_code}：{detail}")


async def check_synthesis(client: httpx.AsyncClient, base: str, token: str, elder_id: str) -> None:
    print()
    print("── 播报链路（服务端方言音色）" + " " * 8 + "─" * 20)
    headers = {"Authorization": f"Bearer {token}"}
    seen: dict[str, str] = {}
    for dialect in SYNTH_DIALECTS:
        response = await client.post(
            f"{base}/api/elders/{elder_id}/speech/synthesize",
            json={"text": "该吃药了，记得喝水。", "dialect": dialect},
            headers=headers,
        )
        if response.status_code != 200:
            record(f"播报 {dialect}", False, f"HTTP {response.status_code}")
            continue
        voice = response.headers.get("x-voice", "?")
        size = len(response.content)
        is_mp3 = response.content[:3] in (b"ID3",) or response.content[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
        seen[dialect] = voice
        record(f"播报 {dialect}", size > 1000 and is_mp3, f"{size} 字节 · 音色 {voice}")

    print()
    print("  实际音色映射：")
    for dialect, voice in seen.items():
        print(f"    {dialect:<10} -> {voice}")
    # 四川话没有免费音色，会回落到普通话音色。这是"诚实的回落"，不是失败，
    # 但如果哪天有人给它加了音色，这里会自然显示出来。
    if seen.get("sichuan") == seen.get("zh-CN"):
        info("四川话", "回落到普通话音色（edge-tts 无四川话音色），文本仍是归一化后的普通话")


def compare_config_sources(file_env: dict[str, str], speech: dict) -> None:
    """配置文件与运行中后端是否一致 —— 这条最容易让人白忙一场。"""
    print()
    print("── 配置来源一致性 " + "─" * 30)
    file_key = bool(file_env.get("QWEN_API_KEY", "").strip())
    _state["file_has_key"] = file_key
    if file_key and not speech.get("configured"):
        record(
            "配置一致性",
            False,
            "local.env 里有 QWEN_API_KEY，但当前后端报告未配置 —— "
            "后端是在填 Key 之前启动的，请关掉预览窗口重新双击启动。",
        )
    elif not file_key and speech.get("configured"):
        record("配置一致性", True, "后端已配置（Key 来自系统环境变量或启动前已设置），local.env 里没有")
    elif file_key and speech.get("configured"):
        record("配置一致性", True, "配置文件与运行中的后端一致")
    else:
        record("配置一致性", True, "两边都未配置方言识别（符合预期，仅影响方言；文字与设备识别不受影响）")


async def main() -> int:
    parser = argparse.ArgumentParser(description="老友方言语音链路自检")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--elder-id", default="")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="Laoyou123!")
    parser.add_argument("--expect-configured", action="store_true")
    parser.add_argument("--skip-synthetic", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    print("=" * 62)
    print("  老友 · 方言语音链路自检")
    print(f"  后端：{base}（{urlsplit(base).netloc}）")
    print("=" * 62)

    file_env = load_env_file(ENV_FILE)
    info("配置文件", f"{ENV_FILE} —— {'存在' if ENV_FILE.is_file() else '不存在'}，解析到 {len(file_env)} 个键")

    async with http_client() as client:
        try:
            health = await client.get(f"{base}/api/health")
            health.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"\n[FAIL] 后端不可达：{exc}")
            print("       先双击「启动老友-预览.cmd」，再跑本脚本。")
            return 1
        info("健康检查", json.dumps(health.json(), ensure_ascii=False))

        try:
            token = await login(client, base, args.user, args.password)
        except Exception as exc:  # noqa: BLE001
            print(f"\n[FAIL] 登录失败（账号 {args.user}）：{exc}")
            return 1

        capabilities = await client.get(f"{base}/api/capabilities")
        capabilities.raise_for_status()
        caps = capabilities.json()

        elder = {"id": args.elder_id} if args.elder_id else await first_elder(client, base, token)
        info("自检对象", f"elder_id={elder['id']}")

        speech = caps.get("speech", {})
        tts = caps.get("tts", {})
        _state["configured"] = bool(speech.get("configured"))
        print()
        print("── 能力上报 " + "─" * 36)
        info("识别 provider", speech.get("provider", "?"))
        info("识别 configured", str(speech.get("configured")))
        info("方言归一化", str(speech.get("normalizes_dialect")))
        for item in speech.get("dialects", []):
            print(f"    {item['id']:<10} {'可用' if item['available'] else '不可用'}  {item['label']}")
        info("播报 provider", tts.get("provider", "?"))
        info("播报服务端", str(tts.get("server_side")))
        for item in tts.get("dialect_voices", []):
            print(f"    {item['id']:<10} {'有音色' if item['available'] else '无独立音色'}")

        compare_config_sources(file_env, speech)
        await check_unconfigured_copy(client, base, token, elder["id"])
        await check_synthesis(client, base, token, elder["id"])

        if not args.skip_synthetic and speech.get("configured"):
            await check_recognition(client, base, token, elder["id"])
        elif not speech.get("configured"):
            print()
            print("── 识别链路 " + "─" * 38)
            info("跳过", "未配置方言识别，跳过真实识别用例（这不算失败）")

    print()
    print("=" * 62)
    failed = [name for name, ok, _ in _results if not ok]
    print(f"  合计 {len(_results)} 项，失败 {len(failed)} 项")
    for name in failed:
        print(f"    - {name}")
    print("=" * 62)

    if args.expect_configured and not _state["configured"]:
        print("  --expect-configured 已指定，但后端未配置方言识别（识别链路整体跳过）。")
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
