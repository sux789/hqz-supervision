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
import threading
import zipfile
from datetime import datetime
from functools import wraps
from pathlib import Path

import openpyxl
from flask import (Blueprint, Flask, abort, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

import sync_cloud
from param_parser import ParamError, _s, parse_params, split_list

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / 'data'
UPLOAD_DIR = DATA_DIR / 'workbooks'
TRACK_DIR = DATA_DIR / 'tracks'
PENDING_DIR = DATA_DIR / 'pending'  # 云同步待推送缓冲（百度成功即删，C07 不长期存储）
DB_PATH = DATA_DIR / 'app.sqlite3'
for d in (UPLOAD_DIR, TRACK_DIR, PENDING_DIR):
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
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS photo_sync(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workbook_id INTEGER NOT NULL,
            xiaoban TEXT DEFAULT '',
            filename TEXT NOT NULL,
            remote_path TEXT NOT NULL,
            qiniu_key TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'received',
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            synced_at TEXT, purged_at TEXT,
            kind TEXT NOT NULL DEFAULT 'photo',
            orig_size INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_photo_state ON photo_sync(state);
    ''')
    # 轻量迁移：v0.10 前建的 photo_sync 无 kind/orig_size 列
    cols = {r[1] for r in con.execute('PRAGMA table_info(photo_sync)')}
    if 'kind' not in cols:
        con.execute("ALTER TABLE photo_sync ADD COLUMN kind TEXT NOT NULL DEFAULT 'photo'")
    if 'orig_size' not in cols:
        con.execute('ALTER TABLE photo_sync ADD COLUMN orig_size INTEGER NOT NULL DEFAULT 0')
    if not con.execute('SELECT 1 FROM users').fetchone():
        con.execute(
            'INSERT INTO users VALUES(?,?,?)',
            ('雷华雄', hashlib.sha256('lhx123'.encode()).hexdigest(), 'admin'))
    # settings 非密钥默认值预置（真实 AK/SK / 百度凭证由一次性脚本写入，不进代码）
    for k, v in {**sync_cloud.SYNC_DEFAULTS, **sync_cloud.VIDEO_DEFAULTS}.items():
        con.execute('INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)', (k, v))
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
    # 返回 user：登录是 fetch 静默完成（页面不刷新），前端需回写 #whoami 的 data-user，
    # 否则验收联动/{{拍照人}} 拿到的用户名为空（v0.8.2 修复）
    return jsonify(ok=True, role=row['role'], user=row['username'])


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


# ────────────────────────── 相片云同步（doc/007） ──────────────────────────
# C07 修订：服务器不长期存储——照片只在七牛 zz-1 暂存，百度成功后按保留期清理。
# 仅 App 端调用（浏览器端不拍照）；开关关闭 → 423，前端静默跳过。

_PHOTO_MAX = 2 * 1024 * 1024  # 2MB（App 端压缩后 300-500KB）


@bp.route('/api/sync/enabled', methods=['GET'])
@login_required
def api_sync_enabled():
    con = db()
    s = sync_cloud.get_settings(con)
    con.close()
    return jsonify(enabled=s.get('sync_enabled') == '1')


@bp.route('/api/photo', methods=['POST'])
@login_required
def api_photo():
    con = db()
    try:
        s = sync_cloud.get_settings(con)
        if s.get('sync_enabled') != '1':
            return jsonify(error='云同步未开启（后台「同步设置」）'), 423
        fs = request.files.get('file')
        if not fs:
            return jsonify(error='缺少 file'), 400
        data = fs.read()
        if not data or len(data) > _PHOTO_MAX:
            return jsonify(error=f'照片大小超出限制（≤{_PHOTO_MAX // 1024}KB）'), 400
        r = request.form
        try:
            wid = int(r.get('workbook_id') or 0)
        except ValueError:
            return jsonify(error='workbook_id 无效'), 400
        filename = re.sub(r'[\\/:*?"<>|]+', '_', (r.get('filename') or '').strip())
        if not filename.lower().endswith('.jpg'):
            filename += '.jpg'
        subdir = re.sub(r'^/+|/+$', '', r.get('subdir') or '')
        if not all(re.match(r'^[^\\/:*?"<>|]+$', seg) for seg in subdir.split('/') if seg):
            return jsonify(error='subdir 含非法字符'), 400
        xiaoban = (r.get('xiaoban') or '').strip()[:64]

        qiniu_key, remote_path = sync_cloud.photo_paths(s, subdir, filename)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cur = con.execute(
            'INSERT INTO photo_sync(workbook_id, xiaoban, filename, remote_path,'
            ' qiniu_key, size, state, created_at) VALUES(?,?,?,?,?,?,?,?)',
            (wid, xiaoban, filename, remote_path, qiniu_key, len(data), 'received', now))
        pid = cur.lastrowid
        con.commit()
        # 待推送缓冲（data/pending/{id}.jpg）：百度成功即删（C07 修订：不长期存储）
        (PENDING_DIR / f'{pid}.jpg').write_bytes(data)

        # 第一跳：七牛暂存（异地备份；失败不阻塞——本地缓冲可支撑后续推送）
        try:
            sync_cloud.QiniuClient(s['sync_qiniu_ak'], s['sync_qiniu_sk'],
                                   s['sync_qiniu_bucket']).put_bytes(
                qiniu_key, data, mime='image/jpeg')
            con.execute("UPDATE photo_sync SET state='qiniu_ok' WHERE id=?", (pid,))
            con.commit()
        except sync_cloud.SyncError as e:
            con.execute('UPDATE photo_sync SET retry_count=retry_count+1, last_error=?'
                        ' WHERE id=?', (str(e)[:500], pid))
            con.commit()

        # 第二跳（懒触发）：立即尝试一次百度推送，失败留给积压等后台手动重推
        pushed = False
        try:
            row = con.execute('SELECT * FROM photo_sync WHERE id=?', (pid,)).fetchone()
            sync_cloud.push_to_baidu(con, row, PENDING_DIR)
            con.commit()
            pushed = True
        except sync_cloud.SyncError as e:
            con.execute("UPDATE photo_sync SET retry_count=retry_count+1, last_error=?"
                        " WHERE id=?", (str(e)[:500], pid))
            con.commit()
        # 积压补推 + 七牛副本清理：放后台 daemon 线程（不阻塞拍照响应；
        # 线程用独立 sqlite 连接，gunicorn worker 退出被杀也无害——下次触发自动续做）
        if pushed:
            def _bg_sync():
                try:
                    c = db()
                    sync_cloud.try_push_pending(c, limit=2, pending_dir=PENDING_DIR)
                    sync_cloud.purge_expired(c, limit=20)
                    c.close()
                except Exception:
                    pass
            threading.Thread(target=_bg_sync, daemon=True).start()
        row = con.execute('SELECT state, last_error FROM photo_sync WHERE id=?', (pid,)).fetchone()
        return jsonify(ok=True, id=pid, state=row['state'],
                       error=row['last_error'] or None)
    finally:
        con.close()


# ────────────────────────── 视频（v0.10）：服务器压缩 → 七牛 → 百度 ──────────────────────────
# 浏览器/WebView 无法转码视频，压缩统一在服务器用 ffmpeg 完成（参数见后台「视频压缩」）。
# 视频体积大：接收即返回（state=received），压缩+推送在后台线程进行，不阻塞手机。

def _video_name(base: str) -> str:
    """视频文件名 = 相片文件名模板渲染结果 + `_视频` 标记（与照片命名区分）。"""
    name = re.sub(r'[\\/:*?"<>|]+', '_', (base or '').strip()) or 'video'
    if not name.endswith('_视频'):
        name += '_视频'
    return name


def _process_video(pid: int, skip_compress: bool = False) -> None:
    """后台：ffmpeg 压缩（手机端已压缩则跳过）→ 七牛暂存 → 百度网盘；失败留状态待重推。"""
    con = db()
    try:
        row = con.execute('SELECT * FROM photo_sync WHERE id=?', (pid,)).fetchone()
        if not row:
            return
        s = sync_cloud.get_settings(con)
        raw = PENDING_DIR / f'{pid}.mp4'
        comp = PENDING_DIR / f'{pid}_c.mp4'
        if not raw.exists():
            con.execute('UPDATE photo_sync SET last_error=? WHERE id=?',
                        ('原始视频缓冲丢失（无法重推）', pid))
            con.commit()
            return
        if skip_compress:
            src = raw  # 手机端已压缩（MediaRecorder），服务器不再重复转码
            con.execute('UPDATE photo_sync SET size=? WHERE id=?', (raw.stat().st_size, pid))
        else:
            ok, msg = sync_cloud.compress_video(raw, comp, s)
            src = comp if ok else raw
            if ok:
                con.execute('UPDATE photo_sync SET size=? WHERE id=?', (comp.stat().st_size, pid))
            else:
                con.execute('UPDATE photo_sync SET last_error=? WHERE id=?',
                            (f'压缩失败（改用原文件）：{msg}'[:400], pid))
        con.commit()
        # 七牛暂存（异地备份）
        try:
            mime = 'video/mp4' if row['qiniu_key'].lower().endswith('.mp4') else 'video/webm'
            sync_cloud.QiniuClient(s['sync_qiniu_ak'], s['sync_qiniu_sk'],
                                   s['sync_qiniu_bucket']).put_bytes(
                row['qiniu_key'], src.read_bytes(), mime=mime)
            con.execute("UPDATE photo_sync SET state='qiniu_ok' WHERE id=?", (pid,))
            con.commit()
        except sync_cloud.SyncError as e:
            con.execute('UPDATE photo_sync SET retry_count=retry_count+1, last_error=? WHERE id=?',
                        (str(e)[:500], pid))
            con.commit()
        # 百度推送
        row = con.execute('SELECT * FROM photo_sync WHERE id=?', (pid,)).fetchone()
        try:
            sync_cloud.push_to_baidu(con, row, PENDING_DIR)
            con.commit()
            sync_cloud.try_push_pending(con, limit=1, pending_dir=PENDING_DIR)
            sync_cloud.purge_expired(con, limit=20)
        except sync_cloud.SyncError as e:
            con.execute('UPDATE photo_sync SET retry_count=retry_count+1, last_error=? WHERE id=?',
                        (str(e)[:500], pid))
            con.commit()
    except Exception:
        pass
    finally:
        con.close()


@bp.route('/api/video/params', methods=['GET'])
@login_required
def api_video_params():
    """手机端录制压缩参数（只读，供前端 MediaRecorder 使用）。"""
    con = db()
    try:
        s = sync_cloud.get_settings(con)
        def _i(k, d):
            try:
                return int(s.get(k) or d)
            except ValueError:
                return d
        return jsonify(rec=s.get('video_phone_rec', '1'),
                       rec_mode=(s.get('video_rec_mode') or 'system'),
                       max_height=_i('video_max_height', 720),
                       bitrate_k=_i('video_maxrate_k', 2500),
                       fps=_i('video_fps', 30),
                       max_seconds=_i('video_max_seconds', 60),
                       max_mb=_i('video_max_mb', 300))
    finally:
        con.close()


@bp.route('/api/video', methods=['POST'])
@login_required
def api_video():
    con = db()
    try:
        s = sync_cloud.get_settings(con)
        if s.get('sync_enabled') != '1':
            return jsonify(error='视频需先开启云同步（后台「同步设置」）'), 423
        fs = request.files.get('file')
        if not fs:
            return jsonify(error='缺少 file'), 400
        try:
            max_mb = int(s.get('video_max_mb') or 300)
        except ValueError:
            max_mb = 300
        data = fs.read()
        if not data:
            return jsonify(error='视频为空'), 400
        if len(data) > max_mb * 1024 * 1024:
            return jsonify(error=f'视频超出上限 {max_mb}MB（后台「视频压缩」可调）'), 400
        r = request.form
        try:
            wid = int(r.get('workbook_id') or 0)
        except ValueError:
            return jsonify(error='workbook_id 无效'), 400
        subdir = re.sub(r'^/+|/+$', '', r.get('subdir') or '')
        if not all(re.match(r'^[^\\/:*?"<>|]+$', seg) for seg in subdir.split('/') if seg):
            return jsonify(error='subdir 含非法字符'), 400
        xiaoban = (r.get('xiaoban') or '').strip()[:64]
        # 手机端 MediaRecorder 录制压缩后上传（precompressed=1）→ 服务器跳过转码；
        # 后缀与真实容器一致（Chrome Android 多为 mp4/h264，旧 WebView 为 webm）
        ext = (r.get('ext') or '').lower().strip('.')
        if ext not in ('mp4', 'webm', 'mov'):
            ext = 'mp4'
        precompressed = (r.get('precompressed') or '') == '1'
        fname = _video_name(r.get('filename') or '') + '.' + ext
        qiniu_key, remote_path = sync_cloud.photo_paths(s, subdir, fname)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cur = con.execute(
            'INSERT INTO photo_sync(workbook_id, xiaoban, filename, remote_path, qiniu_key,'
            ' size, state, created_at, kind, orig_size) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (wid, xiaoban, fname, remote_path, qiniu_key, 0, 'received', now, 'video', len(data)))
        pid = cur.lastrowid
        con.commit()
        (PENDING_DIR / f'{pid}.mp4').write_bytes(data)
        threading.Thread(target=_process_video, args=(pid, precompressed), daemon=True).start()
        return jsonify(ok=True, id=pid, state='received',
                       note='视频已接收，云端压缩同步中' if not precompressed else '视频已接收，同步中')
    finally:
        con.close()


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


# ────────────────────────── APK 直链（手机扫码/直接下载安装） ──────────────────────────

APK_DIR = BASE / 'static'   # 由 deploy.sh 同步，CI 产物放这里


@bp.route('/apk')
def apk_latest():
    """最新 Android 调试包直链（短链，便于二维码/手机输入）：
    取 static/supervision-*.apk 中文件名最大者（带版本号，天然按版本排序）。"""
    files = sorted(APK_DIR.glob('supervision-*.apk'))
    if not files:
        abort(404, description='尚无 APK 产物（CI 打包后放入 static/）')
    f = files[-1]
    return send_file(f, as_attachment=True, download_name=f.name,
                     mimetype='application/vnd.android.package-archive')


# ────────────────────────── 后台：同步设置与状态（doc/007 §5/§7） ──────────────────────────

_SYNC_EDITABLE = set(sync_cloud.SYNC_DEFAULTS) | set(sync_cloud.VIDEO_DEFAULTS)  # 允许后台写入的键
_SYNC_SECRET = sync_cloud.SECRET_KEYS


def _mask(s: dict) -> dict:
    out = dict(s)
    out['has_qiniu_sk'] = bool(s.get('sync_qiniu_sk'))
    out['has_baidu_secret'] = bool(s.get('sync_baidu_secret_key'))
    tok = sync_cloud._load_token(s)
    out['has_token'] = bool(tok.get('access_token'))
    out['token_expires_at'] = (int(tok['expires_at']) if tok.get('expires_at') else None)
    for k in _SYNC_SECRET:
        out.pop(k, None)
    return out


@bp.route('/admin/api/sync/settings', methods=['GET'])
@admin_required
def admin_sync_get():
    con = db()
    s = sync_cloud.get_settings(con)
    con.close()
    ready, missing = sync_cloud.sync_ready(s)
    return jsonify(settings=_mask(s), ready=ready, missing=missing)


@bp.route('/admin/api/sync/settings', methods=['POST'])
@admin_required
def admin_sync_set():
    data = request.get_json(force=True)
    con = db()
    try:
        for k, v in (data.get('settings') or {}).items():
            if k not in _SYNC_EDITABLE:
                return jsonify(error=f'未知配置键：{k}'), 400
            v = str(v).strip()
            if k in _SYNC_SECRET and not v:
                continue  # secret 留空 = 保持原值
            if k == 'sync_baidu_token' and v:
                try:
                    tok = json.loads(v)
                    assert tok.get('access_token') and tok.get('refresh_token')
                except Exception:
                    return jsonify(error='token JSON 无效：需含 access_token 与 refresh_token'), 400
                tok.setdefault('expires_at', __import__('time').time() + tok.get('expires_in', 2592000))
                v = json.dumps(tok, ensure_ascii=False)
            if k == 'sync_enabled' and v == '1':
                s = sync_cloud.get_settings(con)
                probe = dict(s)
                probe.update({kk: str(vv).strip() for kk, vv in (data.get('settings') or {}).items()})
                ok, missing = sync_cloud.sync_ready(probe)
                if not ok:
                    return jsonify(error='开启失败，缺配置：' + '、'.join(missing)), 400
            sync_cloud.set_setting(con, k, v)
        con.commit()
        s = sync_cloud.get_settings(con)
        return jsonify(ok=True, settings=_mask(s))
    finally:
        con.close()


@bp.route('/admin/api/sync/status', methods=['GET'])
@admin_required
def admin_sync_status():
    con = db()
    try:
        counts = {r['state']: r['n'] for r in con.execute(
            'SELECT state, COUNT(*) AS n FROM photo_sync GROUP BY state')}
        recent = con.execute(
            'SELECT p.id, p.workbook_id, w.name AS wb_name, p.xiaoban, p.filename,'
            ' p.state, p.retry_count, p.last_error, p.created_at, p.synced_at,'
            ' p.kind, p.size, p.orig_size'
            ' FROM photo_sync p LEFT JOIN workbooks w ON w.id = p.workbook_id'
            ' ORDER BY p.id DESC LIMIT 30').fetchall()
        purged = sync_cloud.purge_expired(con)  # 顺手做生命周期清理
        s = sync_cloud.get_settings(con)
        return jsonify(counts=counts, purged=purged,
                       recent=[dict(x) for x in recent],
                       token_expires_at=_mask(s).get('token_expires_at'))
    finally:
        con.close()


@bp.route('/admin/api/sync/retry', methods=['POST'])
@admin_required
def admin_sync_retry():
    ids = (request.get_json(silent=True) or {}).get('ids') or []
    con = db()
    try:
        if ids:
            rows = con.execute(
                "SELECT * FROM photo_sync WHERE state IN ('received','qiniu_ok')"
                " AND id IN (%s)" % ','.join('?' * len(ids)), ids).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM photo_sync WHERE state IN ('received','qiniu_ok')"
                " ORDER BY id LIMIT 100").fetchall()
        ok = fail = 0
        for row in rows:
            try:
                sync_cloud.push_to_baidu(con, row, PENDING_DIR)
                con.commit()
                ok += 1
            except sync_cloud.SyncError as e:
                con.execute("UPDATE photo_sync SET retry_count=retry_count+1, last_error=?"
                            " WHERE id=?", (str(e)[:500], row['id']))
                con.commit()
                fail += 1
        return jsonify(ok=ok, fail=fail)
    finally:
        con.close()


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
