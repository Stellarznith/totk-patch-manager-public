"""Direct game/patch updates with verified downloads, journaled backups and official-runtime restoration."""
import argparse
import http.client
import compression.zstd as zstd
import difflib
import hashlib
import json
import mmap
import os
from pathlib import Path
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile

APP = Path(__file__).resolve().parent
sys.path.insert(0,str(APP))
import manager_install
API = 'https://api.github.com/repos/valentderah/totk-pc-recomp'
OWN = 'Stellarznith/totk-patch-manager-public'
OWN_API = 'https://api.github.com/repos/' + OWN
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
CANCEL = None
LOG = None
NETWORK = 'auto'
MIRROR = 'https://gh-proxy.com/'
_MOD_CENTER_SOURCE = None
_MOD_GAME_HASHES = {}
_PS5_ACTION_SOURCE = None
# The game reads one overlay folder, so the installed pack always lands in ICON_FOLDER; the two icon mods
# only replace the six files that carry the artwork, and the shared rest comes from the built-in white set.
ICON_FOLDER = 'romfs-patches-ps5'
ICON_STYLES = ('white', 'stavaas', 'alerion')
ICON_LABELS = {'white': 'Stellarznith', 'stavaas': 'Stavaas', 'alerion': 'Alerion'}
PS5_ACTION_COMMON = 'UI/LayoutArchive/Common.Product.140.Nin_NX_NVN.blarc.zs'
PS5_ACTION_REVISION = 1
# A personal backup is meant to be small enough to hand around; a video-sized mod tree is reported, never truncated.
BACKUP_FILE_LIMIT = 64 * 1024 * 1024
BACKUP_TOTAL_LIMIT = 512 * 1024 * 1024


def ps5_action_backend():
    if _PS5_ACTION_SOURCE is None:
        import manager_ps5_prompt
        return manager_ps5_prompt
    # Keep the fixed file list understood by installed manager updaters.
    from types import ModuleType
    module = ModuleType('manager_ps5_prompt')
    exec(compile(_PS5_ACTION_SOURCE, 'manager_ps5_prompt.py', 'exec'), vars(module))
    return module


def ps5_action_outdated(state):
    return (state.get('ps5') and ICON_FOLDER + '/' + PS5_ACTION_COMMON in state.get('files', {})
            and state.get('ps5_action_revision', 0) < PS5_ACTION_REVISION)


def icon_style(value):
    return value if value in ICON_STYLES else 'white'


def icon_sources(style):
    """Zip prefixes to unpack: the shared white set, then the style's own six files over the top."""
    style = icon_style(style)
    return [ICON_FOLDER + '/'] + ([] if style == 'white' else [ICON_FOLDER + '-' + style + '/'])



def emit(message, **extra):
    row = dict(message=message, **extra)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    if LOG:
        with LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def cancelled():
    if CANCEL and CANCEL.exists():
        raise RuntimeError('操作已取消，安装前数据与备份保留。')


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def usable_archive(path):
    """A download published without a digest is reusable only while it is still a complete archive."""
    path = Path(path)
    if path.suffix.lower() != '.zip' or not path.is_file() or not path.stat().st_size:
        return False
    return zipfile.is_zipfile(path)


def source_tree(root):
    """Locate the extracted upstream checkout; a partial extraction must not count as a cache hit."""
    for p in Path(root).rglob('VERSION'):
        if (p.parent / 'recomp/gen.py').is_file() and (p.parent / 'tools/build_game.py').is_file():
            return p.parent
    return None


def file_version(path):
    if not Path(path).is_file() or os.name != 'nt':
        return ''
    import ctypes as c
    from ctypes import wintypes as w
    library = c.WinDLL('version', use_last_error=True)
    library.GetFileVersionInfoSizeW.argtypes = [w.LPCWSTR, c.POINTER(w.DWORD)]
    library.GetFileVersionInfoSizeW.restype = w.DWORD
    library.GetFileVersionInfoW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p]
    library.VerQueryValueW.argtypes = [c.c_void_p,w.LPCWSTR,c.POINTER(c.c_void_p),c.POINTER(w.UINT)]
    ignored = w.DWORD(); size=library.GetFileVersionInfoSizeW(str(path),c.byref(ignored))
    if not size:
        return '无版本资源'
    data=c.create_string_buffer(size); pointer=c.c_void_p(); length=w.UINT()
    if not library.GetFileVersionInfoW(str(path),0,size,data) or not library.VerQueryValueW(data,'\\',c.byref(pointer),c.byref(length)) or length.value<16:
        return '未知'
    values=c.cast(pointer,c.POINTER(w.DWORD)); ms,ls=values[2],values[3]
    return '%d.%d.%d.%d' % (ms>>16,ms&65535,ls>>16,ls&65535)


def components(ctx):
    state=active(ctx)
    folder=Path(ctx['game']).parent
    features=binary_features(Path(ctx['game']))
    recorded=state.get('mode')=='direct' and state.get('files',{}).get(Path(ctx['game']).name)==digest(ctx['game'])
    damaged=health(ctx).get('missing',[]) if recorded else []
    def component(name,bit):
        enabled=bool(features&bit) if features is not None else bool(state.get(name)) if recorded else None
        if enabled:return '已安装（组件文件异常，请修复）' if damaged else '已安装（已识别当前程序）'
        if enabled is False:return '未安装（当前程序不含此扩展）'
        return '未识别（当前程序无组件标识／安装记录）'
    integrated=(folder/ICON_FOLDER/'manifest.tsv').is_file() and (bool(features&1) if features is not None else recorded and state.get('ps5'))
    icons='需要修复（图标文件异常）' if integrated and any(name.startswith(ICON_FOLDER) for name in damaged) else '已安装（包含在完整 PS5 组件中）' if integrated else '已安装（独立图标 MOD）' if icons_health(ctx)=='正常' else icons_health(ctx)
    styles=sorted(p.name for p in folder.iterdir() if p.is_dir() and p.name.startswith(ICON_FOLDER)) if folder.is_dir() else []
    return dict(ps5=component('ps5',1),graphics=component('graphics',2),
                icons=icons,icon_styles=styles,icon_style=icons_style(ctx),
                detected_features=features,record_matches_executable=recorded,
                dlss=file_version(folder/'nvngx_dlss.dll'),fsr=file_version(folder/'amd_fidelityfx_framegeneration_vk.dll') or file_version(folder/'amd_fidelityfx_vk.dll'),
                dlss_fg=file_version(folder/'framegen/nvngx_dlssg.dll'))


def binary_features(exe):
    with exe.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as data:
        found=set(int(m[1]) for m in re.finditer(rb'TOTK_EXTENSIONS_V1:([0-3]):END',data))
    return found.pop() if len(found)==1 else None


def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).is_file() else default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.new')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def inside(root, relative):
    if '\\' in relative or ':' in relative or relative.startswith('/'):
        raise ValueError('压缩包路径无效。')
    result = (Path(root) / relative).resolve()
    if not result.is_relative_to(Path(root).resolve()):
        raise ValueError('压缩包路径越界。')
    return result


