# -*- coding: utf-8 -*-
"""flow_hit —— 流程命中与环节判定（服务端权威口径）。

从 main.py 拆出的 B19/B29/B30/B31 积木：入口分派、标准值挂载、v2 路由状态、
公式求值、规则/唯一性校验、附件闸门。除 `_tpl_hit` 需要读 `flow_records` 外都是纯函数；
数据库连接由 main 在启动时注入（`set_db`），保证测试改 `main.DB_PATH` 仍然生效。
"""
import ast
import json
import operator as _op
import re
from datetime import datetime

import flow_config
import flow_parser
import flow_tpl
from param_parser import _s

_db = None


def set_db(fn):
    """注入数据库连接工厂（main.db），避免本模块反向 import main 造成循环依赖。"""
    global _db
    _db = fn


def _entry_of(sheet):
    """流程 sheet 名 → 入口（`流程-人工造林·验收` → `验收`）。

    老 12 列格式没有入口后缀 → 返回 ''（前端因此不显示入口 tab，行为不变）。
    """
    return sheet.rsplit('·', 1)[-1] if '·' in sheet else ''


def _entries_of(flows, headers, rows, ridx, file_name=''):
    """该行能进哪几个入口（验收 / 巡视 / 材料…）。

    **为什么需要这一层**：验收和巡视都挂「小班档案」、条件都是「项目类型」，
    match_flow 只会返回一条，另一个入口就永远进不去。业务上的语义是
    "同一个小班既可以做验收、也可以做巡视" —— 那就必须由人（或界面 tab）
    指定走哪个入口，而不是让匹配器替他选。
    """
    reg = (flows or {}).get('registry') or []
    fmap = (flows or {}).get('flows') or {}
    # v2 模板没有「入口 tab」：所有流程按路由顺序进同一个下拉框，由前端渲染 select
    if (flows or {}).get('schema') == 'flow_tpl/1':
        return []
    if not reg:
        return []
    if ridx is not None and ridx < 0:
        # v0.37：项目级（材料检验按批次，不挂小班档案）——与行无关，
        # 只列「独立入口」挂载的流程（注册条件写死 独立入口=独立入口 的那批）。
        out = []
        for r in reg:
            en = _entry_of(r['sheet'])
            if en and r.get('cond_field') == '独立入口':
                out.append({'entry': en, 'mount': r.get('mount') or '独立入口',
                            'sheet': r['sheet'], 'prio': r['prio'],
                            'steps': len(fmap.get(r['sheet']) or [])})
        return out
    if ridx is None or ridx < 0 or ridx >= len(rows):
        return []
    row = dict(zip(headers, rows[ridx]))
    order, seen = [], set()
    for r in reg:
        en = _entry_of(r['sheet'])
        if en and en not in seen:
            seen.add(en)
            order.append(en)
    out = []
    for en in order:
        sub = [x for x in reg if _entry_of(x['sheet']) == en]
        m = flow_parser.match_flow(sub, fmap, row, file_name)
        if m:
            mount = next((x.get('mount') or '' for x in sub if x['sheet'] == m['sheet']), '')
            out.append({'entry': en, 'mount': mount or '小班档案', 'sheet': m['sheet'],
                        'prio': m['prio'], 'steps': len(m['steps'] or [])})
    return out


def _project_std(headers, rows):
    """项目级常量列：全部数据行 trim 后取值都相同的列（项目名称/标段/实施年度…）。

    v0.37 材料检验（按批次）不挂小班档案、一条记录关联多个小班 ——
    基本信息若给随小班变化的列（造林树种/总株数…）会误导（一条批次记录
    该显示哪个小班的值？），只给项目级常量；小班差异由「关联小班」多选承担。
    全空列不算常量（给了也是「—」，不如不给）。
    """
    cols, vals = [], {}
    for i, h in enumerate(headers):
        name = str(h or '').strip()
        if not name:
            continue
        seen = set()
        for r in rows:
            v = _s(r[i]) if i < len(r) else ''
            seen.add(v)
            if len(seen) > 1:
                break
        if len(seen) == 1 and '' not in seen:
            cols.append(name)
            vals[name] = seen.pop()
    return cols, vals


