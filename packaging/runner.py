"""老友 Windows 便携包运行器。

这个文件只负责便携包的生命周期：初始化包内 PostgreSQL、启动单 worker
Uvicorn、打开浏览器、显示手机连接地址和安全停止。它不读取源码工作区的
``runtime`` 数据，也不会创建 Windows 服务。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "runtime" / "postgresql" / "bin"
PG_LIB = ROOT / "runtime" / "postgresql" / "lib"
PG_DATA = ROOT / "data" / "postgres-data"
DATA = ROOT / "data"
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "运行配置.json"
INSTANCE_FILE = DATA / "instance.json"
LOGS = ROOT / "logs"
STATE = ROOT / "state"
ALIAS_FILE = STATE / "drive-alias.json"
BACKEND_STATE_FILE = STATE / "backend-process.json"
LOCK_FILE = STATE / "lifecycle.lock"
BACKEND_DIR = ROOT / "backend"
PYTHON = ROOT / "runtime" / "python" / "python.exe"

DEFAULT_CONFIG: dict[str, Any] = {
    "app_port": 18080,
    "db_port": 55433,
    "host": "0.0.0.0",
    "open_browser": True,
}

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


class RunnerError(RuntimeError):
    """用户可直接理解的运行器错误。"""


class _LifecycleLock:
    """跨双击进程串行化启动/停止，防止并发 initdb。"""

    def __init__(self) -> None:
        self._handle: Any = None

    def __enter__(self) -> "_LifecycleLock":
        if os.name != "nt":
            return self
        import msvcrt

        STATE.mkdir(parents=True, exist_ok=True)
        self._handle = LOCK_FILE.open("a+b")
        if LOCK_FILE.stat().st_size == 0:
            self._handle.write(b"0")
            self._handle.flush()
        deadline = time.monotonic() + 60
        while True:
            try:
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise RunnerError("已有另一个启动/停止操作正在进行，请稍后再试。")
                time.sleep(0.1)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is None or os.name != "nt":
            return
        import msvcrt

        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._handle.close()
            self._handle = None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"无法读取配置文件：{path.name}（{exc}）") from exc


def _read_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        _write_json(CONFIG_FILE, DEFAULT_CONFIG)
    raw = _read_json(CONFIG_FILE)
    if not isinstance(raw, dict):
        raise RunnerError("配置文件必须是 JSON 对象，请恢复为示例格式后重试。")
    config = {**DEFAULT_CONFIG, **raw}
    for key in ("app_port", "db_port"):
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
            raise RunnerError(f"配置项 {key} 必须是 1024-65535 的整数。")
    host = config.get("host")
    if not isinstance(host, str) or not host.strip():
        raise RunnerError("配置项 host 必须是非空文本。")
    config["host"] = host.strip()
    config["open_browser"] = bool(config.get("open_browser"))
    return config


def _ensure_directories() -> None:
    for path in (DATA, CONFIG_DIR, LOGS, STATE, LOGS / "temp"):
        path.mkdir(parents=True, exist_ok=True)


def _is_non_ascii(path: Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


def _drive_exists(letter: str) -> bool:
    return os.path.exists(f"{letter}:\\")


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(str(left), str(right))
    except (FileNotFoundError, OSError):
        try:
            return left.resolve().as_posix().rstrip("/").casefold() == right.resolve().as_posix().rstrip("/").casefold()
        except OSError:
            return False


def _remove_subst(letter: str) -> None:
    subprocess.run(
        ["subst.exe", f"{letter}:", "/D"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )


def _ensure_drive_alias() -> tuple[Path, str | None]:
    """为 PostgreSQL 处理中文包路径，动态选择盘符并校验映射目标。

    PostgreSQL Windows 的 initdb 在部分发行版上无法正确处理非 ASCII 的
    程序路径。映射只在当前用户会话中生效，不安装服务；停止数据库后会撤销。
    """

    if not _is_non_ascii(ROOT):
        return ROOT, None
    if os.name != "nt":
        raise RunnerError("Windows 便携包只能在 Windows 上运行。")

    if ALIAS_FILE.exists():
        try:
            saved = _read_json(ALIAS_FILE)
            letter = str(saved.get("letter", "")).strip().upper()
            alias = Path(f"{letter}:/")
            if len(letter) == 1 and letter.isalpha() and _same_path(alias, ROOT):
                return alias, letter
        except RunnerError:
            pass

    # 跳过 L:，因为开发工作区的旧启动方式可能正在占用它。
    for letter in "ZYXWVUTSRQPONMKJIHGFEDCBA":
        if letter == "L" or _drive_exists(letter):
            continue
        try:
            subprocess.run(
                ["subst.exe", f"{letter}:", str(ROOT)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        alias = Path(f"{letter}:/")
        if _same_path(alias, ROOT):
            _write_json(ALIAS_FILE, {"letter": letter})
            return alias, letter
        _remove_subst(letter)
    raise RunnerError("没有找到可用的临时盘符，无法处理中文安装路径。请释放一个盘符后重试。")


def _release_drive_alias(letter: str | None) -> None:
    if not letter:
        return
    _remove_subst(letter)
    ALIAS_FILE.unlink(missing_ok=True)


def _mapped(path: Path | str, alias: Path) -> str:
    value = Path(path)
    if alias != ROOT:
        try:
            return str(alias / value.relative_to(ROOT))
        except ValueError:
            pass
    return str(value)


def _pg_exe(name: str) -> Path:
    executable = BIN / f"{name}.exe"
    if not executable.is_file():
        raise RunnerError(f"便携包缺少 PostgreSQL 文件：{executable.relative_to(ROOT)}")
    return executable


def _pg_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PGCLIENTENCODING"] = "UTF8"
    env["TEMP"] = str(LOGS / "temp")
    env["TMP"] = str(LOGS / "temp")
    env["PATH"] = os.pathsep.join(
        (str(BIN), str(PG_LIB), str(ROOT / "runtime" / "python"), env.get("PATH", ""))
    )
    if extra:
        env.update(extra)
    return env


def _run_pg(
    name: str,
    *args: Path | str,
    alias: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [_mapped(_pg_exe(name), alias)]
    command.extend(_mapped(arg, alias) if isinstance(arg, Path) else str(arg) for arg in args)
    if name == "pg_ctl" and "start" in {str(arg) for arg in args}:
        output_path = LOGS / "postgres-start.log"
        with output_path.open("a", encoding="utf-8") as output:
            result = subprocess.run(
                command,
                cwd=str(alias),
                env=_pg_env(env),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
        result.stdout = output_path.read_text(encoding="utf-8", errors="replace")
        result.stderr = ""
    else:
        result = subprocess.run(
            command,
            cwd=str(alias),
            env=_pg_env(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    if check and result.returncode != 0:
        output = "\n".join(
            part.strip() for part in (result.stdout or "", result.stderr or "") if part and part.strip()
        )
        raise RunnerError(f"PostgreSQL 操作失败（{name}）：{output[-1200:]}")
    return result


def _db_status(alias: Path) -> bool:
    result = _run_pg("pg_ctl", "-D", PG_DATA, "status", alias=alias, check=False)
    return result.returncode == 0


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _load_instance() -> dict[str, Any]:
    if not INSTANCE_FILE.exists():
        raise RunnerError("还没有数据库实例，请先完成首次初始化。")
    instance = _read_json(INSTANCE_FILE)
    if not isinstance(instance, dict) or not instance.get("postgres_password") or not instance.get("app_password"):
        raise RunnerError("数据库凭据文件不完整，数据未被修改；请查看诊断日志。")
    return instance


def _init_database(alias: Path, config: dict[str, Any]) -> dict[str, Any]:
    _ensure_directories()
    if PG_DATA.exists() and (PG_DATA / "PG_VERSION").exists():
        return _load_instance()
    existing = [item for item in PG_DATA.glob("*") if item.name != ".keep"] if PG_DATA.exists() else []
    if existing:
        raise RunnerError("检测到未完成的数据库目录，出于数据安全考虑不会删除；请查看 logs 后人工处理。")

    instance = _load_instance() if INSTANCE_FILE.exists() else {
        "postgres_password": secrets.token_urlsafe(32),
        "app_password": secrets.token_urlsafe(32),
        "db_port": config["db_port"],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write_json(INSTANCE_FILE, instance)
    password_file = LOGS / "temp" / "postgres-init-password.txt"
    password_file.write_text(str(instance["postgres_password"]), encoding="ascii")
    try:
        _run_pg(
            "initdb",
            "-D",
            PG_DATA,
            "-U",
            "postgres",
            "-E",
            "UTF8",
            "--locale=C",
            "--auth=scram-sha-256",
            "--pwfile=" + _mapped(password_file, alias),
            alias=alias,
        )
    finally:
        password_file.unlink(missing_ok=True)
    postgresql_conf = PG_DATA / "postgresql.conf"
    with postgresql_conf.open("a", encoding="utf-8") as config_file:
        config_file.write(
            "\n# 老友便携版：只监听本机数据库端口\n"
            "listen_addresses = '127.0.0.1'\n"
            f"port = {config['db_port']}\n"
        )
    return instance


def _ensure_db_objects(alias: Path, config: dict[str, Any], instance: dict[str, Any]) -> None:
    admin_env = {"PGPASSWORD": str(instance["postgres_password"])}
    base: tuple[str, ...] = (
        "-h",
        "127.0.0.1",
        "-p",
        str(config["db_port"]),
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-v",
        "ON_ERROR_STOP=1",
    )
    role = _run_pg(
        "psql",
        *base,
        "-Atc",
        "SELECT 1 FROM pg_roles WHERE rolname='laoyou'",
        alias=alias,
        env=admin_env,
    ).stdout.strip()
    if role != "1":
        password = _sql_literal(str(instance["app_password"]))
        _run_pg(
            "psql",
            *base,
            "-c",
            f"CREATE ROLE laoyou LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {password}",
            alias=alias,
            env=admin_env,
        )
    database = _run_pg(
        "psql",
        *base,
        "-Atc",
        "SELECT 1 FROM pg_database WHERE datname='laoyou'",
        alias=alias,
        env=admin_env,
    ).stdout.strip()
    if database != "1":
        _run_pg(
            "createdb",
            "-h",
            "127.0.0.1",
            "-p",
            str(config["db_port"]),
            "-U",
            "postgres",
            "-O",
            "laoyou",
            "laoyou",
            alias=alias,
            env=admin_env,
        )


def _start_database(alias: Path, config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    instance = _init_database(alias, config)
    running = _db_status(alias)
    configured_port = int(config["db_port"])
    saved_port = int(instance.get("db_port", configured_port))
    if running and saved_port != configured_port:
        raise RunnerError(
            f"数据库仍在端口 {saved_port} 运行，而当前配置是 {configured_port}；"
            "请先停止老友，再修改 config/运行配置.json。"
        )
    if not running:
        if _port_in_use(configured_port):
            raise RunnerError(f"数据库端口 {config['db_port']} 已被占用，请修改 config/运行配置.json 后重试。")
        _run_pg(
            "pg_ctl",
            "-D",
            PG_DATA,
            "-l",
            LOGS / "postgres.log",
            "-o",
            f"-p {configured_port}",
            "-w",
            "-t",
            "45",
            "start",
            alias=alias,
        )
    _ensure_db_objects(alias, config, instance)
    if saved_port != configured_port:
        instance["db_port"] = configured_port
        _write_json(INSTANCE_FILE, instance)
    return instance, not running


def _backend_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _health(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(_backend_url(port) + "/api/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("status") == "ok"
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False


def _process_image(pid: int) -> str | None:
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    access = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.c_uint32(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def _process_creation_time(pid: int) -> float | None:
    if os.name != "nt":
        return None
    class _FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created = _FileTime()
        exited = _FileTime()
        kernel = _FileTime()
        user = _FileTime()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = (int(created.high) << 32) | int(created.low)
        return (ticks - 116444736000000000) / 10_000_000
    finally:
        kernel32.CloseHandle(handle)


def _same_executable(pid: int) -> bool:
    image = _process_image(pid)
    if not image:
        return False
    try:
        return str(Path(image).resolve()).casefold() == str(PYTHON.resolve()).casefold()
    except OSError:
        return os.path.normcase(image) == os.path.normcase(str(PYTHON))


def _load_backend_state() -> dict[str, Any] | None:
    if not BACKEND_STATE_FILE.exists():
        return None
    value = _read_json(BACKEND_STATE_FILE)
    return value if isinstance(value, dict) else None


def _backend_pid(state: dict[str, Any] | None) -> int | None:
    if not state:
        return None
    try:
        pid = int(state["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    if pid <= 0 or not _same_executable(pid):
        return None
    expected_created = state.get("process_created_at")
    if expected_created is not None:
        try:
            actual_created = _process_creation_time(pid)
            if actual_created is None or abs(actual_created - float(expected_created)) > 5:
                return None
        except (TypeError, ValueError):
            return None
    return pid


def _kill_backend(state: dict[str, Any], config: dict[str, Any]) -> None:
    try:
        pid = int(state["pid"])
    except (KeyError, TypeError, ValueError):
        BACKEND_STATE_FILE.unlink(missing_ok=True)
        return
    image = _process_image(pid)
    if image is None:
        # 崩溃或用户已手动结束后端时清理陈旧状态，并继续停止数据库。
        BACKEND_STATE_FILE.unlink(missing_ok=True)
        return
    if not _same_executable(pid):
        raise RunnerError("记录的 PID 已被其他程序占用，为安全起见未强制结束。")
    expected_created = state.get("process_created_at")
    if expected_created is not None:
        actual_created = _process_creation_time(pid)
        if actual_created is None or abs(actual_created - float(expected_created)) > 5:
            raise RunnerError("记录的 PID 创建时间不匹配，为安全起见未强制结束。")
    expected_port = int(state.get("port", config["app_port"]))
    if not _health(expected_port):
        # 进程仍是本包 Python，但健康接口不在位；仍允许结束本包记录的进程。
        pass
    result = subprocess.run(
        ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RunnerError(f"后端停止失败：{(result.stdout or result.stderr).strip()[-800:]}")
    for _ in range(20):
        if _process_image(pid) is None:
            break
        time.sleep(0.15)
    BACKEND_STATE_FILE.unlink(missing_ok=True)


def _start_backend(config: dict[str, Any], instance: dict[str, Any]) -> bool:
    state = _load_backend_state()
    pid = _backend_pid(state)
    if pid is not None and _health(int(state.get("port", config["app_port"]))):
        print(f"老友已经在运行：{_backend_url(int(state.get('port', config['app_port'])))}")
        return False
    if state is not None:
        BACKEND_STATE_FILE.unlink(missing_ok=True)
    port = int(config["app_port"])
    if _port_in_use(port):
        raise RunnerError(f"Web 端口 {port} 已被其他程序占用，请修改 config/运行配置.json 后重试。")
    env = _pg_env(
        {
            "DATABASE_URL": (
                "postgresql+psycopg://laoyou:"
                + str(instance["app_password"])
                + f"@127.0.0.1:{config['db_port']}/laoyou"
            ),
            "LAOYOU_HOST": str(config["host"]),
            "LAOYOU_PORT": str(port),
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(BACKEND_DIR),
        }
    )
    stdout_path = LOGS / "backend.stdout.log"
    stderr_path = LOGS / "backend.stderr.log"
    command = [
        str(PYTHON),
        "-X",
        "utf8",
        "-m",
        "uvicorn",
        "app.main:app",
        "--app-dir",
        str(BACKEND_DIR),
        "--host",
        str(config["host"]),
        "--port",
        str(port),
    ]
    try:
        with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open(
            "a", encoding="utf-8"
        ) as stderr:
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
    except OSError as exc:
        raise RunnerError(f"无法启动后端：{exc}") from exc
    _write_json(
        BACKEND_STATE_FILE,
        {
            "pid": process.pid,
            "port": port,
            "started_at": time.time(),
            "process_created_at": _process_creation_time(process.pid),
        },
    )
    for _ in range(90):
        if process.poll() is not None:
            detail = stderr_path.read_text(encoding="utf-8", errors="replace")[-1600:]
            BACKEND_STATE_FILE.unlink(missing_ok=True)
            raise RunnerError(f"后端进程提前退出：\n{detail}")
        if _health(port):
            return True
        time.sleep(0.5)
    try:
        _kill_backend({"pid": process.pid, "port": port}, config)
    except RunnerError:
        pass
    raise RunnerError("后端在 45 秒内没有通过健康检查，请运行 诊断老友.cmd 查看 logs。")


def _stop_database(alias: Path) -> bool:
    if not (PG_DATA / "PG_VERSION").exists():
        return False
    if not _db_status(alias):
        return False
    _run_pg("pg_ctl", "-D", PG_DATA, "-m", "fast", "-w", "-t", "45", "stop", alias=alias)
    return True


def _private_ipv4_addresses() -> list[str]:
    values: set[str] = set()
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
        values.update(addresses)
    except OSError:
        pass
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            values.add(item[4][0])
    except OSError:
        pass
    return sorted(
        value
        for value in values
        if value != "127.0.0.1" and (value.startswith("10.") or value.startswith("192.168.") or value.startswith("172."))
    )


def _print_info(config: dict[str, Any]) -> None:
    port = int(config["app_port"])
    print(f"本机地址：http://localhost:{port}")
    addresses = _private_ipv4_addresses()
    if addresses:
        print("手机与电脑连接同一 Wi-Fi 后，可使用：")
        for address in addresses:
            print(f"  http://{address}:{port}")
    else:
        print("暂未发现局域网 IPv4 地址；请查看 Windows 的 ipconfig 后使用本机地址。")
    print("只建议在家庭/机构可信局域网使用，不会自动开放到互联网。")


def _tail(path: Path, limit: int = 1800) -> str:
    if not path.exists():
        return "（尚无日志）"
    return path.read_text(encoding="utf-8", errors="replace")[-limit:].strip() or "（空）"


def _existing_backend_state() -> tuple[dict[str, Any], int] | None:
    state = _load_backend_state()
    pid = _backend_pid(state)
    if state is None or pid is None:
        return None
    try:
        port = int(state.get("port", 0))
    except (TypeError, ValueError):
        return None
    if not 1 <= port <= 65535 or not _health(port):
        return None
    return state, port


def start(open_browser: bool | None = None) -> int:
    _ensure_directories()
    config = _read_config()
    existing = _existing_backend_state()
    if existing is not None:
        _, actual_port = existing
        print(f"老友已经在运行，当前 Web 端口是 {actual_port}。")
        if actual_port != int(config["app_port"]):
            print(
                f"提示：配置文件当前写的是 {config['app_port']}；请停止老友后再修改端口，"
                "本次继续使用正在运行的端口。"
            )
        _print_info({**config, "app_port": actual_port})
        should_open = config["open_browser"] if open_browser is None else open_browser
        if should_open:
            webbrowser.open(_backend_url(actual_port), new=2)
        return 0
    alias, letter = _ensure_drive_alias()
    db_started = False
    try:
        instance, db_started = _start_database(alias, config)
        _start_backend(config, instance)
        print("老友已启动。")
        _print_info(config)
        should_open = config["open_browser"] if open_browser is None else open_browser
        if should_open:
            webbrowser.open(_backend_url(int(config["app_port"])), new=2)
        return 0
    except Exception:
        if db_started:
            try:
                _stop_database(alias)
            except Exception:
                pass
        raise
    finally:
        # 数据库运行期间保留映射；停止命令会撤销它。若启动失败且数据库未运行，
        # 释放本次动态盘符，避免占用用户的盘符。
        if not _db_status(alias):
            _release_drive_alias(letter)


def stop() -> int:
    _ensure_directories()
    config = _read_config()
    alias, letter = _ensure_drive_alias()
    try:
        state = _load_backend_state()
        if state is not None:
            _kill_backend(state, config)
        stopped = _stop_database(alias)
        print("老友已停止，数据仍保留在 data/。" if stopped or state else "老友当前没有运行。")
        return 0
    finally:
        if not _db_status(alias):
            _release_drive_alias(letter)


def status() -> int:
    _ensure_directories()
    config = _read_config()
    alias, letter = _ensure_drive_alias()
    try:
        db_running = _db_status(alias)
        state = _load_backend_state()
        app_port = int(state.get("port", config["app_port"])) if state else int(config["app_port"])
        app_running = bool(state and _backend_pid(state) and _health(app_port))
        db_port = int(config["db_port"])
        if db_running and INSTANCE_FILE.exists():
            try:
                db_port = int(_load_instance().get("db_port", db_port))
            except (RunnerError, TypeError, ValueError):
                pass
        print(f"Web：{'运行中' if app_running else '未运行'}（{_backend_url(app_port)}）")
        print(f"数据库：{'运行中' if db_running else '未运行'}（本机 {db_port}）")
        if app_running:
            _print_info({**config, "app_port": app_port})
        return 0
    finally:
        if not _db_status(alias):
            _release_drive_alias(letter)


def info() -> int:
    config = _read_config()
    existing = _existing_backend_state()
    actual_port = existing[1] if existing is not None else int(config["app_port"])
    if existing is not None and actual_port != int(config["app_port"]):
        print(
            f"提示：配置文件当前写的是 {config['app_port']}，但正在运行的 Web 端口是 {actual_port}；"
            "请先停止老友再修改端口。"
        )
    _print_info({**config, "app_port": actual_port})
    output = ROOT / "手机连接说明.txt"
    lines = ["老友手机连接说明", "", f"电脑本机地址：http://localhost:{actual_port}", ""]
    addresses = _private_ipv4_addresses()
    if addresses:
        lines.append("安装老友Android APP并与电脑连接同一Wi-Fi，在APP服务地址填写：")
        lines.extend(f"http://{address}:{actual_port}" for address in addresses)
    else:
        lines.append("未发现局域网地址，请在 Windows 命令行运行 ipconfig 查看 IPv4。")
    lines.extend(
        [
            "",
            "首次访问若出现 Windows 防火墙提示，只允许专用网络。",
            "本程序不会自动把端口开放到互联网。",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已生成：{output.name}")
    return 0


def diagnose() -> int:
    _ensure_directories()
    config = _read_config()
    print("老友诊断")
    print(f"包目录：{ROOT}")
    print(f"配置：Web {config['app_port']} / 数据库 {config['db_port']}")
    try:
        status()
    except Exception as exc:
        print(f"生命周期检查失败：{exc}")
    print("\nbackend.stderr.log：")
    print(_tail(LOGS / "backend.stderr.log"))
    print("\npostgres.log：")
    print(_tail(LOGS / "postgres.log"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="老友 Windows 便携包运行器")
    parser.add_argument("command", choices=("start", "stop", "status", "info", "diagnose"))
    parser.add_argument("--no-browser", action="store_true", help="启动后不打开浏览器")
    args = parser.parse_args(argv)
    try:
        if args.command in {"start", "stop"}:
            with _LifecycleLock():
                if args.command == "start":
                    return start(open_browser=False if args.no_browser else None)
                return stop()
        if args.command == "status":
            return status()
        if args.command == "info":
            return info()
        return diagnose()
    except RunnerError as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        print(f"请查看：{LOGS}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - final boundary for a launcher
        print(f"操作失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"请查看：{LOGS}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
