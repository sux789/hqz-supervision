# -*- coding: utf-8 -*-
"""参数 sheet 解析器（约束 C01/C02）。

语法约定：
- 表头固定 6 列：key | value | 类型 | 默认值 | 说明 | 示例
- 多值用半角 ';'；项内子字段用半角 '|'；目录 '/'；文件名段 '_'；占位符 '{{}}'
- 未知 key / 全角分隔符 / 占位符列名不在数据表头 → ParamError（带行号），不静默跳过

13 键：相片备注 / 相片文件名 / 目录 / 验收结果选项 / 可编辑列 / 不显示列 / 功能 / 搜索选项 /
     压缩最长边 / 压缩质量（doc/007 §6，可选，仅影响新拍照片）/
     unique-key（v0.23，唯一键列名，可选）/ 日志字段（v0.23，可选）/ 百分比列（v0.23，可选）
功能可选值：拍照、视频（v0.10）、轨迹、打卡

必填 key（v0.23 起按需）：`可编辑列`、`功能` 恒必填；`相片备注`/`相片文件名`/`目录`
  仅在「功能」含 拍照/视频 时必填——**纯填表、不拍照的 Excel 不必硬写三条拍照模板**。

unique-key（C11）：声明 data 表哪一列是唯一键。**未声明则不校验**（零改动兼容旧模板）。
  声明后该列必须非空且唯一，否则拒绝上传；且不得出现在「可编辑列」中（行标识会漂移）。
  可用 GET /api/workbooks/<id> 的 key_column 取回生效值，前端一律用它替代硬编码「小班号」。
日志字段（C11）：写验收变更日志时要审计的列，缺省 = 验收人/验收日期/验收时间/验收结果/验收备注。
百分比列（C11）：存 0-1 小数、需展示为百分比的列；缺省回落旧规则「列名含『强度』」。
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
    '压缩最长边': '数值',
    '压缩质量': '数值',
    # v0.23 通用化（C11）：唯一键 / 日志字段 / 百分比列
    'unique-key': '文本',
    '日志字段': '列表',
    '百分比列': '列表',
}

REQUIRED = ['可编辑列', '功能']

# 拍照相关三件套：**仅当「功能」含 拍照/视频 时必填**（v0.23 通用化）。
# 目的：一个纯填表、不拍照的 Excel 不必为了过校验而硬写三条拍照模板。
PHOTO_KEYS = ['相片备注', '相片文件名', '目录']
PHOTO_FEATURES = ('拍照', '视频')

# v0.23：缺省值（未声明时的回落，保证零改动兼容）
DEFAULT_UNIQUE_KEY = '小班号'          # 仅在前端/后端取"生效唯一键"时兜底；未声明不校验
DEFAULT_LOG_FIELDS = ['验收人', '验收日期', '验收时间', '验收结果', '验收备注']

# 占位符中的保留字（不要求是数据表头）
# sheet名称=当前数据sheet名；时间=YYYYMMDD_HHMMSS；拍照人=当前登录用户
RESERVED_PH = {'sheet名称', '时间', '拍照人'}

FULLWIDTH = {'｜': '|', '；': ';', '，': ','}

_LIST_KEYS = {'验收结果选项', '可编辑列', '不显示列', '功能', '日志字段', '百分比列'}

# 值必须是数据 sheet 表头的列表类 key（v0.23 起含 日志字段 / 百分比列）
_COL_KEYS = ('可编辑列', '不显示列', '日志字段', '百分比列')


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
                if key in _COL_KEYS and item not in headerset:
                    raise ParamError(f'参数 sheet 第{rno}行 key「{key}」：列「{item}」不在数据 sheet 表头中')
                if key == '功能' and item not in ('拍照', '视频', '轨迹', '打卡'):
                    raise ParamError(f'参数 sheet 第{rno}行 key「功能」：未知功能「{item}」（可选：拍照、视频、轨迹、打卡）')

        # unique-key（C11）：值必须是数据表头列名，且只有一个（不做多列联合）
        if key == 'unique-key' and value:
            if ';' in value or '|' in value:
                raise ParamError(
                    f'参数 sheet 第{rno}行 key「unique-key」：只能写 1 个列名，不支持多值（实际「{value}」）')
            if value not in headerset:
                raise ParamError(
                    f'参数 sheet 第{rno}行 key「unique-key」：列「{value}」不在数据 sheet 表头中'
                    f'（表头：{"、".join(headers)}）')

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

        # 数值类：正整数 / 0-1 小数
        if KEYS[key] == '数值' and value:
            try:
                n = float(value)
            except ValueError:
                raise ParamError(f'参数 sheet 第{rno}行 key「{key}」：value「{value}」不是数值')
            if key == '压缩最长边' and not (300 <= n <= 8000):
                raise ParamError(f'参数 sheet 第{rno}行 key「压缩最长边」：应在 300-8000 之间')
            if key == '压缩质量' and not (0.1 <= n <= 1):
                raise ParamError(f'参数 sheet 第{rno}行 key「压缩质量」：应在 0.1-1 之间')

        cfg[key] = value
        seen[key] = rno

    if not header_checked:
        raise ParamError('参数 sheet 为空：缺少标准表头 key|value|类型|默认值|说明|示例')

    for k in REQUIRED:
        if k not in seen:
            raise ParamError(f'参数 sheet 缺少必填 key「{k}」')

    # 拍照三件套按需必填（v0.23）：功能里开了 拍照/视频 才要求填模板
    feats = split_list(cfg.get('功能', ''))
    if any(f in feats for f in PHOTO_FEATURES):
        for k in PHOTO_KEYS:
            if k not in seen:
                raise ParamError(
                    f'参数 sheet 缺少必填 key「{k}」——「功能」已启用'
                    f'{"、".join(f for f in feats if f in PHOTO_FEATURES)}，拍照/视频需要它；'
                    f'若该模板不拍照，请把「功能」改为不含 拍照/视频')

    # C11：唯一键不得可编辑——否则用户改了列值，行标识（照片归档/日志/导出定位）全漂移
    uk = cfg.get('unique-key')
    if uk and uk in split_list(cfg.get('可编辑列', '')):
        raise ParamError(
            f'参数 sheet 第{seen.get("unique-key", "?")}行：唯一键「{uk}」不能出现在「可编辑列」中'
            f'（该列被改动会导致行标识漂移，请从可编辑列里去掉）')

    return cfg


def split_list(value):
    """按 C02 语法拆多值；空串返回 []。"""
    return [x.strip() for x in (value or '').split(';') if x.strip()]


# ── v0.23（C11）有效值助手：声明优先，未声明回落内置缺省 ──────────────

def unique_key_of(cfg):
    """参数声明的唯一键列名；未声明返回 ''（= 不启用唯一键校验，旧模板零改动）。"""
    return (cfg.get('unique-key') or '').strip()


def row_key_column(cfg):
    """行标识列名：声明优先，未声明回落「小班号」（保持旧行为）。"""
    return unique_key_of(cfg) or DEFAULT_UNIQUE_KEY


def log_fields_of(headers, cfg):
    """写变更日志要审计的列：声明优先，否则取内置 5 列中真实存在的列。"""
    declared = split_list(cfg.get('日志字段', ''))
    if declared:
        return declared
    return [h for h in DEFAULT_LOG_FIELDS if h in headers]


def percent_cols_of(headers, cfg):
    """需把 0-1 小数转百分比的列索引：声明优先，否则回落旧规则「列名含『强度』且不含 %」。"""
    declared = split_list(cfg.get('百分比列', ''))
    if declared:
        return {i for i, h in enumerate(headers) if h in declared}
    return {i for i, h in enumerate(headers) if '强度' in h and '%' not in h}


def check_unique_column(rows, headers, cfg):
    """唯一键校验（仅当参数声明了 unique-key）。

    返回 None = 通过或不校验；否则返回带行号的错误消息。
    行号用 Excel 实际行号（表头占第 1 行，数据从第 2 行起）。
    """
    col = unique_key_of(cfg)
    if not col:
        return None                      # 未声明 → 不校验（用户口径）
    if col not in headers:
        return f'唯一键列「{col}」不在数据 sheet 表头中'
    ci = headers.index(col)
    seen, dups, empties = {}, [], []
    for n, r in enumerate(rows, start=2):
        v = r[ci]
        if v in (None, ''):
            empties.append(n)
            continue
        k = str(v).strip()
        if k in seen:
            dups.append((k, seen[k], n))
        else:
            seen[k] = n
    if empties:
        head = '、'.join(f'第{n}行' for n in empties[:5])
        more = f'（共 {len(empties)} 行）' if len(empties) > 5 else ''
        return f'唯一键「{col}」不能为空：{head} 为空{more}'
    if dups:
        k, a, b = dups[0]
        more = f'；另有 {len(dups) - 1} 组重复' if len(dups) > 1 else ''
        return f'唯一键「{col}」重复：「{k}」同时出现在 第{a}行 与 第{b}行{more}'
    return None
