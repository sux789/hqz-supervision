# -*- coding: utf-8 -*-
"""业务专家填写版流程配置解析（B28，doc/028）

**为什么从「12 列大宽表」换成这 5 张窄表**：大宽表要求填写人写
`下拉(符合设计;部分完成;未完成)` 这种语法，业务专家写错一个括号，界面就空白且不报错。
窄表把所有"要写语法"的地方换成**选择题**（白名单下拉），写错就是校验错误 + 行号。

| 表 | 谁填 | 填什么 |
|---|---|---|
| ①流程   | 业务专家 | 这套项目有哪几条流程：**入口**（验收/巡视/材料）+ 适用项目类型 |
| ②环节   | 业务专家 | 一条流程有哪几道环节：显示哪些基本元素、哪些不显示、提交什么结论、几次 |
| ③核验项 | 业务专家 | 每环节现场要填哪些格子：控件、必填、标准来源、合格判定、校验规则、不显示的选项 |
| ④选项   | 业务专家 | 下拉/多选选项组（③ 可用 `@组名` 引用） |
| ⑤字段字典 | 程序生成 | data 表列名清单（②③ 只从里面选，不手写） |

### 三个入口（关键设计，doc/028 §二）

    入口 = 验收  → 小班档案 → 按「项目类型」命中 → 界面 tab「验收」
    入口 = 巡视  → 小班档案 → 按「项目类型」命中 → 界面 tab「巡视」（可多次、按次留痕）
    入口 = 材料  → 独立入口（不挂小班，按批次）→ 项目级入口「材料」

**一个入口一条流程**：同一个小班可以同时有「人工造林·验收」和「人工造林·巡视」两条流程，
互不干扰；材料检验不占小班档案，不会因为"苗木没验"锁死造林工序（纪要要求）。

### 专家决定的两级显隐

    ②显示的基本信息（白名单）  留空 = data 全部列都显示
    ②不显示的基本信息（黑名单） 从白名单（或全部）里剔除；黑名单优先
    ③不显示选项               下拉/多选的候选值里剔掉（如「未实施」不给现场选）

### 转序与前置条件

`前置环节` 支持带结论：`整地=通过；回塘及底肥` —— 没写结论的默认 `通过`。
结论不在 `PASS_RESULTS` 里的不放行（提交过但不合格 = 没做完）。

纯标准库 + openpyxl（服务器 shared_venv 可直接用）。
"""

import datetime
import re

from flow_parser import parse_att

# ── sheet 名（固定 5 张，名字带序号便于排序）────────────────────────
SHEET_GUIDE = '填写说明'
SHEET_FLOW = '①流程'
SHEET_STEP = '②环节'
SHEET_FIELD = '③核验项'
SHEET_OPT = '④选项'
SHEET_DICT = '⑤字段字典'

# ── 三个入口 ───────────────────────────────────────────────────────
ENTRIES = ['验收', '巡视', '材料']
ENTRY_MOUNT = {'验收': '小班档案', '巡视': '小班档案', '材料': '独立入口'}
MOUNTS = ['小班档案', '独立入口']

# ── 列定义（顺序即 Excel 列顺序；表头必须完全一致）──────────────────
FLOW_COLS = ['流程名称', '入口', '适用项目类型', '适用范围', '启用', '优先级', '说明']
STEP_COLS = ['流程名称', '入口', '序号', '环节名称', '环节类型', '作用对象', '可填次数', '关联小班',
             '显示的基本信息', '不显示的基本信息', '提交方式', '结论问法', '不通过必填原因',
             '附件要求', '前置环节', '唯一性校验', '业务说明']
FIELD_COLS = ['流程名称', '入口', '环节名称', '核验项', '填什么', '单位', '必填', '标准值来源',
              '合格判定', '可选项', '不显示选项', '校验规则', '填写提示']
OPT_COLS = ['选项组', '可选值', '说明']
DICT_COLS = ['data 列名', '中文名', '单位', '说明']

