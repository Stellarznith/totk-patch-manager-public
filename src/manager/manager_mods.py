"""Manage the same folders and INI settings as TotK Recomp's F1 Mods tab."""
from datetime import datetime
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
import zipfile

import manager_install as storage

TITLE = "0100F2C0115B6000"
KEYS = ("mods_order", "mods_disabled")
PRESETS = {"纯净游戏": None, "PS5 手柄": "手柄", "画质增强": "画质"}
PREVIOUS = "上次正常组合"
ROOTS = ((), ("atmosphere", "contents", TITLE), ("contents", TITLE), (TITLE,))
SETTING = re.compile(r"^\s*(?:totk_)?(mods_order|mods_disabled|mods_dir)\s*=\s*(.*?)\s*$", re.I)
WINDOWS_DEVICE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)
READ_ONLY = {"mods-scan", "mods-preview", "mods-profile-preview", "mods-profile-export"}


def safe_name(name):
    if (not isinstance(name, str) or not name or len(name.encode("utf-8")) > 250
            or name.strip() != name or name.endswith(".") or WINDOWS_DEVICE.match(name)
            or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)):
        raise ValueError("Windows 或上游无法使用此路径名称：" + repr(name))
    return name


def protected(name):
    name = name.casefold()
    return name.startswith((".", "romfs-patches")) or name in {"ps5_icons_manager", "framegen", "prebuilt"}


def safe_path(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("MOD 路径包含符号链接或重解析点：" + str(part))
    return path


def tree(folder):
    safe_path(folder)
    def fail(error):
        raise error
    for base, dirs, files in os.walk(folder, onerror=fail):
        directory = Path(base)
        for name in (*dirs, *files):
            safe_name(name)
            child = directory / name
            info = child.lstat()
            if getattr(info, "st_file_attributes", 0) & 0x400 or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError("MOD 内含链接或非普通文件：" + str(child))
            if name in files:
                yield child


def fingerprint(folder):
    if not safe_path(folder).is_dir():
        raise ValueError("MOD 目录已不存在：" + str(folder))
    manifest = [[p.relative_to(folder).as_posix(), storage.sha(p)] for p in tree(folder)]
    return hashlib.sha256(json.dumps(sorted(manifest), ensure_ascii=False).encode("utf-8")).hexdigest()


def read_ini(path):
    path = safe_path(path)
    raw = path.read_bytes() if path.exists() else b""
    if b"\0" in raw:
        raise ValueError("totk.ini 不是可识别的 UTF-8 配置。")
    values = {}
    for line in raw.decode("utf-8-sig").splitlines():
        match = SETTING.fullmatch(line)
        if match:
            value = match[2]
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            if value:
                values[match[1].lower()] = value
    return raw, values


def write_ini(path, values):
    raw, current = read_ini(path)
    if all((values.get(key) or None) == current.get(key) for key in KEYS):
        return
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if b"\r\n" in raw else "\n"
    lines = [line for line in text.splitlines(keepends=True)
             if not ((match := SETTING.fullmatch(line)) and match[1].lower() in KEYS)]
    prefix = "".join(lines)
    if prefix and not prefix.endswith("\n"):
        prefix += newline
    if any(values.get(key) for key in KEYS):
        if not re.search(r"^\s*\[mods\]\s*$", prefix, re.I | re.M):
            prefix += "[mods]" + newline
        for key in KEYS:
            if values.get(key):
                prefix += f'{key} = "{values[key]}"' + newline
    data = prefix.encode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
    if data != raw:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".mod-ini-", delete=False) as temporary:
            tmp = Path(temporary.name)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def find_part(folder, kinds):
    for kind in kinds:
        for parent in ROOTS:
            candidate = folder.joinpath(*parent, kind)
            if candidate.is_dir():
                return candidate
    return None


