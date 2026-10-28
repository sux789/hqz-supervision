# -*- coding: utf-8 -*-
"""wb_parse —— 工作簿摄入（把上传的 xlsx 变成落库所需的纯数据）。

从 main.py 拆出的 B4/B5 积木：只做解析与归一化，不碰 Flask、不碰数据库。
约束：C01 参数驱动 / C02 参数语法 / C11 数据 sheet 定位与唯一键。
"""
import json
from datetime import date, datetime, time

import openpyxl

import flow_config
import flow_parser
import flow_tpl
from param_parser import (ParamError, _s, check_unique_column, parse_params,
                          percent_cols_of, row_key_column, KEYS as PARAM_KEYS)

PARAM_SHEET = '参数'   # 参数 sheet 固定名（C02）
DATA_SHEET = 'data'    # 数据 sheet 约定名（v0.23，C11）
# 非数据 sheet（v0.31，doc/019）：这些 sheet 不参与"选哪个当数据表"的判断
META_SHEETS = {PARAM_SHEET, flow_parser.FLOW_REGISTRY_SHEET, '使用说明', '因子控件对照',
               flow_tpl.ROUTE_SHEET, flow_tpl.DOC_SHEET}


def _norm_cell(v):
    """单元格归一化。

    注意：**必须先判 datetime 再判 date** —— datetime 是 date 的子类，
    且 str(datetime) 会得到 '2026-09-15 00:00:00'，会把日期列写成带时间的字符串
    （导出的 Excel 里日期是真日期单元格，回传时必须还原成 'YYYY-MM-DD'，否则数据被污染）。
    """
    if v is None:
        return ''
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d') if v.time() == time.min else v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, date):
        return v.strftime('%Y-%m-%d')
    # v0.31：纯时间值（datetime.time）。**不是时间，是被 number_format 误设成 h:mm 的数值**
    # —— 造林模板的「混交比例」列 number_format 被设成 h:mm，openpyxl 会把 0.29375
    # 读成 time(7,3,0)，落库就成了 '07:03:00'，界面上显示"混交比例 07:03:00"。
    # 处置：还原成当天的小数（time → 当天已过秒数 / 86400），让值回到 0.2916 附近。
    # 真正的日期/时间列有 datetime/date 类型，不会走到这里。
    if isinstance(v, time):
        secs = v.hour * 3600 + v.minute * 60 + v.second
        return round(secs / 86400.0, 10)
    if isinstance(v, (int, float)):
        return v
    return str(v).strip()


def _norm_rows(headers, rows, config=None):
    """株数强度/蓄积强度等：数值 0.18 存储统一规范化为文本 '18%'（doc/004 §二）。

    v0.23（C11）：转换哪些列由参数「百分比列」声明；未声明回落旧规则
    「列名含『强度』且不含 %」，故旧模板行为完全不变。
    """
    pct_idx = percent_cols_of(headers, config or {})
    out = []
    for r in rows:
        r = [_norm_cell(v) for v in r]
        for i in pct_idx:
            v = r[i]
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 1:
                r[i] = f'{v * 100:g}%'
        out.append(r)
    return out


def _is_flow_sheet(n):
    return n.startswith(flow_parser.FLOW_PREFIX)


