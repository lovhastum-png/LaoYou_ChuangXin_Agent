"""老友 · 本地预览启动器（SQLite 快速预览模式 + 局域网可访问）。

为什么不用 scripts/start.ps1：那条官方路径要求项目自带 PostgreSQL
（runtime/postgresql/pgsql），本机当前缺失。这里改走
doc/01-开发者文档.md 15.3.1 记载的"本地快速预览模式"。

该模式的限制（见 15.3.1）：跳过 pg_advisory_xact_lock，并发写入的幂等
只依赖唯一键，**不支持多 worker / 多客户端并发**。仅适合单人查看效果。
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

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
WEB_DIST = ROOT / "web" / "dist" / "index.html"
TMP = ROOT / "tmp"
DB_FILE = TMP / "preview.db"
PORT = 8000


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


def banner(ip: str) -> None:
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

    if port_in_use(PORT):
        banner(ip)
        print(f"[提示] 端口 {PORT} 已有实例在运行，直接复用，不再启动新进程。")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return 0

    banner(ip)

    env = os.environ.copy()
    # 15.3.1：连接串必须写绝对路径，且用正斜杠。
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
