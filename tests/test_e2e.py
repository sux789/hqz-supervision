#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端测试（v0.23，C11）：真实模板解析 + 完整 HTTP 链路。

用法：python tests/test_e2e.py      # 全绿退出码 0

注意：会在本地库 data/app.sqlite3 里临时建工作簿，跑完自动删除（含源模板目录）。
需要 data/ 下存在真实模板文件；缺失的用例会标 SKIP 而非失败。
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import openpyxl  # noqa: E402

from main import UPLOAD_DIR, ParamError, create_app, parse_workbook_storage  # noqa: E402

DATA = BASE / 'data'
TPL_OLD = DATA / '中幼林抚育-网格填表模板.xlsx'
TPL_V2 = DATA / '中幼林抚育-网格填表模板v2.xlsx'
TPL_SK = DATA / '造林项目-验收模板.xlsx'

_pass = _fail = _skip = 0


def ok(name, cond, detail=''):
    global _pass, _fail
    _pass, _fail = _pass + bool(cond), _fail + (not cond)
    print(f'  {"✅" if cond else "❌"} {name}  {detail}')


def skip(name, why):
    global _skip
    _skip += 1
    print(f'  ⏭  {name}  （SKIP：{why}）')


def parse(path):
    """→ (结果 dict) 或 ('ERR', 错误消息)。"""
    try:
        sn, h, rows, cfg, pr, kc = parse_workbook_storage(open(path, 'rb'))
        return {'sheet': sn, 'cols': len(h), 'rows': len(rows), 'key': kc, 'cfg': cfg}
    except ParamError as e:
        return ('ERR', str(e))


# ── A. 真实模板解析（期望从文件本身推断，不写死——模板会被用户编辑）──

def _txt(v):
    return '' if v is None else str(v).strip()


def file_expectation(path):
    """独立于被测代码，从 xlsx 内容推断"后端应当接受还是拒绝"。

    返回 'accept' / 'skip' / ('reject', 错误里应出现的关键词)。
    """
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        names = wb.sheetnames
        if '参数' not in names:
            return ('reject', '参数') if 'data' in names else 'skip'
        others = [n for n in names if n != '参数']
        if not others:
            return ('reject', '数据 sheet')
        if 'data' in others:
            dname = 'data'
        elif len(others) > 1:
            return ('reject', '无法判断')
        else:
            dname = others[0]

        head = [_txt(h) for h in (next(wb[dname].iter_rows(values_only=True), None) or ())]
        while head and head[-1] == '':
            head.pop()

        uk = None
        for row in wb['参数'].iter_rows(min_col=1, max_col=2, values_only=True):
            if _txt(row[0]).lower() == 'unique-key':
                uk = _txt(row[1])
        if not uk:
            return 'accept'                     # 未声明唯一键 → 不校验
        if uk not in head:
            return ('reject', '不在数据 sheet 表头')
        ci = head.index(uk)
        seen = set()
        for row in wb[dname].iter_rows(min_row=2, values_only=True):
            v = row[ci] if ci < len(row) else None
            if v in (None, '') or _txt(v) == '':
                return ('reject', '为空')
            k = _txt(v)
            if k in seen:
                return ('reject', '重复')
            seen.add(k)
        return 'accept'
    finally:
        wb.close()


print('=== A. 真实模板解析（期望由文件内容推断）===')
for _label, _p in [('A1 v2', TPL_V2), ('A2 造林', TPL_SK), ('A3 旧模板', TPL_OLD)]:
    if not _p.exists():
        skip(_label, '模板文件不存在')
        continue
    exp = file_expectation(_p)
    r = parse(_p)
    got_err = isinstance(r, tuple)
    if exp == 'skip':
        skip(_label, '不是填表模板')
    elif exp == 'accept':
        ok(f'{_label}：文件推断=应通过 → 实际通过',
           not got_err and r['key'],
           r[1] if got_err else f"key={r['key']} rows={r['rows']}")
    else:
        ok(f'{_label}：文件推断=应被拒（含「{exp[1]}」）→ 实际被拒',
           got_err and exp[1] in r[1],
           r[1] if got_err else f'意外通过：{r}')