def describe(folder, name):
    files = list(tree(folder))
    romfs = find_part(folder, ("romfs", "romfslite"))
    exefs = find_part(folder, ("exefs",))
    cheats = find_part(folder, ("cheats",))
    code = [p for p in files if (exefs and p.parent == exefs) or
            p.relative_to(folder).as_posix().lower().startswith("atmosphere/exefs_patches/")]
    patches = [p for p in code if p.suffix.lower() in (".ips", ".pchtxt")]
    plugin = any(p.name.lower() == "main" or p.name.lower().startswith("subsdk") for p in code)
    cheat = any(cheats and p.parent == cheats and p.suffix.lower() == ".txt" for p in files)
    assets = {p.relative_to(romfs).as_posix().lower(): p for p in files if romfs and p.is_relative_to(romfs)}
    problem = ""
    if plugin:
        problem = "含上游不支持的 ExeFS 程序模块，整个 MOD 不会加载。"
    elif not (assets or patches or cheat):
        problem = "没有上游可识别的 RomFS、ExeFS 补丁或 cheats 文件。"
    elif any(p.stat().st_size == 0 for p in (*assets.values(), *patches)):
        problem = "含空的资源或补丁文件，请核对来源。"
    meta = {}
    for metadata in ("mod.json", "metadata.json"):
        source = folder / metadata
        if source.is_file():
            if source.stat().st_size > 65536:
                problem = problem or "MOD 元数据过大。"
                break
            try:
                meta = json.loads(source.read_text(encoding="utf-8-sig"))
                if not isinstance(meta, dict):
                    raise ValueError("object required")
            except (ValueError, UnicodeError):
                meta = {}
                problem = problem or "MOD 元数据损坏。"
            break
    def claims(key):
        value = meta.get(key, [])
        return [x for x in value[:64] if isinstance(x, str) and 0 < len(x) <= 250] if isinstance(value, list) else []
    return dict(id=name, path=str(folder), name=str(meta.get("name") or name)[:160],
                version=str(meta.get("version") or "")[:64], author=str(meta.get("author") or "")[:160],
                description=str(meta.get("description") or "")[:3000],
                category=str(meta.get("category") or ("画质" if assets and all(k.startswith("shader/") for k in assets) else "未分类"))[:32],
                dependencies=claims("dependencies"), game_versions=claims("game_versions"),
                recomp_versions=claims("recomp_versions"),
                protected=protected(name) or (folder / ".totk-manager.json").exists(), problem=problem,
                kind=" + ".join(k for k, present in (("RomFS", assets), ("ExeFS", patches or plugin), ("Cheats", cheat)) if present),
                exefs=bool(patches), files=[p.relative_to(folder).as_posix() for p in files], assets=assets)


