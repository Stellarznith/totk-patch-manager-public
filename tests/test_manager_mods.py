import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/manager"))
import core
import manager_mods as mods


class ModCenterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TotK MOD 验收 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "游戏 副本"
        self.game.mkdir()
        self.exe = self.game / "totk.exe"
        self.exe.write_bytes(b"TOTK_MODS_ORDER\0TOTK_MODS_DISABLED\0TOTK_MODS_DIR\0mods: %d folders in %s\0")
        self.ini = self.game / "totk.ini"
        self.ini.write_bytes(b"[graphics]\r\nupscaler = dlss\r\n# keep this\r\n")
        self.ctx = dict(game=str(self.exe), version="0.4.0+test", profile=str(self.game / "profile"))
        self.idle = lambda _: None
        environment = patch.dict(os.environ, {k: v for k, v in os.environ.items() if k.upper() not in
                                                {"TOTK_MODS_DIR", "TOTK_MODS_DISABLED", "TOTK_MODS_ORDER"}}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def center(self):
        return mods.Center(self.ctx, self.idle, lambda: None, lambda _: "1.4.3")

    def run_mod(self, action, **kwargs):
        return self.center().dispatch(dict(action="mods-" + action, **kwargs))

    def archive(self, name, contents):
        path = self.root / (name + ".zip")
        with zipfile.ZipFile(path, "w") as bundle:
            for key, value in contents:
                bundle.writestr(key, value)
        return str(path)

    def import_mods(self, *sources, replace=True):
        preview = self.run_mod("preview", sources=list(map(str, sources)), replace=True)
        result = self.run_mod("install", sources=list(map(str, sources)), replace=replace, token=preview["token"])
        return preview, result

    def test_zip_and_folder_batch_real_ini_persistence_and_order(self):
        first = self.archive("A 中文", [("romfs/Pack/shared.txt", "alpha"), ("mod.json", '{"author":"demo"}')])
        second = self.root / "B 空格" / "romfs"
        (second / "Pack").mkdir(parents=True)
        (second / "Pack/shared.txt").write_text("beta")
        preview, result = self.import_mods(first, second)
        self.assertEqual(2, result["changed"])
        self.assertEqual(["冲突"], [x["type"] for x in preview["conflicts"]])
        self.assertEqual("demo", self.run_mod("scan")["mods"][0]["author"])
        self.run_mod("toggle", ids=["A 中文", "B 空格"], enabled=False)
        self.run_mod("order", order=["B 空格", "A 中文"])
        self.assertEqual(["B 空格", "A 中文"], [row["id"] for row in self.run_mod("scan")["mods"]])
        raw, config = mods.read_ini(self.ini)
        self.assertEqual("A 中文|B 空格", config["mods_disabled"])
        self.assertEqual("B 空格|A 中文", config["mods_order"])
        self.assertEqual(1, raw.count(b"[mods]"))
        self.assertIn(b"upscaler = dlss\r\n# keep this", raw)
        self.ini.write_bytes(raw + b'TOTK_MODS_DISABLED = "B \xe7\xa9\xba\xe6\xa0\xbc"\r\nmods_disabled = ""\r\n')
        self.assertEqual([False, True], [row["enabled"] for row in self.run_mod("scan")["mods"]])
        with self.assertRaisesRegex(ValueError, "F1"):
            self.run_mod("undo")

    def test_duplicate_skip_replace_and_uninstall_undo(self):
        source = self.archive("Demo", [("romfs/file", "v1")])
        self.assertEqual(1, self.import_mods(source)[1]["changed"])
        self.assertEqual(0, self.import_mods(source)[1]["changed"])
        source = self.archive("Demo", [("romfs/file", "v2")])
        live = self.game / "mods/Demo/romfs/file"
        self.assertEqual(0, self.import_mods(source, replace=False)[1]["changed"])
        self.assertEqual("v1", live.read_text())
        self.assertEqual(1, self.import_mods(source)[1]["changed"])
        self.assertEqual("v2", live.read_text())
        self.run_mod("remove", ids=["Demo"])
        self.assertFalse(live.exists())
        self.run_mod("undo")
        self.assertEqual("v2", live.read_text())

    def test_same_content_merge_dependencies_and_builtin_protection(self):
        table = "romfs/System/Resource/ResourceSizeTable.Product.rsizetable.zs"
        manifest = self.game / "romfs-patches-ps5/manifest.tsv"
        manifest.parent.mkdir()
        manifest.write_text("shared.txt\t4\t" + "0" * 64 + "\t-\n")
        a = self.archive("A", [("romfs/shared.txt", "same"), (table, "a"),
                               ("mod.json", json.dumps({"dependencies": ["Absent"], "game_versions": ["1.0.0"]}))])
        b = self.archive("B", [("romfs/shared.txt", "same"), (table, "b")])
        self.import_mods(a, b)
        locked = self.game / "mods/Managed/romfs"
        locked.mkdir(parents=True)
        (locked / "file").write_text("owned")
        (locked.parent / ".totk-manager.json").write_text("{}")
        flags = {item["type"] for item in self.run_mod("scan")["conflicts"]}
        self.assertEqual({"相同", "可共存", "内置覆盖", "兼容性", "依赖"}, flags)
        for action, kwargs in [("remove", {}), ("toggle", {"enabled": False})]:
            with self.assertRaisesRegex(ValueError, "受保护"):
                self.run_mod(action, ids=["A", "Managed"], **kwargs)
        self.assertTrue(locked.is_dir())
        self.assertEqual(3, len(self.run_mod("scan")["mods"]))

    def test_profiles_preview_apply_previous_export_and_metadata_do_not_change_ini(self):
        self.import_mods(self.archive("A", [("romfs/a", "one")]), self.archive("B", [("romfs/b", "two")]))
        self.run_mod("toggle", ids=["B"], enabled=False)
        raw = self.ini.read_bytes()
        self.run_mod("notes", ids=["A"], favorite=True, category="画质", note="说明")
        self.run_mod("profile-edit", profile="日常", mode="create")
        self.assertEqual(raw, self.ini.read_bytes())
        data = self.run_mod("profile-export", profile="日常")
        self.assertEqual(["A"], data["enabled"])
        self.run_mod("profile-edit", profile="导入", mode="import", data=data)
        self.run_mod("profile-edit", profile="复制", mode="copy", source="导入")
        self.run_mod("profile-edit", profile="改名", mode="rename", source="复制")
        self.run_mod("profile-edit", profile="改名", mode="delete")
        for name, expected in [("纯净游戏", []), (mods.PREVIOUS, ["A"])]:
            preview = self.run_mod("profile-preview", profile=name)
            self.assertEqual(expected, preview["enabled"])
            self.run_mod("profile", profile=name, token=preview["token"])
        self.assertTrue(self.run_mod("scan")["mods"][0]["favorite"])

    def test_unsupported_archive_and_attack_paths_never_commit(self):
        symlink = zipfile.ZipInfo("romfs/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for index, entries in enumerate([
            [("../escaped", "x")], [("C:/escape", "x")], [("romfs/file:stream", "x")],
            [("RomFS/A", "x"), ("RomFS/a", "y")], [("romfs/NUL.dat", "x")], [(symlink, "../outside")],
            [("atmosphere/contents/0000000000000001/romfs/a", "other game")],
            [("exefs/subsdk9", "unsupported module"), ("romfs/a", "x")],
        ]):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.run_mod("preview", sources=[self.archive(str(index), entries)])
        broken = self.root / "broken.zip"
        broken.write_bytes(b"not a ZIP")
        with self.assertRaisesRegex(ValueError, "ZIP 损坏"):
            self.run_mod("preview", sources=[str(broken)])
        self.assertEqual([], self.run_mod("scan")["mods"])
        self.assertFalse((self.root / "escaped").exists())

    def test_stale_previews_and_incompatible_runtime(self):
        source = self.archive("A", [("romfs/a", "v1")])
        preview = self.run_mod("preview", sources=[source], replace=True)
        self.archive("A", [("romfs/a", "v2")])
        with self.assertRaisesRegex(ValueError, "重新预览"):
            self.run_mod("install", sources=[source], token=preview["token"], replace=True)
        self.exe.write_bytes(b"unknown")
        self.assertFalse(self.run_mod("scan")["writable"])
        with self.assertRaisesRegex(ValueError, "可写"):
            self.run_mod("install", sources=[source], token=preview["token"], replace=True)

    def test_cancelled_preview_leaves_no_partial_files(self):
        source = self.archive("A", [("romfs/a", "v1")])
        def cancelled():
            raise InterruptedError("cancelled")
        center = mods.Center(self.ctx, self.idle, cancelled, lambda _: "1.4.3")
        with self.assertRaises(InterruptedError):
            center.dispatch(dict(action="mods-preview", sources=[source]))
        self.assertFalse((self.game / "mods").exists())
        self.assertFalse(list(center.home.glob("preview-*")))

    def test_batch_install_and_uninstall_recover_on_access_denied(self):
        sources = [self.archive(name, [("romfs/a", name)]) for name in ("A", "B")]
        rename = os.rename
        def fail_install(old, new):
            if old.parent.name == "new" and new == self.game / "mods/B":
                raise PermissionError("occupied")
            rename(old, new)
        with patch.object(mods.os, "rename", side_effect=fail_install), self.assertRaises(PermissionError):
            self.import_mods(*sources)
        self.assertEqual([], self.run_mod("scan")["mods"])
        self.assertFalse(self.run_mod("scan")["pending"])
        self.import_mods(*sources)
        before = self.ini.read_bytes()
        def fail_remove(old, new):
            if old == self.game / "mods/B" and new.parent.name == "before":
                raise PermissionError("access denied")
            rename(old, new)
        with patch.object(mods.os, "rename", side_effect=fail_remove), self.assertRaises(PermissionError):
            self.run_mod("remove", ids=["A", "B"])
        self.assertEqual(["A", "B"], [r["id"] for r in self.run_mod("scan")["mods"]])
        self.assertEqual(before, self.ini.read_bytes())
        self.assertFalse(self.run_mod("scan")["pending"])

    def test_block_running_game_and_external_environment_or_protected_root(self):
        source = self.archive("A", [("romfs/a", "one")])
        self.import_mods(source)
        def not_idle(_):
            raise ValueError("running")
        self.idle = not_idle
        with self.assertRaisesRegex(ValueError, "running"):
            self.run_mod("toggle", ids=["A"], enabled=False)
        with patch.dict(os.environ, {"TOTK_MODS_DISABLED": "A"}):
            scan = self.run_mod("scan")
            self.assertFalse(scan["writable"])
            self.assertFalse(scan["mods"][0]["enabled"])
        self.ini.write_text("mods_dir = profile\n")
        with self.assertRaisesRegex(ValueError, "存档"):
            self.run_mod("scan")

    def test_real_shared_process_guard_contract(self):
        import subprocess
        with patch.object(core.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b'"totk.exe","5"')):
            with self.assertRaisesRegex(ValueError, "退出游戏"):
                core.ensure_idle(self.ctx, strict=True)
        with patch.object(core.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"error")):
            with self.assertRaisesRegex(ValueError, "无法确认"):
                core.ensure_idle(self.ctx, strict=True)


if __name__ == "__main__":
    unittest.main()
