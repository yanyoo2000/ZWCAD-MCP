"""遥测模块测试。

重点验证三件事：
  1. 四道开关的串联与优先级
  2. 关闭时真的不采集（不是「记下来但不发」）
  3. 聚合与 offset 推进逻辑正确
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# zwcad2d 在 runtime/ 下，不是仓库根
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from zwcad2d.telemetry import store  # noqa: E402
from zwcad2d.telemetry.config import resolve  # noqa: E402
from zwcad2d.telemetry.reporter import Reporter, _platform_tag  # noqa: E402


def write_event(events_file: Path, tool: str, ok: bool) -> None:
    """测试辅助：写一条记录并立即关闭句柄。

    用完就关，避免测试之间共享文件句柄导致 flush 时机不确定。
    """
    w = store.EventWriter(events_file)
    w.append(tool, ok)
    w.close()


class TestSwitches(unittest.TestCase):
    """四道开关必须串联：任何一关关闭就立即停止，后面的关不执行。"""

    def setUp(self):
        # 每个用例给独立的临时状态目录，避免污染真实 ~/.zwcad-mcp
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def env(self, extra: dict | None = None) -> dict:
        """构造测试用环境变量。

        不能直接 clear=True —— 那会把 HOME / USERPROFILE 等清掉，
        Path.home() 会抛 RuntimeError。这里只清掉遥测相关的键，
        保留系统必需的部分。
        """
        base = {k: v for k, v in os.environ.items() if not k.startswith("TELEMETRY")}
        base.pop("ZWCAD_MCP_OFFLINE", None)
        base["TELEMETRY_STATE_DIR"] = self._tmp.name
        if extra:
            base.update(extra)
        return base

    def test_default_enabled(self):
        with patch.dict(os.environ, self.env(), clear=True):
            cfg = resolve()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.reason, "enabled")

    def test_env_disables(self):
        for value in ("0", "false", "OFF", "no"):
            with self.subTest(value=value), patch.dict(
                os.environ, self.env({"TELEMETRY": value}), clear=True
            ):
                cfg = resolve()
                self.assertFalse(cfg.enabled)
                self.assertEqual(cfg.reason, "env-off")

    def test_env_other_values_do_not_disable(self):
        # 只有明确的否定值才算关闭，避免拼写歧义
        for value in ("1", "true", "yes", ""):
            with self.subTest(value=value), patch.dict(
                os.environ, self.env({"TELEMETRY": value}), clear=True
            ):
                cfg = resolve()
                self.assertTrue(cfg.enabled, f"TELEMETRY={value!r} 不应关闭统计")

    def test_env_beats_config(self):
        """环境变量是一票否决：配置文件写 enabled=true 也开不回来。"""
        cfg_file = Path(self._tmp.name) / "telemetry.json"
        cfg_file.write_text('{"enabled": true}', encoding="utf-8")
        with patch.dict(os.environ, self.env({"TELEMETRY": "0"}), clear=True), patch(
            "zwcad2d.telemetry.config.CONFIG_PATH", cfg_file
        ):
            cfg = resolve()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.reason, "env-off")

    def test_config_disables(self):
        cfg_file = Path(self._tmp.name) / "telemetry.json"
        cfg_file.write_text('{"enabled": false}', encoding="utf-8")
        with patch.dict(os.environ, self.env(), clear=True), patch(
            "zwcad2d.telemetry.config.CONFIG_PATH", cfg_file
        ):
            cfg = resolve()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.reason, "config-off")

    def test_broken_config_does_not_disable(self):
        """配置文件损坏不应意外关闭，也不应报错。"""
        cfg_file = Path(self._tmp.name) / "telemetry.json"
        cfg_file.write_text("{ 这不是 JSON", encoding="utf-8")
        with patch.dict(os.environ, self.env(), clear=True), patch(
            "zwcad2d.telemetry.config.CONFIG_PATH", cfg_file
        ):
            cfg = resolve()
        self.assertTrue(cfg.enabled)

    def test_offline_default_off(self):
        with patch.dict(os.environ, self.env({"ZWCAD_MCP_OFFLINE": "1"}), clear=True):
            cfg = resolve()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.reason, "offline-default-off")

    def test_offline_explicit_enable(self):
        env = self.env({"ZWCAD_MCP_OFFLINE": "1", "TELEMETRY": "1"})
        with patch.dict(os.environ, env, clear=True):
            cfg = resolve()
        self.assertTrue(cfg.enabled)

    def test_offline_layout_bundle_info_disables_without_env(self):
        """无 ZWCAD_MCP_OFFLINE 环境变量时，bundle-info.json 目录特征仍默认关闭。"""
        root = Path(self._tmp.name) / "bundle"
        fake_cfg = root / "runtime" / "zwcad2d" / "telemetry" / "config.py"
        fake_cfg.parent.mkdir(parents=True)
        fake_cfg.write_text("", encoding="utf-8")
        (root / "bundle-info.json").write_text("{}", encoding="utf-8")

        with patch("zwcad2d.telemetry.config.__file__", str(fake_cfg)):
            with patch.dict(os.environ, self.env(), clear=True):
                cfg = resolve()
            self.assertFalse(cfg.enabled)
            self.assertEqual(cfg.reason, "offline-default-off")

            with patch.dict(os.environ, self.env({"TELEMETRY": "1"}), clear=True):
                cfg_on = resolve()
            self.assertTrue(cfg_on.enabled)

    def test_offline_layout_python_and_site_packages_disables_without_env(self):
        """即使删掉 bundle-info.json，只要便携 python 与 site-packages 存在仍视为离线包。"""
        root = Path(self._tmp.name) / "bundle2"
        fake_cfg = root / "runtime" / "zwcad2d" / "telemetry" / "config.py"
        fake_cfg.parent.mkdir(parents=True)
        fake_cfg.write_text("", encoding="utf-8")
        (root / "python").mkdir()
        (root / "python" / "python.exe").write_text("", encoding="utf-8")
        (root / "site-packages").mkdir()

        with patch("zwcad2d.telemetry.config.__file__", str(fake_cfg)):
            with patch.dict(os.environ, self.env(), clear=True):
                cfg = resolve()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.reason, "offline-default-off")

    def test_site_packages_install_not_treated_as_offline(self):
        """普通 site-packages 安装路径不能误判为离线包。"""
        root = Path(self._tmp.name) / "venv"
        fake_cfg = root / "site-packages" / "zwcad2d" / "telemetry" / "config.py"
        fake_cfg.parent.mkdir(parents=True)
        fake_cfg.write_text("", encoding="utf-8")
        (root / "python").mkdir()
        (root / "python" / "python.exe").write_text("", encoding="utf-8")

        with patch("zwcad2d.telemetry.config.__file__", str(fake_cfg)):
            with patch.dict(os.environ, self.env(), clear=True):
                cfg = resolve()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.reason, "enabled")

    def test_no_endpoint_disables(self):
        with patch.dict(os.environ, self.env({"TELEMETRY_ENDPOINT": ""}), clear=True):
            cfg = resolve()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.reason, "no-endpoint")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.events = self.dir / "events.jsonl"
        self.offset = self.dir / "offset.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_read(self):
        write_event(self.events, "zwcad_draw_line", True)
        write_event(self.events, "zwcad_draw_line", False)

        rows, pos = store.read_pending(self.events, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tool"], "zwcad_draw_line")
        self.assertTrue(rows[0]["ok"])
        self.assertFalse(rows[1]["ok"])
        self.assertGreater(pos, 0)

    def test_offset_prevents_reread(self):
        write_event(self.events, "t1", True)
        rows, pos = store.read_pending(self.events, 0)
        self.assertEqual(len(rows), 1)

        # 用新 offset 再读，应该读不到
        rows2, pos2 = store.read_pending(self.events, pos)
        self.assertEqual(len(rows2), 0)
        self.assertEqual(pos2, pos)

    def test_partial_line_not_consumed(self):
        """最后一行没写完时不能被消费，否则会丢数据。"""
        write_event(self.events, "t1", True)
        # 记录完整行的字节数
        full_size = self.events.stat().st_size

        # 手工追加一个不完整行
        with open(self.events, "a", encoding="utf-8") as f:
            f.write('{"tool":"t2"')

        rows, pos = store.read_pending(self.events, 0)
        self.assertEqual(len(rows), 1, "只应读到完整的行")
        self.assertEqual(pos, full_size, "位置应停在完整行的末尾，不越过不完整的行")

    def test_offset_beyond_file_resets(self):
        """文件被清空后 offset 超过文件大小，应从头读。"""
        write_event(self.events, "t1", True)
        _, pos = store.read_pending(self.events, 0)
        self.events.write_text("", encoding="utf-8")

        rows, new_pos = store.read_pending(self.events, pos)
        self.assertEqual(len(rows), 0)
        self.assertEqual(new_pos, 0)

    def test_aggregate(self):
        rows = [
            {"tool": "t1", "ok": True, "day": "2026-11-24", "hour": 9},
            {"tool": "t1", "ok": True, "day": "2026-11-24", "hour": 9},
            {"tool": "t1", "ok": False, "day": "2026-11-24", "hour": 9},
            {"tool": "t2", "ok": True, "day": "2026-11-24", "hour": 10},
        ]
        out = store.aggregate(rows)
        by_key = {(r["tool"], r["ok"], r["hour"]): r["count"] for r in out}
        self.assertEqual(by_key[("t1", True, 9)], 2)
        self.assertEqual(by_key[("t1", False, 9)], 1)
        self.assertEqual(by_key[("t2", True, 10)], 1)

    def test_aggregate_skips_bad_rows(self):
        rows = [
            {"tool": "t1", "ok": True, "day": "2026-11-24", "hour": 9},
            {"tool": None, "ok": True, "day": "2026-11-24", "hour": 9},
            {"tool": "t2", "ok": True, "day": "2026-11-24", "hour": 99},
            {"tool": "t3", "ok": "yes", "day": "2026-11-24", "hour": 9},
        ]
        out = store.aggregate(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tool"], "t1")


class TestEventWriter(unittest.TestCase):
    """写入器的核心保证：快、不丢、无权限时降级到内存。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "events.jsonl"

    def test_data_reaches_os_without_close(self):
        """不用 close，数据也必须已到操作系统（SIGKILL 才不丢）。

        这验证的是行缓冲。若用 8KB 块缓冲，数据会留在 Python 内存里，
        进程被 SIGKILL 时丢失——而那正是本方案要避免的。
        """
        w = store.EventWriter(self.path)
        self.addCleanup(w.close)

        for i in range(5):
            w.append(f"tool_{i}", True)

        # 不调用 close/flush，直接看文件大小
        self.assertTrue(self.path.exists(), "文件应已创建")
        size = self.path.stat().st_size
        self.assertGreater(size, 0, "数据应已落盘，不能留在 Python 缓冲里")

        lines = self.path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 5)

    def test_buffer_empty_when_file_writable(self):
        """正常情况内存缓冲应为空——写完即清。"""
        w = store.EventWriter(self.path)
        self.addCleanup(w.close)

        for _ in range(100):
            w.append("t1", True)

        self.assertEqual(w.pending, 0, "文件可写时不应在内存里攒数据")

    def test_fallback_to_memory_when_not_writable(self):
        """文件不可写时，数据留在内存，且不抛异常。"""
        bad = Path(r"C:\Windows\System32\config\__zwcad_test_nope") / "e.jsonl"
        w = store.EventWriter(bad)

        # 关键：不能抛异常，否则会打断工具调用
        for i in range(3):
            w.append(f"tool_{i}", True)

        self.assertFalse(bad.exists())
        self.assertEqual(w.pending, 3, "写不进文件时应留在内存")

    def test_recovery_writes_back_buffered(self):
        """文件恢复可写后，内存里积压的数据要补写回去。"""
        bad = Path(r"C:\Windows\System32\config\__zwcad_test_nope2") / "e.jsonl"
        w = store.EventWriter(bad)

        for i in range(3):
            w.append(f"tool_{i}", True)
        self.assertEqual(w.pending, 3)

        # 模拟权限恢复：换到可写路径
        w._path = self.path
        w.append("after", True)
        w.flush()

        lines = self.path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 4, "3 条积压 + 1 条新的都应写入")
        self.assertTrue(any("tool_0" in line for line in lines), "积压数据不能丢")
        self.assertEqual(w.pending, 0)

    def test_buffer_has_upper_bound(self):
        """长期不可写时内存不能无限增长，超出上限丢最旧的。"""
        bad = Path(r"C:\Windows\System32\config\__zwcad_test_nope3") / "e.jsonl"
        limit = 10
        w = store.EventWriter(bad, max_buffer=limit)

        for i in range(limit * 3):
            w.append(f"tool_{i}", True)

        self.assertEqual(w.pending, limit, f"内存缓冲应被限制在 {limit} 条")
        # 保留的应是最新的（旧的被挤出）
        self.assertTrue(any(f"tool_{limit * 3 - 1}" in line for line in w._buffer))

    def test_close_is_idempotent(self):
        """重复 close 不应报错。"""
        w = store.EventWriter(self.path)
        w.append("t1", True)
        w.close()
        w.close()  # 第二次应无害


