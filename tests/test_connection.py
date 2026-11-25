"""连接管理与诊断探测回归测试（纯 mock，不依赖运行中的 CAD）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

# 避免导入 server 时向默认测试路径或公网初始化统计后台
os.environ["TELEMETRY"] = "0"

from zwcad2d import server  # noqa: E402


class TestCadConnection(unittest.TestCase):
    def setUp(self):
        server.reset_cad_connection()

    def tearDown(self):
        server.reset_cad_connection()

    def test_require_mechanical_does_not_launch_when_no_cad(self):
        """无活动 CAD 且 create_if_not_exists=False 时，禁止触发 ZwCADMech() 偷偷拉起进程。"""
        fake_zcad = MagicMock()
        type(fake_zcad).app = property(lambda self: (_ for _ in ()).throw(OSError("MK_E_UNAVAILABLE")))
        fake_mech_cls = MagicMock()

        with (
            patch.object(server, "ZwCAD", return_value=fake_zcad),
            patch.object(server, "_load_mechanical_class", return_value=fake_mech_cls),
        ):
            with self.assertRaises(OSError):
                server.get_cad_connection(require_mechanical=True, create_if_not_exists=False)

        fake_mech_cls.assert_not_called()

    def test_require_mechanical_passes_active_cad_app(self):
        """存在活动 CAD 时，应将 zcad_conn.app 作为 cad_app 传入 ZwCADMech。"""
        fake_app = object()
        fake_zcad = MagicMock()
        fake_zcad.app = fake_app
        fake_mech_instance = object()
        fake_mech_cls = MagicMock(return_value=fake_mech_instance)

        with (
            patch.object(server, "ZwCAD", return_value=fake_zcad),
            patch.object(server, "_load_mechanical_class", return_value=fake_mech_cls),
        ):
            zcad_conn, mech_conn = server.get_cad_connection(require_mechanical=True)

        self.assertIs(zcad_conn, fake_zcad)
        self.assertIs(mech_conn, fake_mech_instance)
        fake_mech_cls.assert_called_once_with(cad_app=fake_app)

    def test_capabilities_reports_unavailable_when_app_fails(self):
        """zcad_conn.app 不可用时，platform.available 必须为 False 而非误报 True。"""
        fake_zcad = MagicMock()
        type(fake_zcad).app = property(lambda self: (_ for _ in ()).throw(OSError("no active cad")))

        with patch.object(server, "get_cad_connection", return_value=(fake_zcad, None)):
            caps = server._collect_zwcad_2d_capabilities(probe_cad=True)

        self.assertFalse(caps["platform"]["available"])
        self.assertIn("no active cad", caps["platform"]["error"])


if __name__ == "__main__":
    unittest.main()