def extract(archive, target):
    with zipfile.ZipFile(archive) as z:
        if sum(x.file_size for x in z.infolist()) > 4 * 1024**3:
            raise ValueError('下载包解压大小异常。')
        for item in z.infolist():
            cancelled()
            p = inside(target, item.filename)
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('下载包包含符号链接。')
            if item.is_dir():
                p.mkdir(parents=True, exist_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as src, p.open('wb') as dst:
                    shutil.copyfileobj(src, dst)


def network_routes(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('github.com','api.github.com','raw.githubusercontent.com') or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('只接受公开的官方 GitHub HTTPS 资源地址。')
    direct=('GitHub 直连',url)
    mirror=('GH-Proxy 加速',MIRROR+url)
    return [direct] if NETWORK=='direct' else [mirror] if NETWORK=='mirror' else [mirror,direct]


def get_json(url):
    errors=[]
    for route,address in network_routes(url):
        cancelled()
        try:
            emit('正在查询：'+route)
            req=urllib.request.Request(address,headers={'User-Agent':'TotK-Extension-Manager/1.1','Accept':'application/vnd.github+json'})
            with urllib.request.urlopen(req,timeout=12) as response:
                data=response.read(8*1024*1024+1)
            if len(data)>8*1024*1024:raise ValueError('接口响应过大。')
            result=json.loads(data)
            if not isinstance(result,dict) or ('message' in result and 'documentation_url' in result):raise ValueError('未返回有效的 GitHub 元数据。')
            if '/releases/' in url and (not re.fullmatch(r'v\d+\.\d+\.\d+',str(result.get('tag_name',''))) or not isinstance(result.get('assets'),list)):
                raise ValueError('版本信息缺失或格式无效。')
            if '/commits/' in url and not re.fullmatch(r'[0-9a-f]{40}',str(result.get('sha',''))):raise ValueError('源码提交信息无效。')
            return result
        except (OSError,ValueError,http.client.HTTPException) as e:
            errors.append(route+': '+str(e));emit(route+'查询失败，尝试可用备用线路。')
    raise RuntimeError('无法查询官方更新。'+'；'.join(errors))


def download(url, target, expected=None, expected_size=None):
    routes=network_routes(url)
    if expected_size is not None and len(routes)==1:routes*=2
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if (target.is_file() and (expected_size is None or target.stat().st_size == expected_size)
            and (digest(target) == expected if expected else usable_archive(target))):
        return target
    part = target.with_suffix(target.suffix + '.part')
    emit('正在下载：' + target.name)
    errors=[]
    for route,address in routes:
        cancelled();last=0;started=time.monotonic()
        try:
            emit('下载线路：'+route)
            req=urllib.request.Request(address,headers={'User-Agent':'TotK-Extension-Manager/1.1'})
            with urllib.request.urlopen(req,timeout=15) as src,part.open('wb') as dst:
                total=int(src.headers.get('Content-Length',0));done=0
                while block:=src.read(1024*1024):
                    cancelled();done+=len(block)
                    if expected_size is not None and done>expected_size:raise ValueError('下载大小超过 GitHub 公布值。')
                    dst.write(block)
                    if time.monotonic()-last>2:
                        message='下载 %.1f MB%s' % (done/1024**2,' / %.1f MB' % (total/1024**2) if total else '')
                        if expected_size:
                            speed=done/max(time.monotonic()-started,.01)/1024**2
                            emit(message+' · %.2f MB/s' % speed,progress=dict(done=done,total=expected_size))
                        else:emit(message)
                        last=time.monotonic()
            if total and done!=total:raise ValueError('下载中断，长度不完整。')
            if expected_size is not None and done!=expected_size:raise ValueError('下载大小与 GitHub 公布值不一致。')
            if expected and digest(part)!=expected:raise ValueError('下载文件 SHA-256 校验失败。')
            if target.suffix.lower()=='.zip' and not zipfile.is_zipfile(part):raise ValueError('下载结果不是 ZIP 压缩包。')
            os.replace(part,target)
            message='下载完成并通过校验：'+target.name
            if expected_size:emit(message,progress=dict(done=done,total=expected_size))
            else:emit(message)
            return target
        except (OSError,ValueError,http.client.HTTPException) as e:
            errors.append(route+': '+str(e));emit(route+'下载失败，尝试可用备用线路。')
    raise RuntimeError('下载失败，未安装。'+'；'.join(errors))


def run(command, cwd=None, env=None):
    cancelled()
    p = subprocess.Popen([str(x) for x in command], cwd=cwd, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, creationflags=FLAGS)
    lines = queue.Queue()
    def reader():
        for line in p.stdout:
            lines.put(line.decode('utf-8', 'replace').rstrip())
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        while p.poll() is None or thread.is_alive() or not lines.empty():
            cancelled()
            try:
                line=lines.get(timeout=.2)
                step=re.match(r'^\[(\d+)/(\d+)\]',line) or re.search(r'gfx_shader: prepare: (\d+) of (\d+)',line)
                if step:emit(line,progress=dict(done=int(step[1]),total=int(step[2])))
                else:emit(line)
            except queue.Empty:
                pass
    except BaseException:
        if p.poll() is None:
            subprocess.run(['taskkill.exe', '/PID', str(p.pid), '/T', '/F'], capture_output=True, creationflags=FLAGS)
            p.wait()
        raise
    if p.returncode:
        raise RuntimeError('步骤失败，退出码 %s；详细原因见上方日志，原游戏未改动。' % p.returncode)


def merge_source_traced(base, ours, upstream):
    """Merge disjoint edits at base-line coordinates, also reporting where every accepted edit landed.

    An insertion (i == j) and a replaced span (a < end) only conflict when the insertion lands strictly inside the
    span: at either end the two edits still write different lines of the base and both survive. Two insertions at one
    point conflict only in ordering, which the sort key settles: the extension's own lines go in front of the lines
    the official version added there. Two replaced spans conflict when they share any base line.

    The trace exists because a recipe edit that no longer finds its anchor is not an error: it is an edit that quietly
    stops applying while the build goes on. Preflight needs the base lines each side touched so it can say which
    extension edit the upstream moved away from, instead of only "the merge produced text".
    """
    if ours == base or ours == upstream:
        return upstream, []
    if upstream == base:
        return ours, []
    b = base.splitlines(keepends=True)
    def changes(value, side):
        lines = value.splitlines(keepends=True)
        return [(i, j, lines[x:y], side) for tag, i, j, x, y in difflib.SequenceMatcher(None, b, lines, autojunk=False).get_opcodes() if tag != 'equal']
    # Insertions sort before a span that starts on the same base line, so a guard the extension adds keeps standing
    # in front of the code the official version added there.
    edits = sorted(changes(ours, 0) + changes(upstream, 1), key=lambda x: (x[0], 0 if x[0] == x[1] else 1, x[1], x[3]))
    accepted = []
    for edit in edits:
        i, j, lines, side = edit
        keep = True
        for a, end, previous, other in accepted:
            if i == j and a == end:
                if i != a:
                    continue                      # insertions at different points never collide
                if lines == previous:
                    keep = False                  # both sides inserted the same lines: write them once
                break                             # same point, different lines: both stay, ours first
            if i == j:
                if a < i < end:
                    raise ValueError('上游与扩展修改了同一段代码（扩展在第 %d 行插入，上游改写了第 %d-%d 行），需更新适配配方；未覆盖任何游戏文件。' % (i, a + 1, end))
                continue
            if a == end:
                if i < a < j:
                    raise ValueError('上游与扩展修改了同一段代码（扩展改写了第 %d-%d 行，上游在第 %d 行插入），需更新适配配方；未覆盖任何游戏文件。' % (i + 1, j, a))
                continue
            if i < end and a < j:
                if i == a and j == end and lines == previous:
                    keep = False                  # both sides wrote the same replacement
                else:
                    raise ValueError('上游与扩展修改了同一段代码（第 %d-%d 行与第 %d-%d 行重叠），需更新适配配方；未覆盖任何游戏文件。' % (i + 1, j, a + 1, end))
                break
        if keep:
            accepted.append(edit)
    result, cursor, trace = [], 0, []
    for i, j, lines, side in accepted:
        result.extend(b[cursor:i]); result.extend(lines); cursor = j
        if side == 0 and lines:
            trace.append(dict(base_line=i + 1, base_end=j, lines=len(lines)))
    result.extend(b[cursor:])
    return ''.join(result), trace


def merge_source(base, ours, upstream):
    return merge_source_traced(base, ours, upstream)[0]


def ini_values(path):
    result = {}
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            m = re.match(r'\s*(\w+)\s*=\s*(.*?)\s*$', line)
            if m:
                result[m[1].upper()] = m[2].strip('"')
    return result


def binary_version(exe):
    with exe.open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
        candidates = set(m[0].decode() for m in re.finditer(rb'(?<=\x00)0\.\d+\.\d+(?:\+[a-f0-9]+)?(?=\x00)', data))
    return next(iter(candidates)) if len(candidates) == 1 else '未知（不猜测）'


def prebuilt_version_status(installed, bundled):
    installed = installed.partition('+')[0]
    if not re.fullmatch(r'\d+\.\d+\.\d+', bundled):
        return 'unavailable'
    if not re.fullmatch(r'\d+\.\d+\.\d+', installed):
        return 'unknown'
    if tuple(map(int, installed.split('.'))) > tuple(map(int, bundled.split('.'))):
        return 'newer'
    return 'compatible'


def context(exe):
    exe = Path(exe).resolve()
    if '.totk-extensions' in exe.parts:
        raise ValueError('请选择原有游戏程序，不要选择管理器生成的运行副本。')
    if not exe.is_file():
        raise ValueError('请选择现有 totk.exe。')
    values = ini_values(exe.parent / 'totk.ini')
    def configured(key):
        value = os.environ.get('TOTK_' + key) or values.get(key)
        return (exe.parent / value).resolve() if value else None
    exefs, romfs = configured('EXEFS'), configured('ROMFS')
    for ancestor in [exe.parent, *exe.parent.parents]:
        for data in [ancestor, ancestor / 'data']:
            e, r = exefs or data / 'exefs', romfs or data / 'romfs'
            if (e / 'main').is_file() and (r / 'romfs.bin').is_file():
                profile = configured('PROFILE_DIR')
                if profile is None:
                    profile = exe.parent / 'profile'
                    legacy = exe.parent.parent / 'profile-v143'
                    if not profile.exists() and legacy.is_dir():
                        profile = legacy
                fonts = configured('SHARED_FONTS')
                if fonts is None:
                    fonts = next((p for p in [data / 'fonts', data / 'shared-fonts'] if p.is_dir()), None)
                return dict(game=str(exe), exefs=str(e), romfs=str(r), profile=str(profile.resolve()),
                            fonts=str(fonts) if fonts else '', version=binary_version(exe))
    raise ValueError('未找到现有 exefs/main 和 romfs/romfs.bin；请选择游戏目录中的实际 totk.exe。')


def discover(root):
    root = Path(root).resolve()
    if root.is_file():
        return [context(root)]
    skip = {'totk-src', '.totk-extensions', '补丁交付', '补丁文件', 'data', 'romfs', 'exefs', 'node_modules', '.git', 'backups', 'cache'}
    found = []
    for folder, dirs, files in os.walk(root):
        depth = len(Path(folder).relative_to(root).parts)
        dirs[:] = [d for d in dirs if depth < 5 and d not in skip and not re.search('备份|backup|^profile|^save|^\\.', d, re.I) and not (Path(folder) / d).is_symlink()]
        if 'totk.exe' in files:
            try:
                found.append(context(Path(folder) / 'totk.exe'))
            except ValueError:
                pass
    return found


def home(ctx):
    return Path(ctx['game']).parent / '.totk-extensions'


def active(ctx):
    return read_json(home(ctx) / 'active.json', {})


def health(ctx):
    state = active(ctx)
    if not state:
        return dict(status='未安装', version='', files=0)
    missing = []
    for rel, wanted in state['files'].items():
        p = inside(Path(ctx['game']).parent if state.get('mode')=='direct' else home(ctx), rel)
        if not p.is_file() or digest(p) != wanted:
            missing.append(rel)
    status='旧独立版本（未直接安装）' if state.get('mode')!='direct' else '需要修复' if missing else '正常'
    return dict(status=status, mode=state.get('mode','legacy'),version=state['version'], missing=missing,
                files=len(state['files']), ps5=state['ps5'], graphics=state['graphics'],
                recipe_outdated=bool(state.get('mode')=='direct' and (state.get('ps5') or state.get('graphics')) and (ps5_action_outdated(state) or state.get('recipe_id')!=recipe_id(state['version'] if state.get('install_method')=='source' else None) or state.get('prebuilt_id') and (APP/'prebuilt/manifest.json').is_file() and state['prebuilt_id']!=digest(APP/'prebuilt/manifest.json'))))


def icons_health(ctx):
    folder = Path(ctx['game']).parent / 'mods/PS5_Icons_Manager'
    metadata = read_json(folder / '.totk-manager.json', {})
    if not metadata:
        return '未安装'
    if any(not inside(folder, p).is_file() or digest(inside(folder, p)) != sha for p, sha in metadata['files'].items()):
        return '需要修复'
    if 'romfs/' + PS5_ACTION_COMMON in metadata['files'] and metadata.get('ps5_action_revision', 0) < PS5_ACTION_REVISION:
        return '有图标修复更新'
    return '正常'


def icons_style(ctx):
    """Which style the standalone mod currently carries; the pack records it when it is written."""
    folder = Path(ctx['game']).parent / 'mods/PS5_Icons_Manager'
    metadata = read_json(folder / '.totk-manager.json', {})
    return icon_style(metadata.get('style')) if metadata else None


def install_icons(ctx, style=None):
    ensure_idle(ctx)
    style = icon_style(style)
    root = home(ctx)
    staged = root / 'icons-staging' / time.strftime('%Y%m%d-%H%M%S')
    staged.mkdir(parents=True, exist_ok=True)
    # A standalone mod is a plain RomFS tree, so it carries one style only. F1 → 操作 → 按键提示 reads the three folders
    # next to romfs-index.bin instead, so they are written as well: picking a style there then takes effect on the next
    # start without coming back to this tool.
    prompt_revision = assets(ctx, staged, True, False, None) or 0
    written = []
    features = binary_features(Path(ctx['game']))
    if features is not None and features & 1:
        for folder in (ICON_FOLDER,) + tuple(ICON_FOLDER + '-' + name for name in ICON_STYLES[1:]):
            if not (staged / folder).is_dir():
                continue
            target = Path(ctx['game']).parent / folder
            if target.exists():
                kept = root / 'icons-history' / ('overlay-' + folder + '-' + time.strftime('%Y%m%d-%H%M%S'))
                kept.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), kept)
            shutil.copytree(staged / folder, target)
            written.append(folder)
    source = staged / (ICON_FOLDER if style == 'white' else ICON_FOLDER + '-' + style)
    pack = staged / 'mod'
    pack.mkdir()
    shutil.move(str(source), pack / 'romfs')
    # The manifest describes the pack; it is not an added guest RomFS file.
    shutil.move(str(pack / 'romfs/manifest.tsv'), pack / 'manifest.tsv')
    files = {p.relative_to(pack).as_posix(): digest(p) for p in pack.rglob('*') if p.is_file()}
    write_json(pack / '.totk-manager.json', dict(files=files, style=style, ps5_action_revision=prompt_revision,
                                                 resource_format='guest RomFS; independent of host build version'))
    dest = Path(ctx['game']).parent / 'mods/PS5_Icons_Manager'
    if dest.exists():
        if not (dest / '.totk-manager.json').is_file():
            raise ValueError('同名 MOD 目录不是管理器创建的，已保留，未覆盖。')
        stamp = time.strftime('%Y%m%d-%H%M%S')
        history = root / 'icons-history' / stamp
        # Two style changes within the same second must not land in one folder, or the older pack is buried inside it.
        taken = 1
        while history.exists():
            history = root / 'icons-history' / (stamp + '-' + str(taken))
            taken += 1
        history.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), history)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(pack), dest)
    except BaseException:
        if 'history' in locals() and not dest.exists():
            shutil.move(str(history), dest)
        raise
    emit('已安装独立 PS5 图标 MOD（' + ICON_LABELS[style] + '）。'
         + ('三种样式已一并写入游戏目录（' + '、'.join(written) + '），在 F1 → 操作 → 按键提示里选好，重启游戏即生效。'
            if written else '当前程序不含 PS5 组件，样式文件夹未写入；装好 PS5 组件后重跑一次即可。')
         + '官方版在 F1 → MOD 中启停。不替换程序，不新增原生菜单或触觉代码。',
         result=dict(icons=icons_health(ctx), style=style, path=str(dest), overlay_folders=written))


def release(tag=None):
    info = get_json(API + ('/releases/tags/v' + tag if tag else '/releases/latest'))
    if info.get('draft') or info.get('prerelease'):
        raise ValueError('只使用官方正式版。')
    asset = next((a for a in info['assets'] if a['name'].endswith('-windows.zip')), None)
    if not asset or not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or ''):
        raise ValueError('官方正式包缺少可验证的下载信息。')
    return info, asset


def component_release(cache,tag):
    path=cache/'release-metadata'/('v'+tag+'.json') if tag else None
    saved=read_json(path,{}) if path else {}
    if not saved:
        bundled=read_json(APP/'prebuilt/manifest.json',{})
        if bundled.get('version')==tag:saved=bundled.get('release',{})
    if saved.get('tag_name')=='v'+str(tag) and not saved.get('draft') and not saved.get('prerelease'):
        asset=next((a for a in saved.get('assets',[]) if a.get('name','').endswith('-windows.zip') and re.fullmatch(r'sha256:[0-9a-f]{64}',a.get('digest',''))),None)
        if asset:
            emit('复用已验证版本信息；检查最新上游请使用“检查官方更新”。')
            return saved,asset
    info,asset=release(tag)
    write_json(cache/'release-metadata'/(info['tag_name']+'.json'),info)
    return info,asset


def toolkit(cache, info, asset):
    tag = info['tag_name']
    target = cache / tag / 'toolkit'
    marker = target / 'verified.json'
    if read_json(marker, {}).get('digest') != asset['digest']:
        archive = download(asset['browser_download_url'], cache / asset['name'], asset['digest'][7:])
        extract(archive, target)
        write_json(marker, dict(digest=asset['digest']))
    kit = next(p.parent for p in target.rglob('VERSION') if (p.parent / 'llvm/bin/clang.exe').is_file())
    version = (kit / 'VERSION').read_text().strip()
    # The source is selected from the actual official binary release identity, not a moving branch.
    commit = version.split('+', 1)[1] if '+' in version else tag
    source_dest = cache / tag / 'source'
    archive = cache / tag / 'source.zip'
    source = source_tree(source_dest)
    saved = read_json(source_dest / 'verified.json', {})
    # GitHub names the extracted folder after the full commit, so an intact tree identifies itself.
    named = re.search(r'totk-pc-recomp-([0-9a-f]{40})', source.name) if source else None
    if source and named and saved.get('commit') != named[1]:
        write_json(source_dest / 'verified.json', dict(commit=named[1], sha256=digest(archive) if usable_archive(archive) else ''))
        saved = read_json(source_dest / 'verified.json', {})
    if source and re.fullmatch(r'[0-9a-f]{40}', str(saved.get('commit', ''))):
        emit('复用已验证的上游源码。')
        return kit, source
    # The archive endpoint accepts the short commit from VERSION, so a rate-limited API is not on the critical path.
    if not (archive.is_file() and usable_archive(archive)):
        download('https://github.com/valentderah/totk-pc-recomp/archive/' + commit + '.zip', archive)
    extract(archive, source_dest)
    source = source_tree(source_dest)
    if source is None:
        raise ValueError('上游源码解压不完整，请重试。')
    named = re.search(r'totk-pc-recomp-([0-9a-f]{40})', source.name)
    resolved = named[1] if named else get_json(API + '/commits/' + commit)['sha']
    write_json(source_dest / 'verified.json', dict(commit=resolved, sha256=digest(archive)))
    return kit, source


