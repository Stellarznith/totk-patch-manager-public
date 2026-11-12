"""Exercise the updater against synthetic GitHub responses and Windows fixtures."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / "src/manager"
sys.path.insert(0, str(SOURCE))
import core
import manager_update as update


class Response(io.BytesIO):
    def __init__(self, data, url=""):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="管理器 更新测试 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.app = self.root / "游戏管理器"
        self.app.mkdir()
        self.home = self.app / update.DIRECTORY
        for name in update.FILES:
            (self.app / name).write_bytes(b"old")
        (self.app / update.VERSION).write_bytes(b"1.2.8")
        self.unmanaged = ("settings.json", "totk.ini", "profile/save.dat", "assets.zip",
                          "recipe.zip", "prebuilt/manifest.json", "python/python.exe")
        for name in self.unmanaged:
            path = self.app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"keep")
        self.archive = self.root / "fixture.zip"
        with zipfile.ZipFile(self.archive, "w") as z:
            for name in update.FILES + update.NOTICES:
                z.writestr(name, {update.EXE: b"MZ-test", update.VERSION: b"1.2.9"}.get(name, b"new"))
        self.release = dict(tag_name="v1.2.9", draft=False, prerelease=False, body="测试公告",
                            published_at="2026-11-12T00:00:00Z", assets=[{
                                "name": update.asset_name("1.2.9"), "state": "uploaded",
                                "browser_download_url": "https://github.com/" + update.REPOSITORY
                                + "/releases/download/v1.2.9/" + update.asset_name("1.2.9"),
                                "digest": "sha256:" + self.digest(self.archive.read_bytes()),
                                "size": self.archive.stat().st_size,
                            }])
        self.info = update.update_info(self.app, self.release)

    def digest(self, data):
        return hashlib.sha256(data).hexdigest()

    def copy(self, url, target, sha, size):
        shutil.copyfile(self.archive, target)

    def assert_old(self):
        self.assertEqual((self.app / update.VERSION).read_bytes(), b"1.2.8")
        for name in set(update.FILES) - {update.VERSION}:
            self.assertEqual((self.app / name).read_bytes(), b"old")
        for name in self.unmanaged:
            self.assertEqual((self.app / name).read_bytes(), b"keep")

    def test_release_metadata_and_source_only(self):
        self.assertEqual(self.info["status"], "available")
        self.assertEqual(self.info["notes"], "测试公告")
        self.assertEqual(self.info["published"], "2026-11-12T00:00:00Z")
        self.assertEqual(self.info["size"], self.archive.stat().st_size)
        source = dict(self.release, assets=[{"name": "totk-patch-manager-v1.2.9-source.zip", "state": "uploaded"}])
        self.assertEqual(update.update_info(self.app, source)["status"], "source-only")
        (self.app / update.VERSION).write_bytes(b"1.2.10")
        self.assertEqual(update.update_info(self.app, self.release)["status"], "current")

    def test_stable_and_preview_order(self):
        self.assertLess(update.version_key("1.2.10-beta.2"), update.version_key("1.2.10-beta.10"))
        self.assertLess(update.version_key("1.2.10-rc.1"), update.version_key("1.2.10"))
        preview = dict(self.release, tag_name="v1.2.10-beta.10", prerelease=True)
        drafts = dict(preview, tag_name="v1.2.11", draft=True)
        with patch.object(update.urllib.request, "urlopen", side_effect=lambda req, **_: Response(
                json.dumps([self.release, preview, drafts]).encode(), req.full_url)):
            self.assertEqual(update.fetch_release("preview", lambda: None)["tag_name"], preview["tag_name"])
            with self.assertRaisesRegex(ValueError, "正式版"):
                update.fetch_release("stable", lambda: None)

    def test_metadata_retries_and_rejects_unofficial_host(self):
        with patch.object(update.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")) as read:
            with self.assertRaisesRegex(ValueError, "无法连接 GitHub"):
                update.fetch_release("stable", lambda: None)
            self.assertEqual(read.call_count, 2)
        with patch.object(update.urllib.request, "urlopen", side_effect=lambda *a, **k:
                          Response(b"{}", "https://gh-proxy.com/")):
            with self.assertRaisesRegex(ValueError, "GitHub API"):
                update.fetch_release("stable", lambda: None)
        for changes in ({"digest": None}, {"browser_download_url": "https://example.org/evil.zip"}, {"size": 0}):
            release = dict(self.release, assets=[dict(self.release["assets"][0], **changes)])
            with self.assertRaises(ValueError):
                update.update_info(self.app, release)

    def test_mirror_bad_hash_falls_back_to_github(self):
        content = self.archive.read_bytes()
        bad = b"x" + content[1:]
        with patch.object(core, "NETWORK", "auto"), patch.object(core, "emit"), patch.object(
                core.urllib.request, "urlopen", side_effect=[Response(bad), Response(content)]) as read:
            target = self.root / "mirrored.zip"
            core.download(self.info["download_url"], target, self.info["sha256"], self.info["size"])
        self.assertEqual(target.read_bytes(), content)
        addresses = [call.args[0].full_url for call in read.call_args_list]
        self.assertEqual(addresses, [core.MIRROR + self.info["download_url"], self.info["download_url"]])

    def test_interrupted_download_and_wrong_hash_leave_old_version(self):
        with patch.object(core, "NETWORK", "mirror"), patch.object(core, "emit"), patch.object(
                core.urllib.request, "urlopen", return_value=Response(b"partial")):
            with self.assertRaisesRegex(RuntimeError, "下载失败"):
                update.prepare(self.app, self.info, core.download)
        with self.assertRaisesRegex(ValueError, "校验失败"):
            update.prepare(self.app, dict(self.info, sha256="0" * 64), self.copy)
        self.assertFalse((self.home / "ready.json").exists())
        self.assert_old()

    def test_rejects_unexpected_asset_member(self):
        with zipfile.ZipFile(self.archive, "a") as z:
            z.writestr("../profile/save.dat", b"bad")
        with self.assertRaisesRegex(ValueError, "未知"):
            update.payload(self.archive, "1.2.9")
        self.assert_old()

    def test_replaces_only_manager_files_after_confirmation(self):
        update.prepare(self.app, self.info, self.copy)
        self.assert_old()
        update.install_ready(self.app, self.home, "token")
        self.assertEqual((self.app / update.VERSION).read_bytes(), b"1.2.9")
        update.finish_trial(self.app, self.home, confirmed=True)
        self.assertFalse((self.home / "ready.json").exists())
        self.assertFalse((self.home / "trial.json").exists())
        for name in self.unmanaged:
            self.assertEqual((self.app / name).read_bytes(), b"keep")

    def test_failed_startup_rolls_back(self):
        update.prepare(self.app, self.info, self.copy)
        update.install_ready(self.app, self.home, "token")
        self.assertFalse(update.startup_confirmed(self.home, "token"))
        update.finish_trial(self.app, self.home, confirmed=False)
        self.assert_old()

    @unittest.skipUnless(os.name == "nt", "Windows executable startup")
    def test_invalid_windows_executable_recovers_saved_version(self):
        update.prepare(self.app, self.info, self.copy)
        update.install_ready(self.app, self.home, "token")
        with self.assertRaises(OSError):
            update.start_trial(self.app, self.home, "token")
        update.finish_trial(self.app, self.home, confirmed=False)
        self.assert_old()

    def test_occupied_file_rolls_back_partial_install(self):
        update.prepare(self.app, self.info, self.copy)
        original = update.manager_install.replace_file

        def replace(source, target, duplicate=None):
            if target.name == "manager_update.py" and source.read_bytes() == b"new":
                raise PermissionError("file in use")
            return original(source, target, duplicate)

        with patch.object(update.manager_install, "replace_file", side_effect=replace):
            with self.assertRaises(PermissionError):
                update.install_ready(self.app, self.home, "token")
        update.finish_trial(self.app, self.home, confirmed=False)
        self.assert_old()

    @unittest.skipUnless(os.name == "nt", "Windows file sharing")
    def test_windows_occupied_file_is_detected_before_replacement(self):
        update.prepare(self.app, self.info, self.copy)
        with (self.app / update.EXE).open("rb"):
            with self.assertRaisesRegex(OSError, "无法替换"):
                update.install_ready(self.app, self.home, "token")
        self.assertFalse((self.home / "trial.json").exists())
        self.assert_old()


if __name__ == "__main__":
    unittest.main()