# ── 白名单（写错就是校验错误，不猜）────────────────────────────────
STEP_TYPES = ['前置确认', '工序验收', '材料检验', '抽查核验', '巡护记录', '汇总验收']
SUBJECTS = ['小班', '苗木批次', '材料批次', '巡护点', '项目']
TIMES = ['一次', '多次']
LINKS = ['不需要', '单选', '多选']
VERDICTS = ['是否通过', '三档结果', '完成情况', '仅记录']
UNIQUES = ['不校验', '同小班同日唯一', '同批次号唯一']
CMP_GE, CMP_LE, CMP_EQ, CMP_DEV, CMP_MANUAL, CMP_NONE = (
    '实测≥标准', '实测≤标准', '实测=标准', '偏差≤5%', '人工判断', '不判定')
CMPS = [CMP_GE, CMP_LE, CMP_EQ, CMP_DEV, CMP_MANUAL, CMP_NONE]

CTL_MAP = {
    '数值': '数值', '数值×3': '数值×3', '下拉': '下拉', '多选': '多选', '勾选': '勾选',
    '单行文本': '单行文本', '多行文本': '多行纯文本', '日期': '日期', '自动算': '数值',
}
CTL_TYPES = list(CTL_MAP)

VERDICT_CTL = {
    '是否通过': {'name': '是否通过', 'type': '下拉', 'arg': '通过;不通过'},
    '三档结果': {'name': '验收结果', 'type': '下拉', 'arg': '合格;整改后合格;不合格'},
    '完成情况': {'name': '完成情况', 'type': '下拉', 'arg': '全部完成;部分完成;未完成'},
}

# 转序判定：只有这些结论算"这一关过了"
PASS_RESULTS = ('通过', '合格', '整改后合格', '全部完成', '已完成', '符合')

# 「显示的基本信息」留空 = data 全部列，但这几列是**本系统自己产出的填报结果**，
# 不是本底参考元素。不藏起来的话，现场会在"不可修改"的基本信息里看到
# 「验收结果：—」，误以为这一环节已经有结论了（2026-10-21 截图自测发现）。
# 只在白名单留空（= 全部）时生效；专家显式写进白名单就尊重他的选择。
DEFAULT_HIDE = ['验收人', '验收日期', '验收结果', '验收备注']

_SPLIT = re.compile(r'[;；、,，]')
_SPLIT_LINE = re.compile(r'[;；\n]')


def _s(v):
    return '' if v is None else str(v).strip()


def _to_int(v, default=0):
    try:
        return int(float(_s(v)))
    except (TypeError, ValueError):
        return default


def _list(text):
    return [x.strip() for x in _SPLIT.split(_s(text)) if x.strip()]


def is_user_doc(wb):
    """这份工作簿是不是业务专家填写版（靠 sheet 名判断）。"""
    names = set(wb.sheetnames)
    return SHEET_STEP in names and SHEET_FLOW in names


def _read(ws, cols, sheet_label):
    """读成 (表头, [{列: 值, '_line': Excel 行号}], 缺失列)。

    read_only 模式下工作簿流只能顺序读一次 —— 表头与数据必须在这一遍里全拿到
    （v0.31 踩过：二次 iter_rows 拿到空表头，误报"缺填报控件列"）。
    """
    rows = list(ws.iter_rows(max_col=len(cols), values_only=True))
    if not rows:
        return cols, [], list(cols)
    hdrs = [_s(h) for h in rows[0]]
    while hdrs and not hdrs[-1]:
        hdrs.pop()
    lack = [c for c in cols if c not in hdrs]
    out = []
    for i, r in enumerate(rows[1:], 2):
        cells = list(r) + [None] * (len(cols) - len(r))
        if not any(x not in (None, '') for x in cells):
            continue
        d = dict(zip(hdrs + [''], cells))
        d = {c: d.get(c) for c in cols}
        d['_line'] = i
        out.append(d)
    return hdrs, out, lack


# ── 校验规则（服务端权威，③「校验规则」列）──────────────────────────

