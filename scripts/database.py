"""Manage the project-local PostgreSQL instance; never install a Windows service."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
BIN = RUNTIME / 'postgresql' / 'pgsql' / 'bin'
DATA = RUNTIME / 'postgres-data'
TEMP = ROOT / 'tmp'
ENV_FILE = RUNTIME / 'local.env'
CREDS = RUNTIME / 'database-credentials.json'
PORT = 55432


def tool_root() -> Path:
    """Windows PostgreSQL initdb cannot encode its non-ASCII binary path as UTF-8.

    A verified SUBST alias points to this exact workspace, without copying any data.
    """
    if str(ROOT).isascii():
        return ROOT
    alias = Path('L:/')
    if not alias.exists():
        subprocess.run(['subst.exe', 'L:', str(ROOT)], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if not os.path.samefile(alias, ROOT):
        raise RuntimeError('L: is occupied by another location; cannot create the workspace alias.')
    return alias


def run(name: str, *args: str, env: dict | None = None, check: bool = True):
    executable = BIN / f'{name}.exe'
    if not executable.is_file():
        raise RuntimeError(f'PostgreSQL executable not found: {executable}')
    alias = tool_root()
    def argument(value):
        if isinstance(value, Path) and value.is_relative_to(ROOT):
            return str(alias / value.relative_to(ROOT))
        return str(value).replace(str(ROOT), str(alias).rstrip('\\/'))
    process_env = os.environ.copy()
    process_env.update(TEMP=argument(TEMP), TMP=argument(TEMP), PGCLIENTENCODING='UTF8')
    if env:
        process_env.update(env)
    command = [argument(executable), *map(argument, args)]
    if name == 'pg_ctl' and 'start' in args:
        # Windows may pass pipe handles to the background server, preventing
        # communicate() from reaching EOF even after pg_ctl has exited.
        output = TEMP / 'postgres-start-command.log'
        with output.open('w', encoding='utf-8') as stream:
            result = subprocess.run(command, env=process_env, cwd=alias, check=check,
                                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        result.stdout = output.read_text('utf-8', errors='replace')
        result.stderr = ''
        return result
    return subprocess.run(command, env=process_env,
                          cwd=alias, check=check, capture_output=True,
                          text=True, encoding='utf-8', errors='replace',
                          creationflags=subprocess.CREATE_NO_WINDOW)


def start():
    if not (DATA / 'PG_VERSION').exists():
        raise RuntimeError('Database is not initialized. Run database.py init first.')
    state = run('pg_ctl', '-D', DATA, 'status', check=False)
    if state.returncode == 0:
        print(f'PostgreSQL already running on 127.0.0.1:{PORT}')
        return
    result = run('pg_ctl', '-D', DATA, '-l', TEMP / 'postgres.log', '-w', '-t', '45', 'start')
    print(result.stdout.strip())


def initialize():
    for path in (RUNTIME, TEMP):
        path.mkdir(exist_ok=True)
    if not CREDS.exists():
        if DATA.exists() and any(DATA.iterdir()):
            raise RuntimeError('Existing database has no project credential file; refusing to replace it.')
        CREDS.write_text(json.dumps({'postgres': secrets.token_urlsafe(24),
                                    'laoyou': secrets.token_urlsafe(24)}), encoding='utf-8')
    credentials = json.loads(CREDS.read_text('utf-8'))
    if not (DATA / 'PG_VERSION').exists():
        password_file = TEMP / 'postgres-init-password.txt'
        password_file.write_text(credentials['postgres'], encoding='utf-8')
        try:
            result = run('initdb', '-D', DATA, '-U', 'postgres', '-E', 'UTF8',
                         '--locale=C', '--auth=scram-sha-256', '--pwfile=' + str(password_file))
            print(result.stdout.strip())
        finally:
            password_file.unlink(missing_ok=True)
        with (DATA / 'postgresql.conf').open('a', encoding='utf-8') as config:
            config.write(f"\n# Laoyou local development\nlisten_addresses = '127.0.0.1'\nport = {PORT}\n")
    start()
    admin_env = {'PGPASSWORD': credentials['postgres']}
    base = ('-h', '127.0.0.1', '-p', str(PORT), '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1')
    role_exists = run('psql', *base, '-Atc', "SELECT 1 FROM pg_roles WHERE rolname='laoyou'", env=admin_env).stdout.strip()
    if role_exists != '1':
        # Password is locally generated URL-safe text, never user input.
        run('psql', *base, '-c', f"CREATE ROLE laoyou LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{credentials['laoyou']}'", env=admin_env)
    db_exists = run('psql', *base, '-Atc', "SELECT 1 FROM pg_database WHERE datname='laoyou'", env=admin_env).stdout.strip()
    if db_exists != '1':
        run('createdb', '-h', '127.0.0.1', '-p', str(PORT), '-U', 'postgres', '-O', 'laoyou', 'laoyou', env=admin_env)
    ENV_FILE.write_text(f"DATABASE_URL=postgresql+psycopg://laoyou:{credentials['laoyou']}@127.0.0.1:{PORT}/laoyou\n", encoding='utf-8')
    print(f'Local application database ready. Configuration: {ENV_FILE}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init', 'start', 'stop', 'status'])
    action = parser.parse_args().action
    if action == 'init':
        initialize()
    elif action == 'start':
        start()
    elif action == 'stop':
        if run('pg_ctl', '-D', DATA, 'status', check=False).returncode == 3:
            print('PostgreSQL is already stopped.')
            return 0
        result = run('pg_ctl', '-D', DATA, '-m', 'fast', '-w', 'stop', check=False)
        print(result.stdout.strip() or result.stderr.strip())
        return result.returncode
    else:
        result = run('pg_ctl', '-D', DATA, 'status', check=False)
        print(result.stdout.strip() or result.stderr.strip())
        return result.returncode
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stdout, exc.stderr, file=sys.stderr)
        sys.exit(exc.returncode)
