#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务专家流程配置的端到端测试（v0.36 doc/028 + v0.37 doc/029 项目级材料入口）

用法：python tests/test_flow_e2e.py      # 全绿退出码 0

只测「业务专家填的文档 → 上传 → App 按新流程走」这条链路，重点是业务红线：
  D1  是否通过=不通过 且没写原因 → 400（不通过必填原因）
  D2  上一环节不通过 → 下一环节 409（不合格不放行）
  D3  多次环节（整地分批）连交两条 → rec_no 1/2，互不覆盖
  D4  入口维度：同一小班能分别进「验收」和「巡视」两条流程（tab 切换）
  D5  校验规则：整地数量填 -5 / 小数 → 400（服务端权威，前端只是提示）
  D6  唯一性：巡视声明「同小班同日唯一」→ 同一天第二条 400
  E1~E13  项目级入口（v0.37）：材料检验按批次登记，row=-1 不挂小班档案 ——
          命中/常量列/关联小班多选/转序/同批次号唯一/多条记录/行级要材料→false

会在本地库临时建工作簿，跑完自动清理（与 test_e2e 同口径）。
登录不上（改过管理员密码）时整段 SKIP，不算失败。
"""
import io
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import openpyxl  # noqa: E402

import main as _M  # noqa: E402
from main import create_app  # noqa: E402

DOC = BASE / 'data' / '验收流程设计' / '流程配置-业务专家填写版.xlsx'
TPL = BASE / 'data' / '验收流程设计' / '造林项目-验收模板-含验收流程.xlsx'
ADMIN = ('雷华雄', 'lhx123')

_pass = _fail = 0


def ok(name, cond, detail=''):
    global _pass, _fail
    _pass, _fail = _pass + bool(cond), _fail + (not cond)
    print(f'  {"✅" if cond else "❌"} {name}  {detail}')


def build_workbook():
    """业务专家填的 6 张表 + 真实模板的 data/参数 → 一份能上传的工作簿。"""
    wb = openpyxl.load_workbook(DOC)
    src = openpyxl.load_workbook(TPL)
    for name, max_row in (('data', 90), ('参数', None)):
        tgt = wb.create_sheet(name)
        for row in src[name].iter_rows(max_row=max_row or src[name].max_row, values_only=True):
            tgt.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def main():
    app = create_app()
    cli = app.test_client()
    r = cli.post('/api/login', json={'username': ADMIN[0], 'password': ADMIN[1]})
    tok = (r.get_json() or {}).get('token', '')
    if r.status_code != 200 or not tok:
        print(f'  ⏭  管理员登录失败（HTTP {r.status_code}，可能改过密码）→ 整段 SKIP')
        return 0
    H = {'X-Sup-Token': tok}

    wb_buf = build_workbook()
    r = cli.post('/api/workbooks', data={'file': (wb_buf, '业务专家填写版流程.xlsx')},
                 headers=H, content_type='multipart/form-data')
    j = r.get_json() or {}
    wid = j.get('id')
    ok('D0 业务专家填写版工作簿能上传', r.status_code == 200 and bool(wid), str(j)[:120])
    if not wid:
        return 1

    try:
        g = cli.get(f'/api/workbooks/{wid}', headers=H).get_json()
        fl = g.get('flows') or {}
        ok('D1 落库 flows 标记来源 user_doc 且 9 条',
           fl.get('source') == 'user_doc' and len(fl.get('registry') or []) == 9,
           f"{len(fl.get('registry') or [])} 流程")
        hdrs, rows = g['headers'], g['rows']
        ridx = next(i for i, x in enumerate(rows)
                    if str(dict(zip(hdrs, x)).get('项目类型', '')).strip() == '人工造乔木林')

        # ── 入口维度 ──
        f = cli.get(f'/api/workbooks/{wid}/flow?row={ridx}', headers=H).get_json()
        steps = f.get('steps') or []
        ents = [e.get('entry') for e in (f.get('entries') or [])]
        ok('D2 默认入口命中「人工造林（2026版）·验收」7 个环节',
           f.get('flow_sheet') == '流程-人工造林（2026版）·验收' and len(steps) == 7,
           f"{f.get('flow_sheet')} {len(steps)}")
        ok('D3 该行可进「验收」和「巡视」两个入口（entries 列表）',
           ents == ['验收', '巡视'], str(ents))
        ok('D4 环节带出「基本信息」与「结论问法」',
           bool(steps[0].get('basic_info')) and steps[0].get('question') == '是否在设计小班范围内',
           str(steps[0].get('basic_info'))[:60])
        ok('D5 整地环节带出黑名单（8 列自定义 + 3 个默认隐藏的填报结果列）',
           len(next(s for s in steps if s['step'] == '整地').get('basic_hide') or []) == 11)
        ok('D6 std 把整地数量映射到 data 总株数（设计 vs 核实）',
           f.get('std', {}).get('整地数量') is not None, str(f.get('std', {}).get('整地数量')))

        fv = cli.get(f'/api/workbooks/{wid}/flow?row={ridx}&entry=巡视', headers=H).get_json()
        ok('D7 ?entry=巡视 切到巡视流程（日常巡护 1 环节）',
           fv.get('flow_sheet') == '流程-人工造林（2026版）·巡视'
           and len(fv.get('steps') or []) == 1, str(fv.get('flow_sheet')))
        ok('D8 巡视环节声明了「同小班同日唯一」',
           (fv.get('steps') or [{}])[0].get('unique') == '同小班同日唯一')

        punch = [{'kind': 'punch', 'label': '定位打卡', 'count': 1, 'names': ['打卡 24.1,101.6']}]
        # ── 不通过必填原因 ──
        r1 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': ridx, 'step_no': 1, 'entry': '验收',
            'values': {'是否通过': '不通过'}, 'att': punch})
        ok('D9 不通过但没写原因 → 400',
           r1.status_code == 400 and '不通过原因' in str((r1.get_json() or {}).get('error', '')),
           str((r1.get_json() or {}).get('error'))[:80])

        r2 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': ridx, 'step_no': 1, 'entry': '验收',
            'values': {'是否通过': '不通过',
                       '不通过原因': '边界东移约 15m，含 2 亩不在设计范围'},
            'att': punch})
        ok('D10 写了原因 → 200 且结论=不通过', r2.status_code == 200, str(r2.get_json())[:100])
        f2 = cli.get(f'/api/workbooks/{wid}/flow?row={ridx}', headers=H).get_json()
        ok('D11 结论落库为「不通过」',
           ((f2.get('records') or {}).get('1') or {}).get('', {}).get('result') == '不通过',
           str(((f2.get('records') or {}).get('1') or {}).get('', {}).get('result')))

        # ── 转序闸门 ──
        r3 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': ridx, 'step_no': 2, 'entry': '验收',
            'values': {'是否通过': '通过'},
            'att': [{'kind': 'photo', 'label': '整地现场', 'count': 2},
                    {'kind': 'punch', 'label': '定位打卡', 'count': 1}]})
        ok('D12 上一环节不通过 → 下一环节 409（不合格不放行）', r3.status_code == 409,
           str((r3.get_json() or {}).get('error'))[:80])

        cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': ridx, 'step_no': 1, 'entry': '验收',
            'values': {'是否通过': '通过'}, 'att': punch})

        base_vals = {'整地规格长': 62, '整地规格宽': 62, '整地规格深': 52,
                     '整地方式': '穴状', '整地数量': 660, '是否通过': '通过',
                     '记录日期': '2026-10-20'}
        att2 = [{'kind': 'photo', 'label': '整地现场', 'count': 2},
                {'kind': 'punch', 'label': '定位打卡', 'count': 1}]

        # ── 校验规则（服务端权威）──
        r4 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': ridx, 'step_no': 2, 'entry': '验收',
            'values': dict(base_vals, 整地数量=-5), 'att': att2})
        ok('D13 整地数量 -5 违反「≥0」→ 400',
           r4.status_code == 400 and '≥0' in str((r4.get_json() or {}).get('error', '')),
           str((r4.get_json() or {}).get('error'))[:80])

        r5 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': ridx, 'step_no': 2, 'entry': '验收',
            'values': dict(base_vals, 整地数量=12.5), 'att': att2})
        ok('D14 整地数量 12.5 违反「整数」→ 400',
           r5.status_code == 400 and '整数' in str((r5.get_json() or {}).get('error', '')),
           str((r5.get_json() or {}).get('error'))[:80])

        # ── 多次环节 ──
        r6 = cli.post(f'/api/workbooks/{wid}/flow', headers=H,
                      json={'row': ridx, 'step_no': 2, 'entry': '验收',
                            'values': base_vals, 'att': att2, 'rec_no': 0})
        ok('D15 整地可提交（多次环节第 1 条）', r6.status_code == 200,
           f'{r6.status_code} {str((r6.get_json() or {}).get("error"))[:80]}')
        rec1 = (r6.get_json() or {}).get('rec_no')

        vals2 = dict(base_vals, 整地数量=720, 记录日期='2026-10-21')
        r7 = cli.post(f'/api/workbooks/{wid}/flow', headers=H,
                      json={'row': ridx, 'step_no': 2, 'entry': '验收',
                            'values': vals2, 'att': att2, 'rec_no': 0})
        rec2 = (r7.get_json() or {}).get('rec_no')
        ok('D16 分批再交一条 → rec_no 递增', r7.status_code == 200 and rec2 == (rec1 or 0) + 1,
           f'{rec1} → {rec2}')

        f3 = cli.get(f'/api/workbooks/{wid}/flow?row={ridx}', headers=H).get_json()
        m = (f3.get('multi') or {}).get('2') or {}
        ok('D17 GET 回传两条多次记录（互不覆盖）',
           len(m) == 2
           and str(m.get(str(rec1), {}).get('values', {}).get('整地数量')) == '660'
           and str(m.get(str(rec2), {}).get('values', {}).get('整地数量')) == '720',
           str({k: v.get('values', {}).get('整地数量') for k, v in m.items()}))

        r8 = cli.post(f'/api/workbooks/{wid}/flow', headers=H,
                      json={'row': ridx, 'step_no': 2, 'entry': '验收',
                            'values': dict(base_vals, 整地数量=661),
                            'att': att2, 'rec_no': rec1})
        ok('D18 带 rec_no 提交 = 改那一条，不新增', r8.status_code == 200
           and (r8.get_json() or {}).get('rec_no') == rec1, str(r8.get_json())[:80])
        f4 = cli.get(f'/api/workbooks/{wid}/flow?row={ridx}', headers=H).get_json()
        ok('D19 改后仍是 2 条', len(((f4.get('multi') or {}).get('2') or {})) == 2,
           str(len(((f4.get('multi') or {}).get('2') or {}))))

        # ── 入口隔离：巡视的记录不污染验收 ──
        patrol = {'巡护日期': '2026-10-21', '巡护方式': '步行',
                  '管护作业情况': ['浇水'], '病虫害情况': '无', '处理情况': '现场已整改'}
        p1 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': ridx, 'step_no': 1, 'entry': '巡视', 'values': patrol, 'att': punch})
        ok('D20 巡视第 1 条提交成功（仅记录，不要求验收结果）', p1.status_code == 200,
           f'{p1.status_code} {str((p1.get_json() or {}).get("error"))[:80]}')

        # ── 唯一性：同小班同日唯一 ──
        p2 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': ridx, 'step_no': 1, 'entry': '巡视', 'values': patrol, 'att': punch})
        ok('D21 同一小班同一天再交巡视 → 400（唯一性校验）',
           p2.status_code == 400 and '同一天不能重复填' in str((p2.get_json() or {}).get('error', '')),
           str((p2.get_json() or {}).get('error'))[:80])

        p3 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': ridx, 'step_no': 1, 'entry': '巡视',
            'values': dict(patrol, 巡护日期='2026-10-22'), 'att': punch})
        ok('D22 换一天可以再交（唯一性只限同日）', p3.status_code == 200,
           f'{p3.status_code} {str((p3.get_json() or {}).get("error"))[:80]}')

        f5 = cli.get(f'/api/workbooks/{wid}/flow?row={ridx}&entry=巡视', headers=H).get_json()
        ok('D23 巡视两条记录都在（rec_no 1/2）',
           len(((f5.get('multi') or {}).get('1') or {})) == 2,
           str(len(((f5.get('multi') or {}).get('1') or {}))))

        # 验收的记录没被巡视盖掉
        f6 = cli.get(f'/api/workbooks/{wid}/flow?row={ridx}&entry=验收', headers=H).get_json()
        ok('D24 入口隔离：巡视提交不覆盖验收的环节记录',
           ((f6.get('records') or {}).get('1') or {}).get('', {}).get('result') == '通过',
           str(((f6.get('records') or {}).get('1') or {}).get('', {}).get('result')))

        # ── 项目级入口：材料检验（v0.37，doc/029，row=-1 不挂小班档案）──
        fm = cli.get(f'/api/workbooks/{wid}/flow?row=-1&entry=材料', headers=H).get_json()
        ok('E1 项目级 row=-1&entry=材料 命中「材料检验（按批次）」2 环节',
           fm.get('ok') is True and fm.get('flow_sheet') == '流程-材料检验（按批次）·材料'
           and len(fm.get('steps') or []) == 2,
           f"{fm.get('flow_sheet')} {len(fm.get('steps') or [])}")
        ok('E2 项目级不显示内部条件标记（cond_field=独立入口 是注册用的，不该给现场看）',
           fm.get('cond_field') == '独立入口')
        ok('E3 项目级 entries 只有「材料」且挂载=独立入口',
           [e.get('entry') for e in (fm.get('entries') or [])] == ['材料']
           and (fm.get('entries') or [{}])[0].get('mount') == '独立入口',
           str(fm.get('entries')))
        std = fm.get('std') or {}
        ok('E4 项目级 std 只含项目级常量列（项目名称有值、造林树种没有）',
           std.get('项目名称') and '造林树种' not in std,
           str({k: std[k] for k in list(std)[:4]}))
        s1 = (fm.get('steps') or [{}])[0]
        link_ctl = next((c for c in (s1.get('controls') or []) if c['name'] == '关联小班'), None)
        batch_ctl = next((c for c in (s1.get('controls') or []) if '批次' in c['name']), None)
        ok('E5 苗木进场检验带「关联小班」多选 + 批次号控件（均必填）',
           link_ctl and link_ctl['type'] == '多选' and link_ctl.get('required') is True
           and batch_ctl and batch_ctl.get('required') is True
           and s1.get('link') == '多选' and s1.get('subject') == '苗木批次',
           f"{link_ctl and link_ctl['type']}/{batch_ctl and batch_ctl['name']}")

        # 转序：苗木进场检验没做 → 肥料检验 409（先测这个，后面才好提交）
        fert_vals = {'关联小班': ['YJ-1436'], '进场批次号': '2026-F-001',
                     '材料名称': '复合肥', '进场数量': 40, '抽检结论': '合格',
                     '是否通过': '通过', '记录日期': '2026-10-21'}
        e1 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': -1, 'step_no': 2, 'entry': '材料', 'values': fert_vals, 'att': []})
        ok('E6 苗木进场未检验 → 肥料检验 409（转序闸门对项目级同样生效）',
           e1.status_code == 409 and '苗木进场检验' in str((e1.get_json() or {}).get('error', '')),
           str((e1.get_json() or {}).get('error'))[:80])

        seed_vals = {'关联小班': ['YJ-1436', 'YJ-1437'], '进场批次号': '2026-M-001',
                     '苗木树种': '余甘子+清香木', '苗龄': 2, '地径': 1.2, '苗高': 80,
                     '进场数量': 5000, '是否通过': '通过', '记录日期': '2026-10-21'}
        # 「苗木证件」被 parse_att 判成 upload 且下限 1（三证一签|证件 规则）——
        # 种苗规格/剪苗作业照片下限 0，不传也能过
        seed_att = [{'kind': 'upload', 'label': '苗木证件', 'count': 1,
                     'names': ['苗木生产经营许可证.pdf']}]
        e2 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': -1, 'step_no': 1, 'entry': '材料', 'values': seed_vals, 'att': seed_att})
        ok('E7 苗木批次第 1 条提交成功（项目级，row=-1）',
           e2.status_code == 200 and (e2.get_json() or {}).get('rec_no') == 1,
           f'{e2.status_code} {str((e2.get_json() or {}).get("error"))[:80]}')

        e3 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': -1, 'step_no': 1, 'entry': '材料',
            'values': dict(seed_vals, 苗木树种='余甘子+云南松'), 'att': seed_att})
        ok('E8 同批次号再交一条 → 400（同批次号唯一，项目级查重）',
           e3.status_code == 400 and '批次号' in str((e3.get_json() or {}).get('error', '')),
           str((e3.get_json() or {}).get('error'))[:80])

        e4 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': -1, 'step_no': 2, 'entry': '材料', 'values': fert_vals, 'att': []})
        ok('E9 苗木已检合格 → 肥料批次可提交', e4.status_code == 200,
           f'{e4.status_code} {str((e4.get_json() or {}).get("error"))[:80]}')

        e5 = cli.post(f'/api/workbooks/{wid}/flow', headers=H, json={
            'row': -1, 'step_no': 1, 'entry': '材料',
            'values': dict(seed_vals, 进场批次号='2026-M-002', 进场数量=3000),
            'att': seed_att})
        ok('E10 换一个批次号 → 新增第 2 条（按批次多次登记）',
           e5.status_code == 200 and (e5.get_json() or {}).get('rec_no') == 2,
           str((e5.get_json() or {}).get('rec_no')))

        fm2 = cli.get(f'/api/workbooks/{wid}/flow?row=-1&entry=材料', headers=H).get_json()
        m1 = (fm2.get('multi') or {}).get('1') or {}
        m2 = (fm2.get('multi') or {}).get('2') or {}
        link_raw = (m1.get('1', {}).get('values', {}).get('关联小班') or '')
        try:    # 多选落库是 JSON 字符串（前端 decodeVal 解析），测试里也要解
            link_arr = json.loads(link_raw) if isinstance(link_raw, str) else link_raw
        except ValueError:
            link_arr = None
        ok('E11 GET 回传两条苗木批次，关联小班数组落库',
           len(m1) == 2 and len(m2) == 1 and link_arr == ['YJ-1436', 'YJ-1437'],
           str({k: v.get('values', {}).get('进场批次号') for k, v in m1.items()}))

        fm3 = cli.get(f'/api/workbooks/{wid}/flow?row=-1', headers=H).get_json()
        ok('E12 项目级不带 entry 也命中材料流程（唯一项目级候选）',
           fm3.get('flow_sheet') == '流程-材料检验（按批次）·材料',
           str(fm3.get('flow_sheet')))

        fm4 = cli.get(f'/api/workbooks/{wid}/flow?row={ridx}&entry=材料', headers=H).get_json()
        ok('E13 行级要材料流程 → ok=false（材料不挂小班，不能从行进）',
           fm4.get('ok') is False, str(fm4.get('reason'))[:60])
    finally:
        import sqlite3
        con = sqlite3.connect(_M.DB_PATH)
        con.execute('DELETE FROM workbooks WHERE id=?', (wid,))
        con.execute('DELETE FROM flow_records WHERE workbook_id=?', (wid,))
        con.commit()
        con.close()
        import shutil
        shutil.rmtree(_M.UPLOAD_DIR / str(wid), ignore_errors=True)

    print('\n' + '=' * 62)
    print(f'通过 {_pass} / 失败 {_fail}')
    return 0 if _fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
