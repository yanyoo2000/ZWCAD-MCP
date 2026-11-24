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
        store.append_event(self.events, "zwcad_draw_line", True)
        store.append_event(self.events, "zwcad_draw_line", False)

        rows, pos = store.read_pending(self.events, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tool"], "zwcad_draw_line")
        self.assertTrue(rows[0]["ok"])
        self.assertFalse(rows[1]["ok"])
        self.assertGreater(pos, 0)

    def test_offset_prevents_reread(self):
        store.append_event(self.events, "t1", True)
        rows, pos = store.read_pending(self.events, 0)
        self.assertEqual(len(rows), 1)

        # 用新 offset 再读，应该读不到
        rows2, pos2 = store.read_pending(self.events, pos)
        self.assertEqual(len(rows2), 0)
        self.assertEqual(pos2, pos)

    def test_partial_line_not_consumed(self):
        """最后一行没写完时不能被消费，否则会丢数据。"""
        store.append_event(self.events, "t1", True)
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
        store.append_event(self.events, "t1", True)
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
                store.append_event(cfg.events_file, "t1", True)

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
                store.append_event(cfg.events_file, "t1", True)

                with patch.object(r, "_post", return_value=True):
                    r._flush_once()

                self.assertGreater(store.read_offset(cfg.offset_file), 0)

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
                    store.append_event(cfg.events_file, f"tool_{i}", True)

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


if __name__ == "__main__":
    unittest.main()
