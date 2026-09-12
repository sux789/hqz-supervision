# -*- coding: utf-8 -*-
"""相片云同步（doc/007）：服务器接收 → 七牛 zz-1 暂存 → 百度网盘。

纯标准库实现（服务器 shared_venv 无 requests/qiniu SDK）：
- 七牛：管理 API 查区域（uc /v4/buckets/{bucket}/region）→ 直传域名回退列表，
  PUT 签名 token = AK:base64_urlsafe(HMAC-SHA1(SK, put_policy))，multipart 直传；
  回读用区域 io 域名 + 下载签名（私有/公有桶通用）。
- 百度：xpan 开放平台三步上传（precreate → superfile2 4MB/片 → create），
  token 自动续期；移植自 trans_v2 cloud/baidu_pan.py（剥离 infra 依赖）。

状态机（photo_sync.state，单向推进，每步幂等）：
    received → qiniu_ok → baidu_ok(终态)；失败留在当前态，retry_count++，last_error。

C07 修订：服务器不长期存储——照片只在七牛暂存，百度成功后按保留期清理副本。
"""
import base64
import hashlib
import hmac
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}

TOKEN_URL = "https://openapi.baidu.com/oauth/2.0/token"
BAIDU_API = "https://pan.baidu.com/rest/2.0/xpan"
BAIDU_UPLOAD = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2"
_BLOCK = 4 * 1024 * 1024  # 百度分片 4MB

# settings 默认值（真实 AK/SK / 百度凭证由一次性脚本写入 DB，不进代码/git）
SYNC_DEFAULTS = {
    'sync_enabled': '0',
    'sync_qiniu_ak': '', 'sync_qiniu_sk': '', 'sync_qiniu_bucket': 'zz-1',
    'sync_baidu_app_key': '', 'sync_baidu_secret_key': '',
    'sync_baidu_app_dir': '/apps/book_translator',
    'sync_baidu_prefix': 'supervision',
    'sync_baidu_token': '',
    'sync_keep_days': '30',
}
SECRET_KEYS = {'sync_qiniu_sk', 'sync_baidu_secret_key', 'sync_baidu_token'}


class SyncError(Exception):
    """同步链路错误，message 可直接给后台提示。"""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def get_settings(con) -> dict:
    s = dict(SYNC_DEFAULTS)
    for r in con.execute('SELECT key, value FROM settings'):
        s[r['key']] = r['value']
    return s


