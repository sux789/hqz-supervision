# -*- coding: utf-8 -*-
"""流程模板 v2 解析（tpl-人工造林-验收流程模板.xlsx，2026-10-27）

模板结构（与 v0.31/v0.35 两套旧格式并存，互不影响）：

| sheet | 作用 |
|---|---|
| 设计值 | 第 1 个 sheet：一行 = 一个小班，列 = 设计值 |
| 流程路由 | 一行 = 一条流程；序号 = 界面顺序，前置流程 = 全部完成后才可发起；项目类型留空 = 公用 |
| 流程-* | 一行 = 一个填报项（环节）；列 = 序号/环节/设计值表头/控件/附件/备注 |
| 说明 | 人读说明，不参与解析 |

控件 DSL（取自模板「说明」页，本模块只解释不发明）：

    选项(正确;不正确→备注≥2)   单选，第一项是正向词；→ 后标了备注的选项，
                               选中时在同一环节就地展开「备注」文本框（≥2 汉字）
    选项(...)→首项通过          整条控件判定：首项=通过，其余=不通过；
                               参数「选项判定=首项通过」时默认生效
    选项(...)→不判定            退出默认判定，纯记录（如日常巡护）
    按钮(通过;不通过→备注≥2)   结论按钮
    多选(甲;乙;丙)             多选
    数值(株) / 百分数(0-100)   数字；数值非负，百分数 0-100
    数值(株→≥设计值)           通过判断：实测 ≥ 当前小班设计值 → 正确；
    百分数(0-100→≥85)          `→` 后可写 ≥/</>≤/</=/设计值/数字/×系数/±偏移
    文本 / 长文本              单行 / 多行
    长文本(隐藏;不通过必填≥2汉字)  默认隐藏，结论=不通过时展开
    自动(公式)                 自动计算，不手填；也可接 `→规则`（如 →≥85）

设计值表头：命中「设计值」列 → 按当前小班取值；「常量：xxx」→ 固定提示；留空 = 无。
"""

import re

import flow_judge
from flow_parser import parse_att, split_top

DESIGN_SHEET = '设计值'
ROUTE_SHEET = '流程路由'
DOC_SHEET = '说明'

ROUTE_COLS = ['序号', '项目类型', '流程', '前置流程', '可重复', '触发方式', '说明']
# v0.53：环节表末尾两列——「关联小班」（不需要/单选/多选）与「作用对象」（小班/项目）。
# **作用对象=项目** 的环节 = 按批次登记、一条记录关联多个小班（如苗木核验/肥料核验）：
# 这类流程不挂小班档案（row_idx=-1）、可多次、不进小班流程下拉。留空 = 小班。
# 两列都放末尾：Excel 里加列不用挪已有内容，业务专家也只需在最右两列上填写。
STEP_COLS = ['序号', '环节', '设计值表头', '控件', '附件', '备注', '关联小班', '作用对象']
LINKS = ['不需要', '单选', '多选']
SUBJECTS = ['小班', '项目']

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


def _note_min(rule, default=2):
    """`备注≥2` / `不通过必填≥3汉字` → 3；没有数字时取 default。"""
    m = re.search(r'[≥>=]+\s*(\d+)', rule or '')
    return int(m.group(1)) if m else default


_EMPTY_JUDGE = {'judge': '', 'judge_op': '', 'judge_ref': '',
                'judge_value': None, 'judge_factor': None, 'judge_delta': None}


def _split_judge(arg):
    """`株→≥设计值` → (`株`, `≥设计值`)；没有 `→` 时规则为空（不自动判断）。"""
    unit, sep, rule = str(arg or '').partition('→')
    return unit.strip(), (rule.strip() if sep else '')


def _judge_fields(rule_text, sheet, no, step):
    """数值类控件的 `→规则` → 控件字段；规则看不懂直接报错（配置期暴露）。"""
    if not rule_text:
        return dict(_EMPTY_JUDGE)
    rule = flow_judge.parse(rule_text)
    if not rule:
        raise TplError(
            f'「{sheet}」第 {no} 行「{step}」通过判断规则「{rule_text}」看不懂；'
            f'写法示例：≥设计值 / ≥设计值×0.95 / ≥85')
    return flow_judge.fields(rule)


def is_tpl_doc(wb):
    """是不是 v2 模板：同时有「设计值」和「流程路由」两张表。"""
    names = wb.sheetnames
    return DESIGN_SHEET in names and ROUTE_SHEET in names


