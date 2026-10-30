#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2 流程模板深度体检：结构 / 路由 / 控件 / 数据 / 说明页一致性。

与 tools/check_template.py 的分工：
    check_template.py  通用体检（参数页、表头、唯一键、脏值）——任何模板都跑
    check_flow_tpl.py  只查 v2 流程模板（设计值 + 流程路由 + 流程-* + 说明）——
                       跨表引用、路由连通、控件 DSL、设计值对照、说明页漂移

用法:
    python tools/check_flow_tpl.py                 # 默认检查 data/*.xlsx
    python tools/check_flow_tpl.py data/a.xlsx ... # 指定文件
    python tools/check_flow_tpl.py --quiet         # 只输出问题与合计

退出码 0 = 无 FATAL。只读，不修改文件。

三级结论:
    [FATAL] 上传/运行会坏，或模板内部自相矛盾 —— 必须修
    [WARN ] 不影响上传，但现场口径会出错 / 说明页与结构不符 —— 建议修
    [INFO ] 业务策略选择（如错误项是否强制备注、选项首项用原表口径）—— 需人确认
"""
import re
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import flow_tpl  # noqa: E402
from flow_parser import split_top  # noqa: E402
from param_parser import ParamError, parse_params  # noqa: E402

DATA_SHEET = flow_tpl.DESIGN_SHEET
ROUTE_SHEET = flow_tpl.ROUTE_SHEET
DOC_SHEET = flow_tpl.DOC_SHEET
PARAM_SHEET = '参数'
DEFAULT_KEY = '小班号'
CTL_TYPES = {'下拉', '多选', '数值', '单行文本', '多行纯文本', '自动'}


def _s(v):
    return '' if v is None else str(v).strip()


def _num(v):
    try:
        return float(_s(v))
    except (TypeError, ValueError):
        return None


def _read_headers_rows(ws):
    it = ws.iter_rows(values_only=True)
    first = next(it, None) or ()
    headers = [_s(x) for x in first]
    while headers and not headers[-1]:
        headers.pop()
    n = len(headers)
    rows = []
    for r in it:
        vals = list(r) + [None] * (n - len(r))
        if any(v not in (None, '') for v in vals[:n]):
            rows.append(vals[:n])
    return headers, rows


def _raw_option_names(raw):
    """从控件原文取选项名（保留重复，便于发现同名选项）。"""
    d = flow_tpl.parse_dsl(raw)
    if d['type'] not in ('选项', '多选', '按钮'):
        return []
    out = []
    for part in split_top(d['arg'], ';'):
        for sub in part.split('；'):
            sub = sub.strip()
            if not sub:
                continue
            out.append(sub.partition('→')[0].strip())
    return [x for x in out if x]


def _formula_missing(expr, names):
    """按 flow_hit._tpl_formula 的同一口径（精确 → 后缀 → 包含）解析公式变量。"""
    s = _s(expr).replace('×', '*').replace('÷', '/').replace('（', '(').replace('）', ')')
    bad = []
    for tok in dict.fromkeys(re.findall(r'[\u4e00-\u9fff]+', s)):
        if tok in names or any(n.endswith(tok) for n in names) \
                or any(tok in n for n in names):
            continue
        bad.append(tok)
    return bad


def _has_cycle(names, prereq):
    color = {}

    def dfs(n):
        color[n] = 1
        for p in prereq.get(n, []):
            if p not in names:
                continue
            if color.get(p) == 1:
                return True
            if color.get(p) is None and dfs(p):
                return True
        color[n] = 2
        return False

    return any(color.get(n) is None and dfs(n) for n in names)


def check_file(path: Path):
    F, W, I, info = [], [], [], {}

    if path.suffix.lower() not in ('.xlsx', '.xlsm'):
        return F, W + [f'只支持 .xlsx / .xlsm（跳过 {path.name}）'], I, info

    from openpyxl import load_workbook
    try:
        wb = load_workbook(path, data_only=True)
    except Exception as e:  # noqa: BLE001
        return F + [f'读取异常 {type(e).__name__}: {e}'], W, I, info

    if not flow_tpl.is_tpl_doc(wb):
        info['skip'] = True
        return F, W, I + [f'不是 v2 流程模板（无「{DATA_SHEET}」+「{ROUTE_SHEET}」）→ 跳过'], info

    info['sheets'] = wb.sheetnames
    headers, rows = _read_headers_rows(wb[DATA_SHEET])
    info['headers'] = headers
    info['rows'] = len(rows)

    # ── 表头 / 数据 ─────────────────────────────────────
    if not headers:
        F.append(f'数据 sheet「{DATA_SHEET}」缺少表头行')
    else:
        if any(not h for h in headers):
            F.append(f'「{DATA_SHEET}」存在空表头列：{headers}')
        dup = [h for h, n in Counter(headers).items() if h and n > 1]
        if dup:
            F.append(f'「{DATA_SHEET}」列名重复：{dup} —— 取值与匹配会错乱')
    if not rows:
        F.append(f'「{DATA_SHEET}」没有任何数据行')

    # ── 参数页 ─────────────────────────────────────────
    config = {}
    if PARAM_SHEET in wb.sheetnames:
        prows = [(i, r) for i, r in enumerate(
            wb[PARAM_SHEET].iter_rows(max_col=6, values_only=True), 1)
            if any(x not in (None, '') for x in r)]
        try:
            config = parse_params(prows, headers)
        except ParamError as e:
            F.append(str(e))
    else:
        W.append('缺少「参数」sheet —— 搜索栏 / 功能 / 唯一键全部回退缺省值')
    info['config'] = config

    for key in ('搜索选项', '导出筛选'):
        for item in [x.strip() for x in _s(config.get(key, '')).split(';') if x.strip()]:
            field = item.split('|', 1)[0].strip()
            if field and field not in headers:
                F.append(f'参数「{key}」引用了不存在的列「{field}」')

    key_col = config.get('unique-key') or DEFAULT_KEY
    info['key'] = key_col
    if key_col in headers:
        ki = headers.index(key_col)
        vals = [_s(r[ki]) for r in rows if r[ki] not in (None, '')]
        blanks = [n for n, r in enumerate(rows, 2) if r[ki] in (None, '')]
        dups = [v for v, n in Counter(vals).items() if n > 1]
        if blanks:
            F.append(f'唯一键「{key_col}」有 {len(blanks)} 行为空：行 {blanks[:6]}')
        if dups:
            F.append(f'唯一键「{key_col}」重复：{dups[:8]}')
        info['unique'] = len(set(vals))
    else:
        F.append(f'唯一键列「{key_col}」不在「{DATA_SHEET}」表头中')

    # ── 流程解析 ───────────────────────────────────────
    try:
        option_default = flow_tpl.option_default_of(config)
    except flow_tpl.TplError as e:
        F.append(str(e))
        option_default = False
        info['option_default'] = False
    else:
        info['option_default'] = option_default
    try:
        tpl = flow_tpl.parse(wb, headers, option_default)
    except flow_tpl.TplError as e:
        F.append(str(e))
        return F, W, I, info

    routes, flows = tpl['routes'], tpl['flows']
    info['routes'] = len(routes)
    info['flows'] = len(flows)
    for w in tpl['warn']:
        (F if w['level'] == 'error' else W).append(w['text'])

    # ── 项目类型覆盖（留空 = 公用） ─────────────────────
    wildcard = [r for r in routes if not _s(r['type'])]
    typed = [r for r in routes if _s(r['type'])]
    typed_types = sorted({_s(r['type']) for r in typed})
    if '项目类型' in headers:
        ti = headers.index('项目类型')
        raw_types = [_s(r[ti]) for r in rows]
        blanks = [n for n, v in enumerate(raw_types, 2) if not v]
        if blanks:
            F.append(f'数据「项目类型」有 {len(blanks)} 行为空：行 {blanks[:6]}'
                     f' —— 空类型只能走公用流程，专用流程打不开，请补全')
        data_types = {v for v in raw_types if v}
        probe = sorted(data_types | set(typed_types))
        effective = {t: wildcard + [r for r in typed if _s(r['type']) == t]
                     for t in probe}
        for t in sorted(data_types):
            rs = effective.get(t) or []
            if not rs:
                F.append(f'数据里的项目类型「{t}」既没有专用路由，也没有公用路由'
                         f' —— 这些小班会打不开任何流程')
            elif not any(not r['independent'] for r in rs):
                F.append(f'类型「{t}」只有公用独立流程（如巡护），没有主线流程'
                         f' —— 现场只能发起巡护')
        for t in sorted(set(typed_types) - data_types):
            W.append(f'「{ROUTE_SHEET}」的项目类型「{t}」在数据中未出现')
    else:
        data_types = set()
        effective = {'（全部）': list(routes)}
        if len(typed_types) > 1:
            F.append(f'数据没有「项目类型」列，但「{ROUTE_SHEET}」有 {len(typed_types)} 种专用类型'
                     f' —— 运行时无法区分；请把公用流程的项目类型留空，或只保留一种类型')
        elif typed_types:
            W.append('数据没有「项目类型」列 —— 专用路由按全量生效'
                     '（建议把流程的项目类型留空表示公用）')
        if effective['（全部）'] and not any(not r['independent'] for r in effective['（全部）']):
            W.append('路由里只有独立流程，没有主线流程')

    # ── 路由：按「公用 + 专用」有效集合查重复 / 引用 / 环 ──
    for t, rs in effective.items():
        names = {_s(r['flow']) for r in rs}
        dup = [x for x, n in Counter(_s(r['flow']) for r in rs).items() if n > 1]
        if dup:
            F.append(f'类型「{t}」同时用公用和专用路由重复配置了流程：{dup}'
                     f' —— 保留专用或删掉公用那一行')
        nos = [r['no'] for r in rs]
        if len(set(nos)) != len(nos):
            W.append(f'类型「{t}」的流程序号有重复：{nos}')
        prereq = {_s(r['flow']): [_s(p) for p in r['prereq']] for r in rs}
        for r in rs:
            for pre in r['prereq']:
                if _s(pre) not in names:
                    F.append(f'类型「{t}」流程「{r["flow"]}」的前置「{pre}」'
                             f'不在该类型的有效路由里（公用+专用）')
        if _has_cycle(names, prereq):
            F.append(f'类型「{t}」的前置流程存在环，流程永远无法解锁')
        heads = [_s(r['flow']) for r in rs if not r['independent'] and not r['prereq']]
        if len(heads) > 1:
            W.append(f'类型「{t}」有多个无前置主流程：{heads}（若为并行入口可忽略）')
    for r in routes:
        if r['independent'] and r['no'] != 0:
            W.append(f'独立流程「{r["flow"]}」序号为 {r["no"]}（约定 0，界面会排到主线之后）')

    # ── 单个流程 ───────────────────────────────────────
    std_numeric_cols = set()
    for sheet, steps in flows.items():
        if not steps:
            F.append(f'「{sheet}」没有任何环节行')
            continue
        if not any(r['flow'] == sheet for r in routes):
            W.append(f'流程「{sheet}」没有出现在「{ROUTE_SHEET}」—— 界面上不会出现')

        step_names = [_s(s['step']) for s in steps]
        dup_step = [x for x, n in Counter(step_names).items() if n > 1]
        if dup_step:
            F.append(f'「{sheet}」环节名重复：{dup_step} —— 填报值会互相覆盖')

        policy = Counter()
        for s in steps:
            for c in s['controls']:
                if c.get('verdict') or c.get('note_for') or c.get('type') != '下拉':
                    continue
                policy['首项通过' if c.get('first_pass') else '不判定'] += 1
        if policy:
            I.append(f'「{sheet}」选项判定：'
                     + ' / '.join(f'{k} {v} 项' for k, v in policy.items()))

        nos = [s['no'] for s in steps]
        if len(set(nos)) != len(nos):
            F.append(f'「{sheet}」序号重复：{nos}')
        else:
            missing = [x for x in range(min(nos), max(nos) + 1) if x not in nos]
            if missing:
                W.append(f'「{sheet}」序号断号：缺 {missing}（排序不受影响，建议补连续）')

        verdicts = [s for s in steps if s['verdict']]
        if len(verdicts) != 1:
            F.append(f'「{sheet}」应有且仅有 1 个「是否通过」结论行，实际 {len(verdicts)} 个')
        elif steps[-1] is not verdicts[0]:
            W.append(f'「{sheet}」结论行不在最后一行（约定最后一行固定为「是否通过」）')
        notes = [s for s in steps
                 if _s(s['step']) == '备注'
                 or any(c.get('hidden') and not c.get('note_for') for c in s['controls'])]
        if not notes:
            F.append(f'「{sheet}」没有隐藏备注行 —— 选「不通过」时没有理由可填')
        elif verdicts:
            vi = steps.index(verdicts[0])
            if vi > 0:
                prev = steps[vi - 1]
                prev_note = (_s(prev['step']) == '备注'
                             or any(c.get('hidden') and not c.get('note_for')
                                    for c in prev['controls']))
                if not prev_note:
                    W.append(f'「{sheet}」结论行前一行不是「备注」（约定固定相邻）')

        all_names = set(step_names)
        for s in steps:
            raw = _s(s.get('controls_raw'))
            if not raw:
                W.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」控件为空（按单行文本落地）')

            design = _s(s.get('std_from'))
            if design and not design.startswith(('常量：', '常量:')):
                if not any(c.get('std_col') for c in s['controls']):
                    W.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」设计值表头'
                             f'「{design}」不在「{DATA_SHEET}」列中')
            elif design:
                if any(c.get('design_const') and not _s(c.get('design_value'))
                       for c in s['controls']):
                    W.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」常量设计值为空')

            for c in s['controls']:
                if c.get('note_for') or c.get('verdict'):
                    continue
                t = c.get('type')
                if t not in CTL_TYPES:
                    F.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」控件类型「{t}」未知')
                    continue
                if t == '下拉':
                    opts = c.get('options') or []
                    if len(opts) < 2:
                        W.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」选项少于 2 个')
                    dup_opt = [x for x, n in Counter(_raw_option_names(raw)).items() if n > 1]
                    if dup_opt:
                        W.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」选项重复：{dup_opt}')
                    if opts and opts[0] != '正确' and c.get('first_pass'):
                        I.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」首项是'
                                 f'「{opts[0]}」，非「正确」（保留原表口径时可忽略）')
                    rules = flow_tpl.parse_dsl(raw)['rules']
                    wrong = [o for o in opts[1:] if '备注' not in rules.get(o, '')]
                    if wrong and c.get('first_pass'):
                        I.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」有 '
                                 f'{len(wrong)} 个错误项未要求备注（失败可不写原因）')
                elif t == '多选':
                    if len(c.get('options') or []) < 2:
                        W.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」多选选项少于 2 个')
                elif t == '数值':
                    if c.get('judge_ref') == '设计值' and not c.get('std_col'):
                        F.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」判断规则引用'
                                 f'「设计值」，但这一行没有可用的设计值表头 —— 提交「通过」会被拦下')
                    if c.get('std_col'):
                        std_numeric_cols.add(c['std_col'])
                elif t == '自动':
                    if not c.get('formula'):
                        F.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」自动控件没有公式')
                    else:
                        bad = _formula_missing(c['formula'], all_names)
                        if bad:
                            F.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」公式变量'
                                     f'{bad} 找不到对应环节 —— 运行时算不出值')

            atts = s.get('attachments') or []
            if _s(s.get('att_raw')) and not atts:
                W.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」附件写法解析不出条目：'
                         f'{_s(s["att_raw"])!r}')
            for a in atts:
                if a.get('warn'):
                    W.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」附件：{a["warn"]}')

    # ── 数值对照列必须是数字 ───────────────────────────
    if headers:
        for col in sorted(std_numeric_cols):
            if col not in headers:
                continue
            idx = headers.index(col)
            vals = [r[idx] for r in rows if r[idx] not in (None, '')]
            bad = [v for v in vals if _num(v) is None]
            if vals and len(bad) / len(vals) > 0.1:
                W.append(f'列「{col}」被数值控件用于对照，但有 {len(bad)}/{len(vals)} 个值不是数字'
                         f'（如 {bad[:3]}）—— 自动判断会失效')

    # ── 说明页漂移 ─────────────────────────────────────
    if DOC_SHEET in wb.sheetnames:
        doc = '\n'.join(' '.join('' if x is None else str(x) for x in r)
                        for r in wb[DOC_SHEET].iter_rows(values_only=True))
        for sheet in flows:
            short = sheet.replace('流程-', '')
            if sheet not in doc and short not in doc:
                W.append(f'「{DOC_SHEET}」页未提及流程「{sheet}」')

        att_total = sum(len(s.get('attachments') or []) for steps in flows.values() for s in steps)
        if att_total == 0 and re.search(r'(拍照|上传)\s*[:：]', doc) \
                and not any(x in doc for x in ('无附件', '未启用附件', '不要求拍照')):
            W.append(f'「{DOC_SHEET}」页按「有附件」描述，但所有流程的附件列都为空')

        multi = any(c.get('type') == '多选' for steps in flows.values()
                    for s in steps for c in s['controls'])
        if '可多选' in doc and not multi:
            W.append(f'「{DOC_SHEET}」页/备注声称可多选，但模板里没有多选控件')

        first_bad = [(sheet, s['line'], s['step'], _s((s['controls'][0].get('options') or [''])[0]))
                     for sheet, steps in flows.items() for s in steps
                     if s['controls'] and s['controls'][0].get('type') == '下拉'
                     and not s['controls'][0].get('verdict')
                     and (s['controls'][0].get('options') or [''])[0] != '正确']
        if first_bad and ('第一项固定为正向词「正确」' in doc or '正向统一为「正确」' in doc):
            W.append(f'「{DOC_SHEET}」页要求选项首项固定「正确」，实际有 {len(first_bad)} 行'
                     f'使用原表口径（如 {first_bad[0][0]}「{first_bad[0][2]}」）')

        split_rx = re.compile(r'拆成「.+?（[^）]*）\s*\+\s*(.+?)（[^）]*）」两行')
        for sheet, steps in flows.items():
            for s in steps:
                m = split_rx.search(_s(s.get('memo')))
                if not m:
                    continue
                target = m.group(1).strip()
                if not any(target in _s(x['step']) for x in steps):
                    W.append(f'「{sheet}」第 {s["line"]} 行「{s["step"]}」备注说拆成两行，'
                             f'但找不到「{target}」行')

    wb.close()
    info['doc'] = DOC_SHEET in wb.sheetnames
    return F, W, I, info


def main(argv):
    quiet = '--quiet' in argv
    args = [a for a in argv[1:] if not a.startswith('--')]
    if args:
        files = [Path(a) for a in args]
    else:
        files = sorted(Path(BASE, 'data').glob('*.xlsx'))
        # Excel/WPS 打开文件时会留 .~xxx / ~$xxx 锁文件，不是 zip，直接跳过
        files = [f for f in files if not f.name.startswith(('.~', '~$'))]
    if not files:
        print('没有找到 .xlsx 模板（data/*.xlsx）')
        return 2

    total_f = total_w = 0
    for p in files:
        if not quiet:
            print('=' * 76)
        print(f'▶ {p.name}')
        try:
            F, W, I, info = check_file(p)
        except Exception as e:  # noqa: BLE001
            print(f'  [FATAL  ] 体检异常 {type(e).__name__}: {e}')
            total_f += 1
            continue

        if info.get('rows') is not None:
            print(f'  sheets={len(info.get("sheets") or [])}  数据={info.get("rows")} 行'
                  f'  唯一键={info.get("key")!r}  流程={info.get("flows")}'
                  f'  路由={info.get("routes")}'
                  f'  选项判定={"首项通过" if info.get("option_default") else "不判定"}')
        for m in F:
            print(f'  [FATAL  ] {m}')
        for m in W:
            print(f'  [WARN   ] {m}')
        for m in I:
            print(f'  [INFO   ] {m}')
        if info.get('skip'):
            print('  —— 已跳过（不是 v2 流程模板）')
        elif not (F or W or I):
            print('  ✅ 全部通过')
        else:
            print(f'  —— {len(F)} FATAL / {len(W)} WARN / {len(I)} INFO')
        total_f += len(F)
        total_w += len(W)

    if not quiet:
        print('=' * 76)
    print(f'合计 {total_f} FATAL / {total_w} WARN —— '
          + ('模板可用' if total_f == 0 else '修完再上传'))
    return 1 if total_f else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
