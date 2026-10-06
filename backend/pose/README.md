# 跌倒检测推理侧车（基线版）

把摄像头画面里发生的跌倒，变成老友后端能接收的一条 `kind=fall` 观测。
**不新增后端接口、不改动现有规则引擎**——事件分级、通知分流、误报修正全部复用原有实现。

## 1. 为什么单独放一个侧车

后端 `backend/.venv` 只装 FastAPI / SQLAlchemy 等轻量依赖；姿态模型需要
PyTorch + Ultralytics（约 1-2 GB）。两者依赖冲突且启动开销差异大，
因此拆成独立目录、独立虚拟环境，通过 HTTP 调用后端，进程互不影响。

```
摄像头 / 视频文件
   └─> backend/pose（本侧车，PyTorch + YOLOv8-pose）
          └─> POST /api/elders/{id}/observations  kind=fall
                 └─> 后端规则引擎 → 创建 critical 事件 → 通知子女+社区
```

## 2. 目录

| 文件 | 作用 |
|---|---|
| `config.py` | 全部阈值与客户端配置，标注了每个数字的实测依据 |
| `detector.py` | 核心：姿态几何 + 运动学 + 状态机 |
| `client.py` | 登录后端并注入观测、保存告警快照 |
| `run.py` | 命令行入口：单视频 / 摄像头 / 批量评测 |
| `calibrate.py` | 阈值标定工具，打印各样本实测峰值（非产品功能） |
| `test_detector.py` | 状态机单测（合成特征序列，不加载模型）；覆盖「沉出画面」判据的四条边界 |
| `models/yolov8n-pose.pt` | 预训练姿态模型（Ultralytics 官方，AGPL-3.0） |
| `samples/` | URFD 真实跌倒/日常片段，用于回归验证 |

## 3. 识别原理

**没有训练任何自己的模型。** 用的是 COCO 人体姿态预训练权重
（`yolov8n-pose.pt`，17 个关键点），判定逻辑是规则状态机：

```
ABSENT → UPRIGHT → DESCENDING → LYING → ALARMED
```

**两条硬判据 + 一条证据链：**

1. **横卧**（姿态）：躯干偏离竖直轴 ≥ 62°。
   用 `arctan2(|dx|, |dy|)` 计算，结果恒在 0-90°，与躯干朝向符号无关。

2. **坠落**（运动学，满足其一）：
   - 鼻部纵坐标下降速度 ≥ 0.8 画面高/秒
   - 躯干角速度 ≥ 600 度/秒

3. **坠落证据回溯**：横卧开始前 2.0 秒内必须出现过「坠落」信号。
   这条是精确度的主要来源——慢慢躺下、坐下、弯腰都不告警。

再加连续 5 帧横卧确认（约 0.17 秒）和 30 秒冷却去重。

### 3.1 可选判据：「坠落并沉出画面」（默认关闭）

上面两条是**硬 AND**：必须「先快速坠落、再确认横卧」。但老人朝镜头方向扑倒时，
身体会在转成横卧之前就从画面下沿消失，`lying` 永远等不到 —— 实测这段
1280×720 走廊平视视频（`scripts/diagnostics/pose-fall-verify/`）就是这样漏的：
下降速度连续 5 帧越过 0.8（峰值 0.978），躯干角却只到 21.1°，随后人出画，**0 告警**。

因此提供一条补充判据，把「沉出画面」当作横卧确认的等价路径：

```
人出画  AND  消失前最后一帧 hip_y ≥ 0.95  AND  2 秒内有坠落证据
```

- 开关：`detect_frame_exit_fall`（默认 `False`）、`exit_frame_hip_threshold`（默认 `0.95`）
- ⚠️ **URFD 全量评估后判定为净负，保持默认关闭且不建议开启**：
  召回 0.700→0.733，但精确 **0.724→0.667**、F1 **0.712→0.698**
  （捞回 `fall-15`，同时新增 `adl-31/38/39` 三条「人快速走出画面」误报）。
  8 段样本曾给出「无退化」的假象 —— 又一次小样本陷阱。
