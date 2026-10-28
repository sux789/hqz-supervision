# -*- coding: utf-8 -*-
"""验收流程解析（B18 / B19，doc/019）

从 Excel 的两类 sheet 读出流程配置，驱动 App 端填报界面：

| sheet          | 命名规则                  | 作用                          |
|----------------|---------------------------|-------------------------------|
| 流程注册表     | 固定名 `验收流程`          | 有哪些流程、对谁生效（条件）   |
| 流程定义表     | 名称以 `流程-` 开头        | 一个流程内有哪些验收环节       |

设计口径（承接 C01/C11「配置驱动」）：界面上每个输入框、下拉、按钮都由本模块解析
「填报控件」「附件要求」两列得出，**管理层改 Excel 即生效，程序不改**。

三条实现铁律（都是实测踩出来的，写在这里防止回退）：

1. **括号感知分割**：`字段=类型(参数;参数)` 语法里分号可能出现在括号内
   （`下拉(符合设计;部分完成;未完成)`、`多选(浇水;培土;补植;病虫害防治)`），
   按 `;` 裸切会把选项全丢掉。必须先数括号深度，只在括号外切。与 C19 同源。

2. **规则表顺序敏感、命中即停**：自然语言兜底规则（`照片|现场`）必须排在最后，
   否则每个环节都会多出一个冗余的"现场照片"按钮。标签取片段自身而非规则里的通用名。

3. **列名匹配一律 trim**：data 表头「总株数  」末尾带 2 个空格（key 长度 5），
   不 trim 就取不到设计株数，种植数量的合格判定会整条失效。
"""

import re

# ── sheet 命名约定 ────────────────────────────────────────────────
PARAM_SHEET = '参数'
DATA_SHEET = 'data'
FLOW_REGISTRY_SHEET = '验收流程'
FLOW_PREFIX = '流程-'          # 流程定义表前缀（使用说明里的红线：不可改）
FLOW_CTRL_COL = '填报控件'      # 流程定义表第 12 列
FLOW_ATT_COL = '附件要求'       # 流程定义表第 8 列

# 流程定义表的 12 个列（顺序取自 Excel 表头；用名字取，不用下标）
FLOW_COLS = ['序号', '验收环节', '验收类型', '核验因子', '标准值来源', '填报要求',
             '校验方式', '附件要求', '通过标准', '转序规则', '备注', '填报控件']

# 注册表 7 列
REG_COLS = ['流程sheet名', '启用', '条件字段', '条件值', '适用范围', '优先级', '备注']

# ── 控件类型（白名单，防止 Excel 里写错类型导致界面出不来东西）────────
KNOWN_CTL = {
    '勾选', '自动', '日期', '数值', '数值×3', '下拉', '单行文本', '多选', '多行纯文本',
}

# ── 附件按钮类型 ───────────────────────────────────────────────────
# photo  = App 内实时拍摄（禁相册选图，防造假）
# upload = 可选照片/PDF（证件、单据），操作人/时间/定位仍留痕
# punch  = 定位打卡（单点标记）。≠ 连续记录的「◎ 轨迹」
ATT_KIND = {'拍照': 'photo', '上传': 'upload', '打卡': 'punch'}

# 自然语言兜底规则表。**顺序敏感，命中即停**（铁律 2）：
# 越具体的放前面，`照片|现场` 这种泛化兜底必须垫底。
ATT_RULES = [
    (re.compile(r'定位打卡|打卡'), 'punch', '定位打卡', 1, True),
    (re.compile(r'巡护记录'), 'upload', '巡护记录', 1, True),
    (re.compile(r'三证一签|四证一签|证件'), 'upload', '苗木证件', 1, True),
    (re.compile(r'种苗|苗木规格'), 'photo', '种苗规格', 0, True),
    (re.compile(r'剪苗'), 'photo', '剪苗作业', 0, True),
    (re.compile(r'巡护'), 'photo', '巡护照片', 0, True),
    (re.compile(r'回塘|放肥|底肥|覆土'), 'photo', '', 0, False),
    (re.compile(r'撑杆|覆膜'), 'photo', '', 0, False),
    (re.compile(r'整地'), 'photo', '整地现场', 0, True),
    (re.compile(r'成活率'), 'photo', '成活率抽查', 0, True),
    (re.compile(r'保存率|竣工'), 'photo', '竣工现场', 0, True),
    (re.compile(r'边界'), 'photo', '小班边界', 0, True),
    (re.compile(r'照片|现场'), 'photo', '现场照片', 0, True),
]