def option_default_of(config):
    """参数「选项判定」→ 是否默认「首项通过」；空 / 不判定 = 关。"""
    v = _s((config or {}).get('选项判定'))
    if v in ('', '不判定'):
        return False
    if v == '首项通过':
        return True
    raise TplError(f'参数「选项判定」只能是「首项通过」或留空（实际「{v}」）')


# ── 控件 DSL ────────────────────────────────────────────────────────

def parse_dsl(text):
    """`选项(正确;不正确→备注≥2)→不判定` → {type,arg,options,rules,hidden,tail}

    options 只对 选项/多选/按钮 有意义；rules 是 `→` 后的附加规则（如 `备注≥2`）。
    tail 是右括号之后的控件级规则（如 `→首项通过` / `→不判定`）。
    """
    s = _s(text)
    opens = [i for i in (s.find('('), s.find('（')) if i >= 0]
    if not opens:
        return {'type': s, 'arg': '', 'options': [], 'rules': {},
                'hidden': False, 'tail': ''}
    i = min(opens)
    j = max(s.rfind(')'), s.rfind('）'))
    arg = s[i + 1:j].strip() if j > i else s[i + 1:].strip()
    tail = s[j + 1:].strip() if j > i else ''
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
            'rules': rules, 'hidden': '隐藏' in arg, 'tail': tail}


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


def _tail_rules(tail):
    """右括号后的控件级规则：`→首项通过` / `→不判定;→…` → ['首项通过', ...]。"""
    out = []
    for part in re.split(r'[;；]', tail or ''):
        p = part.strip()
        if not p:
            continue
        if p.startswith('→'):
            p = p[1:].strip()
        if p:
            out.append(p)
    return out


def _control(step, ctl_text, design, sheet, no, option_default=False):
    d = parse_dsl(ctl_text)
    t = d['type']
    tails = _tail_rules(d.get('tail'))
    if tails and t != '选项':
        raise TplError(
            f'「{sheet}」第 {no} 行「{step}」控件「{ctl_text}」类型「{t or "（空）"}」'
            f'不支持 →{tails[0]}；只有「选项」可用 →首项通过 / →不判定')
    c = {
        'name': step, 'label': '', 'type': '单行文本', 'arg': '', 'sub': [],
        'std_col': design['std_col'], 'hint': '', 'unit': '',
        'required': True, 'verdict': False, 'drop': [], 'rule': '',
        'design': design['design'], 'design_const': design['const'],
        'design_value': design['const_value'],
        'options': [], 'note_on': [], 'note_rules': {}, 'note_name': '',
        'hidden': d['hidden'], 'show_on': {}, 'note_for': '', 'min_chars': 0,
        'judge': '', 'judge_op': '', 'judge_ref': '',
        'judge_value': None, 'judge_factor': None, 'judge_delta': None,
        'first_pass': False, 'fail_on': [], 'option_rule': '',
    }
    if t == '选项':
        opts = d['options']
        unknown = [x for x in tails if x not in ('首项通过', '不判定')]
        if unknown:
            raise TplError(
                f'「{sheet}」第 {no} 行「{step}」控件「{ctl_text}」的 →{unknown[0]} 不认识；'
                f'选项可用：→首项通过 / →不判定')
        if '首项通过' in tails and '不判定' in tails:
            raise TplError(
                f'「{sheet}」第 {no} 行「{step}」不能同时写 →首项通过 和 →不判定')
        first_pass = ('首项通过' in tails) or (option_default and '不判定' not in tails)
        if first_pass and len(opts) < 2:
            raise TplError(
                f'「{sheet}」第 {no} 行「{step}」按首项通过判定，但选项少于 2 个')
        notes = {o: _note_min(r) for o, r in d['rules'].items() if '备注' in r}
        c.update(type='下拉', arg=';'.join(opts), options=opts,
                 default=opts[0] if opts else '',
                 first_pass=first_pass,
                 fail_on=opts[1:] if first_pass else [],
                 option_rule=('首项通过' if first_pass
                              else ('不判定' if '不判定' in tails else '')))
        c['note_on'] = list(notes)
        c['note_rules'] = notes
        c['note_name'] = f'{step}说明' if notes else ''
    elif t == '按钮':
        c.update(name='是否通过', type='下拉', arg='通过;不通过',
                 required=True, default='', verdict=True,
                 options=['通过', '不通过'], note_on=['不通过'])
    elif t == '多选':
        c.update(type='多选', arg=';'.join(d['options']), options=d['options'])
    elif t == '数值':
        unit, rule = _split_judge(d['arg'])
        c.update(type='数值', arg=unit, unit=unit, rule='≥0')
        c.update(_judge_fields(rule, sheet, no, step))
    elif t == '百分数':
        unit, rule = _split_judge(d['arg'])
        c.update(type='数值', arg=unit, unit='%', rule='0-100')
        c.update(_judge_fields(rule, sheet, no, step))
    elif t == '文本':
        c.update(type='单行文本', arg=d['arg'])
    elif t == '长文本':
        hidden = d['hidden']
        # 隐藏备注只在条件满足时展开；展开后就是必填，汉字下限来自模板（≥N）
        min_chars = _note_min(d['arg'], 2) if hidden else 0
        c.update(type='多行纯文本', arg=d['arg'], required=True,
                 hidden=hidden, min_chars=min_chars,
                 hint=(f'结论=不通过时必填，≥{min_chars} 个汉字'
                       if hidden and '不通过' in d['arg'] else ''),
                 show_on={'是否通过': ['不通过']}
                 if hidden and '不通过' in d['arg'] else {})
    elif t == '自动':
        formula, rule = _split_judge(d['arg'])
        c.update(type='自动', arg='系统自动算: ' + formula, formula=formula,
                 required=False)
        c.update(_judge_fields(rule, sheet, no, step))
    elif t:
        # 白名单之外的类型不猜：模板写错就在上传期炸掉（C 口径：坏数据入口报错带行号）
        raise TplError(
            f'「{sheet}」第 {no} 行「{step}」控件「{ctl_text}」类型「{t}」不认识；'
            f'可用：选项 / 按钮 / 多选 / 数值 / 百分数 / 文本 / 长文本 / 自动')
    else:
        # 空控件保留旧行为：按单行文本落地（纯附件行不写控件时仍可上传）
        c['type'] = '单行文本'
        c['arg'] = d['arg']
    return c


