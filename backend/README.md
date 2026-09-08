# 老友后端

后端入口是 `app.main:app`，技术栈为 Python 3.11、FastAPI、SQLAlchemy 2 和 PostgreSQL。数据库连接只从 `DATABASE_URL` 读取；未设置时启动会直接给出配置错误，不会悄悄切换到 SQLite 或超级用户。

在工作区根目录运行：

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -m pip install -r backend/requirements.lock.txt
$env:DATABASE_URL = "postgresql+psycopg://laoyou:<password>@127.0.0.1:55432/laoyou"
& backend/.venv/Scripts/python.exe -X utf8 -m uvicorn --app-dir backend app.main:app --host 0.0.0.0 --port 8000
```

首次启动会执行可重复的 schema 初始化并创建本地演示账号：`elder`、`child`、`community`、`admin`，密码均为 `Laoyou123!`。调度器每 5 秒检查一次待播报，播报唯一键和事件/通知记录均持久化。

定向规则测试：

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
```

讯飞语音为可选接入，需同时配置 `XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`；未配置时使用浏览器语音/文字入口，`/api/capabilities` 会明确标注状态。摄像头、穿戴、120、放心医均按 API 契约标明真实或模拟边界。