# ── B. 多 sheet 歧义（真实文件）────────────────────────────
print('\n=== B. 多 sheet 歧义 ===')
if TPL_OLD.exists():
    tmp = Path(tempfile.mkdtemp())
    amb = tmp / 'amb.xlsx'
    shutil.copy(TPL_OLD, amb)
    wb = openpyxl.load_workbook(amb)
    wb.create_sheet('汇总')
    wb.save(amb)
    wb.close()
    r = parse(amb)
    ok('B1 旧模板 + 汇总页（无 data 表）→ 明确报错指名 sheet',
       isinstance(r, tuple) and '无法判断' in r[1], r[1] if isinstance(r, tuple) else r)

    okp = tmp / 'okname.xlsx'
    shutil.copy(TPL_OLD, okp)
    wb = openpyxl.load_workbook(okp)
    for ws in wb.worksheets:          # 不用 wb.active：它不一定指向数据表
        if ws.title != '参数':
            ws.title = 'data'
            break
    wb.create_sheet('说明')
    wb.save(okp)
    wb.close()
    r = parse(okp)
    ok('B2 数据表改名 data + 多一个「说明」页 → 正常解析（多余页可容忍）',
       not isinstance(r, tuple) and r['sheet'] == 'data',
       r[1] if isinstance(r, tuple) else f"sheet={r['sheet']} key={r['key']}")
else:
    skip('B1/B2', '旧模板不存在')

# ── C. HTTP 完整链路 ──────────────────────────────────────
print('\n=== C. HTTP 完整链路（Flask test_client）===')
app = create_app()
c = app.test_client()
r = c.post('/api/login', json={'username': '雷华雄', 'password': 'lhx123'})
tok = (r.get_json() or {}).get('token', '')
ok('C1 登录', r.status_code == 200 and bool(tok), f'HTTP {r.status_code}')
H = {'X-Sup-Token': tok}

if not TPL_V2.exists():
    skip('C2~C7', '需要 v2 模板做基底')