def check_rule(rule, value, label=''):
    """按 `校验规则` 检查一个值。返回 '' 表示通过，否则返回一句人话。

    支持（多个用；分隔，全部满足才通过）：
      `整数`  `≥0`  `≤100`  `>0`  `<50`  `10-100`（闭区间）
      `≤500字`（长度）  `日期≥今天`  `正则:^\\d{11}$`
    """
    t = _s(rule)
    if not t:
        return ''
    v = '' if value is None else str(value).strip()
    if v == '':
        return ''                      # 空值归"必填"管，不归规则管
    for tok in _SPLIT_LINE.split(t):
        tok = tok.strip()
        if not tok:
            continue
        nm = label or '该值'
        if tok == '整数':
            if not re.match(r'^-?\d+$', v):
                return f'{nm}必须是整数'
            continue
        if tok == '日期≥今天':
            try:
                d = datetime.date.fromisoformat(v[:10])
            except ValueError:
                return f'{nm}不是合法日期（要 2026-10-21 这种）'
            if d < datetime.date.today():
                return f'{nm}不能早于今天'
            continue
        m = re.match(r'^≤\s*(\d+)\s*字$', tok)
        if m:
            if len(v) > int(m.group(1)):
                return f'{nm}不能超过 {m.group(1)} 字（现 {len(v)} 字）'
            continue
        m = re.match(r'^正则[:：](.+)$', tok)
        if m:
            try:
                if not re.search(m.group(1), v):
                    return f'{nm}格式不对'
            except re.error:
                return ''              # 正则写错不该拦现场，由配置校验报
            continue
        m = re.match(r'^(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)$', tok)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            try:
                x = float(v)
            except ValueError:
                return f'{nm}必须是数字'
            if not (lo <= x <= hi):
                return f'{nm}要在 {m.group(1)}~{m.group(2)} 之间（现 {v}）'
            continue
        m = re.match(r'^(≥|>=|≤|<=|>|<)\s*(-?\d+(?:\.\d+)?)$', tok)
        if m:
            op, num = m.group(1), float(m.group(2))
            try:
                x = float(v)
            except ValueError:
                return f'{nm}必须是数字'
            bad = {'≥': x < num, '>=': x < num, '≤': x > num, '<=': x > num,
                   '>': x <= num, '<': x >= num}[op]
            if bad:
                return f'{nm}不满足「{tok}」（现 {v}）'
            continue
    return ''


def rule_ok(rule):
    """配置期判断规则写法是否可识别（不能识别 → 校验错误，免得静默不校验）。"""
    t = _s(rule)
    if not t:
        return True
    pats = [r'^整数$', r'^日期≥今天$', r'^≤\s*\d+\s*字$', r'^正则[:：].+$',
            r'^\d+(?:\.\d+)?\s*[-~]\s*\d+(?:\.\d+)?$',
            r'^(≥|>=|≤|<=|>|<)\s*-?\d+(?:\.\d+)?$']
    for tok in _SPLIT_LINE.split(t):
        tok = tok.strip()
        if tok and not any(re.match(p, tok) for p in pats):
            return False
    return True


# ── ②环节 / ③核验项 解析 ────────────────────────────────────────────

def _resolve_options(text, opts):
    """可选项三写法：字面 `符合;不符合` / `@组名` / `data:列名`。"""
    t = _s(text)
    if not t:
        return ''
    if t.startswith('data:') or t.startswith('data：'):
        col = t.split(':', 1)[-1].split('：', 1)[-1].strip()
        return 'data「%s」枚举' % col
    if t.startswith('@'):
        vals = opts.get(t[1:].strip())
        return ';'.join(vals) if vals else ''
    return ';'.join(_list(t))