def unpack(source, destination, cancelled):
    safe_path(source)
    if source.is_dir():
        paths = list(tree(source))
        if len(paths) > 30000 or sum(p.stat().st_size for p in paths) > 16 * 1024**3:
            raise ValueError("MOD 文件夹超出 30,000 文件或 16 GiB 导入上限。")
        for file in paths:
            cancelled()
            output = destination / file.relative_to(source)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, output)
        return
    if source.suffix.lower() != ".zip":
        raise ValueError("支持 ZIP 和 MOD 文件夹。请先解压 7z、RAR 或其他容器。")
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > 30000 or sum(i.file_size for i in infos) > 16 * 1024**3:
                raise ValueError("ZIP 超出 30,000 文件或 16 GiB 解包上限。")
            aliases = {}
            paths = set()
            for item in infos:
                cancelled()
                names = item.orig_filename.removesuffix("/").split("/")
                for part in names:
                    safe_name(part)
                mode = stat.S_IFMT(item.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or item.external_attr & 0x400 or item.flag_bits & 1:
                    raise ValueError("ZIP 含链接、特殊文件或加密内容。")
                for i in range(1, len(names) + 1):
                    spelling = "/".join(names[:i])
                    if aliases.setdefault(spelling.casefold(), spelling) != spelling:
                        raise ValueError("ZIP 含 Windows 大小写冲突。")
                output = destination.joinpath(*names)
                if output in paths:
                    raise ValueError("ZIP 含重复路径。")
                paths.add(output)
                if item.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as inp, output.open("xb") as out:
                        while chunk := inp.read(1024 * 1024):
                            cancelled()
                            out.write(chunk)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError("ZIP 损坏或格式不受支持：" + str(error)) from error


def candidates(directory, fallback, depth=0):
    if depth > 8 or re.fullmatch(r"[0-9a-f]{16}", fallback, re.I) and fallback.upper() != TITLE:
        return []
    if any(find_part(directory, (part,)) for part in ("romfs", "romfslite", "exefs", "cheats")) or (directory / "atmosphere/exefs_patches").is_dir():
        return [(fallback, directory)]
    result = []
    for child in directory.iterdir():
        if child.is_dir() and not child.name.startswith((".", "__MACOSX")):
            result.extend(candidates(child, child.name, depth + 1))
    return result


class Center:
    def __init__(self, ctx, idle, cancelled, identify_game):
        self.ctx, self.idle, self.cancelled = ctx, idle, cancelled
        self.exe = Path(ctx["game"])
        self.root = self.exe.parent
        self.ini = self.root / "totk.ini"
        self.raw, self.config = read_ini(self.ini)
        configured = os.environ.get("TOTK_MODS_DIR", self.config.get("mods_dir", ""))
        self.mods = safe_path(Path(os.path.abspath(self.root / (configured or "mods"))))
        app = Path(__file__).absolute().parent
        protected_paths = [self.root / "profile", self.root / ".totk-extensions"]
        if not self.root.is_relative_to(app):
            protected_paths.append(app)
        protected_paths += [Path(ctx[key]).absolute() for key in ("profile", "romfs", "exefs") if ctx.get(key)]
        relative_parts = self.mods.relative_to(self.root).parts if self.mods.is_relative_to(self.root) else ()
        if (self.root.is_relative_to(self.mods)
                or any(p.is_relative_to(self.mods) or self.mods.is_relative_to(p) for p in protected_paths)
                or any(protected(part) for part in relative_parts)):
            raise ValueError("配置的 MOD 路径与管理器、游戏资源或存档目录重叠。")
        identity = hashlib.sha256(str(self.root).casefold().encode("utf-8")).hexdigest()[:16]
        self.home = safe_path(self.mods.parent / (".totk-mod-center-" + identity))
        self.state = safe_path(self.root / ".totk-extensions/mod-center.json")
        self.store = storage.read(self.state, {}) or {}
        self.order = self.values("mods_order")
        self.off = self.values("mods_disabled")
        self.game_version = identify_game(ctx)
        with self.exe.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as exe:
            self.supported = all(exe.find(x) >= 0 for x in (
                b"TOTK_MODS_ORDER\0", b"TOTK_MODS_DISABLED\0", b"TOTK_MODS_DIR\0", b"mods: %d folders in %s\0"))
        self.override = any("TOTK_" + key.upper() in os.environ for key in (*KEYS, "mods_dir"))

    def values(self, key):
        return list(dict.fromkeys(filter(None, os.environ.get("TOTK_" + key.upper(), self.config.get(key, "")).split("|"))))

    def assert_write(self):
        if not self.supported or self.override:
            raise ValueError("无法确认运行时可写能力，或环境变量覆盖 MOD 配置。")
        self.idle(self.ctx)

    def installed(self):
        rows = {}
        if self.mods.is_dir():
            for folder in sorted(self.mods.iterdir(), key=lambda p: p.name.encode("utf-8")):
                self.cancelled()
                if not folder.is_dir() and not folder.is_symlink():
                    continue
                try:
                    safe_name(folder.name)
                    row = describe(folder, folder.name)
                except (OSError, ValueError) as error:
                    row = dict(id=folder.name, path=str(folder), name=folder.name, kind="", assets={}, files=[],
                               protected=True, problem=str(error))
                note = self.store.get("mods", {}).get(folder.name, {})
                row.update(enabled=folder.name not in self.off, priority=0,
                           category=note.get("category", row.get("category", "未分类")),
                           note=note.get("note", ""), favorite=note.get("favorite", False),
                           source=note.get("source", "已有 MOD 文件夹"), installed=note.get("installed", ""))
                rows[folder.name] = row
        for index, name in enumerate(self.ordered(rows)):
            rows[name]["priority"] = index + 1
            if len(rows) > 512:
                rows[name]["problem"] = rows[name].get("problem") or "超出上游 512 文件夹上限，实际扫描结果待核对。"
        return rows

    def ordered(self, rows, order=None):
        order = self.order if order is None else order
        return [x for x in order if x in rows] + sorted(set(rows) - set(order), key=lambda x: x.encode("utf-8"))

    def problems(self, rows, order=None, off=None):
        off = set(self.off if off is None else off)
        flags, resources = [], {}
        hashes = {}
        def checksum(path):
            if path not in hashes:
                try:
                    hashes[path] = storage.sha(path)
                except OSError:
                    hashes[path] = None
            return hashes[path]
        def add(kind, names, file, reason):
            if len(flags) < 4000:
                flags.append(dict(type=kind, mods=names, file=file, reason=reason))
        for name in self.ordered(rows, order):
            row = rows[name]
            if row.get("problem"):
                add("兼容性", [name], "", row["problem"])
            if row.get("exefs"):
                add("兼容性", [name], "", "ExeFS 需由对应上游判断构建 ID、补丁内容和重编状态。")
            for key, value in (("game_versions", self.game_version), ("recomp_versions", self.ctx["version"].split("+")[0])):
                allowed = row.get(key, [])
                if isinstance(allowed, list) and allowed and value not in allowed:
                    add("兼容性", [name], "", f"声明的 {key} 为 {allowed}，当前为 {value or '未知'}。")
            dependencies = row.get("dependencies")
            if isinstance(dependencies, list):
                for dep in dependencies:
                    if isinstance(dep, str) and (dep not in rows or dep in off):
                        add("依赖", [name], "", f"依赖 {dep} 缺失或已禁用。")
            for path, file in row["assets"].items():
                if path in resources:
                    for earlier, previous in resources[path]:
                        a, b = checksum(previous), checksum(file)
                        if a is None or b is None:
                            add("检查失败", [earlier, name], path, "无法读取资源，请检查文件占用或访问权限。")
                        elif a == b:
                            add("相同", [earlier, name], path, "同路径内容相同。")
                        elif path.startswith("system/resource/resourcesizetable.") and path.endswith(".rsizetable.zs"):
                            add("可共存", [earlier, name], path, "上游合并资源尺寸表。")
                        elif earlier in off or name in off:
                            add("可共存", [earlier, name], path, "至少一个已禁用。若同时启用，优先级高者覆盖。")
                        else:
                            add("冲突", [earlier, name], path, f"内容不同。按加载顺序由 {earlier} 覆盖。")
                resources.setdefault(path, []).append((name, file))
        for folder in self.root.glob("romfs-patches*"):
            manifest = folder / "manifest.tsv"
            if not manifest.is_file() or folder.is_symlink():
                continue
            for line in safe_path(manifest).read_text(encoding="utf-8", errors="replace").splitlines():
                path = line.split("\t", 1)[0].lower()
                for name, candidate in resources.get(path, ()):
                    if name not in off and not rows[name]["protected"]:
                        fields = line.split("\t")
                        if len(fields) >= 3 and checksum(candidate) == fields[2].lower():
                            add("相同", [name], path, f"与 {folder.name} 的内置资源内容相同。")
                        elif path.startswith("system/resource/resourcesizetable.") and path.endswith(".rsizetable.zs"):
                            add("可共存", [name], path, f"如 {folder.name} 被游戏选用，上游会合并资源尺寸表。")
                        else:
                            add("内置覆盖", [name], path, f"如 {folder.name} 被游戏选用，此 MOD 优先覆盖。")
        same = {}
        for name, row in rows.items():
            label = row["name"].casefold()
            if label in same:
                add("重复", [same[label], name], "", "元数据名称相同，请核对是否混装版本。")
            same[label] = name
        return flags

    def loaded(self, rows):
        possible = [self.root / ".totk-extensions/logs" / name for name in ("game.log", "game-safe.log", "game-sdr.log")]
        possible += [self.root / "logs/totk.log", self.root / "totk.log"]
        possible = [p for p in possible if p.is_file()]
        if not possible:
            return
        log = max(possible, key=lambda p: p.stat().st_mtime)
        with safe_path(log).open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 4 * 1024 * 1024))
            text = handle.read().decode("utf-8", "replace")
        revision = self.ini.stat().st_mtime if self.ini.exists() else 0
        for name, row in rows.items():
            match = re.findall(r"mods: " + re.escape(name) + r": (\d+) files served", text)
            state = re.findall(r"mods: " + re.escape(name) + r": (on|off),[^\r\n]+", text)
            stale = "（状态已修改，需重启）" if log.stat().st_mtime < revision else ""
            if match:
                row["loaded"] = f"最近日志：{match[-1]} 个资源" + stale
            elif state:
                row["loaded"] = ("最近日志：已识别，未确认资源加载" if state[-1] == "on" else "最近日志：已识别，禁用") + stale

    def scan(self):
        rows = self.installed()
        self.loaded(rows)
        return dict(folder=str(self.mods), runtime=self.ctx["version"], game_version=self.game_version,
                    writable=self.supported and not self.override,
                    pending=(self.home / "pending.json").exists(), conflicts=self.problems(rows),
                    profiles=list(PRESETS) + ([PREVIOUS] if self.store.get("previous") else []) + sorted(self.store.get("profiles", {})),
                    mods=[{k: v for k, v in rows[n].items() if k not in ("assets", "files")} |
                          {"file_count": len(rows[n]["files"])} for n in self.ordered(rows)])

    def recover(self):
        pending = storage.read(safe_path(self.home / "pending.json"))
        if not pending:
            return
        tx = safe_path(self.home / uuid.UUID(pending["id"]).hex)
        for op in reversed(pending["ops"]):
            name = safe_name(op["name"])
            live, old, new = (safe_path(p) for p in (self.mods / name, tx / "before" / name, tx / "new" / name))
            if op["new"] and not new.exists() and live.exists():
                if fingerprint(live) != op["new"]:
                    raise ValueError("恢复目标已被外部修改：" + name)
                os.rename(live, new)
            if old.exists():
                if live.exists():
                    raise ValueError("恢复目标已被外部修改：" + name)
                os.rename(old, live)
        write_ini(self.ini, pending["before"])
        storage.save(self.state, pending["old_store"])
        storage.save(self.home / "last.json", pending["last"])
        (self.home / "pending.json").unlink()

    def commit(self, tx, ops, order, off, store, config_change=True):
        self.assert_write()
        if read_ini(self.ini)[0] != self.raw:
            raise ValueError("totk.ini 在操作期间发生变化，请刷新重试。")
        def representable(names):
            for name in names:
                try:
                    yield safe_name(name)
                except ValueError:
                    continue
        order, off = (list(representable(names)) for names in (order, off))
        if any(len("|".join(names).encode("utf-8")) > 512 * 256 for names in (order, off)):
            raise ValueError("MOD 顺序或禁用列表过长，超出已核验的上游配置容量。")
        self.mods.mkdir(parents=True, exist_ok=True)
        (tx / "before").mkdir()
        old_keys = {key: self.config.get(key) for key in KEYS}
        new_keys = dict(zip(KEYS, ("|".join(order) or None, "|".join(off) or None))) if config_change else old_keys
        journal = dict(id=tx.name, ops=ops, before=old_keys, after=new_keys, old_store=self.store,
                       last=storage.read(safe_path(self.home / "last.json")))
        storage.save(self.home / "pending.json", journal)
        try:
            for op in ops:
                name = op["name"]
                live = safe_path(self.mods / name)
                if op["old"]:
                    if fingerprint(live) != op["old"]:
                        raise ValueError("MOD 在操作期间发生变化：" + name)
                    os.rename(live, tx / "before" / name)
                if op["new"]:
                    os.rename(tx / "new" / name, live)
            write_ini(self.ini, new_keys)
            storage.save(self.state, store)
            storage.save(self.home / "last.json", {k: v for k, v in journal.items() if k != "last"})
            (self.home / "pending.json").unlink()
        except BaseException:
            self.recover()
            raise

    def import_plan(self, request, tx):
        installed = self.installed()
        folded = {name.casefold(): name for name in installed}
        entries, ops = [], []
        selected_sources = request.get("sources", [])
        if not isinstance(selected_sources, list) or not 1 <= len(selected_sources) <= 50:
            raise ValueError("每次请选择 1 至 50 个 ZIP 或文件夹。")
        for index, raw in enumerate(selected_sources):
            source = Path(os.path.abspath(raw))
            if self.root.is_relative_to(source) or source == self.mods:
                raise ValueError("请选择具体的 MOD 文件夹或 ZIP。")
            incoming = tx / f"input-{index}"
            incoming.mkdir()
            if source.is_dir() and source.name.lower() in {"romfs", "romfslite", "exefs", "cheats"}:
                unpack(source, incoming / source.name.lower(), self.cancelled)
                found = candidates(incoming, source.parent.name)
            else:
                unpack(source, incoming, self.cancelled)
                found = candidates(incoming, source.name if source.is_dir() else source.stem)
            if not found:
                raise ValueError("没有找到 TotK MOD 根目录：" + source.name)
            for name, folder in found:
                safe_name(name)
                existing = folded.get(name.casefold())
                if existing is not None:
                    name = existing
                if protected(name) or any(row["id"].casefold() == name.casefold() for row in entries):
                    raise ValueError("导入包含受保护的名称或多个相同目标：" + name)
                row = describe(folder, name)
                if row["problem"] or row["protected"]:
                    raise ValueError(f"{name}：{row['problem'] or '受保护资源标记'}")
                old = installed.get(name)
                if old and old["protected"]:
                    raise ValueError("不能替换受保护的 MOD：" + name)
                before = fingerprint(self.mods / name) if old else None
                after = fingerprint(folder)
                row.update(source=str(source), destination=str(self.mods / name),
                           decision="相同" if before == after else "替换" if before else "安装",
                           before=before, after=after)
                entries.append(row)
                if before == after or old and not request.get("replace"):
                    continue
                output = tx / "new" / name
                shutil.copytree(folder, output)
                ops.append(dict(name=name, old=before, new=after))
                row.update(path=str(output))
        combined = installed | {row["id"]: row for row in entries if row["id"] in {op["name"] for op in ops}}
        if len(combined) > 512:
            raise ValueError("安装后将超过上游 512 个 MOD 文件夹的限制。")
        potential = self.problems(installed | {row["id"]: row for row in entries})
        proof = [[[x["id"], x["before"], x["after"]] for x in entries], potential]
        state = self.raw + json.dumps(proof, ensure_ascii=False).encode("utf-8")
        token = hashlib.sha256(state).hexdigest()
        return dict(entries=[{k: v for k, v in row.items() if k not in ("assets", "path")} for row in entries],
                    conflicts=potential, token=token), ops

    def snapshot(self, rows, name):
        if name == PREVIOUS:
            return self.store["previous"]
        if name in PRESETS:
            category = PRESETS[name]
            return dict(order=self.ordered(rows), enabled=[n for n in rows if category and rows[n]["category"] == category])
        return self.store.get("profiles", {})[name]

    def current_profile(self, rows):
        order = self.ordered(rows)
        return dict(order=order, enabled=[n for n in order if n not in self.off and not rows[n]["protected"]])

    def apply_profile(self, rows, profile):
        ordered = self.ordered(rows, profile["order"])
        protected_order = iter(n for n in self.ordered(rows) if rows[n]["protected"])
        ordered = [next(protected_order) if rows[n]["protected"] else n for n in ordered]
        ordinary = [n for n in rows if not rows[n]["protected"] and not rows[n]["problem"]]
        off = [n for n in self.off if n not in ordinary] + [n for n in ordinary if n not in profile["enabled"]]
        return ordered, off

    def dispatch(self, request):
        action = request["action"]
        if action == "mods-scan":
            return self.scan()
        if action not in READ_ONLY:
            self.assert_write()
            if (self.home / "pending.json").exists():
                self.recover()
                self.raw, self.config = read_ini(self.ini)
                self.order, self.off = (self.values(key) for key in KEYS)
                self.store = storage.read(self.state, {}) or {}
                if action == "mods-recover":
                    return {}
        if action == "mods-recover":
            return {}
        if action in ("mods-preview", "mods-install"):
            self.home.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.home, prefix="preview-") as directory:
                staging = Path(directory)
                (staging / "new").mkdir()
                result, ops = self.import_plan(request, staging)
                if action == "mods-preview":
                    return result
                if result["token"] != request.get("token"):
                    raise ValueError("源文件或游戏状态已变化，请重新预览。")
                if not ops:
                    return dict(changed=0)
                tx = self.home / uuid.uuid4().hex
                tx.mkdir()
                os.rename(staging / "new", tx / "new")
                store = json.loads(json.dumps(self.store))
                for entry in result["entries"]:
                    if entry["id"] in {op["name"] for op in ops}:
                        store.setdefault("mods", {}).setdefault(entry["id"], {}).update(
                            source=entry["source"], installed=datetime.now().isoformat(timespec="seconds"))
                self.commit(tx, ops, self.order, self.off, store)
                return dict(changed=len(ops))
        rows = self.installed()
        names = list(dict.fromkeys(request.get("ids", [])))
        for name in names:
            safe_name(name)
            if name not in rows or rows[name]["protected"]:
                raise ValueError("所选 MOD 不存在或受保护：" + name)
        order, off = self.ordered(rows), self.off[:]
        store = json.loads(json.dumps(self.store))
        ops, restore = [], None
        if action == "mods-toggle":
            on = request.get("enabled") is True
            if on and any(rows[n]["problem"] for n in names):
                raise ValueError("所选 MOD 含不可用项目，先查看诊断。")
            off = [n for n in off if n not in names] if on else list(dict.fromkeys(off + names))
        elif action == "mods-remove":
            for name in names:
                ops.append(dict(name=name, old=fingerprint(self.mods / name), new=None))
            order, off = ([n for n in values if n not in names] for values in (order, off))
        elif action == "mods-order":
            requested = request["order"]
            if len(requested) != len(order) or set(requested) != set(order):
                raise ValueError("MOD 列表已变化，请刷新后排序。")
            if [n for n in requested if rows[n]["protected"]] != [n for n in order if rows[n]["protected"]]:
                raise ValueError("不能改变受保护组件彼此之间的优先级。")
            order = requested
        elif action == "mods-notes":
            for name in names:
                store.setdefault("mods", {}).setdefault(name, {}).update(
                    note=str(request.get("note", ""))[:1500], category=str(request.get("category") or "未分类")[:32],
                    favorite=request.get("favorite") is True)
        elif action in ("mods-profile-preview", "mods-profile"):
            profile = self.snapshot(rows, request["profile"])
            order, off = self.apply_profile(rows, profile)
            conflicts = self.problems(rows, order, off)
            token = hashlib.sha256(self.raw + json.dumps([order, off, conflicts], ensure_ascii=False).encode("utf-8")).hexdigest()
            if action.endswith("preview"):
                return dict(token=token, conflicts=conflicts,
                            missing=[n for n in profile["enabled"] if n not in rows],
                            enabled=[n for n in order if n not in off])
            if request.get("token") != token:
                raise ValueError("MOD 状态已变化，请重新预览方案。")
        elif action == "mods-profile-edit":
            name = safe_name(request["profile"])
            profiles = store.setdefault("profiles", {})
            if name in PRESETS or name == PREVIOUS:
                raise ValueError("内置方案可复制，不能改名或删除。")
            mode = request["mode"]
            if mode == "delete":
                del profiles[name]
            elif mode in ("copy", "rename", "create", "import"):
                if name in profiles and mode != "create":
                    raise ValueError("同名方案已存在。")
                if mode == "import":
                    data = request["data"]
                    if not isinstance(data, dict) or any(not isinstance(data.get(k), list) or len(data[k]) > 512 for k in ("enabled", "order")):
                        raise ValueError("配置方案应包含有效的 enabled 和 order 列表。")
                    profiles[name] = {k: list(dict.fromkeys(safe_name(n) for n in data[k])) for k in ("enabled", "order")}
                elif mode in ("copy", "rename"):
                    source = request["source"]
                    if mode == "rename" and source not in profiles:
                        raise ValueError("只能重命名自定义方案。")
                    profiles[name] = self.snapshot(rows, source)
                    if mode == "rename":
                        del profiles[source]
                else:
                    profiles[name] = self.current_profile(rows)
            else:
                raise ValueError("未知方案操作。")
        elif action == "mods-profile-export":
            return self.snapshot(rows, request["profile"])
        elif action == "mods-undo":
            last = storage.read(safe_path(self.home / "last.json"))
            if not last:
                raise ValueError("没有可撤销的 MOD 操作。")
            if any(self.config.get(key) != last["after"][key] for key in KEYS):
                raise ValueError("F1 或外部工具已更改 MOD 状态，自动撤销已停止。")
            restore = safe_path(self.home / uuid.UUID(last["id"]).hex / "before")
            for old in last["ops"]:
                name = safe_name(old["name"])
                live = self.mods / name
                if protected(name) or (live / ".totk-manager.json").exists() or (fingerprint(live) if live.is_dir() else None) != old["new"]:
                    raise ValueError("MOD 已被外部修改，停止撤销：" + name)
                if old["old"] and fingerprint(restore / name) != old["old"]:
                    raise ValueError("备份校验失败：" + name)
                ops.append(dict(name=name, old=old["new"], new=old["old"]))
            order, off = (list(filter(None, (last["before"][key] or "").split("|"))) for key in KEYS)
            store = last["old_store"]
        else:
            raise ValueError("未知 MOD 操作。")
        if action in ("mods-toggle", "mods-order", "mods-profile") and (order != self.ordered(rows) or off != self.off):
            store["previous"] = self.current_profile(rows)
        tx = self.home / uuid.uuid4().hex
        (tx / "new").mkdir(parents=True)
        if restore:
            for op in ops:
                if op["new"]:
                    shutil.copytree(restore / op["name"], tx / "new" / op["name"])
        self.commit(tx, ops, order, off, store, config_change=action not in ("mods-notes", "mods-profile-edit"))
        return {}
