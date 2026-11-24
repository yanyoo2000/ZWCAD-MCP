"""遥测开关与配置。

四道判断是串联的（见设计方案第 4 节）：任何一关关闭，整条链路就断，
不会「先记录再标记」。这样做的理由：涉密客户会抓包验证，若代码里
有「记下来但不发」的路径，无法自证清白。

优先级不是投票：环境变量是一票否决的硬开关。企业下发 TELEMETRY=0，
用户在自己配置里写 "enabled": true 也开不回来。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# 占位域名。部署时改成实际地址，或用 TELEMETRY_ENDPOINT 覆盖。
DEFAULT_ENDPOINT = "https://telemetry.example.com/v1/events"

# 配置文件位置。个人长期关闭统计时改这里。
CONFIG_PATH = Path.home() / ".zwcad-mcp" / "telemetry.json"


@dataclass(frozen=True)
class TelemetryConfig:
    """一次判断的结果。enabled 为 False 时 reason 说明是哪一关拦下的。"""

    enabled: bool
    reason: str
    endpoint: str | None
    state_dir: Path

    @property
    def events_file(self) -> Path:
        return self.state_dir / "events.jsonl"

    @property
    def offset_file(self) -> Path:
        return self.state_dir / "offset.json"


def _env_disabled() -> bool:
    """第 1 关：环境变量。

    只有明确写 0 / false / off 才算关闭。其他值（包括未设置）都放行，
    避免「用户随手写了 TELEMETRY=no」这类歧义被误判成开启。
    """
    raw = os.environ.get("TELEMETRY")
    if raw is None:
        return False
    return raw.strip().lower() in {"0", "false", "off", "no", "disable", "disabled"}


def _config_disabled() -> bool:
    """第 2 关：配置文件。

    文件不存在或读不了都视为「未关闭」——不能让一个损坏的配置文件
    意外把统计打开，但也不能因此报错影响主流程。
    """
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("enabled") is False


def _is_offline_bundle() -> bool:
    """当前是否运行在离线包里。

    离线包自带 Python 与依赖，通过 bridge.js 设置的环境变量识别。
    内网客户解压即用，不该默认上报——这是设计方案第 4 节第 3 关。
    """
    return os.environ.get("ZWCAD_MCP_OFFLINE") == "1"


def _offline_explicitly_enabled() -> bool:
    """离线包里必须显式 TELEMETRY=1 才开启，配置文件不能覆盖。"""
    return os.environ.get("TELEMETRY", "").strip() == "1"


def _endpoint() -> str | None:
    """上报端点。环境变量优先，其次内置默认值。"""
    raw = os.environ.get("TELEMETRY_ENDPOINT")
    if raw is not None:
        raw = raw.strip()
        return raw or None
    return DEFAULT_ENDPOINT


def _state_dir() -> Path:
    """本地状态目录。TELEMETRY_STATE_DIR 可覆盖，主要给测试用。

    Path.home() 在部分环境下会抛 RuntimeError（服务账户没有 home、
    精简容器没设 HOME、域账户配置文件只读）。这时退回系统临时目录，
    而不是让整个统计功能失效——临时目录重启会清空，但总比完全不工作好。
    """
    raw = os.environ.get("TELEMETRY_STATE_DIR")
    if raw:
        return Path(raw)
    try:
        return Path.home() / ".zwcad-mcp"
    except RuntimeError:
        return Path(tempfile.gettempdir()) / "zwcad-mcp"


def resolve() -> TelemetryConfig:
    """按四道开关的顺序判断。短路返回，后面的关不执行。"""
    state_dir = _state_dir()
    endpoint = _endpoint()

    # 第 1 关：环境变量（一票否决）
    if _env_disabled():
        return TelemetryConfig(False, "env-off", endpoint, state_dir)

    # 第 2 关：配置文件
    if _config_disabled():
        return TelemetryConfig(False, "config-off", endpoint, state_dir)

    # 第 3 关：离线包默认关闭，除非显式开启
    if _is_offline_bundle() and not _offline_explicitly_enabled():
        return TelemetryConfig(False, "offline-default-off", endpoint, state_dir)

    # 第 4 关：没有端点就不发（自建、本地调试场景）
    if not endpoint:
        return TelemetryConfig(False, "no-endpoint", None, state_dir)

    return TelemetryConfig(True, "enabled", endpoint, state_dir)


def log_disabled_once(cfg: TelemetryConfig) -> None:
    """关闭时在 stderr 提示一次。

    设计方案要求「首次运行提示」：用户有权知道统计是开还是关。
    只写 stderr，不写日志文件——避免为了提示反而产生本地落盘。
    """
    if cfg.enabled:
        return
    if getattr(log_disabled_once, "_done", False):
        return
    log_disabled_once._done = True  # type: ignore[attr-defined]
    print(f"[zwcad-mcp] 使用统计已关闭（{cfg.reason}）", file=sys.stderr)