def set_setting(con, key: str, value: str) -> None:
    con.execute('INSERT INTO settings(key, value) VALUES(?,?)'
                ' ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


def sync_ready(s: dict):
    """开关开启前置校验：返回 (ok, 缺失项列表)。"""
    need = ['sync_qiniu_ak', 'sync_qiniu_sk', 'sync_qiniu_bucket',
            'sync_baidu_app_key', 'sync_baidu_secret_key', 'sync_baidu_token']
    missing = [k for k in need if not (s.get(k) or '').strip()]
    return (not missing), missing


def photo_paths(s: dict, subdir: str, filename: str):
    """→ (七牛 key, 百度 remote_path)：与手机相册 Pictures/{目录}/{文件名}.jpg 镜像。"""
    sub = '/'.join(p for p in (subdir or '').split('/') if p)
    key = f"{s['sync_baidu_prefix']}/{sub}/{filename}" if sub else f"{s['sync_baidu_prefix']}/{filename}"
    remote = f"{s['sync_baidu_app_dir'].rstrip('/')}/{key}"
    return key, remote


def _http(url: str, data: bytes = None, headers: dict = None, timeout: int = 30,
          method: str = None) -> bytes:
    """urllib 裸请求；非 2xx 抛 SyncError（带响应片段）。"""
    req = urllib.request.Request(url, data=data, headers=headers or dict(_HEADERS),
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = b''
        try:
            body = e.read()[:300]
        except Exception:
            pass
        raise SyncError(f'HTTP {e.code} {url.split("?")[0]} {body.decode("utf-8", "replace")}')
    except Exception as e:
        raise SyncError(f'{type(e).__name__}: {e} ({url.split("?")[0]})')


def _form(fields: dict) -> bytes:
    return urllib.parse.urlencode(fields).encode()


def _multipart(fields: dict = None, files: list = None):
    """→ (body, content_type)。files: [(field, filename, bytes, mime)]"""
    b = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in (fields or {}).items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for name, fname, content, mime in (files or []):
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{name}"; '
                  f'filename="{fname}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
        out.write(content)
        out.write(b'\r\n')
    out.write(f'--{b}--\r\n'.encode())
    return out.getvalue(), f'multipart/form-data; boundary={b}'


# ────────────────────────── 七牛（暂存层） ──────────────────────────

# 区域未知时的直传回退域名（uc 查询失败才用；z2=华南为 zz-1 实测区域）
_UP_FALLBACK = ('up-z2.qiniup.com', 'upload-z2.qiniup.com',
                'upload.qiniup.com', 'upload-z1.qiniup.com', 'up-z0.qiniup.com')
_IO_FALLBACK = ('iovip-z2.qiniuio.com', 'iovip-z2.qbox.me', 'iovip.qiniuio.com')


class QiniuClient:
    """七牛客户端（签名直传 + io 域名回读）。进程级缓存区域结果。"""

    _region = None  # {up: [...], io: [...]} 类级缓存（4 worker 各自一份，无害）

    def __init__(self, ak: str, sk: str, bucket: str):
        if not (ak and sk and bucket):
            raise SyncError('七牛配置不完整（AK/SK/bucket）')
        self.ak, self.sk, self.bucket = ak, sk, bucket

    def _fetch_region(self):
        if QiniuClient._region:
            return QiniuClient._region
        # v4/query 免签名（同 qiniu SDK 7.18 regions_provider 实现）
        url = f'https://uc.qiniuapi.com/v4/query?ak={urllib.parse.quote(self.ak)}&bucket={urllib.parse.quote(self.bucket)}'
        with urllib.request.urlopen(urllib.request.Request(url, headers=dict(_HEADERS)), timeout=15) as resp:
            data = json.loads(resp.read())
        hosts = (data.get('hosts') or [{}])[0]
        region = {'up': (hosts.get('up') or {}).get('domains') or [],
                  'io': (hosts.get('io') or {}).get('domains') or []}
        if region['up']:
            QiniuClient._region = region
        return region

    def _up_hosts(self):
        try:
            ups = self._fetch_region()['up']
        except Exception:
            ups = []
        return list(dict.fromkeys(ups)) + [h for h in _UP_FALLBACK if h not in ups]

    def _io_hosts(self):
        try:
            ios = self._fetch_region()['io']
        except Exception:
            ios = []
        return list(dict.fromkeys(ios)) + [h for h in _IO_FALLBACK if h not in ios]

    def _upload_token(self, key: str, ttl: int = 3600) -> str:
        policy = {'scope': f'{self.bucket}:{key}', 'deadline': int(time.time()) + ttl}
        enc = _b64(json.dumps(policy, separators=(',', ':')).encode())
        sign = hmac.new(self.sk.encode(), enc.encode(), hashlib.sha1).digest()
        return f'{self.ak}:{_b64(sign)}:{enc}'

    def put_bytes(self, key: str, data: bytes, mime: str = 'application/octet-stream') -> None:
        """直传（scope 绑 key，覆盖式；主机逐个回退，成功即记住）。"""
        token = self._upload_token(key)
        last = None
        for host in self._up_hosts():
            body, ctype = _multipart(
                {'key': key, 'token': token},
                [('file', key, data, mime)])
            try:
                _http(f'https://{host}/', data=body,
                      headers={**_HEADERS, 'Content-Type': ctype, 'Authorization': f'UpToken {token}'},
                      timeout=60)
                return
            except SyncError as e:
                last = e
        raise SyncError(f'七牛上传失败（全部域名）：{last}')

    def fetch_bytes(self, key: str) -> bytes:
        """从区域 io 域名回读（下载签名，私有/公有桶通用）。"""
        deadline = int(time.time()) + 600
        sign = _b64(hmac.new(self.sk.encode(), f'/{key}?e={deadline}'.encode(),
                             hashlib.sha1).digest())
        token = f'{self.ak}:{sign}'
        last = None
        for host in self._io_hosts():
            for scheme in ('https', 'http'):
                url = (f'{scheme}://{host}/{urllib.parse.quote(key, safe="/")}'
                       f'?e={deadline}&token={urllib.parse.quote(token, safe="")}')
                try:
                    return _http(url, timeout=60)
                except SyncError as e:
                    last = e
        raise SyncError(f'七牛回读失败（全部域名）：{last}')

    def delete(self, key: str) -> None:
        path = f'/delete/{_b64(f"{self.bucket}:{key}".encode())}'
        sign = _b64(hmac.new(self.sk.encode(), path.encode() + b'\n', hashlib.sha1).digest())
        _http('https://rs.qiniuapi.com' + path,
              headers={**_HEADERS, 'Authorization': f'QBox {self.ak}:{sign}'}, timeout=15)


# ────────────────────────── 百度网盘（终存层） ──────────────────────────

class BaiduPan:
    """百度网盘开放平台客户端（最小移植：token 续期 + 建目录 + 三步上传 + 删除）。"""

    def __init__(self, app_key: str, secret_key: str, token: dict, on_save=None,
                 app_dir: str = ''):
        self.app_key, self.secret_key = app_key, secret_key
        self.token = token or {}
        self.on_save = on_save  # 续期成功后回调持久化
        # 网盘应用固定目录（/apps/{应用名}，由 app_key 决定）——其下层级才会真正创建
        self.app_dir = '/' + '/'.join(p for p in (app_dir or '').split('/') if p)

    # ---- token ----
    def _at(self) -> str:
        return self.token.get('access_token', '')

    def ensure_token(self):
        exp = self.token.get('expires_at', 0)
        if self._at() and exp and time.time() < exp - 3600:
            return
        rt = self.token.get('refresh_token', '')
        if not rt:
            raise SyncError('百度网盘未授权：请先在后台导入 token')
        new = self._refresh(rt)
        if new:
            self.token = new
            if self.on_save:
                try:
                    self.on_save(new)
                except Exception:
                    pass
            return
        if self._at() and exp and time.time() < exp:
            return  # 刷新失败但旧 token 仍在有效期内
        raise SyncError('百度 token 续期失败，请重新导入 token')

    def _refresh(self, rt: str):
        try:
            body = _http(TOKEN_URL, data=_form({
                'grant_type': 'refresh_token', 'refresh_token': rt,
                'client_id': self.app_key, 'client_secret': self.secret_key,
            }), headers=dict(_HEADERS), timeout=15)
            new = json.loads(body)
            if 'access_token' not in new:
                return None
            new['expires_at'] = time.time() + new.get('expires_in', 2592000)
            return new
        except Exception:
            return None

    # ---- API ----
    def _call_api(self, method: str, params: dict = None, fields: dict = None,
                  files: list = None, timeout: int = 30):
        params = {'access_token': self._at(), 'method': method, **(params or {})}
        # 分片上传走 pcs superfile2 专用端点（xpan/file 会 403 31064 file is not authorized）
        url = (f'{BAIDU_UPLOAD}?' if files else f'{BAIDU_API}/file?') + urllib.parse.urlencode(params)
        if files:
            body, ctype = _multipart(fields, files)
            headers = {**_HEADERS, 'Content-Type': ctype}
        elif fields is not None:
            body, ctype, headers = _form(fields), 'application/x-www-form-urlencoded', dict(_HEADERS)
        else:
            body, headers = None, dict(_HEADERS)
        raw = _http(url, data=body, headers=headers, timeout=timeout)
        try:
            return json.loads(raw)
        except ValueError:
            raise SyncError(f'百度响应非 JSON：{raw[:200]!r}')

    def list_dir(self, remote_dir: str):
        url = (f'{BAIDU_API}/file?' + urllib.parse.urlencode({
            'access_token': self._at(), 'method': 'list', 'dir': remote_dir,
            'order': 'name', 'desc': '0', 'web': '1'}))
        return json.loads(_http(url, headers=dict(_HEADERS), timeout=15)).get('list', [])

    def get_file_meta(self, remote_path: str):
        try:
            for f in self.list_dir(urllib.parse.urlsplit(remote_path).path.rsplit('/', 1)[0] or '/'):
                if f.get('path') == remote_path:
                    return f
        except SyncError:
            pass
        return None

    def mkdir_p(self, remote_dir: str) -> None:
        """递归建目录（rtype=1 同名不转存，-8 视为已存在——trans_v2 踩坑修复）。
        /apps 与 /apps/{应用名} 是百度应用固定层级（errno=102 无权限创建），跳过。"""
        parts = [p for p in remote_dir.split('/') if p]
        base_len = len([p for p in self.app_dir.split('/') if p]) if self.app_dir else 0
        cur = ''
        for i, p in enumerate(parts):
            cur += '/' + p
            if i < base_len:
                continue  # 应用固定前缀（/apps、/apps/xxx），视为已存在
            data = self._call_api('create', fields={'path': cur, 'isdir': '1',
                                                    'autoinit': '1', 'rtype': '1'})
            if data.get('errno', -1) not in (0, -8):
                raise SyncError(f'建目录失败 {cur}: errno={data.get("errno")}')

    def delete_file(self, remote_path: str) -> bool:
        data = self._call_api('filemanager',
                              params={'opera': 'delete'},
                              fields={'async': '0', 'filelist': json.dumps([remote_path])})
        if data.get('errno') != 0:
            return False
        info = data.get('info', [])
        return bool(info) and info[0].get('errno') == 0

    def upload_bytes(self, data: bytes, remote_path: str) -> dict:
        """上传字节到网盘（覆盖式：已存在先删；返回 create 响应）。"""
        self.ensure_token()
        parent = remote_path.rsplit('/', 1)[0]
        if parent and parent != remote_path:
            self.mkdir_p(parent)
        if self.get_file_meta(remote_path):
            self.delete_file(remote_path)
            for _ in range(6):  # 删除有延迟（trans_v2 踩坑：立即重传生成 _时间戳 副本）
                time.sleep(0.5)
                if not self.get_file_meta(remote_path):
                    break
        md5s = [hashlib.md5(data[i:i + _BLOCK]).hexdigest()
                for i in range(0, len(data), _BLOCK)] or [hashlib.md5(b'').hexdigest()]
        pre = self._call_api('precreate', fields={
            'path': remote_path, 'size': str(len(data)), 'isdir': '0',
            'autoinit': '1', 'block_list': json.dumps(md5s)})
        if pre.get('errno', 0) != 0:
            raise SyncError(f'precreate 失败: errno={pre.get("errno")}')
        uploadid = pre['uploadid']
        for idx, i in enumerate(range(0, len(data), _BLOCK)):
            block = data[i:i + _BLOCK]
            resp = self._call_api(
                'upload', params={'type': 'tmpfile', 'path': remote_path,
                                  'uploadid': uploadid, 'partseq': str(idx)},
                files=[('file', 'blob', block, 'application/octet-stream')], timeout=120)
            # superfile2 成功返回 {"md5":...}（errno 缺省视为 0）
            if resp.get('errno', 0) != 0:
                raise SyncError(f'分片 {idx} 上传失败: errno={resp.get("errno")}')
        created = self._call_api('create', fields={
            'path': remote_path, 'size': str(len(data)), 'isdir': '0',
            'block_list': json.dumps(md5s), 'uploadid': uploadid})
        if created.get('errno', 0) != 0:
            raise SyncError(f'create 失败: errno={created.get("errno")}')
        return created


# ────────────────────────── 同步流水线（photo_sync 表操作） ──────────────────────────
# 回读源优先级：服务器 data/pending/{id}.jpg（百度成功即删，C07 不长期存储）
#             → 七牛 io 回读（需 bucket 绑定下载域名，当前 zz-1 未绑，作将来兜底）

def _clients(con):
    s = get_settings(con)
    ok, missing = sync_ready(s)
    if not ok:
        raise SyncError('同步配置不完整：' + '、'.join(missing))
    q = QiniuClient(s['sync_qiniu_ak'], s['sync_qiniu_sk'], s['sync_qiniu_bucket'])
    pan = BaiduPan(
        s['sync_baidu_app_key'], s['sync_baidu_secret_key'],
        _load_token(s), app_dir=s.get('sync_baidu_app_dir') or '',
        on_save=lambda t: (set_setting(con, 'sync_baidu_token',
                                       json.dumps(t, ensure_ascii=False)), con.commit()))
    return s, q, pan


def _load_token(s: dict) -> dict:
    try:
        return json.loads(s.get('sync_baidu_token') or '{}')
    except ValueError:
        return {}


def push_to_baidu(con, row, pending_dir=None) -> None:
    """(received|qiniu_ok) → baidu_ok。失败抛 SyncError（调用方记 last_error/retry_count）。"""
    s, q, pan = _clients(con)
    data = None
    if pending_dir:
        p = Path(pending_dir) / f"{row['id']}.jpg"
        if p.exists():
            data = p.read_bytes()
    if data is None:
        data = q.fetch_bytes(row['qiniu_key'])  # 兜底：七牛回读（需绑定下载域名）
    pan.upload_bytes(data, row['remote_path'])
    con.execute("UPDATE photo_sync SET state='baidu_ok', synced_at=?, last_error='' WHERE id=?",
                (time.strftime('%Y-%m-%d %H:%M:%S'), row['id']))
    if pending_dir:
        p = Path(pending_dir) / f"{row['id']}.jpg"
        if p.exists():
            p.unlink()


def try_push_pending(con, limit: int = 3, pending_dir=None) -> int:
    """补推积压（懒触发：新照片到达后调用；不引入常驻进程）。返回成功数。"""
    rows = con.execute(
        "SELECT * FROM photo_sync WHERE state IN ('received','qiniu_ok')"
        " ORDER BY id LIMIT ?", (limit,)).fetchall()
    ok = 0
    for row in rows:
        try:
            push_to_baidu(con, row, pending_dir)
            con.commit()
            ok += 1
        except SyncError as e:
            con.execute("UPDATE photo_sync SET retry_count=retry_count+1, last_error=? WHERE id=?",
                        (str(e)[:500], row['id']))
            con.commit()
            break  # 连续失败即止，避免白烧 API 配额
    return ok


def purge_expired(con) -> int:
    """清理七牛副本：baidu_ok 且 synced_at 超过保留期（keep_days=0 立即清）。"""
    s = get_settings(con)
    try:
        keep = int(s.get('sync_keep_days') or 30)
    except ValueError:
        keep = 30
    if keep < 0:
        return 0
    rows = con.execute(
        "SELECT id, qiniu_key FROM photo_sync WHERE state='baidu_ok' AND purged_at IS NULL"
        " AND synced_at <= datetime('now', ?)", (f'-{keep} days',)).fetchall()
    if not rows:
        return 0
    try:
        q = QiniuClient(s['sync_qiniu_ak'], s['sync_qiniu_sk'], s['sync_qiniu_bucket'])
    except SyncError:
        return 0
    n = 0
    for r in rows:
        try:
            q.delete(r['qiniu_key'])
        except SyncError:
            continue  # 删除失败下次再清
        con.execute('UPDATE photo_sync SET purged_at=? WHERE id=?',
                    (time.strftime('%Y-%m-%d %H:%M:%S'), r['id']))
        n += 1
    con.commit()
    return n