def _origin_hint(memo):
    """备注里的「原表头：…（1.已实施完成2.未实施完成）」→ 界面提示「原表口径：…」。

    选项第一项统一成「正确」后，现场不知道正确指什么；把原表说法带出来（只显示不改值）。
    拆分行（备注含「这里拆成」）的括注混着两类选项，不生成提示，免得指错。
    """
    text = _s(memo)
    if '这里拆成' in text:
        return ''
    m = re.search(r'原表头：([^；;]*)', text)
    if not m:
        return ''
    raw = m.group(1)
    opens = [i for i in (raw.find('('), raw.find('（')) if i >= 0]
    if not opens:
        return ''
    i = min(opens)
    j = max(raw.rfind(')'), raw.rfind('）'))
    if j <= i:
        return ''
    arg = raw[i + 1:j].strip()
    if not arg:
        return ''
    arg = re.sub(r'(?<!\d)[1-9]\s*[.．、，,]\s*(?!\d)', ' / ', arg)
    # 「1一致2.不一致」这种第一项没写分隔符的写法
    arg = re.sub(r'(?<![\d.])[1-9](?=[^\d.．、，,\s])', ' / ', arg)
    arg = re.sub(r'\s{2,}', ' ', arg).strip(' /')
    return f'原表口径：{arg}' if arg else ''


def _note_control(step, notes):
    """选项带 `→备注≥N` 时，在同一环节挂一个隐藏多行文本，选中错误项才展开。

    字段名固定为「{环节}说明」：服务端按环节存值，导出/排查时能看出是哪一项的说明。
    `show_on` 引用同一环节的选项字段，前端只负责按条件显示，不再另写字样判断。
    """
    opts = list(notes)
    mins = sorted(set(notes.values()))
    min_c = mins[0]
    cond = (f'选「{opts[0]}」时' if len(opts) == 1
            else '选「' + '」「'.join(opts) + '」任一项时')
    return {
        'name': f'{step}说明', 'label': '备注', 'type': '多行纯文本', 'arg': '',
        'sub': [], 'std_col': '', 'hint': f'{cond}必填，≥{min_c} 个汉字', 'unit': '',
        'required': True, 'verdict': False, 'drop': [], 'rule': '',
        'design': '', 'design_const': False, 'design_value': '',
        'options': [], 'note_on': [], 'note_rules': {}, 'note_name': '',
        'hidden': True, 'show_on': {step: opts}, 'note_for': step,
        'min_chars': min_c,
        'judge': '', 'judge_op': '', 'judge_ref': '',
        'judge_value': None, 'judge_factor': None, 'judge_delta': None,
        'first_pass': False, 'fail_on': [], 'option_rule': '',
    }


