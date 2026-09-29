"""URFD 全量下载器。

用法：
    backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.dataset_download

先跑 `tmp/_urfd_available.txt` 里列出的分卷（该清单由人工/脚本探测生成），
逐个下载并当场用 OpenCV 解码校验，坏文件删掉重下。

用 Python 而非 shell 循环，避免两个经典坑：
  1. `while read` + curl 会让 curl 吃掉循环的 stdin
     （症状是全部报 `code=000`，看着像网络问题，其实是 shell 语义问题）；
  2. shell 里判断「已存在则跳过」容易写错，导致重复下载。
"""
from __future__ import annotations

import os
import sys
import urllib.request

import cv2

BASE = "https://fenix.ur.edu.pl/~mkepski/ds/data"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEST = os.path.join(ROOT, "tmp", "urfd")
AVAIL = os.path.join(ROOT, "tmp", "_urfd_available.txt")


def fetch(name: str) -> bool:
    dest = os.path.join(DEST, name)
    if os.path.exists(dest) and os.path.getsize(dest) > 1024:
        cap = cv2.VideoCapture(dest)
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            return True
        os.remove(dest)  # 坏文件删掉重下

    tmp = dest + ".part"
    url = f"{BASE}/{name}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
    except Exception as exc:  # noqa: BLE001
        if os.path.exists(tmp):
            os.remove(tmp)
        print(f"  [失败] {name}: {exc}", flush=True)
        return False

    cap = cv2.VideoCapture(tmp)
    ok, frame = cap.read()
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    if not ok or frame is None or n == 0:
        os.remove(tmp)
        print(f"  [坏文件·已删] {name}", flush=True)
        return False

    os.replace(tmp, dest)
    return True


def main() -> int:
    os.makedirs(DEST, exist_ok=True)
    with open(AVAIL, encoding="utf-8") as fh:
        names = [ln.strip() for ln in fh if ln.strip()]

    print(f"待下载 {len(names)} 个分卷 → {DEST}")
    done = 0
    bad = []
    for i, name in enumerate(names, 1):
        if fetch(name):
            done += 1
        else:
            bad.append(name)
        if i % 10 == 0 or i == len(names):
            print(f"  进度 {i}/{len(names)}  已就绪 {done}  失败 {len(bad)}", flush=True)

    print(f"\n完成：{done} 个可用")
    if bad:
        print(f"失败 {len(bad)} 个：{bad}")
        with open(os.path.join(ROOT, "tmp", "_urfd_fail.log"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(bad))
    total = sum(
        os.path.getsize(os.path.join(DEST, f)) for f in os.listdir(DEST) if f.endswith(".mp4")
    )
    print(f"总体积：{total / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
