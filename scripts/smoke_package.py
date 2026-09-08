"""Verify the actual Windows ZIP in a fresh workspace directory, without a venv.

Creates an isolated package database and a marker reminder, then checks repeated
startup, concurrent startup, restart persistence and shutdown. Does not touch the
development database. Every test artifact remains under this workspace's tmp/.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--port", type=int, default=18880)
    parser.add_argument("--db-port", type=int, default=55439)
    parser.add_argument("--keep-running", action="store_true", help="通过后保留服务供浏览器/Android复核")
    args = parser.parse_args()
    directory = (ROOT / args.work_dir).resolve()
    if not directory.is_relative_to(ROOT / "tmp") or directory.exists():
        raise ValueError("测试目录须为工作区tmp下尚不存在的新目录")
    for port in (args.port, args.db_port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    directory.mkdir(parents=True)
    with zipfile.ZipFile(ROOT / args.archive) as archive:
        for entry in archive.infolist():
            path = (directory / entry.filename).resolve()
            if not path.is_relative_to(directory):
                raise ValueError("压缩包包含越界路径")
            if not entry.is_dir() and any(part in {"data", "state", ".venv"} for part in Path(entry.filename).parts):
                raise ValueError("分发包包含运行数据或进程状态")
        archive.extractall(directory)
    candidates = list(directory.glob("*/support/runner.py"))
    if len(candidates) != 1:
        raise ValueError("未找到唯一便携包入口")
    runner = candidates[0]
    package = runner.parents[1]
    config_path = package / "config/运行配置.json"
    config = json.loads(config_path.read_text("utf-8-sig"))
    config.update(app_port=args.port, db_port=args.db_port, open_browser=False)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "DATABASE_URL"):
        env.pop(key, None)
    system_root = os.environ["SystemRoot"]
    env["PATH"] = os.pathsep.join((str(Path(system_root) / "System32"), system_root))
    env["TEMP"] = env["TMP"] = str(directory)
    checks: list[str] = []
    report = {"at": datetime.now(timezone.utc).isoformat(), "package": str(package), "checks": checks, "passed": False}
    base = f"http://127.0.0.1:{args.port}"
    token = ""

    def command(action: str, expected: int = 0):
        launcher_names = {"start": "启动老友.cmd", "stop": "停止老友.cmd", "status": "查看状态.cmd", "info": "手机连接说明.cmd"}
        # Exercise the files a non-developer actually double-clicks, including
        # cmd.exe encoding/newline rules. open_browser=False keeps QA headless.
        result = subprocess.run([os.environ["COMSPEC"], "/d", "/c", str(package / launcher_names[action])], cwd=package,
                                env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                input="", timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        with (directory / "launcher.log").open("a", encoding="utf-8") as log:
            log.write(f"\n{action}: {result.returncode}\n{result.stdout}\n{result.stderr}\n")
        if result.returncode != expected:
            raise AssertionError(f"{action}返回{result.returncode}，预期{expected}；见launcher.log")
        return result

    def request(path: str, data=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    def login():
        return request("/api/auth/login", {"username": "child", "password": "Laoyou123!"})["token"]

    def passed(name: str):
        checks.append(name)
        print("PASS " + name, flush=True)

    try:
        command("start")
        assert request("/api/health")["status"] == "ok"
        with urllib.request.urlopen(base, timeout=10) as response:
            assert "<html" in response.read().decode().lower()
        passed("中文空格新目录首次启动，PATH不含开发工具，内嵌Python与数据库运行")
        state_path = package / "state/backend-process.json"
        first_pid = json.loads(state_path.read_text("utf-8"))["pid"]
        command("start")
        assert json.loads(state_path.read_text("utf-8"))["pid"] == first_pid
        passed("重复启动复用原进程")
        token = login()
        elder = request("/api/elders")[0]["id"]
        reminder = request(f"/api/elders/{elder}/reminders", {"title": "封包持久化验证", "medicine": "演示", "dose": "演示", "time": "23:59"})
        command("stop")
        for port in (args.port, args.db_port):
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", port)) != 0
        passed("停止后Web与数据库端口均释放")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: command("start"), range(2)))
        token = login()
        assert any(r["id"] == reminder["id"] for r in request(f"/api/elders/{elder}/reminders"))
        passed("并发双启动正常，重启后原提醒保留")
        config_path.write_text(json.dumps({**config, "app_port": args.port + 1}), encoding="utf-8")
        try:
            continued = command("start")
            assert "请停止老友" in continued.stdout and f"http://localhost:{args.port}" in continued.stdout
            assert request("/api/health")["status"] == "ok"
        finally:
            config_path.write_text(json.dumps(config), encoding="utf-8")
        passed("运行中更改端口提示先停止，并继续显示实际地址，原服务可用")
        command("status")
        command("info")
        assert (package / "手机连接说明.txt").is_file()
        passed("状态查询与手机地址说明可用")
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
        print("FAIL " + str(exc), file=sys.stderr)
    finally:
        if not (report["passed"] and args.keep_running):
            try:
                command("stop")
            except Exception as exc:
                report["shutdown_error"] = str(exc)
                report["passed"] = False
        (directory / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
