"""跌倒检测推理侧车。

独立进程，独立虚拟环境（backend/pose/.venv），不与 backend/.venv 共享依赖。
职责边界：只做「视频帧 → kind=fall 观测」的推理；事件、通知、权限仍归后端。
"""

__all__ = ["detector", "client", "config"]
