"""异步批量上报。

设计要点（见设计方案第 2 节）：

  全部跑在客户端拿到工具结果之后，不阻塞任何调用。
  定时线程设为 daemon，不阻塞进程退出。
  无未上报行不发请求。
  失败不重试——数据还在文件里，下次启动接着发。

不引入 requests / httpx：标准库 urllib 够用，少一个依赖就少一个
安装失败的可能（内网环境尤其重要）。
"""

from __future__ import annotations

import json
import platform
import threading
import urllib.error
import urllib.request
import uuid
from typing import Any

from . import store
from .config import TelemetryConfig

# 一批最多 500 条，与后端 schema 的上限一致
BATCH_LIMIT = 500

# 单次请求超时。设计方案要求 3 秒，避免慢网络拖住后台线程
TIMEOUT = 3.0


class Reporter:
    """负责攒批与上报。线程安全，可被定时器与「满一批立即发」两条路径调用。"""

    def __init__(
        self,
        cfg: TelemetryConfig,
        version: str,
        channel: str,
        writer: store.EventWriter | None = None,
    ) -> None:
        self._cfg = cfg
        self._version = version
        self._channel = channel
        # writer 用来在读取前确保内存缓冲已落盘。不传时退化为只读文件
        # （测试里直接往文件写数据时不需要它）。
        self._writer = writer
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._started = False
        self._agent: str | None = None
        self._agent_version: str | None = None

        # installId 首次运行生成后持久化。只用于统计设备数，
        # 不含任何机器标识（见设计方案第 5 节）。
        self._install_id = self._load_or_create_install_id()

    # ---------- 对外 ----------

    def set_agent(self, name: str | None, version: str | None) -> None:
        """记录调用方平台信息，来自 MCP initialize 的 clientInfo。"""
        self._agent = name
        self._agent_version = version

    def start(self) -> None:
        """启动上报：先补发积压，再排定时器。

        设计方案第 3 节：上次运行遗留的未上报数据，应在下次启动时
        一并补发。所以这里先异步触发一次 flush，不等第一个 5 分钟
        定时器——否则用户开机几分钟就退出的话，积压数据要再等一轮。

        用 daemon 线程与定时器，都不阻止进程退出。
        """
        if self._timer is None and not self._started:
            self._started = True
            # 补发积压。异步执行，不拖慢服务启动与 MCP 握手。
            t = threading.Thread(target=self._flush_once, daemon=True)
            t.start()

        if self._timer is not None:
            return
        self._timer = threading.Timer(300.0, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def flush_async(self) -> None:
        """触发一次上报，不等待结果。

        满一批时由中间件调用，避免数据攒太久、降低断网滞留量。
        """
        t = threading.Thread(target=self._flush_once, daemon=True)
        t.start()

    # ---------- 内部 ----------

    def _tick(self) -> None:
        """定时器回调。发完再排下一次，避免累积多个定时器。"""
        self._flush_once()
        self._timer = None
        self.start()

    def _flush_once(self) -> None:
        """读未上报行 → 聚合 → 发送 → 记录 offset。

        按行数读取而不是「聚合后截断」：聚合只会减少条数，所以读
        BATCH_LIMIT 行聚合后必然不超过 BATCH_LIMIT，不会出现
        「只发了前 N 条、offset 却跳过全部」导致丢数据。

        重发时复用上次的 batchId 与结束位置：服务端 events 的唯一索引
        是 (batch_id, tool, ok, day, hour)，含 batch_id。换新号重发会被
        当成新数据再插一遍，调用数翻倍。
        """
        with self._lock:
            # 先把内存缓冲刷进文件，否则读不到刚发生的调用。
            # 正常情况下这一步几乎无开销（缓冲本来就空）。
            if self._writer is not None:
                self._writer.flush()

            state = store.read_state(self._cfg.offset_file)
            resend_end = state.resend_end

            if resend_end is not None:
                # 上次发出但未确认：严格重发同一区间、复用同一批次号
                rows, new_pos = store.read_pending(
                    self._cfg.events_file, state.pos, max_lines=BATCH_LIMIT, end_pos=resend_end
                )
                batch_id = state.pending_batch_id
            else:
                rows, new_pos = store.read_pending(
                    self._cfg.events_file, state.pos, max_lines=BATCH_LIMIT
                )
                batch_id = uuid.uuid4().hex

            if not rows:
                return

            events = store.aggregate(rows)
            if not events:
                # 有行但全部解析失败，直接推进位置避免卡住
                store.write_offset(self._cfg.offset_file, new_pos)
                return

            payload = {
                "schema": 1,
                "batchId": batch_id,
                "installId": self._install_id,
                "version": self._version,
                "channel": self._channel,
                "platform": _platform_tag(),
                "events": events,
            }
            if self._agent:
                payload["agent"] = self._agent
            if self._agent_version:
                payload["agentVersion"] = self._agent_version

            ok = self._post(payload)

            if ok:
                # 成功：推进位置，并清掉未确认批次号
                store.write_offset(self._cfg.offset_file, new_pos)
            else:
                # 失败：位置不动，记住批次号与结束位置，
                # 下次原样重发同一区间
                store.write_offset(
                    self._cfg.offset_file,
                    state.pos,
                    pending_batch_id=batch_id,
                    pending_end_pos=new_pos,
                )

    def _post(self, payload: dict[str, Any]) -> bool:
        """发一个批次。返回是否成功。

        任何异常都吞掉——统计是旁路，不能影响主流程。
        """
        if not self._cfg.endpoint:
            return False

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._cfg.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": f"zwcad-mcp/{self._version}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            return False

    def _load_or_create_install_id(self) -> str:
        """installId 持久化在状态目录。首次运行生成，之后复用。"""
        path = self._cfg.state_dir / "install_id"
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass

        new_id = uuid.uuid4().hex
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_id, encoding="utf-8")
        except OSError:
            # 写不了就用内存里的，本次运行有效
            pass
        return new_id


def _platform_tag() -> str:
    """平台标签，形如 win32-x64。不含主机名等可识别信息。"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # 归一化，避免同一平台出现多种写法
    system = {"windows": "win32", "darwin": "darwin", "linux": "linux"}.get(system, system)
    machine = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine
    )
    return f"{system}-{machine}"
