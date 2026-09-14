#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数解析与唯一键的单元测试（v0.23，C11）。

用法：python tests/test_unit.py      # 全绿退出码 0

只测纯函数，不碰数据库与网络；涉及真实模板/HTTP 的部分在 tests/test_e2e.py。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from param_parser import (  # noqa: E402
    ParamError, check_unique_column, log_fields_of, parse_params,
    percent_cols_of, row_key_column, unique_key_of)
from main import _pick_data_sheet  # noqa: E402

HDR = ['key', 'value', '类型', '默认值', '说明', '示例']
HEADERS = ['项目类型', '小班号', '标段', '验收人', '验收日期', '验收结果', '验收备注',
           '株数强度', '混交比例']

_pass = _fail = 0


def ok(name, cond, detail=''):
    global _pass, _fail
    _pass, _fail = _pass + bool(cond), _fail + (not cond)
    print(f'  {"✅" if cond else "❌"} {name}  {detail}')


def prows(*rows):
    """构造参数 sheet 行：[表头行] + 数据行（自动补足 6 列）。"""
    return [(1, HDR)] + [(i + 2, list(r) + [''] * (6 - len(r))) for i, r in enumerate(rows)]


def raises(name, fn, must_contain=''):
    try:
        fn()
        ok(name, False, '未报错（预期应报错）')
    except ParamError as e:
        ok(name, must_contain in str(e) if must_contain else True, str(e))


def passes(name, fn, check=None):
    try:
        r = fn()
    except ParamError as e:
        ok(name, False, str(e))
        return None
    ok(name, check(r) if check else True, repr(r))
    return r


# ── 参数三件套（A）──────────────────────────────────────────
print('=== A. 参数解析：必填与按需必填 ===')
FULL = [['可编辑列', '验收人;验收日期'], ['功能', '轨迹'],
        ['相片备注', '{{小班号}}'], ['相片文件名', '{{小班号}}_{{时间}}'],
        ['目录', '{{项目类型}}/{{小班号}}']]

passes('A1 三件套齐全 → 通过', lambda: parse_params(prows(*FULL), HEADERS))
raises('A2 缺「功能」→ 报错（恒必填）', lambda: parse_params(prows(*FULL[0:1]), HEADERS), '功能')
raises('A3 缺「可编辑列」→ 报错（恒必填）',
       lambda: parse_params(prows(*[[r for r in FULL if r[0] != '可编辑列']][0]), HEADERS), '可编辑列')
passes('A4 不拍照（功能=轨迹）且无三件套 → 通过（v0.23 起按需必填）',
       lambda: parse_params(prows(['可编辑列', '验收人'], ['功能', '轨迹']), HEADERS))
raises('A5 功能含拍照但无三件套 → 报错',
       lambda: parse_params(prows(['可编辑列', '验收人'], ['功能', '拍照']), HEADERS), '相片备注')
raises('A6 功能含视频但无三件套 → 报错',
       lambda: parse_params(prows(['可编辑列', '验收人'], ['功能', '视频']), HEADERS), '相片备注')
passes('A7 功能含拍照且三件套齐全 → 通过',
       lambda: parse_params(prows(['可编辑列', '验收人'], ['功能', '拍照;轨迹'],
                                  ['相片备注', '{{小班号}}'], ['相片文件名', '{{小班号}}'],
                                  ['目录', '{{项目类型}}']), HEADERS))
raises('A8 未知 key → 报错', lambda: parse_params(prows(*FULL, ['唯一键', '小班号']), HEADERS), '未知 key')
raises('A9 占位符不是表头 → 报错',
       lambda: parse_params(prows(*FULL, ['相片备注', '{{地块号}}']), HEADERS), '不是数据 sheet 表头')

# ── 唯一键（B）────────────────────────────────────────────
print('\n=== B. unique-key 声明与校验 ===')
UK = ['unique-key', '小班号']
passes('B1 unique-key=表头列 → 通过', lambda: parse_params(prows(*FULL, UK), HEADERS),
       lambda c: c['unique-key'] == '小班号')
