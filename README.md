# 老友

面向独居老人的适老化总控屏，以及子女/社区安卓客户端。后端集中处理用户权限、提醒、观测规则、异常通知与处置记录。

本项目仅实现已确认的11项需求。设备观测、120通知和放心医服务以明确标记的模拟方式验收；未配置的方言服务不会冒充可用。需求和验收范围见 [开发安排](docs/PLAN.md)，接口见 [API契约](docs/API.md)。

## 在这台电脑启动

双击根目录 **启动老友.cmd**，或在 PowerShell 7 中运行：

```powershell
./scripts/start.ps1 -OpenBrowser
```

总控页面：`http://localhost:8000`。API文档：`http://localhost:8000/docs`。后端健康：`http://localhost:8000/api/health`。

演示账号：`elder`（老人屏）、`child`（子女）、`community`（社区）、`admin`（演示管理）。密码均为 `Laoyou123!`。这些是虚构家庭的本机演示账号，不是公开部署的生产配置。

关闭服务：

```powershell
./scripts/stop.ps1
```

数据库保留在 `runtime/postgres-data`，停止服务不会删除数据。数据库仅监听本机 `127.0.0.1:55432`，后台连接凭据在未纳入Git的 `runtime/local.env`。

## 安卓端

安装包完成构建后位于 `artifacts/laoyou-debug.apk`。手机和电脑在同一局域网时，在APP登录页将服务地址改为 `http://电脑局域网IP:8000`。

这台电脑的验收模拟器通过ADB反向端口连接，在APP填 `http://127.0.0.1:8000`：

```powershell
& 'D:/Android/Sdk/platform-tools/adb.exe' -s emulator-5554 reverse tcp:8000 tcp:8000
```

浏览器媒体权限要求安全上下文。电脑用 `localhost`，验收模拟器用ADB反向连接的 `127.0.0.1`；普通局域网HTTP地址可以读取业务数据，但浏览器/WebView可能不允许摄像头和麦克风。公网音视频未作为已接通能力承诺。

## 开发与复验

固定技术：Vue3/TypeScript、FastAPI/Python3.11/PostgreSQL、Kotlin/Compose。入口分别为 `web/`、`backend/app/main.py`、`android/`。

```powershell
./scripts/build-web.ps1
./backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -q
./backend/.venv/Scripts/python.exe -X utf8 scripts/smoke_api.py --base http://127.0.0.1:8000
```

最后一条会创建带“联通测试”说明的模拟事件，保留审计记录并删除临时提醒，请只在演示数据中运行。开发验收使用独立数据库，结果见 [验收记录](docs/ACCEPTANCE.md)。

需要隔离复验时，运行 `./backend/.venv/Scripts/python.exe -X utf8 scripts/start-acceptance.py`，再把测试地址改为 `http://localhost:8001`。`scripts/stop.ps1` 会停止本项目管理的普通与验收后端。

所有项目文件均在本工作区。下载缓存、测试截图、日志及Gradle缓存统一放 `tmp/`；可运行工具及数据库放 `runtime/`，二者不纳入Git。

Windows PostgreSQL在中文二进制路径下初始化UTF-8数据库会失败，因此 `scripts/database.py` 使用经 `samefile` 校验的 `L:` 工作区别名。它始终映射回本目录，不复制或转移文件；如果L盘已被其他用途占用，脚本会停止并报错。没有安装Windows数据库服务。

后端依赖已锁定在 `backend/requirements.lock.txt`；安卓调试签名位于 `runtime/android-debug.keystore`，仅用于本次验收包。

## 可配置语音

普通话可用浏览器设备语音与文字入口；方言服务接口支持普通话、粤语、四川话、东北话选项。若需要讯飞识别，在 `runtime/local.env` 增加服务账号自己的 `XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`，并在服务方开通相应方言权限后重启后端。密钥仅在后端使用，录音不落盘。

未配置时会明确显示能力限制。服务凭据、真实方言识别准确率、真实设备及机构送达属于外部接入验证，实际完成情况以交付验证记录为准。
