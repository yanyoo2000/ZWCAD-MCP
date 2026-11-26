"""针对平台布局/字典/XData兼容及机械前置校验修复的回归测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

os.environ["TELEMETRY"] = "0"

from zwcad2d import server  # noqa: E402


class _NoModelSpaceLayout:
    """模拟 ZWCAD 2025 无 ModelSpace 属性的布局对象。"""

    def __init__(self, name: str, tab_order: int):
        self.Name = name
        self.TabOrder = tab_order

    def __getattr__(self, item):
        if item == "ModelSpace":
            raise OSError("Name ModelSpace not found")
        raise AttributeError(item)


class _NoNameDictEntry:
    """模拟顶层 Dictionaries 中不含 Name 属性的内部条目。"""

    ObjectName = "AcDbPlaceHolder"
    Handle = "2A"

    def __getattr__(self, item):
        if item in ("Name", "Count"):
            raise OSError(f"Name {item} not found")
        raise AttributeError(item)


class _FakeDictionaries:
    def __init__(self, items):
        self._items = items
        self.Count = len(items)

    def Item(self, idx):
        return self._items[idx]


class TestPlatformAndMechFixes(unittest.TestCase):
    def setUp(self):
        server.reset_cad_connection()

    def tearDown(self):
        server.reset_cad_connection()

    def test_manage_view_list_and_active_layout_without_modelspace_attr(self):
        model_layout = _NoModelSpaceLayout("Model", 0)
        paper_layout = _NoModelSpaceLayout("Layout1", 1)

        fake_zcad = MagicMock()
        fake_zcad.iter_layouts.return_value = [model_layout, paper_layout]
        fake_zcad.doc.ActiveLayout = model_layout

        with patch.object(server, "get_cad_connection", return_value=(fake_zcad, None)):
            res_list = server.zwcad_manage_view.fn("list_layouts", params={"include_model": True})
            res_active = server.zwcad_manage_view.fn("get_active_layout")

        self.assertNotIn("error", res_list)
        self.assertEqual(
            res_list["data"],
            [
                {"name": "Model", "tab_order": 0, "is_model_space": True},
                {"name": "Layout1", "tab_order": 1, "is_model_space": False},
            ],
        )
        self.assertNotIn("error", res_active)
        self.assertEqual(
            res_active["data"],
            {"name": "Model", "tab_order": 0, "is_model_space": True},
        )

    def test_manage_dictionary_list_tolerates_missing_name_or_count(self):
        named_dict = SimpleNamespace(Name="ACAD_GROUP", Count=2)
        anon_entry = _NoNameDictEntry()

        fake_zcad = MagicMock()
        fake_zcad.doc.Dictionaries = _FakeDictionaries([named_dict, anon_entry])

        with patch.object(server, "get_cad_connection", return_value=(fake_zcad, None)):
            res = server.zwcad_manage_dictionary.fn("list")

        self.assertNotIn("error", res)
        self.assertEqual(
            res["data"],
            [
                {"index": 0, "name": "ACAD_GROUP", "count": 2},
                {"index": 1, "name": "AcDbPlaceHolder"},
            ],
        )

    def test_get_xrecord_and_get_xdata_fallback_to_typed_interface(self):
        class _DynamicDispatchStub:
            def __init__(self, comobj):
                self._comobj = comobj

            def GetXRecordData(self):
                raise OSError(-2147352562, "无效的参数数目。")

            def GetXData(self, app_name):
                raise OSError(-2147352562, "无效的参数数目。")

        raw_xr = MagicMock()
        typed_xr = MagicMock()
        typed_xr.GetXRecordData.return_value = ((100, 1), ("AcDbXrecord", "val"))

        raw_ent = MagicMock()
        typed_ent = MagicMock()
        typed_ent.GetXData.return_value = ((1001, 1000), ("APP", "hello"))

        fake_dict = MagicMock()
        fake_dict.GetObject.return_value = _DynamicDispatchStub(raw_xr)
        fake_zcad = MagicMock()
        fake_zcad.doc.Dictionaries.Item.return_value = fake_dict
        fake_zcad.doc.HandleToObject.return_value = _DynamicDispatchStub(raw_ent)

        def _fake_best_iface(ptr):
            if ptr is raw_xr:
                return typed_xr
            if ptr is raw_ent:
                return typed_ent
            raise RuntimeError("unexpected pointer")

        with (
            patch.object(server, "get_cad_connection", return_value=(fake_zcad, None)),
            patch.object(server.comtypes.client, "GetBestInterface", side_effect=_fake_best_iface),
        ):
            res_xr = server.zwcad_manage_dictionary.fn(
                "get_xrecord", {"dict_name": "D1", "keyword": "K1"}
            )
            res_xd = server.zwcad_manage_xdata.fn(
                "get_xdata", {"handle": "60", "app_name": "APP"}
            )

        self.assertNotIn("error", res_xr)
        self.assertEqual(res_xr["data"], {"types": [100, 1], "values": ["AcDbXrecord", "val"]})
        self.assertNotIn("error", res_xd)
        self.assertEqual(
            res_xd["data"],
            {"handle": "60", "app_name": "APP", "types": [1001, 1000], "values": ["APP", "hello"]},
        )

    def test_mech_tools_reject_when_mechanical_unavailable(self):
        fake_zcad = MagicMock()
        fake_mech = MagicMock()
        fake_mech.open_file.side_effect = RuntimeError("Failed to get ZwmDb object.")

        with (
            patch.object(server, "get_cad_connection", return_value=(fake_zcad, fake_mech)),
            patch.object(server, "_typelib_state", return_value=(False, None, "tlb error")),
        ):
            res_partlist = server.zwcad_mech_create_partlist.fn()
            res_balloon = server.zwcad_mech_insert_balloon.fn()
            res_doc = server.zwcad_mech_doc.fn("open", r"C:\missing.dwg")
            res_info = server.zwcad_get_app_info.fn("mech_version")

        self.assertIn("error", res_partlist)
        fake_mech.zwm_app.send_command.assert_not_called()

        self.assertIn("error", res_balloon)
        fake_zcad.doc.SendCommand.assert_not_called()

        self.assertEqual(res_doc.get("code"), "TYPELIB_NOT_LOADED")
        fake_mech.zwm_app.open_doc.assert_not_called()

        self.assertEqual(res_info.get("code"), "TYPELIB_NOT_LOADED")

    def test_mech_doc_open_rejects_missing_file_even_when_typelib_loaded(self):
        fake_zcad = MagicMock()
        fake_mech = MagicMock()

        with (
            patch.object(server, "get_cad_connection", return_value=(fake_zcad, fake_mech)),
            patch.object(server, "_typelib_state", return_value=(True, "loaded", None)),
        ):
            res_missing = server.zwcad_mech_doc.fn("open", r"C:\definitely_not_exists_12345.dwg")
            self.assertIn("error", res_missing)
            fake_mech.zwm_app.open_doc.assert_not_called()

            with tempfile.NamedTemporaryFile(suffix=".dwg", delete=False) as tmp:
                tmp_path = tmp.name
            self.addCleanup(lambda: os.path.exists(tmp_path) and os.unlink(tmp_path))

            res_ok = server.zwcad_mech_doc.fn("open", tmp_path)
            self.assertNotIn("error", res_ok)
            fake_mech.zwm_app.open_doc.assert_called_once_with(tmp_path)


if __name__ == "__main__":
    unittest.main()
