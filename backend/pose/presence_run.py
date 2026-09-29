"""平安看护入口：摄像头看得见老人就是平安。

用法示例：

  # 跑一段视频，打印状态变化（不上报）
  python -m backend.pose.presence_run --video x.mp4

  # 接本机摄像头，真实上报到后端
  python -m backend.pose.presence_run --camera 0 --inject \
      --elder-id <id> --password <pwd>

  # 用便于演示的短阈值跑（60 秒看不到人就算离画 -> 6 秒）
  python -m backend.pose.presence_run --video x.mp4 --missing-grace 6

上报的观测：
    kind=activity，value 里带 state 与中文描述。
    为兼容后端 location 校验（必须含 latitude/longitude），
    同时带上标记为演示占位的坐标，并在日志里说明，不伪造成真实定位。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

from .config import DEFAULT_CLIENT, DEFAULT_PRESENCE, ClientConfig, DetectionConfig, PresenceConfig
from .detector import FallDetector, State as FallState, extract_features
from .presence import (
    PresenceMonitor,
    PresenceState,
    describe,
    feature_from_frame,
    safe_label,
)

# 平安看护复用跌倒检测的几何阈值（关键点置信度、最小关键点数等）
GEOMETRY_FALLBACK = DetectionConfig()


def _load_model(cfg: PresenceConfig):
    from ultralytics import YOLO

    if not os.path.exists(cfg.model_path):
        raise FileNotFoundError(
            f"未找到姿态模型：{cfg.model_path}\n"
            "请先下载：curl -L -o backend/pose/models/yolov8n-pose.pt "
            "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n-pose.pt"
        )
    return YOLO(cfg.model_path)


def _pick_primary(result, cfg: PresenceConfig):
    """居家单老人场景：挑出「确实是个人」的那一个。

    返回 (关键点, 框中心归一化坐标, 质量明细)。

    这里不只是「有人形就认」，还要求置信度与关键点质量达标。
    原因：静态背景（家具、光照纹理）会被模型误检成人形，
    实测误检的平均关键点置信度只有约 0.50，而真人在 0.91 以上。
    不加这道门槛，「老人离画」会被误检一直顶住，永远判不出来。
    """
    empty = (None, None, {"reason": "no_detection"})
    if result.keypoints is None or result.keypoints.data is None:
        return empty
    data = result.keypoints.data.cpu().numpy()
    if len(data) == 0:
        return empty

    h, w = result.orig_shape
    boxes = result.boxes.xyxy.cpu().numpy() if result.boxes is not None else None
    confs = result.boxes.conf.cpu().numpy() if result.boxes is not None else None

    if boxes is None or len(boxes) == 0:
        return empty

    # 逐个候选套质量门槛，取面积最大的合格者
    candidates = []
    for i in range(len(data)):
        box = boxes[i]
        box_conf = float(confs[i]) if confs is not None else 0.0
        area_ratio = float(
            (box[2] - box[0]) * (box[3] - box[1]) / max(w * h, 1)
        )
        kpt_mean = float(data[i][:, 2].mean())
        if (
            box_conf >= cfg.min_box_conf
            and kpt_mean >= cfg.min_kpt_mean_conf
            and area_ratio >= cfg.min_box_area_ratio
        ):
            candidates.append((area_ratio, i, box_conf, kpt_mean))

    if not candidates:
        # 有检出但都被质量门槛挡下：记录最佳候选，便于排查是不是真人被误挡
        best = int(np.argmax(
            (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        ))
        return (
            None,
            None,
            {
                "reason": "below_quality_threshold",
                "box_conf": round(float(confs[best]) if confs is not None else 0.0, 3),
                "kpt_mean_conf": round(float(data[best][:, 2].mean()), 3),
            },
        )

    candidates.sort(reverse=True)
    area_ratio, i, box_conf, kpt_mean = candidates[0]
    box = boxes[i]
    center = (
        float((box[0] + box[2]) / 2 / max(w, 1)),
        float((box[1] + box[3]) / 2 / max(h, 1)),
    )
    return data[i], center, {
        "reason": "ok",
        "box_conf": round(box_conf, 3),
        "kpt_mean_conf": round(kpt_mean, 3),
        "area_ratio": round(area_ratio, 4),
    }


def _pick_for_fall(result, cfg: PresenceConfig):
    """给摔倒检测挑人：只按面积取最大，不套质量门槛。

    为什么不套门槛：人摔倒时身体横躺、姿态剧烈变化，模型的关键点置信度
    会掉到 0.70 以下；而质量门槛是为了挡「静态背景被误检成人形」设的，
    一视同仁地套上去会把正在摔倒的真人一起挡掉。
    实测：套门槛后 fall-01 在 frame=105-120（正是摔倒落地的几帧）
    全部被判为「画面内无有效人体」，摔倒直接漏检。

    两个判定各用各的取舍：
        摔倒检测   宁可误报，不可漏报 -> 宽松
        平安看护   宁可漏检，不可误报 -> 严格（防背景误检）
    """
    if result.keypoints is None or result.keypoints.data is None:
        return None
    data = result.keypoints.data.cpu().numpy()
    if len(data) == 0:
        return None
    if result.boxes is not None and len(result.boxes) > 0:
        boxes = result.boxes.xyxy.cpu().numpy()
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        return data[int(np.argmax(areas))]
    return data[0]


class PresenceRunner:
    """把视频/摄像头帧流接到平安看护状态机与后端。

    同时跑两个判定：
      - PresenceMonitor  老人在不在画面里
      - FallDetector     老人有没有摔倒
    两者结论汇总成一个平安状态。摔倒优先级最高。
    """

    def __init__(
        self,
        *,
        presence_cfg: PresenceConfig | None = None,
        client_cfg: ClientConfig | None = None,
        inject: bool = False,
        verbose: bool = True,
        detect_fall: bool = True,
    ):
        self.cfg = presence_cfg or DEFAULT_PRESENCE
        self.client_cfg = client_cfg or DEFAULT_CLIENT
        self.inject = inject
        self.verbose = verbose
        self.detect_fall = detect_fall
        self.model = _load_model(self.cfg)
        self._client = None
        self.timeline: list[dict] = []
        # 跌倒检测用独立的几何阈值配置。
        # 注意 stride：平安看护本身不关心高帧率，但跌倒瞬间只持续几帧，
        # 跳帧会直接跳过坠落那一瞬（实测 stride=5 时跌倒全部漏检）。
        # 所以只要开启摔倒识别，就必须逐帧推理。
        self.fall_cfg = DetectionConfig()
        if detect_fall and self.cfg.frame_stride != 1:
            self._log(
                f"  [!] 已开启摔倒识别，帧采样自动改为逐帧"
                f"（原 stride={self.cfg.frame_stride}；跳帧会漏掉坠落瞬间）"
            )
            self.cfg.frame_stride = 1
        self.fall_cfg.frame_stride = 1

    def _get_client(self):
        if self._client is None:
            from .client import ObservationClient

            self._client = ObservationClient(self.client_cfg)
        return self._client

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    # ------------------------------------------------------------------ #
    def _build_value(self, state: PresenceState, detail: dict) -> dict:
        """构造观测 value。"""
        return {
            "state": state.value,
            "label": describe(state),
            "safe": safe_label(state) == "平安",
            "detail": detail,
        }

    def _report(self, state: PresenceState, reason: str, detail: dict) -> None:
        """把状态变化上报到后端。

        摔倒要发两条，因为它们在后端是两种不同的东西：
          - kind=activity  看护状态记录，家属端能看到「检测到老人摔倒」
          - kind=fall      异常事件，走 rules.py 产生 critical 告警并通知子女与社区
        只发 activity 会出现「系统知道老人摔了，但没人被通知」；
        只发 fall 则家属端的看护状态里看不到这次摔倒。
        """
        if not self.inject:
            self._log("          [未上报] 需要 --inject 才写入后端")
            return
        client = self._get_client()
        try:
            resp = client.inject_observation(
                kind="activity",
                value=self._build_value(state, detail),
            )
            events = (resp or {}).get("events", [])
            self._log(
                f"          已上报看护状态 {(resp or {}).get('observation', {}).get('id')}"
                f"，生成事件 {[e['id'] for e in events]}"
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"          [!] 上报看护状态失败：{exc}")

        # 摔倒额外发一条 fall，触发后端的告警链路
        if state is PresenceState.FALLEN:
            try:
                resp = client.inject_fall(
                    extra_value={
                        "state": state.value,
                        "label": describe(state),
                        "detail": detail,
                    }
                )
                events = (resp or {}).get("events", [])
                self._log(
                    f"          已上报跌倒事件"
                    f" {(resp or {}).get('observation', {}).get('id')}"
                    f"，生成告警 {[(e.get('kind'), e.get('severity')) for e in events]}"
                )
            except Exception as exc:  # noqa: BLE001
                self._log(f"          [!] 上报跌倒事件失败：{exc}")

    # ------------------------------------------------------------------ #
    def process_frame(
        self,
        frame,
        monitor: PresenceMonitor,
        index: int,
        timestamp: float,
        fall_detector: "FallDetector | None" = None,
    ):
        h, w = frame.shape[:2]
        if w > self.cfg.resize_width:
            scale = self.cfg.resize_width / w
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            h, w = frame.shape[:2]

        results = self.model.predict(
            frame,
            imgsz=self.cfg.imgsz,
            conf=self.cfg.conf,
            device=self.cfg.device,
            verbose=False,
        )
        # 平安看护：严格取人（带质量门槛，防背景误检）
        kpts, center, quality = _pick_primary(results[0], self.cfg)
        frame_feat = extract_features(
            kpts,
            width=w,
            height=h,
            index=index,
            timestamp=timestamp,
            cfg=GEOMETRY_FALLBACK,
        )

        # 摔倒判定：宽松取人（不套门槛，否则摔倒中的真人会被挡掉），
        # 复用同一个模型结果，不额外推理。
        fallen = False
        fall_reason = ""
        fall_detail: dict = {}
        fall_state = None
        if fall_detector is not None:
            fall_kpts = _pick_for_fall(results[0], self.cfg)
            fall_feat = extract_features(
                fall_kpts,
                width=w,
                height=h,
                index=index,
                timestamp=timestamp,
                cfg=GEOMETRY_FALLBACK,
            )
            fall_res = fall_detector.feed(fall_feat)
            fall_state = fall_res.state.value
            if fall_res.triggered:
                fallen = True
                fall_reason = fall_res.reason
                fall_detail = dict(fall_res.detail or {})
                self._log(
                    f"\n  [摔倒] frame={index} t={timestamp:.1f}s :: {fall_res.reason}"
                )
                self._log(f"          {fall_detail}")

        pfeat = feature_from_frame(frame_feat, center)
        pfeat.fallen = fallen
        pfeat.fallen_reason = fall_reason
        pfeat.fall_detail = fall_detail
        result = monitor.feed(pfeat)

        record = {
            "index": index,
            "timestamp": round(timestamp, 3),
            "state": result.state.value,
            "safe": result.safe,
            "present": bool(frame_feat.present),
            "motion": round(frame_feat.motion, 4),
            "quality": quality,
            "fall_state": fall_state,
            "reason": result.reason,
            "reported": result.report,
        }
        self.timeline.append(record)

        if result.report:
            tag = "平安" if result.safe else "注意"
            self._log(
                f"\n  [{tag}] frame={index} t={timestamp:.1f}s "
                f"{result.state.value} :: {result.reason}"
            )
            self._log(f"          {result.detail}")
            self._report(result.state, result.reason, result.detail)
        elif self.verbose and index % 150 == 0:
            self._log(
                f"  frame={index} t={timestamp:.1f}s state={result.state.value}"
                f" present={frame_feat.present} kpt_mean={quality.get('kpt_mean_conf')}"
                f" ({quality.get('reason')})"
            )
        return result

    # ------------------------------------------------------------------ #
    def run_video(self, video_path: str, max_frames: int | None = None) -> list:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频：{video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._log(
            f"\n=== {os.path.basename(video_path)}  {total} 帧 @ {fps:.1f}fps ==="
        )

        monitor = PresenceMonitor(self.cfg)
        fall_detector = FallDetector(self.fall_cfg) if self.detect_fall else None
        changes, idx = [], 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % self.cfg.frame_stride != 0:
                idx += 1
                continue
            res = self.process_frame(
                frame, monitor, idx, idx / fps, fall_detector
            )
            if res.report:
                changes.append((idx / fps, res))
            idx += 1
            if max_frames and idx >= max_frames:
                break
        cap.release()
        return changes

    def run_camera(self, cam_index: int, seconds: float | None = None) -> list:
        cap = cv2.VideoCapture(cam_index)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开摄像头 {cam_index}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        self._log(f"\n=== 摄像头 {cam_index} @ {fps:.1f}fps（Ctrl+C 停止）===")

        monitor = PresenceMonitor(self.cfg)
        fall_detector = FallDetector(self.fall_cfg) if self.detect_fall else None
        changes, idx, t0 = [], 0, time.time()
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                elapsed = time.time() - t0
                if idx % self.cfg.frame_stride != 0:
                    idx += 1
                    continue
                res = self.process_frame(frame, monitor, idx, elapsed, fall_detector)
                if res.report:
                    changes.append((elapsed, res))
                idx += 1
                if seconds and elapsed >= seconds:
                    break
        except KeyboardInterrupt:
            self._log("\n  已手动停止")
        finally:
            cap.release()
        return changes


# ---------------------------------------------------------------------- #
def _summarize(runner: PresenceRunner) -> dict:
    """把逐帧记录压缩成「状态时间段」汇总。"""
    segments: list[dict] = []
    for row in runner.timeline:
        if segments and segments[-1]["state"] == row["state"]:
            segments[-1]["until"] = row["timestamp"]
            segments[-1]["frames"] += 1
        else:
            segments.append(
                {
                    "state": row["state"],
                    "from": row["timestamp"],
                    "until": row["timestamp"],
                    "frames": 1,
                }
            )
    for seg in segments:
        seg["seconds"] = round(seg["until"] - seg["from"], 1)
    return {"segments": segments, "frames": len(runner.timeline)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="老友 — 平安看护（摄像头可见即平安）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="输入视频文件路径")
    src.add_argument("--camera", type=int, help="摄像头序号，如 0")

    ap.add_argument("--inject", action="store_true", help="状态变化时上报后端")
    ap.add_argument("--elder-id", default=None, help="老人 ID")
    ap.add_argument("--password", default=None, help="后端登录密码")
    ap.add_argument("--username", default=None, help="后端登录用户名（默认 admin）")
    ap.add_argument("--base-url", default=None, help="后端 API 基址")

    ap.add_argument(
        "--missing-grace",
        type=float,
        default=None,
        help="连续多少秒看不到人算「离画」（默认 60，演示可用 6）",
    )
    ap.add_argument(
        "--still-seconds",
        type=float,
        default=None,
        help="画面内多久无动作算「长时间不动」（默认 1800，设 0 关闭）",
    )
    ap.add_argument("--stride", type=int, default=None, help="每 N 帧推理一次（开启摔倒识别时强制为 1）")
    ap.add_argument("--max-frames", type=int, default=None, help="最多处理帧数")
    ap.add_argument("--no-fall", action="store_true", help="关闭摔倒识别，只看在不在画面里")
    ap.add_argument("--quiet", action="store_true", help="只输出状态变化")
    ap.add_argument("--json", default=None, help="把结果写入 JSON 文件")
    args = ap.parse_args(argv)

    cfg = PresenceConfig()
    cli_cfg = ClientConfig()
    if args.missing_grace is not None:
        cfg.missing_grace_seconds = args.missing_grace
    if args.still_seconds is not None:
        cfg.still_seconds = args.still_seconds
    if args.stride:
        cfg.frame_stride = args.stride
    if args.elder_id:
        cli_cfg.elder_id = args.elder_id
    if args.password:
        cli_cfg.password = args.password
    if args.username:
        cli_cfg.username = args.username
    if args.base_url:
        cli_cfg.base_url = args.base_url

    if args.inject:
        if not cli_cfg.password:
            print("[!] --inject 需要 --password 或环境变量 LAOYOU_PASSWORD", file=sys.stderr)
            return 2
        if not cli_cfg.elder_id:
            print("[!] --inject 需要 --elder-id 或环境变量 LAOYOU_ELDER_ID", file=sys.stderr)
            return 2

    runner = PresenceRunner(
        presence_cfg=cfg,
        client_cfg=cli_cfg,
        inject=args.inject,
        verbose=not args.quiet,
        detect_fall=not args.no_fall,
    )

    runner._log(
        f"  配置：看不见人 {cfg.missing_grace_seconds:.0f}s 判离画；"
        f"画面内无动作 {cfg.still_seconds:.0f}s 判长时间不动；"
        f"摔倒识别{'已开启' if not args.no_fall else '已关闭'}；"
        f"每 {cfg.frame_stride} 帧推理一次"
    )

    if args.video:
        changes = runner.run_video(args.video, max_frames=args.max_frames)
    else:
        changes = runner.run_camera(args.camera)

    summary = _summarize(runner)
    summary["video"] = args.video or f"camera:{args.camera}"
    summary["changes"] = [
        {"at": round(at, 2), "state": c.state.value, "reason": c.reason}
        for at, c in changes
    ]

    print("\n=== 状态时间段 ===")
    for seg in summary["segments"]:
        print(
            f"  {seg['from']:>7.1f}s - {seg['until']:>7.1f}s  "
            f"{seg['seconds']:>7.1f}s  {describe(PresenceState(seg['state']))}"
        )
    print(f"\n  共 {len(summary['segments'])} 段，状态变化 {len(changes)} 次")

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        print(f"  结果已写入：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
