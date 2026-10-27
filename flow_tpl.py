# -*- coding: utf-8 -*-
"""流程模板 v2 解析（tpl-人工造林-验收流程模板.xlsx，2026-10-27）

模板结构（与 v0.31/v0.35 两套旧格式并存，互不影响）：

| sheet | 作用 |
|---|---|
| 设计值 | 第 1 个 sheet：一行 = 一个小班，列 = 设计值 |
| 流程路由 | 一行 = 一条流程；序号 = 界面顺序，前置流程 = 全部完成后才可发起 |
| 流程-* | 一行 = 一个填报项（环节）；列 = 序号/环节/设计值表头/控件/附件/备注 |
| 说明 | 人读说明，不参与解析 |

控件 DSL（取自模板「说明」页，本模块只解释不发明）：

    选项(正确;不正确→备注≥2)   单选，第一项是正向词；→ 后可带备注下限
    按钮(通过;不通过→备注≥2)   结论按钮
    多选(甲;乙;丙)             多选
    数值(株) / 百分数(0-100)   数字；数值非负，百分数 0-100
    文本 / 长文本              单行 / 多行
    长文本(隐藏;不通过必填≥2汉字)  默认隐藏，结论=不通过时展开
    自动(公式)                 自动计算，不手填

设计值表头：命中「设计值」列 → 按当前小班取值；「常量：xxx」→ 固定提示；留空 = 无。
"""

import re

from flow_parser import parse_att, split_top

DESIGN_SHEET = '设计值'
ROUTE_SHEET = '流程路由'
DOC_SHEET = '说明'

ROUTE_COLS = ['序号', '项目类型', '流程', '前置流程', '可重复', '触发方式', '说明']
STEP_COLS = ['序号', '环节', '设计值表头', '控件', '附件', '备注']

REPEAT_VALUES = ('是', '否')


class TplError(Exception):
    """模板 v2 违规，message 已带 sheet + 行号。"""


def _s(v):
    return '' if v is None else str(v).strip()


def _to_int(v, default=0):
    try:
        return int(float(_s(v)))
    except (TypeError, ValueError):
        return default


def is_tpl_doc(wb):
    """是不是 v2 模板：同时有「设计值」和「流程路由」两张表。"""
    names = wb.sheetnames
    return DESIGN_SHEET in names and ROUTE_SHEET in names


# ── 控件 DSL ────────────────────────────────────────────────────────

def parse_dsl(text):
    """`选项(正确;不正确→备注≥2)` → {type,arg,options,rules,hidden}

    options 只对 选项/多选/按钮 有意义；rules 是 `→` 后的附加规则（如 `备注≥2`）。
    """
    s = _s(text)
    opens = [i for i in (s.find('('), s.find('（')) if i >= 0]
    if not opens:
        return {'type': s, 'arg': '', 'options': [], 'rules': {}, 'hidden': False}
    i = min(opens)
    j = max(s.rfind(')'), s.rfind('）'))
    arg = s[i + 1:j].strip() if j > i else s[i + 1:].strip()
    ctype = s[:i].strip()
    options, rules = [], {}
    if ctype in ('选项', '多选', '按钮'):
        for item in split_top(arg, ';'):
            # 模板可能写中文分号（业务员在 Excel 里随手打），一并切
            for sub in item.split('；'):
                sub = sub.strip()
                if not sub:
                    continue
                name, _, rule = sub.partition('→')
                name, rule = name.strip(), rule.strip()
                if name and name not in options:
                    options.append(name)
                if rule:
                    rules[name] = rule
    return {'type': ctype, 'arg': arg, 'options': options,
            'rules': rules, 'hidden': '隐藏' in arg}


def _design_of(raw, headers):
    """设计值表头 → {design, std_col, const, const_value}。"""
    text = _s(raw)
    if not text:
        return {'design': '', 'std_col': '', 'const': False, 'const_value': ''}
    if text.startswith('常量：') or text.startswith('常量:'):
        value = re.split(r'[：:]', text, 1)[-1].strip()
        return {'design': text, 'std_col': '', 'const': True, 'const_value': value}
    col = text
    if headers and col not in headers:
        hit = next((h for h in headers if _s(h) == col), '')
        if hit:
            col = hit
    return {'design': text, 'std_col': col if (not headers or col in headers) else '',
            'const': False, 'const_value': ''}


