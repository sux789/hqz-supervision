#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人工造林验收模板：源表解析 / 模板生成 / 模板回读。

源表用「字体颜色」区分列：
  蓝色 = 身份键（小班号 / 乡镇 / 村 / 标段）
  绿色 = 设计值（面积 / 密度 / 株行距 / …）
  其他 = 填报或核验项（抽成流程表的「环节」）

生成物：
  设计值   一行 = 一个小班；A 列「项目类型」默认「人工造乔木林」
  流程路由 一行 = 一条路由；列 = 序号 / 项目类型 / 流程 / 前置流程 / 可重复 / 触发方式 / 说明
  流程-*   一行 = 一个环节；列 = 序号 / 环节 / 设计值表头 / 控件 / 附件 / 备注
  说明     列语义 + 控件 DSL + 解析约定

流程表是「定义表」不是数据表：第 1 行表头，第 2 行起每行定义一个环节。
流程路由决定同一项目类型下流程的先后（前置流程）、并行与重复：
  主线  整地 → 回塘底肥 → 栽植撑杆覆膜 → 完工验收（每步一次）
  并行  日常巡护（栽植后发起，定期，可重复）
控件用统一 DSL，例如：
  选项(正确;不正确)    第一项为默认，Excel / App 都允许手填实际值
  数值(株)             非负数，自动与「设计值」列对照
  数值(株→≥设计值)     通过判断：实测 ≥ 设计值 → 正确；也可 ≥85 / ≥设计值×0.95
  百分数(0-100)
  多选(甲;乙;丙)
  文本 / 长文本(隐藏)
  自动(成活株数/(成活株数+死亡株数)×100%)

用法：
  python3 tools/tpl_tool.py build
  python3 tools/tpl_tool.py build --src <源表.xlsx> --out <模板.xlsx>
  python3 tools/tpl_tool.py show --file <模板.xlsx>
