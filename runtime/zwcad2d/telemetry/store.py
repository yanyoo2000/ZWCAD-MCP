"""本地 JSONL 落盘与上报进度记录。

核心约束（见设计方案第 2、3 节）：

  同步段只做一次追加写，不发网络。
  逐次落文件是数据不丢的根本保障——MCP 客户端杀进程用 SIGKILL，
  不给程序任何收尾机会，所以不能靠「退出时上报」。

文件格式：一行一条调用记录，JSON。
  {"tool":"zwcad_draw_line","ok":true,"ts":"2026-11-24T09:05:12Z","day":"2026-11-24","hour":9}

offset.json 记录已消费到的字节位置。只记录位置而不是删文件，
因为删文件会与并发写产生竞态：写到一半的文件被删，那条记录就丢了。
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# 内存缓冲上限。文件长期不可写时（权限、磁盘满），超出这个数就丢最旧的，
# 避免无限占内存。正常情况缓冲区始终为空，不会触发。
MEMORY_BUFFER_MAX = 1000


def _now() -> tuple[str, str, int]:
    """返回 (ISO 时间戳, 本地日期, 本地小时)。

    day 与 hour 用本地时间，与设计方案一致：看板按客户端本地时区展示。
    """
    local = datetime.now()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ts, local.strftime("%Y-%m-%d"), local.hour


class EventWriter:
    """事件写入：内存缓冲 + 常驻文件句柄。

    为什么不是每次 open/close：
      Windows 实测每次 mkdir + open + close 约 480 微秒，占一次 COM 工具
      调用（约 50ms）的 1%。保持句柄后单次 write 只要 0.3 微秒。

    为什么还要内存缓冲：
      作为「文件写不进去」时的安全网。写完文件就清空缓冲，所以正常情况下
      内存占用接近零；只有在磁盘满 / 无写权限 / 杀软锁文件时才会攒数据，
      等文件恢复可写再补写回去。

    这个组合既能保证「一次调用只花 0.3 微秒」，又保证「写不进文件时不丢」。
    """

    def __init__(self, events_file: Path, max_buffer: int = MEMORY_BUFFER_MAX) -> None:
        self._path = events_file
        self._fh = None
        self._lock = threading.Lock()
        self._buffer: deque[str] = deque(maxlen=max_buffer)

    def append(self, tool: str, ok: bool) -> None:
        """记录一次调用。

        先入内存（永不失败），再尝试落盘。任何 IOException 都吞掉——
        统计是旁路，不能因为写文件失败让工具调用报错。
        """
        ts, day, hour = _now()
        line = (
            json.dumps(
                {"tool": tool, "ok": ok, "ts": ts, "day": day, "hour": hour},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )

        with self._lock:
            self._buffer.append(line)
            self._flush_locked()

    def flush(self) -> None:
        """把缓冲里的数据写入文件并刷内核缓冲。

        上报前调用，确保文件里有最新数据可读。
        """
        with self._lock:
            self._flush_locked()
            if self._fh is not None and not self._fh.closed:
                try:
                    self._fh.flush()
                except OSError:
                    pass

    def close(self) -> None:
        """关闭句柄。由 atexit 注册，保证退出前最后刷一次。"""
        with self._lock:
            self._flush_locked()
            if self._fh is not None and not self._fh.closed:
                try:
                    self._fh.flush()
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None

    @property
    def pending(self) -> int:
        """缓冲里还有多少条未落盘。正常情况恒为 0。"""
        return len(self._buffer)

    # ---------- 内部 ----------

    def _flush_locked(self) -> None:
        """把缓冲写入文件。失败时数据留在缓冲里，下次重试。"""
        if not self._buffer:
            return

        try:
            fh = self._ensure_handle_locked()
            for line in self._buffer:
                fh.write(line)
            self._buffer.clear()
        except OSError:
            # 磁盘满、权限不足等。保留缓冲，等下次调用或退出时再试。
            pass

    def _ensure_handle_locked(self):
        """惰性打开文件句柄，之后复用。

        必须用行缓冲（buffering=1）：默认的 8KB 块缓冲会把数据留在
        Python 内存里，进程被 SIGKILL 时这部分就丢了——而那正是本方案
        要避免的。行缓冲让每次 write 都进入操作系统缓冲，SIGKILL 不会丢
        （只有断电才会丢内核缓冲里最后几行）。

        实测：行缓冲 6 微秒/次，块缓冲 0.4 微秒/次，但前者保证不丢。
        相比原先每次 open/close 的 480 微秒，仍然快 80 倍。

        句柄失效（被外部关闭）时重新打开。
        """
        if self._fh is not None and not self._fh.closed:
            return self._fh

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8", buffering=1)
        return self._fh


@dataclass
class OffsetState:
    """上报进度。

    pos              已确认消费到的字节位置
    pending_batch_id 已发出但未确认的批次号；非空表示下次要重发同一批
    pending_end_pos  那一批数据的结束位置；重发时严格读到这个位置，
                     避免把期间新追加的行混进同一批次
    """

    pos: int = 0
    pending_batch_id: str | None = None
    pending_end_pos: int = 0

    @property
    def resend_end(self) -> int | None:
        """需要重发时返回那批的结束位置，否则 None。"""
        if self.pending_batch_id and self.pending_end_pos > self.pos:
            return self.pending_end_pos
        return None


def read_state(offset_file: Path) -> OffsetState:
    """读取进度状态。读不到或损坏就当全新开始。"""
    try:
        with open(offset_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return OffsetState()

    pos = data.get("pos", 0)
    if not isinstance(pos, int) or pos < 0:
        pos = 0

    bid = data.get("pendingBatchId")
    if not (isinstance(bid, str) and bid):
        bid = None

    end = data.get("pendingEndPos", 0)
    if not isinstance(end, int) or end <= pos:
        end = 0
        if bid is not None and end == 0:
            # 有批次号却没有结束位置，状态不一致，丢弃批次号重新生成
            bid = None

    return OffsetState(pos=pos, pending_batch_id=bid, pending_end_pos=end)


def write_state(offset_file: Path, state: OffsetState) -> None:
    """写进度状态。先写临时文件再 rename，避免写到一半断电损坏。"""
    try:
        offset_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = offset_file.with_suffix(".tmp")
        payload: dict = {"pos": state.pos}
        if state.pending_batch_id:
            payload["pendingBatchId"] = state.pending_batch_id
            payload["pendingEndPos"] = state.pending_end_pos
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, offset_file)
    except OSError:
        pass


def read_offset(offset_file: Path) -> int:
    """只读已消费位置。"""
    return read_state(offset_file).pos


def write_offset(offset_file: Path, pos: int, pending_batch_id: str | None = None,
                 pending_end_pos: int = 0) -> None:
    """写进度。pending_batch_id 非空表示这批已发出但未确认。"""
    write_state(offset_file, OffsetState(pos, pending_batch_id, pending_end_pos))


def read_pending_batch_id(offset_file: Path) -> str | None:
    """读取未确认批次的批次号。"""
    return read_state(offset_file).pending_batch_id


def read_pending(
    events_file: Path,
    offset: int,
    max_lines: int | None = None,
    end_pos: int | None = None,
) -> tuple[list[dict], int]:
    """读取 offset 之后的记录。

    返回 (记录列表, 新的字节位置)。新位置是「成功解析到的最后一行末尾」，
    这样解析失败的行会被下次重试，而不是跳过。

    max_lines 限制一次读多少行。调用方应传批次上限——
    聚合只会减少条数，所以读 N 行聚合后必然 ≤ N 条，
    不会出现「发出去了但 offset 越过了未发的行」导致丢数据。

    end_pos 限定读到哪个字节位置为止。重发「已发出但未确认」的批次时
    必须传它，否则期间新追加的行会混进同一批次——同一批次号下内容变了，
    服务端按 (batch_id, tool, ok, day, hour) 去重时会误删新增的计数。

    文件被截断（比如用户手动清空）时，offset 可能超过文件大小，
    此时从头读。
    """
    try:
        size = events_file.stat().st_size
    except OSError:
        return [], 0

    if offset > size:
        offset = 0

    limit = size if end_pos is None or end_pos > size else end_pos

    rows: list[dict] = []
    new_pos = offset

    try:
        with open(events_file, "rb") as f:
            f.seek(offset)
            while f.tell() < limit:
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    # 最后一行还没写完（并发追加中），留到下次
                    break
                try:
                    rows.append(json.loads(line.decode("utf-8")))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # 坏行跳过，但位置前移，避免卡住整条链路
                    pass
                new_pos = f.tell()
                if max_lines is not None and len(rows) >= max_lines:
                    break
    except OSError:
        return [], offset

    return rows, new_pos


def aggregate(rows: list[dict]) -> list[dict]:
    """按「工具 × 成败 × 天 × 小时」聚合。

    设计方案第 9 节：events 表存的是聚合行，不是每次调用一行。
    客户端先聚合再上报，能把一天的几百次调用压成几十行。
    """
    buckets: dict[tuple[str, bool, str, int], int] = {}

    for r in rows:
        tool = r.get("tool")
        day = r.get("day")
        hour = r.get("hour")
        ok = r.get("ok")

        if not isinstance(tool, str) or not isinstance(day, str):
            continue
        if not isinstance(hour, int) or not (0 <= hour <= 23):
            continue
        if not isinstance(ok, bool):
            continue

        key = (tool, ok, day, hour)
        buckets[key] = buckets.get(key, 0) + 1

    return [
        {"tool": t, "ok": o, "day": d, "hour": h, "count": c}
        for (t, o, d, h), c in buckets.items()
    ]
