from pathlib import Path
import json
import os
import subprocess
import sys
import socket
import time
import httpx

root = Path(__file__).resolve().parents[1]
with socket.socket() as probe:
    try:
        probe.bind(('127.0.0.1', 8001))
    except OSError:
        raise SystemExit('Port 8001 is already in use. Stop the previous acceptance service first.')
sys.path.insert(0, str(root/'scripts'))
from database import run

credentials = json.loads((root/'runtime/database-credentials.json').read_text('utf-8'))
admin_env = {'PGPASSWORD': credentials['postgres']}
base = ('-h','127.0.0.1','-p','55432','-U','postgres','-d','postgres')
exists = run('psql', *base, '-Atc', "SELECT 1 FROM pg_database WHERE datname='laoyou_acceptance'", env=admin_env).stdout.strip()
if exists != '1':
    run('createdb','-h','127.0.0.1','-p','55432','-U','postgres','-O','laoyou','laoyou_acceptance',env=admin_env)
env=os.environ.copy()
env.update(TEMP=str(root/'tmp'),TMP=str(root/'tmp'),PYTHONUTF8='1',
           DATABASE_URL=f"postgresql+psycopg://laoyou:{credentials['laoyou']}@127.0.0.1:55432/laoyou_acceptance")
with (root/'tmp/acceptance-server.log').open('w',encoding='utf-8') as log:
    process=subprocess.Popen([str(root/'backend/.venv/Scripts/python.exe'),'-X','utf8','-m','uvicorn','app.main:app','--app-dir',str(root/'backend'),'--host','127.0.0.1','--port','8001','--no-access-log'],cwd=root,env=env,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
(root/'tmp/acceptance-process.json').write_text(json.dumps({'Pid':process.pid,'Port':8001,'Workspace':str(root)}),encoding='utf-8')
for attempt in range(30):
    try:
        response = httpx.get('http://127.0.0.1:8001/api/health', timeout=1)
        if response.status_code == 200:
            break
    except httpx.HTTPError:
        pass
    if process.poll() is not None:
        raise SystemExit('Acceptance service exited. Inspect tmp/acceptance-server.log.')
    time.sleep(0.3)
else:
    raise SystemExit('Acceptance service was not ready. Inspect tmp/acceptance-server.log.')
print('Acceptance API PID',process.pid,'port 8001')
