"""老友 · runtime/local.env 的**唯一**解析器。

为什么单独抽一个模块：项目里有两个启动器会读同一份 runtime/local.env
（scripts/start.ps1 与 scripts/preview.py），再加一个自检脚本就是三处。
如果每处各写一份解析规则，迟早出现"命令行能读到、双击读不到"这类
只在用户机器上复现的问题。所以解析规则只有这一份，其余全部 import。

解析规则与 scripts/start.ps1 第 15-16 行逐字对齐：

    foreach($line in Get-Content -LiteralPath $config -Encoding utf8) {
        if ($line -match '^([A-Z_]+)=(.*)$') { ... }
    }

即**不是**完整 dotenv：注释行、空行、小写键名、引号包裹、行尾注释
一律按原样处理或整行跳过。这不是偷懒 —— 两边行为一致比"更聪明"重要。
"""
from __future__ import annotations

import re
from pathlib import Path

# 只认大写字母与下划线的键名；值允许为空。
_ENV_LINE = re.compile(r"^([A-Z_]+)=(.*)$")


def load_env_file(path: Path) -> dict[str, str]:
    """读取 KEY=value，返回解析到的键值对；文件不存在时返回空字典。

    故意不 strip、不剥引号：后端各处消费时本来就会 .strip()，
    在这里"帮用户清理"反而会让本函数与 start.ps1 的行为产生分歧。
    """
    if not path.is_file():
        return {}
    try:
        # utf-8-sig 兼容记事本等编辑器写出的 BOM；坏字节用 replace，
        # 不要因为一个坏字符就让整个启动流程抛异常。
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _ENV_LINE.match(line.rstrip("\r"))
        if match:
            values[match.group(1)] = match.group(2)
    return values