"""

import argparse
import math
import os
import re
import sys
from collections import Counter

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.pagebreak import Break

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import flow_judge  # noqa: E402  （模板校验与运行时共用同一套规则语法）

# ---------- 默认路径 ----------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TPL_DEFAULT = os.path.join(REPO_ROOT, 'data', 'tpl-人工造林-验收流程模板.xlsx')
# 源表（5 个 sheet 的原始台账）已移出仓库；build 只在显式 --src 时使用。
# 日常改模板配置请用 `params`（只补/同步参数页，不动业务数据）。
SRC_DEFAULT = ''
OUT_DEFAULT = TPL_DEFAULT
PARAM_SHEET = '参数'

PROJECT_TYPE = '人工造乔木林'
BLUE = 'FF0070C0'
GREEN = 'FF00B050'
FLOW_COLS = ['序号', '环节', '设计值表头', '控件', '附件', '备注']
ROUTE_COLS = ['序号', '项目类型', '流程', '前置流程', '可重复', '触发方式', '说明']
REPEAT_VALUES = ('是', '否')
TRIGGER_VALUES = ('按序', '按需', '定期')

# 流程名关键字 → (前置流程关键字, 可重复, 触发方式, 说明)。
# 未命中的新增流程默认串到前一流程之后，可在「流程路由」里直接改。
ROUTE_SPEC = {
    '整地': ('', '否', '按序', '主线第 1 步，无前置；通过后进入回塘底肥。'),
    '回塘': ('整地', '否', '按序', '前置为整地；整地验收通过后才能开始。'),
    '栽植': ('回塘', '否', '按序', '前置为回塘底肥；回塘验收通过后才能开始。'),
    '完工': ('栽植', '否', '按序', '主线最后一步，仅一次；通过后项目归档。'),
    # 独立流程：无前置、可重复，序号 0 让程序把它排除在主线默认选择之外
    '巡护': ('', '是', '定期', '独立流程：不参与主线，随时可发起，可多次，不影响任何前后关系。'),
}

# 源表列名 → (角色, 规范名)。规范名 = 新模板表头。
CANON = {
    '小班号': ('id', '小班号'),
    '唯一小班号1': ('id', '小班号'),
    '乡（镇）': ('id', '乡（镇）'),
    '乡镇2': ('id', '乡（镇）'),
    '村（社区）': ('id', '村（社区）'),
    '村2': ('id', '村（社区）'),
    '标段': ('id', '标段'),
    '设计面积': ('design', '设计面积（亩）'),
    '小班面积': ('design', '设计面积（亩）'),
    '设计密度': ('design', '设计密度（株/亩）'),
    '设计株行距': ('design', '设计株行距（米）'),
    '整地方式': ('design', '整地方式'),
    '设计整地规格': ('design', '设计整地规格（cm）'),
    '设计总株数': ('design', '设计总株数（株）'),
    '栽植总株数': ('design', '设计总株数（株）'),
    '造林树种': ('design', '造林树种'),
    '混交方式': ('design', '混交方式'),
    '混交比例': ('design', '混交比例'),
    '树种1': ('design', '树种1株数（株）'),
    '树种2': ('design', '树种2株数（株）'),
    '撑杆总数量': ('design', '撑杆总数量'),
    '覆膜总数量': ('design', '覆膜总数量'),
}

ID_PREF = ['小班号', '乡（镇）', '村（社区）', '标段']

# 选项里出现这些词，说明原表是「状态选项」，归一成 正确/不正确。
STATUS_WORDS = (
    '达标', '未达标', '完成', '未完成', '一致', '不一致', '符合', '不符合',
    '已实施', '未实施', '达到', '未达到', '质量', '规格', '带状', '非带状',
    '是', '否', '合格', '不合格',
)

# 环节名规范化（只改读起来含糊的，原表头仍保留在备注）。
FIELD_RENAME = {'核实总数量': '核实总株数'}

# 设计值列注释（只对认识的列加，未知列不加）。
DESIGN_COMMENTS = {
    '项目类型': '默认值：人工造乔木林。可按项目类型筛选后复用。',
    '小班号': '源表「小班号 / 唯一小班号1」已统一；149 个唯一小班。',
    '乡（镇）': '源表「乡（镇） / 乡镇2」已统一。',
    '村（社区）': '源表「村（社区） / 村2」已统一。',
    '设计面积（亩）': '合并源表「设计面积 / 小班面积」；单位：亩。',
    '设计密度（株/亩）': '单位：株/亩。',
    '设计株行距（米）': '源值形如 3.0×4.0米，单位已上移到表头。',
    '设计整地规格（cm）': '源值形如 60×60×50，单位 cm 已上移。',
    '设计总株数（株）': '合并源表「设计总株数 / 栽植总株数」。',
    '混交比例': '全角冒号已统一为半角（6：4 → 6:4）。',
    '树种1株数（株）': '源表列名「树种1」，值为株数。',
    '树种2株数（株）': '源表列名「树种2」，值为株数。',
}

# ---------- 通用小工具 ----------

def nspace(v):
    return re.sub(r'\s+', ' ', str(v or '')).strip()


def nkey(v):
    return re.sub(r'\s+', '', str(v or ''))


def font_rgb(cell):
    color = getattr(cell.font, 'color', None)
    if color is not None and getattr(color, 'type', None) == 'rgb' and color.rgb:
        return str(color.rgb).upper()
    return ''


def disp_len(v):
    """按显示宽度估字符数：中文 2，英文/数字 1。"""
    return sum(2 if ord(ch) > 127 else 1 for ch in str(v or ''))


def split_top(text, sep=';'):
    """只在括号外按 sep 切，避免把 下拉(甲;乙) 切碎。"""
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


def to_int(v):
    return int(round(float(v)))


# ---------- 源表读取 ----------

def detect_header_row(ws):
    """在前 8 行里找同时含「小班号」和彩色表头的那一行。"""
    best = None
    limit = min(ws.max_row or 0, 8)
    for r in range(1, limit + 1):
        vals = [ws.cell(r, c).value for c in range(1, (ws.max_column or 0) + 1)]
        has_key = any(v and '小班号' in nkey(v) for v in vals)
        if not has_key:
            continue
        colored = sum(1 for c in range(1, (ws.max_column or 0) + 1)
                      if font_rgb(ws.cell(r, c)) in (BLUE, GREEN))
        filled = sum(1 for v in vals if v not in (None, ''))
        score = 100 + colored * 2 + filled
        if best is None or score > best[0]:
            best = (score, r)
    return best[1] if best else 1


def classify(col):
    """返回 (id|design|flow, 规范名)。备注永远是流程列，哪怕源表误标绿色。"""
    raw = nspace(col['raw'])
    key = nkey(raw)
    if key == '备注':
        return 'flow', '备注'
    if key in CANON:
        return CANON[key]
    if col['color'] == BLUE:
        return 'id', raw
    if col['color'] == GREEN:
        return 'design', raw
    return 'flow', raw


def read_source(path):
    wb = load_workbook(path, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        hr = detect_header_row(ws)
        cols = []
        for c in range(1, (ws.max_column or 0) + 1):
            cell = ws.cell(hr, c)
            if cell.value is None or not str(cell.value).strip():
                continue
            cols.append({'col': c, 'raw': nspace(cell.value), 'color': font_rgb(cell)})
        key_col = next((x['col'] for x in cols if '小班号' in nkey(x['raw'])), None)
        if key_col is None:
            continue
        rows = []
        for r in range(hr + 1, (ws.max_row or 0) + 1):
            key = ws.cell(r, key_col).value
            if key is None or not str(key).strip():
                continue
            vals = {x['col']: ws.cell(r, x['col']).value for x in cols}
            rows.append((nspace(key), vals))
        if rows:
            sheets.append({'name': ws.title, 'header_row': hr, 'cols': cols, 'rows': rows})
    return sheets


def norm_value(canon, v):
    s = nspace(v)
    if canon == '设计面积（亩）':
        return round(float(s), 2)
    if canon in ('设计密度（株/亩）', '设计总株数（株）', '树种1株数（株）',
                 '树种2株数（株）', '撑杆总数量', '覆膜总数量'):
        return to_int(s)
    if canon == '设计株行距（米）':
        return re.sub(r'\s*(米|m|M)$', '', s)
    if canon == '设计整地规格（cm）':
        return re.sub(r'\s*(cm|CM|厘米)$', '', s)
    if canon == '混交比例':
        return s.replace('：', ':').replace(' ', '')
    return s


def build_design(sheets):
    """把多张源 sheet 按小班号合并成一张设计值表。"""
    design_cols, seen_design = [], set()
    for sh in sheets:
        for col in sh['cols']:
            role, name = classify(col)
            if role == 'design' and name not in seen_design:
                seen_design.add(name)
                design_cols.append(name)

    id_present = {classify(c)[1] for sh in sheets for c in sh['cols'] if classify(c)[0] == 'id'}
    id_cols = [x for x in ID_PREF if x in id_present]
    for sh in sheets:
        for col in sh['cols']:
            role, name = classify(col)
            if role == 'id' and name not in id_cols:
                id_cols.append(name)

    headers = ['项目类型'] + id_cols + design_cols
    order, data, conflicts = [], {}, []
    for sh in sheets:
        for key, vals in sh['rows']:
            rec = data.setdefault(key, {})
            if key not in order:
                order.append(key)
            for col in sh['cols']:
                role, name = classify(col)
                if role not in ('id', 'design'):
                    continue
                v = vals.get(col['col'])
                if v is None or not str(v).strip():
                    continue
                nv = norm_value(name, v)
                if name in rec and str(rec[name]) != str(nv):
                    conflicts.append((key, name, rec[name], nv))
                else:
                    rec.setdefault(name, nv)

    rows = []
    for key in order:
        rec = {'项目类型': PROJECT_TYPE}
        rec.update(data[key])
        rows.append({h: rec.get(h) for h in headers})
    return headers, rows, id_cols, design_cols, conflicts


# ---------- 表头解析 ----------

def parse_header(raw):
    """'核实密度（1.达标2.未达标）' → ('核实密度', '1.达标2.未达标')。"""
    s = nspace(raw)
    opens = [i for i in (s.find('('), s.find('（')) if i >= 0]
    if not opens:
        return s, ''
    i = min(opens)
    name = s[:i].strip()
    j = max(s.rfind(')'), s.rfind('）'))
    arg = s[i + 1:j].strip() if j > i else s[i + 1:].strip()
    return name, arg


def extract_options(arg):
    """从括号参数里抽选项，兼容 1. / 1， / 1、 / 1一致 等写法。"""
    a = (arg or '').strip()
    if not a:
        return []

    starts = []
    for m in re.finditer(r'(?<!\d)([1-9])\s*[.．、，,]\s*(?!\d)', a):
        starts.append(m.start())
    for m in re.finditer(
            r'(?:^|(?<=[；;，,]))\s*([1-9])\s*'
            r'(?:[.．、，,]\s*(?!\d)|(?=[^\d.．、，,\s]))', a):
        starts.append(m.start())
    starts = sorted(set(starts))

    if len(starts) >= 2:
        parts = []
        for i, pos in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(a)
            seg = re.sub(r'^\s*[1-9]\s*[.．、，,]?\s*', '', a[pos:end])
            seg = seg.strip(' ；;，,）)')
            if seg:
                parts.append(seg)
        if len(parts) >= 2:
            return parts

    if '；' in a or ';' in a:
        parts = []
        for seg in re.split(r'[；;]', a):
            seg = re.sub(r'^\s*[1-9]\s*[.．、，,]?\s*', '', seg).strip(' ；;，,）)')
            if seg:
                parts.append(seg)
        if len(parts) >= 2:
            return parts
    return []


# ---------- 概念匹配 ----------

def concept(name):
    n = nkey(name)
    if '规格' in n:
        if '撑杆' in n:
            return 'pole_spec'
        if '整地' in n:
            return 'site_spec'
        return 'spec'
    if '质量' in n:
        if '覆膜' in n:
            return 'film_quality'
        return 'quality'
    if '株行距' in n:
        return 'spacing'
    if '密度' in n:
        return 'density'
    if '整地方式' in n:
        return 'site_method'
    if '造林树种' in n:
        return 'species'
    if '混交方式' in n:
        return 'mix_mode'
    if '混交比例' in n:
        return 'mix_ratio'
    if '树种1' in n:
        return 'species1'
    if '树种2' in n:
        return 'species2'
    if '撑杆' in n:
        return 'pole'
    if '覆膜' in n:
        return 'film'
    if '成活率' in n:
        return 'survival_rate'
    if '成活株数' in n:
        return 'sample_live'
    if '死亡株数' in n:
        return 'sample_dead'
    if '巡护' in n or '情况' in n:
        return 'patrol'
    if '面积' in n:
        return 'area'
    if '株数' in n or '数量' in n:
        return 'tree_total'
    if '有机肥' in n or '普钙' in n:
        return 'fertilizer'
    return None


def design_concept(header):
    n = nkey(header)
    if '设计整地规格' in n:
        return 'site_spec'
    if '整地方式' in n:
        return 'site_method'
    if '设计株行距' in n:
        return 'spacing'
    if '设计密度' in n:
        return 'density'
    if '面积' in n:
        return 'area'
    if '设计总株数' in n:
        return 'tree_total'
    if '树种1' in n:
        return 'species1'
    if '树种2' in n:
        return 'species2'
    if '造林树种' in n:
        return 'species'
    if '混交方式' in n:
        return 'mix_mode'
    if '混交比例' in n:
        return 'mix_ratio'
    if '撑杆' in n:
        return 'pole'
    if '覆膜' in n:
        return 'film'
    return None


def with_judge(control, design):
    """数值类控件 + 有设计值表头 → 自动接 `→≥设计值`（通过判断，默认正确）。

    模板里已手写 `→规则` 的不覆盖；设计值是「常量：…」的不接（无法比大小）。
    """
    if not control.startswith('数值(') or '→' in control:
        return control
    if not design or str(design).startswith('常量'):
        return control
    return control[:-1] + '→≥设计值)'


def numeric_control(name):
    n = nkey(name)
    if '面积' in n:
        return '数值(亩)'
    if '密度' in n:
        return '数值(株/亩)'
    return '数值(株)'


def is_activity(name, options):
    n = nkey(name)
    return ('情况' in n and len(options) >= 2
            and any('、' in o or len(o) >= 4 for o in options))


def is_negative_option(text):
    s = re.split(r'[（(]', text)[0].strip()
    if not s:
        return False
    return (s.startswith(('未', '不', '非', '无'))
            or any(k in s for k in ('不达标', '不一致', '不符合', '不合格',
                                    '未达到', '未实施', '未完成')))


def option_control(options):
    """正向项统一为「正确」；错误项保留源表说法，多个错误项时逐个列出。

    每个错误项都带 →备注≥2：选中它必须就地填 ≥2 汉字小备注。
    只有「不正确」一个错误项时归一成同一个词（现场更好读）。
    """
    cleaned = []
    for o in options:
        s = re.split(r'[（(]', o)[0].strip()
        if s and s not in cleaned:
            cleaned.append(s)
    wrong = [o for o in cleaned if is_negative_option(o)]
    if len(wrong) >= 2:
        rest = [o + '→备注≥2' if o in wrong else o for o in cleaned[1:]]
        return '选项(正确;' + ';'.join(rest) + ')'
    if len(wrong) == 1:
        return '选项(正确;不正确→备注≥2)'
    rest = cleaned[1:]
    if rest:
        return '选项(正确;' + ';'.join(rest) + ')'
    return '选项(正确;不正确→备注≥2)'


def infer_control(name, arg, options, all_names):
    n = nkey(name)
    if n == '备注':
        return '长文本(隐藏;不通过必填≥2汉字)'
    if '成活率' in n:
        is_sample = '样地' in n or '调查' in n
        if (is_sample and any('成活株数' in x for x in all_names)
                and any('死亡株数' in x for x in all_names)):
            return '自动(成活株数/(成活株数+死亡株数)×100%)'
        return '百分数(0-100)'
    if '率' in n and ('测算' in arg or '百分' in arg):
        return '百分数(0-100)'
    if is_activity(name, options):
        return '多选(' + ';'.join(options) + ')'
    if options:
        quantitative = any(k in n for k in ('数量', '株数', '面积'))
        wants_actual = any(k in arg for k in ('填写实际', '按实际测量', '按实际测算', '实际数量'))
        if quantitative and wants_actual:
            return numeric_control(name)
        return option_control(options)
    if '数量' in n or '株数' in n:
        return numeric_control(name)
    if '率' in n:
        return '百分数(0-100)'
    return '文本'


def match_design(name, col_idx, sheet_design, used, occurrence):
    """按概念找设计列；重复表头按“左侧最近未用列”区分。"""
    candidates = [(c, h) for c, h in sheet_design if c < col_idx and h not in used]
    if not candidates:
        return ''
    if occurrence > 0:
        c, h = max(candidates, key=lambda x: x[0])
        used.add(h)
        return h
    con = concept(name)
    for c, h in sorted(candidates, key=lambda x: x[0], reverse=True):
        if design_concept(h) == con:
            used.add(h)
            return h
    return ''


def design_for(name, col_idx, sheet_design, used, occurrence):
    con = concept(name)
    if con == 'fertilizer':
        m = re.search(r'(\d+(?:\.\d+)?)\s*kg\s*/\s*株', name)
        return '常量：' + re.sub(r'\s+', '', m.group(0)) if m else ''
    if con in ('quality', 'film_quality'):
        return '常量：按设计要求'
    if con == 'pole_spec':
        return '常量：按设计规格'
    if con in ('survival_rate', 'sample_live', 'sample_dead', 'patrol') or not con:
        return ''
    return match_design(name, col_idx, sheet_design, used, occurrence)


def quality_label(name, quality, options):
    m = re.search(r'([\u4e00-\u9fa5]{2,4})' + quality, ' '.join(options))
    if m:
        return m.group(1) + quality
    base = nkey(name).replace('核实', '').replace('数量', '')
    return base + quality


def build_note(raw, control, design):
    note = '原表头：' + raw
    if control.startswith('选项'):
        if '→备注' in control:
            note += '；「正确」在首位；选「不正确」须填 ≥2 汉字小备注。'
        else:
            note += '；「正确」在首位，其余为错误项，选中即记录。'
    elif control.startswith('按钮'):
        note += '；不通过时校验备注 ≥2 个汉字。'
    elif control.startswith('多选'):
        note += '；可多选。'
    elif control.startswith('数值'):
        note += '；按数值填写，与设计值自动对照。'
    elif control.startswith('百分数'):
        note += '；填 0-100 的百分数。'
    elif control.startswith('自动'):
        note += '；自动计算，不手填。'
    elif control.startswith('长文本(隐藏)'):
        note += '；默认隐藏。'
    elif design.startswith('常量：'):
        note += '；设计值为常量提示。'
    else:
        note += '；按实际填写。'
    return note


def flow_rows_for_sheet(sh):
    """一张源 sheet → 一个流程定义。

    重复表头（如「核实栽植总株数」×3）连续输出，避免中间夹进别的环节；
    每行都重新做设计列匹配，因此第二/第三次会自动落到 树种1 / 树种2。
    """
    flow_cols = [c for c in sh['cols'] if classify(c)[0] == 'flow']
    sheet_design = [(c['col'], classify(c)[1]) for c in sh['cols'] if classify(c)[0] == 'design']
    names = [parse_header(c['raw'])[0] for c in flow_cols]
    counts = Counter(names)
    seen = Counter()
    used = set()
    rows = []

    def emit(col):
        raw = col['raw']
        name, arg = parse_header(raw)
        occurrence = seen[name]
        seen[name] += 1
        out = []

        if name == '备注':
            return [{
                'step': '备注', 'design': '',
                'control': '长文本(隐藏;不通过必填≥2汉字)',
                'note': '原表头：备注；默认隐藏，点「不通过」时展开，需 ≥2 个汉字。',
            }]

        options = extract_options(arg)
        quality = next((q for q in ('规格', '质量') if any(q in o for o in options)), None)

        if '数量' in name and quality:
            design = design_for(name, col['col'], sheet_design, used, occurrence)
            label = quality_label(name, quality, options)
            note = (f'原表头：{raw}；原表把数量与{quality}合在一列，'
                    f'这里拆成「{name}（数值）+ {label}（选项）」两行。')
            out.append({
                'step': FIELD_RENAME.get(name, name), 'design': design,
                'control': with_judge(numeric_control(name), design), 'note': note,
            })
            out.append({
                'step': label,
                'design': '常量：按设计规格' if quality == '规格' else '常量：按设计要求',
                'control': '选项(正确;不正确→备注≥2)',
                'note': note + ' 选「不正确」须填 ≥2 汉字小备注。',
            })
            return out

        design = design_for(name, col['col'], sheet_design, used, occurrence)
        control = with_judge(infer_control(name, arg, options, names), design)

        step = name
        note = build_note(raw, control, design)
        if occurrence > 0 and design and not design.startswith('常量：'):
            base = re.sub(r'[（(].*$', '', design)
            base = re.sub(r'^设计', '', base)
            step = '核实' + base
            note = (f'源表同名列第 {occurrence + 1} 次出现，按列位置取「{design}」。'
                    + note)
        step = FIELD_RENAME.get(step, step)

        out.append({
            'step': step, 'design': design,
            'control': control, 'note': note,
        })
        return out

    done = set()
    for col in flow_cols:
        if col['col'] in done:
            continue
        name = parse_header(col['raw'])[0]
        if counts[name] > 1:
            group = [c for c in flow_cols if parse_header(c['raw'])[0] == name]
            for gc in group:
                done.add(gc['col'])
                rows.extend(emit(gc))
        else:
            done.add(col['col'])
            rows.extend(emit(col))

    if not any(r['step'] == '备注' for r in rows):
        rows.append({
            'step': '备注', 'design': '',
            'control': '长文本(隐藏;不通过必填≥2汉字)',
            'note': '源表无备注列，补通用备注；默认隐藏，点「不通过」时展开，需 ≥2 个汉字。',
        })

    rows.append({
        'step': '是否通过', 'design': '', 'control': '按钮(通过;不通过→备注≥2)',
        'note': '本环节结论：通过 / 不通过按钮；不通过时校验备注 ≥2 个汉字后提交。',
    })
    return rows


def flow_sheet_name(title):
    t = nspace(title)
    if '整地' in t and '栽植' not in t:
        return '流程-整地'
    if '回塘' in t:
        return '流程-回塘底肥'
    if '栽植' in t:
        return '流程-栽植撑杆覆膜'
    if '完工' in t:
        return '流程-完工验收'
    if '巡护' in t:
        return '流程-日常巡护'
    t = re.sub(r'^人工造林[（(]?', '', t)
    t = re.sub(r'[）)]?转序验收$', '', t)
    t = re.sub(r'[^\u4e00-\u9fa5A-Za-z0-9]+', '', t)
    return '流程-' + (t[:10] or '未命名')


def route_rows(flow_names, project_type=PROJECT_TYPE):
    """按流程名生成默认路由；未识别的新流程串到前一流程之后。

    序号 0 = 独立流程（无前置 + 可重复），程序把它排除在主线默认选择之外，
    界面排在主线之后；它不参与任何前置关系，频繁填报也不影响主线。
    """
    def resolve(key):
        return next((n for n in flow_names if key in n), '') if key else ''

    rows = []
    for i, name in enumerate(flow_names):
        spec = next((v for k, v in ROUTE_SPEC.items() if k in name), None)
        if spec:
            pre_key, repeat, trigger, note = spec
            prereq = resolve(pre_key)
        else:
            prereq, repeat, trigger = (flow_names[i - 1] if i else ''), '否', '按序'
            note = '新增流程：默认串在前一流程之后，可按实际路由修改。'
        rows.append({
            'type': project_type, 'flow': name,
            'prereq': prereq, 'repeat': repeat,
            'trigger': trigger, 'note': note,
            'independent': bool(repeat == '是' and not prereq),
        })
    no = 0
    for rec in rows:
        if rec['independent']:
            rec['no'] = 0
        else:
            no += 1
            rec['no'] = no
    return rows


# ---------- 样式 ----------

THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=Side(style='medium', color='7F7F7F'))
FONT = 'PingFang SC'


def font(size=10, bold=False, color='000000'):
    return Font(name=FONT, size=size, bold=bold, color=color)


def fill(color):
    return PatternFill('solid', fgColor=color)


def add_comment(cell, text, w=360, h=150):
    cm = Comment(text, '模板')
    cm.width = w
    cm.height = h
    cell.comment = cm


def write_design(wb, headers, rows, id_cols, design_cols):
    ws = wb.active
    ws.title = '设计值'
    ws.append(headers)

    for c, h in enumerate(headers, 1):
        cell = ws.cell(1, c)
        is_id = h in id_cols
        cell.font = font(10, True, '1F4E78' if is_id else '375623')
        cell.fill = fill('DDEBF7' if is_id else 'E2EFDA')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = HEAD_BORDER
        if h in DESIGN_COMMENTS:
            add_comment(cell, DESIGN_COMMENTS[h])

    for r, rec in enumerate(rows, 2):
        for c, h in enumerate(headers, 1):
            cell = ws.cell(r, c, rec.get(h))
            cell.font = font(10)
            cell.border = BORDER
            numeric = h in design_cols and any(k in h for k in ('面积', '密度', '株数', '数量'))
            cell.alignment = Alignment(
                horizontal='right' if numeric else 'left',
                vertical='center', indent=1,
            )
            if '面积' in h:
                cell.number_format = '0.00'
            elif any(k in h for k in ('密度', '株数', '总数量')):
                cell.number_format = '0'

    ws.freeze_panes = get_column_letter(len(id_cols) + 1) + '2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{len(rows) + 1}'
    ws.row_dimensions[1].height = 44
    for r in range(2, len(rows) + 2):
        ws.row_dimensions[r].height = 20

    widths = {
        '项目类型': 14, '小班号': 12, '乡（镇）': 12, '村（社区）': 14, '标段': 10,
        '设计面积（亩）': 15, '设计密度（株/亩）': 17, '设计株行距（米）': 15,
        '整地方式': 12, '设计整地规格（cm）': 19, '设计总株数（株）': 15,
        '造林树种': 20, '混交方式': 10, '混交比例': 10,
        '树种1株数（株）': 15, '树种2株数（株）': 15,
        '撑杆总数量': 13, '覆膜总数量': 13,
    }
    for c, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(c)].width = widths.get(h, max(12, len(h) * 2 + 2))

    ws.sheet_properties.tabColor = '375623'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = '1:1'
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return ws


def write_routes(wb, routes):
    ws = wb.create_sheet('流程路由')
    ws.append(ROUTE_COLS)

    for c, h in enumerate(ROUTE_COLS, 1):
        cell = ws.cell(1, c)
        cell.font = font(10, True, 'FFFFFF')
        cell.fill = fill('2F5597')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = HEAD_BORDER

    add_comment(ws['A1'], '同一项目类型内的排序；也是路由唯一编号。')
    add_comment(ws['B1'], '与「设计值」A 列取值一致。一个项目类型一套路由；'
                          '新增类型时加行即可，不用改代码。')
    add_comment(ws['C1'], '对应「流程-*」sheet 名；每个流程必须且只能在本项目类型出现一次。')
    add_comment(ws['D1'], '可写多个，用英文分号分隔（AND：全部完成才可发起）；'
                          '留空 = 无前置、随时可发起。只能引用同项目类型的流程。')
    add_comment(ws['E1'], '是 = 可多次发起（如日常巡护）；否 = 只允许一次（如完工验收）。')
    add_comment(ws['F1'], '按序 = 前置完成后自动进入；按需 = 手动发起；定期 = 按周期发起。')
    add_comment(ws['G1'], '业务含义 / 路由备注；也用于说明并行、重复和不阻塞关系。')

    ordered = sorted(routes, key=lambda r: (0 if r.get('independent') else 1,
                                            r.get('no') or 0))
    for i, rec in enumerate(ordered, 2):
        vals = [rec.get('no', i - 1), rec['type'], rec['flow'], rec['prereq'] or None,
                rec['repeat'], rec['trigger'], rec['note']]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(i, c, v)
            cell.border = BORDER
            cell.font = font(10)
            cell.alignment = Alignment(vertical='center', wrap_text=(c >= 3))
        ws.cell(i, 1).alignment = Alignment(horizontal='center', vertical='center')
        ws.cell(i, 2).alignment = Alignment(horizontal='center', vertical='center')
        ws.cell(i, 3).font = font(10, True)
        ws.cell(i, 4).font = font(10, False, '1F4E78')
        ws.cell(i, 5).alignment = Alignment(horizontal='center', vertical='center')
        ws.cell(i, 6).alignment = Alignment(horizontal='center', vertical='center')
        if rec['repeat'] == '是':
            ws.cell(i, 5).font = font(10, True, 'C55A11')
            ws.cell(i, 5).fill = fill('FCE4D6')
        if rec['trigger'] == '定期':
            ws.cell(i, 6).fill = fill('FFF2CC')
        ws.cell(i, 7).font = font(9, False, '595959')

        lines = max(math.ceil(disp_len(rec['note']) / 58),
                    math.ceil(disp_len(rec['prereq']) / 24), 1)
        ws.row_dimensions[i].height = max(28, lines * 15 + 12)

    widths = {'A': 6, 'B': 15, 'C': 24, 'D': 26, 'E': 9, 'F': 11, 'G': 60}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:G{len(routes) + 1}'
    ws.row_dimensions[1].height = 34
    ws.sheet_properties.tabColor = '2F5597'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.print_title_rows = '1:1'
    ws.print_options.horizontalCentered = True
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return ws


# ---------- 参数页（程序配置 → 模板，改参数不改代码） ----------

PARAM_HEADER = ['key', 'value', '类型', '默认值', '说明', '示例']

# 程序消费的 key。以后程序新增配置项时，往这里加一行，运行 `params` 命令即补进模板。
PARAM_SPECS = [
    ('可编辑列', '列表', 'v2 界面不显示数据网格，留空；以后若要开网格再填列名。', ''),
    ('功能', '列表', '拍照 / 视频 / 轨迹 / 打卡；开了拍照/视频还要补三条拍摄模板 key。',
     '拍照;轨迹'),
    ('相片备注', '模板', '照片水印正文（首行时间戳固定，C06 不参数化）；{{列名}}取当前小班行。',
     ''),
    ('相片文件名', '模板', '照片文件名（不含扩展名）；{{时间}}=YYYYMMDD_HHMMSS。',
     ''),
    ('目录', '模板', '照片在本机相册的子目录：Pictures/{目录}/；半角 / 分层。', ''),
    ('压缩最长边', '数值', '手机端压缩最长边(px)，300-8000，仅影响新拍照片。', '1440'),
    ('压缩质量', '数值', 'JPEG 质量 0.1-1，仅影响新拍照片；建议 0.75-0.82。', '0.8'),
    ('搜索选项', '控件映射',
     '选择栏字段|控件类型；顺序即级联顺序，前面的选择过滤后面的候选，小班放最后。',
     '项目类型|select;标段|select;乡（镇）|select;村（社区）|select;小班号|search'),
    ('unique-key', '文本', '行标识列；必须非空且唯一，且不能出现在可编辑列。', '小班号'),
    ('导出筛选', '控件映射', '后台下载 Excel 的筛选弹框字段（只筛行）。',
     '项目类型|select;标段|select'),
]


def _ph(headers, names):
    """把存在的数据表头拼成占位符片段（缺列自动跳过，避免模板写死列名）。"""
    return ''.join('{{%s}}' % h for h in names if h in headers)


def photo_remark_value(headers):
    return _ph(headers, ('项目类型', '标段', '乡（镇）', '村（社区）', '小班号')) + '小班'


def photo_filename_value(headers):
    parts = [h for h in ('标段', '小班号') if h in headers]
    return '_'.join(['{{%s}}' % h for h in parts] + ['{{时间}}'])


def photo_dir_value(headers):
    return '/'.join('{{%s}}' % h for h in ('项目类型', '标段', '小班号') if h in headers)


def search_option_value(headers):
    """选择栏默认顺序：项目类型 → 标段 → 乡（镇）→ 村（社区）→ 小班（最后，文本搜索）。"""
    hs = [str(h).strip() for h in headers if h not in (None, '')]
    order = [h for h in ('项目类型', '标段', '乡（镇）', '村（社区）') if h in hs]
    key = '小班号' if '小班号' in hs else (hs[0] if hs else '')
    items = [f'{h}|select' for h in order]
    if key:
        items.append(f'{key}|search')
    return ';'.join(items)


def write_params(wb, headers, routes=None, keep_existing=True):
    """写入/同步「参数」sheet：程序升级新增 key 时只补行，**不覆盖**已有值。

    keep_existing=False 用于重建模板：所有 key 一律回到默认值。
    """
    existed = {}
    if PARAM_SHEET in wb.sheetnames:
        for row in wb[PARAM_SHEET].iter_rows(min_row=2, max_col=2, values_only=True):
            k, v = (list(row) + [None, None])[:2]
            k = '' if k is None else str(k).strip()
            if k:
                existed[k] = '' if v is None else str(v)
        wb.remove(wb[PARAM_SHEET])

    hs = [str(h).strip() for h in headers if h not in (None, '')]
    defaults = {
        '功能': '拍照;轨迹',
        '相片备注': photo_remark_value(hs),
        '相片文件名': photo_filename_value(hs),
        '目录': photo_dir_value(hs),
        '压缩最长边': '1440',
        '压缩质量': '0.8',
        '搜索选项': search_option_value(headers),
        'unique-key': '小班号' if '小班号' in hs else '',
        '导出筛选': ';'.join(f'{h}|select' for h in ('项目类型', '标段') if h in hs),
    }

    ws = wb.create_sheet(PARAM_SHEET)
    ws.append(PARAM_HEADER)
    for c, h in enumerate(PARAM_HEADER, 1):
        cell = ws.cell(1, c)
        cell.font = font(10, True, 'FFFFFF')
        cell.fill = fill('7F6000')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = HEAD_BORDER
    add_comment(ws['A1'], '程序读取的配置。程序新增 key 时用 '
                          '`python3 tools/tpl/tpl_tool.py params` 补行，已有值不覆盖。')

    added, kept = [], []
    for i, (key, typ, desc, example) in enumerate(PARAM_SPECS, 2):
        default = defaults.get(key, '')
        if keep_existing and key in existed:
            val = existed[key]
            kept.append(key)
        else:
            val = default
            if key not in existed:
                added.append(key)
        for c, v in enumerate([key, val, typ, val, desc, example], 1):
            cell = ws.cell(i, c, v)
            cell.border = BORDER
            cell.font = font(10, c == 1)
            cell.alignment = Alignment(vertical='center', wrap_text=(c in (2, 5, 6)))
        ws.row_dimensions[i].height = 30

    for col, w in zip('ABCDEF', (14, 46, 10, 40, 52, 40)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'
    ws.row_dimensions[1].height = 26
    ws.sheet_properties.tabColor = '7F6000'
    return {'added': added, 'kept': kept}


def write_flow(wb, name, rows):
    ws = wb.create_sheet(name)
    ws.append(FLOW_COLS)

    for c, h in enumerate(FLOW_COLS, 1):
        cell = ws.cell(1, c)
        cell.font = font(10, True, 'FFFFFF')
        cell.fill = fill('1F4E78')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = HEAD_BORDER

    add_comment(ws['A1'], '界面顺序；也是流程内唯一编号。')
    add_comment(ws['B1'], '一个填报 / 核验项，取自源表黑色表头。')
    add_comment(ws['C1'], '「设计值」sheet 的列名；运行时按小班取该列值，显示在表单里作提示；'
                          '或「常量：xxx」固定提示；留空=无设计值。')
    add_comment(ws['D1'], '控件 DSL：类型(参数)，例如 选项(正确;不正确→备注≥2)、按钮(通过;不通过→备注≥2)、'
                          '数值(株)、自动(公式)。详见「说明」。')
    add_comment(ws['E1'], '预留附件控件：可填 拍照(现场;张数) / 上传(照片,PDF)；留空=无附件。')
    add_comment(ws['F1'], '原始表头 + 归一 / 拆分说明，便于审计。')

    for i, rec in enumerate(rows, 2):
        vals = [i - 1, rec['step'], rec['design'], rec['control'], None, rec['note']]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(i, c, v)
            cell.border = BORDER
            cell.font = font(10)
            cell.alignment = Alignment(vertical='center', wrap_text=(c >= 3))
        ws.cell(i, 1).alignment = Alignment(horizontal='center', vertical='center')
        ws.cell(i, 2).font = font(10, True)
        ws.cell(i, 3).font = font(10, False, '1F4E78' if str(rec['design']).startswith('常量：') else '000000')
        ctrl = str(rec['control'])
        if ctrl.startswith(('选项', '多选', '按钮')):
            ws.cell(i, 4).font = font(10, True, 'C00000')
            ws.cell(i, 4).fill = fill('FCE4D6')
        elif ctrl.startswith('自动'):
            ws.cell(i, 4).font = font(10, False, '1F4E78')
            ws.cell(i, 4).fill = fill('DDEBF7')
        elif '隐藏' in ctrl:
            ws.cell(i, 4).font = font(10, False, '7F7F7F')
            ws.cell(i, 4).fill = fill('F2F2F2')
        ws.cell(i, 5).alignment = Alignment(vertical='center', wrap_text=True)
        ws.cell(i, 6).font = font(9, False, '595959')
        ws.cell(i, 6).alignment = Alignment(vertical='center', wrap_text=True)

        lines = max(
            math.ceil(disp_len(rec['note']) / 62),
            math.ceil(disp_len(rec['control']) / 36),
            1,
        )
        ws.row_dimensions[i].height = max(28, lines * 15 + 12)

    widths = {'A': 6, 'B': 24, 'C': 26, 'D': 44, 'E': 14, 'F': 66}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:F{len(rows) + 1}'
    ws.row_dimensions[1].height = 34
    ws.sheet_properties.tabColor = 'C55A11'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.print_title_rows = '1:1'
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return ws


def write_doc(wb, src_name, flow_names):
    ws = wb.create_sheet('说明')
    ws.sheet_properties.tabColor = '7F7F7F'
    ws.sheet_view.showGridLines = False
    widths = {'A': 18, 'B': 18, 'C': 30, 'D': 18, 'E': 34, 'F': 34}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    def title(text, row):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        c = ws.cell(row, 1, text)
        c.font = font(16, True, '375623')
        c.alignment = Alignment(vertical='center')
        ws.row_dimensions[row].height = 30
        return row + 1

    def section(text, row):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        c = ws.cell(row, 1, text)
        c.font = font(11, True, 'FFFFFF')
        c.fill = fill('375623')
        c.alignment = Alignment(vertical='center')
        ws.row_dimensions[row].height = 24
        return row + 1

    def para(text, row, height=None):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        c = ws.cell(row, 1, text)
        c.font = font(10)
        c.alignment = Alignment(vertical='top', wrap_text=True)
        if height:
            ws.row_dimensions[row].height = height
        return row + 1

    def kv(k, v, row, height=30):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=6)
        a = ws.cell(row, 1, k)
        a.font = font(10, True)
        a.alignment = Alignment(vertical='top', wrap_text=True)
        b = ws.cell(row, 3, v)
        b.font = font(10)
        b.alignment = Alignment(vertical='top', wrap_text=True)
        ws.row_dimensions[row].height = height
        return row + 1

    def table(headers, rows, row):
        spans = [(1, 2), (3, 4), (5, 6)]
        for (c1, c2), text in zip(spans, headers):
            ws.merge_cells(start_row=row, start_column=c1, end_row=row, end_column=c2)
            cell = ws.cell(row, c1, text)
            cell.font = font(10, True, 'FFFFFF')
            cell.fill = fill('1F4E78')
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = BORDER
        ws.row_dimensions[row].height = 24
        row += 1
        for vals in rows:
            for (c1, c2), text in zip(spans, vals):
                ws.merge_cells(start_row=row, start_column=c1, end_row=row, end_column=c2)
                cell = ws.cell(row, c1, text)
                cell.font = font(9)
                cell.alignment = Alignment(vertical='center', wrap_text=True)
                cell.border = BORDER
            ws.row_dimensions[row].height = 26
            row += 1
        return row

    r = 1
    r = title('tpl-人工造林-验收流程模板 · 说明', r)
    r = para(f'版本 v2.7 ｜ 2026-10-28 ｜ 来源：{src_name}', r)
    r = para('口径：模板由 tools/tpl_tool.py 从源表头自动解析生成；新增项目 / 流程只改源表或加 sheet，不改解析代码。', r, 30)
    r += 1

    r = section('一、Sheet 约定', r)
    r = kv('设计值（第一个 sheet）',
           '一行 = 一个小班。A 列「项目类型」默认「人工造乔木林」；蓝底 = 身份键，绿底 = 设计值。'
           '149 个唯一小班，5 个源 sheet 按小班号合并去重。', r, 34)
    r = kv('流程路由',
           '一行 = 一条路由。列 = 序号 / 项目类型 / 流程 / 前置流程 / 可重复 / 触发方式 / 说明；'
           '决定同一项目类型下各流程的先后、并行与重复。', r, 30)
    r = kv(f'流程-*（{len(flow_names)} 个）',
           '一行 = 一个环节。列 = 序号 / 环节 / 设计值表头 / 控件 / 附件 / 备注。不含填报数据，只是界面定义。', r, 30)
    r = kv('参数',
           '程序读取的配置（搜索栏顺序、功能、唯一键、导出筛选）。'
           '由 tpl_tool.py 的 params 命令同步：程序新增 key 时只补行，已有值不覆盖。', r, 34)
    r = kv('说明', '本页：列语义、控件 DSL、设计值来源、解析约定。', r)
    r += 1

    r = section('二、流程路由', r)
    r = para('主线（按序、每步一次）：流程-整地 → 流程-回塘底肥 → 流程-栽植撑杆覆膜 → 流程-完工验收。', r, 24)
    r = para('独立（序号 0、无前置、可重复）：流程-日常巡护；随时可发起，可多次，'
             '不参与主线默认选择，也不影响任何前后关系。', r, 34)
    r = kv('项目类型', '与「设计值」A 列一致；一个项目类型一套路由，新增类型只需加行。', r, 30)
    r = kv('前置流程', '可写多个，用 ; 分隔 = 全部完成后才可发起；留空 = 随时可发起；只能引用同项目类型内的流程。', r, 34)
    r = kv('可重复 / 触发方式', '可重复：是 = 可多次（如巡护），否 = 仅一次（如完工验收）。'
                              '触发方式：按序 = 前置完成后进入，按需 = 手动发起，定期 = 按周期发起。', r, 34)
    r = kv('新增流程', '源表加 sheet 后重新 build，路由默认串到前一流程之后；再按实际调整前置 / 重复 / 触发。', r, 30)
    r += 1

    r = section('三、流程表 6 列', r)
    r = kv('序号', '主线界面顺序（1 起）；**序号 0 = 独立流程**，界面排在主线之后、不参与默认选择。', r, 30)
    r = kv('环节', '本流程中的一个填报 / 核验项，取自源表黑色表头，已去掉括号里的选项说明。', r, 30)
    r = kv('设计值表头',
           '写「设计值」sheet 的列名。运行时按当前小班取该列值，自动显示在表单里作提示；'
           '「常量：xxx」为固定提示；留空 = 无设计值。', r, 34)
    r = kv('控件', '控件 DSL：类型(参数)，见第四节。', r, 24)
    r = kv('附件', '预留：上传附件 / 拍照。目前全部留空，后续按需填 拍照(现场;张数) 或 上传(照片,PDF)。', r, 30)
    r = kv('备注', '原始表头 + 归一 / 拆分说明，便于审计；「备注」行默认隐藏，不通过时展开。', r, 30)
    r += 1

    r = section('四、控件 DSL（“控件表达”）', r)
    r = table(
        ['控件写法', '示例', '含义'],
        [
            ('选项(正确;错误项)', '选项(正确;未实施施肥;未达到每株1.5kg)', '单选；第一项为默认；错误项可多个'),
            ('选项(正确;错误项→备注≥2)', '选项(正确;不正确→备注≥2)', '选中带 →备注 的错误项，就在该环节下方展开「备注」文本框，须 ≥2 汉字'),
            ('按钮(通过;不通过→备注≥2)', '按钮(通过;不通过→备注≥2)', '操作按钮；不通过时校验流程备注 ≥2 汉字'),
            ('多选(甲;乙;丙)', '多选(林地清理;底肥放置;…)', '多选勾选'),
            ('数值(单位)', '数值(株) / 数值(亩)', '非负数；与设计值自动对照'),
            ('数值(单位→判断规则)', '数值(株→≥设计值) / 数值(株→≥设计值×0.95)',
             '通过判断：实测满足规则显示「正确」（默认），不满足显示「不正确」并拦住「通过」'),
            ('百分数(0-100)', '百分数(0-100) / 百分数(0-100→≥85)', '百分数，0–100；可接 →规则 自动判断'),
            ('文本', '文本', '单行文本'),
            ('长文本', '长文本', '多行文本'),
            ('自动(公式)', '自动(成活株数/(成活株数+死亡株数)×100%)', '自动计算，不手填'),
            ('长文本(隐藏;条件)', '长文本(隐藏;不通过必填≥2汉字)', '默认隐藏；可带条件，如不通过时展开必填'),
            ('拍照 / 上传 / 打卡', '拍照(现场;1)', '填在「附件」列，本模板暂未使用'),
        ], r)
    r += 1

    r = section('五、通过判断（→规则）', r)
    r = para('1. 数值 / 百分数 / 自动 控件后面可以接 →规则，例如 数值(株→≥设计值)、百分数(0-100→≥85)。', r, 30)
    r = para('2. 规则写法：比较符（≥ > ≤ < =）+ 阈值；阈值可以是「设计值」或数字，'
             '后面可乘系数（×0.95）或加减偏移（-5）。', r, 34)
    r = para('3. 默认「正确」：没填实测值时显示正确；填了以后按规则实时重算。'
             '判为「不正确」时不能提交「通过」，现场确实不达标请选「不通过」并写明备注。', r, 40)
    r = para('4. 不写 →规则 = 不自动判断（与旧模板一致）；规则引用「设计值」但该行没有设计值表头时，'
             '提交「通过」会被拦下并提示无法判定。', r, 36)
    r += 1

    r = section('六、选项、按钮与必填', r)
    r = para('1. 选项第一项固定为正向词「正确」，其后为错误项；错误项可以有多个。', r, 24)
    r = para('2. 错误项可以多个，**每个错误项都写 →备注≥2**（如 选项(正确;未实施施肥→备注≥2;未达到每株1.5kg→备注≥2)）：'
             '选中它就在本环节下方展开「备注」文本框，≥2 汉字才能提交。', r, 36)
    r = para('3. 可见环节默认必填；「自动」与带「隐藏」的控件不必填，带条件的隐藏项按条件必填。', r, 26)
    r = para('4. 每个流程最后一行固定为「是否通过」，其前一行固定为「备注」；'
             'App 不单独渲染这两行，统一在表单底部显示「通过 / 不通过」两个按钮。', r, 36)
    r = para('5. 点「通过」= 提交且结论通过；点「不通过」= 展开并校验备注 ≥2 个汉字后再提交。'
             '原「提交」按钮已由这两个按钮取代。', r, 36)
    r += 1
    break_row = r

    r = section('七、设计值表头来源', r)
    r = para('• 自动匹配：核实密度 → 设计密度（株/亩）、核实株行距 → 设计株行距（米）、'
             '核实总株数 → 设计总株数（株）等；按概念匹配，不写死列号。', r, 30)
    r = para('• 常量：有机肥 1.5kg/株、普钙 0.2kg/株、撑杆规格、覆膜质量、回塘质量。', r, 24)
    r = para('• 留空：栽植成活率、样地调查、巡护情况等源表没有设计值的项。', r, 24)
    r = para('• 源表重复表头「核实栽植总株数」按位置区分：栽植总株数 / 树种1 / 树种2。', r, 26)
    r += 1

    r = section('八、数据口径', r)
    r = para('• 5 个源 sheet 均 149 行，小班号集合一致；合并为 149 行，无重复行。', r, 24)
    r = para('• 语义重复列合并：设计面积 / 小班面积 → 设计面积（亩）；设计总株数 / 栽植总株数 → 设计总株数（株）。', r, 26)
    r = para('• 单位上移表头；株行距 / 整地规格去掉尾部单位；混交比例全角冒号 → 半角。', r, 26)
    r = para('• 撑杆 / 覆膜原表把数量与规格 / 质量合在一列，流程里拆成「数量（数值）+ 规格 / 质量（选项）」两行。', r, 30)
    r = para('• 选项正向统一为「正确」，源表错误项保留；每个错误项须写 →备注≥2，选中时就地展开文本框。', r, 30)
    r = para('• 原始表头完整保留在流程表「备注」列；未删除任何源数据列。', r, 24)
    r += 1

    r = section('九、解析约定（后续代码）', r)
    r = kv('设计值', '第 1 行表头，第 2 行起数据；按「小班号」关联。', r, 24)
    r = kv('流程路由', '第 1 行表头；按「项目类型」分组、按「序号」排序；前置流程支持 ; 分隔与环检测。', r, 30)
    r = kv('流程-*', '第 1 行表头，第 2 行起为环节定义；按「序号」排序。', r, 24)
    r = kv('控件', '取「(」前的类型；选项 / 按钮用半角分号分隔，「→」后是附加规则：'
                    '选项 → 备注≥2（就地展开备注），数值 → ≥设计值 / ≥85（通过判断）。', r, 36)
    r = kv('结论行', '最后一行「是否通过」= 按钮(通过;不通过→备注≥2)；其前一行「备注」必填 ≥2 汉字；'
                      'App 底部统一渲染「通过 / 不通过」提交，不重复显示这两行。', r, 40)
    r = kv('设计值表头引用', '命中「设计值」sheet 表头 → 取当前小班值；以「常量：」开头 → 原样展示；空 → 无。', r, 30)
    r = kv('不改字段', '新增源表 sheet / 列后重新运行 tools/tpl_tool.py build；代码按列语义读，不写死项目字段。', r, 30)
    r = kv('命令', 'python3 tools/tpl/tpl_tool.py build  |  params [--file <模板.xlsx>] [--force]  |  show --file <模板.xlsx>',
           r, 30)

    ws.page_setup.orientation = 'portrait'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 2
    ws.print_area = f'A1:F{r - 1}'
    ws.row_breaks.append(Break(id=break_row))
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.sheet_view.zoomScale = 90
    return ws


# ---------- 生成 ----------

def build_workbook(src, out):
    sheets = read_source(src)
    if not sheets:
        raise SystemExit('未从源表识别到任何数据 sheet（需要含「小班号」表头）')

    headers, rows, id_cols, design_cols, conflicts = build_design(sheets)
    wb = Workbook()
    write_design(wb, headers, rows, id_cols, design_cols)

    flow_defs = []
    for sh in sheets:
        name = flow_sheet_name(sh['name'])
        if any(name == n for n, _ in flow_defs):
            name = name + '-' + str(len(flow_defs) + 1)
        flow_defs.append((name, flow_rows_for_sheet(sh)))
    flow_names = [n for n, _ in flow_defs]

    routes = route_rows(flow_names)
    write_routes(wb, routes)
    for name, flow_rows in flow_defs:
        write_flow(wb, name, flow_rows)

    write_params(wb, headers, routes, keep_existing=False)
    write_doc(wb, os.path.basename(src), flow_names)

    tmp = out + '.tmp.xlsx'
    wb.save(tmp)
    os.replace(tmp, out)
    return {
        'sheets': sheets,
        'headers': headers,
        'rows': rows,
        'id_cols': id_cols,
        'design_cols': design_cols,
        'conflicts': conflicts,
        'flow_names': flow_names,
        'routes': routes,
        'out': out,
    }


# ---------- 回读校验 ----------

def parse_control(text):
    s = (text or '').strip()
    opens = [i for i in (s.find('('), s.find('（')) if i >= 0]
    if not opens:
        return {'type': s, 'arg': '', 'options': [], 'hidden': False}
    i = min(opens)
    j = max(s.rfind(')'), s.rfind('）'))
    arg = s[i + 1:j].strip() if j > i else s[i + 1:].strip()
    ctype = s[:i].strip()
    options, rules = [], {}
    if ctype in ('选项', '多选', '按钮'):
        for item in split_top(arg, ';'):
            item = item.strip()
            if not item:
                continue
            name, _, rule = item.partition('→')
            name, rule = name.strip(), rule.strip()
            if name and name not in options:
                options.append(name)
            if rule:
                rules[name] = rule
    return {'type': ctype, 'arg': arg, 'options': options,
            'rules': rules, 'hidden': '隐藏' in arg}


def read_routes(wb):
    ws = wb['流程路由']
    headers = [nspace(ws.cell(1, c).value) for c in range(1, len(ROUTE_COLS) + 1)]
    rows = []
    for r in range(2, ws.max_row + 1):
        vals = [ws.cell(r, c).value for c in range(1, len(ROUTE_COLS) + 1)]
        if not any(v not in (None, '') for v in vals):
            continue
        no, ptype, flow, prereq, repeat, trigger, note = vals
        rows.append({
            'no': no, 'type': nspace(ptype), 'flow': nspace(flow),
            'prereq': [x.strip() for x in re.split(r'[;；]', nspace(prereq)) if x.strip()],
            'repeat': nspace(repeat), 'trigger': nspace(trigger), 'note': nspace(note),
        })
    return headers, rows


def find_cycle(graph):
    state, stack = {}, []

    def dfs(n):
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, []):
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if state.get(m, 0) == 0:
                cyc = dfs(m)
                if cyc:
                    return cyc
        stack.pop()
        state[n] = 2
        return None

    for n in graph:
        if state.get(n, 0) == 0:
            cyc = dfs(n)
            if cyc:
                return cyc
    return None


def validate_routes(wb, errors, warnings):
    if '流程路由' not in wb.sheetnames:
        errors.append('缺少「流程路由」sheet')
        return []
    headers, routes = read_routes(wb)
    if headers != ROUTE_COLS:
        errors.append('「流程路由」表头必须是：' + ' / '.join(ROUTE_COLS))
    if not routes:
        errors.append('「流程路由」没有路由数据')
        return routes

    ws = wb['设计值']
    design_types = {nspace(ws.cell(r, 1).value)
                    for r in range(2, ws.max_row + 1)
                    if ws.cell(r, 1).value not in (None, '')}
    flow_sheets = {n for n in wb.sheetnames if n.startswith('流程-')}
    by_type, seen, nums = {}, set(), set()
    for rec in routes:
        no, ptype, flow = rec['no'], rec['type'], rec['flow']
        tag = f'流程路由 第 {no} 行「{flow or "—"}」'
        try:
            no = int(no)
        except (TypeError, ValueError):
            errors.append(f'{tag}的「序号」必须是整数')
        else:
            if (ptype, no) in nums:
                errors.append(f'{tag}的「序号」在同一项目类型下重复')
            nums.add((ptype, no))
        if not ptype:
            errors.append(f'{tag}缺少「项目类型」')
        elif ptype not in design_types:
            warnings.append(f'{tag}的项目类型「{ptype}」不在「设计值」中')
        if not flow:
            errors.append(f'{tag}缺少「流程」')
        elif flow not in flow_sheets:
            errors.append(f'{tag}引用了不存在的流程 sheet')
        if (ptype, flow) in seen:
            errors.append(f'{tag}在同一项目类型下重复出现')
        seen.add((ptype, flow))
        if rec['repeat'] not in REPEAT_VALUES:
            errors.append(f'{tag}的「可重复」必须是 是 / 否')
        if rec['trigger'] not in TRIGGER_VALUES:
            errors.append(f'{tag}的「触发方式」必须是 按序 / 按需 / 定期')
        by_type.setdefault(ptype, []).append(rec)

    for ptype, recs in by_type.items():
        names = {r['flow'] for r in recs}
        graph = {}
        for rec in recs:
            for pre in rec['prereq']:
                if pre == rec['flow']:
                    errors.append(f'流程路由「{rec["flow"]}」不能以自己为前置')
                elif pre not in names:
                    errors.append(f'流程路由「{rec["flow"]}」的前置「{pre}」不在同项目类型中')
            graph[rec['flow']] = [p for p in rec['prereq'] if p in names]
        cyc = find_cycle(graph)
        if cyc:
            errors.append('流程路由存在环：' + ' → '.join(cyc))

    for ptype in sorted(design_types - set(by_type)):
        errors.append(f'项目类型「{ptype}」没有流程路由')
    unused = flow_sheets - {r['flow'] for r in routes}
    if unused:
        errors.append('以下流程未出现在任何路由中：' + '、'.join(sorted(unused)))
    return routes


def show_workbook(path):
    wb = load_workbook(path)
    if '设计值' not in wb.sheetnames:
        raise SystemExit('缺少「设计值」sheet')
    design_headers = [c.value for c in wb['设计值'][1] if c.value]
    errors, warnings = [], []
    total = 0

    routes = validate_routes(wb, errors, warnings)
    if routes:
        print(f'\n== 流程路由（{len(routes)} 条）')
        for rec in routes:
            prereq = '、'.join(rec['prereq']) or '—'
            kind = '可重复' if rec['repeat'] == '是' else '仅一次'
            print('  {:>2}  {} | {:<22} | 前置 {:<24} | {} | {}'.format(
                rec['no'], rec['type'], rec['flow'], prereq, kind, rec['trigger']))

    for name in wb.sheetnames:
        if not name.startswith('流程-'):
            continue
        ws = wb[name]
        rows = []
        for r in range(2, ws.max_row + 1):
            vals = [ws.cell(r, c).value for c in range(1, 7)]
            if not any(v not in (None, '') for v in vals):
                continue
            rows.append(vals)
        total += len(rows)
        print(f'\n== {name}（{len(rows)} 个环节）')
        for no, step, design, control, attach, note in rows:
            c = parse_control(control)
            ref = ''
            if design:
                if str(design).startswith('常量：'):
                    ref = '常量'
                elif design in design_headers:
                    ref = '设计值表'
                else:
                    ref = '缺失'
                    errors.append(f'{name} 第 {no} 行「{step}」引用了不存在的设计表头：{design}')
            shown = c['type'] + (f'({c["arg"]})' if c['arg'] else '')
            print(f'  {no:>2}  {step:<12} | {design or "—":<22} | '
                  f'{shown}{(" → " + "/".join(c["options"])) if c["options"] else ""}'
                  f'{(" [" + ref + "]") if ref else ""}'
                  f'{(" [附件:" + str(attach) + "]") if attach else ""}')
            if (c['type'] in ('选项', '按钮') and c['options']
                    and c['options'][0] not in ('正确', '通过')):
                errors.append(f'{name} 第 {no} 行「{step}」第一个选项不是正向词：{c["options"][0]}')
            if c['type'] == '选项':
                for o in c['options'][1:]:
                    if is_negative_option(o) and '备注' not in str(c['rules'].get(o, '')):
                        warnings.append(
                            f'{name} 第 {no} 行「{step}」错误项「{o}」没写 →备注≥N，'
                            f'选中时不会展开文本框')
            if c['type'] == '按钮' and c['options'] != ['通过', '不通过']:
                errors.append(f'{name} 第 {no} 行「{step}」按钮必须是 通过 / 不通过')
            if c['type'] in ('数值', '百分数', '自动') and '→' in str(c['arg']):
                rule_txt = str(c['arg']).split('→', 1)[1].strip()
                rule = flow_judge.parse(rule_txt)
                if not rule:
                    errors.append(
                        f'{name} 第 {no} 行「{step}」通过判断规则「{rule_txt}」看不懂；'
                        f'示例：≥设计值 / ≥设计值×0.95 / ≥85')
                elif rule['is_design'] and (not design or str(design).startswith('常量')):
                    warnings.append(
                        f'{name} 第 {no} 行「{step}」通过判断引用「设计值」，'
                        f'但本行没有设计值表头 → 提交「通过」时会被拦下（无法判定）')

        if len(rows) < 2 or rows[-1][1] != '是否通过':
            errors.append(f'{name} 最后一行必须固定为「是否通过」')
        elif '不通过' not in str(rows[-2][3]):
            errors.append(f'{name}「是否通过」前一行必须是备注（不通过时必填 ≥2 汉字）')

    print(f'\n共 {len([n for n in wb.sheetnames if n.startswith("流程-")])} 个流程 / {total} 个环节')
    if warnings:
        print('\n提示：')
        for w in warnings:
            print('  - ' + w)
    if errors:
        print('\n校验未通过：')
        for e in errors:
            print('  - ' + e)
        return False
    print('校验通过：流程路由、设计值表头引用、选项首选、结论行均正常。')
    return True


def rewrite_doc(path):
    """按当前流程表重写「说明」页（只动说明，不碰数据 / 流程 / 参数）。

    为什么单独一条命令：说明页是给业务员和 AI 看的约定，改 DSL 后必须同步；
    手工改单元格容易漏行、串行，重生成一次就对。
    """
    wb = load_workbook(path)
    names = [n for n in wb.sheetnames if n.startswith('流程-')]
    if not names:
        raise SystemExit('没有「流程-」sheet，不是 v2 模板')
    if '说明' in wb.sheetnames:
        wb.remove(wb['说明'])
    write_doc(wb, os.path.basename(path), names)
    if '参数' in wb.sheetnames:
        wb.move_sheet('说明',
                      offset=wb.sheetnames.index('参数') - wb.sheetnames.index('说明'))
    tmp = path + '.tmp.xlsx'
    wb.save(tmp)
    os.replace(tmp, path)
    return names


def main(argv=None):
    ap = argparse.ArgumentParser(description='人工造林验收模板：生成 / 回读')
    sub = ap.add_subparsers(dest='cmd')

    b = sub.add_parser('build', help='从源表生成模板（源表已移出仓库，需 --src）')
    b.add_argument('--src', default=SRC_DEFAULT, help='原始台账（含 5 个数据 sheet）')
    b.add_argument('--out', default=OUT_DEFAULT)

    s = sub.add_parser('show', help='回读模板并校验')
    s.add_argument('--file', default=OUT_DEFAULT)

    p = sub.add_parser('params', help='给现有模板补/同步「参数」页（不覆盖已有值）')
    p.add_argument('--file', default=TPL_DEFAULT)
    p.add_argument('--force', action='store_true', help='所有 key 重置为程序默认值')

    d = sub.add_parser('doc', help='按当前流程表重写「说明」页（只动说明）')
    d.add_argument('--file', default=TPL_DEFAULT)

    args = ap.parse_args(argv)
    if args.cmd == 'show':
        ok = show_workbook(args.file)
        return 0 if ok else 1
    if args.cmd == 'doc':
        names = rewrite_doc(args.file)
        print(f'已重写「说明」页（{len(names)} 个流程）→ {args.file}')
        return 0
    if args.cmd == 'params':
        wb = load_workbook(args.file)
        if '设计值' not in wb.sheetnames:
            raise SystemExit('缺少「设计值」sheet，不是 v2 模板')
        headers = [c.value for c in wb['设计值'][1] if c.value]
        info = write_params(wb, headers, keep_existing=not args.force)
        tmp = args.file + '.tmp.xlsx'
        wb.save(tmp)
        os.replace(tmp, args.file)
        print(f'参数页已同步：{args.file}')
        print(f'  新增 {len(info["added"])} 个 key：{"、".join(info["added"]) or "—"}')
        print(f'  保留已有 {len(info["kept"])} 个：{"、".join(info["kept"]) or "—"}')
        return 0

    if not args.src or not os.path.exists(args.src):
        raise SystemExit('源表不存在（build 需要 5 个原始 sheet）；'
                         '只改现有模板的配置请用：python3 tools/tpl/tpl_tool.py params')
    info = build_workbook(args.src, args.out)
    print(f'生成：{info["out"]}')
    print(f'设计值：{len(info["rows"])} 行 × {len(info["headers"])} 列')
    print(f'流程路由：{len(info["routes"])} 条')
    print(f'流程：{", ".join(info["flow_names"])}')
    if info['conflicts']:
        print(f'警告：源表同名列有 {len(info["conflicts"])} 处冲突（已保留首次出现的值）')
        for x in info['conflicts'][:5]:
            print('  -', x)
    print('')
    return 0 if show_workbook(info['out']) else 1


if __name__ == '__main__':
    sys.exit(main())