def _pick_data_sheet(sheetnames):
    """定位数据 sheet（v0.23，C11）。

    优先精确取约定名「data」；没有则要求"有且仅有 1 个非参数 sheet"；
    多个非参数 sheet 时**明确报错**——旧写法 `next(n for n in sheetnames if n != '参数')`
    会静默取第一个，工作簿一带说明页/汇总页就取错表。

    v0.31（doc/019）：验收流程工作簿会带「验收流程」「流程-XXX」「使用说明」
    「因子控件对照」等说明性 sheet，它们**不是数据表**，必须排除后再判断，
    否则一份流程 Excel 会因为 sheet 太多而根本传不上去。

    v2 流程模板（flow_tpl）：第一个 sheet「设计值」就是数据表，直接认领。
    """
    if flow_tpl.DESIGN_SHEET in sheetnames and flow_tpl.ROUTE_SHEET in sheetnames:
        return flow_tpl.DESIGN_SHEET

    others = [n for n in sheetnames
              if n not in META_SHEETS and not _is_flow_sheet(n)]
    if not others:
        raise ParamError(f'上传的 Excel 只有「{PARAM_SHEET}」/流程/说明 sheet，没有数据 sheet')
    if DATA_SHEET in others:
        return DATA_SHEET
    if len(others) > 1:
        raise ParamError(
            f'无法判断用哪个数据 sheet：共有 {len(others)} 个非说明 sheet {others}。'
            f'请把数据 sheet 命名为「{DATA_SHEET}」，或只保留一个（其余说明/汇总页请删掉）')
    return others[0]


def _parse_flows(wb, headers=None, rows=None):
    """解析验收流程配置（v0.31 老格式 + v0.35 业务员填写版 + v2 模板，doc/019 / doc/027）。

    三套格式**各自落地、前端按 schema 分派**：
    - `流程-XXX` 12 列大宽表（程序员口径，flow_parser）
    - `①流程 / ②环节 / ③核验项` 业务员填写版（flow_config，优先识别）
    - `设计值 / 流程路由 / 流程-*` v2 模板（flow_tpl，界面为「选流程 → 整张表单」）

    流程配置**不阻断上传**：解析失败时记一条 warning 存进 flows_json['warn']，
    界面退回原网格填表 —— 现场不能因为"流程表写错了一个字"就填不了表。
    """
    if flow_tpl.is_tpl_doc(wb):
        try:
            res = flow_tpl.parse(wb, data_headers=headers)
        except flow_tpl.TplError as e:
            return json.dumps({'registry': [], 'flows': {}, 'warn': [
                {'level': 'error', 'text': 'v2 流程模板有错误，本次不生效：' + str(e)}]},
                ensure_ascii=False)
        except Exception as e:      # 结构异常也不能让整份 Excel 传不上来
            return json.dumps({'registry': [], 'flows': {}, 'warn': [
                {'level': 'error', 'text': 'v2 流程模板解析失败：' + str(e)}]},
                ensure_ascii=False)
        return json.dumps(res, ensure_ascii=False)

    if flow_config.is_user_doc(wb):
        res = flow_config.parse(wb, data_headers=headers)
        if res['errors']:
            return json.dumps({'registry': [], 'flows': {}, 'source': 'user_doc', 'warn': [
                {'level': 'error', 'text': '业务员填写版流程配置有 %d 处错误，本次不生效：\n%s'
                 % (len(res['errors']), flow_config.check_lines(res, limit=8))}]},
                ensure_ascii=False)
        out = {
            'schema': 'flow_config/1',
            'source': 'user_doc',
            'registry': res['registry'],
            'flows': res['flows'],
            'warn': [{'level': 'info', 'text': w['msg']} for w in res['warnings']],
        }
        return json.dumps(out, ensure_ascii=False)

    try:
        parsed = flow_parser.parse_flows(wb)
    except flow_parser.FlowError as e:
        return json.dumps({'registry': [], 'flows': {}, 'warn': [
            {'level': 'error', 'text': str(e)}]}, ensure_ascii=False)
    out = {
        'registry': parsed['registry'],
        'flows': parsed['flows'],
        'warn': [r['warn'] for r in parsed['registry'] if r.get('warn')],
    }
    # 配置体检（无条件流程 / 缺填报控件列 / 需要打卡功能 …）—— 让管理层在上传时就看到坑，
    # 而不是等现场人员填不了表才发现（doc/019 §五）
    if headers is not None and rows is not None:
        out['warn'] = out['warn'] + flow_parser.flow_warnings(
            parsed['registry'], parsed['flows'], rows, headers)
    return json.dumps(out, ensure_ascii=False)