class FlowError(Exception):
    """流程 sheet 违规，message 已带 Excel 行号。"""


def _s(v):
    return '' if v is None else str(v).strip()


def _to_int(v, default=0):
    try:
        return int(float(_s(v)))
    except (TypeError, ValueError):
        return default


# ── 括号感知分割（铁律 1）────────────────────────────────────────────

def split_top(text, sep=';'):
    """按 sep 切，但**只在括号外**切。

    `下拉(符合设计;部分完成;未完成)` → ['下拉(符合设计;部分完成;未完成)']（1 段）
    `数值(kg/袋,>0); 单行文本(≤20字)` → ['数值(kg/袋,>0)', '单行文本(≤20字)']（2 段）
    """
    out, buf, depth = [], '', 0
    for ch in text or '':
        if ch in '（(':
            depth += 1
        elif ch in '）)':
            depth -= 1
        if ch == sep and depth <= 0:
            out.append(buf)
            buf = ''
            continue
        buf += ch
    out.append(buf)
    return out


# ── 「填报控件」列解析 ──────────────────────────────────────────────

def parse_ctl(text):
    """`确认项=勾选(逐项); 验收人=自动(登录账号)` → [{name,type,arg,sub}, ...]

    用 split_top 而非 split(';')，否则括号内的分号会把选项切断（铁律 1）。

    `数值×3`（如 整地规格=数值×3(长/宽/深,cm)）会额外产出 `sub`：三个子字段的**完整名字**，
    如 ['整地长','整地宽','整地深']。前后端都按 sub 取值，不各自拼字符串
    ——拼法一旦不一致，服务端必填校验就会把已填的格子判成未填（实测踩过）。
    """
    out = []
    for seg in split_top(_s(text), ';'):
        seg = seg.strip()
        if not seg:
            continue
        i = seg.find('=')
        if i < 0:
            continue
        name = seg[:i].strip()
        rest = seg[i + 1:].strip()
        ctype, arg = rest, ''
        m = re.match(r'^([^(（]+)\((.*)\)$', rest)
        if m:
            ctype, arg = m.group(1).strip(), m.group(2).strip()
        item = {'name': name, 'type': ctype, 'arg': arg}
        if ctype == '数值×3':
            # arg 形如「长/宽/深,cm,≥标准自动判」，第一段是三个分量名
            dims = re.split(r'[/／]', arg)[0].strip() if arg else ''
            parts = [x.strip() for x in re.split(r'[/／、,]', dims) if x.strip()]
            item['sub'] = ([name + p for p in parts[:3]] if len(parts) >= 3
                           else [name + p for p in ('长', '宽', '深')])
        else:
            item['sub'] = []
        out.append(item)
    return out


def ctl_options(ctl):
    """多选/下拉控件的选项列表：从 arg 里按分号切（此时分号已在括号内，安全）。"""
    arg = ctl.get('arg') or ''
    if not arg:
        return []
    return [x.strip() for x in arg.split(';') if x.strip()]


# ── 「附件要求」列解析（两层）─────────────────────────────────────────

def _tidy_label(p):
    """标签取片段自身，剥掉「照片」「各N张」这类尾巴，读起来就是现场话术。"""
    s = re.sub(r'各\s*\d+\s*张', '', p)
    # 业务员写的是「整地现场照片 2 张」（不是「各 2 张」），这张数要算进下限、不进标签
    s = re.sub(r'\d+\s*张', '', s)
    s = re.sub(r'照片?$', '', s.strip())
    s = re.sub(r'的$', '', s)
    return s.strip() or p


_ATT_TYPE_RX = re.compile(r'^(图片|照片|pdf|PDF|Pdf)\s*[≥>=]+\s*(\d+)$')


def _att_types(arg):
    """`图片≥1;PDF≥1` → ({'image': 1, 'pdf': 1}, warn)。"""
    types, warn = {}, ''
    for rule in re.split(r'[;；]', arg or ''):
        rule = rule.strip()
        if not rule:
            continue
        m = _ATT_TYPE_RX.match(rule)
        if not m:
            warn = f'无法识别的附件规则「{rule}」，已忽略'
            continue
        key = 'pdf' if m.group(1).lower() == 'pdf' else 'image'
        types[key] = _to_int(m.group(2))
    return types, warn