def _ctl_of(f, opts, warnings):
    """③核验项 一行 → 控件描述（flow.js 直接渲染）。"""
    name = _s(f.get('核验项'))
    kind = _s(f.get('填什么'))
    unit = _s(f.get('单位'))
    must = _s(f.get('必填')) in ('是', '必填', 'Y', 'y', '')
    std_col = _s(f.get('标准值来源'))
    cmp_ = _s(f.get('合格判定')) or CMP_MANUAL
    hint = _s(f.get('填写提示'))
    drop = _list(f.get('不显示选项'))
    rule = _s(f.get('校验规则'))
    line = f.get('_line')

    if kind not in CTL_MAP:
        warnings.append({'sheet': SHEET_FIELD, 'line': line,
                         'msg': '「填什么」写的是「%s」，只认：%s（已按「单行文本」处理）'
                                % (kind, '、'.join(CTL_TYPES))})
        kind = '单行文本'
    ctype = CTL_MAP[kind]

    arg = ''
    if kind in ('下拉', '多选'):
        arg = _resolve_options(f.get('可选项'), opts)
        if not arg:
            warnings.append({'sheet': SHEET_FIELD, 'line': line,
                             'msg': '「%s」是%s，但「可选项」是空的，界面会出一个没有选项的框'
                                    % (name, kind)})
        arg = arg or '（未配置选项）'
        if drop and not arg.startswith('data'):
            allopt = [x for x in arg.split(';') if x]
            lack = [x for x in drop if x not in allopt]
            if lack:
                warnings.append({'sheet': SHEET_FIELD, 'line': line,
                                 'msg': '「不显示选项」里的「%s」不在可选项里（会被忽略）'
                                        % '、'.join(lack)})
    elif kind == '数值×3':
        arg = '长/宽/深,%s' % (unit or '')
    elif kind == '自动算':
        arg = '%s,系统自动算' % (unit or '')
    else:
        bits = [unit] if unit else []
        if hint:
            bits.append(hint)
        arg = ','.join(bits)

    if not must and kind not in ('勾选', '自动算'):
        arg = (arg + ',选填') if arg else '选填'
    if cmp_ not in CMPS:
        warnings.append({'sheet': SHEET_FIELD, 'line': line,
                         'msg': '「合格判定」写的是「%s」，只认：%s（已按「人工判断」处理）'
                                % (cmp_, '、'.join(CMPS))})
        cmp_ = CMP_MANUAL

    return {'name': name, 'type': ctype, 'arg': arg,
            'sub': ([name + '长', name + '宽', name + '深'] if ctype == '数值×3' else []),
            'std_col': std_col, 'cmp': cmp_, 'hint': hint, 'unit': unit,
            'required': bool(must), 'drop': drop, 'rule': rule}


def _hide_list(row):
    """②「不显示的基本信息」+ 白名单留空时的默认黑名单（保持顺序、去重）。"""
    hide = _list(row.get('不显示的基本信息'))
    if _list(row.get('显示的基本信息')):
        return hide                       # 专家点名了要看的列 → 只听他的
    out = list(DEFAULT_HIDE)
    for k in hide:
        if k not in out:
            out.append(k)
    return out


