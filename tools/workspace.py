"""Temporary workspace selection so a build or test never fills the system drive.

The manager itself keeps every cache under its own application folder, but the
build helpers, the tests and the packaging scripts all unpack hundreds of
megabytes at once.  On this machine the system drive has very little headroom,
so everything defaults to a project-local workspace instead of %TEMP%.
"""

import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "build" / "tmp"
MINIMUM = 4 * 1024**3


def root() -> Path:
    """The workspace root: TOTK_TEMP, else <project>/build/tmp."""
    chosen = Path(os.environ.get("TOTK_TEMP") or DEFAULT)
    chosen.mkdir(parents=True, exist_ok=True)
    return chosen


def free(path: Path) -> int:
    return shutil.disk_usage(path).free


def require(path: Path, needed: int, what: str) -> None:
    """Fail before writing rather than halfway through."""
    available = free(path)
    if available < needed:
        raise RuntimeError(
            "%s 需要约 %.1f GB 可用空间，%s 只剩 %.1f GB；请先清理或把 TOTK_TEMP 指向空间充足的磁盘。"
            % (what, needed / 1024**3, path, available / 1024**3))


def directory(prefix: str):
    """A TemporaryDirectory inside the workspace root."""
    return tempfile.TemporaryDirectory(prefix=prefix, dir=root())


def environment() -> dict:
    """Environment overrides so child processes also stay off the system drive."""
    base = root()
    return {"TEMP": str(base), "TMP": str(base), "TMPDIR": str(base)}


def apply() -> None:
    os.environ.update(environment())


def sweep(keep: int = 0) -> int:
    """Remove leftover workspace entries, newest `keep` excluded.  Returns bytes freed."""
    base = root()
    entries = sorted((p for p in base.iterdir()), key=lambda p: p.stat().st_mtime, reverse=True)
    freed = 0
    for path in entries[keep:]:
        if path.is_dir():
            freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file():
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
    return freed