def recipe_manifest(archive):
    """Identity carried by the recipe archive itself: the upstream basis it was authored against."""
    with zipfile.ZipFile(archive) as z:
        metadata = json.loads(z.read('manifest.json'))
    return dict(basis=str(metadata.get('basis', '')), commit=str(metadata.get('source_commit', '')),
                files=metadata.get('files', {}))


def recipe_candidates():
    """Every recipe shipped beside the manager or fetched later; a leftover archive is a candidate, never an assumption."""
    result = []
    for path in sorted(APP.glob('recipe*.zip')) + sorted((APP / 'cache' / 'recipes').glob('*.zip')):
        if not usable_archive(path):
            continue
        try:
            item = recipe_manifest(path)
        except (KeyError, ValueError, zipfile.BadZipFile):
            continue
        item['path'] = path
        result.append(item)
    return result


def upstream_identity(source):
    """Version and commit read from the upstream tree that will actually be merged."""
    version_file = Path(source) / 'VERSION'
    version = version_file.read_text(encoding='utf-8').strip().split('+')[0] if version_file.is_file() else ''
    verified = read_json(Path(source).parent / 'verified.json', {})
    commit = str(verified.get('commit', ''))
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        named = re.search(r'totk-pc-recomp-([0-9a-f]{40})', Path(source).name)
        commit = named[1] if named else ''
    return version, commit


def version_tuple(text):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', str(text or '').strip())
    return tuple(int(x) for x in match.groups()) if match else None


def catalog_paths():
    """Where adapters live: beside the manager, plus every adapter the updater verified and kept in the cache."""
    return [APP] + sorted(p for p in (APP / 'cache' / 'recipes').glob('*') if p.is_dir())


def supported_versions():
    """Upstream bases this manager can author, oldest first; a leftover archive is not counted."""
    bases = {item['basis'] for item in recipe_candidates()}
    return sorted((key, text) for text in bases if (key := version_tuple(text)))


def recipe_catalog_urls():
    return OWN_API + '/releases/latest'


def recipe_catalog(cache=None):
    """Adapters published by this project, so a new official basis does not need a whole manager release.

    The catalog rides the manager's own release as an ordinary asset, so it uses the same verified channel the updater
    already trusts: the digest comes from the GitHub API record, every adapter it names carries its own SHA-256, and a
    downloaded archive only becomes usable once its manifest parses — which recipe_candidates() re-checks when the
    adapter is finally selected.
    """
    target = Path(cache or APP / 'cache') / 'recipes' / 'recipe-catalog.json'
    try:
        info = get_json(recipe_catalog_urls())
    except (RuntimeError, ValueError) as error:
        # An offline player keeps the adapters already beside the manager; a missing catalog is not an install blocker.
        emit('无法获取在线适配配方清单，继续使用本机配方：' + str(error))
        return read_json(target, {}).get('recipes', []) if isinstance(read_json(target, {}), dict) else []
    tag = str(info.get('tag_name', ''))
    assets = [a for a in info.get('assets', []) if isinstance(a, dict) and a.get('name') == 'recipe-catalog.json']
    if not assets:
        emit('在线适配配方清单尚未发布，继续使用本机配方。')
        return []
    asset, = assets
    if asset.get('state') != 'uploaded':
        raise ValueError('在线适配配方清单尚未上传完成，请稍后重试。')
    url = asset.get('browser_download_url') or ''
    expected = asset.get('digest') or ''
    size = asset.get('size')
    if (url != 'https://github.com/' + OWN + '/releases/download/' + tag + '/recipe-catalog.json'
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected) or type(size) is not int or not 0 < size <= 2 ** 20):
        raise ValueError('在线适配配方清单缺少有效的下载地址、大小或 SHA-256。')
    download(url, target, expected[7:], size)
    data = read_json(target, {})
    rows = data.get('recipes') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError('在线适配配方清单格式无效。')
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        basis, name, sha, count = row.get('basis'), row.get('asset'), row.get('sha256'), row.get('size')
        if (not isinstance(basis, str) or version_tuple(basis) is None or not isinstance(name, str)
                or not re.fullmatch(r'recipe-[0-9][0-9A-Za-z.\-]*\.zip', name) or not isinstance(sha, str)
                or not re.fullmatch(r'[0-9a-f]{64}', sha) or type(count) is not int or not 0 < count <= 64 * 1024 * 1024):
            continue
        result.append(dict(basis=basis, asset=name, sha256=sha, size=count,
                           url='https://github.com/' + OWN + '/releases/download/' + tag + '/' + name))
    return sorted(result, key=lambda row: version_tuple(row['basis']))


def sync_recipes(cache=None):
    """Fetch the adapters this manager does not already carry, verifying each one before it can be selected."""
    rows = recipe_catalog(cache)
    if not rows:
        return []
    target = Path(cache or APP / 'cache') / 'recipes'
    target.mkdir(parents=True, exist_ok=True)
    have = {}
    for item in recipe_candidates():
        if item['basis']:
            have[item['basis']] = item['path']
    added, kept = [], []
    for row in rows:
        path = target / row['asset']
        if path.is_file() and digest(path) == row['sha256']:
            kept.append(row['basis'])
            continue
        if have.get(row['basis']) and digest(have[row['basis']]) == row['sha256']:
            kept.append(row['basis'])
            continue
        download(row['url'], path, row['sha256'], row['size'])
        try:
            recipe_manifest(path)
        except (KeyError, ValueError, zipfile.BadZipFile) as error:
            path.unlink(missing_ok=True)
            raise ValueError('在线适配配方 %s 无法读取清单：%s' % (row['basis'], error)) from error
        added.append(row['basis'])
    emit('适配配方已更新：新增 %d 份，已有 %d 份。' % (len(added), len(kept)))
    return added


def select_recipe(source, adaptive=True):
    """Pick the adapter that will author this upstream tree's extension source.

    A version number is a label, not a proof: an official release can re-tag the same code, and a development build
    carries the version it was cut from. Matching is therefore two-tiered. An adapter whose basis equals the tree's
    VERSION wins; when the commit also matches it is reported as verified. With no exact basis, the nearest *older*
    verified basis is used as a starting point and its edits are re-located by context — the merge itself decides
    whether the adapter really fits, and it refuses rather than guessing when an edit has no unique home.
    """
    version, commit = upstream_identity(source)
    candidates = recipe_candidates()
    if not candidates:
        raise ValueError('管理器缺少适配配方，请完整解压安装包；未覆盖任何游戏文件。')
    if not version:
        raise ValueError('上游源码缺少 VERSION，无法确认适配配方；未覆盖任何游戏文件。')
    wanted = version_tuple(version)
    exact = [c for c in candidates if c['basis'] == version]
    pinned = [c for c in exact if c['commit'] and commit and c['commit'] == commit]
    if len(pinned) == 1:
        chosen, mode = pinned[0], 'verified'
    elif len(exact) == 1:
        chosen, mode = exact[0], 'basis'
    elif len(exact) > 1:
        raise ValueError('管理器内有多份配方同时适配上游 %s，无法确定使用哪一份；未覆盖任何游戏文件。' % version)
    else:
        if not adaptive or wanted is None:
            known = '、'.join(sorted({c['basis'] for c in candidates if c['basis']})) or '无'
            raise ValueError('当前上游版本 %s 没有对应的适配配方（本机配方适配：%s）。'
                             '可使用「一键适配」按当前版本安全适配，或改用「更新上游并重编」升级到官方最新正式版；未覆盖任何游戏文件。'
                             % (version, known))
        older = [c for c in candidates if version_tuple(c['basis']) and version_tuple(c['basis']) < wanted]
        if not older:
            known = '、'.join(sorted({c['basis'] for c in candidates if c['basis']})) or '无'
            raise ValueError('当前上游版本 %s 低于本机所有适配配方（本机配方适配：%s），无法安全适配。'
                             '使用「更新上游并重编」即可升级到官方最新正式版后再安装扩展；未覆盖任何游戏文件。'
                             % (version, known))
        chosen, mode = max(older, key=lambda c: version_tuple(c['basis'])), 'rebased'
    chosen = dict(chosen)
    chosen['version'], chosen['upstream_commit'], chosen['mode'] = version, commit, mode
    chosen['verified'] = mode == 'verified'
    return chosen


def try_sync(cache):
    """Best-effort adapter refresh before an adaptive install; an offline player must still be able to build."""
    try:
        return sync_recipes(cache)
    except (RuntimeError, ValueError, OSError) as error:
        emit('在线适配配方暂时不可用，继续使用本机配方：' + str(error))
        return []


def recipe_archive(version=None):
    """The recipe whose basis equals this upstream version; a leftover archive for another version is not it."""
    if version:
        for item in recipe_candidates():
            if item['basis'] == version:
                return item['path']
    return APP / 'recipe.zip'


def recipe_merge(source, entry):
    """Run every recipe edit in memory; nothing is written until all of them succeeded."""
    merged, problems = {}, []
    with zipfile.ZipFile(entry['path']) as z:
        for name, item in sorted(entry['files'].items()):
            cancelled()
            base = z.read(item['base']).decode('utf-8')
            modified = z.read(item['modified']).decode('utf-8')
            current = Path(source) / 'runtime' / name
            text = current.read_text(encoding='utf-8') if current.exists() else ''
            try:
                result, trace = merge_source_traced(base, modified, text)
            except ValueError as error:
                problems.append(name + '：' + str(error))
                continue
            lost = lost_edits(base, modified, result)
            if lost:
                problems.append('%s：配方有 %d 处改动在当前上游没有落地（配方第 %s 行附近），扩展功能会静默缺失；需更新适配配方。'
                                % (name, len(lost), '、'.join(str(n) for n in lost[:4])))
                continue
            merged[name] = result
    return merged, problems


def lost_edits(base, ours, result):
    """Recipe edits that did not survive into the merged text, reported at their recipe-side line.

    A merge that raises is loud. A merge that succeeds while an edit no longer matches anything is silent: the build
    finishes, the player installs it, and the feature the edit provided is simply absent. Preflight has to catch that
    case too, so every block the recipe introduced is looked for in the merged text.
    """
    b, lines = base.splitlines(), ours.splitlines()
    present = result.splitlines()
    missing = []
    for tag, i, j, x, y in difflib.SequenceMatcher(None, b, lines, autojunk=False).get_opcodes():
        block = lines[x:y]
        if tag == 'equal' or not block:
            continue
        if not any(present[k:k + len(block)] == block for k in range(len(present) - len(block) + 1)):
            missing.append(x + 1)
    return missing


def recipe_preflight(source, entry):
    """Dry-run every recipe edit before anything is compiled, installed or overwritten."""
    merged, problems = recipe_merge(source, entry)
    return problems, len(merged)


def anchors(text):
    """Normalized text of every line, so an anchor survives reformatting of the upstream source."""
    return [' '.join(line.split()) for line in text.splitlines()]


def locate(block, haystack, k=3):
    """Occurrences of a block of normalized lines in the upstream file, with `k` lines of context.

    A line number is not an identity: the official version inserts and removes lines above the code an extension
    edits, so the same edit lives at a different number in every release. The surrounding text is the identity, and
    the wider the context that matches exactly once, the stronger the claim that this is the same code.
    """
    if not block:
        return []
    size = len(block)
    hits = []
    for start in range(len(haystack) - size + 1):
        if haystack[start:start + size] == block:
            hits.append(start)
    return hits


def carved(original, block, replacement):
    """The edit's own lines, but with upstream's formatting kept on the lines the edit did not touch.

    An opcode carries a few untouched lines along with the changed ones. Writing those back verbatim would undo an
    upstream re-indentation or comment reflow inside the edited span: the build stays correct, but the file becomes a
    mix of two styles, and every later upstream diff on that span turns into noise. Lines the edit did change still come
    from the edit; lines it only carried are taken from the upstream text that now stands at that place.
    """
    result = []
    for tag, i1, i2, x1, x2 in difflib.SequenceMatcher(None, original, block, autojunk=False).get_opcodes():
        result.extend(replacement[i1:i2] if tag == 'equal' else block[x1:x2])
    return result


