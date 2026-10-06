"""老友 · 本地预览启动器（SQLite 快速预览模式 + 局域网可访问）。

为什么不用 scripts/start.ps1：那条官方路径要求项目自带 PostgreSQL
（runtime/postgresql/pgsql），本机当前缺失。这里改走
doc/01-开发者文档.md 15.3.1 记载的"本地快速预览模式"。

该模式的限制（见 15.3.1）：跳过 pg_advisory_xact_lock，并发写入的幂等
只依赖唯一键，**不支持多 worker / 多客户端并发**。仅适合单人查看效果。

与 scripts/start.ps1 的差异：本脚本不启动 PostgreSQL，但**同样会读取
runtime/local.env**。早先的版本漏了这一步，导致用户在该文件里填的
QWEN_API_KEY / CLOUDFLARE_TURN_* 只在官方启动器里生效 —— 而本机没有
PostgreSQL，官方启动器跑不起来，等于"填了没用"。见 doc/03-开发历史.md。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 解析规则与 start.ps1 共用同一份实现（scripts/localenv.py）。
# 直接跑本文件时 sys.path[0] 就是 scripts/，所以通常第一条就成功；
# 若被别的脚本 import，则补一次脚本目录再 import。
try:
    from localenv import load_env_file
except ImportError:  # pragma: no cover - 只在本文件被 import 时走到
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from localenv import load_env_file

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
WEB_DIST = ROOT / "web" / "dist" / "index.html"
TMP = ROOT / "tmp"
DB_FILE = TMP / "preview.db"
ENV_FILE = ROOT / "runtime" / "local.env"
ENV_EXAMPLE = ROOT / "runtime" / "local.env.example"
# 默认 8000。留一个环境变量口子，便于在不打断已有实例的情况下再起一个
# 用于自检（见 scripts/diagnostics/dialect-speech/selfcheck.py）。
PORT = int(os.getenv("LAOYOU_PREVIEW_PORT", "8000") or "8000")


def speech_summary(env: dict[str, str]) -> str:
    """报告语音识别凭据状态；只报告有没有，不打印任何密钥内容。"""
    if env.get("QWEN_API_KEY", "").strip():
        return "已配置 通义千问（方言会归一化成普通话书面语）"
    if all(env.get(name, "").strip() for name in ("XFYUN_APP_ID", "XFYUN_API_KEY", "XFYUN_API_SECRET")):
        return "已配置 讯飞（方言需在讯飞控制台单独开通，否则按普通话处理）"
    return "未配置（只能用文字输入；设备自带语音识别不受影响）"


def tts_summary(env: dict[str, str]) -> str:
    if env.get("LAOYOU_DISABLE_TTS", "").strip() in {"1", "true", "yes"}:
        return "已禁用（LAOYOU_DISABLE_TTS）"
    return "已就绪 edge-tts（粤语/东北话有独立音色，四川话回落普通话）"


def lan_ipv4() -> str:
    """取默认出口网卡的 IPv4；UDP connect 不实际发包。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def banner(ip: str, env: dict[str, str], env_loaded: bool) -> None:
    line = "=" * 52
    print(line)
    print("  老友 · 本地预览启动器（SQLite 快速预览模式）")
    print(line)
    print()
    print("  电脑浏览器打开：", f"http://127.0.0.1:{PORT}")
    print("  手机同一 Wi-Fi 打开：", f"http://{ip}:{PORT}")
    print()
    print("  演示账号（密码统一 Laoyou123!）：")
    print("    老人 elder / 子女 child / 社区 community / 管理 admin")
    print()
    print("  手机安装 APK 后，登录页「服务地址」填：", f"http://{ip}:{PORT}")
    print("    （APP 会自动补 /api；不要填 localhost）")
    print()
    print("  语音识别：", speech_summary(env))
    print("  语音播报：", tts_summary(env))
    print()
    if env_loaded:
        print("  配置来源：", ENV_FILE)
    else:
        print("  配置来源： 未找到", ENV_FILE)
        print(f"    想把方言识别打开：复制 {ENV_EXAMPLE.name} 为 {ENV_FILE.name}，")
        print("    在 QWEN_API_KEY= 后面填上通义千问的 Key，再重启本窗口。")
        print("    （该文件在 .gitignore 里，不会被提交）")
    print()
    print("  ★ 关闭本窗口即停止服务。")
    print("  ★ 预览模式不支持并发重试语义，仅供单人查看效果。")
    print(line)
    print()
    # 输出被重定向到文件时 stdout 是块缓冲，不刷新就看不到上面的提示。
    sys.stdout.flush()


def open_browser_when_ready() -> None:
    for _ in range(60):
        time.sleep(0.5)
        if port_in_use(PORT):
            webbrowser.open(f"http://127.0.0.1:{PORT}")
            return


def main() -> int:
    if not PYTHON.is_file():
        print(f"[错误] 找不到后端虚拟环境：{PYTHON}")
        print("       请先按 README.md 准备 backend/.venv。")
        return 1
    if not WEB_DIST.is_file():
        print(f"[错误] 找不到前端构建产物：{WEB_DIST}")
        print("       请先在 web 目录完成构建。")
        return 1

    TMP.mkdir(exist_ok=True)
    ip = lan_ipv4()

    file_env = load_env_file(ENV_FILE)
    env_loaded = ENV_FILE.is_file()

    if port_in_use(PORT):
        banner(ip, file_env, env_loaded)
        print(f"[提示] 端口 {PORT} 已有实例在运行，直接复用，不再启动新进程。")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return 0

    env = os.environ.copy()
    # 文件里的值覆盖系统环境变量（与 start.ps1 的 SetEnvironmentVariable 一致）。
    env.update(file_env)
    banner(ip, env, env_loaded)

    # 15.3.1：连接串必须写绝对路径，且用正斜杠。
    # 放在 env.update 之后：预览模式的数据库由本脚本决定，local.env 里的
    # DATABASE_URL 在这里不生效（那是官方 PostgreSQL 启动路径用的）。
    if file_env.get("DATABASE_URL", "").strip():
        print("[提示] local.env 里的 DATABASE_URL 在预览模式下不生效，已改用 tmp/preview.db。")
    env["DATABASE_URL"] = f"sqlite:///{ROOT.as_posix()}/tmp/{DB_FILE.name}"
    env["LAOYOU_DB_CONNECT_TIMEOUT"] = "5"
    env["PYTHONUTF8"] = "1"
    env["TEMP"] = str(TMP)
    env["TMP"] = str(TMP)

    print(f"[信息] 数据库：{env['DATABASE_URL']}")
    print("[信息] 正在启动后端，绑定 0.0.0.0:%d ..." % PORT)
    print()

    threading.Thread(target=open_browser_when_ready, daemon=True).start()

    command = [
        str(PYTHON), "-X", "utf8", "-m", "uvicorn", "app.main:app",
        "--app-dir", str(ROOT / "backend"),
        "--host", "0.0.0.0", "--port", str(PORT),
    ]
    try:
        return subprocess.call(command, cwd=str(ROOT), env=env)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