def _hit_payload(m, std, std_cols, warns):
    """流程命中 → 前端要的全部内容（环节定义 + 标准值 + 警告）。行级/项目级共用。"""
    steps = []
    for s in m['steps']:
        steps.append({
            'no': s['no'], 'step': s['step'], 'type': s['type'],
            'factors': s['factors'], 'std_from': s['std_from'],
            'require': s['require'], 'check': s['check'], 'pass': s['pass'],
            'seq': s['seq'], 'memo': s['memo'],
            'controls': s['controls'], 'attachments': s['attachments'],
            # 原样带出两列原文（附件要求 / 填报控件），界面上要显示"Excel 原文是这么写的"，
            # 现场人员填错时能一眼看出配置问题在哪
            'att_raw': s['att_raw'], 'controls_raw': s['controls_raw'],
            # v0.35 业务专家填写版新增维度（老格式没有这些键 → 前端按缺省值走，行为不变）
            'expert': bool(s.get('expert')),
            'subject': s.get('subject', '小班'),
            'multi': bool(s.get('multi')),
            'link': s.get('link', '不需要'),
            'basic_info': s.get('basic_info') or [],
            'basic_hide': s.get('basic_hide') or [],
            'verdict': s.get('verdict', ''),
            'question': s.get('question', ''),
            'note_required': bool(s.get('note_required')),
            'note_field': s.get('note_field', ''),
            'note_no': s.get('note_no', 0),
            'unique': s.get('unique', '不校验'),
            'prev': s.get('prev') or [],
            'prev_result': s.get('prev_result') or {},
            'need_result': s.get('need_result', True),
        })
    # v0.35：核验项声明了「标准值来源」时，把 data 该列的值挂到**这一格的名字**下，
    # 前端就能显示"设计值 X / 实测 Y"并自动判定（不要求列名与字段名同名）
    for s in steps:
        for c in s['controls']:
            col = c.get('std_col') or ''
            if not col:
                continue
            v = std.get(col)
            if v not in (None, ''):
                std[c['name']] = v
    return {'sheet': m['sheet'], 'prio': m['prio'], 'entry': _entry_of(m['sheet']),
            'cond_field': m['cond_field'], 'cond_val': m['cond_val'],
            'scope': m['scope'], 'steps': steps, 'std': std, 'warn': warns,
            # data 表的真实列名（原样，可能带尾随空格）。
            # **必须单独给一份**：std 里混着"控件名 → 设计值"的映射（整地数量=627 这种），
            # 前端拿 Object.keys(std) 当"data 全部列"会多出几个假列，黑名单也就剔不干净
            # （2026-10-21 截图自测：33 键里 4 个是映射，29-8 被算成 33-8=25）。
            'std_cols': [str(h) for h in std_cols if str(h or '').strip()]}


def _project_hit(reg, fmap, headers, rows, file_name, warns):
    """项目级入口（v0.37：材料检验按批次）—— row=-1 时按入口认领，不走行条件匹配。

    材料流程的注册条件写死「独立入口=独立入口」（flow_config 第 470 行），
    任何行都匹配不到它 —— 否则每个小班都会跑一遍材料流程。项目级在这里显式认领：
    同入口内取优先级最高、已启用、范围相符、条件为无条件或独立入口的那条。
    """
    cand = [r for r in reg if r['enabled']
            and (not r['scope'] or r['scope'] == '全部' or r['scope'] == file_name)
            and r['cond_field'] in ('', '1', '独立入口')]
    if not cand:
        return None
    best = min(cand, key=lambda r: r['prio'])
    steps = fmap.get(best['sheet'])
    if not steps:
        return None
    m = {'sheet': best['sheet'], 'prio': best['prio'],
         'cond_field': best['cond_field'], 'cond_val': best['cond_val'],
         'scope': best['scope'], 'steps': steps}
    cols, std = _project_std(headers, rows)
    return _hit_payload(m, std, cols, warns)


def _tpl_row_type(headers, rows, ridx):
    """当前小班的项目类型；数据表没有这一列 → None（路由不按类型过滤）。"""
    if ridx is None or ridx < 0 or ridx >= len(rows):
        return ''
    h = next((x for x in headers if _s(x) == '项目类型'), None)
    if h is None:
        return None
    return _s(dict(zip(headers, rows[ridx])).get(h))


