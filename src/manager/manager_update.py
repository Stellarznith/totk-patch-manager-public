"""Download and install manager-only releases without touching game payloads."""

from contextlib import contextmanager
import ctypes
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manager_install

REPOSITORY = "Stellarznith/totk-patch-manager-public"
RELEASES = f"https://api.github.com/repos/{REPOSITORY}/releases"
EXE = "王国之泪补丁管理器.exe"
VERSION = "manager-version.txt"
FILES = (EXE, EXE + ".config", "core.py", "manager_install.py", "manager_update.py", VERSION)
NOTICES = ("LICENSE", "THIRD_PARTY_NOTICES.md")
MAX_BYTES = 32 * 1024 * 1024
DIRECTORY = "cache/manager-update"


def version_key(text):
    match = re.fullmatch(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?", text) if isinstance(text, str) and len(text) <= 64 else None
    if not match:
        raise ValueError("管理器版本格式无效。")
    parts = (match[4] or "").split(".") if match[4] else []
    if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in parts):
        raise ValueError("管理器测试版本格式无效。")
    return (*map(int, match.groups()[:3]), not parts,
            tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts))


def fetch_release(channel, cancelled):
    if channel not in ("stable", "preview"):
        raise ValueError("管理器更新渠道无效。")
    url = RELEASES + ("/latest" if channel == "stable" else "?per_page=30")
    for attempt in range(2):
        cancelled()
        try:
            request = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json", "User-Agent": "TotK-Patch-Manager",
            })
            with urllib.request.urlopen(request, timeout=8) as response:
                if response.geturl() != url:
                    raise ValueError("版本信息未来自本项目的 GitHub API。")
                data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError("GitHub 版本信息超过大小限制。")
            release = json.loads(data)
            break
        except (OSError, ValueError, http.client.HTTPException) as error:
            if attempt or isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise ValueError("无法连接 GitHub 查询管理器更新：" + str(error)) from error
    if channel == "stable":
        if not isinstance(release, dict) or release.get("prerelease"):
            raise ValueError("GitHub 未返回正式版。")
        return release
    if not isinstance(release, list):
        raise ValueError("GitHub 未返回测试渠道的发行版列表。")
    candidates = []
    for item in release:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = item.get("tag_name", "")
        if not isinstance(tag, str) or not tag.startswith("v"):
            continue
        try:
            key = version_key(tag[1:])
        except ValueError:
            continue
        if bool(item.get("prerelease")) == (not key[3]):
            candidates.append((key, item))
    if not candidates:
        raise ValueError("GitHub 没有可识别的正式版或测试版。")
    return max(candidates, key=lambda candidate: candidate[0])[1]


def asset_name(version):
    version_key(version)
    return f"totk-patch-manager-v{version}-windows-x64-update.zip"


def update_info(app, release):
    current = (app / VERSION).read_text(encoding="ascii").strip()
    tag = release.get("tag_name", "")
    if not isinstance(tag, str) or not tag.startswith("v") or release.get("draft"):
        raise ValueError("GitHub 未返回管理器版本信息。")
    version = tag[1:]
    key = version_key(version)
    if bool(release.get("prerelease")) != (not key[3]):
        raise ValueError("GitHub 版本与发行渠道不匹配。")
    newer = key > version_key(current)
    release_url = f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
    result = dict(version=version, current=current, status="current",
                  notes=str(release.get("body") or "")[:16000], url=release_url,
                  published=str(release.get("published_at") or ""), newer=newer)
    name = asset_name(version)
    if not isinstance(release.get("assets"), list):
        raise ValueError("GitHub 未返回管理器发行版资产。")
    matches = [a for a in release["assets"] if isinstance(a, dict) and a.get("name") == name and a.get("state") == "uploaded"]
    if not matches:
        return dict(result, status="source-only")
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
    asset, = matches
    sha = asset.get("digest") or ""
    size = asset.get("size")
    if (asset.get("browser_download_url") != expected_url or not isinstance(sha, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", sha)
            or type(size) is not int or not 0 < size <= MAX_BYTES):
        raise ValueError("管理器更新包缺少有效的 GitHub 下载地址、大小或 SHA-256。")
    return dict(result, status="available" if newer else "current", name=name,
                download_url=expected_url, sha256=sha[7:], size=size)


def payload(archive, version):
    """Check the entire ZIP before exposing any of its bytes to the installer."""
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        names = {item.filename for item in entries}
        if (names != set(FILES + NOTICES) or len(entries) != len(names)
                or sum(item.file_size for item in entries) > MAX_BYTES):
            raise ValueError("管理器更新包包含未知、重复或过大的文件。")
        for item in entries:
            if (item.external_attr >> 16) & 0o170000 not in (0, 0o100000):
                raise ValueError("管理器更新包包含非普通文件。")
        result = {name: z.read(name) for name in FILES}
    if result[VERSION].decode("ascii").strip() != version or not result[EXE].startswith(b"MZ"):
        raise ValueError("管理器更新包的版本标记或程序格式不符。")
    return result


def prepare(app, info, download):
    home = app / DIRECTORY
    if home.resolve() != home or any((home / name).exists() for name in ("ready.json", "pending.json", "trial.json")):
        raise ValueError("已有待完成的管理器更新，请重新打开管理器继续。")
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home, prefix="download-") as temporary:
        archive = Path(temporary) / info["name"]
        download(info["download_url"], archive, info["sha256"], info["size"])
        if archive.stat().st_size != info["size"] or manager_install.sha(archive) != info["sha256"]:
            raise ValueError("管理器更新包校验失败。")
        payload(archive, info["version"])
        worker = home / "worker"
        worker.mkdir(exist_ok=True)
        if worker.resolve() != worker:
            raise ValueError("管理器更新工作目录无效。")
        for name in ("manager_update.py", "manager_install.py"):
            shutil.copyfile(app / name, worker / name)
        os.replace(archive, home / "update.zip")
        manager_install.save(home / "ready.json", {key: info[key] for key in ("version", "sha256", "size")})