- 实测效果（仅 9 段小样本，**仅供参考、不要据此上线**）：召回 0.667 → 0.833。
  完整记录见 [`doc/03-子文档/34-跌倒识别外场视频验证.md`](../../doc/03-子文档/34-跌倒识别外场视频验证.md)
  与 [`35-跌倒识别机位标定实测.md`](../../doc/03-子文档/35-跌倒识别机位标定实测.md)。

### 3.2 ★ 横卧角度阈值是机位相关的，不是普适常数

`torso_angle_threshold` 衡量的是躯干在**图像平面内**相对图像竖直轴的夹角，
隐含假设「摄像头在人体侧方、光轴大致水平」。换成吊装/高仰角机位后该假设不成立：

| 机位 | 躺下时的躯干角 | 62° 是否可用 |
|---|---|---|
| 近景侧视（URFD 类） | 68–90° | ✅ 可用（全量 F1 最优） |
| 高仰角 / 吊装（居家常见） | **仅 40–60°** | ❌ 漏报，需实测标定（实录素材标定为 52°） |

实测（`scripts/diagnostics/pose-fall-verify/second/`，720×1280 房间吊装机位）：
人躺在地上始终在画面内，躯干角只到 63.1° 且**仅 1 帧越线** → 漏报。

**但不要把全局默认值降到 50°**：URFD 全量上召回一点不涨（0.700 不变），
精确却从 0.724 掉到 **0.600**（详见 `doc/03-子文档/35-...` §5.2）。
正确做法是**按部署机位单独标定**，标定值用上面的命令行覆盖项落到运行时。

> ⚠️ **吊装机位的检出并不可靠**：实录素材在 52° 下能告警，但其「坠落信号」
> 实测是姿态估计抖动造成的单帧尖峰（`nose_y` 呈 +0.030/+0.002 交替），
> **丢帧后即失效**（stride≥2 时 0 组配置可检出）。
> 本机位目前**只有 1 段正样本、0 段负样本**，误报率无从测量。
> 生产环境请先按 `doc/03-子文档/36-...` 第 6 节采集标注数据。

## 4. 实测精度（URFD 数据集，全量 100 分卷 / 70 个唯一场景）

**调参后（`lying_confirm_frames=5`）：**

| 口径 | 召回 | 精确 | F1 | 准确 |
|---|---|---|---|---|
| 按唯一场景（fall 双机位合并，真实部署形态） | 0.700 | 0.750 | 0.724 | 0.771 |
| 按分卷 | 0.517 | 0.816 | 0.633 | 0.640 |

**调参前（`lying_confirm_frames=3`，按唯一场景）：** 召回 0.733 / 精确 0.550 / F1 0.629

> ⚠️ **本节此前写的是「8 段样本：召回 0.800 / 精确 0.800」，该结论样本量不足、偏乐观，
> 已作废。** 全量复测后指标明显下降，这才是真实水平。
> 完整分析（误报来源拆解、cam1 漏检的逐帧追踪、阈值扫描）见 `README-evaluation.md`。

复现（全量）：

```bash
backend/pose/.venv/Scripts/python.exe -X utf8 tmp/_urfd_download.py
backend/pose/.venv/Scripts/python.exe -X utf8 tmp/_urfd_eval.py --dir tmp/urfd
```

## 5. 已知边界（重要，不要过度表述）

- **单目姿态估计对机位角度敏感**：URFD 的 cam1 机位检出率仅 40%（cam0 为 63%），
  且 cam1 **从未捞回 cam0 漏掉的场景**。该角度下人体跌倒时躯干被遮挡/透视缩短，
  「横卧」几何判据不成立，只有坠落信号。**换机位必须做实际跌倒覆盖测试。**
- **「横卧」与「躺下休息」难区分**：全量评估剩余 7 条误报均属此类
  （对照 URFD 日常动作的「在床上躺下」环节）。居家场景中这更难。
- **样本代表性有限**：URFD 是近景手持/固定风格、片段 2–7 秒、受试者跌倒后迅速起身，
  与居家固定广角机位差异大。**上述指标只说明链路可用、无退化，
  不能外推为家用准确率。**
- **只处理画面内一个人**：取人体框面积最大者。多人场景会串。
- **URFD 是近景手持风格**，人跌倒时常冲出画面，所以阈值（下降速度 0.8 画面高/秒）
  偏高。换到固定广角吊装的居家机位必须重新标定 `calibrate.py`。