def _tpl_flow_info(con, wid, ridx, flow, steps):
    """一条流程在某个小班上的完成状态 + 最近一次的值（v2 模板）。

    一次提交会把流程内所有环节写成同一个 rec_no（批次），所以取最大 rec_no
    就是"最近一次填报"。完成 = 最近一次里每个环节的结论都在 PASS_RESULTS。
    """
    rows = con.execute(
        'SELECT step_no,rec_no,factor,value,result,operator,updated_at,att_json'
        ' FROM flow_records WHERE workbook_id=? AND row_idx=? AND flow_sheet=?'
        ' ORDER BY rec_no, id', (wid, ridx, flow)).fetchall() if con is not None else []
    latest = max([int(x['rec_no'] or 0) for x in rows], default=0) if rows else 0
    values, meta, atts = {}, {}, {}
    for x in rows:
        rn = int(x['rec_no'] or 0)
        if rn != latest:
            continue
        sn = str(int(x['step_no']))
        if x['factor']:
            values.setdefault(sn, {})[x['factor']] = _decode_value(x['value'])
        else:
            meta[sn] = {'result': x['result'] or '', 'operator': x['operator'] or '',
                        'updated_at': x['updated_at'] or ''}
            atts[sn] = json.loads(x['att_json'] or '[]')
    done = bool(steps) and all(
        (meta.get(str(int(s['no']))) or {}).get('result') in flow_config.PASS_RESULTS
        for s in steps
        # v2：一条流程只有一个「是否通过」结论环节，其余环节没有独立结论；
        # 只按结论环节判完成，否则永远 done=False，流程永远不往下走
        if s.get('verdict'))
    return {'has': bool(rows), 'done': done, 'values': values, 'meta': meta,
            'atts': atts, 'rec_no': latest}


def _decode_value(v):
    """flow_records 里多选/勾选存 JSON 字符串；取回时按首字符判断，不硬 parse。"""
    if isinstance(v, str) and v[:1] in ('[', '{'):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return v
    return v


def _geo_obj(s):
    try:
        g = json.loads(s or '{}')
    except (TypeError, ValueError):
        return {}
    return g if isinstance(g, dict) else {}


# ── 流程附件（v0.42）：上传件只认魔数，不认扩展名 / 客户端 mimetype ──
_ATT_MAX = 10 * 1024 * 1024     # 10MB：扫描件够用，也压住磁盘占用
_ATT_SIGS = (
    ('application/pdf', '.pdf', 'pdf', lambda b: b[:5] == b'%PDF-'),
    ('image/jpeg', '.jpg', 'image', lambda b: b[:3] == b'\xff\xd8\xff'),
    ('image/png', '.png', 'image', lambda b: b[:8] == b'\x89PNG\r\n\x1a\n'),
    ('image/webp', '.webp', 'image',
     lambda b: b[:4] == b'RIFF' and b[8:12] == b'WEBP'),
)
_ATT_CLS = {'image': '图片', 'pdf': 'PDF 文件'}


def _att_sig(data):
    """文件头 → (mime, ext, cls)；不认识返回 None。"""
    for mime, ext, cls, hit in _ATT_SIGS:
        try:
            if hit(data):
                return mime, ext, cls
        except (TypeError, IndexError):
            continue
    return None


def _att_name(raw):
    """上传文件名只留 basename 并去危险字符；空则给个占位。"""
    name = re.sub(r'.*[\\/]', '', str(raw or '')).strip()
    name = re.sub(r'[\\/:*?"<>|\r\n]+', '_', name)[:120]
    return name or 'attachment'


def _att_gate(con, wid, ridx, flow, steps, att_by_step, rec_no):
    """按 flow_att 里的**实际文件**校验附件下限 / 类型下限。

    客户端报的 count 不作数（只有拍照/打卡这种不上传的才认 count）：
    上传件必须能按 id 找到本工作簿、本行、本流程、本环节的行，且未绑定到别的 rec_no。
    返回 (missing, bind_ids)。
    """
    missing, bind = [], []
    for s in steps:
        no = int(s['no'])
        entries = att_by_step.get(no) or []
        for a in s.get('attachments') or []:
            need = int(a.get('min') or 0)
            types = a.get('types') or {}
            if not need and not types:
                continue
            label = a.get('label') or ''
            if a.get('kind') != 'upload':
                got = sum(int(e.get('count') or 0) for e in entries
                          if e.get('kind') == a['kind'] and e.get('label') == label)
                if got < need:
                    missing.append(f'附件「{label}」需 {need} 份，现 {got} 份')
                continue
            ids = []
            for e in entries:
                if e.get('kind') != 'upload' or e.get('label') != label:
                    continue
                for x in (e.get('ids') or []):
                    try:
                        ids.append(int(x))
                    except (TypeError, ValueError):
                        pass
            ids = list(dict.fromkeys(ids))
            rows = []
            if ids:
                q = ','.join('?' * len(ids))
                rows = con.execute(
                    f'SELECT id,mime,rec_no FROM flow_att WHERE id IN ({q})'
                    ' AND workbook_id=? AND row_idx=? AND flow_sheet=?'
                    ' AND step_no=? AND label=?',
                    (*ids, wid, ridx, flow, no, label)).fetchall()
            bad = [x['id'] for x in rows
                   if int(x['rec_no'] or 0) not in (0, int(rec_no))]
            if bad or len(rows) != len(ids):
                missing.append(f'附件「{label}」引用的文件无效或已属于别的记录，请重新上传')
                continue
            got = len(rows)
            if got < need:
                missing.append(f'附件「{label}」需 {need} 份，现 {got} 份')
            for cls, n in types.items():
                cn = sum(1 for x in rows if _att_sig_cls(x['mime']) == cls)
                if cn < int(n or 0):
                    missing.append(f'附件「{label}」{_ATT_CLS.get(cls, cls)}'
                                   f'需 ≥{n} 份，现 {cn} 份')
            bind.extend(x['id'] for x in rows)
    return missing, bind