def _link_control(step, link):
    """「关联小班=单选/多选」→ 一个自动控件（选项在命中时按数据表小班号列填）。

    线上场景：苗木一批到 3 车、覆盖 12 个小班 —— 一条记录挂多个小班，
    所以这条流程不挂小班档案（项目级，row_idx=-1），小班差异全靠这个控件。
    """
    return {
        'name': '关联小班', 'label': '关联小班',
        'type': '多选' if link == '多选' else '下拉',
        'arg': '', 'sub': [], 'std_col': '',
        'hint': ('一条记录可关联多个小班；勾了哪些小班，提交后这些小班就不再提示'
                 if link == '多选' else '这一条记录属于哪个小班'),
        'unit': '', 'required': True, 'verdict': False, 'drop': [], 'rule': '',
        'design': '', 'design_const': False, 'design_value': '',
        'options': [], 'note_on': [], 'note_rules': {}, 'note_name': '',
        'hidden': False, 'show_on': {}, 'note_for': '', 'min_chars': 0,
        'judge': '', 'judge_op': '', 'judge_ref': '',
        'judge_value': None, 'judge_factor': None, 'judge_delta': None,
        'first_pass': False, 'fail_on': [], 'option_rule': '', 'auto': True,
        'link': link,
    }


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


def parse_flow_sheet(ws, data_headers, option_default=False):
    _hdr, rows = _read_sheet(ws, STEP_COLS)
    out = []
    for ln, d in rows:
        step = d['环节']
        if not step:
            continue
        design = _design_of(d['设计值表头'], data_headers)
        ctl = _control(step, d['控件'], design, ws.title, ln, option_default)
        if ctl['type'] == '下拉' and not ctl.get('verdict'):
            hint = _origin_hint(d['备注'])
            if hint:
                ctl['hint'] = hint
        ctrls = [ctl]
        if ctl.get('note_rules'):
            ctrls.append(_note_control(step, ctl['note_rules']))
        link = _s(d.get('关联小班')) or '不需要'
        if link not in LINKS:
            raise TplError(
                f'「{ws.title}」第 {ln} 行「{step}」的「关联小班」写的是「{link}」，'
                f'只认：{" / ".join(LINKS)}')
        # v0.53：作用对象决定这条流程挂在小班档案上，还是按批次登记（row=-1）
        subject = _s(d.get('作用对象')) or '小班'
        if subject not in SUBJECTS:
            raise TplError(
                f'「{ws.title}」第 {ln} 行「{step}」的「作用对象」写的是「{subject}」，'
                f'只认：{" / ".join(SUBJECTS)}（留空 = 小班）')
        if link != '不需要' and subject != '项目':
            raise TplError(
                f'「{ws.title}」第 {ln} 行「{step}」填了「关联小班={link}」，'
                f'但「作用对象」不是「项目」——一条记录挂多个小班只在项目级流程里有意义；'
                f'小班级流程就填作用对象=小班、关联小班=不需要')
        if link != '不需要':
            ctrls.append(_link_control(step, link))
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
            'controls': ctrls,
            'controls_raw': d['控件'],
            'expert': False,
            'subject': subject,
            'multi': False,
            'link': link,
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
        # 流程共用备注：独立成行的「备注」，或本环节自带的隐藏控件；
        # 选项旁的就地备注（note_for）不算，否则第一个带 →备注 的环节会被误当成流程备注。
        note = next((s for s in steps
                     if s['step'] == '备注'
                     or any(c.get('hidden') and not c.get('note_for')
                            for c in (s['controls'] or []))), None)
        if not note:
            continue
        for s in steps:
            if not s.get('verdict'):
                continue
            s['note_required'] = True
            s['note_field'] = note['step']
            s['note_no'] = note['no']


