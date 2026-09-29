"""Export the current working source, including uncommitted edits, without local data.

Run after building and documenting a delivery. Git is used as a file inventory;
deleted files are omitted and no commit or repository metadata is distributed.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "node_modules", "runtime", "tmp", "__pycache__", ".pytest_cache", ".gradle", ".kotlin", "build", "dist"}
# runtime/ 默认整目录不导出（含本机凭据与抓拍），只放行可公开的环境变量模板。
ALLOWED_IN_EXCLUDED = {"runtime/local.env.example"}
# 分端源码包都带上的共有文件：协议，以及全项目统一的环境变量模板
# （start.ps1 读的就是它，各端按文档配置时都要照抄）。
SHARED_FILES = {"LICENSE", "runtime/local.env.example"}
SECRET_SUFFIXES = {".keystore", ".jks", ".pem", ".key"}
REPOSITORY = "https://github.com/lovhastum-png/LaoYou_ChuangXin_Agent"


def export(destination: Path, component: str = "all") -> int:
    destination = destination.resolve()
    if not destination.is_relative_to(ROOT) or destination.suffix.lower() != ".zip":
        raise ValueError("源码包必须保存到工作区内的.zip文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    listing = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode("utf-8")
    files: list[tuple[Path, Path]] = []
    for name in sorted(set(listing.split("\0")) - {""}):
        relative = Path(name)
        if component != "all" and relative.parts[0] != component and relative.as_posix() not in SHARED_FILES:
            continue
        if relative.parts[0] == "artifacts" and not (relative.parts[1:2] == ("qa",) or relative.name == "build.json"):
            continue
        if EXCLUDED_PARTS.intersection(relative.parts) and relative.as_posix() not in ALLOWED_IN_EXCLUDED:
            continue
        if relative.suffix.lower() in SECRET_SUFFIXES | {".zip", ".apk", ".pyc"}:
            continue
        if relative.name in {".env", "local.properties"} or relative.name.endswith(".env"):
            continue
        file = (ROOT / relative).resolve()
        if not file.is_relative_to(ROOT) or not file.is_file() or file == destination:
            continue
        files.append((file, relative))
    if not files:
        raise RuntimeError("没有可导出的源码文件")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file, relative in files:
            archive.write(file, Path("老友源码") / relative)
        if component != "all":
            archive.writestr("老友源码/开始开发.txt", (
                f"老友 {component} 源码\n\n"
                "本包仅含目标端源码和MIT协议，不是可直接双击运行的安装包。\n"
                f"各端开发步骤：{REPOSITORY}/blob/main/doc/01-子文档/12-安装使用与维护.md#7-开发者维护\n"
                f"完整架构与接口：{REPOSITORY}/tree/main/doc\n"
                f"运行包下载：{REPOSITORY}/releases/latest\n"
                "Web及Android开发需要可访问的后端，可先启动Windows便携包。\n"
                "Android源码不附签名密钥；请按文档生成本地测试密钥，更新现有安装须使用原证书。\n"
            ))
    print(f"已导出 {len(files)} 个文件：{destination}")
    return len(files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/老友-开发者源码.zip")
    parser.add_argument("--component", choices=("all", "web", "backend", "android"), default="all")
    args = parser.parse_args()
    export(ROOT / args.output, args.component)
