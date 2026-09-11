# -*- coding: utf-8 -*-
"""hqz-supervision —— 通用 Excel 网格填表工具（B1 本地预览版）。

约束：C01 参数驱动 / C02 参数语法 / C03 不影响其他业务（本地 127.0.0.1:8720）
     C04 通用无调查业务 / C05 账号（雷华雄 admin）/ C06 水印日期不参数化
启动：python main.py  →  http://127.0.0.1:8720
"""
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime
from functools import wraps
from pathlib import Path

import openpyxl
from flask import (Blueprint, Flask, abort, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

from param_parser import ParamError, _s, parse_params, split_list

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / 'data'
UPLOAD_DIR = DATA_DIR / 'workbooks'
TRACK_DIR = DATA_DIR / 'tracks'
DB_PATH = DATA_DIR / 'app.sqlite3'
for d in (UPLOAD_DIR, TRACK_DIR):
    d.mkdir(parents=True, exist_ok=True)

PORT = 8720  # 独立端口（C03：不与 hqz-survey / hqz-cam-app 冲突）

bp = Blueprint('sup', __name__)

# ────────────────────────── 基础设施 ──────────────────────────

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript('''
        CREATE TABLE IF NOT EXISTS users(
            username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workbooks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, sheet_name TEXT NOT NULL,
            headers TEXT NOT NULL, rows TEXT NOT NULL,
            config TEXT NOT NULL, param_rows TEXT NOT NULL,
            uploaded_at TEXT NOT NULL);
    ''')
    if not con.execute('SELECT 1 FROM users').fetchone():
        con.execute(
            'INSERT INTO users VALUES(?,?,?)',
            ('雷华雄', hashlib.sha256('lhx123'.encode()).hexdigest(), 'admin'))
    con.commit()
    con.close()


def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get('user'):
            return jsonify(error='未登录'), 401
        return f(*a, **kw)
    return wrap


def admin_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get('user'):
            return jsonify(error='未登录'), 401
        if session.get('role') != 'admin':
            return jsonify(error='需要管理员权限'), 403
        return f(*a, **kw)
    return wrap


# ────────────────────────── 页面路由 ──────────────────────────

@bp.route('/')
def index():
    if not session.get('user'):
        return render_template('index.html', user=None)
    return render_template('index.html', user=session['user'], role=session.get('role'))


@bp.route('/admin')
def admin_page():
    if not session.get('user') or session.get('role') != 'admin':
        return redirect(url_for('sup.index'))
    return render_template('admin.html', user=session['user'])


# ────────────────────────── 认证 API ──────────────────────────

@bp.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(force=True)
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    con = db()
    row = con.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    con.close()
    if not row or row['password_hash'] != hashlib.sha256(password.encode()).hexdigest():
        return jsonify(error='用户名或密码错误'), 401
    session['user'] = row['username']
    session['role'] = row['role']
    return jsonify(ok=True, role=row['role'])


@bp.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify(ok=True)


# ────────────────────────── 工作簿 API ──────────────────────────

def _norm_cell(v):
    return '' if v is None else (v if isinstance(v, (int, float)) else str(v).strip())


def _norm_rows(headers, rows):
    """株数强度/蓄积强度等：数值 0.18 存储统一规范化为文本 '18%'（doc/004 §二）。"""
    pct_idx = {i for i, h in enumerate(headers) if '强度' in h and '%' not in h}
    out = []
    for r in rows:
        r = [_norm_cell(v) for v in r]
        for i in pct_idx:
            v = r[i]
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 1:
                r[i] = f'{v * 100:g}%'
        out.append(r)
    return out


def parse_workbook_storage(stream):
    """解析上传的 xlsx：数据 sheet + 参数 sheet。违规抛 ParamError。"""
    wb = openpyxl.load_workbook(stream, data_only=True, read_only=True)
    try:
        if '参数' not in wb.sheetnames:
            raise ParamError('上传的 Excel 缺少「参数」sheet（需同时含数据 sheet 和 参数 sheet）')
        data_name = next(n for n in wb.sheetnames if n != '参数')

        ws_p = wb['参数']
        prows = [(i, r) for i, r in enumerate(
            ws_p.iter_rows(max_col=6, values_only=True), 1)
            if any(x not in (None, '') for x in r)]

        ws_d = wb[data_name]
        it = ws_d.iter_rows(max_col=64, values_only=True)  # read_only 维度可能误报列数，强制扩列
        first = next(it, None)
        if first is None:
            raise ParamError(f'数据 sheet「{data_name}」为空')
        headers = [_s(h) for h in first]
        while headers and not headers[-1]:
            headers.pop()
        if not headers:
            raise ParamError(f'数据 sheet「{data_name}」缺少表头行')
        ncols = len(headers)
        rows = []
        for r in it:
            vals = list(r) + [None] * (ncols - len(r))
            if any(v not in (None, '') for v in vals[:ncols]):
                rows.append(vals[:ncols])

        config = parse_params(prows, headers)
        rows = _norm_rows(headers, rows)
        param_rows = [[_norm_cell(v) for v in (list(r) + [None] * 6)[:6]] for _, r in prows]
        return data_name, headers, rows, config, param_rows
    finally:
        wb.close()


@bp.route('/api/workbooks', methods=['GET'])
@login_required
def api_list():
    con = db()
    rows = con.execute(
        'SELECT id, name, sheet_name, uploaded_at FROM workbooks ORDER BY id DESC').fetchall()
    con.close()
    return jsonify(workbooks=[dict(r) for r in rows])


@bp.route('/api/workbooks', methods=['POST'])
@login_required
def api_upload():
    fs = request.files.get('file')
    if not fs or not fs.filename.lower().endswith(('.xlsx', '.xlsm')):
        return jsonify(error='请上传 .xlsx 文件'), 400
    try:
        sheet_name, headers, rows, config, param_rows = parse_workbook_storage(fs.stream)
    except ParamError as e:
        return jsonify(error=str(e)), 400
    con = db()
    cur = con.execute(
        'INSERT INTO workbooks(name, sheet_name, headers, rows, config, param_rows, uploaded_at)'
        ' VALUES(?,?,?,?,?,?,?)',
        (fs.filename, sheet_name, json.dumps(headers, ensure_ascii=False),
         json.dumps(rows, ensure_ascii=False), json.dumps(config, ensure_ascii=False),
         json.dumps(param_rows, ensure_ascii=False),
         datetime.now().strftime('%Y-%m-%d %H:%M')))
    wid = cur.lastrowid
    con.commit()
    con.close()
    return jsonify(ok=True, id=wid, rows=len(rows))


@bp.route('/api/workbooks/<int:wid>', methods=['GET'])
@login_required
def api_get(wid):
    con = db()
    r = con.execute('SELECT * FROM workbooks WHERE id=?', (wid,)).fetchone()
    con.close()
    if not r:
        abort(404)
    return jsonify(id=r['id'], name=r['name'], sheet_name=r['sheet_name'],
                   headers=json.loads(r['headers']), rows=json.loads(r['rows']),
                   config=json.loads(r['config']))


@bp.route('/api/workbooks/<int:wid>', methods=['DELETE'])
@admin_required
def api_delete(wid):
    con = db()
    con.execute('DELETE FROM workbooks WHERE id=?', (wid,))
    con.commit()
    con.close()
    return jsonify(ok=True)


@bp.route('/api/workbooks/<int:wid>/rows/<int:ridx>', methods=['POST'])
@login_required
def api_save_row(wid, ridx):
    """单行自动保存（前端两列表单 onchange 触发，对齐 hqz-survey 编辑体验）。"""
    vals = (request.get_json(silent=True) or {}).get('values')
    if not isinstance(vals, list):
        return jsonify(error='values 必须为数组'), 400
    con = db()
    try:
        r = con.execute('SELECT rows FROM workbooks WHERE id=?', (wid,)).fetchone()
        if not r:
            abort(404)
        rows = json.loads(r['rows'])
        if not (0 <= ridx < len(rows)):
            abort(404)
        if len(vals) != len(rows[ridx]):
            return jsonify(error=f'列数不匹配：期望 {len(rows[ridx])} 列，收到 {len(vals)}'), 400
        rows[ridx] = [_norm_cell(v) for v in vals]
        con.execute('UPDATE workbooks SET rows=? WHERE id=?',
                    (json.dumps(rows, ensure_ascii=False), wid))
        con.commit()
    finally:
        con.close()
    return jsonify(ok=True)


@bp.route('/api/workbooks/<int:wid>/save', methods=['POST'])
@login_required
def api_save(wid):
    rows = request.get_json(force=True).get('rows')
    if rows is None:
        return jsonify(error='缺少 rows'), 400
    con = db()
    cur = con.execute('UPDATE workbooks SET rows=? WHERE id=?',
                      (json.dumps(rows, ensure_ascii=False), wid))
    con.commit()
    n = cur.rowcount
    con.close()
    if not n:
        abort(404)
    return jsonify(ok=True)


# ────────────────────────── 轨迹（B3 前端接入） ──────────────────────────

@bp.route('/api/track', methods=['POST'])
@login_required
def api_track():
    fs = request.files.get('file')
    if not fs or not fs.filename.lower().endswith('.gpx'):
        return jsonify(error='请上传 .gpx 轨迹文件'), 400
    safe = re.sub(r'[\\/:*?"<>|]+', '_', fs.filename)
    path = TRACK_DIR / f'{datetime.now().strftime("%Y%m%d_%H%M%S")}_{safe}'
    fs.save(path)
    return jsonify(ok=True, file=path.name)


# ────────────────────────── 相片（B2：参数化目录/文件名，前端水印） ──────────────────────────

def _safe_segments(rel):
    """校验相对路径：按 / 拆段，每段清洗非法字符，禁止 .. 与空段。返回清洗后的段列表，违规抛 ValueError。"""
    segs = []
    for seg in (rel or '').split('/'):
        seg = re.sub(r'[\\/:*?"<>|]+', '_', seg).strip(' .')
        if not seg or seg == '..':
            raise ValueError(f'非法路径段「{seg}」')
        segs.append(seg)
    if len(segs) > 8:
        raise ValueError('目录层级过深（最多 8 层）')
    return segs


def _photo_root(wid):
    return UPLOAD_DIR / str(wid) / 'photos'


@bp.route('/api/workbooks/<int:wid>/photos', methods=['POST'])
@login_required
def api_photo_upload(wid):
    con = db()
    if not con.execute('SELECT 1 FROM workbooks WHERE id=?', (wid,)).fetchone():
        con.close()
        abort(404)
    con.close()
    fs = request.files.get('file')
    if not fs:
        return jsonify(error='缺少相片文件'), 400
    try:
        segs = _safe_segments(request.form.get('subdir', ''))
        name_segs = _safe_segments(request.form.get('filename', 'photo'))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    fname = '_'.join(name_segs)
    if not fname.lower().endswith(('.jpg', '.jpeg')):
        fname += '.jpg'
    d = _photo_root(wid)
    if segs:
        d = d.joinpath(*segs)
    d.mkdir(parents=True, exist_ok=True)
    path = d / fname
    i = 2
    stem, ext = path.stem, path.suffix
    while path.exists():
        path = d / f'{stem}_{i}{ext}'
        i += 1
    fs.save(path)
    rel = path.relative_to(_photo_root(wid)).as_posix()
    return jsonify(ok=True, path=rel)


@bp.route('/api/workbooks/<int:wid>/photos', methods=['GET'])
@login_required
def api_photo_list(wid):
    root = _photo_root(wid)
    photos = []
    if root.exists():
        for p in sorted(root.rglob('*.jpg')) + sorted(root.rglob('*.jpeg')):
            rel = p.relative_to(root).as_posix()
            photos.append({'path': rel, 'size': p.stat().st_size,
                           'mtime': datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M')})
    return jsonify(photos=photos)


@bp.route('/api/workbooks/<int:wid>/photos/file/<path:rel>', methods=['GET'])
@login_required
def api_photo_file(wid, rel):
    try:
        segs = _safe_segments(rel)
    except ValueError:
        abort(400)
    path = _photo_root(wid).joinpath(*segs)
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype='image/jpeg')


@bp.route('/api/workbooks/<int:wid>/photos.zip', methods=['GET'])
@login_required
def api_photo_zip(wid):
    root = _photo_root(wid)
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        if root.exists():
            for p in sorted(root.rglob('*.jpg')) + sorted(root.rglob('*.jpeg')):
                zf.write(p, p.relative_to(root).as_posix())
                n += 1
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f'工作簿{wid}_相片.zip',
                     mimetype='application/zip') if n else (jsonify(error='该工作簿暂无相片'), 404)


# ────────────────────────── 管理后台 API ──────────────────────────

@bp.route('/admin/api/workbooks', methods=['GET'])
@admin_required
def admin_list():
    con = db()
    rows = con.execute(
        'SELECT id, name, sheet_name, uploaded_at, config FROM workbooks ORDER BY id DESC').fetchall()
    con.close()
    out = []
    for r in rows:
        cfg = json.loads(r['config'])
        out.append({
            'id': r['id'], 'name': r['name'], 'sheet_name': r['sheet_name'],
            'uploaded_at': r['uploaded_at'],
            # 参数摘要：让管理端直接看到该 Excel 决定了哪些项可编辑 / 启用了什么功能
            # （config 中列表类 key 存的是分号分隔的原始字符串，这里归一化为数组）
            'editable_cols': split_list(cfg.get('可编辑列')),
            'hidden_cols': split_list(cfg.get('不显示列')),
            'features': split_list(cfg.get('功能')),
            'result_options': split_list(cfg.get('验收结果选项')),
        })
    return jsonify(workbooks=out)


@bp.route('/admin/api/workbooks/<int:wid>/download', methods=['GET'])
@admin_required
def admin_download(wid):
    con = db()
    r = con.execute('SELECT * FROM workbooks WHERE id=?', (wid,)).fetchone()
    con.close()
    if not r:
        abort(404)
    headers = json.loads(r['headers'])
    rows = json.loads(r['rows'])
    param_rows = json.loads(r['param_rows'])

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = r['sheet_name'][:31]
    ws.append(headers)
    for row in rows:
        ws.append(row)
    ws_p = wb.create_sheet('参数')
    ws_p.append(['key', 'value', '类型', '默认值', '说明', '示例'])
    for row in param_rows:
        ws_p.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stem = re.sub(r'\.xlsx$', '', r['name'])
    return send_file(buf, as_attachment=True,
                     download_name=f'{stem}_导出.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/admin/api/tracks', methods=['GET'])
@admin_required
def admin_tracks():
    files = sorted(TRACK_DIR.glob('*.gpx'), reverse=True)
    return jsonify(tracks=[{'file': f.name, 'size': f.stat().st_size} for f in files])


@bp.route('/admin/api/tracks.zip', methods=['GET'])
@admin_required
def admin_tracks_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(TRACK_DIR.glob('*.gpx')):
            zf.write(f, f.name)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='轨迹导出.zip', mimetype='application/zip')