def parse(wb, data_headers=None, option_default=False, key_col=''):
    """整个 v2 模板 → {'schema':'flow_tpl/1','routes':[...],'flows':{...},'projects':{},'warn':[]}

    v0.53：项目级流程由**环节表「作用对象=项目」**声明，不再从参数页读流程名。
    一条流程里只要有一个「作用对象=项目」的环节，整条流程就是项目级
    （不挂小班档案、一条记录关联多个小班、可多次、不进小班的流程下拉）。
    """
    routes = parse_routes(wb)
    flows = {}
    for name in wb.sheetnames:
        if name.startswith('流程-'):
            flows[name] = parse_flow_sheet(wb[name], data_headers or [], option_default)
    if not flows:
        raise TplError('工作簿里没有任何「流程-」定义表')

    warn = []
    projects = _project_flows(flows, routes, data_headers or [], _s(key_col), warn)
    _attach_note(flows)

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
        'projects': projects,
        'warn': warn,
    }


def _project_flows(flows, routes, data_headers, key_col, warn):
    """哪些流程是项目级（环节表「作用对象=项目」）→ {'key_col','flows':[{flow,name,no}]}。

    项目级 = row_idx=-1：一条记录挂多个小班，所以它必须
      ① 至少一个环节填了「关联小班」（否则提交完不知道覆盖了谁）；
      ② 可多次（每批一条），否则第二批会把第一批顶掉；
      ③ 不进任何小班的流程下拉（否则现场会在某个小班里填成单班记录）。
    **同一张流程表不许混用**：小班级环节和项目级环节放一条流程里，
    记录维度会打架（一半挂小班、一半不挂），解析期直接报错。
    """
    out = []
    for name, steps in flows.items():
        kinds = {(_s(s.get('subject')) or '小班') for s in steps}
        if kinds == {'小班'}:
            continue
        if len(kinds) > 1:
            bad = next(s for s in steps if (_s(s.get('subject')) or '小班') != '项目')
            raise TplError(
                f'「{name}」第 {bad["line"]} 行「{bad["step"]}」：同一张流程表里'
                f'「作用对象」不能既有小班又有项目（项目级流程整条都按批次登记）')
        if not any(s.get('link') in ('单选', '多选') for s in steps):
            warn.append({'level': 'error',
                         'text': f'流程「{name}」声明为项目级，但没有任何环节填「关联小班」'
                                 f'（单选/多选）：提交后无法判定覆盖了哪些小班，'
                                 f'这些小班仍会提示未核验'})
        hit = [r for r in routes if r['flow'] == name]
        if not hit:
            warn.append({'level': 'info',
                         'text': f'项目级流程「{name}」没有出现在「流程路由」里；'
                                 f'仍可从工具栏进，但看不到序号/触发方式'})
        for r in hit:
            r['project'] = True
            r['independent'] = True        # 不参与主线先后，也不被任何前置引用
            if not r['repeat']:
                r['repeat'] = True         # 按批次登记，天然可多次
                warn.append({'level': 'info',
                             'text': f'项目级流程「{name}」按批次登记，已自动改为「可重复」'})
        out.append({'flow': name, 'name': name.replace('流程-', ''),
                    'no': hit[0]['no'] if hit else 0})
    cols = [_s(h) for h in (data_headers or []) if _s(h)]
    key = key_col if key_col in cols else ('小班号' if '小班号' in cols
                                           else (cols[0] if cols else ''))
    return {'key_col': key, 'flows': out}


def routes_for(flows, project_type):
    """某项目类型的流程：主线按「序号」在前，独立流程（巡护）排最后。

    项目类型留空 = 公用，适用于所有类型；同一流程同时有公用和专用路由时，
    专用优先且只保留一条（避免界面出现两个同名流程）。
    project_type=None 表示数据表没有「项目类型」列（单流程/全量验收模板）：
    不按类型过滤，全部路由都可用（同一流程去重）。
    """
    allr = flows.get('routes') or []
    if project_type is None:
        picked, seen = [], set()
        for r in allr:
            if r['flow'] in seen:
                continue
            seen.add(r['flow'])
            picked.append(r)
    else:
        t = _s(project_type)
        matched = [r for r in allr if _s(r.get('type')) in ('', t)]
        exact = {r['flow']: r for r in matched if _s(r.get('type')) == t}
        picked, seen = [], set()
        for r in matched:
            if r['flow'] in seen:
                continue
            seen.add(r['flow'])
            picked.append(exact.get(r['flow'], r))
    return sorted(picked, key=lambda r: (1 if r.get('independent') else 0,
                                         r.get('no') or 0, r.get('line') or 0))


def steps_of(flows, sheet):
    return list((flows.get('flows') or {}).get(sheet) or [])