def _control(step, ctl_text, design, sheet, no):
    d = parse_dsl(ctl_text)
    t = d['type']
    c = {
        'name': step, 'type': '单行文本', 'arg': '', 'sub': [],
        'std_col': design['std_col'], 'hint': '', 'unit': '',
        'required': True, 'verdict': False, 'drop': [], 'rule': '',
        'design': design['design'], 'design_const': design['const'],
        'design_value': design['const_value'],
        'options': d['options'], 'note_on': [], 'hidden': d['hidden'],
    }
    if t == '选项':
        opts = d['options']
        c.update(type='下拉', arg=';'.join(opts), default=opts[0] if opts else '')
        c['note_on'] = [o for o, r in d['rules'].items() if '备注' in r]
    elif t == '按钮':
        c.update(name='是否通过', type='下拉', arg='通过;不通过',
                 required=True, default='', verdict=True,
                 options=['通过', '不通过'], note_on=['不通过'])
    elif t == '多选':
        c.update(type='多选', arg=';'.join(d['options']))
    elif t == '数值':
        c.update(type='数值', arg=d['arg'], unit=d['arg'], rule='≥0')
    elif t == '百分数':
        c.update(type='数值', arg=d['arg'], unit='%', rule='0-100')
    elif t == '文本':
        c.update(type='单行文本', arg=d['arg'])
    elif t == '长文本':
        hidden = d['hidden']
        c.update(type='多行纯文本', arg=d['arg'], required=not hidden,
                 hidden=hidden,
                 rule='≥2汉字' if ('≥2' in d['arg'] or '不通过必填' in d['arg']) else '')
    elif t == '自动':
        c.update(type='自动', arg='系统自动算: ' + d['arg'], formula=d['arg'],
                 required=False)
    else:
        # 白名单之外的类型不猜：按单行文本落地，同时在 flow 警告里说清表 + 行号
        c['type'] = '单行文本'
        c['arg'] = d['arg']
    return c


# ── sheet 读取 ──────────────────────────────────────────────────────

def _read_sheet(ws, cols):
    """按固定列数读表 → (表头, [(Excel行号, {列: 值})])。

    **只用 iter_rows**：上传时工作簿是 read_only 打开的，`ws[1]` / `ws.cell()`
    会直接抛「Read-only worksheet does not support cell access」。
    """
    it = ws.iter_rows(max_col=len(cols), values_only=True)
    first = next(it, None) or ()
    headers = [_s(v) for v in (list(first) + [None] * len(cols))[:len(cols)]]
    rows = []
    for ln, vals in enumerate(it, 2):
        cells = [_s(v) for v in (list(vals) + [None] * len(cols))[:len(cols)]]
        if not any(cells):
            continue
        rows.append((ln, dict(zip(cols, cells))))
    return headers, rows


def parse_routes(wb):
    ws = wb[ROUTE_SHEET]
    headers, rows = _read_sheet(ws, ROUTE_COLS)
    if headers[:len(ROUTE_COLS)] != ROUTE_COLS:
        raise TplError('「流程路由」表头必须是：' + ' / '.join(ROUTE_COLS))
    out = []
    for ln, d in rows:
        flow = d['流程']
        if not flow:
            raise TplError(f'「流程路由」第 {ln} 行缺少「流程」')
        if d['可重复'] not in REPEAT_VALUES:
            raise TplError(f'「流程路由」第 {ln} 行「可重复」必须是 是 / 否（实际「{d["可重复"]}」）')
        prereq = [x.strip() for x in re.split(r'[;；]', d['前置流程']) if x.strip()]
        repeat = d['可重复'] == '是'
        out.append({
            'no': _to_int(d['序号'], len(out) + 1),
            'type': d['项目类型'],
            'flow': flow,
            'prereq': prereq,
            'repeat': repeat,
            # 无前置 + 可重复 = 独立流程（如日常巡护）：随时可发起、可多次，
            # 不参与主线默认选择，也不阻塞 / 解锁任何流程
            'independent': repeat and not prereq,
            'trigger': d['触发方式'],
            'note': d['说明'],
            'line': ln,
        })
    if not out:
        raise TplError('「流程路由」没有任何流程行')
    return out