def rebase_edits(base, ours, upstream, minimum=1):
    """Re-apply the extension's edits onto a newer upstream by matching content, not line numbers.

    A three-way merge works in base coordinates, so an upstream that only shifts code never conflicts with it. What
    does conflict is real: the official version rewrote the very lines an edit touches. Some of those rewrites are
    cosmetic (re-indentation, a comment reflow, a tab), and those are recoverable because the *normalized* content of
    the edited span still exists at exactly one place upstream. Anything else is a genuine semantic collision and has
    to stop the install: guessing which side to keep is how an extension silently disappears.

    Every relocated edit is verified afterwards by lost_edits(), so "success" here means the extension's own lines are
    provably present in the result.
    """
    b, lines = base.splitlines(keepends=True), ours.splitlines(keepends=True)
    up = upstream.splitlines(keepends=True)
    norm_base, norm_up = anchors(base), anchors(upstream)
    moved = []
    for tag, i, j, x, y in difflib.SequenceMatcher(None, b, lines, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        block = lines[x:y]
        span = norm_base[i:j]
        if span:
            points = [start for start in range(len(norm_up) - len(span) + 1) if norm_up[start:start + len(span)] == span]
            if len(points) != 1:
                raise ValueError('扩展改写的第 %d-%d 行在上游%s，需更新适配配方。'
                                 % (i + 1, j, '出现多次、无法定位' if points else '已被官方改写'))
            start = points[0]
            moved.append((start, start + len(span), carved(b[i:j], block, up[start:start + len(span)])))
            continue
        # A pure insertion has no span of its own; its neighbors are what identify the place it belongs to.
        left, right = norm_base[:i], norm_base[i:]
        placed = None
        for take_left in range(min(3, len(left)), 0, -1):
            for take_right in range(min(3, len(right)), -1, -1):
                if take_left + take_right < minimum:
                    continue
                before, after = left[len(left) - take_left:], right[:take_right]
                hits = [at + take_left for at in range(len(norm_up) - take_left - take_right + 1)
                        if norm_up[at:at + take_left] == before
                        and norm_up[at + take_left:at + take_left + take_right] == after]
                if len(hits) == 1:
                    placed = hits[0]
                    break
            if placed is not None:
                break
        if placed is None:
            raise ValueError('扩展在第 %d 行插入的代码找不到唯一插入点（上下文已变化），需更新适配配方。' % (i + 1))
        moved.append((placed, placed, block))
    # Two edits landing on one upstream region would overwrite each other; that is a collision, not an adaptation.
    ordered = sorted(moved)
    for (s1, e1, _), (s2, e2, _) in zip(ordered, ordered[1:]):
        if s2 < e1 and not (s1 == e1 == s2):
            raise ValueError('扩展有两处改动落在上游同一段代码上，需更新适配配方。')
    result = list(up)
    for start, end, block in reversed(ordered):
        result[start:end] = block
    text = ''.join(result)
    absent = lost_edits(base, ours, text)
    if absent:
        raise ValueError('扩展有 %d 处改动没有落到合并结果里（配方第 %s 行附近）。' % (len(absent), '、'.join(str(n) for n in absent[:4])))
    return text, len(moved)


def recipe_preflight_adaptive(source, entry):
    """Preflight that first tries exact three-way merge, then a rebased merge, and reports which one held."""
    merged, problems, rebased = {}, [], []
    with zipfile.ZipFile(entry['path']) as z:
        for name, item in sorted(entry['files'].items()):
            cancelled()
            base = z.read(item['base']).decode('utf-8')
            modified = z.read(item['modified']).decode('utf-8')
            current = Path(source) / 'runtime' / name
            text = current.read_text(encoding='utf-8') if current.exists() else ''
            try:
                result = merge_source(base, modified, text)
            except ValueError as error:
                try:
                    result, count = rebase_edits(base, modified, text)
                except ValueError as second:
                    problems.append('%s：%s' % (name, second))
                    continue
                rebased.append('%s（%d 处编辑按上下文重新定位）' % (name, count))
            merged[name] = result
    return merged, problems, rebased


def recipe(source, destination, ps5, graphics):
    """Plan and apply in one step, for callers that have nothing to preflight separately."""
    entry, merged, rebased = recipe_plan(source)
    recipe_apply(source, destination, merged, entry, rebased, ps5, graphics)


def recipe_plan(source):
    """Decide and dry-run every recipe edit; nothing outside this process is touched."""
    entry = select_recipe(source)
    merged, problems, rebased = recipe_preflight_adaptive(source, entry)
    if problems:
        raise ValueError('适配配方与当前上游不兼容，已在改动任何游戏文件前停止（共 %d 处；配方适配上游 %s，当前上游 %s）：\n%s'
                         % (len(problems), entry['basis'], entry['version'], '\n'.join(problems)))
    if entry['mode'] == 'rebased':
        emit('本机没有 %s 专用配方，已按上下文把 %s 配方的编辑重新定位到当前上游；%d 处编辑全部落地，尚未改动游戏文件。'
             % (entry['version'], entry['basis'], len(rebased)))
    else:
        emit('适配配方预检通过：%d 个文件可自动合并（配方适配上游 %s）。' % (len(merged), entry['basis']))
    return entry, merged, rebased


def recipe_apply(source, destination, merged, entry, rebased, ps5, graphics):
    """Write an already verified merge result, plus every file the extension adds outright."""
    with zipfile.ZipFile(entry['path']) as z:
        metadata = json.loads(z.read('manifest.json'))
    for original in (source/'runtime').rglob('*'):
        if not original.is_file():continue
        name=original.relative_to(source/'runtime').as_posix()
        if name in metadata['files']:continue
        output=inside(destination,name)
        if not output.exists() or output.read_bytes()!=original.read_bytes():
            output.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,output)
    for name in sorted(merged):
        cancelled()
        output = inside(destination, name)
        output.parent.mkdir(parents=True, exist_ok=True)
        encoded=merged[name].encode('utf-8')
        if not output.is_file() or output.read_bytes()!=encoded:output.write_bytes(encoded)
    p = destination / 'cfg.h'
    text = p.read_text(encoding='utf-8')
    text = text.replace('#define TOTK_PATCH_FEATURES 3', '#define TOTK_PATCH_FEATURES ' + str(int(ps5) + 2 * int(graphics)))
    if p.read_text(encoding='utf-8')!=text:p.write_text(text, encoding='utf-8')
    emit('扩展源码接入完成；保留本次官方版本。')


def recipe_id(version=None):
    """Stable identity of the recipe this manager applies for an upstream version.

    A leftover archive never enters the hash: a player who keeps an old recipe beside a new manager must not get a
    different "same version" fingerprint from a player with a clean install, because repair, health and the prepared
    cache would then disagree about what the installed build was made from.
    """
    if version:
        exact = [item['path'] for item in recipe_candidates() if item['basis'] == version]
        if len(exact) == 1:
            archive = exact[0]
        else:
            return 'missing-recipe-' + version
    else:
        archive = APP / 'recipe.zip'
    h=hashlib.sha256()
    with zipfile.ZipFile(archive) as z:
        for name in sorted(z.namelist()):
            h.update(name.encode()); h.update(z.read(name))
    return h.hexdigest()


def assets_id():
    # ZIP timestamps change on repackaging; only payload changes invalidate a component cache.
    h=hashlib.sha256()
    with zipfile.ZipFile(APP/'assets.zip') as z:
        for name in sorted(z.namelist()):
            h.update(name.encode());h.update(z.read(name))
    return h.hexdigest()


def prepared_key(ctx,info,cpu,ps5,graphics,official,entry=None):
    image=Path(ctx['romfs'])/'romfs.bin';stat=image.stat()
    index=Path(ctx['game']).parent/'romfs-index.bin'
    identity=dict(version=info['tag_name'],main=digest(Path(ctx['exefs'])/'main'),cpu=cpu,
                  romfs=str(image.resolve()),romfs_size=stat.st_size,romfs_mtime=stat.st_mtime_ns,
                  index=digest(index) if index.is_file() else '',profile=ctx['profile'],
                  ps5=ps5,graphics=graphics,official=official,
                  recipe=recipe_id(info['tag_name'].removeprefix('v')) if not official else 'official',
                  assets=assets_id() if not official else '')
    if ps5 and not official:
        identity['ps5_action_revision'] = PS5_ACTION_REVISION
    if entry:
        # A rebuild without an exact adapter depends on which adapter was rebased and onto which upstream commit;
        # without this, two different upstream trees would share one cache entry and reuse the wrong binary.
        identity.update(recipe_basis=entry.get('basis',''),recipe_mode=entry.get('mode',''),
                        recipe_commit=entry.get('upstream_commit',''),recipe_archive=digest(entry['path']))
    return hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()


def load_prepared(cache,key):
    package=read_json(cache/'prepared'/(key+'.json'),{})
    if not package:return None
    deployment=inside(cache,package['deployment'])
    for name,wanted in package['files'].items():
        p=inside(deployment,name)
        if not p.is_file() or digest(p)!=wanted:return None
    package['path']=deployment
    return package


def install_prepared(ctx,package,original_hash):
    cancelled();ensure_idle(ctx)
    if digest(ctx['game'])!=original_hash:raise ValueError('准备期间游戏程序发生变化，已停止安装。')
    root=Path(ctx['game']).parent;deployment=Path(package['path'])
    def target_name(name):return Path(ctx['game']).name if name=='totk.exe' else name
    files={target_name(name):inside(deployment,name) for name in package['files']}
    tracked={target_name(name) for name in package['tracked']}
    removals=[];metadata=package['metadata']
    previous=active(ctx)
    if not package.get('cleanup') and previous.get('mode')=='direct' and set(previous.get('files',{}))==tracked and all(previous.get(k)==metadata.get(k) for k in ('version','ps5','graphics','official')) and health(ctx)['status']=='正常' and all((root/name).is_file() and digest(root/name)==package['files']['totk.exe' if name==Path(ctx['game']).name else name] for name in tracked):
        if any(previous.get(k)!=metadata.get(k) for k in ('recipe_id','prebuilt_id','ps5_action_revision')):
            previous.update(metadata);write_json(home(ctx)/'active.json',previous)
        emit('所选组件已安装，程序与资源校验正常，无需重复安装。',result=dict(state=previous,health=health(ctx)))
        return previous
    standalone=root/'mods/PS5_Icons_Manager'
    if metadata['ps5'] and (standalone/'.totk-manager.json').is_file():
        removals=[p.relative_to(root).as_posix() for p in standalone.rglob('*') if p.is_file()]
    if metadata.get('official') and package.get('cleanup'):
        removals.extend(p.relative_to(root).as_posix() for p in cleanup_candidates(ctx))
    backup_personal(ctx)
    state=manager_install.install(root,home(ctx),files,tracked,metadata,emit,removals)
    checked=health(ctx)
    if checked['status']!='正常':raise ValueError('安装后文件校验未通过，请使用回退或修复。')
    emit('安装成功：PS5 '+('已安装' if state['ps5'] else '未安装')+'；图形扩展 '+('已安装' if state['graphics'] else '未安装')+'。上游版本保留 '+state['version']+'。',result=dict(state=state,health=checked))
    return state


def bundled_components():
    manifest=read_json(APP/'prebuilt/manifest.json',{})
    if manifest.get('schema')!=1 or not manifest.get('games'):
        raise ValueError('预编译组件包不完整，请完整解压免编译安装包。本次不会自动下载或重编译。')
    if manifest.get('recipe_id')!=recipe_id():raise ValueError('随包程序与修复配方不匹配，请更新完整预编译包；未改动游戏。')
    if manifest.get('assets_id') and manifest['assets_id']!=assets_id():raise ValueError('随包资源与成品版本不匹配，请完整解压安装包。')
    return manifest


def extract_prebuilt(item,stage):
    archive=inside(APP/'prebuilt',item['path'])
    if not archive.is_file() or digest(archive)!=item['sha256']:raise ValueError('预编译文件缺失或损坏：'+item['path'])
    extract(archive,stage)
    links = item.get('links', {})
    for name, original in links.items():
        cancelled()
        if name not in item['files'] or original in links or item['files'][name] != item['files'].get(original):
            raise ValueError('预编译资源引用清单无效。')
        alias, source = inside(stage, name), inside(stage, original)
        if alias.exists() or not source.is_file():
            raise ValueError('预编译资源引用与包内文件不匹配。')
        alias.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, alias)
        except OSError:
            shutil.copy2(source, alias)
    if not all(inside(stage,name).is_file() and digest(inside(stage,name))==sha for name,sha in item['files'].items()):
        raise ValueError('预编译文件解压后校验失败。')


