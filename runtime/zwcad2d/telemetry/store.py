"""本地 JSONL 落盘与上报进度记录。

核心约束（见设计方案第 2、3 节）：

  同步段只做一次追加写，不 fsync、不发网络。
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
from datetime import datetime, timezone
from pathlib import Path

# 同一进程内多线程写文件要串行化。MCP 服务可能有并发请求。
_lock = threading.Lock()

# 单条记录的行数上限保护：正常情况下文件不会超过这个规模，
# 超过说明上报长期失败（比如端点配错），此时截断避免无限增长。
MAX_LINES = 50_000


def _now() -> tuple[str, str, int]:
    """返回 (ISO 时间戳, 本地日期, 本地小时)。

    day 与 hour 用本地时间，与设计方案一致：看板按客户端本地时区展示。
    """
    local = datetime.now()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ts, local.strftime("%Y-%m-%d"), local.hour


def append_event(events_file: Path, tool: str, ok: bool) -> None:
    """追加一条调用记录。

    失败不影响主流程——统计只是旁路，不能因为写文件失败让工具调用报错。
    """
    ts, day, hour = _now()
    line = json.dumps(
        {"tool": tool, "ok": ok, "ts": ts, "day": day, "hour": hour},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    try:
        events_file.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(events_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                # 不 fsync：设计上接受「断电可能丢最后几行」，
                # 换取每次调用少一次同步 IO
    except OSError:
        # 磁盘满、权限不足等。静默忽略，不影响 CAD 操作。
        pass


def read_offset(offset_file: Path) -> int:
    """读取已消费的字节位置。读不到就当 0（从头开始）。"""
    try:
        with open(offset_file, encoding="utf-8") as f:
            data = json.load(f)
        pos = data.get("pos", 0)
        return pos if isinstance(pos, int) and pos >= 0 else 0
    except (OSError, json.JSONDecodeError):
        return 0


def write_offset(offset_file: Path, pos: int) -> None:
    """记录消费位置。先写临时文件再 rename，避免写到一半断电损坏。"""
    try:
        offset_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = offset_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pos": pos}, f)
        os.replace(tmp, offset_file)
    except OSError:
        pass


def read_pending(events_file: Path, offset: int) -> tuple[list[dict], int]:
    """读取 offset 之后的记录。

    返回 (记录列表, 新的字节位置)。新位置是「成功解析到的最后一行末尾」，
    这样解析失败的行会被下次重试，而不是跳过。

    文件被截断（比如用户手动清空）时，offset 可能超过文件大小，
    此时从头读。
    """
    try:
        size = events_file.stat().st_size
    except OSError:
        return [], 0

    if offset > size:
        offset = 0

    rows: list[dict] = []
    new_pos = offset

    try:
        with open(events_file, "rb") as f:
            f.seek(offset)
            while True:
                start = f.tell()
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    # 最后一行还没写完（并发追加中），留到下次
                    break
                try:
                    rows.append(json.loads(line.decode("utf-8")))
                    new_pos = f.tell()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # 坏行跳过，但位置前移，避免卡住整条链路
                    new_pos = f.tell()
                if len(rows) >= MAX_LINES:
                    break
                _ = start
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