def parse_flow_sheet(ws, data_headers):
    _hdr, rows = _read_sheet(ws, STEP_COLS)
    out = []
    for ln, d in rows:
        step = d['环节']
        if not step:
            continue
        design = _design_of(d['设计值表头'], data_headers)
        ctl = _control(step, d['控件'], design, ws.title, ln)
        out.append({
            'no': _to_int(d['序号'], len(out) + 1),
            'step': step,
            'type': '',
            'factors': '',
            'std_from': design['design'],
            'require': '',
            'check': '',
            'pass': '',
            'seq': '',
            'memo': d['备注'],
            'att_raw': d['附件'],
            'attachments': parse_att(d['附件']),
            'controls': [ctl],
            'controls_raw': d['控件'],
            'expert': False,
            'subject': '小班',
            'multi': False,
            'link': '不需要',
            'basic_info': [],
            'basic_hide': [],
            'verdict': '是否通过' if ctl.get('verdict') else '',
            'question': '是否通过',
            'note_required': False,
            'note_field': '',
            'need_result': False,
            'prev': [],
            'prev_result': {},
            'tpl': True,
            'line': ln,
        })
    if not out:
        raise TplError(f'「{ws.title}」没有任何环节行')
    return out


def _attach_note(flows):
    """把「隐藏备注」的行号与名称挂到本流程的结论行上。

    模板约定：每个流程固定有一行「备注」（长文本(隐藏;不通过必填≥2汉字)），
    结论「是否通过=不通过」时它必须展开并填 ≥2 个汉字。这里只建立引用，
    是否必填由前端/服务端在提交时按条件判定。
    """
    for steps in flows.values():
        note = next((s for s in steps
                     if s['step'] == '备注'
                     or (s['controls'] and s['controls'][0].get('hidden'))), None)
        if not note:
            continue
        for s in steps:
            if not s.get('verdict'):
                continue
            s['note_required'] = True
            s['note_field'] = note['step']
            s['note_no'] = note['no']


def parse(wb, data_headers=None):
    """整个 v2 模板 → {'schema':'flow_tpl/1','routes':[...],'flows':{...},'warn':[...]}"""
    routes = parse_routes(wb)
    flows = {}
    for name in wb.sheetnames:
        if name.startswith('流程-'):
            flows[name] = parse_flow_sheet(wb[name], data_headers or [])
    if not flows:
        raise TplError('工作簿里没有任何「流程-」定义表')
    _attach_note(flows)

    warn = []
    for r in routes:
        if r['flow'] not in flows:
            warn.append({'level': 'error',
                         'text': f'「流程路由」第 {r["line"]} 行引用了不存在的流程「{r["flow"]}」'})
        for pre in r['prereq']:
            if pre not in flows:
                warn.append({'level': 'error',
                             'text': f'「流程路由」第 {r["line"]} 行前置流程「{pre}」不存在'})
    for name in flows:
        if not any(r['flow'] == name for r in routes):
            warn.append({'level': 'info',
                         'text': f'流程「{name}」没有出现在「流程路由」中，界面上不会出现'})
    return {
        'schema': 'flow_tpl/1',
        'source': 'tpl_v2',
        'routes': routes,
        'flows': flows,
        'warn': warn,
    }


def routes_for(flows, project_type):
    """某项目类型的流程：主线按「序号」在前，独立流程（巡护）排最后。"""
    return sorted([r for r in (flows.get('routes') or [])
                   if _s(r.get('type')) == _s(project_type)],
                  key=lambda r: (1 if r.get('independent') else 0,
                                 r.get('no') or 0, r.get('line') or 0))


def steps_of(flows, sheet):
    return list((flows.get('flows') or {}).get(sheet) or [])