def install_prebuilt(ctx,ps5,graphics,cache,repair=False):
    ensure_idle(ctx)
    if not ps5 and not graphics:raise ValueError('请选择需要安装的组件；恢复官方原版请使用官方还原。')
    manifest=bundled_components();original_hash=digest(ctx['game'])
    installed=binary_version(Path(ctx['game']))
    bundled=manifest['version']
    status=prebuilt_version_status(installed,bundled)
    if status=='unavailable':
        raise ValueError('无法识别内置成品版本，请完整解压新版管理器。')
    if status=='unknown':
        raise ValueError('无法可靠识别当前程序版本，已停止替换。可在 PS5 页面选择“安装 / 修复图标”。')
    if status=='newer':
        raise ValueError(f'当前游戏程序 {installed} 高于管理器内置的 {bundled}，已阻止降级。'
                         '请使用同版的免编译包，或在“上游与更新”选择“重编当前版本”。'
                         '仅需 PS5 图标时，请在 PS5 页面选择“安装 / 修复图标”。')
    # These hashes identify the guest code the precompiled executable implements, not a host compiler or RomFS size.
    found=[]
    for version,game in manifest['games'].items():
        if all((Path(ctx['exefs'])/name).is_file() and digest(Path(ctx['exefs'])/name)==sha for name,sha in game['exefs'].items()):found.append((version,game))
    if len(found)!=1:raise ValueError('这份游戏资源尚无随包成品；未下载、未重编译。可自行选择“按当前版本重编译”。独立图标仍可单独安装。')
    game_version,game=found[0]
    checker=APP/'prebuilt/hostcheck.exe'
    if not checker.is_file() or digest(checker)!=manifest['hostcheck_sha256']:raise ValueError('随包硬件检测工具损坏，请重新解压。')
    checked=subprocess.run([str(checker)],capture_output=True,encoding='utf-8',creationflags=FLAGS,timeout=30)
    if checked.returncode:raise ValueError('硬件检测未完成，未安装。')
    cpu=json.loads(checked.stdout)
    if not cpu.get('x86_64_v2'):raise ValueError('CPU 未达到此官方基线的 x86-64-v2 要求，未改动游戏。')
    tier='v3' if cpu.get('x86_64_v3') else 'v2';flags=int(ps5)+2*int(graphics)
    selected=game['variants'].get(tier+'-'+str(flags))
    if not selected:raise ValueError('包中缺少所选组件组合，未自动改用编译。')
    identity=digest(APP/'prebuilt/manifest.json');current=active(ctx)
    if repair and current.get('install_method')=='source' and current.get('version')!=manifest['version']:
        raise ValueError('当前是手动构建的其它上游版本；请自行选择“按当前版本重编译”，本次未降级。')
    if current.get('prebuilt_id')==identity and current.get('ps5')==ps5 and current.get('graphics')==graphics and not ps5_action_outdated(current) and health(ctx)['status']=='正常':
        emit('所选内置组件已安装，文件校验正常；无需重复安装。',result=dict(state=current,health=health(ctx)));return current
    emit('安装随包 '+manifest['version']+' 成品，CPU '+tier+'；不联网、不编译、不全量预热着色器。')
    stage=cache/'prebuilt-staging'/str(time.time_ns());stage.mkdir(parents=True)
    extract_prebuilt(game['common'],stage)
    if selected.get('kind')=='xor-zstd':
        extract_prebuilt(game['variants'][selected['base']],stage)
        patch=inside(APP/'prebuilt',selected['path'])
        if not patch.is_file() or digest(patch)!=selected['sha256']:raise ValueError('组件差分文件缺失或损坏。')
        delta=zstd.decompress(patch.read_bytes())
        if len(delta)!=selected['size']:raise ValueError('组件差分长度错误。')
        base=(stage/'totk.exe').read_bytes()
        with (stage/'totk.exe').open('wb') as output:
            for offset in range(0,len(delta),8*1024*1024):
                cancelled();part=delta[offset:offset+8*1024*1024];n=len(part)
                output.write((int.from_bytes(part,'little')^int.from_bytes(base[offset:offset+n].ljust(n,b'\0'),'little')).to_bytes(n,'little'))
        if digest(stage/'totk.exe')!=selected['files']['totk.exe']:raise ValueError('组件差分还原校验失败。')
    else:extract_prebuilt(selected,stage)
    if binary_features(stage/'totk.exe')!=flags:raise ValueError('程序组件标识与选择不一致，未安装。')
    prompt_revision = assets(ctx,stage,ps5,graphics,version=bundled) or 0
    files={p.relative_to(stage).as_posix():digest(p) for p in stage.rglob('*') if p.is_file()}
    # Reuse the player's own index for loader probes. Standard RomFS images need no external index.
    index=Path(ctx['game']).parent/'romfs-index.bin'
    standard=True
    if index.is_file():shutil.copy2(index,stage/'romfs-index.bin')
    else:
        with (Path(ctx['romfs'])/'romfs.bin').open('rb') as source:standard=source.read(8)==struct.pack('<Q',80)
    if not index.is_file() and not standard:
        rows=local_resources(ctx);strings=bytearray();entries=bytearray()
        for name,(at,size) in sorted(rows.items(),key=lambda item:item[0].encode()):
            encoded=name.encode()+b'\0';entries+=struct.pack('<QQII',at,size,len(strings),len(encoded));strings+=encoded
        (stage/'romfs-index.bin').write_bytes(struct.pack('<8sQQ',b'TOTKRFS1',len(rows),24+len(entries))+entries+strings)
        files['romfs-index.bin']=digest(stage/'romfs-index.bin')
    environment=launch_env(ctx);environment.update(TOTK_DLAA='0',TOTK_FRAMEGEN='off',TOTK_DUALSENSE_HD='0',TOTK_PHYSICAL_INPUT='0',TOTK_PROFILE_DIR=str(stage/'probe-profile'))
    for mode in ('--probe-cfg','--probe-loader'):
        run([stage/'totk.exe',mode,'--exefs',ctx['exefs'],'--romfs',ctx['romfs'],'--profile',stage/'probe-profile'],stage,environment)
    metadata=dict(version=manifest['version'],source_commit=manifest['source_commit'],ps5=ps5,graphics=graphics,official=False,game_version=game_version,recipe_id=manifest['recipe_id'],prebuilt_id=identity,install_method='prebuilt',cpu=tier)
    if prompt_revision:
        metadata['ps5_action_revision'] = prompt_revision
    if 'romfs-index.bin' in files:metadata['local_index_regenerated']=True
    package=dict(path=stage,files=files,tracked=list(files),metadata=metadata)
    return install_prepared(ctx,package,original_hash)


def python_tool(kit, script, args):
    # Embedded Python's _pth disables the ordinary script-directory import path.
    wrapper = "import runpy,sys,os;from pathlib import Path;p=Path(sys.argv[1]);sys.path[:0]=[str(p.parent),str(p.parent.parent),str(p.parent.parent/'tools'),str(p.parent.parent/'recomp')];original=Path.read_text;exists=Path.is_file;source=os.environ.get('TOTK_MANAGER_SOURCE_INDEX');local=os.environ.get('TOTK_MANAGER_LOCAL_INDEX');Path.read_text=lambda self,*a,**k: original(Path(local),*a,**k) if source and str(self.resolve())==source else original(self,*a,**k);Path.is_file=lambda self: True if source and str(self.resolve())==source else exists(self);sys.argv=sys.argv[1:];runpy.run_path(str(p),run_name='__main__')"
    return [kit / 'python/python.exe', '-X', 'utf8', '-c', wrapper, script, *args]


def local_resources(ctx):
    """Read local offsets; no developer whole-image hash or fixed image size is required."""
    romfs = Path(ctx['romfs'])
    image = romfs / 'romfs.bin'
    with image.open('rb') as f:
        header = f.read(80)
        if len(header) == 80 and struct.unpack_from('<Q', header)[0] == 80:
            h = struct.unpack('<10Q', header)
            if h[4] > 128*1024**2 or h[8] > 128*1024**2 or max(h[3]+h[4],h[7]+h[8],h[9]) > image.stat().st_size:
                raise ValueError('本地 RomFS 表范围无效。')
            f.seek(h[3]); dirs = f.read(h[4]); f.seek(h[7]); files = f.read(h[8])
            entries, seen_dirs, seen_files = {}, set(), set()
            def name(table, at, size, prefix):
                if at+prefix+size > len(table):
                    raise ValueError('本地 RomFS 名称越界。')
                value = table[at+prefix:at+prefix+size].decode('utf-8')
                if value in ('.','..') or '/' in value or '\\' in value or '\0' in value:
                    raise ValueError('本地 RomFS 名称无效。')
                return value
            pending=[(0,[])]
            while pending:
                at,path=pending.pop()
                if at in seen_dirs or at+24 > len(dirs):
                    raise ValueError('本地 RomFS 目录循环或越界。')
                seen_dirs.add(at)
                _, sibling, child, file_at, _, size = struct.unpack_from('<6I', dirs, at)
                leaf = name(dirs, at, size, 24)
                here = path + ([leaf] if leaf else [])
                while file_at != 0xffffffff:
                    if file_at in seen_files or file_at+32 > len(files):
                        raise ValueError('本地 RomFS 文件循环或越界。')
                    seen_files.add(file_at)
                    _, following, offset, n, _, size = struct.unpack_from('<IIQQII', files, file_at)
                    logical = '/'.join(here+[name(files,file_at,size,32)])
                    if not size or logical in entries or h[9]+offset+n > image.stat().st_size:
                        raise ValueError('本地 RomFS 文件范围无效。')
                    entries[logical] = (h[9]+offset,n); file_at = following
                if sibling != 0xffffffff: pending.append((sibling,path))
                if child != 0xffffffff: pending.append((child,here))
            return entries
    index = Path(ctx['game']).parent / 'romfs-index.bin'
    if index.is_file():
        data = index.read_bytes()
        if len(data) >= 24 and data[:8] == b'TOTKRFS1':
            count, strings = struct.unpack_from('<QQ', data, 8)
            if count <= 1000000 and strings == 24 + count * 24 and strings <= len(data):
                entries = {}
                for i in range(count):
                    at, n, name, size = struct.unpack_from('<QQII', data, 24 + 24 * i)
                    if not size or strings + name + size > len(data) or at + n > (romfs / 'romfs.bin').stat().st_size:
                        raise ValueError('本地索引范围无效。')
                    entries[data[strings + name:strings + name + size - 1].decode()] = (at, n)
                return entries
    manifest = read_json(romfs / 'index.json')
    if manifest:
        return {x['path']: (x['offset'], x['size']) for x in manifest['files']}
    raise ValueError('此安装没有可用的本地资源索引；图标组件无法生成，原游戏未改动。')


def assets(ctx, output, ps5, graphics, style=None, *, version=None):
    """Unpack the packaged resources. style None writes all three icon folders side by side, which is what the
    game's F1 switch reads; a named style writes one folder only, for the standalone icon mod."""
    rows = local_resources(ctx) if ps5 else {}
    prefixes = (ICON_FOLDER + '/', ICON_FOLDER + '-stavaas/', ICON_FOLDER + '-alerion/')
    groups = {prefix: {} for prefix in prefixes}
    latest_fsr = version is None or tuple(map(int, version.removeprefix("v").split("."))) >= (0, 4, 0)
    graphics_items = []
    shaped_items = []
    with zipfile.ZipFile(APP / 'assets.zip') as z, (Path(ctx['romfs']) / 'romfs.bin').open('rb') as blob:
        metadata = json.loads(z.read('manifest.json'))
        for item in metadata:
            name = item['path']
            if 'ps5_shape_base' in item:
                if ps5 and style is None and latest_fsr:
                    shaped_items.append(item)
                continue
            prefix = next((p for p in prefixes if name.startswith(p)), None)
            if prefix is None:
                legacy = name == 'amd_fidelityfx_vk.dll'
                current = name in ['amd_fidelityfx_framegeneration_vk.dll', 'amd_fidelityfx_loader_vk.dll']
                if graphics and not (legacy and latest_fsr or current and not latest_fsr):
                    graphics_items.append(item)
                continue
            if not ps5:
                continue
            data = bytearray(zstd.decompress(z.read(item['entry'])))
            if item['kind'] == 1:
                logical = name.removeprefix(prefix)
                if logical not in rows:
                    raise ValueError('PS5 图标缺少对应原资源：' + logical)
                at, n = rows[logical]
                blob.seek(at); original = blob.read(n)
                if hashlib.sha256(original).hexdigest() != item['source_sha256']:
                    raise ValueError('PS5 图标原资源不同或已被 MOD 修改：' + logical)
                for i in range(min(len(data), len(original))):
                    data[i] ^= original[i]
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('扩展资源生成校验失败：' + name)
            groups[prefix][name.removeprefix(prefix)] = bytes(data)
        for item in graphics_items:
            data = zstd.decompress(z.read(item['entry']))
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('扩展资源生成校验失败：' + item['path'])
            p = inside(output, item['path']); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    if not ps5:
        return
    # The three styles share the 15 message archives and differ only in four fonts, one archive and the manifest.
    base = dict(groups[ICON_FOLDER + '/'])
    for suffix in ('-stavaas', '-alerion'):
        merged = dict(base)
        merged.update(groups[ICON_FOLDER + suffix + '/'])
        groups[ICON_FOLDER + suffix + '/'] = merged
    chosen = ICON_FOLDER + '/' if style is None else ICON_FOLDER + ('/' if icon_style(style) == 'white' else '-' + icon_style(style) + '/')
    for folder in (prefixes if style is None else (chosen,)):
        for logical, data in sorted(groups[folder].items()):
            p = inside(output, folder + logical); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)


    folders = list(prefixes if style is None else (chosen,))
    if shaped_items:
        with zipfile.ZipFile(APP / 'assets.zip') as z:
            install_shaped_ps5(output, groups, z, shaped_items)
        folders.extend(sorted({item['path'].partition('/')[0] for item in shaped_items}))
    folders = [folder.rstrip('/') for folder in folders]
    if any((output / folder / PS5_ACTION_COMMON).is_file() for folder in folders):
        emit('正在用本地 1.4.3 资源校准 PS5 交互按键。')
        with (Path(ctx['romfs']) / 'romfs.bin').open('rb') as blob:
            return ps5_action_backend().repair(output, folders, rows, blob, ICON_FOLDER, cancelled)
    return 0


