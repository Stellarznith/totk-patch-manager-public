"""Bind the action HUD's face-button labels to the existing button font."""
import hashlib
import math
import os
import re
import struct
from functools import lru_cache

from compression import zstd

MEMBER = 'blyt/DoCommand_00.bflyt'
ATLAS = 'timg/__Combined.bntx'
COMMON = 'UI/LayoutArchive/Common.Product.140.Nin_NX_NVN.blarc.zs'
REVISION = 1
NORMAL = b'Normal_00.fcpx'
EXTERNAL = b'External_00.fcpx'
GLYPHS = dict(zip(b'ABXY', range(0xe040, 0xe044)))
LABEL = re.compile(rb'T_([ABXY])_\d{2}')


def sections(data):
    if data[:6] != b'FLYT\xff\xfe' or struct.unpack_from('<I', data, 12)[0] != len(data):
        raise ValueError('Unrecognized DoCommand BFLYT header')
    at = struct.unpack_from('<H', data, 6)[0]
    count = struct.unpack_from('<H', data, 16)[0]
    for _ in range(count):
        kind, size = struct.unpack_from('<4sI', data, at)
        if size < 8 or at + size > len(data):
            raise ValueError('Invalid DoCommand BFLYT section length')
        yield kind, at, size
        at += size
    if at != len(data):
        raise ValueError('Unexpected DoCommand BFLYT trailing bytes')


def bind(data, style='original'):
    data = bytes(data)
    result = bytearray(data)
    table, labels = None, {}
    for kind, at, size in sections(data):
        if kind == b'fnl1':
            count = struct.unpack_from('<H', data, at + 8)[0]
            offsets = struct.unpack_from(f'<{count}I', data, at + 12)
            names = [data[at + 12 + offset:at + size].split(b'\0', 1)[0] for offset in offsets]
            if table is not None or names not in ([NORMAL], [NORMAL, EXTERNAL]):
                raise ValueError('Unexpected DoCommand font list')
            table = at, size, names
        elif kind == b'txt1':
            name = data[at + 12:at + 36].split(b'\0', 1)[0]
            match = LABEL.fullmatch(name)
            if not match:
                continue
            index = struct.unpack_from('<H', data, at + 0x5a)[0]
            text_at = at + struct.unpack_from('<I', data, at + 0x64)[0]
            expected = struct.pack('<HH', GLYPHS[match[1][0]], 0)
            if (name in labels or index not in (0, 1) or not at < text_at <= at + size - 4
                    or data[text_at:text_at + 4] != expected):
                raise ValueError(f'Unexpected DoCommand PS5 label {name!r}')
            labels[name] = index
            struct.pack_into('<H', result, at + 0x5a, 1)
            if index == 0:
                # Nintendo's dim letters are 17 px high in a 1080p capture. External's
                # button face is 21 px at the same pane scale and has a lower baseline.
                button = match[1]
                x, y = struct.unpack_from('<2f', data, at + 0x2c)
                dx = 1.05 if button in (b'B', b'Y') else 0.0
                dy = 2.15 if button == b'Y' else 1.75
                if style == 'alerion':
                    dx -= {b'A': .18, b'B': -.09, b'X': .31, b'Y': -.03}[button]
                    dy -= 1.45
                struct.pack_into('<2f', result, at + 0x2c, x + dx, y + dy)
                sx, sy = struct.unpack_from('<2f', data, at + 0x44)
                struct.pack_into('<2f', result, at + 0x44, sx * .81, sy * .81)
    if table is None or len(labels) != 15:
        raise ValueError('Expected a font list and 15 DoCommand face-button labels')
    at, size, names = table
    if any(index >= len(names) for index in labels.values()):
        raise ValueError('DoCommand label refers to a missing font')
    if len(names) == 1:
        section = bytearray(struct.pack('<4sIHHII', b'fnl1', 0, 2, 0, 8, 8 + len(NORMAL) + 1))
        section += NORMAL + b'\0' + EXTERNAL + b'\0'
        section += bytes(-len(section) % 8)
        struct.pack_into('<I', section, 4, len(section))
        result[at:at + size] = section
        struct.pack_into('<I', result, 12, len(result))
    return bytes(result)


