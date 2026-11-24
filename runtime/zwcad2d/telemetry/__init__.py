"""使用统计（遥测）。

设计原则（详见 ZWCAD-Telemetry 仓库的设计方案）：

  只统计工具名与调用次数，不碰图纸内容、文件路径、工具参数。
  可关闭：环境变量 TELEMETRY=0 一票否决。
  离线包默认关闭，需显式 TELEMETRY=1 开启。
  关闭时根本不采集，不是「记下来但不发」。

对外只暴露 install()，其余为内部实现。
"""

from __future__ import annotations

from .config import resolve
from .middleware import install

__all__ = ["install", "resolve"]
