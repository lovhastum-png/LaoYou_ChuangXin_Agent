# 老友项目工作约定

- 项目根目录为 `D:/代码玩具测试/老友`。所有源码、文档、数据、测试及构建产物留在工作区；临时文件统一放 `tmp/`。不在其他项目借用临时目录。不清理用户文件。
- 首版目标与实际验收见 `docs/PLAN.md`、`docs/ACCEPTANCE.md`；后续按用户最新范围推进。不要添加需求外功能、微服务、过度兼容层或未来预留。
- 固定技术栈：Vue3 + TypeScript 总控 WebUI；Python 3.11 + FastAPI + PostgreSQL 后端；Kotlin + Jetpack Compose Android 子女/社区 APP。
- 主代理负责接口、架构、集成与复核。子工作组仅使用 `gpt-5.6-luna`、`max`，按分配的目录工作，不另建用户任务。公共接口以 `docs/API.md` 为准，变更先通知主代理。
- 设备和机构通知允许明确标注的模拟接入。不得把模拟送达写成真实联系120，不得伪造监控识别或方言识别已验证。实际业务状态、提醒、权限、审计和事件处理必须持久化且能运行。
- 使用清楚中文。最小充分结构，业务规则唯一来源。异常阈值为演示可配置规则，不宣称医疗诊断。
- Python 使用 `backend/.venv/Scripts/python.exe -X utf8`。Node 24.20.0、pnpm，依赖仓库服从全局配置。PowerShell 使用工具已验证的 PowerShell 7。安卓 SDK 为 `D:/Android/Sdk`；构建暂用 JDK17 `C:/Program Files/Java/jdk-17`，Gradle 缓存 `tmp/gradle-home`。
- 每个工作组执行定向测试或构建，不得只写代码不验证。主代理负责跨端测试。不要擅自提交 Git、删除其他组文件、安装全局服务或更换技术栈。