raises('B2 指向不存在的列 → 报错',
       lambda: parse_params(prows(*FULL, ['unique-key', '地块号']), HEADERS), '不在数据 sheet 表头')
raises('B3 多值 → 报错',
       lambda: parse_params(prows(*FULL, ['unique-key', '小班号;标段']), HEADERS), '不支持多值')
raises('B4 出现在「可编辑列」里 → 报错',
       lambda: parse_params(prows(['可编辑列', '小班号;验收人'], ['功能', '轨迹'], UK), HEADERS), '不能出现在')
passes('B5 未声明 → 不校验（即使数据重复）', lambda: check_unique_column(
    [['T', 'X'], ['T', 'X']], HEADERS, {}), lambda r: r is None)
ok('B6 唯一键重复 → 返回带行号错误串',
   isinstance(check_unique_column([['T', 'X'], ['T', 'Y'], ['T', 'X']], HEADERS,
                                 {'unique-key': '小班号'}), str))
ok('B7 唯一键为空 → 返回带行号错误串',
   isinstance(check_unique_column([['T', ''], ['T', 'Y']], HEADERS,
                                 {'unique-key': '小班号'}), str))
ok('B8 全唯一 → None',
   check_unique_column([['T', 'X'], ['T', 'Y']], HEADERS, {'unique-key': '小班号'}) is None)
ok('B9 唯一键列不在表头 → 返回错误串',
   isinstance(check_unique_column([['T', 'X']], HEADERS, {'unique-key': '地块号'}), str))

# ── 生效值助手（C）────────────────────────────────────────
print('\n=== C. 生效值助手：声明优先 / 缺省回落 ===')
passes('C1 unique_key_of 未声明 → 空串', lambda: unique_key_of({}), lambda r: r == '')
passes('C2 row_key_column 未声明 → 回落小班号', lambda: row_key_column({}), lambda r: r == '小班号')
passes('C3 row_key_column 声明优先', lambda: row_key_column({'unique-key': '地块号'}),
       lambda r: r == '地块号')
passes('C4 log_fields_of 缺省 = 内置里真实存在的列',
       lambda: log_fields_of(HEADERS, {}),
       lambda r: r == ['验收人', '验收日期', '验收结果', '验收备注'])
passes('C5 log_fields_of 声明优先',
       lambda: log_fields_of(HEADERS, {'日志字段': '验收人;验收备注'}),
       lambda r: r == ['验收人', '验收备注'])
passes('C6 percent_cols_of 缺省 = 列名含「强度」', lambda: percent_cols_of(HEADERS, {}),
       lambda r: r == {7})
passes('C7 percent_cols_of 声明优先（可覆盖非强度列）',
       lambda: percent_cols_of(HEADERS, {'百分比列': '混交比例'}), lambda r: r == {8})

# ── 数据 sheet 定位（D）───────────────────────────────────
print('\n=== D. 数据 sheet 定位（C11）===')


def pick(sheets):
    try:
        return _pick_data_sheet(sheets)
    except ParamError as e:
        return f'ERR:{e}'


ok('D1 有 data → 取 data', pick(['data', '参数']) == 'data', pick(['data', '参数']))
ok('D2 有 data + 说明 → 仍取 data（多余页可容忍）',
   pick(['data', '说明', '参数']) == 'data', pick(['data', '说明', '参数']))
ok('D3 无 data、单非参数 sheet → 取它（旧模板兼容）',
   pick(['中幼林抚育验收', '参数']) == '中幼林抚育验收')
ok('D4 无 data、多非参数 sheet → 报错', pick(['甲', '乙', '参数']).startswith('ERR'))
ok('D5 只有参数 → 报错', pick(['参数']).startswith('ERR'))
ok('D6 歧义报错里列出全部 sheet 名', '甲' in pick(['甲', '乙', '参数']) and '乙' in pick(['甲', '乙', '参数']))

print('\n' + '=' * 62)
print(f'通过 {_pass} / 失败 {_fail}')
sys.exit(1 if _fail else 0)