def _step_of(row, fields, opts, entry, warnings):
    no = _to_int(row.get('序号'), 0)
    step = _s(row.get('环节名称'))
    line = row.get('_line')
    verdict = _s(row.get('提交方式')) or '仅记录'
    if verdict not in VERDICTS:
        warnings.append({'sheet': SHEET_STEP, 'line': line,
                         'msg': '「提交方式」写的是「%s」，只认：%s（已按「仅记录」处理）'
                                % (verdict, '、'.join(VERDICTS))})
        verdict = '仅记录'
    multi = _s(row.get('可填次数')) == '多次'
    subject = _s(row.get('作用对象')) or '小班'
    link = _s(row.get('关联小班')) or '不需要'
    unique = _s(row.get('唯一性校验')) or '不校验'

    controls = [_ctl_of(f, opts, warnings) for f in fields]

    if verdict in VERDICT_CTL:
        c = dict(VERDICT_CTL[verdict])
        c.update({'std_col': '', 'cmp': CMP_NONE, 'sub': [], 'hint': '', 'unit': '',
                  'required': True, 'verdict': True, 'drop': [], 'rule': ''})
        controls.append(c)

    if multi and not any(c['type'] == '日期' and '日期' in c['name'] for c in controls):
        controls.append({'name': '记录日期', 'type': '日期', 'arg': '', 'sub': [],
                         'std_col': '', 'cmp': CMP_NONE, 'hint': '多次填报时用于区分每次记录',
                         'unit': '', 'required': True, 'auto': True, 'drop': [], 'rule': ''})

    if link in ('单选', '多选'):
        controls.append({'name': '关联小班', 'type': '多选' if link == '多选' else '下拉',
                         'arg': 'data「小班号」枚举', 'sub': [], 'std_col': '',
                         'cmp': CMP_NONE, 'hint': '一条记录可关联多个小班' if link == '多选' else '',
                         'unit': '', 'required': True, 'auto': True, 'drop': [], 'rule': ''})

    # 前置环节：`整地=通过；回塘及底肥` → [{step,result}]；没写结论的默认「通过」
    prev = []
    for item in _list(row.get('前置环节')):
        if '=' in item or '＝' in item:
            nm, want = re.split(r'[=＝]', item, maxsplit=1)
            prev.append({'step': nm.strip(), 'result': want.strip() or '通过'})
        else:
            prev.append({'step': item, 'result': '通过'})

    standards = [c['std_col'] for c in controls if c['std_col']]
    factors = [c['name'] for c in controls if not c.get('verdict') and not c.get('auto')]
    return {
        'no': no, 'step': step, 'type': _s(row.get('环节类型')) or '工序验收',
        'entry': entry,
        'factors': '、'.join(factors),
        'std_from': '、'.join(dict.fromkeys(standards)),
        'require': _s(row.get('业务说明')),
        'check': '、'.join(dict.fromkeys([c['cmp'] for c in controls
                                         if c['cmp'] not in (CMP_NONE, CMP_MANUAL)])) or '人工判断',
        'att_raw': _s(row.get('附件要求')),
        'pass': ('%s = 通过' % _s(row.get('结论问法'))
                 if verdict == '是否通过' else
                 '%s 达到「%s」' % (_s(row.get('环节类型')), verdict)),
        'seq': _s(row.get('前置环节')),
        'memo': _s(row.get('业务说明')),
        'attachments': parse_att(_s(row.get('附件要求'))),
        'controls': controls,
        'controls_raw': '；'.join('%s=%s' % (c['name'], c['type']) for c in controls),
        # ── 业务专家文档维度 ──
        # expert 标记很关键：老 12 列流程没有这几列，前端要能区分
        # 「业务专家没填白名单（= 显示 data 全部列）」和「老流程（= 不显示基本信息块）」
        'expert': True,
        'subject': subject,
        'multi': multi,
        'link': link,
        'basic_info': _list(row.get('显示的基本信息')),
        'basic_hide': _hide_list(row),
        'verdict': verdict,
        'question': _s(row.get('结论问法')) or '是否通过',
        'note_required': _s(row.get('不通过必填原因')) == '是',
        'unique': unique,
        'prev': [p['step'] for p in prev],
        'prev_result': {p['step']: p['result'] for p in prev},
        'need_result': verdict == '三档结果',
        'line': line,
    }


# ── 校验（错误带 sheet + 行号，C02 口径）──────────────────────────────

def _err(sheet, line, msg):
    return {'sheet': sheet, 'line': line, 'msg': f'「{sheet}」第 {line} 行：{msg}'}


def flow_key(name, entry):
    """流程在系统里的唯一标识（也存进 flow_records.flow_sheet）。"""
    return '流程-%s·%s' % (name, entry)


