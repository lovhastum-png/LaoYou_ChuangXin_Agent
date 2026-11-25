# 老友 · 适老化照护系统（LaoYou）

面向独居老人的适老化总控屏，以及子女、社区使用的照护应用。电脑提供大字网页和后端服务，手机查看健康记录、异常预警并进行视频通话。

当前是可运行的本地验收版本：设备行为、穿戴数据和120／放心医机构流程采用明确标记的模拟接入，业务记录和处置时间线真实持久化。功能与接入边界详见[项目总纲](doc/00-项目总纲.md)。

## 技术亮点

### 跌倒识别引擎（[`backend/pose/`](backend/pose)）

仓库里最值得看的一部分。**没有训练任何自有模型**——用 COCO 17 关键点的
YOLOv8-pose 预训练权重，配合几何 + 运动学规则状态机
（`ABSENT → UPRIGHT → DESCENDING → LYING → ALARMED`）判断跌倒，
CPU 上单帧 35 ms（约 28 fps @640px），独立侧车进程，不侵入后端。

- **两条硬判据 AND**：鼻部下降速度 ≥ 0.8 画面高/秒（或躯干角速度 ≥ 600°/s）
  **且** 躯干角 ≥ 62° 连续 5 帧。慢慢躺下、坐下、弯腰都不触发。
- **URFD 公开数据集全量实测**（100 分卷 / 70 个唯一场景）：召回 0.700、
  精确 0.750、F1 0.724。
- **边界和失败案例写在文档里**：横卧阈值是机位相关的（侧视 62°，吊装实测需
  标定到 52°）、单目姿态在特定视角会漏检、「横卧」与「躺下休息」难区分——
  详见 [`backend/pose/README.md`](backend/pose/README.md)，其中也保留了被自己
  推翻的旧结论（小样本乐观值已标注作废）。

### 事件链路与可追溯

观测 → 规则判定 → 事件分级 → 通知子女／社区 → 处置时间线，全流程落库。
家属端的事件详情是独立整页（可刷新、可直达链接），能直接回放跌倒识别实录：
画面里同时有骨架叠加、躯干角与阈值刻度、告警触发时刻。

### 三端协同

Vue 3 + TypeScript 的适老化 WebUI（老人屏／家属／社区共用一套组件与主题）、
FastAPI 后端（HTTP + WebSocket）、Kotlin + Jetpack Compose 的安卓子女端；
通话走 WebRTC，跨网络用 Cloudflare Tunnel + TURN 中继。

## 直接下载使用

无需下载源码，也无需安装开发IDE。