class TestReporter(unittest.TestCase):
    def test_platform_tag(self):
        tag = _platform_tag()
        self.assertIn("-", tag)
        # 不含主机名等可识别信息
        self.assertNotIn(os.environ.get("COMPUTERNAME", "___"), tag)

    def test_install_id_persists(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"TELEMETRY_STATE_DIR": td}, clear=True):
                cfg = resolve()
                r1 = Reporter(cfg, "1.0.0", "npm")
                r2 = Reporter(cfg, "1.0.0", "npm")
                self.assertEqual(r1._install_id, r2._install_id)

    def test_no_request_when_no_rows(self):
        """无未上报行时不应发请求。"""
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"TELEMETRY_STATE_DIR": td}, clear=True):
                cfg = resolve()
                r = Reporter(cfg, "1.0.0", "npm")
                with patch.object(r, "_post") as mock_post:
                    r._flush_once()
                mock_post.assert_not_called()

    def test_offset_not_advanced_on_failure(self):
        """上报失败时不能推进 offset，否则数据就丢了。"""
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"TELEMETRY_STATE_DIR": td}, clear=True):
                cfg = resolve()
                r = Reporter(cfg, "1.0.0", "npm")
                write_event(cfg.events_file, "t1", True)

                with patch.object(r, "_post", return_value=False):
                    r._flush_once()

                # offset 仍为 0，数据还在
                self.assertEqual(store.read_offset(cfg.offset_file), 0)
                rows, _ = store.read_pending(cfg.events_file, 0)
                self.assertEqual(len(rows), 1)

    def test_offset_advanced_on_success(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"TELEMETRY_STATE_DIR": td}, clear=True):
                cfg = resolve()
                r = Reporter(cfg, "1.0.0", "npm")
                write_event(cfg.events_file, "t1", True)

                with patch.object(r, "_post", return_value=True):
                    r._flush_once()

                self.assertGreater(store.read_offset(cfg.offset_file), 0)

    def test_retry_reuses_batch_id(self):
        """重发必须复用同一 batchId。

        服务端 events 的唯一索引是 (batch_id, tool, ok, day, hour)，
        含 batch_id。换新号重发不会冲突，同一批数据会被重复插入，
        查询时调用数翻倍。
        """
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"TELEMETRY_STATE_DIR": td}, clear=True):
                cfg = resolve()
                r = Reporter(cfg, "1.0.0", "npm")
                write_event(cfg.events_file, "t1", True)

                seen: list[str] = []

                def fail_post(payload):
                    seen.append(payload["batchId"])
                    return False

                with patch.object(r, "_post", side_effect=fail_post):
                    r._flush_once()  # 第一次失败

                with patch.object(r, "_post", side_effect=lambda p: (seen.append(p["batchId"]), True)[1]):
                    r._flush_once()  # 重发，应复用

                self.assertEqual(len(seen), 2)
                self.assertEqual(seen[0], seen[1], "重发的 batchId 必须与首次一致")

    def test_retry_does_not_mix_new_lines(self):
        """重发时严格只发上次那批，不能把期间新追加的行混进来。

        否则同一批次号下内容变了，服务端按 (batch_id, tool, ok, day, hour)
        去重时会把新增的计数误删。
        """
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"TELEMETRY_STATE_DIR": td}, clear=True):
                cfg = resolve()
                r = Reporter(cfg, "1.0.0", "npm")

                # 第一批：3 条
                for _ in range(3):
                    write_event(cfg.events_file, "t1", True)

                counts: list[int] = []

                def fail_post(payload):
                    counts.append(sum(e["count"] for e in payload["events"]))
                    return False

                with patch.object(r, "_post", side_effect=fail_post):
                    r._flush_once()

                # 期间又追加 5 条
                for _ in range(5):
                    write_event(cfg.events_file, "t1", True)

                with patch.object(r, "_post", side_effect=lambda p: (counts.append(sum(e["count"] for e in p["events"])), True)[1]):
                    r._flush_once()  # 重发：应仍然只发原来那 3 条

                self.assertEqual(counts[0], 3, "首次应发 3 条")
                self.assertEqual(counts[1], 3, "重发也只应发原来那 3 条，不能混入新追加的 5 条")

                # 再发一次，才轮到新增的 5 条
                with patch.object(r, "_post", side_effect=lambda p: (counts.append(sum(e["count"] for e in p["events"])), True)[1]):
                    r._flush_once()
                self.assertEqual(counts[2], 5, "第二批应是剩余的 5 条")

    def test_success_clears_pending_batch(self):
        """成功后要清掉未确认批次号，下一批用新号。"""
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"TELEMETRY_STATE_DIR": td}, clear=True):
                cfg = resolve()
                r = Reporter(cfg, "1.0.0", "npm")
                write_event(cfg.events_file, "t1", True)

                with patch.object(r, "_post", return_value=False):
                    r._flush_once()
                self.assertIsNotNone(store.read_state(cfg.offset_file).pending_batch_id)

                with patch.object(r, "_post", return_value=True):
                    r._flush_once()
                self.assertIsNone(store.read_state(cfg.offset_file).pending_batch_id)

    def test_oversized_batch_does_not_lose_data(self):
        """超过批次上限时，剩余数据必须留到下一批，不能丢。

        这是曾经的缺陷：按聚合后截断 events[:BATCH_LIMIT]，
        但 offset 已推进到文件末尾，超出的部分永久丢失。
        现在按行读取，聚合只减不增，所以不会超限。
        """
        from zwcad2d.telemetry.reporter import BATCH_LIMIT

        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"TELEMETRY_STATE_DIR": td}, clear=True):
                cfg = resolve()
                r = Reporter(cfg, "1.0.0", "npm")

                total = BATCH_LIMIT + 100
                for i in range(total):
                    write_event(cfg.events_file, f"tool_{i}", True)

                sent: list[int] = []

                def fake_post(payload):
                    sent.append(len(payload["events"]))
                    return True

                with patch.object(r, "_post", side_effect=fake_post):
                    # 第一批
                    r._flush_once()
                    # 第二批（剩余部分）
                    r._flush_once()

                self.assertEqual(sum(sent), total, "两批加起来应等于总行数，一条不丢")
                remaining, _ = store.read_pending(
                    cfg.events_file, store.read_offset(cfg.offset_file)
                )
                self.assertEqual(len(remaining), 0, "全部发送完毕后不应有剩余")