def mask_offsets(texture):
    width, height, tile = texture['width'], texture['height'], 1 << texture['tile']
    if texture['format'] != '0x201' or texture['mips'] != 1:
        raise ValueError('Expected an R8 action-prompt mask')
    return [texture['start'] + (y // (8 * tile)) * ((width + 63) // 64) * 512 * tile
            + (x // 64) * 512 * tile + ((y % (8 * tile)) // 8) * 512
            + (x % 64 // 32) * 256 + (y % 8 // 2) * 64 + (x % 32 // 16) * 32 + (y % 2) * 16 + x % 16
            for y in range(height) for x in range(width)]


def mask_bounds(mask, width):
    points = [(i % width, i // width) for i, value in enumerate(mask) if value >= 128]
    if not points:
        raise ValueError('Empty action-prompt mask')
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def fit_primary(atlas, stock, textures, draw_face=None):
    """Match Nintendo's visible button diameter and center while keeping the PS shapes proportional."""
    result = bytearray(atlas)
    target_textures, stock_textures = textures(atlas), textures(stock)
    for button in 'ABXY':
        name = f'Nt_KeyTex{button}_00'
        target, reference = target_textures[name], stock_textures[name]
        width, height = target['width'], target['height']
        if (width, height) != (reference['width'], reference['height']):
            raise ValueError(f'Unexpected action-prompt texture dimensions for {name}')
        offsets = mask_offsets(target)
        stock_mask = bytes(stock[at] for at in mask_offsets(reference))
        mask = draw_face(button, width, height, stock_mask) if draw_face else [atlas[at] for at in offsets]
        x0, y0, x1, y1 = mask_bounds(mask, width)
        rx0, ry0, rx1, ry1 = mask_bounds(stock_mask, width)
        current = (x1 - x0 + 1, y1 - y0 + 1)
        desired = (rx1 - rx0 + 1, ry1 - ry0 + 1)
        scale = min(desired[0] / current[0], desired[1] / current[1])
        source_center = ((x0 + x1) / 2, (y0 + y1) / 2)
        stock_center = ((rx0 + rx1) / 2, (ry0 + ry1) / 2)

        def value(x, y):
            return mask[y * width + x] if 0 <= x < width and 0 <= y < height else 0

        fitted = []
        for pixel in range(width * height):
            x = (pixel % width - stock_center[0]) / scale + source_center[0]
            y = (pixel // width - stock_center[1]) / scale + source_center[1]
            ix, iy = math.floor(x), math.floor(y)
            dx, dy = x - ix, y - iy
            a = value(ix, iy) * (1 - dx) + value(ix + 1, iy) * dx
            b = value(ix, iy + 1) * (1 - dx) + value(ix + 1, iy + 1) * dx
            fitted.append(round(a * (1 - dy) + b * dy))
        left, top, right, bottom = mask_bounds(fitted, width)
        if (abs(max(right - left + 1, bottom - top + 1) - max(desired)) > 2
                or abs((left + right) / 2 - stock_center[0]) > 1.5
                or abs((top + bottom) / 2 - stock_center[1]) > 1.5):
            raise ValueError(f'Action-prompt mask did not align to the stock button: {name}')
        for at, value in zip(offsets, fitted):
            result[at] = value
    return bytes(result)


# SARC helpers adapted from TotK Recomp's make_romfs_patches.py (MIT).
# That tool credits MacNativeGames contributors, copyright 2026.
def sarc_nodes(data):
    """(name, node offset, absolute start, end) of every SARC member, in SFAT order."""
    assert data[:4] == b'SARC' and data[6:8] == b'\xff\xfe'
    header, size, start = struct.unpack_from('<HxxII', data, 4)
    assert size == len(data) and data[header:header + 4] == b'SFAT'
    count = struct.unpack_from('<H', data, header + 6)[0]
    names = header + 12 + count * 16
    assert data[names:names + 4] == b'SFNT'
    names += 8
    out = []
    for i in range(count):
        node = header + 12 + i * 16
        _, attribute, lo, hi = struct.unpack_from('<4I', data, node)
        offset = names + (attribute & 0xffffff) * 4
        out.append((data[offset:data.index(0, offset, start)].decode(), node, start + lo, start + hi))
    return out


def sarc_members(data):
    """name -> (absolute start, end) of every SARC member."""
    return {name: (lo, hi) for name, _, lo, hi in sarc_nodes(data)}


def sarc_replace(data, new):
    """The SARC data with the members named in new (name -> bytes) replaced. The header, SFAT and SFNT stay; the
    members are laid out again in their old order, each at an offset with at least the alignment its old one had."""
    start = struct.unpack_from('<I', data, 12)[0]
    out = bytearray(data[:start])
    for name, node, lo, hi in sorted(sarc_nodes(data), key=lambda n: n[2]):
        align = min(lo & -lo, 0x2000) if lo else 0x2000
        out += bytes(-len(out) % align)
        body = new.get(name, data[lo:hi])
        struct.pack_into('<II', out, node + 8, len(out) - start, len(out) - start + len(body))
        out += body
    struct.pack_into('<I', out, 8, len(out))
    return bytes(out)


def textures(data):
    assert data[:4] == b'BNTX'
    count, table = struct.unpack_from('<IQ', data, 0x24)
    result = {}
    for k in range(count):
        info = struct.unpack_from('<Q', data, table + 8 * k)[0]
        name = struct.unpack_from('<Q', data, info + 0x60)[0]
        name = data[name + 2:name + 2 + struct.unpack_from('<H', data, name)[0]].decode().split('^')[0]
        mips = struct.unpack_from('<Q', data, info + 0x70)[0]
        result[name] = {'info': info, 'format': hex(struct.unpack_from('<I', data, info + 0x1c)[0]),
                        'width': struct.unpack_from('<I', data, info + 0x24)[0],
                        'height': struct.unpack_from('<I', data, info + 0x28)[0],
                        'tile': struct.unpack_from('<I', data, info + 0x34)[0],
                        'mips': struct.unpack_from('<H', data, info + 0x16)[0],
                        'start': struct.unpack_from('<Q', data, mips)[0],
                        'size': struct.unpack_from('<I', data, info + 0x50)[0]}
    return result


def polygon(points):
    return {'start': points[0], 'segments': [{'kind': 'lines', 'points': points[1:] + [points[0]]}]}


def circle(cx,cy,r,clockwise=False):
    k = r * 0.5522847498
    points = []
    for q in range(4):
        for x,y in [(r,k),(k,r),(0,r)]:
            for _ in range(q): x,y = -y,x
            if clockwise: y = -y
            points.append([round(cx+x),round(cy+y)])
    return {'start':[cx+r,cy], 'segments':[{'kind':'cubics','points':points}]}


def face_shapes(name):
    if name == 'circle':
        return [circle(320, 301, 132, True), circle(320, 301, 96)]
    if name == 'cross':
        points = [(-110,-140),(0,-30),(110,-140),(140,-110),(30,0),(140,110),
                  (110,140),(0,30),(-110,140),(-140,110),(-30,0),(-140,-110)]
        return [polygon([[320+x, 301+y] for x,y in reversed(points)])]
    if name == 'triangle':
        return [polygon([[320,450],[475,170],[165,170]]), polygon([[320,367],[232,213],[408,213]])]
    if name == 'square':
        return [polygon([[190,171],[190,431],[450,431],[450,171]]),
                polygon([[230,211],[410,211],[410,391],[230,391]])]
    raise ValueError(name)


def flattened(figure):
    ps=[figure['start']]; last=ps[0]
    for s in figure['segments']:
        if s['kind'] not in ('cubic','cubics'): ps.extend(s['points']); last=ps[-1]; continue
        for at in range(0,len(s['points']),3):
            p0=last; p1,p2,p3=s['points'][at:at+3]
            for j in range(1,25):
                t=j/24; u=1-t
                ps.append([u*u*u*p0[k]+3*u*u*t*p1[k]+3*u*t*t*p2[k]+t*t*t*p3[k] for k in [0,1]])
            last=p3
    return ps


def coverage(name, w, h, original):
    # Fill the old label using its silhouette's row spans. Preserve outer antialiasing exactly.
    base=bytearray(original)
    for y in range(h):
        solid=[x for x in range(w) if original[y*w+x]>=250]
        if solid:
            for x in range(min(solid),max(solid)+1): base[y*w+x]=255
    canvas, top = 640, 621
    polys=[[(x/canvas*w,(top-y)/canvas*h) for x,y in flattened(f)] for f in face_shapes(name)]
    def inside(x,y,ps):
        hit=False
        for (x1,y1),(x2,y2) in zip(ps,ps[1:]+ps[:1]):
            if (y1>y)!=(y2>y) and x<(x2-x1)*(y-y1)/(y2-y1)+x1: hit=not hit
        return hit
    result=bytearray(base)
    for y in range(h):
        for x in range(w):
            n=sum(sum(inside(x+sx,y+sy,p) for p in polys)%2 for sy in [.125,.375,.625,.875] for sx in [.125,.375,.625,.875])
            result[y*w+x]=round(base[y*w+x]*(1-n/16))
    return result


@lru_cache(maxsize=4)
def action_face(button, width, height, stock_mask):
    shape = {'A': 'circle', 'B': 'cross', 'X': 'triangle', 'Y': 'square'}[button]
    return bytes(coverage(shape, width, height, stock_mask))


def unpack(frame, dictionaries):
    info = zstd.get_frame_info(frame)
    if info.decompressed_size is None or not 0 < info.decompressed_size <= 128 * 1024**2:
        raise ValueError('PS5 交互提示资源大小无效。')
    return zstd.decompress(frame, zstd_dict=dictionaries[info.dictionary_id])


def repair(output, folders, rows, blob, base, cancelled):
    """Repair the staged, verified 1.4.3 overlays from the player's own RomFS."""
    targets = [(name, output / name / COMMON) for name in folders]
    targets = [(name, path) for name, path in targets if path.is_file()]
    if not targets:
        return 0

    def original(path):
        at, size = rows[path]
        if not 0 < size <= 128 * 1024**2:
            raise ValueError('本地 PS5 交互提示原资源大小无效。')
        blob.seek(at)
        data = blob.read(size)
        if len(data) != size:
            raise ValueError('本地 PS5 交互提示原资源不完整。')
        return data

    dictionary_pack = unpack(original('Pack/ZsDic.pack.zs'), {0: None})
    dictionaries = {0: None}
    for lo, hi in sarc_members(dictionary_pack).values():
        dictionary = zstd.ZstdDict(dictionary_pack[lo:hi])
        dictionaries[dictionary.dict_id] = dictionary
    stock_frame = original(COMMON)
    stock_hash = hashlib.sha256(stock_frame).hexdigest()
    stock = unpack(stock_frame, dictionaries)
    stock_atlas = stock[slice(*sarc_members(stock)[ATLAS])]
    for name, path in targets:
        cancelled()
        frame = path.read_bytes()
        manifest = output / name / 'manifest.tsv'
        lines = manifest.read_bytes().splitlines(keepends=True)
        expected = [COMMON.encode(), str(len(frame)).encode(), hashlib.sha256(frame).hexdigest().encode(), stock_hash.encode()]
        indexes = [i for i, line in enumerate(lines) if line.rstrip(b'\r\n').split(b'\t') == expected]
        if len(indexes) != 1:
            raise ValueError('本地 PS5 交互提示与 1.4.3 原资源或组件清单不符：' + name)
        archive = unpack(frame, dictionaries)
        entries = sarc_members(archive)
        layout = archive[slice(*entries[MEMBER])]
        style = 'alerion' if name.startswith(base + '-alerion') else 'original'
        fixed = bind(layout, style)
        is_white = not name.startswith((base + '-alerion', base + '-stavaas'))
        changes = {MEMBER: fixed} if fixed != layout else {}
        if is_white or changes:
            atlas = archive[slice(*entries[ATLAS])]
            fitted = fit_primary(atlas, stock_atlas, textures, action_face if is_white else None)
            if fitted != atlas:
                changes[ATLAS] = fitted
        if not changes:
            continue
        dictionary = dictionaries[zstd.get_frame_info(frame).dictionary_id]
        revised = zstd.compress(sarc_replace(archive, changes), level=9, zstd_dict=dictionary)
        temp = path.with_suffix('.prompt-fix')
        temp.write_bytes(revised)
        os.replace(temp, path)
        expected[1] = str(len(revised)).encode()
        expected[2] = hashlib.sha256(revised).hexdigest().encode()
        i = indexes[0]
        ending = lines[i][len(lines[i].rstrip(b'\r\n')):]
        lines[i] = b'\t'.join(expected) + ending
        temp = manifest.with_suffix('.prompt-fix')
        temp.write_bytes(b''.join(lines))
        os.replace(temp, manifest)
    return REVISION