def install_shaped_ps5(output, groups, archive, items):
    """Complete each shaped overlay from its own layout and the style's verified shared resources."""
    layouts = {}
    written = {}

    def link(source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.samefile(source):
            return
        target.unlink(missing_ok=True)
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)

    for item in items:
        cancelled()
        folder, _, logical = item['path'].partition('/')
        base = item['ps5_shape_base']
        if base + '/' not in groups or not re.fullmatch(re.escape(base) + r'-[0-9]+x[0-9]+', folder):
            raise ValueError('PS5 宽屏资源目录无效：' + folder)
        layouts.setdefault(folder, (base, set()))[1].add(logical)
        target = inside(output, item['path'])
        key = (item['entry'], item['sha256'])
        if key in written:
            link(written[key], target)
        else:
            data = zstd.decompress(archive.read(item['entry']))
            if hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('PS5 宽屏资源校验失败：' + item['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or digest(target) != item['sha256']:
                target.unlink(missing_ok=True)
                target.write_bytes(data)
            written[key] = target
    for folder, (base, replaced) in layouts.items():
        for logical in sorted(groups[base + '/'].keys() - replaced):
            link(inside(output, base + '/' + logical), inside(output, folder + '/' + logical))


def launch_env(ctx):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('TOTK_'):
            env.pop(key)
    env.update(TOTK_EXEFS=ctx['exefs'], TOTK_ROMFS=ctx['romfs'], TOTK_PROFILE_DIR=ctx['profile'],
               TOTK_MODS_DIR=str(Path(ctx['game']).parent / 'mods'))
    if ctx['fonts']:
        env['TOTK_SHARED_FONTS'] = ctx['fonts']
    return env


def ensure_idle(ctx, strict=False):
    # Conservatively protect saves while any TotK game is running; never terminate a player's process.
    for name in set(['totk.exe',Path(ctx['game']).name]):
        r = subprocess.run(['tasklist.exe', '/FI', 'IMAGENAME eq '+name, '/FO', 'CSV', '/NH'], capture_output=True, creationflags=FLAGS)
        if strict and (r.returncode or not r.stdout.strip()):
            raise ValueError('无法确认游戏是否已退出，MOD 操作已暂停。')
        if r.stdout.lstrip().startswith(b'"'):
            raise ValueError('请先正常保存并退出游戏，再进行更新、修复或回退。')


def cleanup_owned(ctx):
    ensure_idle(ctx)
    state=active(ctx)
    if binary_features(Path(ctx['game'])) or (state.get('mode')=='direct' and (state.get('ps5') or state.get('graphics'))):
        raise ValueError('请先还原安装前的游戏，再收纳补丁残留。')
    root=Path(ctx['game']).parent.resolve()
    ini=root/'totk.ini'
    mod=root/'mods/PS5_Icons_Manager'
    marker=mod/'.totk-manager.json'
    if marker.is_file() and (mod.resolve()!=mod or marker.resolve()!=marker):
        raise ValueError('独立图标位于链接路径，未收纳。')
    metadata=read_json(marker)
    owned_mod=isinstance(metadata,dict) and isinstance(metadata.get('files'),dict) and bool(metadata['files'])
    if ini.is_file() and ini.resolve()!=ini:
        raise ValueError('totk.ini 位于链接路径，未更改。')
    raw=ini.read_bytes() if ini.is_file() else b''
    bom=b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b''
    extension_keys={b'ps5_prompts',b'ps5_prompt_style',b'dualsense_hd',b'dualsense_hd_strength',
                    b'dlss_model',b'temporal_engine',b'framegen',b'framegen_multiplier'}
    remaining=[]
    removed=[]
    for line in raw[len(bom):].splitlines(keepends=True):
        match=re.match(rb'[ \t]*(\w+)[ \t]*=[ \t]*(.*)',line)
        key=match[1].lower() if match else b''
        value=match[2].strip().strip(b'"').lower() if match else b''
        if (key in extension_keys or (key==b'prompts' and value==b'ps5')
                or (key==b'dlss_preset' and value in (b'm',b'l'))):
            removed.append(key.decode('ascii'))
        else:
            remaining.append(line)
    if not owned_mod and not removed:
        emit('没有发现可收纳的补丁残留。');return
    backup=home(ctx)/'backups'/('restore-clean-'+str(time.time_ns()))
    backup.mkdir(parents=True)
    if removed:
        (backup/'totk.ini').write_bytes(raw)
        updated=backup/'totk.ini.cleaned'
        updated.write_bytes(bom+b''.join(remaining))
    saved_mod=backup/mod.name
    if owned_mod:
        os.replace(mod,saved_mod)
    try:
        if removed:
            manager_install.replace_file(updated,ini)
    except OSError:
        if owned_mod:
            os.replace(saved_mod,mod)
        raise
    emit('已收纳补丁残留，备份：'+str(backup),
         result=dict(path=str(backup),settings=sorted(set(removed)),standalone_icons=owned_mod))


def reset_render_cache(ctx):
    ensure_idle(ctx)
    root=Path(ctx['game']).parent.resolve();profile=Path(ctx['profile']).resolve()
    candidates=[(root/name,'game/'+name,root) for name in ('shader-cache','shader-cache.bin','shader-programs.bin','shader-answers.bin','shader-prepared.bin','pipeline-cache.bin','pipeline-keys.bin','shader-prepare-skip.txt')]
    candidates += [(profile/name,'profile-generated/'+name,profile) for name in ('resolution/Product.Nin_NX_NVN.HeapDef.byml.zs','camera/PlayerCamera.pack.zs')]
    plan=[]
    backup=home(ctx)/'backups'/('render-cache-'+str(time.time_ns()))
    for source,relative,boundary in candidates:
        if not source.exists():continue
        if source.is_symlink() or source.is_junction():raise ValueError('缓存位置是目录链接，未更改：'+str(source))
        paths=[source,*source.rglob('*')] if source.is_dir() else [source]
        if any(p.is_symlink() or p.is_junction() or not p.resolve().is_relative_to(boundary) for p in paths):
            raise ValueError('缓存目录包含目录链接或越界路径，未更改：'+str(source))
        target=inside(backup,relative)
        if target.resolve().is_relative_to(source.resolve()):raise ValueError('缓存备份位置与来源重叠。')
        plan.append((source,target))
    if not plan:
        emit('没有需要重置的渲染缓存。');return
    write_json(backup/'manifest.json',dict(files=[dict(source=str(source),backup=str(target.relative_to(backup))) for source,target in plan]))
    moved=[]
    try:
        for source,target in plan:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(source),target);moved.append((source,target))
    except BaseException:
        for source,target in reversed(moved):
            source.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(target),source)
        raise
    emit('渲染缓存已收纳到备份，存档和设置未更改。下次进入游戏会重新生成缓存，首次载入可能较慢。',result=dict(backup=str(backup),items=len(moved)))


def backup_personal(ctx):
    ensure_idle(ctx)
    target = home(ctx) / 'backups' / time.strftime('%Y%m%d-%H%M%S')
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target / '存档与配置.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        account=Path(ctx['profile'])/'account.uid'
        if account.is_file():z.write(account,'profile/account.uid')
        ini = Path(ctx['game']).parent / 'totk.ini'
        if ini.is_file():
            z.write(ini, 'official/totk.ini')
        state = active(ctx)
        if state and state.get('mode')!='direct':
            ini = home(ctx) / state['folder'] / 'totk.ini'
            if ini.is_file():
                z.write(ini, 'extension/totk.ini')
        for name in ['save', 'save-backup', 'save-tx']:
            for p in (Path(ctx['profile']) / name).rglob('*'):
                if p.is_file() and not p.is_symlink():
                    z.write(p, 'profile/' + p.relative_to(ctx['profile']).as_posix())
    kept, skipped = backup_extension(ctx, target)
    emit('存档与配置已备份：' + str(target)
         + ('；同时收纳了 %d 个模组与设置文件' % kept if kept else '')
         + ('；%d 个过大或位于链接路径的模组文件未收纳' % skipped if skipped else ''))
    return target


