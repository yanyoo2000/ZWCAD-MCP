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

# 启动后等待首次 initialize 的宽限秒数。
# 避免启动补发抢在客户端握手前触发、让老格式积压数据被记成 unknown。
INITIAL_FLUSH_DELAY = 3.0


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
        self._state_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._boot_timer: threading.Timer | None = None
        self._started = False
        self._boot_flushed = False
        self._agent: str | None = None
        self._agent_version: str | None = None

        # installId 首次运行生成后持久化。只用于统计设备数，
        # 不含任何机器标识（见设计方案第 5 节）。
        self._install_id = self._load_or_create_install_id()

    # ---------- 对外 ----------

    @property
    def agent_info(self) -> tuple[str | None, str | None]:
        """当前已握手的调用方 (agent, agentVersion)。"""
        with self._state_lock:
            return self._agent, self._agent_version

    def set_agent(self, name: str | None, version: str | None) -> None:
        """记录调用方平台信息，来自 MCP initialize 的 clientInfo。"""
        clean_name = name if isinstance(name, str) and name else None
        clean_ver = version if clean_name and isinstance(version, str) and version else None
        should_flush = False
        with self._state_lock:
            self._agent = clean_name
            self._agent_version = clean_ver
            if self._started and not self._boot_flushed:
                self._boot_flushed = True
                if self._boot_timer is not None:
                    self._boot_timer.cancel()
                    self._boot_timer = None
                should_flush = True
        if should_flush:
            self.flush_async()

    def start(self) -> None:
        """启动上报：等待握手携带调用方信息后补发积压，并排周期定时器。

        若启动瞬间立即补发，会抢在客户端 initialize 前发出，使历史未带
        agent 字段的积压缺少上下文而被服务端记为 unknown。因此仅挂短兜底
        定时器：收到 initialize 即取消兜底并立刻补发；不走 initialize 的
        直连脚本在超期后仍会自动补发。
        """
        boot_timer: threading.Timer | None = None
        tick_timer: threading.Timer | None = None
        flush_now = False

        with self._state_lock:
            if not self._started:
                self._started = True
                if self._agent is not None:
                    self._boot_flushed = True
                    flush_now = True
                elif not self._boot_flushed:
                    boot_timer = threading.Timer(INITIAL_FLUSH_DELAY, self._trigger_boot_flush)
                    boot_timer.daemon = True
                    self._boot_timer = boot_timer

            if self._timer is None:
                tick_timer = threading.Timer(300.0, self._tick)
                tick_timer.daemon = True
                self._timer = tick_timer

        if boot_timer is not None:
            boot_timer.start()
        if tick_timer is not None:
            tick_timer.start()
        if flush_now:
            self.flush_async()

    def stop(self) -> None:
        """取消挂起定时器，主要供测试清理资源。"""
        with self._state_lock:
            if self._boot_timer is not None:
                self._boot_timer.cancel()
                self._boot_timer = None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._started = False

    def flush_async(self) -> None:
        """触发一次异步全量补发，不等待结果。

        满一批时由中间件调用，避免数据攒太久、降低断网滞留量。
        """
        t = threading.Thread(target=self._flush_all, daemon=True)
        t.start()

    # ---------- 内部 ----------

    def _trigger_boot_flush(self) -> None:
        """启动宽限期到期且仍未收到 initialize 时的兜底补发。"""
        with self._state_lock:
            self._boot_timer = None
            if self._boot_flushed:
                return
            self._boot_flushed = True
        self._flush_all()

    def _tick(self) -> None:
        """定时器回调。发完再排下一次，避免累积多个定时器。"""
        with self._state_lock:
            self._boot_flushed = True
            if self._boot_timer is not None:
                self._boot_timer.cancel()
                self._boot_timer = None
            self._timer = None

        self._flush_all()

        with self._state_lock:
            if self._started and self._timer is None:
                t = threading.Timer(300.0, self._tick)
                t.daemon = True
                self._timer = t
                t.start()

    def _flush_all(self) -> None:
        """连续发送所有待上报分段（按批上限及调用方切分）。"""
        while self._flush_once():
            pass

    def _resolve_batch_agent(self, rows: list[dict]) -> tuple[str | None, str | None]:
        """优先取事件自身携带的 agent；兼容无 agent 字段的老数据回退当前会话。"""
        for row in rows:
            if isinstance(row, dict):
                agent, ver = store.row_agent_key(row)
                if agent is not None:
                    return agent, ver
        return self.agent_info

    def _flush_once(self) -> bool:
        """读一段同调用方未上报行 → 聚合 → 发送 → 记录 offset。

        返回 True 表示本批已成功消费（推进了 offset），可能还有后续分段；
        返回 False 表示无待发数据或发送失败，调用方应停止本轮循环。

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
                    self._cfg.events_file,
                    state.pos,
                    max_lines=BATCH_LIMIT,
                    split_by_agent=True,
                )
                batch_id = uuid.uuid4().hex

            if not rows:
                return False

            events = store.aggregate(rows)
            if not events:
                # 有行但全部解析失败，直接推进位置避免卡住
                store.write_offset(self._cfg.offset_file, new_pos)
                return True

            batch_agent, batch_agent_ver = self._resolve_batch_agent(rows)

            payload = {
                "schema": 1,
                "batchId": batch_id,
                "installId": self._install_id,
                "version": self._version,
                "channel": self._channel,
                "platform": _platform_tag(),
                "events": events,
            }
            if batch_agent:
                payload["agent"] = batch_agent
            if batch_agent_ver:
                payload["agentVersion"] = batch_agent_ver

            ok = self._post(payload)

            if ok:
                # 成功：推进位置，并清掉未确认批次号
                store.write_offset(self._cfg.offset_file, new_pos)
                return True

            # 失败：位置不动，记住批次号与结束位置，
            # 下次原样重发同一区间
            store.write_offset(
                self._cfg.offset_file,
                state.pos,
                pending_batch_id=batch_id,
                pending_end_pos=new_pos,
            )
            return False

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
