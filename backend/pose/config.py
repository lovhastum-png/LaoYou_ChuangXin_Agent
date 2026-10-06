"""侧车配置。

所有阈值集中在 dataclass，便于按场地标定；默认值取自公开跌倒检测项目的常用经验值，
不是经临床验证的医学参数，也不构成医疗诊断。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class DetectionConfig:
    # ---- 模型 ----
    model_path: str = os.path.join(os.path.dirname(__file__), "models", "yolov8n-pose.pt")
    imgsz: int = 640
    conf: float = 0.35
    device: str = "cpu"

    # ---- 姿态几何阈值（度） ----
    # 躯干偏离竖直轴的夹角；超过此值认为身体已接近横卧。
    # 实测（URFD + YOLOv8n-pose）：直立 0-12°，跌倒横卧 68-90°，且已对
    # 关键点跳变做过钳制，不会出现 >90° 的假信号。取 62 留出安全边际。
    torso_angle_threshold: float = 62.0

    # ---- 垂直下降运动学（以画面高度为单位） ----
    # 说明：URFD 是手持/固定近景，跌倒瞬间人几乎冲出画面，下降速度普遍
    # 超过 1 画面高/秒；而画面内正常走动只有 0.05-0.2。因此把阈值放在
    # 0.8，既能抓住真实坠落，又不会被走动触发。
    descent_speed_threshold: float = 0.8
    # 躯干角速度阈值（度/秒）。实测跌倒瞬间普遍 >1400°/秒，走动时 <300。
    angular_velocity_threshold: float = 600.0

    # ---- 时序约束（抑制误报的关键） ----
    # 判定为落定前，需连续处于横卧姿态的帧数。
    # 取 5 帧（约 0.17 秒 @30fps）。
    #
    # 这个值在 URFD 全量（100 分卷 / 70 个唯一场景）上扫过一遍：
    #   3 帧 → 召回 0.733  精确 0.550  F1 0.629   （原值）
    #   5 帧 → 召回 0.700  精确 0.724  F1 0.712   ← 采用
    #   8 帧 → 召回 0.567  精确 0.739  F1 0.642
    # 3→5 帧把误报从 18 条砍到 8 条，召回只掉 0.033，是性价比最高的一档。
    # 再往上精确率几乎不动、召回继续掉，不划算。
    #
    # 注意：**没有靠提高 torso_angle_threshold 来降误报**。扫描显示
    # 角度阈值从 62° 升到 85° 时召回掉得比误报快（85°+3 帧时召回只剩 0.333），
    # 说明 62° 这个几何判据本身是合适的，误报主因是「确认时长太短」而非「角度太松」。
    lying_confirm_frames: int = 5
    # 横卧需持续的秒数下限。默认 0.0，表示只靠帧数确认。
    # 实测 URFD 受试者跌倒后迅速起身，最长横卧仅 0.5-0.7 秒，
    # 任何大于 0.5 秒的窗口都会造成大量漏检，因此不再用它抑制误报。
    immobility_seconds: float = 0.0
    # 静止判定的位移上限（画面高 / 秒）
    immobility_motion_threshold: float = 0.15
    # 是否要求「先观察到快速坠落」才允许告警。
    # True：仅慢速躺下/坐下不会告警，这是本基线精确度的主要来源。
    require_falling_evidence: bool = True
    # 允许在横卧开始前回溯多少秒内的坠落证据（秒）
    falling_evidence_window: float = 2.0
    # 是否额外要求横卧期间髋部基本不动。默认关闭：
    # 起身动作本身产生位移，开启会明显降低召回，仅用于误报过高的场地。
    enforce_rest_check: bool = False

    # ---- 「坠落并沉出画面」补充判据 ----
    # 背景：当老人朝镜头方向扑倒时（尤其平视/低机位的近景机位），身体会很快
    # 从画面下沿消失；躯干在消失前根本来不及转到 62°，于是 lying 判据永不成立，
    # 状态机卡在「只有坠落信号、没有横卧」这一档，最终漏报。
    # 实测（tmp/fall-verify，1280x720 走廊、平视跟拍、5.43s）：
    #   下降速度连续 5 帧越过 0.8 阈值（峰值 0.978 画面高/秒），
    #   但躯干最大只有 21.1°，紧接着人整体沉出画面下沿 → 0 告警。
    # 因此增加一条等价判据：消失前最后一帧的髋部已抵近画面下沿，且此前
    # falling_evidence_window 秒内出现过坠落证据，即判为跌倒。
    #
    # 默认 False：这条判据与机位强相关，开启前必须在本场地跑一遍
    # 日常动作视频（走动出画、快速下蹲、坐低凳）做误报验证。
    detect_frame_exit_fall: bool = False
    # 髋部归一化纵坐标达到该值即视为「已抵近画面下沿」（0=顶部，1=底边）。
    exit_frame_hip_threshold: float = 0.95
    # 同一老人告警的最短间隔（秒），防止重复上报
    cooldown_seconds: float = 30.0
    # 滚动窗口长度（帧），用于计算速度
    window_frames: int = 15

    # ---- 帧采样 ----
    # 每 N 帧推理一次（1 = 每帧）。CPU 上建议 2-3
    frame_stride: int = 1
    # 送入推理前的最大宽度
    resize_width: int = 960

    # ---- 有效性门槛 ----
    # 单帧内至少要有这么多个高置信关键点，才认为人体可用
    min_valid_keypoints: int = 8

    # ---- COCO 17 关键点索引 ----
    NOSE: int = 0
    L_SHOULDER: int = 5
    R_SHOULDER: int = 6
    L_HIP: int = 11
    R_HIP: int = 12

    kpt_conf_threshold: float = 0.30

    def as_dict(self) -> dict:
        return {
            k: v
            for k, v in self.__dict__.items()
            if not k.isupper() and not callable(v)
        }


@dataclass
class PresenceConfig:
    """平安看护阈值。

    范围刻意做小：摄像头能看见老人就算平安。
    只有「持续看不到人」或「画面内长时间不动」才提示家属。

    注意这些是演示用可配置规则，不是医疗或看护专业判定。
    """

    # 连续多少秒检测不到人体，才算「老人不在画面内」。
    # 不能太小：老人转身、被家具或门框挡住一两秒是常态。
    # 取 60 秒，允许正常的遮挡与短暂离开。
    missing_grace_seconds: float = 60.0
    # 画面内多久没有明显动作，才算「长时间不动」。
    # 取 30 分钟；设为 0 可完全关闭这项判定。
    still_seconds: float = 1800.0
    # 摔倒判定后保持多久才允许回到平安状态（秒）。
    # 跌倒检测是单帧触发，而老人摔倒后通常还躺在画面里、甚至继续不动，
    # 若不保持，下一帧就会因为「人在画面内」被判回平安（实际踩过）。
    # 取 120 秒，给家属留出确认时间；角色确认后是另一条链路的事。
    fall_hold_seconds: float = 120.0
    # 判定为「有动作」的髋部位移阈值（画面高/秒）。
    # 与跌倒检测里的静止判定保持一致。
    still_motion_threshold: float = 0.01
    # 上报节流：同一风险再次上报的最小间隔（秒）。
    # 状态没变化时不重复上报，这里只兜底极长的持续风险。
    repeat_report_seconds: float = 0.0

    # ---- 帧采样 ----
    # 平安看护不需要高帧率，CPU 上每 5 帧推理一次足够，开销约为跌倒检测的 1/5。
    frame_stride: int = 5
    resize_width: int = 960

    # ---- 判定为「确实有人」的质量门槛 ----
    # 实测（URFD 素材 + yolov8n-pose）：
    #   真人帧    框置信度 0.86-0.88，关键点平均置信度 0.91-0.95
    #   静态背景  框置信度 0.67-0.72，关键点平均置信度 0.50-0.51
    # 误检的框置信度与真人接近，但关键点平均置信度差了近一倍，
    # 因此以「关键点平均置信度」作为主判据，另两项做兜底。
    # 不加这道门槛会发生什么：静态背景被当成人体，
    # 「老人离画」永远判不出来（实测踩过这个坑）。
    min_box_conf: float = 0.50
    min_kpt_mean_conf: float = 0.70
    min_box_area_ratio: float = 0.004

    model_path: str = os.path.join(os.path.dirname(__file__), "models", "yolov8n-pose.pt")
    imgsz: int = 640
    conf: float = 0.35
    device: str = "cpu"


@dataclass
class ClientConfig:
    """后端注入客户端配置。"""

    base_url: str = field(
        default_factory=lambda: os.environ.get("LAOYOU_API_BASE", "http://127.0.0.1:18080/api")
    )
    username: str = field(default_factory=lambda: os.environ.get("LAOYOU_USER", "admin"))
    password: str = field(default_factory=lambda: os.environ.get("LAOYOU_PASSWORD", ""))
    elder_id: str = field(default_factory=lambda: os.environ.get("LAOYOU_ELDER_ID", ""))
    # 上报来源标签。后端拒绝 live / live_ 前缀；camera 前缀要求老人端摄像头为开启。
    source: str = "camera_pose_v1"
    capture_dir: str = field(
        default_factory=lambda: os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "runtime",
            "pose-captures",
        )
    )


DEFAULT_DETECTION = DetectionConfig()
DEFAULT_PRESENCE = PresenceConfig()
DEFAULT_CLIENT = ClientConfig()
