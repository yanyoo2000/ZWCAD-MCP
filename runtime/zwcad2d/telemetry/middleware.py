"""FastMCP 中间件：唯一采集点。

设计方案第 1 节的核心判断：

  采集点选在执行侧（Python），不在 Node 转发层。
  因为只有执行侧才知道工具是否真的成功了，转发层看到的只是 JSON 文本。

用 FastMCP 的 on_call_tool 钩子，而不是改 40 个工具函数——
一处埋点全覆盖，加新工具时不用记得补埋点。
"""

from __future__ import annotations

import atexit
import time
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from . import store
from .config import TelemetryConfig, log_disabled_once
from .reporter import Reporter

# 满这么多条未上报记录就立刻发，不等定时器。
# 高频用户一天几百次调用，攒 5 分钟可能上千条。
FLUSH_THRESHOLD = 50


def _is_failure(result: Any) -> bool:
    """判断调用是否失败。

    设计方案第 2 节的成败判据：
      调用抛异常，或返回体含 error 字段，都记失败。
      不能只看异常——现有工具大量以 {"error": ...} 返回。
    """
    # FastMCP 的 ToolResult 有多种形态，逐层探
    content = getattr(result, "content", None)

    # 结构化输出（dict）优先看
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if structured.get("error"):
            return True
        if structured.get("ok") is False:
            return True

    # 文本内容里找 JSON 形态的 error
    if content:
        for item in content:
            text = getattr(item, "text", None)
            if not isinstance(text, str):
                continue
            # 只做轻量判断：不解析完整 JSON，避免在大响应上耗 CPU
            stripped = text.strip()
            if stripped.startswith("{") and '"error"' in stripped[:200]:
                return True

    return False


class TelemetryMiddleware(Middleware):
    """记录每次工具调用的工具名与成败。

    只在同步段做一次追加写，不 fsync、不发网络（设计方案第 2 节）。
    """

    def __init__(self, cfg: TelemetryConfig, reporter: Reporter, writer: store.EventWriter) -> None:
        self._cfg = cfg
        self._reporter = reporter
        self._writer = writer
        self._pending = 0

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        # 统计关闭时直接放行，不碰任何 IO。
        # 这是「关」的定义：根本不采集，不是「记下来但不发」。
        if not self._cfg.enabled:
            return await call_next(context)

        tool_name = self._tool_name(context)

        started = time.monotonic()
        try:
            result = await call_next(context)
        except Exception:
            # 工具抛异常也算一次失败调用，记完再把异常抛回去
            self._record(tool_name, ok=False)
            raise
        finally:
            _ = time.monotonic() - started

        self._record(tool_name, ok=not _is_failure(result))
        return result

    async def on_initialize(self, context: MiddlewareContext, call_next):
        """从 MCP 握手取调用方平台名。

        设计方案第 7 节：客户端在 initialize 时上报 clientInfo.name，
        服务端据此区分调用方。npm / 离线包由 bridge 注入，uvx / PyPI
        直连时读 Python 侧的 clientInfo。
        """
        if self._cfg.enabled:
            try:
                params = context.message.params  # type: ignore[attr-defined]
                info = getattr(params, "clientInfo", None)
                if info is not None:
                    self._reporter.set_agent(
                        getattr(info, "name", None),
                        getattr(info, "version", None),
                    )
            except (AttributeError, TypeError):
                # 拿不到就算了，服务端会记为 unknown
                pass

        return await call_next(context)

    def _tool_name(self, context: MiddlewareContext) -> str:
        try:
            params = context.message.params  # type: ignore[attr-defined]
            name = getattr(params, "name", None)
            if isinstance(name, str) and name:
                return name
        except (AttributeError, TypeError):
            pass
        return "unknown"

    def _record(self, tool: str, ok: bool) -> None:
        """落一行文件，必要时触发异步上报。"""
        self._writer.append(tool, ok)

        self._pending += 1
        if self._pending >= FLUSH_THRESHOLD:
            self._pending = 0
            self._reporter.flush_async()


def install(mcp, version: str, channel: str) -> TelemetryConfig:
    """把中间件挂到 FastMCP 实例上。

    返回配置，供调用方决定是否打印「已关闭」提示。
    """
    from .config import resolve

    cfg = resolve()
    log_disabled_once(cfg)

    # 无论开关如何都创建 writer：关闭时不追加数据，但保持结构一致，
    # 也便于将来「运行中改成开启」的扩展。
    writer = store.EventWriter(cfg.events_file)
    # 进程退出时最后刷一次，把内存缓冲里可能残留的数据落盘。
    # atexit 对 SIGKILL 无效，但能覆盖正常退出与 SIGTERM。
    atexit.register(writer.close)

    reporter = Reporter(cfg, version, channel, writer)
    mcp.add_middleware(TelemetryMiddleware(cfg, reporter, writer))

    if cfg.enabled:
        reporter.start()

    return cfg
