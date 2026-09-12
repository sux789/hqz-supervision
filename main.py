# -*- coding: utf-8 -*-
"""hqz-supervision —— 通用 Excel 网格填表工具（B1 本地预览版）。

约束：C01 参数驱动 / C02 参数语法 / C03 不影响其他业务（本地 127.0.0.1:8720）
     C04 通用无调查业务 / C05 账号（雷华雄 admin）/ C06 水印日期不参数化
     C07 相片不落服务器（App 存系统相册 / 浏览器下载，前端仅记录文件名提示）
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
        CREATE TABLE IF NOT EXISTS accept_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workbook_id INTEGER NOT NULL,
            row_idx INTEGER NOT NULL,
            xiaoban TEXT,
            field TEXT NOT NULL,
            old_value TEXT, new_value TEXT,
            operator TEXT NOT NULL,
            created_at TEXT NOT NULL);
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
    blob = fs.read()   # 先读全量字节：既供解析，也原样留存作导出模板
    try:
        sheet_name, headers, rows, config, param_rows = parse_workbook_storage(io.BytesIO(blob))
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
    # 原始模板留存（导出按上传模板回填，保留全部格式）
    src_dir = UPLOAD_DIR / str(wid)
    src_dir.mkdir(parents=True, exist_ok=True)
    ext = '.xlsm' if fs.filename.lower().endswith('.xlsm') else '.xlsx'
    (src_dir / f'source{ext}').write_bytes(blob)
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


# 变更日志覆盖的字段（列名在模板中存在才记录，通用不硬编码业务模板）
ACCEPT_LOG_FIELDS = ('验收人', '验收日期', '验收时间', '验收结果', '验收备注')


@bp.route('/api/workbooks/<int:wid>/rows/<int:ridx>', methods=['POST'])
@login_required
def api_save_row(wid, ridx):
    """单行自动保存（前端两列表单 onchange 触发，对齐 hqz-survey 编辑体验）。
    验收相关字段的每次变化写入 accept_logs（操作人+时间）。"""
    vals = (request.get_json(silent=True) or {}).get('values')
    if not isinstance(vals, list):
        return jsonify(error='values 必须为数组'), 400
    con = db()
    try:
        r = con.execute('SELECT headers, rows FROM workbooks WHERE id=?', (wid,)).fetchone()
        if not r:
            abort(404)
        headers = json.loads(r['headers'])
        rows = json.loads(r['rows'])
        if not (0 <= ridx < len(rows)):
            abort(404)
        if len(vals) != len(rows[ridx]):
            return jsonify(error=f'列数不匹配：期望 {len(rows[ridx])} 列，收到 {len(vals)}'), 400
        old_row = rows[ridx]
        rows[ridx] = [_norm_cell(v) for v in vals]

        # 验收字段变更日志：仅记录验收4字段（验收人/验收日期(时间)/验收结果/验收备注）的变化
        logs = []
        for i, h in enumerate(headers):
            if h not in ACCEPT_LOG_FIELDS:
                continue
            ov = '' if old_row[i] is None else str(old_row[i]).strip()
            nv = '' if rows[ridx][i] is None else str(rows[ridx][i]).strip()
            if ov != nv:
                logs.append((h, ov, nv))
        if logs:
            hi = headers.index('小班号') if '小班号' in headers else -1
            xiaoban = '' if hi < 0 else str(old_row[hi] or '').strip()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            op = session.get('user') or ''
            con.executemany(
                'INSERT INTO accept_logs(workbook_id,row_idx,xiaoban,field,old_value,new_value,operator,created_at)'
                ' VALUES(?,?,?,?,?,?,?,?)',
                [(wid, ridx, xiaoban, f, ov, nv, op, now) for f, ov, nv in logs])

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


# ────────────────────────── 导出（按上传模板回填，保留原始格式） ──────────────────────────
# C07：相片不落服务器，原相片上传/预览/zip 路由已移除（v0.8）。

_XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
_XLSM_MIME = 'application/vnd.ms-excel.sheet.macroEnabled.12'


def _export_workbook(wid):
    """用上传时留存的原始模板（source.xlsx/xlsm）回填数据行，保留模板全部格式。
    返回 (BytesIO, 下载文件名, mimetype)；模板缺失 404。"""
    con = db()
    r = con.execute('SELECT * FROM workbooks WHERE id=?', (wid,)).fetchone()
    con.close()
    if not r:
        abort(404)
    ext = '.xlsm' if r['name'].lower().endswith('.xlsm') else '.xlsx'
    src = UPLOAD_DIR / str(wid) / f'source{ext}'
    if not src.exists():
        abort(404, description='原始模板文件缺失（该工作簿为旧版上传），请重新上传后导出')
    headers = json.loads(r['headers'])
    rows = json.loads(r['rows'])
    wb = openpyxl.load_workbook(src, keep_vba=(ext == '.xlsm'))
    if r['sheet_name'] not in wb.sheetnames:
        abort(500, description=f'模板中找不到数据 sheet「{r["sheet_name"]}」')
    ws = wb[r['sheet_name']]
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)   # 清掉模板里的示例/旧数据行，表头及格式保留
    for row in rows:
        ws.append((list(row) + [''] * len(headers))[:len(headers)])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stem = re.sub(r'\.xlsxm?$', '', r['name'], flags=re.IGNORECASE)
    return buf, f'{stem}_导出{ext}', _XLSM_MIME if ext == '.xlsm' else _XLSX_MIME


@bp.route('/api/workbooks/<int:wid>/export', methods=['GET'])
@login_required
def api_export(wid):
    """前端导出：登录用户即可按上传模板导出当前数据。"""
    buf, name, mime = _export_workbook(wid)
    return send_file(buf, as_attachment=True, download_name=name, mimetype=mime)


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
    """后台下载：与前端导出同链路（按上传模板回填，保留格式）。"""
    buf, name, mime = _export_workbook(wid)
    return send_file(buf, as_attachment=True, download_name=name, mimetype=mime)


@bp.route('/admin/api/accept-logs', methods=['GET'])
@admin_required
def admin_accept_logs():
    """验收字段变更日志（最近 300 条，新在前）。"""
    con = db()
    rows = con.execute(
        'SELECT l.id, l.workbook_id, w.name AS wb_name, l.row_idx, l.xiaoban,'
        ' l.field, l.old_value, l.new_value, l.operator, l.created_at'
        ' FROM accept_logs l LEFT JOIN workbooks w ON w.id = l.workbook_id'
        ' ORDER BY l.id DESC LIMIT 300').fetchall()
    con.close()
    return jsonify(logs=[dict(x) for x in rows])


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

    def __getattr__(self, name):
        # gateway mount 会对工厂返回值访问 .config 等属性，必须透传给 Flask app
        return getattr(self.app, name)

    def __call__(self, environ, start_response):
        if environ.get('PATH_INFO', '') == '':
            environ['PATH_INFO'] = '/'
        return self.app(environ, start_response)


init_db()

if __name__ == '__main__':
    print(f'hqz-supervision 本地预览 → http://127.0.0.1:{PORT} （不影响其他业务）')
    create_app().run(host='127.0.0.1', port=PORT, debug=False)