def parse_att(text):
    """`拍照:整地现场照片@2` 或 `上传:巡护资料@3(图片≥1;PDF≥1)` → [{kind,label,min,types}, ...]

    第一层是正式语法（`拍照:` / `上传:` / `打卡:` + 半角分号组合 + `@N` 张数下限
    + 可选类型下限 `(图片≥A;PDF≥B)`）；第二层是自然语言兜底（当前 Excel 里就是这么写的），
    按规则表逐片匹配。`types` 只对上传有意义，拍照/打卡恒为空。
    """
    t = _s(text)
    if not t:
        return []

    # ── 正式语法 ──
    if re.match(r'^(拍照|上传|打卡):', t):
        out = []
        for seg in split_top(t, ';'):
            seg = seg.strip()
            if not seg:
                continue
            m = re.match(r'^(拍照|上传|打卡):(.+?)(?:@(\d+))?'
                         r'(?:[（(]([^()（）]*)[)）])?$', seg)
            if not m:
                continue
            types, warn = _att_types(m.group(4))
            out.append({'kind': ATT_KIND[m.group(1)], 'label': m.group(2).strip(),
                        'min': _to_int(m.group(3)), 'types': types,
                        'raw': seg, 'warn': warn})
        return out

    # ── 自然语言兜底（铁律 2）──
    parts = [x.strip() for x in re.split(r'[、，,＋+]', t) if x.strip()]
    merged = []
    for p in parts:
        # 片段没有任何关键词 → 并入上一片（处理「撑杆、覆膜照片」这种省略写法）
        if not any(r.search(p) for r, _, _, _, _ in ATT_RULES) and merged:
            merged[-1] += '、' + p
        else:
            merged.append(p)

    out, seen = [], set()
    for p in merged:
        kind, min_, label, fixed = 'photo', 0, '', False
        for rx, k, lb, mn, fx in ATT_RULES:       # 命中即停
            if rx.search(p):
                kind, min_, fixed = k, mn, fx
                label = lb if fx else _tidy_label(p)
                break
        else:
            label = _tidy_label(p)

        per = re.search(r'各\s*(\d+)\s*张', p)
        warn = ''
        if per:
            n = _to_int(per.group(1))
            if min_ and min_ != n:
                warn = f'“各{n}张”与该按钮下限冲突，取 {n}'
            min_ = n or min_
        else:
            # 「整地现场照片 2 张」这类写法（业务员填写版最常见），张数直接算下限
            one = re.search(r'(\d+)\s*张', p)
            if one:
                min_ = _to_int(one.group(1)) or min_
        if not label:
            label = _tidy_label(p)

        key = f'{kind}/{label}'
        if key in seen:                          # 同名按钮只出一个
            continue
        seen.add(key)
        out.append({'kind': kind, 'label': label, 'min': min_, 'types': {},
                    'raw': p, 'warn': warn})
    return out


# ── 标准值取值（铁律 3）──────────────────────────────────────────────

def std_lookup(row):
    """把一行 data 转成「列名 → 值」的字典，**同时登记原名与 trim 名**。

    「总株数  」这类带尾随空格的表头，调用方用 '总株数' 就能取到（铁律 3）；
    原名也保留，是因为前端要按 data 表头原文做下拉枚举取数（app.js 注入 __supRows 时用）。

    注意：这里刻意**不用 _s()**（它会 strip 掉尾随空格，原名就丢了）。
    """
    out = {}
    for k, v in dict(row).items():
        raw = '' if k is None else str(k)          # 原名（可能带尾随空格）
        out[raw] = v
        t = raw.strip()
        if t and t not in out:
            out[t] = v                             # trim 名（匹配用）
    return out


# ── 注册表 / 流程定义表解析 ──────────────────────────────────────────

def _read_sheet(ws, headers_row=1, max_col=16):
    """读成 (表头list, 行列表, 每行在 Excel 的行号)。

    表头**去尾部空列**后返回（Excel 里表头右侧常有一串空单元格，
    直接用 max_col 当表头会把空名也算成列）。判"某列在不在"要用这份表头。
    """
    rows = list(ws.iter_rows(max_col=max_col, values_only=True))
    if not rows:
        return [], [], []
    hdrs = [_s(h) for h in rows[0]]
    while hdrs and not hdrs[-1]:
        hdrs.pop()
    out, linenos = [], []
    for i, r in enumerate(rows[1:], headers_row + 1):
        cells = list(r) + [None] * (max_col - len(r))
        if not any(x not in (None, '') for x in cells):
            continue
        out.append(dict(zip(hdrs, cells[:max_col])))
        linenos.append(i)
    return hdrs, out, linenos


