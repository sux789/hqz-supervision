#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务专家流程配置的单元测试（v0.36，doc/028）

用法：python tests/test_flow_config.py     # 全绿退出码 0

覆盖三层：
  A. 解析：业务专家文档 → registry/flows
     （入口维度 / 两级显隐 / 选项剪枝 / 校验规则 / 带结论的前置 / 多次 / 一对多）
  B. 校验：写错的东西必须**带表名+行号**报出来，而不是静默失效
  C. 接线：同一份配置经 main.parse_workbook_storage 落库后，
     match_flow 能按项目类型命中、独立入口不被误命中、_flow_hit 带出新维度、
     服务端 check_rule / 唯一性 / 转序闸门的口径正确

不碰数据库与网络（parse_workbook_storage 只吃内存流）。
"""
import io
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import openpyxl  # noqa: E402

import flow_config as fc  # noqa: E402
import flow_parser as fp  # noqa: E402

DOC = BASE / 'data' / '验收流程设计' / '流程配置-业务专家填写版.xlsx'

_pass = _fail = 0


def ok(name, cond, detail=''):
    global _pass, _fail
    _pass, _fail = _pass + bool(cond), _fail + (not cond)
    print(('  ✅ ' if cond else '  ❌ ') + name + (f'  [{detail}]' if detail else ''))


def section(t):
    print(f'\n=== {t} ===')


# ── A. 解析 ────────────────────────────────────────────────────────
section('A. 业务专家文档解析')

res = fc.parse_path(DOC)
ok('A1 校验 0 错误', not res['errors'], f'{len(res["errors"])} 条')
ok('A2 9 条流程 / 25 个环节',
   res['summary']['flows'] == 9 and res['summary']['steps'] == 25, str(res['summary']))
ok('A3 三个入口都有内容（验收 4 / 巡视 4 / 材料 1）',
   res['summary']['entries'] == {'验收': 4, '巡视': 4, '材料': 1}, str(res['summary']['entries']))

reg = {r['sheet']: r for r in res['registry']}
ok('A4 流程唯一键带入口后缀（同名不同入口互不覆盖）',
   '流程-人工造林（2026版）·验收' in reg and '流程-人工造林（2026版）·巡视' in reg
   and fc.flow_key('人工造林（2026版）', '验收') == '流程-人工造林（2026版）·验收')
ok('A5 验收/巡视挂小班档案，按项目类型匹配',
   reg['流程-人工造林（2026版）·验收']['cond_field'] == '项目类型'
   and reg['流程-人工造林（2026版）·验收']['cond_val'] == '人工造乔木林'
   and reg['流程-人工造林（2026版）·验收']['mount'] == '小班档案'
   and reg['流程-人工造林（2026版）·巡视']['mount'] == '小班档案')
ok('A6 材料入口 → 条件字段为独立入口（永不自动命中）',
   reg['流程-材料检验（按批次）·材料']['cond_field'] == '独立入口'
   and reg['流程-材料检验（按批次）·材料']['mount'] == '独立入口')

FLOW = '流程-人工造林（2026版）·验收'
steps = {s['step']: s for s in res['flows'][FLOW]}
ok('A7 序号按 Excel 顺序编号 1..7',
   [s['no'] for s in res['flows'][FLOW]] == list(range(1, 8)))
ok('A8 每个环节都带 expert 标记（前端据此区分老 12 列格式）',
   all(s.get('expert') for s in res['flows'][FLOW]))
ok('A9 每个环节都带 entry（入口随环节走，前端可显示）',
   all(s.get('entry') == '验收' for s in res['flows'][FLOW]))

s1 = steps['小班边界确认']
ok('A10 提交方式=是否通过 → 生成「是否通过」控件',
   any(c['name'] == '是否通过' and c['type'] == '下拉' for c in s1['controls']))
ok('A11 结论问法带出来（现场显示业务问句）',
   s1['question'] == '是否在设计小班范围内', s1['question'])
ok('A12 不通过必填原因=True', s1['note_required'] is True)
ok('A13 显示的基本信息按；切成列名列表',
   s1['basic_info'][:3] == ['小班号', '小班面积', '项目类型'], str(s1['basic_info'][:3]))
ok('A14 仅记录/是否通过环节不再要求验收结果', s1['need_result'] is False)
ok('A15 三档结果环节要求验收结果', steps['成活率核验']['need_result'] is True)

# ── 两级显隐（v0.36 核心）──
sd = steps['整地']
ok('A16 白名单留空 = 显示 data 全部列（basic_info 为空）', sd['basic_info'] == [])
ok('A17 黑名单剪枝：整地剔掉采伐方式等 8 列 + 默认藏起 3 个填报结果列',
   '采伐方式' in sd['basic_hide'] and len(sd['basic_hide']) == 11,
   str(sd['basic_hide']))
ok('A17b 默认黑名单只对「白名单留空」的环节生效（边界确认有白名单 → 不追加）',
   s1['basic_hide'] == [], str(s1['basic_hide']))
ok('A18 白名单 + 黑名单可同时用（回塘：只显示 3 列，无黑名单）',
   steps['回塘及底肥']['basic_info'] == ['密度', '株行距', '整地方式']
   and steps['回塘及底肥']['basic_hide'] == [])

# ── 选项剪枝（下拉黑名单）──
sx = res['flows']['流程-人工造林（2026版）·巡视'][0]
sw = next(c for c in sx['controls'] if c['name'] == '巡护方式')
ok('A19 不显示选项：巡护方式有 3 个选项，剪掉「无人机」',
   sw['arg'] == '步行;车辆;无人机' and sw['drop'] == ['无人机'],
   f'{sw["arg"]} / drop={sw["drop"]}')

# ── 校验规则 ──
dc = next(c for c in sd['controls'] if c['name'] == '整地数量')
ok('A20 校验规则原文带到控件（整数；≥0）', dc['rule'] == '整数；≥0', dc['rule'])
ok('A21 校验规则能识别（rule_ok）',
   fc.rule_ok('整数；≥0') and fc.rule_ok('0-100') and fc.rule_ok('≤500字')
   and not fc.rule_ok('必须大于零'))
ok('A22 校验规则执行：≥0 拦负数 / 整数拦小数',
   fc.check_rule('≥0', '-1') != '' and fc.check_rule('整数', '1.5') != ''
   and fc.check_rule('整数；≥0', '20') == '')
ok('A23 校验规则：区间与字数上限',
   fc.check_rule('0-100', '120') != '' and fc.check_rule('≤5字', '一二三四五六七') != ''
   and fc.check_rule('0-100', '88') == '' and fc.check_rule('≤5字', '三字') == '')

# ── 多次 / 一对多 ──
ok('A24 多次环节 multi=True', sd['multi'] is True)
ok('A25 多次环节自动补「记录日期」（已有日期控件则不补）',
   any(c['name'] == '记录日期' and c['type'] == '日期' for c in sd['controls'])
   and not any(c['name'] == '记录日期' for c in sx['controls']))
spr = next(s for s in res['flows']['流程-材料检验（按批次）·材料'] if s['step'] == '苗木进场检验')
ok('A26 苗木检验=苗木批次 + 多次 + 关联小班多选',
   spr['subject'] == '苗木批次' and spr['multi'] and spr['link'] == '多选')
ok('A27 关联小班控件是多选且取 data 小班号',
   any(c['name'] == '关联小班' and c['type'] == '多选'
       and '小班号' in c['arg'] for c in spr['controls']))
ok('A28 单行文本可显式必填（进场批次号 required=True）',
   next(c for c in spr['controls'] if c['name'] == '进场批次号')['required'] is True)

# ── 前置条件带结论 ──
ok('A29 前置环节解析成列表（成活率等 3 个工序）',
   steps['成活率核验']['prev'] == ['整地', '回塘及底肥', '栽植'], str(steps['成活率核验']['prev']))
ok('A30 前置条件带期望结论（prev_result 映射）',
   steps['成活率核验']['prev_result'] == {'整地': '通过', '回塘及底肥': '通过', '栽植': '通过'},
   str(steps['成活率核验']['prev_result']))
ok('A31 材料检验只等边界确认，不等造林工序（纪要要求）',
   spr['prev'] == [], str(spr['prev']))

# ── 唯一性 ──
ok('A32 巡视声明同小班同日唯一', sx['unique'] == '同小班同日唯一', sx['unique'])
ok('A33 未声明的环节默认不校验', sd['unique'] == '不校验')

# ── 附件 / 标准值 ──
ok('A34 附件自然语言解析出打卡按钮', any(a['kind'] == 'punch' for a in s1['attachments']))
ok('A35 照片张数写进 min（整地 2 张）',
   next((a['min'] for a in sd['attachments'] if a['kind'] == 'photo'), 0) >= 2,
   str([(a['label'], a['min']) for a in sd['attachments']]))
ok('A36 整地数量 标准值来源=总株数（设计数量带出）',
   dc['std_col'] == '总株数')
ok('A37 整地规格=数值×3 且 sub 为长/宽/深',
   next(c for c in sd['controls'] if c['name'] == '整地规格')['sub']
   == ['整地规格长', '整地规格宽', '整地规格深'])
ok('A38 整地方式选项取 data 枚举',
   next(c for c in sd['controls'] if c['name'] == '整地方式')['arg'] == 'data「整地方式」枚举')
ok('A39 老格式 12 列仍可被 flow_parser 解析（回归锚点）', 'FLOW_PREFIX' in dir(fp))


# ── B. 校验（错误必须带表名+行号）────────────────────────────────────
section('B. 校验规则')


def _doc_with(mutate):
    """在真实文档上改一处，返回 (res)。mutate(wb) 直接改 sheet 单元格。"""
    wb = openpyxl.load_workbook(DOC)
    mutate(wb)
    return fc.parse(wb)


def _find_row(ws, want, start=2):
    """按 {列号: 值} 联合定位；want 形如 {1: '人工造林（2026版）', 4: '整地'}。"""
    for r in range(start, ws.max_row + 1):
        if all(str(ws.cell(row=r, column=c).value or '').strip() == v
               for c, v in want.items()):
            return r
    return 0


C_FLOW, C_ENTRY, C_NO, C_STEP = 1, 2, 3, 4
C_TYPE, C_BI, C_HIDE, C_VERDICT = 5, 9, 10, 11
C_PREV, C_UNI = 15, 16

F_FLOW, F_STEP, F_ITEM = 1, 3, 4
F_STD, F_OPT, F_DROP, F_RULE = 8, 10, 11, 12

AT = {C_FLOW: '人工造林（2026版）', C_ENTRY: '验收'}


def _step_row(wb, name, entry='验收'):
    return _find_row(wb[fc.SHEET_STEP],
                     {C_FLOW: AT[C_FLOW], C_ENTRY: entry, C_STEP: name})


# B1 序号跳号
def m1(wb):
    wb[fc.SHEET_STEP].cell(row=_step_row(wb, '整地'), column=C_NO, value=7)


r1 = _doc_with(m1)
ok('B1 序号跳号 → 报错且带表名+行号',
   any(e['sheet'] == fc.SHEET_STEP and '序号不连续' in e['msg'] for e in r1['errors']),
   r1['errors'][0]['msg'] if r1['errors'] else '无错误')


# B2 前置环节指向自己
def m2(wb):
    wb[fc.SHEET_STEP].cell(row=_step_row(wb, '整地'), column=C_PREV, value='整地')


r2 = _doc_with(m2)
ok('B2 前置环节指向自己 → 报错（转序会死锁）',
   any('排在本环节之后' in e['msg'] for e in r2['errors']))


# B3 显示的基本信息写了不存在的列
def m3(wb):
    wb[fc.SHEET_STEP].cell(row=_step_row(wb, '整地'), column=C_BI, value='不存在的列名')


r3 = _doc_with(m3)
ok('B3 基本信息列名在 data 里不存在 → 报错',
   any('没有这一列' in e['msg'] for e in r3['errors']))


# B4 环节类型写错
def m4(wb):
    wb[fc.SHEET_STEP].cell(row=_step_row(wb, '整地'), column=C_TYPE, value='随便写')


r4 = _doc_with(m4)
ok('B4 环节类型不在白名单 → 报错并列出可选值',
   any('环节类型' in e['msg'] and '只认' in e['msg'] for e in r4['errors']))


# B5 标准值来源写错 → 只是提示，不阻断（data 可能后续补列）
def m5(wb):
    ws = wb[fc.SHEET_FIELD]
    r = _find_row(ws, {F_FLOW: '人工造林（2026版）', F_STEP: '栽植', F_ITEM: '混交比例实核'})
    ws.cell(row=r, column=F_STD, value='不存在的设计列')


r5 = _doc_with(m5)
ok('B5 标准值来源列不存在 → 提示而非报错（不阻断生成）',
   not r5['errors'] and any('只能人工判断' in w['msg'] for w in r5['warnings']),
   f'errors={len(r5["errors"])} warn={len(r5["warnings"])}')


# B6 选项组引用
def m6(wb):
    ws = wb[fc.SHEET_FIELD]
    r = _find_row(ws, {F_FLOW: '人工造林（2026版）', F_STEP: '回塘及底肥', F_ITEM: '回塘质量'})
    ws.cell(row=r, column=F_OPT, value='@符合类')


r6 = _doc_with(m6)
ctl = next(c for s in r6['flows'][FLOW] for c in s['controls'] if c['name'] == '回塘质量')
ok('B6 @选项组 引用解析成字面值', ctl['arg'] == '符合;不符合', ctl['arg'])


# B7 环节没有核验项且提交方式=仅记录 → 报错
def m7(wb):
    ws = wb[fc.SHEET_STEP]
    ws.cell(row=_step_row(wb, '日常巡护', '巡视'), column=C_VERDICT, value='仅记录')
    ws2 = wb[fc.SHEET_FIELD]
    for rr in range(ws2.max_row, 1, -1):
        if (str(ws2.cell(row=rr, column=F_FLOW).value or '').strip() == '人工造林（2026版）'
                and str(ws2.cell(row=rr, column=F_STEP).value or '').strip() == '日常巡护'):
            ws2.delete_rows(rr)


r7 = _doc_with(m7)
ok('B7 空环节（无核验项 + 仅记录）→ 报错',
   any('现场打开是空的' in e['msg'] for e in r7['errors']))


# B8 ②环节的入口与①流程对不上
def m8(wb):
    wb[fc.SHEET_STEP].cell(row=_step_row(wb, '整地'), column=C_ENTRY, value='督查')


r8 = _doc_with(m8)
ok('B8 环节的入口在①流程里没定义 → 报错（不会静默挂到别的流程）',
   any('在「①流程」里没有定义' in e['msg'] for e in r8['errors']),
   str(r8['errors'][:1]))


# B8b ①流程的入口写成不认识的值
def m8b(wb):
    ws = wb[fc.SHEET_FLOW]
    r = _find_row(ws, {C_FLOW: '人工造林（2026版）', C_ENTRY: '验收'})
    ws.cell(row=r, column=C_ENTRY, value='督查')


r8b = _doc_with(m8b)
ok('B8b ①流程 入口不在白名单 → 报错并列出可选值',
   any('入口「督查」不认识' in e['msg'] and '只认' in e['msg'] for e in r8b['errors']),
   str(r8b['errors'][:1]))


# B9 校验规则写法不可识别 → 报错（否则静默不校验，最危险）
def m9(wb):
    ws = wb[fc.SHEET_FIELD]
    r = _find_row(ws, {F_FLOW: '人工造林（2026版）', F_STEP: '整地', F_ITEM: '整地数量'})
    ws.cell(row=r, column=F_RULE, value='必须大于零')


r9 = _doc_with(m9)
ok('B9 校验规则写错 → 报错（不静默失效）',
   any('校验规则' in e['msg'] for e in r9['errors']), str(r9['errors'][:1]))


# B10 不显示选项里有不存在的值 → 只提示（不阻断，选项可能来自 data 动态枚举）
def m10(wb):
    ws = wb[fc.SHEET_FIELD]
    r = _find_row(ws, {F_FLOW: '人工造林（2026版）', F_STEP: '栽植', F_ITEM: '栽植质量'})
    ws.cell(row=r, column=F_DROP, value='不存在的方式')


r10 = _doc_with(m10)
ok('B10 不显示选项里有不存在的值 → 提示而非报错',
   not r10['errors'] and any('不显示选项' in w['msg'] for w in r10['warnings']),
   f'errors={len(r10["errors"])} warn={len(r10["warnings"])}')


# B11 黑名单列名在 data 里不存在 → 只提示（不阻断，data 可能脏）
def m11(wb):
    wb[fc.SHEET_STEP].cell(row=_step_row(wb, '整地'), column=C_HIDE, value='不存在的列')


r11 = _doc_with(m11)
ok('B11 黑名单列名不存在 → 提示而非报错',
   not r11['errors'] and any('不显示的基本信息' in w['msg'] for w in r11['warnings']))


# ── C. 接线（落库结构 → 匹配 → 带出）────────────────────────────────
section('C. 与主程序的接线')

import main  # noqa: E402

# 造一份能上传的工作簿：data（前 90 行）+ 参数 + 业务专家填写版 5 张表
wb = openpyxl.load_workbook(DOC)
src = openpyxl.load_workbook(BASE / 'data' / '验收流程设计' / '造林项目-验收模板-含验收流程.xlsx')
for name in ['data', '参数']:
    ws = src[name]
    tgt = wb.create_sheet(name)
    for row in ws.iter_rows(max_row=90 if name == 'data' else ws.max_row, values_only=True):
        tgt.append(list(row))


def _save(w):
    b = io.BytesIO()
    w.save(b)
    b.seek(0)
    return b


_sn, _h, _rows, _cfg, _pr, _kc, _flows = main.parse_workbook_storage(_save(wb))
ok('C1 含业务专家填写版的工作簿能正常解析（不被多 sheet 拦住）', bool(_sn), _sn)

cj = json.loads(_flows)
ok('C2 落库结构是 registry + flows 9 条（前端无感）',
   len(cj.get('registry') or []) == 9 and len(cj.get('flows') or {}) == 9,
   f'{len(cj.get("registry") or [])}/{len(cj.get("flows") or {})}')
ok('C3 source 标记为 user_doc', cj.get('source') == 'user_doc')

row_at = next(dict(zip(_h, r)) for r in _rows
              if str(dict(zip(_h, r)).get('项目类型', '')).strip() == '人工造乔木林')
hit = fp.match_flow(cj['registry'], cj['flows'], row_at)
ok('C4 人工造乔木林小班命中（验收优先于巡视，同优先级取配置在前）',
   hit and hit['sheet'].endswith('·验收'), hit and hit['sheet'])

row_any = dict(zip(_h, _rows[0]))
MAT = '流程-材料检验（按批次）·材料'
h2 = fp.match_flow([r for r in cj['registry'] if r['sheet'] == MAT],
                   {MAT: cj['flows'][MAT]}, row_any)
ok('C5 独立入口不会被小班行自动命中', h2 is None, str(h2 and h2['sheet']))

_ridx = next(i for i, r in enumerate(_rows)
             if str(dict(zip(_h, r)).get('项目类型', '')).strip() == '人工造乔木林')
fh = main._flow_hit(cj, _h, _rows, _ridx, '造林项目-验收模板.xlsx')
ok('C6 _flow_hit 带出 basic_info', bool(fh['steps'][0]['basic_info']),
   str(fh['steps'][0]['basic_info'][:3]))
ok('C7 _flow_hit 带出 verdict / note_required',
   fh['steps'][0]['verdict'] == '是否通过' and fh['steps'][0]['note_required'] is True)
ok('C8 _flow_hit 透传 expert / basic_hide / unique / prev_result 四个新字段',
   fh['steps'][0]['expert'] is True
   and isinstance(fh['steps'][0]['basic_hide'], list)
   and fh['steps'][0]['unique'] == '不校验'
   and isinstance(fh['steps'][0]['prev_result'], dict))
ok('C9 整地环节的黑名单经 _flow_hit 原样带到前端',
   '采伐方式' in next(s for s in fh['steps'] if s['step'] == '整地')['basic_hide'])
ok('C10 _flow_hit 的 std 把「整地数量」映射到 data「总株数」',
   fh['std'].get('整地数量') is not None, str(fh['std'].get('整地数量')))
ok('C11 仅记录环节保持 need_result=false（不硬塞验收结果）',
   next(s for s in fh['steps'] if s['step'] == '小班边界确认')['need_result'] is False)

# 同一份流可重复解析（read_only 工作簿只能顺序读一次，二次读会拿到空表头 —— v0.31 踩过）
_sn2, _h2, _rows2, _c2, _p2, _k2, _f2 = main.parse_workbook_storage(_save(wb))
ok('C12 C1 复核（同一份流可重复解析）', bool(_sn2) and bool(json.loads(_f2)['flows']))


# ── D. 服务端校验函数的语义（不连库，纯函数部分）──────────────────
section('D. 服务端校验语义')

ok('D1 check_rule 空值不拦（空值归必填管）', fc.check_rule('整数；≥0', '') == '')
ok('D2 check_rule 多规则用；分隔且全部满足才过',
   fc.check_rule('整数；>0', '5') == '' and fc.check_rule('整数；>0', '0') != '')
ok('D3 check_rule 日期≥今天 能拦过去日期',
   fc.check_rule('日期≥今天', '2000-01-01') != '')
ok('D4 check_rule 正则可拦手机号格式',
   fc.check_rule(r'正则:^\d{11}$', '138001380001') != ''
   and fc.check_rule(r'正则:^\d{11}$', '13800138000') == '')
ok('D5 _rule_errors 遍历控件并跳过没写规则的',
   main._rule_errors({'controls': [
       {'name': '甲', 'type': '数值', 'rule': '整数'},
       {'name': '乙', 'type': '数值', 'rule': ''}]}, {'甲': '1.5', '乙': 'x'})
   == ['甲必须是整数'])
ok('D6 _rule_errors 数值×3 拆到三个子字段分别校验',
   len(main._rule_errors({'controls': [
       {'name': '规格', 'type': '数值×3', 'rule': '≥0',
        'sub': ['规长', '规宽', '规深']}]},
       {'规长': '1', '规宽': '-2', '规深': '3'})) == 1)
# ── E. 老记录迁移（v0.35 无入口后缀 → v0.36 补后缀）──────────────
section('E. 老记录迁移（flow_build --apply 时自动做）')

import importlib.util as _ilu  # noqa: E402
import sqlite3 as _sq  # noqa: E402

_spec = _ilu.spec_from_file_location('flow_build', str(BASE / 'tools' / 'flow_build.py'))
_m = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_m)

_c = _sq.connect(':memory:')
_c.row_factory = _sq.Row
_c.execute('CREATE TABLE flow_records(workbook_id INT, flow_sheet TEXT)')
_c.executemany('INSERT INTO flow_records VALUES(?,?)',
               [(1, '流程-人工造林（2026版）')] * 3
               + [(1, '流程-材料检验（按批次）')]
               + [(2, '流程-人工造林（2026版）')])       # 另一个工作簿的记录不该动
_c.commit()

_old_reg = [{'sheet': '流程-人工造林（2026版）'}, {'sheet': '流程-材料检验（按批次）'}]
plan = _m._plan_migrate(_c, 1, _old_reg, res['registry'])
_kv = {o: (t, n) for o, t, n in plan}
ok('E1 老流程名 → 补「·验收」后缀（3 条记录）',
   _kv.get('流程-人工造林（2026版）') == ('流程-人工造林（2026版）·验收', 3), str(plan))
ok('E2 材料流程补「·材料」而不是「·验收」',
   _kv.get('流程-材料检验（按批次）', ('', 0))[0] == '流程-材料检验（按批次）·材料', str(plan))
ok('E3 只统计本工作簿的记录（别的 wid 不算进来）',
   _kv.get('流程-人工造林（2026版）', ('', 0))[1] == 3)
ok('E4 已经是新格式的 sheet 不重复迁移',
   _m._plan_migrate(_c, 1, [{'sheet': '流程-人工造林（2026版）·验收'}], res['registry']) == [])
ok('E5 新配置里已没有的老流程 → 不迁移（留着不丢数据）',
   _m._plan_migrate(_c, 1, [{'sheet': '流程-已删除的旧流程'}], res['registry']) == [])

# 真跑一次 UPDATE，确认迁移后按新名能查到
for o, t, _n in plan:
    _c.execute('UPDATE flow_records SET flow_sheet=? WHERE workbook_id=? AND flow_sheet=?',
               (t, 1, o))
_c.commit()
_left = _c.execute("SELECT COUNT(*) AS n FROM flow_records WHERE workbook_id=1"
                   " AND flow_sheet NOT LIKE '%·%'").fetchone()['n']
ok('E6 迁移后本工作簿不再有无后缀记录', _left == 0, str(_left))
_other = _c.execute("SELECT COUNT(*) AS n FROM flow_records WHERE workbook_id=2"
                    " AND flow_sheet='流程-人工造林（2026版）'").fetchone()['n']
ok('E7 别的 workid 的记录没被误改', _other == 1, str(_other))
_c.close()

print('\n' + '=' * 62)
print(f'通过 {_pass} / 失败 {_fail}')
sys.exit(0 if _fail == 0 else 1)
