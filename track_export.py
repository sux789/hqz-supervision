"""轨迹导出：GPX → 「每个小班一个文件夹」的 Shapefile 压缩包（纯标准库，零 GIS 依赖）。

对齐 hqz-survey 的 R17 口径（2026-09-13 用户要求）：轨迹下载用 **SHP**，
且 zip 内**每个小班一个文件夹**（shapefile 组件文件同名，必须目录隔离）。

zip 结构::

    轨迹导出_20260913_182500.zip
    ├── YJ-515/                      ← 每个小班一个文件夹
    │   ├── YJ-515.shp / .shx / .dbf / .prj / .cpg
    │   └── 轨迹_YJ-515_2026-09-13101711.gpx      ← 原始 GPX 一并保留（便于回溯）
    └── YJ-519/
        └── ...

- 每段 GPX 轨迹 = 一条 **PolyLineZ** 要素（含高程；无高程时补 0）
- 属性表字段用 **ASCII 名**（ArcGIS/dBASE 对中文列名支持差）：SC/FILE/POINTS/START/END，
  值本身是 UTF-8 中文（随附 .cpg 声明 UTF-8，ArcGIS 10.1+/QGIS 正常显示）
- 连续重复点自动去重（GPS 常回传相同坐标）；不足 2 点的片段跳过（构不成线要素）
- 投影 WGS84（GPX 本身即 WGS-84），故无需纠偏
"""
import io
import os
import re
import struct
import zipfile
from datetime import datetime
from pathlib import Path

# ── shapefile 常量 ──
_SHP_POLYLINEZ = 13
_FILE_CODE = 9994
_VERSION = 1000
_WGS84_PRJ = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)

_TRKPT_RE = re.compile(
    r'<trkpt[^>]*lat="([-\d.]+)"[^>]*lon="([-\d.]+)"[^>]*>(.*?)</trkpt>',
    re.S)
_ELE_RE = re.compile(r'<ele>([-\d.]+)</ele>')
_TIME_RE = re.compile(r'<time>([^<]+)</time>')
_SC_IN_NAME_RE = re.compile(r'轨迹_(.+?)_\d{4}-')


# ────────────────────────── GPX 解析 ──────────────────────────

def gpx_to_points(path) -> list:
    """GPX → [(lon, lat, ele|None, time|None)]（按文件顺序）。"""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    pts = []
    for m in _TRKPT_RE.finditer(text):
        lat, lon, body = float(m.group(1)), float(m.group(2)), m.group(3)
        ele = _ELE_RE.search(body)
        tm = _TIME_RE.search(body)
        pts.append((lon, lat,
                    float(ele.group(1)) if ele else None,
                    tm.group(1) if tm else None))
    return pts


def dedupe_points(points: list) -> list:
    """去掉连续重复坐标（GPS 静止时常见）。"""
    out = []
    for p in points:
        if out and abs(out[-1][0] - p[0]) < 1e-9 and abs(out[-1][1] - p[1]) < 1e-9:
            continue
        out.append(p)
    return out


def group_key_from_name(filename: str) -> str:
    """从 GPX 文件名解析小班号：轨迹_{小班}_{时间}.gpx（兼容带前缀的旧命名）。"""
    m = _SC_IN_NAME_RE.search(filename)
    if m:
        return m.group(1).strip() or '无小班'
    stem = Path(filename).stem
    if '轨迹_' in stem:
        tail = stem.split('轨迹_', 1)[1].strip('_')
        return tail or '无小班'
    return '无小班'


# ────────────────────────── shapefile 写入 ──────────────────────────

def _polylinez_record(coords):
    """一条折线的 shape 内容（type=PolyLineZ）：coords=[(lon,lat,ele|None)]"""
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    zs = [0.0 if c[2] is None else float(c[2]) for c in coords]
    n = len(coords)
    buf = io.BytesIO()
    buf.write(struct.pack('<i', _SHP_POLYLINEZ))
    buf.write(struct.pack('<4d', min(xs), min(ys), max(xs), max(ys)))     # bbox
    buf.write(struct.pack('<2i', 1, n))                                   # numParts=1, numPoints
    buf.write(struct.pack('<i', 0))                                       # part 0 起点索引
    for x, y in zip(xs, ys):
        buf.write(struct.pack('<2d', x, y))
    buf.write(struct.pack('<2d', min(zs), max(zs)))
    for z in zs:
        buf.write(struct.pack('<d', z))
    return buf.getvalue()


def _shp_header(file_len_words, shape_type, bbox):
    buf = io.BytesIO()
    buf.write(struct.pack('>i', _FILE_CODE))
    buf.write(b'\x00' * 20)                                  # 5 个未用 int
    buf.write(struct.pack('>i', file_len_words))             # 文件长度（16 位字）
    buf.write(struct.pack('<i', _VERSION))
    buf.write(struct.pack('<i', shape_type))
    xmin, ymin, xmax, ymax, zmin, zmax = bbox
    buf.write(struct.pack('<4d', xmin, ymin, xmax, ymax))
    buf.write(struct.pack('<2d', zmin, zmax))
    buf.write(struct.pack('<2d', 0.0, 0.0))                  # M 范围（未使用）
    return buf.getvalue()