def _tpl_default_config(headers):
    """v2 流程模板没有「参数」页时的缺省配置（零配置即可跑通）。

    只影响搜索栏与工具按钮：v2 的界面主体是「选流程 → 整张表单」，数据网格不显示，
    所以「可编辑列」为空；「功能」也为空（模板暂未声明拍照/轨迹）。
    """
    cfg = {k: '' for k in PARAM_KEYS}
    cfg['可编辑列'] = ''
    cfg['功能'] = ''
    names = [_s(h) for h in headers]
    # 级联顺序固定：项目类型 → 标段 → 乡（镇）→ 村（社区），小班最后（文本搜索）。
    # 模板带「参数」页时以模板值为准，这里只是没参数页时的兜底。
    sel = []
    for h in ('项目类型', '标段', '乡（镇）', '乡镇', '村（社区）', '村'):
        if h in names:
            sel.append(f'{h}|select')
    key = '小班号' if '小班号' in names else (names[0] if names else '')
    if key:
        sel.append(f'{key}|search')
    cfg['搜索选项'] = ';'.join(sel)
    return cfg


def parse_workbook_storage(stream):
    """解析上传的 xlsx：数据 sheet + 参数 sheet（+ 验收流程 sheet，v0.31）。

    返回 (sheet_name, headers, rows, config, param_rows, key_column, flows_json)。
    声明了「unique-key」时对唯一键做非空+唯一校验，不过则拒绝上传（C11）。
    """
    wb = openpyxl.load_workbook(stream, data_only=True, read_only=True)
    try:
        is_tpl = flow_tpl.is_tpl_doc(wb)
        if PARAM_SHEET not in wb.sheetnames and not is_tpl:
            raise ParamError(
                f'上传的 Excel 缺少「{PARAM_SHEET}」sheet（需同时含数据 sheet 和「{PARAM_SHEET}」sheet；'
                f'当前 sheet：{wb.sheetnames}）')
        data_name = _pick_data_sheet(wb.sheetnames)

        # v2 流程模板可以不带「参数」页：搜索栏/功能全部走缺省值（见 _tpl_default_config）
        prows = []
        if PARAM_SHEET in wb.sheetnames:
            ws_p = wb[PARAM_SHEET]
            prows = [(i, r) for i, r in enumerate(
                ws_p.iter_rows(max_col=6, values_only=True), 1)
                if any(x not in (None, '') for x in r)]

        ws_d = wb[data_name]
        it = ws_d.iter_rows(max_col=64, values_only=True)  # read_only 维度可能误报列数，强制扩列
        first = next(it, None)
        if first is None:
            raise ParamError(f'数据 sheet「{data_name}」为空')
        headers = [_s(h) for h in first]
        while headers and not headers[-1]:
            headers.pop()
        if not headers:
            raise ParamError(f'数据 sheet「{data_name}」缺少表头行')
        ncols = len(headers)
        rows = []
        for r in it:
            vals = list(r) + [None] * (ncols - len(r))
            if any(v not in (None, '') for v in vals[:ncols]):
                rows.append(vals[:ncols])

        config = parse_params(prows, headers) if prows else _tpl_default_config(headers)
        rows = _norm_rows(headers, rows, config)
        # C11：声明了 unique-key 才校验（未声明 = 不校验，旧模板零改动）
        uerr = check_unique_column(rows, headers, config)
        if uerr:
            raise ParamError(f'数据 sheet「{data_name}」：{uerr}')
        param_rows = [[_norm_cell(v) for v in (list(r) + [None] * 6)[:6]] for _, r in prows]
        # v0.31：流程 sheet 复用同一个 wb（read_only 模式下仍可按名取 sheet）
        flows_json = _parse_flows(wb, headers, rows)
        return (data_name, headers, rows, config, param_rows,
                row_key_column(config), flows_json)
    finally:
        wb.close()