def _att_sig_cls(mime):
    return 'pdf' if str(mime or '') == 'application/pdf' else 'image'


def _flow_log_from_records(recs):
    """v0.40 之前的提交没有 flow_audit 行：从 factor='' 的环节留痕反推日志。

    一次批量提交里每个环节各有一条 factor='' 记录（同 rec_no / 结论 / 定位），
    按 rec_no 归并成一条；rec_no=0 的非重复流程就是最近一次。
    """
    seen = {}
    for x in recs:
        if x['factor']:
            continue
        rn = int(x['rec_no'] or 0)
        e = seen.get(rn)
        if not e:
            e = seen[rn] = {'rec_no': rn, 'result': '', 'operator': '', 'ip': '',
                            'geo': {}, 'steps': 0, 'at': ''}
        e['steps'] += 1
        e['result'] = x['result'] or e['result']
        e['operator'] = x['operator'] or e['operator']
        e['ip'] = x['ip'] or e['ip']
        e['geo'] = _geo_obj(x['geo']) or e['geo']
        e['at'] = x['updated_at'] or x['created_at'] or e['at']
    return sorted(seen.values(), key=lambda e: (e['rec_no'], e['at']),
                  reverse=True)[:100]


def _tpl_route_status(con, wid, ridx, flows, routes):
    """每条流程的 done / has / available（available = 前置流程全部完成）。"""
    out = {}
    for r in routes:
        info = _tpl_flow_info(con, wid, ridx, r['flow'], flow_tpl.steps_of(flows, r['flow']))
        out[r['flow']] = {
            'sheet': r['flow'], 'no': r['no'], 'prereq': r['prereq'],
            'repeat': r['repeat'], 'trigger': r['trigger'], 'note': r['note'],
            'independent': bool(r.get('independent')),
            'done': info['done'], 'has': info['has'], 'available': True,
            '_values': info['values'], '_meta': info['meta'],
            '_atts': info['atts'], '_rec_no': info['rec_no'],
        }
    for r in routes:
        cur = out[r['flow']]
        # 独立流程没有前置，永远可发起；也不能被别人的前置引用（模板解析已保证不串线）
        cur['available'] = (True if r.get('independent')
                            else all(out.get(p, {}).get('done') for p in r['prereq']))
    return out


def _tpl_default_route(routes, status):
    """按路由顺序找"当前该做的那条"：前置完成、自己还没完成；可重复流程始终可选。

    独立流程（巡护）不参与默认选择；主线全部完成后落到最后一条主线，不会跳到巡护。
    """
    main = [r for r in routes if not r.get('independent')] or routes
    for r in main:
        st = status.get(r['flow']) or {}
        if not st.get('available'):
            continue
        if r['repeat'] or not st.get('done'):
            return r
    return main[-1] if main else None


_ARITH = {ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul,
          ast.Div: _op.truediv, ast.USub: _op.neg, ast.UAdd: _op.pos}


