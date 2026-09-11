# -*- coding: utf-8 -*-
"""参数 sheet 解析器（约束 C01/C02）。

语法约定：
- 表头固定 6 列：key | value | 类型 | 默认值 | 说明 | 示例
- 多值用半角 ';'；项内子字段用半角 '|'；目录 '/'；文件名段 '_'；占位符 '{{}}'
- 未知 key / 全角分隔符 / 占位符列名不在数据表头 → ParamError（带行号），不静默跳过

8 键：相片备注 / 相片文件名 / 目录 / 验收结果选项 / 可编辑列 / 不显示列 / 功能 / 搜索选项
"""
import re

HEADER = ['key', 'value', '类型', '默认值', '说明', '示例']

# key -> 类型
KEYS = {
    '相片备注': '模板',
    '相片文件名': '模板',
    '目录': '模板',
    '验收结果选项': '列表',
    '可编辑列': '列表',
    '不显示列': '列表',
    '功能': '列表',
    '搜索选项': '控件映射',
}

REQUIRED = ['相片备注', '相片文件名', '目录', '可编辑列', '功能']

# 占位符中的保留字（不要求是数据表头）
RESERVED_PH = {'sheet名称', '时间'}

FULLWIDTH = {'｜': '|', '；': ';', '，': ','}

_LIST_KEYS = {'验收结果选项', '可编辑列', '不显示列', '功能'}


class ParamError(Exception):
    """参数 sheet 违规，message 已带行号。"""


def _s(v):
    return '' if v is None else str(v).strip()


def parse_params(param_rows, headers):
    """解析「参数」sheet。

    param_rows: [(row_no, [A..F 值]), ...]  含表头行（row 1）
    headers:    数据 sheet 表头列名列表（去空）
    返回 config dict；违规抛 ParamError。
    """
    headerset = set(headers)
    cfg = {k: '' for k in KEYS}
    seen = {}

    header_checked = False
    for rno, cells in param_rows:
        c = [ _s(x) for x in (list(cells) + [''] * 6)[:6] ]
        key, value = c[0], c[1]

        if not key:
            continue
        if not header_checked:
            # 第一条非空行必须是标准表头
            if [x.lower() for x in c[:2]] != ['key', 'value']:
                raise ParamError(f'参数 sheet 第{rno}行：不是标准表头（应为 key|value|类型|默认值|说明|示例），实际 A={key!r} B={value!r}')
            header_checked = True
            continue
        if key in HEADER:  # 表头重复行，跳过
            continue

        if key not in KEYS:
            raise ParamError(f'参数 sheet 第{rno}行：未知 key「{key}」（合法 key：{"、".join(KEYS)}）')

        for ch, rep in FULLWIDTH.items():
            if ch in value:
                raise ParamError(f'参数 sheet 第{rno}行 key「{key}」：value 含全角分隔符「{ch}」，应使用半角「{rep}」')

        # 模板类：占位符必须是数据表头或保留字
        if KEYS[key] == '模板':
            for ph in re.findall(r'\{\{(.+?)\}\}', value):
                if ph in RESERVED_PH:
                    continue
                if ph not in headerset:
                    raise ParamError(f'参数 sheet 第{rno}行 key「{key}」：占位符 {{{{{ph}}}}} 不是数据 sheet 表头（表头：{"、".join(headers)}）')
            if not value:
                raise ParamError(f'参数 sheet 第{rno}行 key「{key}」：必填模板不能为空')

        # 列表类：分号分隔项的校验
        if key in _LIST_KEYS and value:
            for item in value.split(';'):
                item = item.strip()
                if not item:
                    continue
                if key in ('可编辑列', '不显示列') and item not in headerset:
                    raise ParamError(f'参数 sheet 第{rno}行 key「{key}」：列「{item}」不在数据 sheet 表头中')
                if key == '功能' and item not in ('拍照', '轨迹', '打卡'):
                    raise ParamError(f'参数 sheet 第{rno}行 key「功能」：未知功能「{item}」（可选：拍照、轨迹、打卡）')

        # 控件映射：字段|控件类型
        if key == '搜索选项' and value:
            for item in value.split(';'):
                item = item.strip()
                if not item:
                    continue
                parts = [p.strip() for p in item.split('|')]
                if len(parts) != 2:
                    raise ParamError(f'参数 sheet 第{rno}行 key「搜索选项」：项「{item}」格式应为 字段|控件类型')
                if parts[0] not in headerset:
                    raise ParamError(f'参数 sheet 第{rno}行 key「搜索选项」：字段「{parts[0]}」不在数据 sheet 表头中')
                if parts[1] not in ('select', 'search'):
                    raise ParamError(f'参数 sheet 第{rno}行 key「搜索选项」：控件类型「{parts[1]}」不支持（select=search / select=下拉）')

        cfg[key] = value
        seen[key] = rno

    if not header_checked:
        raise ParamError('参数 sheet 为空：缺少标准表头 key|value|类型|默认值|说明|示例')

    for k in REQUIRED:
        if k not in seen:
            raise ParamError(f'参数 sheet 缺少必填 key「{k}」')

    return cfg


def split_list(value):
    """按 C02 语法拆多值；空串返回 []。"""
    return [x.strip() for x in (value or '').split(';') if x.strip()]
