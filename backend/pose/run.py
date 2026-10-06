"""跌倒检测侧车入口。

用法示例：

  # 跑一段视频，打印每帧判定，命中后调用后端注入 fall 观测
  python -m backend.pose.run --video backend/pose/samples/fall-01-cam0.mp4 --dry-run

  # 真实注入（需要后端在运行 + 提供密码）
  python -m backend.pose.run --video x.mp4 --elder-id <id> --password <pwd> --inject

  # 只做批量评测，统计召回/误报
  python -m backend.pose.run --eval backend/pose/samples

  # 接本机摄像头
  python -m backend.pose.run --camera 0 --inject --elder-id <id> --password <pwd>
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

from .config import DEFAULT_CLIENT, DEFAULT_DETECTION, ClientConfig, DetectionConfig
from .detector import FallDetector, State, extract_features


def _load_model(cfg: DetectionConfig):
    from ultralytics import YOLO

    if not os.path.exists(cfg.model_path):
        raise FileNotFoundError(
            f"未找到姿态模型：{cfg.model_path}\n"
            "请先下载：curl -L -o backend/pose/models/yolov8n-pose.pt "
            "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n-pose.pt"
        )
    return YOLO(cfg.model_path)


def _pick_primary(result, cfg: DetectionConfig) -> np.ndarray | None:
    """居家场景取主位目标：优先画面中心附近、面积最大的人。"""
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


class PoseRunner:
    """把视频/摄像头帧流接到检测器与后端。"""

    def __init__(
        self,
        *,
        det_cfg: DetectionConfig | None = None,
        client_cfg: ClientConfig | None = None,
        inject: bool = False,
        save_capture: bool = True,
        verbose: bool = True,
    ):
        self.det_cfg = det_cfg or DEFAULT_DETECTION
        self.client_cfg = client_cfg or DEFAULT_CLIENT
        self.inject = inject
        self.save_capture = save_capture
        self.verbose = verbose
        self.model = _load_model(self.det_cfg)
        self._client = None

    # ------------------------------------------------------------------ #
    def _get_client(self):
        if self._client is None:
            from .client import ObservationClient

            self._client = ObservationClient(self.client_cfg)
        return self._client

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    # ------------------------------------------------------------------ #
    def process_frame(
        self, frame: np.ndarray, detector: FallDetector, index: int, timestamp: float
    ):
        h, w = frame.shape[:2]
        # 缩放以控制 CPU 推理开销
        if w > self.det_cfg.resize_width:
            scale = self.det_cfg.resize_width / w
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            h, w = frame.shape[:2]

        results = self.model.predict(
            frame,
            imgsz=self.det_cfg.imgsz,
            conf=self.det_cfg.conf,
            device=self.det_cfg.device,
            verbose=False,
        )
        kpts = _pick_primary(results[0], self.det_cfg)
        feat = extract_features(
            kpts, width=w, height=h, index=index, timestamp=timestamp, cfg=self.det_cfg
        )
        result = detector.feed(feat)

        if result.triggered:
            self._log(
                f"  [ALARM] frame={index} t={timestamp:.2f}s state={result.state.value}"
                f" :: {result.reason}"
            )
            self._log(f"          {result.detail}")
            if self.save_capture:
                ok, buf = cv2.imencode(".jpg", frame)
                if ok:
                    path = self._get_client().save_capture(buf.tobytes(), timestamp)
                    self._log(f"          快照已保存：{path}")
            if self.inject:
                try:
                    resp = self._get_client().inject_fall()
                    events = resp.get("events", [])
                    self._log(
                        f"          已注入观测 {resp['observation']['id']}，"
                        f"生成事件 {[e['id'] for e in events]}"
                    )
                except Exception as exc:  # noqa: BLE001
                    self._log(f"          [!] 注入失败：{exc}")
        elif self.verbose and index % 30 == 0:
            self._log(
                f"  frame={index} t={timestamp:.2f}s state={result.state.value}"
                f" angle={feat.torso_angle:.0f}° vr={feat.vertical_ratio:.2f}"
                f" v={feat.descent_speed:.2f} w={feat.angular_velocity:.0f}"
            )
        return result

    # ------------------------------------------------------------------ #
    def run_video(self, video_path: str, max_frames: int | None = None) -> list:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开视频：{video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._log(f"\n=== {os.path.basename(video_path)}  {total} 帧 @ {fps:.1f}fps ===")

        detector = FallDetector(self.det_cfg)
        alarms, idx = [], 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % self.det_cfg.frame_stride != 0:
                idx += 1
                continue
            ts = idx / fps
            res = self.process_frame(frame, detector, idx, ts)
            if res.triggered:
                alarms.append(res)
            idx += 1
            if max_frames and idx >= max_frames:
                break
        cap.release()
        return alarms

    def run_camera(self, cam_index: int, seconds: float | None = None) -> list:
        cap = cv2.VideoCapture(cam_index)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开摄像头 {cam_index}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        self._log(f"\n=== 摄像头 {cam_index} @ {fps:.1f}fps（Ctrl+C 停止）===")

        detector = FallDetector(self.det_cfg)
        alarms, idx, t0 = [], 0, time.time()
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                elapsed = time.time() - t0
                res = self.process_frame(frame, detector, idx, elapsed)
                if res.triggered:
                    alarms.append(res)
                idx += 1
                if seconds and elapsed >= seconds:
                    break
        except KeyboardInterrupt:
            self._log("\n  已手动停止")
        finally:
            cap.release()
        return alarms


# ---------------------------------------------------------------------- #
def _eval_dataset(runner: PoseRunner, directory: str) -> dict:
    """对 URFD 风格目录做批量评测：文件名 fall-* 视为正样本，adl-* 视为负样本。"""
    videos = sorted(glob.glob(os.path.join(directory, "*.mp4")))
    if not videos:
        print(f"目录内没有 mp4：{directory}")
        return {}

    tp = fp = fn = tn = 0
    rows = []
    for path in videos:
        name = os.path.basename(path)
        if name.startswith("fall-"):
            truth = True
        elif name.startswith("adl-"):
            truth = False
        else:
            continue
        alarms = runner.run_video(path)
        got = len(alarms) > 0
        if truth and got:
            tp += 1
        elif truth and not got:
            fn += 1
        elif not truth and got:
            fp += 1
        else:
            tn += 1
        rows.append(
            {"video": name, "truth": "fall" if truth else "adl", "alarms": len(alarms)}
        )

    total = tp + fp + fn + tn
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    accuracy = (tp + tn) / total if total else 0.0
    summary = {
        "directory": directory,
        "clips": total,
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "TN": tn,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "accuracy": round(accuracy, 4),
        "rows": rows,
    }
    print("\n=== 评测汇总 ===")
    for r in rows:
        print(f"  {r['video']:<28} 真值={r['truth']:<5} 告警={r['alarms']}")
    print(
        f"\n  TP={tp} FP={fp} FN={fn} TN={tn}"
        f"  |  召回={recall:.3f}  精确={precision:.3f}  准确={accuracy:.3f}"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="老友 — 跌倒检测推理侧车（基线版）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="输入视频文件路径")
    src.add_argument("--camera", type=int, help="摄像头序号，如 0")
    src.add_argument("--eval", metavar="DIR", help="对目录内 URFD 风格视频批量评测")

    ap.add_argument("--inject", action="store_true", help="命中后调用后端注入 fall 观测")
    ap.add_argument("--dry-run", action="store_true", help="只检测不注入（默认行为）")
    ap.add_argument("--elder-id", default=None, help="老人 ID")
    ap.add_argument("--password", default=None, help="后端登录密码")
    ap.add_argument("--username", default=None, help="后端登录用户名（默认 admin）")
    ap.add_argument("--base-url", default=None, help="后端 API 基址")
    ap.add_argument("--source", default=None, help="上报来源标签（不得以 live 开头）")
    ap.add_argument("--max-frames", type=int, default=None, help="最多处理帧数")
    ap.add_argument("--stride", type=int, default=None, help="每 N 帧推理一次")

    # 机位标定覆盖项。README 要求「换机位必须重新标定」，标定结果需要能
    # 在不改源码的前提下落到运行时，否则只能改 config.py 默认值（会波及所有机位）。
    cal = ap.add_argument_group("机位标定覆盖（默认沿用 config.py）")
    cal.add_argument("--torso-angle", type=float, default=None,
                     help="横卧角度阈值（度）。侧视机位 62；吊装/高仰角机位建议实测标定")
    cal.add_argument("--confirm-frames", type=int, default=None,
                     help="横卧连续确认帧数")
    cal.add_argument("--descent-speed", type=float, default=None,
                     help="鼻部下降速度阈值（画面高/秒）")
    cal.add_argument("--angular-velocity", type=float, default=None,
                     help="躯干角速度阈值（度/秒）")
    cal.add_argument("--exit-fall", action="store_true",
                     help="启用「坠落并沉出画面」判据（默认关闭；URFD 全量评估为净负，慎用）")

    ap.add_argument("--quiet", action="store_true", help="只输出告警")
    ap.add_argument("--json", default=None, help="把结果写入 JSON 文件")
    args = ap.parse_args(argv)

    det_cfg = DetectionConfig()
    cli_cfg = ClientConfig()
    if args.stride:
        det_cfg.frame_stride = args.stride
    if args.torso_angle is not None:
        det_cfg.torso_angle_threshold = args.torso_angle
    if args.confirm_frames is not None:
        det_cfg.lying_confirm_frames = args.confirm_frames
    if args.descent_speed is not None:
        det_cfg.descent_speed_threshold = args.descent_speed
    if args.angular_velocity is not None:
        det_cfg.angular_velocity_threshold = args.angular_velocity
    if args.exit_fall:
        det_cfg.detect_frame_exit_fall = True
    if args.elder_id:
        cli_cfg.elder_id = args.elder_id
    if args.password:
        cli_cfg.password = args.password
    if args.username:
        cli_cfg.username = args.username
    if args.base_url:
        cli_cfg.base_url = args.base_url
    if args.source:
        cli_cfg.source = args.source
    if args.inject:
        if not cli_cfg.password:
            print("[!] --inject 需要 --password 或环境变量 LAOYOU_PASSWORD", file=sys.stderr)
            return 2
        if not cli_cfg.elder_id:
            print("[!] --inject 需要 --elder-id 或环境变量 LAOYOU_ELDER_ID", file=sys.stderr)
            return 2

    runner = PoseRunner(
        det_cfg=det_cfg,
        client_cfg=cli_cfg,
        inject=args.inject and not args.dry_run,
        verbose=not args.quiet,
    )

    if args.eval:
        summary = _eval_dataset(runner, args.eval)
    elif args.video:
        alarms = runner.run_video(args.video, max_frames=args.max_frames)
        summary = {
            "video": args.video,
            "alarms": len(alarms),
            "first_alarm_seconds": round(alarms[0].features.timestamp, 2) if alarms else None,
        }
        print(f"\n  告警次数：{len(alarms)}")
    else:
        alarms = runner.run_camera(args.camera)
        summary = {"camera": args.camera, "alarms": len(alarms)}
        print(f"\n  告警次数：{len(alarms)}")

    if args.json:
        # 一并记录本次实际生效的阈值，便于事后核对用的是哪套标定
        if isinstance(summary, dict):
            summary["detection_config"] = det_cfg.as_dict()
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        print(f"  结果已写入：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
