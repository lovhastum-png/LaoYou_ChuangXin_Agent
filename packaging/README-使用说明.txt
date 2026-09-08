老友 Windows 便携版
====================

这是 Windows 10/11 x64 的免安装包。解压后双击“启动老友.cmd”，浏览器会打开
老友总控页面。程序不会安装 Windows 服务，也不会自动把端口开放到互联网。

第一次启动
----------
首次启动会在 data/ 中初始化本包专用数据库，可能需要几十秒。演示账号如下：

  elder      老人屏
  child      子女
  community  社区
  admin      演示管理

所有账号的密码都是：Laoyou123!

常用入口
--------
  启动老友.cmd       启动数据库和 Web 端，并按配置打开浏览器
  停止老友.cmd       结束本包启动的 Web 端和数据库，保留 data/ 数据
  查看状态.cmd       查看 Web、数据库和本机访问地址
  手机连接说明.cmd   显示局域网地址并生成“手机连接说明.txt”
  诊断老友.cmd       查看状态和最近的后端/数据库日志

手机访问
--------
先从以下发布页下载laoyou-android.apk并安装：
https://github.com/falling-feather/LaoYou_ChuangXin_Agent/releases/latest
电脑和手机连接同一个Wi-Fi，双击“手机连接说明.cmd”，将列出的
http://局域网地址:18080填入APP“服务地址”。首次出现防火墙提示时，允许“专用网络”。
如果看不到局域网地址，可在 Windows 命令行运行 ipconfig 查看电脑 IPv4 地址。

端口与配置
----------
默认 Web 端口为 18080，数据库端口为 55433。需要避开占用端口时，先停止老友，编辑
config/运行配置.json 中的 app_port 或 db_port，然后再次启动。host 默认是 0.0.0.0，
用于让同一局域网的手机访问；数据库始终只监听本机。修改配置前请保留 JSON 格式。

数据和更新
----------
data/ 是本包的持久化数据目录，包含首次运行后生成的数据库和本包凭据；logs/ 是
运行日志，state/ 是临时进程状态。停止老友后再移动、更新或替换程序文件。

更新时先用旧包“停止老友.cmd”，并确认旧包的 Web 和数据库都已停止；不要在数据库
运行时复制 data/。把新的 laoyou-windows-x64.zip 解压到新目录，在新包第一次启动
之前，把旧包的 data/ 和 config/ 复制到新包对应位置。新包的 data/ 应仍为空，复制
前不要用不可恢复的方式删除新包已有数据；若已经启动过新包，请改用全新的解压目录
迁移。不要覆盖新包的 runtime/、backend/、web/、support/，也不要从旧包复制 state/。
若保留日志用于排查，可以复制 logs/，否则新包会自动创建空日志目录。

开发者可从源码重新运行 scripts/build-package.ps1 生成同名 ZIP；脚本使用官方 Python
3.11.9 embeddable runtime 和 backend/requirements.lock.txt，生成包不复制 backend/.venv，
也不带源码工作区的 runtime/postgres-data、database-credentials.json 或 local.env。

系统要求与故障排查
------------------
需要 Windows 10/11 x64；本包已附带所需的Microsoft Visual C++运行库，普通用户
无需安装开发工具。如果提示缺少MSVCP140或VCRUNTIME140，请先重新完整解压并检查
安全软件是否隔离了文件。无需管理员权限；若防火墙拦截，请只为专用网络放行。

启动失败时运行“诊断老友.cmd”，把屏幕提示和 logs/backend.stderr.log、logs/postgres.log
交给维护人员。不要删除 data/，也不要把 data/instance.json 发给他人。

第三方组件许可
--------------
PostgreSQL、Python 及 Python 依赖的许可随包放在 licenses/；依赖版本清单在
licenses/PYTHON-DEPENDENCIES.txt。PostgreSQL 的 server_license.txt 和第三方许可原文
也一并保留。

完整使用和开发者文档：
https://github.com/falling-feather/LaoYou_ChuangXin_Agent
本版本的设备观测和机构通知为明确标记的模拟接入；业务记录真实保存。