class TestFastMCPMiddlewareIntegration(unittest.IsolatedAsyncioTestCase):
    """用真实 FastMCP + Client 验证中间件提取工具名、成败与 clientInfo。"""

    async def test_real_fastmcp_tool_name_and_result_status(self):
        from fastmcp import Client, FastMCP
        from mcp.types import Implementation
        from zwcad2d.telemetry import middleware

        mcp = FastMCP("test-telemetry")

        @mcp.tool
        def ok_with_null_error() -> dict:
            return {"ok": True, "error": None, "data": "fine"}

        @mcp.tool
        def fail_with_error_dict() -> dict:
            return {"error": "something failed", "code": "ERR"}

        @mcp.tool
        def fail_with_exception() -> dict:
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ,
                {"TELEMETRY_STATE_DIR": td, "TELEMETRY_ENDPOINT": "https://example.invalid/events"},
                clear=True,
            ):
                cfg = resolve()
                writer = store.EventWriter(cfg.events_file)
                reporter = middleware.Reporter(cfg, "1.2.1", "npm", writer)
                mcp.add_middleware(middleware.TelemetryMiddleware(cfg, reporter, writer))

                async with Client(
                    mcp,
                    client_info=Implementation(name="Antigravity-QA", version="2.0.0"),
                ) as client:
                    await client.call_tool("ok_with_null_error", {})
                    await client.call_tool("fail_with_error_dict", {})
                    with self.assertRaises(Exception):
                        await client.call_tool("fail_with_exception", {})

                writer.close()

                rows = [
                    json.loads(line)
                    for line in cfg.events_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertEqual(
                    [(r["tool"], r["ok"]) for r in rows],
                    [
                        ("ok_with_null_error", True),
                        ("fail_with_error_dict", False),
                        ("fail_with_exception", False),
                    ],
                )
                self.assertEqual(reporter._agent, "Antigravity-QA")
                self.assertEqual(reporter._agent_version, "2.0.0")


if __name__ == "__main__":
    unittest.main()