| 你要使用什么 | 下载 | 需要什么 |
|---|---|---|
| 电脑总控＋后端 | [Windows 便携包](https://github.com/lovhastum-png/LaoYou_ChuangXin_Agent/releases/latest/download/laoyou-windows-x64.zip) | Windows 10/11 x64，浏览器；包内自带Python及PostgreSQL |
| 子女／社区手机端 | [安卓 APK](https://github.com/lovhastum-png/LaoYou_ChuangXin_Agent/releases/latest/download/laoyou-android.apk) | Android 8.0及以上，能访问已启动的电脑服务 |
| 其他版本及校验值 | [Releases 下载页](https://github.com/lovhastum-png/LaoYou_ChuangXin_Agent/releases) | 可分别选择电脑包和APK |

### 电脑端：解压后启动

1. 将Windows ZIP完整解压到可写文件夹，双击 **启动老友.cmd**。
2. 等待首次数据库准备完成，浏览器自动打开总控页面。
3. 选择“老人屏”“家属端”或“社区端”，点击“进入老友”。
4. 不用时双击 **停止老友.cmd**；关闭网页不会自动停止后端。

默认电脑地址为 `http://localhost:18080`。检查运行状态用 **查看状态.cmd**。数据随电脑包保留，更新前先停止服务并按[备份与更新说明](doc/01-子文档/12-安装使用与维护.md#5-停止备份与更新)操作。

### 手机端：安装后连接电脑

1. 安装APK，手机与电脑连接同一个家庭或办公局域网。
2. 在电脑包中双击 **手机连接说明.cmd**，将显示的地址填入APP“服务地址”，例如 `http://192.168.1.100:18080`。
3. 使用子女或社区账号登录；通话时允许摄像头和麦克风。Windows询问网络权限时允许可信的专用网络。

手机不能填写电脑上的 `localhost`。电脑需要保持开机且服务运行。APK为使用固定调试证书签名的验收安装包，未上架应用商店。

**电脑与手机不在同一网络时**：在源码工作区先启动老友，再双击 **启动老友-公网.cmd**，脚本会建立 Cloudflare 隧道并打印一个 `https://xxx.trycloudflare.com` 地址，把这个地址填进APP“服务地址”即可（无需公网IP、无需在路由器开端口；该窗口关闭即断开）。原理、TURN中继和安全要求见[跨网络访问](doc/01-开发者文档.md#157-跨网络访问cloudflare-tunnel)。便携包（ZIP）本身不含隧道组件。

| 演示身份 | 账号 | 密码 |
|---|---|---|
| 老人 | `elder` | `Laoyou123!` |
| 子女 | `child` | `Laoyou123!` |
| 社区 | `community` | `Laoyou123!` |
| 演示管理 | `admin` | `Laoyou123!` |

完整操作、权限、提醒、摄像头、通话和故障排查见[安装使用与维护](doc/01-子文档/12-安装使用与维护.md)。老人网页需保持打开才能播报、采集快照和接听来电；APP被系统冻结或关闭后不保证即时通知。

## 开发者入口

一个仓库维护三部分，通过HTTP API和WebSocket协作。可以只修改目标端，运行时仍需连接后端。

| 要开发的部分 | 目录 | 技术 |
|---|---|---|
| 适老化总控／家属网页 | [web](web) · [仅下载Web源码](https://github.com/lovhastum-png/LaoYou_ChuangXin_Agent/releases/latest/download/laoyou-web-source.zip) | Vue 3、TypeScript、Vite |
| 智能指令、规则、用户和数据服务 | [backend](backend) · [仅下载后端源码](https://github.com/lovhastum-png/LaoYou_ChuangXin_Agent/releases/latest/download/laoyou-backend-source.zip) | Python 3.11、FastAPI、PostgreSQL |
| 子女／社区APP | [android](android) · [仅下载Android源码](https://github.com/lovhastum-png/LaoYou_ChuangXin_Agent/releases/latest/download/laoyou-android-source.zip) | Kotlin、Jetpack Compose，包含通话WebView |
| Windows封包和启动器 | [packaging](packaging) | 包内运行环境、初始化和进程管理 |

下载源码使用仓库的 **Code → Download ZIP**，或：

```bash
git clone https://github.com/lovhastum-png/LaoYou_ChuangXin_Agent.git
```

### 源码本地跑起来（SQLite 预览模式，不需要 PostgreSQL）

```bash
# 1. 后端依赖（Python 3.11+）
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt        # macOS / Linux

# 2. 前端构建（Node 22+）
cd ../web && npm install && npm run build

# 3. 启动：后端托管 web/dist，数据库落在 tmp/preview.db
cd .. && backend/.venv/Scripts/python.exe -X utf8 scripts/preview.py
```

浏览器会自动打开 `http://127.0.0.1:8000`，用上方演示账号登录。该模式为单人预览，
不支持多 worker 并发；正式形态用 PostgreSQL，差异见
[开发者文档 15.3.1](doc/01-开发者文档.md#1531-本地快速预览模式sqlite)。

跌倒识别引擎是独立侧车，需要单独的虚拟环境（含 PyTorch，1–2 GB）：

```bash
# 在仓库根目录执行
cd backend/pose && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt && cd ../..
backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.test_detector   # 状态机自测，8 项
```

权重 `models/yolov8n-pose.pt` 与 URFD 样本不进仓库（分别是 AGPL-3.0 与学术授权），
首次运行自动下载权重；样本用 `dataset_download.py` 重建。完整用法与阈值标定见
[`backend/pose/README.md`](backend/pose/README.md)。

普通用户下载上方Release中的包体；GitHub自动提供的“Source code”是源码，需要自行准备开发环境。

> Release 中的 Windows／Android 二进制包为 2026-09-09 验收版。跌倒识别引擎与事件
> 详情页在源码中持续更新，二进制包未随之重建；要体验最新功能请用上面的源码启动方式。

- [开发者文档](doc/01-开发者文档.md)：架构、数据、权限、全部功能逻辑、修改入口、构建和测试。
- [跨网络访问](doc/01-开发者文档.md#157-跨网络访问cloudflare-tunnel)：Cloudflare Tunnel 的两种模式、TURN 中继配置、对外暴露前的账号收紧要求。
- [接口契约](doc/01-子文档/11-接口契约.md)：HTTP／WebSocket调用约定。
- [安装与维护](doc/01-子文档/12-安装使用与维护.md#7-开发者维护)：分发包、更新和签名说明。
- [项目规划](doc/02-项目规划.md)及[开发历史](doc/03-开发历史.md)：当前任务和实际验证记录。
- [封包与跨端验收](doc/03-子文档/32-封包与跨端验收.md)：启动、持久化、接口及安卓模拟器与浏览器双向通话证据和未测边界。

仓库不包含本机数据库、凭据、Android签名密钥或IDE。请按[环境准备](doc/01-开发者文档.md#152-新开发者环境准备)配置；直接复制开发机虚拟环境不能替代安装依赖。

## 协议

本项目源码采用 [MIT License](LICENSE)。包内Python、PostgreSQL及其他依赖仍适用各自许可证，随Windows包保留第三方许可信息。