else:
    tmp = Path(tempfile.mkdtemp())
    good = tmp / 'good.xlsx'
    shutil.copy(TPL_V2, good)
    wb = openpyxl.load_workbook(good)
    ws = wb['data']
    # 唯一键改指新列「地块号」，并让该列逐行唯一 —— 验证参数化真的生效
    ci = ws.max_column + 1
    ws.cell(1, ci).value = '地块号'
    for r_ in range(2, ws.max_row + 1):
        ws.cell(r_, ci).value = f'DK-{r_ - 1:05d}'
    wsp = wb['参数']
    for r_ in range(1, wsp.max_row + 1):
        if str(wsp.cell(r_, 1).value).strip() == 'unique-key':
            wsp.cell(r_, 2).value = '地块号'
    wb.save(good)
    wb.close()

    def upload(path, name):
        with open(path, 'rb') as f:
            return c.post('/api/workbooks', data={'file': (f, name)},
                          headers=H, content_type='multipart/form-data')

    rr = upload(good, 'good.xlsx')
    jj = rr.get_json() or {}
    ok('C2 上传（unique-key=地块号，数据唯一）→ 通过', rr.status_code == 200 and jj.get('ok'),
       f'HTTP {rr.status_code} {jj}')
    wid = jj.get('id')

    if wid:
        d = c.get(f'/api/workbooks/{wid}', headers=H).get_json() or {}
        ok('C3 返回 key_column = 地块号（参数化生效）', d.get('key_column') == '地块号',
           f"key_column={d.get('key_column')!r}")
        ok('C4 返回 log_fields（前端联动用，不再各写一份）',
           d.get('log_fields') == ['验收人', '验收日期', '验收结果', '验收备注'],
           f"log_fields={d.get('log_fields')}")
        rr = c.get(f'/api/workbooks/{wid}/export', headers=H)
        ok('C5 导出仍是真 xlsx', rr.status_code == 200 and rr.data[:2] == b'PK',
           f'HTTP {rr.status_code} {len(rr.data)}B')
        rr = c.post(f'/api/workbooks/{wid}/rows/0', json={'values': d['rows'][0]}, headers=H)
        ok('C6 单行保存仍可用', rr.status_code == 200, f'HTTP {rr.status_code}')

        # 删除清理（本次修复项）：源模板目录必须一并删掉
        src = UPLOAD_DIR / str(wid)
        ok('C7 删除前源模板目录存在（前置条件）', src.exists(), str(src))
        c.delete(f'/api/workbooks/{wid}', headers=H)
        ok('C8 删除后源模板目录被清理（本次修复）', not src.exists(), str(src))
        ok('C9 删除后 API 取不到该工作簿',
           c.get(f'/api/workbooks/{wid}', headers=H).status_code == 404)

    # 坏例：v2 原样（YJ-643 重复）
    rr = upload(TPL_V2, 'v2.xlsx'); jj = rr.get_json() or {}
    ok('C10 YJ-643 重复 → 被拒', rr.status_code == 400 and '重复' in jj.get('error', ''),
       f"HTTP {rr.status_code} {jj.get('error')}")

    # 坏例：v2 去掉唯一键声明 → 不再校验，应通过（按 key 名定位，不假设它在最后一行）
    nk = tmp / 'nokey.xlsx'
    shutil.copy(TPL_V2, nk)
    wb = openpyxl.load_workbook(nk)
    _wsp = wb['参数']
    for r_ in range(_wsp.max_row, 0, -1):
        if str(_wsp.cell(r_, 1).value or '').strip().lower() == 'unique-key':
            _wsp.delete_rows(r_)
            break
    wb.save(nk)
    wb.close()
    rr = upload(nk, 'nokey.xlsx'); jj = rr.get_json() or {}
    ok('C11 未声明 unique-key → 不校验，通过', rr.status_code == 200 and jj.get('ok'),
       f"HTTP {rr.status_code} {jj}")
    if jj.get('id'):
        d = c.get(f"/api/workbooks/{jj['id']}", headers=H).get_json() or {}
        ok('C12 未声明时 key_column 回落小班号', d.get('key_column') == '小班号',
           f"key_column={d.get('key_column')!r}")
        c.delete(f"/api/workbooks/{jj['id']}", headers=H)

    # C13 参数 sheet 被改名 → 被拒（合成用例，不依赖用户模板现状）
    ren = tmp / 'renamed.xlsx'
    shutil.copy(TPL_V2, ren)
    wb = openpyxl.load_workbook(ren)
    for _ws in wb.worksheets:
        if _ws.title == '参数':
            _ws.title = 'Sheet1'
            break
    wb.save(ren)
    wb.close()
    with open(ren, 'rb') as f:
        rr = c.post('/api/workbooks', data={'file': (f, 'renamed.xlsx')},
                    headers=H, content_type='multipart/form-data')
    jj = rr.get_json() or {}
    ok('C13 「参数」sheet 被改名成 Sheet1 → 被拒', rr.status_code == 400 and '参数' in jj.get('error', ''),
       f"HTTP {rr.status_code} {jj.get('error')}")

    # C14 数据 sheet 名为 data 时，多一个「说明」页仍可上传（合成用例）
    #     先摘掉 unique-key 行，让唯一键不再拦截，从而单独验证"多余 sheet 可容忍"
    multi = tmp / 'multi.xlsx'
    shutil.copy(TPL_V2, multi)
    wb = openpyxl.load_workbook(multi)
    wsp = wb['参数']
    for r_ in range(wsp.max_row, 0, -1):
        if str(wsp.cell(r_, 1).value or '').strip().lower() == 'unique-key':
            wsp.delete_rows(r_)
            break
    wb.create_sheet('说明')
    wb.save(multi)
    wb.close()
    with open(multi, 'rb') as f:
        rr = c.post('/api/workbooks', data={'file': (f, 'multi.xlsx')},
                    headers=H, content_type='multipart/form-data')
    jj = rr.get_json() or {}
    ok('C14 有 data 表时多余「说明」页可容忍 → 上传通过',
       rr.status_code == 200 and jj.get('ok'), f"HTTP {rr.status_code} {jj}")
    if jj.get('id'):
        c.delete(f"/api/workbooks/{jj['id']}", headers=H)

print('\n' + '=' * 62)
print(f'通过 {_pass} / 失败 {_fail} / 跳过 {_skip}')
sys.exit(1 if _fail else 0)