def parse_registry(ws):
    """解析「验收流程」注册表 → [{sheet,enabled,cond_field,cond_val,scope,prio,note,line}]"""
    _hdr, rows, linenos = _read_sheet(ws, max_col=8)
    out = []
    for d, ln in zip(rows, linenos):
        name = _s(d.get('流程sheet名'))
        if not name or name == '流程sheet名':
            continue
        if not name.startswith(FLOW_PREFIX):
            raise FlowError(
                f'「{FLOW_REGISTRY_SHEET}」第 {ln} 行：流程sheet名「{name}」必须以「{FLOW_PREFIX}」开头')
        out.append({
            'sheet': name,
            # 「启用」只认 是 / 否（空视作否），避免 truthy 误判（参照 C13 的 or 陷阱）
            'enabled': _s(d.get('启用')) == '是',
            'cond_field': _s(d.get('条件字段')),
            'cond_val': _s(d.get('条件值')),
            'scope': _s(d.get('适用范围')) or '全部',
            'prio': _to_int(d.get('优先级'), 999),
            'note': _s(d.get('备注')),
            'line': ln,
        })
    # 同名 + 同优先级 + 同适用范围 = 真重复注册（doc/019 §五 坑 2）。
    # **不报错阻断上传** —— 重复只是让同一条流程被注册两次，match_flow 按优先级取第一条即可，
    # 报错会让这份 Excel 根本传不上线。改为 warning 提示管理层删掉演示行。
    seen = {}
    for r in out:
        k = (r['sheet'], r['prio'], r['scope'])
        if k in seen:
            r['warn'] = (f'注册表第 {r["line"]} 行与第 {seen[k]} 行重复注册同一流程'
                         f'（{r["sheet"]} / 优先级 {r["prio"]} / 适用范围 {r["scope"]}）。'
                         f'系统按优先级取第一条，功能不受影响，建议删掉其中一行')
        else:
            seen[k] = r['line']
    return out


def parse_flow_sheet(ws):
    """解析「流程-XXX」定义表 → [{no,step,type,factors,std_from,require,check,att,pass,seq,memo,controls,line}]"""
    hdrs, rows, linenos = _read_sheet(ws, max_col=len(FLOW_COLS))
    # 表头取自 _read_sheet 的第一份返回值：**不能在 read_only 模式下再 iter_rows 一次** ——
    # 工作簿流只能顺序读一次，二次读表头拿到空值 → 会误报"缺填报控件列"（2026-10-14 踩过）。
    ctrl_col_missing = FLOW_CTRL_COL not in hdrs
    miss_warn = (f'流程定义表「{ws.title}」缺少「{FLOW_CTRL_COL}」列'
                 f'（表头只有 {_s_list(FLOW_COLS[:-1])}），该流程的界面只能出附件按钮、'
                 f'没有任何填报控件') if ctrl_col_missing else ''
    out = []
    for d, ln in zip(rows, linenos):
        step = _s(d.get('验收环节'))
        if not step or step == '验收环节':
            continue
        out.append({
            'no': _to_int(d.get('序号'), len(out) + 1),
            'step': step,
            'type': _s(d.get('验收类型')),
            'factors': _s(d.get('核验因子')),
            'std_from': _s(d.get('标准值来源')),
            'require': _s(d.get('填报要求')),
            'check': _s(d.get('校验方式')),
            'att_raw': _s(d.get(FLOW_ATT_COL)),
            'pass': _s(d.get('通过标准')),
            'seq': _s(d.get('转序规则')),
            'memo': _s(d.get('备注')),
            'controls_raw': _s(d.get(FLOW_CTRL_COL)),
            'controls': parse_ctl(_s(d.get(FLOW_CTRL_COL))),
            'attachments': parse_att(_s(d.get(FLOW_ATT_COL))),
            'warn': miss_warn,
            'line': ln,
        })
    if not out:
        raise FlowError(f'流程定义表「{ws.title}」没有任何环节行')
    return out


def _s_list(xs):
    return '、'.join(_s(x) for x in xs if _s(x))