def write_shapefile(shp_path, features) -> None:
    """features = [coords, ...]（每条一条折线）→ 写出 .shp/.shx/.prj/.cpg。

    属性表由 write_dbf() 单独写（调用方负责行序与 features 一致）。
    """
    shp_path = Path(shp_path)
    records = [(_polylinez_record(c), c) for c in features]
    all_x, all_y, all_z = [], [], []
    for _, coords in records:
        for c in coords:
            all_x.append(c[0]); all_y.append(c[1])
            all_z.append(0.0 if c[2] is None else float(c[2]))
    bbox = (min(all_x), min(all_y), max(all_x), max(all_y), min(all_z), max(all_z))

    # .shp
    body = b''.join(struct.pack('>2i', i + 1, len(rec) // 2) + rec
                    for i, (rec, _c) in enumerate(records))
    total = 100 + len(body)
    shp_path.write_bytes(_shp_header(total // 2, _SHP_POLYLINEZ, bbox) + body)

    # .shx（每条记录：偏移与内容长度，均以 16 位字计）
    idx, offset = b'', 50          # 首条记录从第 100 字节 = 50 字 开始
    for i, (rec, _c) in enumerate(records):
        idx += struct.pack('>2i', offset, len(rec) // 2)
        offset += 4 + len(rec) // 2
    shx_total = 100 + len(idx)
    shp_path.with_suffix('.shx').write_bytes(
        _shp_header(shx_total // 2, _SHP_POLYLINEZ, bbox) + idx)

    shp_path.with_suffix('.prj').write_text(_WGS84_PRJ, encoding='utf-8')
    shp_path.with_suffix('.cpg').write_text('UTF-8', encoding='utf-8')


def write_dbf(dbf_path, fields, rows) -> None:
    """dBASE III 属性表：fields=[(name, 'C'|'N', length)]（名字用 ASCII），rows=[[值…]]。"""
    dbf_path = Path(dbf_path)
    now = datetime.now()
    n_rec = len(rows)
    rec_len = 1 + sum(f[2] for f in fields)
    header_len = 32 + 32 * len(fields) + 1

    out = io.BytesIO()
    out.write(struct.pack('<B3B', 0x03, now.year - 1900, now.month, now.day))
    out.write(struct.pack('<i', n_rec))
    out.write(struct.pack('<2H', header_len, rec_len))
    out.write(b'\x00' * 20)
    for name, typ, length in fields:
        raw = name.encode('ascii', 'replace')[:10]
        out.write(raw + b'\x00' * (11 - len(raw)))
        out.write(typ.encode('ascii'))
        out.write(b'\x00' * 4)
        out.write(struct.pack('<2B', length, 0))
        out.write(b'\x00' * 14)      # 字段描述符固定 32 字节：name11+type1+reserved4+len1+dec1+reserved14
    out.write(b'\x0d')
    for row in rows:
        out.write(b' ')
        for (name, typ, length), val in zip(fields, row):
            s = '' if val is None else str(val)
            enc = s.encode('utf-8')[:length]
            # 按字节截断后可能截断多字节字符 → 再安全化一次
            while True:
                try:
                    enc.decode('utf-8'); break
                except UnicodeDecodeError:
                    enc = enc[:-1]
            if typ == 'N':
                out.write(enc.rjust(length, b' '))
            else:
                out.write(enc.ljust(length, b' '))
    out.write(b'\x1a')
    dbf_path.write_bytes(out.getvalue())


# ────────────────────────── 导出 zip ──────────────────────────

def _short_time(iso):
    return (iso or '').replace('T', ' ').replace('Z', '')[:19]


def export_tracks_zip(track_dir) -> tuple:
    """轨迹目录 → (BytesIO zip, 统计 dict)。每个小班一个文件夹，内含 shapefile + 原始 GPX。"""
    files = sorted(Path(track_dir).glob('*.gpx'))
    if not files:
        raise ValueError('暂无轨迹文件')

    groups = {}          # 小班 → [(文件名, 去重点集)]
    for f in files:
        pts = dedupe_points(gpx_to_points(f))
        if len(pts) < 2:      # 单点/空轨迹无法构线
            continue
        groups.setdefault(group_key_from_name(f.name), []).append((f, pts))
    if not groups:
        raise ValueError('轨迹均不足 2 个有效点，无法生成线要素')

    buf = io.BytesIO()
    used, n_files, n_tracks, n_points = {}, 0, 0, 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for sc in sorted(groups):
            base = sc or '无小班'
            k = used.get(base, 0) + 1
            used[base] = k
            dirname = base if k == 1 else f'{base}_{k}'
            feats = [pts for _f, pts in groups[sc]]
            fields = [('SC', 'C', 40), ('FILE', 'C', 80), ('POINTS', 'N', 8),
                      ('START', 'C', 20), ('END', 'C', 20)]
            rows = []
            for f, pts in groups[sc]:
                rows.append([sc, f.name, len(pts),
                             _short_time(next((p[3] for p in pts if p[3]), '')),
                             _short_time(next((p[3] for p in reversed(pts) if p[3]), ''))])
                n_points += len(pts)
            # shapefile 组件必须同目录多文件 → 用临时目录写出后再入 zip
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                shp_path = Path(td) / (dirname + '.shp')
                write_shapefile(shp_path, feats)
                write_dbf(shp_path.with_suffix('.dbf'), fields, rows)
                for comp in shp_path.parent.glob(dirname + '.*'):
                    zf.write(comp, f'{dirname}/{comp.name}')
                    n_files += 1
            # 原始 GPX 一并保留（便于回溯/重导）
            for f, _pts in groups[sc]:
                zf.write(f, f'{dirname}/{f.name}')
                n_files += 1
            n_tracks += len(feats)

    buf.seek(0)
    return buf, {'fmt': 'shp', 'groups': len(groups), 'files': n_files,
                 'tracks': n_tracks, 'points': n_points}