def parse(wb, data_headers=None):
    """解析业务专家填写版工作簿 → {registry, flows, errors, warnings, summary}。"""
    errors, warnings = [], []
    if not is_user_doc(wb):
        return {'registry': [], 'flows': {}, 'errors': [_err(SHEET_STEP, 1,
                f'缺少「{SHEET_STEP}」「{SHEET_FLOW}」表，这份文件不是业务专家填写版')],
                'warnings': [], 'summary': {}}

    opts = {}
    if SHEET_OPT in wb.sheetnames:
        _h, orows, _l = _read(wb[SHEET_OPT], OPT_COLS, SHEET_OPT)
        for r in orows:
            g = _s(r.get('选项组'))
            if g:
                opts[g] = _list(r.get('可选值'))

    _h, frows, flack = _read(wb[SHEET_FLOW], FLOW_COLS, SHEET_FLOW)
    for c in flack:
        errors.append(_err(SHEET_FLOW, 1, f'缺少「{c}」列'))
    _h, srows, slake = _read(wb[SHEET_STEP], STEP_COLS, SHEET_STEP)
    for c in slake:
        errors.append(_err(SHEET_STEP, 1, f'缺少「{c}」列'))
    _h, crows, clake = _read(wb[SHEET_FIELD], FIELD_COLS, SHEET_FIELD)
    for c in clake:
        errors.append(_err(SHEET_FIELD, 1, f'缺少「{c}」列'))

    # 列名校验来源：有 data 表头用 data；独立文件用 ⑤字段字典
    if not data_headers and SHEET_DICT in wb.sheetnames:
        _h, drows, _l = _read(wb[SHEET_DICT], DICT_COLS, SHEET_DICT)
        data_headers = [r.get('data 列名') for r in drows]
    hdr_set = {str(h).strip() for h in (data_headers or []) if str(h or '').strip()}

    # ①流程
    registry, flow_line, flow_order = [], {}, []
    for r in frows:
        name = _s(r.get('流程名称'))
        if not name:
            continue
        ln = r.get('_line')
        entry = _s(r.get('入口'))
        if entry not in ENTRIES:
            errors.append(_err(SHEET_FLOW, ln,
                               f'入口「{entry or "空"}」不认识，只认：{"、".join(ENTRIES)}'))
            continue
        key = (name, entry)
        if key in flow_line:
            errors.append(_err(SHEET_FLOW, ln,
                               f'「{name}」的「{entry}」入口重复定义（第 {flow_line[key]} 行已有）'))
            continue
        mount = ENTRY_MOUNT[entry]
        ptype = _s(r.get('适用项目类型'))
        if entry != '材料' and not ptype:
            warnings.append(_err(SHEET_FLOW, ln,
                                 '「适用项目类型」没填，这条流程会对**全部小班**生效'
                                 '（包括别的项目类型），建议填上'))
        if entry == '材料':
            cond_f, cond_v = '独立入口', '独立入口'    # 永不自动命中，走项目级入口
        else:
            cond_f = '项目类型' if ptype else '1'
            cond_v = ';'.join(_list(ptype))
        registry.append({'sheet': flow_key(name, entry), 'enabled': _s(r.get('启用')) == '是',
                         'cond_field': cond_f, 'cond_val': cond_v,
                         'scope': _s(r.get('适用范围')) or '全部',
                         'prio': _to_int(r.get('优先级'), 999),
                         'note': _s(r.get('说明')), 'line': ln,
                         'name': name, 'entry': entry, 'mount': mount, 'ptype': ptype})
        flow_line[key] = ln
        flow_order.append(key)

    # ③核验项按 (流程, 入口, 环节) 归拢
    fmap = {}
    for r in crows:
        fn, en, sn, nm = (_s(r.get('流程名称')), _s(r.get('入口')),
                          _s(r.get('环节名称')), _s(r.get('核验项')))
        if not (fn or sn or nm):
            continue
        ln = r.get('_line')
        if not nm:
            errors.append(_err(SHEET_FIELD, ln, '「核验项」是空的，要么填名字，要么删掉这一行'))
            continue
        if (fn, en) not in flow_line:
            errors.append(_err(SHEET_FIELD, ln,
                               f'「{fn}」+「{en or "空"}」在「{SHEET_FLOW}」里没有定义'))
            continue
        fmap.setdefault((fn, en, sn), []).append(r)
        sc = _s(r.get('标准值来源'))
        if sc and hdr_set and sc not in hdr_set:
            warnings.append(_err(SHEET_FIELD, ln,
                                 f'标准值来源「{sc}」在 data 表里没有这一列 → '
                                 f'该核验项只能人工判断（data 补列后自动生效）'))
        kind = _s(r.get('填什么'))
        if kind not in CTL_TYPES:
            errors.append(_err(SHEET_FIELD, ln,
                               f'「填什么」写的是「{kind}」，只认：{"、".join(CTL_TYPES)}'))
        if kind in ('下拉', '多选') and not _s(r.get('可选项')):
            errors.append(_err(SHEET_FIELD, ln,
                               f'「{nm}」是{kind}，但「可选项」空着 → 界面上会出一个空下拉框'))
        rule = _s(r.get('校验规则'))
        if rule and not rule_ok(rule):
            errors.append(_err(SHEET_FIELD, ln,
                               f'「校验规则」里有一段认不出来：{rule}。'
                               f'支持 整数 / ≥0 / ≤100 / 10-100 / ≤500字 / 日期≥今天 / 正则:…'))

    # ②环节
    flows, used = {}, set()
    for r in srows:
        fn, en, step = _s(r.get('流程名称')), _s(r.get('入口')), _s(r.get('环节名称'))
        ln = r.get('_line')
        if not (fn or step):
            continue
        if (fn, en) not in flow_line:
            errors.append(_err(SHEET_STEP, ln,
                               f'「{fn}」+「{en or "空"}」在「{SHEET_FLOW}」里没有定义'))
            continue
        if not step:
            errors.append(_err(SHEET_STEP, ln, '「环节名称」是空的'))
            continue
        stype = _s(r.get('环节类型'))
        if stype not in STEP_TYPES:
            errors.append(_err(SHEET_STEP, ln,
                               f'环节类型「{stype}」不认识，只认：{"、".join(STEP_TYPES)}'))
        subj = _s(r.get('作用对象')) or '小班'
        if subj not in SUBJECTS:
            errors.append(_err(SHEET_STEP, ln,
                               f'作用对象「{subj}」不认识，只认：{"、".join(SUBJECTS)}'))
        if en == '材料' and subj == '小班':
            warnings.append(_err(SHEET_STEP, ln,
                                 '材料入口的记录不挂小班档案，"作用对象"建议用 苗木批次/材料批次'))
        times = _s(r.get('可填次数')) or '一次'
        if times not in TIMES:
            errors.append(_err(SHEET_STEP, ln, f'可填次数「{times}」不认识，只认：一次 / 多次'))
        lk = _s(r.get('关联小班')) or '不需要'
        if lk not in LINKS:
            errors.append(_err(SHEET_STEP, ln,
                               f'关联小班「{lk}」不认识，只认：{"、".join(LINKS)}'))
        ver = _s(r.get('提交方式')) or '仅记录'
        if ver not in VERDICTS:
            errors.append(_err(SHEET_STEP, ln,
                               f'提交方式「{ver}」不认识，只认：{"、".join(VERDICTS)}'))
        nr = _s(r.get('不通过必填原因')) or '是'
        if nr not in ('是', '否'):
            errors.append(_err(SHEET_STEP, ln, f'「不通过必填原因」只能填 是 / 否，填的是「{nr}」'))
        uq = _s(r.get('唯一性校验')) or '不校验'
        if uq not in UNIQUES:
            errors.append(_err(SHEET_STEP, ln,
                               f'唯一性校验「{uq}」不认识，只认：{"、".join(UNIQUES)}'))
        for c in _list(r.get('显示的基本信息')):
            if hdr_set and c not in hdr_set:
                errors.append(_err(SHEET_STEP, ln,
                                   f'「显示的基本信息」里的「{c}」在 data 表里没有这一列'))
        for c in _list(r.get('不显示的基本信息')):
            if hdr_set and c not in hdr_set:
                warnings.append(_err(SHEET_STEP, ln,
                                     f'「不显示的基本信息」里的「{c}」在 data 表里不存在（会被忽略）'))
        if ver == '仅记录' and en == '验收':
            warnings.append(_err(SHEET_STEP, ln,
                                 '验收入口的「仅记录」环节没有结论，转序只能靠"提交过"；'
                                 '如果它是工序，建议改成「是否通过」'))
        fields = fmap.get((fn, en, step), [])
        if not fields and ver == '仅记录':
            errors.append(_err(SHEET_STEP, ln,
                               f'环节「{step}」既没有核验项、提交方式又是「仅记录」→ '
                               f'现场打开是空的，请至少加一个核验项'))
        flows.setdefault(flow_key(fn, en), []).append(
            _step_of(r, fields, opts, en, warnings))
        used.add((fn, en))

    # 序号连续性 / 环节重名 / 前置环节
    for sheet_name, steps in flows.items():
        label = sheet_name[len('流程-'):]
        steps.sort(key=lambda s: (s['no'] or 9999, s['line']))
        names = [s['step'] for s in steps]
        for i, s in enumerate(steps, 1):
            if s['no'] != i:
                errors.append(_err(SHEET_STEP, s['line'],
                                   f'序号不连续：流程「{label}」第 {i} 个环节写的序号是 '
                                   f'{s["no"]}，应改为 {i}（序号必须 1、2、3… 连续）'))
        for s in steps:
            if names.count(s['step']) > 1:
                errors.append(_err(SHEET_STEP, s['line'],
                                   f'环节「{s["step"]}」在同一流程里出现了 '
                                   f'{names.count(s["step"])} 次，环节名要唯一'))
        for s in steps:
            for p in s['prev']:
                if p not in names:
                    errors.append(_err(SHEET_STEP, s['line'],
                                       f'前置环节「{p}」在流程「{label}」里不存在'))
                elif names.index(p) >= names.index(s['step']):
                    errors.append(_err(SHEET_STEP, s['line'],
                                       f'前置环节「{p}」排在本环节之后（或就是自己），转序会死锁'))
                elif s['prev_result'].get(p) and s['prev_result'][p] not in PASS_RESULTS:
                    warnings.append(_err(SHEET_STEP, s['line'],
                                         f'前置条件写的是「{p}={s["prev_result"][p]}」，'
                                         f'这个结论本身不算"通过"，该环节永远进不去'))

    for key in flow_order:
        if key not in used:
            errors.append(_err(SHEET_FLOW, flow_line[key],
                               f'流程「{key[0]}·{key[1]}」一个环节都没有，至少要有 1 个'))

    by_entry = {}
    for r in registry:
        by_entry[r['entry']] = by_entry.get(r['entry'], 0) + 1
    summary = {
        'flows': len(flows),
        'steps': sum(len(v) for v in flows.values()),
        'fields': sum(len([1 for c in s['controls'] if not c.get('verdict')
                           and not c.get('auto')]) for v in flows.values() for s in v),
        'multi_steps': sum(1 for v in flows.values() for s in v if s['multi']),
        'entries': by_entry,
        'subjects': sorted({s['subject'] for v in flows.values() for s in v}),
    }
    return {'registry': registry, 'flows': flows,
            'errors': errors, 'warnings': warnings, 'summary': summary}


def parse_path(path, data_headers=None):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return parse(wb, data_headers=data_headers)
    finally:
        try:
            wb.close()
        except Exception:
            pass


def check_lines(res, limit=20):
    out = []
    for e in res.get('errors', [])[:limit]:
        out.append('✗ ' + e['msg'])
    more = len(res.get('errors', [])) - limit
    if more > 0:
        out.append(f'… 另有 {more} 条错误')
    for w in res.get('warnings', [])[:limit]:
        out.append('⚠ ' + w['msg'])
    return '\n'.join(out)