def parse_flows(wb):
    """扫描整个工作簿，产出 {'registry': [...], 'flows': {sheet: [环节...]}}。

    - 「验收流程」注册表**可选**（没有就返回空注册表，界面退回原网格填表）
    - 「流程-」开头的 sheet 都当作流程定义表，**允许多个**（这跟数据 sheet 的
      单 sheet 限制不同：流程本身就是多流程设计）
    """
    registry = []
    flows = {}
    names = list(wb.sheetnames)
    if FLOW_REGISTRY_SHEET in names:
        registry = parse_registry(wb[FLOW_REGISTRY_SHEET])
    for n in names:
        if n.startswith(FLOW_PREFIX):
            flows[n] = parse_flow_sheet(wb[n])
    for r in registry:
        if r['enabled'] and r['sheet'] not in flows:
            raise FlowError(
                f'「{FLOW_REGISTRY_SHEET}」第 {r["line"]} 行启用了流程「{r["sheet"]}」，'
                f'但工作簿里没有这个 sheet（现有：{sorted(flows)}）')
    return {'registry': registry, 'flows': flows}


# ── 流程匹配（B19）───────────────────────────────────────────────────

def match_flow(registry, flows, row, file_name=''):
    """该 data 行该走哪条流程。

    条件语法（注册表列）：
      条件字段 = `1` 或留空   → 无条件，全部小班执行
      条件字段 = 数据列名      → 该列值 trim 后等于条件值才执行
      多条件 `字段1;字段2`     → 与条件值一一对应，**全部满足**才执行

    优先级数字越小越先匹配，命中多条时取第一条（避免重复验收）。
    找不到返回 None（界面退回原网格填表）。
    """
    if not registry or not flows:
        return None
    std = std_lookup(row)
    best = None
    for r in registry:
        if not r['enabled']:
            continue
        scope = r['scope']
        if scope and scope != '全部' and file_name and scope != file_name:
            continue
        cf = r['cond_field']
        if cf in ('', '1'):
            ok = True
        else:
            fields = cf.split(';')
            vals = (r['cond_val'] or '').split(';')
            ok = all(_s(std.get(f)) == _s(vals[i] if i < len(vals) else '')
                     for i, f in enumerate(fields))
        if ok and (best is None or r['prio'] < best['prio']):
            best = r
    if not best:
        return None
    return {'sheet': best['sheet'], 'prio': best['prio'],
            'cond_field': best['cond_field'], 'cond_val': best['cond_val'],
            'scope': best['scope'], 'steps': flows[best['sheet']]}


def flow_warnings(reg, flows, rows, headers):
    """配置体检，把已知的坑显式告诉前端/后台，而不是等现场人员踩。

    返回 [{level, text}]，level ∈ {'warn','info'}。
    """
    out = []
    # 坑 3：条件字段=1 的无条件流程会让所有小班都走这条流程
    for r in reg:
        if r['enabled'] and r['cond_field'] in ('', '1'):
            out.append({
                'level': 'warn',
                'text': (f'注册表第 {r["line"]} 行「{r["sheet"]}」的条件字段是「{r["cond_field"] or "空"}」，'
                         f'即**无条件** —— 全部 {len(rows)} 个小班都会执行这条流程，'
                         f'包括其他项目类型的小班。建议改成「条件字段=项目类型」并填对应的条件值'),
            })
            break
    # 附件里出现 punch（打卡）但「功能」没开
    need_punch = any(a['kind'] == 'punch'
                     for steps in flows.values() for s in steps for a in s['attachments'])
    if need_punch:
        out.append({'level': 'info',
                    'text': '流程里用到「定位打卡」，需在「参数」页 功能 里加入 打卡，否则该按钮不可用'})
    # 流程定义表缺「填报控件」列 → 界面只能出附件按钮，没法填
    seen_missing = set()
    for sheet, steps in flows.items():
        for s in steps:
            if s.get('warn') and s['warn'] not in seen_missing:
                seen_missing.add(s['warn'])
                out.append({'level': 'error', 'text': s['warn']})
    # 验收结果选项缺 整改后合格
    for r in reg:
        if not r['enabled']:
            continue
        for s in flows.get(r['sheet'], []):
            for c in s['controls']:
                if c['name'] == '验收结果':
                    out.append({'level': 'info',
                                'text': '「验收结果」下拉选项来自「参数」页 验收结果选项；'
                                        '「因子控件对照」表里还有「整改后合格」和「整改期限」，两边需对齐'})
                    break
            break
        break
    return out