def backup_extension(ctx, target):
    """Mods and per-game settings, which a source rebuild does not otherwise preserve.

    Saves are protected by the install journal, but a mods folder and the extension settings live in the game folder
    and are replaced or regenerated by a rebuild. They are small text/asset trees in practice, so they get their own
    archive with a per-file and a total ceiling: a giant video pack is reported, never silently truncated.
    """
    root = Path(ctx['game']).parent
    limit_file, limit_total = BACKUP_FILE_LIMIT, BACKUP_TOTAL_LIMIT
    kept = skipped = total = 0
    with zipfile.ZipFile(target / '模组与设置.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for folder in ('mods', ICON_FOLDER, ICON_FOLDER + '-stavaas', ICON_FOLDER + '-alerion'):
            base = root / folder
            if not base.is_dir() or base.is_symlink():
                continue
            for p in base.rglob('*'):
                if not p.is_file() or p.is_symlink():
                    continue
                size = p.stat().st_size
                if size > limit_file or total + size > limit_total:
                    skipped += 1
                    continue
                z.write(p, p.relative_to(root).as_posix())
                kept += 1
                total += size
        for name in ('settings.json', 'active.json', 'rollback.json'):
            p = home(ctx) / name
            if p.is_file() and p.stat().st_size <= 4 * 1024 * 1024:
                z.write(p, 'manager/' + name)
    return kept, skipped


def cleanup_candidates(ctx):
    root=Path(ctx['game']).parent
    names={'dxgi.dll','d3d11.dll','d3d12.dll','dinput8.dll','dsound.dll','version.dll','winmm.dll','xinput1_3.dll','xinput1_4.dll','opengl32.dll',
           'reshade.ini','reshade.log','dxvk.conf','amd_fidelityfx_vk.dll',
           'amd_fidelityfx_loader_vk.dll','amd_fidelityfx_framegeneration_vk.dll','dlssg_to_fsr3_amd_is_better.dll','dlssg_to_fsr3.ini',
           '补丁启动器.exe','启动王国之泪-PS5.exe','安装并启动.bat','启动王国之泪.bat'}
    folders={'mods','framegen','reshade-shaders',ICON_FOLDER,ICON_FOLDER+'-stavaas',ICON_FOLDER+'-alerion','optiscaler','dlssg_to_fsr3','补丁文件'}
    names={name.casefold() for name in names}
    result=[]
    for p in root.iterdir():
        if p.is_symlink() or p.is_junction() or p.name.startswith('.'):continue
        if p.is_file() and p.name.lower() in names and p.resolve()!=Path(ctx['game']).resolve():result.append(p)
        elif p.is_dir() and p.name.lower() in folders:
            for folder,dirs,files in os.walk(p,followlinks=False):
                dirs[:]=[name for name in dirs if not (Path(folder)/name).is_symlink() and not (Path(folder)/name).is_junction()]
                result.extend(f for name in files if (f:=Path(folder)/name).is_file() and not f.is_symlink() and f.resolve().is_relative_to(root.resolve()))
    return sorted({p.relative_to(root).as_posix():p for p in result}.values())


def build(ctx, ps5, graphics, info, asset, cache, official=False, cleanup=False):
    import msvcrt
    cache.mkdir(parents=True,exist_ok=True)
    with (cache/'build.lock').open('a+b') as lock:
        try:
            if lock.seek(0,2)==0:lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as e:raise ValueError('另一个构建任务正在使用缓存，请等待完成。') from e
        try:return _build(ctx,ps5,graphics,info,asset,cache,official,cleanup)
        finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


def _build(ctx, ps5, graphics, info, asset, cache, official=False, cleanup=False, entry=None):
    ensure_idle(ctx)
    if not ps5 and not graphics and not official:
        raise ValueError('请至少选择一个扩展组件。')
    official_hash = digest(ctx['game'])
    root=Path(ctx['game']).parent
    # Fail before downloads/builds when the selected program or state directory is locked/read-only.
    preflight=home(ctx)/('write-check-'+str(time.time_ns()))
    preflight.parent.mkdir(parents=True,exist_ok=True)
    try:
        preflight.write_bytes(b'');
        with Path(ctx['game']).open('r+b'):pass
    except OSError as e:raise ValueError('当前目录或游戏程序不可写，请关闭游戏或解除只读后重试。') from e
    finally:preflight.unlink(missing_ok=True)
    write_json(cache/'release-metadata'/(info['tag_name']+'.json'),info)
    kit, source = toolkit(cache, info, asset)
    tag = info['tag_name'].removeprefix('v')
    # The source merge is decided here, before a compiler, a CPU probe or a game file is touched: an upstream this
    # manager cannot adapt has to be refused while the previous install is still untouched and usable.
    prepared = None
    entry = None
    if not official:
        entry, merged, rebased = recipe_plan(source)
        prepared = merged
    source_id = read_json(source.parent / 'verified.json')['commit']
    identity = hashlib.sha256((digest(Path(ctx['exefs']) / 'main') + source_id).encode()).hexdigest()
    key = identity[:16]
    builds = cache / 'builds'
    previous_work = next((p for p in builds.iterdir() if p.is_dir() and read_json(p/'identity.json',{}).get('identity')==identity), None) if builds.is_dir() else None
    work = previous_work or builds / key
    work.mkdir(parents=True, exist_ok=True)
    write_json(work/'identity.json',dict(identity=identity,source_commit=source_id))
    hostcheck=work/'hostcheck.exe'
    run([kit/'llvm/bin/clang.exe','-O1',source/'installer/hostcheck.c','-I',kit/'runtime/deps/include','-o',hostcheck])
    detected=subprocess.run([str(hostcheck)],capture_output=True,encoding='utf-8',creationflags=FLAGS,timeout=30)
    host=json.loads(detected.stdout)
    if not host.get('x86_64_v2'):
        raise ValueError('CPU 未达到官方当前版本的要求，原游戏未改动。')
    cpu='v3' if host.get('x86_64_v3') else 'v2'
    emit('沿用官方硬件检测，构建 CPU 档位：'+cpu)
    ready_key=prepared_key(ctx,info,cpu,ps5,graphics,official,entry)
    package=load_prepared(cache,ready_key)
    if package:
        emit('复用已完成并通过校验的组件包，跳过编译、界面资源和着色器准备。')
        package['cleanup']=cleanup
        return install_prepared(ctx,package,official_hash)
    runtime = work / 'runtime'
    if official:
        runtime.mkdir(exist_ok=True)
        # This is our disposable compiler workspace, never a player's source/game folder.
        for p in runtime.rglob('*'):
            if p.is_file() and not (source/'runtime'/p.relative_to(runtime)).is_file():p.unlink()
        for p in (source/'runtime').rglob('*'):
            if p.is_file():
                target=runtime/p.relative_to(source/'runtime');target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists() or target.read_bytes()!=p.read_bytes():shutil.copyfile(p,target)
        emit('使用未修改的官方运行时源码恢复，不接入本地 PS5 或图形扩展。')
    else:recipe_apply(source, runtime, prepared, entry, [], ps5, graphics)
    main_sha = digest(Path(ctx['exefs']) / 'main')
    candidates = []
    for p in (source / 'tools/data').glob('expected-v*.json'):
        e = read_json(p)
        if e.get('exefs_sha256', {}).get('main') == main_sha:
            candidates.append((p, p.stem.removeprefix('expected-v')))
    if len(candidates) != 1:
        raise ValueError('当前官方源码未识别这份游戏 main；未改动原版或当前扩展。')
    expected, game_version = candidates[0]
    v = 'v' + game_version.replace('.', '')
    output = work / ('output-3' if (work/'output-3').exists() else 'output')
    gen = work / 'generated'
    generated_identity=dict(source_commit=source_id,main_sha256=main_sha)
    if read_json(work/'generated-identity.json')!=generated_identity or not (gen/'layout.json').is_file():
        emit('正在生成当前版本游戏代码，首次运行需要等待；后续会复用本机缓存。')
        run(python_tool(kit, source / 'recomp/gen.py', ['--exefs', ctx['exefs'], '--out', gen, '--patches', source / ('recomp/patches-' + v + '.txt'), '--jobs', '4']), source)
        write_json(work/'generated-identity.json',generated_identity)
    else:
        emit('复用已生成的当前版本游戏代码。')
    profile = kit / ('data/pgo-' + v + '.profdata')
    args = ['--gen', gen, '--out', output, '--runtime', runtime, '--toolchain', kit / 'llvm', '--deps', kit / 'runtime/deps',
            '--ninja', kit / 'bin/ninja.exe', '--version', v, '--expected', expected, '--exefs', ctx['exefs'], '--romfs', ctx['romfs'],
            '--profile', ctx['profile'], '--jobs', '6', '--cpu',cpu,'--no-romfs']
    if profile.is_file():
        args += ['--pgo', profile]
    build_env=None
    if official:
        emit('正在按这份本地资源生成官方程序配套索引，请等待资源校验。')
        rows=local_resources(ctx);image=Path(ctx['romfs'])/'romfs.bin'
        local_index=work/'official-local-index.json'
        write_json(local_index,dict(status='pass',romfs_size=image.stat().st_size,romfs_sha256=digest(image),
                                  files=[dict(path=name,offset=at,size=n) for name,(at,n) in rows.items()]))
        build_env=os.environ.copy();build_env.update(TOTK_MANAGER_SOURCE_INDEX=str((Path(ctx['romfs'])/'index.json').resolve()),TOTK_MANAGER_LOCAL_INDEX=str(local_index))
    emit('正在临时目录准备新版游戏和补丁，验证后再备份并更新所选目录。')
    run(python_tool(kit, source / 'tools/build_game.py', args), source,build_env)
    deployment = work / 'staging' / (tag + '-' + str(int(ps5) + 2 * int(graphics)) + '-' + time.strftime('%Y%m%d-%H%M%S'))
    deployment.mkdir(parents=True)
    for p in output.glob('*'):
        if p.is_file() and p.suffix.lower() in ['.exe', '.dll']:
            shutil.copy2(p, deployment / p.name)
    for p in (kit / 'bin').glob('*.dll'):
        shutil.copy2(p, deployment / p.name)
    prompt_revision = (assets(ctx, deployment, ps5, graphics, version=tag) or 0) if not official else 0
    original_index = Path(ctx['game']).parent / 'romfs-index.bin'
    if official:shutil.copy2(output/'romfs-index.bin',deployment/'romfs-index.bin')
    elif original_index.is_file():shutil.copy2(original_index, deployment / original_index.name)
    shutil.copy2(kit / 'bin/totk_xlate.exe', deployment / 'totk_xlate.exe')
    shutil.copy2(kit / 'bin/totk_xlate.id', deployment / 'totk_xlate.id')
    ini = Path(ctx['game']).parent / 'totk.ini'
    previous = active(ctx)
    if ini.is_file():
        shutil.copy2(ini, deployment / 'totk.ini')
    old_dlss = Path(ctx['game']).parent / 'nvngx_dlss.dll'
    cached_dlss = cache / 'dlss'
    if not official and tag.removeprefix('v') == '0.4.0':
        bundled = bundled_components()['games']['1.4.3']['common']
        archive = inside(APP / 'prebuilt', bundled['path'])
        name = 'nvngx_dlss.dll'
        with zipfile.ZipFile(archive) as package:
            dlss = package.read(name)
        if hashlib.sha256(dlss).hexdigest() != bundled['files'][name]:
            raise ValueError('内置 DLSS 运行库校验失败，请重新解压安装包。')
        (deployment / name).write_bytes(dlss)
    elif old_dlss.is_file() and not official:
        shutil.copy2(old_dlss, deployment / old_dlss.name)
    else:
        # Use the exact NVIDIA DLL specified by this official release's installer.
        text = (source / 'installer/steps.py').read_text(encoding='utf-8')
        url = re.search(r"DLSS_URL\s*=\s*f?['\"]([^'\"]+)", text)
        sha = re.search(r"DLSS_SHA256\s*=\s*['\"]([0-9a-f]{64})", text)
        if url and sha:
            version = re.search(r"DLSS_VERSION\s*=\s*['\"]([^'\"]+)", text)
            address = url[1].replace('{DLSS_VERSION}', version[1] if version else '').replace('{DLSS_DLL}', 'nvngx_dlss.dll')
            # Keep the verified DLL once per hash so a rebuild or a reinstall never downloads it again.
            stored = cached_dlss / (sha[1] + '.dll')
            if old_dlss.is_file() and digest(old_dlss) == sha[1]:
                shutil.copy2(old_dlss, deployment / 'nvngx_dlss.dll')
            elif stored.is_file() and digest(stored) == sha[1]:
                emit('复用已下载的 DLSS 运行库。')
                shutil.copy2(stored, deployment / 'nvngx_dlss.dll')
            else:
                download(address, stored, sha[1])
                shutil.copy2(stored, deployment / 'nvngx_dlss.dll')
    # Reuse shader translations only when the translator identity matches.
    original = Path(ctx['game']).parent
    if (original / 'totk_xlate.id').is_file() and digest(original / 'totk_xlate.id') == digest(deployment / 'totk_xlate.id'):
        for name in ['shader-prepared.bin', 'shader-programs.bin', 'shader-answers.bin', 'shader-cache.bin']:
            if (original / name).is_file():
                shutil.copy2(original / name, deployment / name)
    managed = [p for p in deployment.rglob('*') if p.is_file() and (p.suffix.lower() in ['.exe','.dll','.id'] or any(part.startswith(ICON_FOLDER) for part in p.parts))]
    if official:managed.append(deployment/'romfs-index.bin')
    env = launch_env(ctx)
    env.update(TOTK_PHYSICAL_INPUT='0', TOTK_DUALSENSE_HD='0')
    test_profile = work / 'probe-profile'
    env['TOTK_PROFILE_DIR'] = str(test_profile)
    emit('正在验证待安装程序：配置、加载器、文件系统。')
    for mode in ['--probe-cfg', '--probe-loader', '--probe-fs']:
        run([deployment / 'totk.exe', mode, '--exefs', ctx['exefs'], '--romfs', ctx['romfs'], '--profile', test_profile], deployment, env)
    # Shader preparation and stock prompts come from this exact official source, not the previous host version.
    rows=local_resources(ctx)
    local_index=work/'local-romfs-index.json'
    write_json(local_index,dict(status='pass',romfs_size=(Path(ctx['romfs'])/'romfs.bin').stat().st_size,
                              romfs_sha256='',files=[dict(path=name,offset=at,size=n) for name,(at,n) in rows.items()]))
    env.update(TOTK_MANAGER_SOURCE_INDEX=str((Path(ctx['romfs'])/'index.json').resolve()),TOTK_MANAGER_LOCAL_INDEX=str(local_index),
               TOTK_FRAMEGEN='off',TOTK_DLAA='0',TOTK_XLATE=str(deployment/'totk_xlate.exe'),TOTK_XLATE_ID=str(deployment/'totk_xlate.id'))
    emit('正在准备本次官方版本的界面资源。')
    for number,style in enumerate([None,'xbox','xbox-label','pc'],1):
        emit('准备界面资源 '+str(number)+' / 4',progress=dict(done=number-1,total=4))
        folder=deployment/('romfs-patches'+('-'+style if style else ''))
        args=['--version',v,'--romfs',ctx['romfs'],'--out',folder]
        if style:args+=['--prompts',style]
        run(python_tool(kit,source/'tools/make_romfs_patches.py',args),source,env)
    emit('默认跳过全量着色器预热，保留已有缓存；进入场景时按需生成。')
    managed += [p for p in deployment.glob('romfs-patches*/**/*') if p.is_file() and p not in managed]
    if not official:
        actual=binary_features(deployment/'totk.exe')
        if actual!=int(ps5)+2*int(graphics):raise ValueError('待安装程序的组件标识与所选组件不一致，未开始安装。')
    files={p.relative_to(deployment).as_posix():p for p in managed}
    tracked=list(files)
    # Copy only generated caches, never probe logs, profiles, settings or the user's local index.
    for name in ['shader-prepared.bin','shader-programs.bin','shader-answers.bin','pipeline-cache.bin','pipeline-keys.bin']:
        if (deployment/name).is_file():files[name]=deployment/name
    for p in (deployment/'shader-cache').rglob('*'):
        if p.is_file():files[p.relative_to(deployment).as_posix()]=p
    metadata=dict(version=tag,source_commit=source_id,ps5=ps5,graphics=graphics,official=official,game_version=game_version,recipe_id=recipe_id(tag) if not official else '',install_method='source')
    if prompt_revision:
        metadata['ps5_action_revision'] = prompt_revision
    if official:metadata['local_index_regenerated']=True
    package=dict(deployment=deployment.relative_to(cache).as_posix(),files={name:digest(p) for name,p in files.items()},tracked=tracked,metadata=metadata)
    write_json(cache/'prepared'/(ready_key+'.json'),package)
    package.update(path=deployment,cleanup=cleanup)
    return install_prepared(ctx,package,official_hash)


def launch(ctx, official=False, safe=False, sdr=False):
    ensure_idle(ctx)
    state = active(ctx)
    exe = Path(ctx['game'])
    if state.get('mode')=='direct' and health(ctx)['status'] != '正常':
        raise ValueError('已安装程序或补丁文件损坏，请先检查／修复。')
    env = launch_env(ctx)
    env.pop('TOTK_MODS_DIR')
    for key in ('TOTK_MODS_DIR', 'TOTK_MODS_ORDER', 'TOTK_MODS_DISABLED'):
        if key in os.environ:
            env[key] = os.environ[key]
    if safe:
        env.update(TOTK_DLAA='0',TOTK_FRAMEGEN='off',TOTK_TEMPORAL_ENGINE='dlss',TOTK_VK_RECORDER='0',TOTK_PIPE_PREBUILD='0')
    if sdr:env['TOTK_HDR']='0'
    if state.get('mode')=='direct':
        env['TOTK_XLATE'] = str(exe.parent / 'totk_xlate.exe')
        env['TOTK_XLATE_ID'] = str(exe.parent / 'totk_xlate.id')
        if state.get('ps5'):
            try:
                _, values = mod_backend().read_ini(exe.parent / 'totk.ini')
            except (ValueError, OSError):
                values = {'mods_disabled': ini_values(exe.parent / 'totk.ini').get('MODS_DISABLED', '')}
            disabled = env.get('TOTK_MODS_DISABLED', values.get('mods_disabled', '')).split('|')
            env['TOTK_MODS_DISABLED'] = '|'.join(dict.fromkeys([x for x in disabled if x] + ['PS5_Icons_Manager']))
    log = home(ctx) / 'logs' / ('game-safe.log' if safe else 'game-sdr.log' if sdr else 'game.log')
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('wb') as f:
        p = subprocess.Popen([str(exe), '--exefs', ctx['exefs'], '--romfs', ctx['romfs'], '--profile', ctx['profile']], cwd=exe.parent,
                             env=env, stdout=f, stderr=subprocess.STDOUT, creationflags=FLAGS)
    mode='安全启动（本次关闭时域超分、帧生成和 Vulkan 录制线程，跳过启动预热）' if safe else 'SDR 排查（本次关闭游戏 HDR）' if sdr else '普通启动'
    emit(f'{mode}，正在等待游戏初始化。日志：{log}')
    try:
        code=p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        with log.open('rb') as stream:
            stream.seek(max(0,log.stat().st_size-16384))
            tail=stream.read()
        prewarm=re.findall(rb'gfx_shader: prewarm: (\d+) of (\d+) shaders',tail)
        status=f'游戏进程 {p.pid} 仍在运行。'
        if prewarm and b'gfx: window ' not in tail:
            done,total=(int(n) for n in prewarm[-1])
            status+=f'正在预热着色器（{done} / {total}），窗口会在初始化后显示。安全启动可跳过这一步。'
        else:
            status+='窗口由游戏初始化后显示，若随后退出请导出诊断。'
        emit(status,
             result=dict(pid=p.pid,game=str(exe),safe=safe,sdr=sdr))
    else:
        raise ValueError(f'游戏进程在启动检查期间退出（0x{code & 0xffffffff:08X}）。日志：{log}。'
                         '可用安全启动对照超分与帧生成，或用 SDR 排查对照 HDR。')


def mod_backend():
    if _MOD_CENTER_SOURCE is None:
        import manager_mods
        return manager_mods
    # The existing updater accepts six fixed paths. Ship this module with core.py.
    from types import ModuleType
    module = ModuleType('manager_mods')
    module.__file__ = __file__
    exec(compile(_MOD_CENTER_SOURCE, 'manager_mods.py', 'exec'), vars(module))
    return module


def mod_game_version(ctx):
    games = read_json(APP/'prebuilt/manifest.json', {}).get('games', {})
    for version, game in games.items():
        expected = game.get('exefs', {})
        if expected and all((Path(ctx['exefs']) / name).is_file()
                            and digest(Path(ctx['exefs']) / name) == checksum for name, checksum in expected.items()):
            return version
    main = Path(ctx['exefs']) / 'main'
    if main.is_file():
        return _MOD_GAME_HASHES.get(digest(main), '')
    return ''


def main(request):
    # Independent manager copies must not concurrently activate or repair the same game.
    mutating={'apply','update','repair','rebuild-current','refresh-recipes','prewarm-shaders','rollback','disable','icons','icons-disable','backup','launch','launch-safe','launch-sdr','official','restore-official','language','reset-render-cache','cleanup-owned'}
    action = request['action']
    mods_write = action.startswith('mods-') and action not in {
        'mods-scan', 'mods-preview', 'mods-profile-preview', 'mods-profile-export'}
    if action not in mutating and not mods_write:
        return dispatch(request)
    import msvcrt
    ctx=context(request['game']); path=home(ctx)/'operation.lock'
    if mods_write:
        mod_backend().safe_path(path)
        ensure_idle(ctx, strict=True)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as lock:
        try:
            if lock.seek(0,2)==0:lock.write(b'0');lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as e:
            raise ValueError('另一个管理器正在处理这个游戏，请等待它完成。') from e
        try:
            if (home(ctx)/'pending.json').is_file():
                if mods_write:
                    raise ValueError('有未完成的游戏补丁安装，请先在安装管理中恢复。')
                ensure_idle(ctx);manager_install.recover(Path(ctx['game']).parent,home(ctx),emit)
            return dispatch(request)
        finally:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


def dispatch(request):
    global CANCEL, LOG, NETWORK
    cache = Path(request.get('cache') or APP / 'cache').resolve(); cache.mkdir(parents=True, exist_ok=True)
    CANCEL = Path(request['cancel']) if request.get('cancel') else None
    LOG = cache / 'manager.log'
    NETWORK=request.get('network') or read_json(APP/'settings.json',{}).get('network','auto')
    if NETWORK not in ('auto','mirror','direct'):raise ValueError('下载线路设置无效。')
    action = request['action']
    if action == 'discover':
        emit('目录识别完成。', result=dict(games=discover(request['root'])))
        return
    if action == 'check':
        info, asset = release()
        emit('官方最新正式版：' + info['tag_name'], result=dict(version=info['tag_name'].removeprefix('v'), notes=info['body'], url=info['html_url']))
        return
    if action in ('manager-check','manager-update'):
        import manager_update
        info=manager_update.update_info(APP,manager_update.fetch_release(request.get('channel','stable'),cancelled))
        if action == 'manager-update':
            if (info['status']!='available' or info['version']!=request.get('version')
                    or info['sha256']!=request.get('sha256')):
                raise ValueError('GitHub 上的可安装版本已变化，请重新检查。')
            manager_update.prepare(APP,info,download)
            emit('更新包已校验，退出管理器后开始安装。',result=dict(version=info['version']))
        else:
            emit('GitHub 管理器发行版：'+info['version'],result=info)
        return
    if action in ('recipe-check','recipe-sync'):
        # A third, independent update channel: adapters can gain a new upstream basis without a manager release.
        local = [dict(basis=c['basis'], commit=c['commit'], file=c['path'].name) for c in recipe_candidates()]
        if action == 'recipe-sync':
            added = sync_recipes(cache)
            local = [dict(basis=c['basis'], commit=c['commit'], file=c['path'].name) for c in recipe_candidates()]
            emit('适配配方已更新：新增 %d 份。' % len(added), result=dict(added=added, local=local))
            return
        online = recipe_catalog(cache)
        covered = {c['basis'] for c in recipe_candidates()}
        pending = [row['basis'] for row in online if row['basis'] not in covered]
        emit('本机适配配方：%s。' % ('、'.join(c['basis'] for c in local) or '无')
             + ('可获取的新版本：%s。' % '、'.join(pending) if pending else '在线没有更新的适配配方。'),
             result=dict(local=local, online=[dict(basis=r['basis'], size=r['size']) for r in online], pending=pending))
        return
    ctx = context(request['game'])
    if action.startswith('mods-'):
        result = mod_backend().Center(ctx, lambda target: ensure_idle(target, strict=True), cancelled, mod_game_version).dispatch(request)
        emit('MOD 操作完成。资源变化在下次启动游戏时生效。', result=result)
        return
    if action == 'inspect':
        bundled=read_json(APP/'prebuilt/manifest.json',{}).get('version','')
        compatibility=dict(status=prebuilt_version_status(ctx['version'],bundled),bundled=bundled)
        emit('版本与文件状态检查完成。', result=dict(context=ctx, health=health(ctx), icons=icons_health(ctx),components=components(ctx),language=ini_values(Path(ctx['game']).parent/'totk.ini').get('MENU_LANG','auto'),compatibility=compatibility))
    elif action=='cleanup-scan':
        root=Path(ctx['game']).parent;found=cleanup_candidates(ctx)
        emit('以下文件可在恢复官方时收纳进备份；尚未更改任何文件。',result=dict(files=[p.relative_to(root).as_posix() for p in found],bytes=sum(p.stat().st_size for p in found)))
        for p in found:emit('待收纳：'+p.relative_to(root).as_posix())
        emit('仅处理已识别的外置 MOD、注入 DLL 和旧补丁入口；未知文件与游戏资源不盲目删除。')
    elif action=='reset-render-cache':
        reset_render_cache(ctx)
    elif action=='language':
        ensure_idle(ctx)
        language=request.get('language')
        if language not in ('auto','zh-Hans','en','ru','pt-BR'):raise ValueError('不支持的菜单语言。')
        if language=='auto':language=''
        ini=Path(ctx['game']).parent/'totk.ini'
        if ini.is_file():
            backup=home(ctx)/'backups'/('menu-language-'+str(time.time_ns())+'.ini');backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ini,backup)
            text=ini.read_text(encoding='utf-8-sig')
        else:text=''
        pattern=r'(?im)^([ \t]*menu_lang[ \t]*=[ \t]*)([^\r\n#;]*)(.*)$'
        if re.search(pattern,text):text=re.sub(pattern,lambda match:match[1]+language+(' '+match[3] if match[3] else ''),text)
        else:
            lines=text.splitlines()
            section=next((i for i,line in enumerate(lines) if re.match(r'\s*\[\s*language\s*\]\s*$',line,re.I)),None)
            if section is None:
                if lines and lines[-1].strip():lines.append('')
                lines+=['[language]','menu_lang = '+language]
            else:
                last=section+1
                while last<len(lines) and not re.match(r'\s*\[',lines[last]):last+=1
                lines.insert(last,'menu_lang = '+language)
            text='\n'.join(lines)+'\n'
        temporary=ini.with_name(ini.name+'.new');temporary.write_text(text,encoding='utf-8');os.replace(temporary,ini)
        emit('菜单语言已保存，下次启动游戏生效；游戏内 F1 的语言选择仍保留。自动时跟随游戏语言（启动器/安装器所选语言）。')
    elif action=='restore-official':
        info,asset=release()
        build(ctx,False,False,info,asset,cache,official=True,cleanup=request.get('cleanup',True))
    elif action == 'icons':
        install_icons(ctx, request.get('style'))
    elif action == 'icons-disable':
        ensure_idle(ctx)
        folder = Path(ctx['game']).parent / 'mods/PS5_Icons_Manager'
        if not (folder / '.totk-manager.json').is_file():
            raise ValueError('未找到管理器安装的独立图标 MOD。')
        backup = home(ctx) / 'icons-history' / ('disabled-'+time.strftime('%Y%m%d-%H%M%S'))
        backup.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(folder),backup)
        emit('已停用独立图标 MOD，文件保留在扩展历史中，官方程序未改动。')
    elif action in ['apply','repair']:
        state=active(ctx)
        selected=state if action=='repair' and state.get('mode')=='direct' and (state.get('ps5') or state.get('graphics')) else request
        install_prebuilt(ctx,selected.get('ps5',True),selected.get('graphics',True),cache,repair=action=='repair')
    elif action in ['update','rebuild-current','refresh-recipes']:
        if action != 'refresh-recipes':
            # Adapters are refreshed before the build so a newly published one is used instead of rebasing an old one.
            # Offline players fall through to whatever adapters are already on disk.
            try_sync(cache)
        tag = None if action == 'update' else request.get('version') or ctx['version'].split('+')[0]
        if not re.fullmatch(r'\d+\.\d+\.\d+', tag or '') and tag is not None:
            raise ValueError('当前程序版本无法可靠识别，请选择手动更新到官方最新正式版。')
        info, asset = component_release(cache,tag)
        build(ctx, request.get('ps5', True), request.get('graphics', True), info, asset, cache)
    elif action=='prewarm-shaders':
        ensure_idle(ctx)
        tag=active(ctx).get('version') or ctx['version'].split('+')[0]
        if not re.fullmatch(r'\d+\.\d+\.\d+',tag):raise ValueError('无法识别当前游戏版本，未启动预热。')
        emit('这是用户主动选择的全量预热，会准备大量着色器；缺少官方工具时需要下载。')
        info,asset=component_release(cache,tag);kit,source=toolkit(cache,info,asset)
        main_sha=digest(Path(ctx['exefs'])/'main');versions=[p.stem.removeprefix('expected-v') for p in (source/'tools/data').glob('expected-v*.json') if read_json(p).get('exefs_sha256',{}).get('main')==main_sha]
        if len(versions)!=1:raise ValueError('官方工具未识别此游戏资源，未预热。')
        root=Path(ctx['game']).parent;env=launch_env(ctx);env.update(TOTK_DLAA='0',TOTK_FRAMEGEN='off',TOTK_DUALSENSE_HD='0',TOTK_PROFILE_DIR=str(cache/'prewarm-profile'))
        rows=local_resources(ctx);local_index=cache/'prewarm-index.json'
        write_json(local_index,dict(status='pass',romfs_size=(Path(ctx['romfs'])/'romfs.bin').stat().st_size,romfs_sha256='',files=[dict(path=name,offset=at,size=n) for name,(at,n) in rows.items()]))
        env.update(TOTK_MANAGER_SOURCE_INDEX=str((Path(ctx['romfs'])/'index.json').resolve()),TOTK_MANAGER_LOCAL_INDEX=str(local_index))
        run(python_tool(kit,source/'tools/prepare_shaders.py',[root,'--version','v'+versions[0].replace('.',''),'--romfs',ctx['romfs'],'--exefs',ctx['exefs'],'--list',kit/'data/shader_list.tsv']),source,env)
        emit('主动全量预热完成。')
    elif action == 'rollback':
        ensure_idle(ctx)
        manager_install.rollback(Path(ctx['game']).parent,home(ctx),emit)
        emit('回退完成。',result=dict(health=health(ctx)))
    elif action == 'disable':
        ensure_idle(ctx)
        seen=set()
        while active(ctx).get('mode')=='direct':
            checkpoint=read_json(home(ctx)/'rollback.json',{})
            token=checkpoint.get('backup')
            if not token or token in seen:raise ValueError('还原记录不完整，已保留当前备份，请导出诊断。')
            seen.add(token);manager_install.rollback(Path(ctx['game']).parent,home(ctx),emit)
        emit('已还原到本工具首次直接安装之前。存档和个人设置保持当前内容。')
    elif action == 'cleanup-owned':
        cleanup_owned(ctx)
    elif action in ['launch', 'official','launch-safe','launch-sdr']:
        launch(ctx, action == 'official',safe=action=='launch-safe',sdr=action=='launch-sdr')
    elif action == 'backup':
        emit('备份完成。', result=dict(path=str(backup_personal(ctx))))
    elif action == 'export':
        out = Path(request['output'])
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            values=ini_values(Path(ctx['game']).parent/'totk.ini')
            settings={key:values[key] for key in ('DLAA','DLSS','DLSS_PRESET','DLSS_MODEL','TEMPORAL_ENGINE',
                       'FRAMEGEN','FRAMEGEN_MULTIPLIER','HDR','HDR_PEAK_NITS','HDR_GAME_NITS','HDR_UI_NITS',
                       'UPSCALER','VK_RECORDER','GPU_INDEX','GPU_NAME','OUTPUT_RES','SCENE_SCALE','MENU_LANG') if key in values}
            version_file = APP/'manager-version.txt'
            manager_version = version_file.read_text(encoding='ascii').strip() if version_file.is_file() else read_json(APP/'prebuilt/manifest.json', {}).get('manager_version', '1.2.2')
            z.writestr('status.json', json.dumps(dict(manager_version=manager_version,captured_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),context=ctx, health=health(ctx),components=components(ctx),executable_sha256=digest(ctx['game']),settings=settings), ensure_ascii=False, indent=2))
            candidates = [LOG, home(ctx) / 'logs/game.log', home(ctx) / 'logs/game-safe.log',
                          home(ctx) / 'logs/game-sdr.log', Path(ctx['game']).parent / 'logs/totk.log']
            for i, p in enumerate(candidates):
                if p.is_file():
                    z.write(p, str(i) + '-' + p.name)
            z.writestr('log-times.json',json.dumps([dict(source=str(p),modified_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(p.stat().st_mtime)),bytes=p.stat().st_size) for p in candidates if p.is_file()],ensure_ascii=False,indent=2))
        emit('诊断包已导出（不包含存档、密钥和游戏资源）。', result=dict(path=str(out)))
    else:
        raise ValueError('未知操作：' + action)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--request', type=Path, required=True); args = ap.parse_args()
    try:
        main(read_json(args.request))
    except Exception as e:
        emit(str(e), error=True)
        sys.exit(1)