def check_replaceable(path):
    if os.name != "nt" or not path.is_file():
        return
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    handle = kernel.CreateFileW(str(path), 0x10000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(f"无法替换 {path.name}，请关闭占用它的程序。", ctypes.WinError(ctypes.get_last_error()))
    kernel.CloseHandle(handle)


def install_ready(app, home, token):
    ready = manager_install.read(home / "ready.json")
    if not ready:
        return
    archive = home / "update.zip"
    if archive.stat().st_size != ready["size"] or manager_install.sha(archive) != ready["sha256"]:
        raise ValueError("暂存的管理器更新包校验失败。")
    version = ready["version"]
    current = (app / VERSION).read_text(encoding="ascii").strip()
    if version_key(version) <= version_key(current):
        raise ValueError("暂存版本已过期，请重新检查更新。")
    content = payload(archive, version)
    for name in FILES:
        if (app / name).resolve() != app / name:
            raise ValueError("管理器文件路径无效：" + name)
        check_replaceable(app / name)
    with tempfile.TemporaryDirectory(dir=home, prefix="install-") as temporary:
        files = {}
        for name, data in content.items():
            files[name] = Path(temporary) / name
            files[name].write_bytes(data)
        manager_install.save(home / "trial.json", dict(token=token, version=version))
        manager_install.install(app, home, files, files, dict(manager_version=version))


def finish_trial(app, home, confirmed):
    manager_install.recover(app, home, print)
    trial = manager_install.read(home / "trial.json")
    active = manager_install.read(home / "active.json", {})
    if trial and not confirmed and active.get("manager_version") == trial["version"]:
        manager_install.rollback(app, home)
    (home / "trial.json").unlink(missing_ok=True)
    (home / "ready.json").unlink(missing_ok=True)
    (home / "update.zip").unlink(missing_ok=True)


def startup_confirmed(home, token):
    ack = home / "startup-ok.txt"
    return ack.is_file() and ack.read_text(encoding="ascii") == token


def start_trial(app, home, token):
    child = subprocess.Popen([str(app / EXE), "--updated-manager", token], cwd=app, close_fds=True)
    deadline = time.monotonic() + 30
    while child.poll() is None and time.monotonic() < deadline:
        if startup_confirmed(home, token):
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                return
            break
        time.sleep(0.1)
    if child.poll() is None:
        child.terminate()
        child.wait(timeout=10)
    raise RuntimeError("新版本未能启动，正在恢复旧版。")


@contextmanager
def worker_lock(home):
    import msvcrt
    with (home / "worker.lock").open("a+b") as file:
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)


def wait_for_manager(pid):
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    process = kernel.OpenProcess(0x100000, False, pid)
    if not process:
        if ctypes.get_last_error() == 87:
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(process, 30000) != 0:
            raise TimeoutError("等待旧版管理器退出超时。请关闭窗口后重试。")
    finally:
        kernel.CloseHandle(process)


def run_worker(pid):
    home = Path(__file__).resolve().parent.parent
    app = home.parent.parent
    with worker_lock(home) as acquired:
        if not acquired:
            return
        try:
            wait_for_manager(pid)
        except OSError as error:
            ctypes.windll.user32.MessageBoxW(None, str(error), "管理器更新未完成", 0x10)
            return
        if not any((home / name).exists() for name in ("ready.json", "pending.json", "trial.json")):
            return
        trial = manager_install.read(home / "trial.json")
        restart = True
        confirmed = False
        try:
            if trial or (home / "pending.json").exists():
                confirmed = bool(trial and startup_confirmed(home, trial["token"]))
                finish_trial(app, home, confirmed)
            else:
                token = uuid.uuid4().hex
                install_ready(app, home, token)
                start_trial(app, home, token)
                confirmed = True
                restart = False
                finish_trial(app, home, confirmed)
        except Exception as error:
            try:
                finish_trial(app, home, confirmed)
            except Exception as rollback_error:
                restart = False
                error = RuntimeError(f"{error}\n恢复尚未完成：{rollback_error}")
            with (home / "update.log").open("a", encoding="utf-8") as log:
                log.write(str(error) + "\n")
            ctypes.windll.user32.MessageBoxW(None, str(error), "管理器更新未完成", 0x10)
    if restart:
        subprocess.Popen([str(app / EXE)], cwd=app, close_fds=True)


if __name__ == "__main__":
    run_worker(int(sys.argv[1]))