- **依赖画面质量**：遮挡严重、逆光、夜间无补光时关键点置信度不足，
  侧车会判定"画面内无有效人体"而不告警——这是保守设计，不会因此误报。
- **不处理视频流**：当前按视频文件或本机摄像头逐帧读。接入 RTSP 需要另加取流层。
- **不是医疗诊断**，也**不是经认证的人身安全系统**，只是辅助告警。
- `samples/` 中的 URFD 数据仅限学术研究使用，随侧车一起分发前需确认授权。
- `yolov8n-pose` 为 **AGPL-3.0**，对外分发前须处理。

## 6. 用法

首次安装（本机清华镜像源不可用，必须走官方 PyPI）：

```bash
"C:/Users/ASUS/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m venv backend/pose/.venv
backend/pose/.venv/Scripts/python.exe -m pip install -r backend/pose/requirements.txt -i https://pypi.org/simple
```

模型权重已随仓库放在 `models/yolov8n-pose.pt`；如需重新下载：

```bash
curl -L -o backend/pose/models/yolov8n-pose.pt \
  https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n-pose.pt
```

运行：

```bash
# 批量评测（不需要后端）
backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.run --eval backend/pose/samples

# 单视频，只检测不注入
backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.run --video xxx.mp4

# 单视频，命中后注入后端
backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.run \
  --video xxx.mp4 --inject --elder-id <老人ID> --password <后端口令>

# 本机摄像头实时
backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.run \
  --camera 0 --inject --elder-id <老人ID> --password <后端口令>

# 阈值标定（换了机位必做）
backend/pose/.venv/Scripts/python.exe -X utf8 backend/pose/calibrate.py

# 机位标定覆盖项（不必改源码即可应用本场地标定值）
backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.run \
  --video xxx.mp4 --torso-angle 52 --confirm-frames 5 --json out.json

# 状态机单测（快，不加载模型）
backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.test_detector
```

机位标定覆盖参数（详见 [`doc/03-子文档/36-...`](../../doc/03-子文档/36-吊装机位跌倒检出标定结论.md)）：

| 参数 | 作用 |
|---|---|
| `--torso-angle` | 横卧角度阈值（度）。侧视 62；吊装/高仰角建议实测标定 |
| `--confirm-frames` | 横卧连续确认帧数 |
| `--descent-speed` | 鼻部下降速度阈值（画面高/秒） |
| `--angular-velocity` | 躯干角速度阈值（度/秒） |
| `--exit-fall` | 启用「坠落并沉出画面」判据（默认关闭，全量评估为净负，慎用） |

`--json` 输出会附带本次**实际生效**的整套阈值，便于事后核对用的是哪套标定。

环境变量：`LAOYOU_API_BASE` / `LAOYOU_USER` / `LAOYOU_PASSWORD` / `LAOYOU_ELDER_ID`

本地无 PostgreSQL 时，可用项目支持的 SQLite 预览模式起后端做联调：

```bash
export DATABASE_URL='sqlite:///D:/实验室项目/LaoYou_ChuangXin_Agent/tmp/preview.db'
backend/.venv/Scripts/python.exe -X utf8 -m uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 18080
```

演示账号 `admin` / `Laoyou123!`。

## 7. 后端接口约束（踩过的坑）

- `source` **不能**以 `live` / `live_` 开头，后端直接 422。
  本侧车用 `camera_pose_v1`。
- `source` 以 `camera` 开头时，要求 `elder.camera_enabled` 为真，否则 409。
- 只有 `child` / `admin` 角色能注入观测。
- `idempotency_key` 每次告警都换新值，否则重复注入会被去重掉、不产生新事件。

## 8. 许可与出处

- 姿态模型：Ultralytics YOLOv8n-pose，**AGPL-3.0**。用于对外分发/商用前必须确认合规。
- 判定逻辑思路参考了若干公开项目（omrode1/Person-falling-Pose 的角度阈值、
  AwaisShah75 的 v_y 与 Δθ/Δt 形态），本项目代码为独立实现，未复制其源码。
- 数据集：University of Rzeszow Fall Detection (URFD)，学术研究用途。