def _safe_arith(expr):
    """只允许 + - * / 与括号的数字表达式（不 eval，模板内容不可信）。"""
    def walk(n):
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _ARITH:
            return _ARITH[type(n.op)](walk(n.left), walk(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _ARITH:
            return _ARITH[type(n.op)](walk(n.operand))
        raise ValueError('不支持的公式')
    return walk(ast.parse(expr, mode='eval'))


def _tpl_formula(expr, values):
    """v2「自动(公式)」求值 → 数字；变量名解析不出来 / 值不是数字 → None。

    模板里的变量写的是短名（公式 `成活株数/(成活株数+死亡株数)×100%`，
    环节却叫「样地调查成活株数」），所以先精确匹配，再按**后缀唯一匹配**。
    末尾的 `%` 是"按百分数展示"的单位，去掉后 ×100 才是 0-100 的数值。
    """
    s = _s(expr)
    if not s:
        return None
    s = (s.replace('×', '*').replace('÷', '/')
          .replace('（', '(').replace('）', ')').replace('，', ','))
    s = re.sub(r'%\s*$', '', s)
    nums = {}
    for tok in re.findall(r'[\u4e00-\u9fff]+', s):
        if tok in nums:
            continue
        cand = [k for k in values if _s(k) == tok]
        if not cand:
            cand = ([k for k in values if _s(k).endswith(tok)]
                    or [k for k in values if tok in _s(k)])
        if not cand:
            return None
        try:
            nums[tok] = float(_s(values.get(cand[0])))
        except (TypeError, ValueError):
            return None
    for tok in sorted(nums, key=len, reverse=True):
        s = s.replace(tok, ('%.6f' % nums[tok]).rstrip('0').rstrip('.'))
    try:
        return round(_safe_arith(s), 2)
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError):
        return None


def _tpl_hit(flows, headers, rows, ridx, flow_sheet='', wid=None):
    """v2 模板：某小班的一条流程（含路由状态、设计值、备注行引用）。"""
    if ridx is None or ridx < 0 or ridx >= len(rows):
        return None
    ptype = _tpl_row_type(headers, rows, ridx)
    routes = flow_tpl.routes_for(flows, ptype)
    if not routes:
        return None
    con = _db() if wid is not None else None
    try:
        status = _tpl_route_status(con, wid, ridx, flows, routes)
    finally:
        if con is not None:
            con.close()
    chosen = next((r for r in routes if r['flow'] == flow_sheet), None)
    if flow_sheet and not chosen:
        return None
    if not chosen:
        chosen = _tpl_default_route(routes, status)
    if not chosen:
        return None
    steps = [dict(s, multi=bool(chosen['repeat']))
             for s in flow_tpl.steps_of(flows, chosen['flow'])]
    if not steps:
        return None
    row = dict(zip(headers, rows[ridx]))
    m = {'sheet': chosen['flow'], 'prio': chosen['no'],
         'cond_field': '项目类型', 'cond_val': ptype or '', 'scope': '全部',
         'steps': steps}
    payload = _hit_payload(m, flow_parser.std_lookup(row), headers,
                           list(flows.get('warn') or []))
    # 常量设计值（常量：1.5kg/株）没有 data 列，_hit_payload 拿不到，这里补上
    for s in payload['steps']:
        for c in s['controls']:
            if c.get('design_const') and c.get('design_value'):
                payload['std'][c['name']] = c['design_value']
    note_no, note_field = 0, ''
    for s in steps:
        if s.get('note_no'):
            note_no, note_field = s['note_no'], s.get('note_field') or '备注'
            break
    cur = status[chosen['flow']]
    payload.update({
        'schema': 'flow_tpl/1',
        'entry': '',
        'flow_sheet': chosen['flow'],
        # 下拉列表只带路由信息；values/meta 留在本题流程上，避免每个选项都背一份填报数据
        'flow_list': [{k: v for k, v in status[r['flow']].items()
                       if not k.startswith('_')} for r in routes],
        'flow_locked': not cur['available'],
        'flow_prereq': chosen['prereq'],
        'flow_repeat': bool(chosen['repeat']),
        'flow_independent': bool(chosen.get('independent')),
        'flow_trigger': chosen['trigger'],
        'values': cur['_values'],
        'meta': cur['_meta'],
        'atts': cur['_atts'],
        'rec_no': cur['_rec_no'],
        'note_step_no': note_no,
        'note_field': note_field,
    })
    return payload


def _flow_hit(flows, headers, rows, ridx, file_name='', entry='', flow_sheet='', wid=None):
    """该行/该项目命中的流程（含各环节 + 每个因子的标准值），未命中返回 None。

    `entry` 给定（验收/巡视/材料）时只在带该入口后缀的流程里匹配；
    留空 = 老行为（全量匹配，取优先级最高那条）。
    **ridx < 0 = 项目级**（v0.37 材料检验按批次，不挂小班档案）：
    不走行条件匹配，按入口认领「独立入口」流程，标准值只给项目级常量列。
    """
    # v0.38：v2 模板（设计值 / 流程路由 / 流程-XXX）是另一套 schema，先分流
    if (flows or {}).get('schema') == 'flow_tpl/1':
        return _tpl_hit(flows, headers, rows, ridx, flow_sheet, wid)
    if not flows or not flows.get('registry') or not flows.get('flows'):
        return None
    reg, fmap = flows['registry'], flows['flows']
    if entry:
        reg = [r for r in reg if _entry_of(r['sheet']) == entry]
        fmap = {k: v for k, v in fmap.items() if _entry_of(k) == entry}
        if not reg:
            return None
    warns = list(flows.get('warn') or [])
    if ridx is not None and ridx < 0:
        return _project_hit(reg, fmap, headers, rows, file_name, warns)
    if ridx is None or ridx < 0 or ridx >= len(rows):
        return None
    row = dict(zip(headers, rows[ridx]))
    m = flow_parser.match_flow(reg, fmap, row, file_name)
    if not m:
        return None
    # 标准值随环节带出（trim 后取，「总株数  」这类尾随空格表头也能取到）
    return _hit_payload(m, flow_parser.std_lookup(row), headers, warns)


def _rule_errors(step, values):
    """按 ③「校验规则」逐项检查（服务端权威，C13）。空值不归规则管，交给必填兜。"""
    errs = []
    for c in step['controls']:
        rule = (c.get('rule') or '').strip()
        if not rule:
            continue
        if c['type'] == '数值×3':
            for k in (c.get('sub') or []):
                e = flow_config.check_rule(rule, values.get(k), k)
                if e:
                    errs.append(e)
            continue
        e = flow_config.check_rule(rule, values.get(c['name']), c['name'])
        if e:
            errs.append(e)
    return errs


def _unique_conflict(con, wid, ridx, sheet, step, values, rec_no):
    """环节声明了「唯一性校验」时查重。返回一句冲突说明，没冲突返回 ''。"""
    mode = step.get('unique') or '不校验'
    if mode == '不校验':
        return ''
    if mode == '同小班同日唯一':
        # 多次环节里同一个小班同一天只允许一条（防现场重复刷记录）。
        # 日期控件的名字由业务专家定（巡护日期 / 记录日期 / 验收日期…），
        # **不能写死「记录日期」**——巡视环节叫「巡护日期」，写死就永远读不到值、
        # 校验退化成"今天"，等于没校验（2026-10-21 自查发现）。
        dayc = next((c['name'] for c in step['controls'] if c['type'] == '日期'), '记录日期')
        day = _s(values.get(dayc))[:10] or datetime.now().strftime('%Y-%m-%d')
        rows = con.execute(
            'SELECT rec_no,factor,value FROM flow_records WHERE workbook_id=? AND row_idx=?'
            ' AND flow_sheet=? AND step_no=? AND rec_no>0 AND rec_no<>?',
            (wid, ridx, sheet, int(step['no']), rec_no)).fetchall()
        for x in rows:
            if x['factor'] == dayc and _s(x['value'])[:10] == day:
                return ('本小班 %s 已有「%s」的记录（第 %s 条），同一天不能重复填'
                        % (day, step['step'], x['rec_no']))
        return ''
    if mode == '同批次号唯一':
        # 同理：批次字段名也是业务专家起的（进场批次号 / 材料批次号…），
        # 取「第一个名字里带批次的控件」，不能写死成「批次号」。
        bc = next((c['name'] for c in step['controls'] if '批次' in c['name']), '批次号')
        batch = _s(values.get(bc))
        if not batch:
            return ''
        rows = con.execute(
            'SELECT workbook_id,row_idx,rec_no,value FROM flow_records'
            ' WHERE flow_sheet=? AND step_no=? AND factor=? AND rec_no>0',
            (sheet, int(step['no']), bc)).fetchall()
        for x in rows:
            if _s(x['value']) != batch:
                continue
            if (x['workbook_id'] == wid and x['row_idx'] == ridx
                    and int(x['rec_no'] or 0) == rec_no):
                continue                       # 是同一条记录在改自己
            return '批次号「%s」已被其他记录使用，同一批次只能登记一次' % batch
        return ''
    return ''