@bp.route('/admin/api/tracks/<path:name>', methods=['GET'])
@admin_required
def admin_track_dl(name):
    path = (TRACK_DIR / Path(name).name)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


def create_app(prefix=''):
    """Flask 工厂。gateway 约定 create_app(prefix=spec['prefix'])；
    本地预览 prefix=''（路由在根路径）。
    注意：gateway DispatcherMiddleware 已设 SCRIPT_NAME 剥前缀，路由不挂 url_prefix
    （否则双重前缀 404）；static 用默认 /static 规则（剥离后正好命中）；
    prefix 仅用于 session cookie 限定。
    """
    app = Flask(__name__)
    app.secret_key = 'hqz-supervision-local-preview-key'

    @app.after_request
    def _no_cache_html(resp):
        # HTML 不缓存：发版后浏览器必须拿新页面（静态资源 /static 不受限，正常缓存）
        if resp.mimetype == 'text/html':
            resp.headers['Cache-Control'] = 'no-cache'
        return resp

    if prefix:
        app.config['APPLICATION_ROOT'] = prefix      # session cookie 限定本应用前缀
        app.config['PREFERRED_URL_SCHEME'] = 'https'
    app.register_blueprint(bp)
    return _EmptyPathFix(app)


class _EmptyPathFix:
    """网关 DispatcherMiddleware 剥前缀后 PATH_INFO 可能为空串（如 GET /supervision），
    werkzeug 会 308「补斜杠」且 Location 用 environ 的明文 scheme 拼成
    http://forest.bibook.top/supervision/ —— Android WebView 直接
    net::ERR_CLEARTEXT_NOT_PERMITTED（2026-09-11 v0.7.2 APK 实测踩坑）。
    空路径归一为 /，直接命中首页，绕开重定向（浏览器也少一跳）。
    nginx 在容器内无权改，故在应用层根治；hqz-survey 同架构同坑（其用
    usesCleartextTraffic=true 掩盖，本项目不走明文）。"""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ.get('PATH_INFO', '') == '':
            environ['PATH_INFO'] = '/'
        return self.app(environ, start_response)


init_db()

if __name__ == '__main__':
    print(f'hqz-supervision 本地预览 → http://127.0.0.1:{PORT} （不影响其他业务）')
    create_app().run(host='127.0.0.1', port=PORT, debug=False)
