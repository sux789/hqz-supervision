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
    # v0.25：后台「下载 Excel」的筛选弹框（字段|控件类型，只筛行）
    '导出筛选': '控件映射',
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
# v0.29.3 新增工作簿级保留字（便于把照片按工作簿分层，而不必把目录结构写死在代码里）：
#   工作簿id=工作簿编号；工作簿名=上传的 Excel 文件名（不含扩展名，适合做目录名）；工作簿文件名=含扩展名
RESERVED_PH = {'sheet名称', '时间', '拍照人', '工作簿id', '工作簿名', '工作簿文件名'}

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
                    raise ParamError(f'参数 sheet 第{rno}行 key「搜索选项」：控件类型「{parts[1]}」不支持'
                                     f'（select=下拉 / search=文本搜索）')

        # 导出筛选（v0.25）：字段|控件类型 —— 后台「下载 Excel」弹框里按值筛行
        # select=下拉单选（候选值取该列当前数据里出现过的不同值）；date=日期选择（单日等于）
        if key == '导出筛选' and value:
            for item in value.split(';'):
                item = item.strip()
                if not item:
                    continue
                parts = [p.strip() for p in item.split('|')]
                if len(parts) != 2:
                    raise ParamError(f'参数 sheet 第{rno}行 key「导出筛选」：项「{item}」格式应为 字段|控件类型')
                if parts[0] not in headerset:
                    raise ParamError(f'参数 sheet 第{rno}行 key「导出筛选」：字段「{parts[0]}」不在数据 sheet 表头中')
                if parts[1] not in ('select', 'date'):
                    raise ParamError(f'参数 sheet 第{rno}行 key「导出筛选」：控件类型「{parts[1]}」不支持'
                                     f'（select=下拉单选 / date=日期选择）')

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


# ── v0.25：后台「下载 Excel」的筛选（只筛行，C01 参数驱动）──────────────

def export_filters_of(headers, cfg):
    """导出筛选弹框的字段清单 → [(字段名, 控件类型)]，类型为 'select' / 'date'。

    只返回**当前工作簿表头里确实存在**的声明项 —— 用户口径："没有的字段不提示筛选"。
    （参数解析阶段已要求声明字段必须是表头；这里再按 headers 过滤一次是防御，
    用于"同一份参数被换过 sheet/被人工改过"的情况。）
    """
    out = []
    for item in split_list(cfg.get('导出筛选', '')):
        parts = [p.strip() for p in item.split('|')]
        if len(parts) != 2 or not parts[1]:
            continue
        field, kind = parts
        if field in headers and kind in ('select', 'date'):
            out.append((field, kind))
    return out


def filter_options(rows, headers, field, cfg=None, users=None):
    """某列的下拉候选值 —— **三路合并**，按「先枚举、后数据」的顺序去重。

    ① 该列若有对应的「<列>选项」参数（如 `验收结果选项` = 合格;不合格）→ 并入其枚举
       （与 App 内的下拉同源；**没人填过时也能选**，否则空数据的下拉是死的）
    ② 列名含「人」/「员」的列 → 并入系统用户名单
       （与 App 里"自动填当前登录用户"的既有约定一致；同样让空数据时可用）
    ③ 数据里已经出现过的非空值（**首次出现顺序**，不按字典序 —— 中文值按码位排会乱）

    `cfg` / `users` 不传时退化为"只看数据"（v0.25 的老行为）。
    """
    if field not in headers:
        return []
    out, seen = [], set()

    def add(v):
        v = '' if v is None else str(v).strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    if cfg:
        for v in split_list(cfg.get(f'{field}选项', '')):      # ① 参数枚举
            add(v)
    if users and re.search(r'[人员]', field):                  # ② 系统用户
        for u in users:
            add(u)
    i = headers.index(field)                                   # ③ 数据里出现过的值
    for r in rows:
        if i < len(r):
            add(r[i])
    return out


def filter_rows(rows, headers, filters, kinds=None):
    """按条件过滤数据行（只筛行、不筛列）。

    filters: {字段名: 期望值}（空串/None = 该条件不生效）
    kinds:   {字段名: 'select'|'date'}，缺省按 select 处理
    - select：整串精确匹配（去首尾空格）
    - date  ：取单元格文本前 10 位与 'YYYY-MM-DD' 比，兼容 '2026-09-15 08:30:00' 这种带时间的值
    未声明的、表头里没有的字段一律忽略（不会因为多传参数就筛空）。
    """
    kinds = kinds or {}
    active = {}
    for h, want in (filters or {}).items():
        w = '' if want is None else str(want).strip()
        if w and h in headers:
            active[headers.index(h)] = (w, kinds.get(h, 'select'))
    if not active:
        return rows
    out = []
    for r in rows:
        for i, (want, kind) in active.items():
            cell = '' if i >= len(r) or r[i] is None else str(r[i]).strip()
            ok = (cell[:10] == want[:10]) if kind == 'date' else (cell == want)
            if not ok:
                break
        else:
            out.append(r)
    return out


# ── v0.26：按唯一键合并更新（后台「更新 Excel」）──────────────────────

def _keep(v):
    """输出用：字符串去首尾空格，数值原样保留（不要把 108.87 变成 '108.87' 文本）。"""
    if v is None:
        return ''
    return v.strip() if isinstance(v, str) else v


def _txt(v):
    """比较/取键用。"""
    return '' if v is None else str(v).strip()


def merge_rows(old_headers, old_rows, new_headers, new_rows, key_col, preserve_cols):
    """把新 Excel 的数据行按唯一键**合并**进已有工作簿。

    口径（用户确认 + 一处必要细化）：
    - **以旧表行顺序为基准**：匹配上的行**原地**合并 → 老行的 row_idx 不变，
      变更日志（`accept_logs` 按 workbook_id + row_idx 定位）不会错位；
      新表多出来的行**追加到末尾**。
    - 旧表有、新表没有的行**保留**（不删，保命）。
    - 冲突取值：`preserve_cols`（人工在 App 里填过的列 = 旧表「可编辑列」∪ 新表「可编辑列」）
      的旧值**非空优先**；**其余列以新表为准** —— 否则"填到一半改 Excel"
      （改小班面积、修正原始数据、新增列取值）永远不会生效。
    - 按**列名**对齐（列可增删、可换序），输出列序 = 新表列序。

    返回 `(merged_rows, stats)`；`key_col` 必须两张表都有，否则抛 `ParamError`。
    """
    if key_col not in old_headers or key_col not in new_headers:
        raise ParamError(
            f'按唯一键更新要求「{key_col}」列在**被更新的工作簿**和**新上传的 Excel**里都存在' +
            f'（旧表头：{"、".join(old_headers)}；新表头：{"、".join(new_headers)}）')

    preserve = set(preserve_cols or ())
    oh = {h: i for i, h in enumerate(old_headers)}
    nh = {h: i for i, h in enumerate(new_headers)}
    ki_old, ki_new = oh[key_col], nh[key_col]

    def g(row, i):
        return row[i] if (i is not None and i < len(row)) else None

    old_only_cols = [h for h in old_headers if h not in nh]
    new_only_cols = [h for h in new_headers if h not in oh]

    # 新表按唯一键建索引；重复键无法判断"更新哪一行"，统计出来交给调用方决定
    new_by_key, dups = {}, []
    for i, r in enumerate(new_rows):
        k = _txt(g(r, ki_new))
        if not k:
            continue
        if k in new_by_key:
            dups.append(k)
        else:
            new_by_key[k] = i

    stats = {
        'matched': 0,            # 匹配上并按规则合并的行数
        'added': 0,              # 新表新增、追加到末尾的行数
        'kept_old_only': 0,      # 仅旧表有的行（保留）
        'conflicts': 0,          # 被"旧值非空优先"保住的冲突格子数
        'blanked_nonempty': 0,   # 非保护列：旧表有值但新表为空 → 会被清空的格子数（要提醒用户）
        'dropped_nonempty': 0,   # 旧表有值但新表已删该列 → 丢弃的有值格子数
        'old_only_cols': old_only_cols,
        'new_only_cols': new_only_cols,
        'preserve_cols': sorted(preserve),
        'dup_new_keys': sorted(set(dups))[:5],
        'dup_new_key_count': len(set(dups)),
    }

    merged, used = [], set()
    for r in old_rows:
        ni = new_by_key.get(_txt(g(r, ki_old)))
        if ni is not None and ni not in used:
            used.add(ni)
            src = new_rows[ni]
            row = []
            for h in new_headers:
                nv, ov = g(src, nh[h]), g(r, oh.get(h))
                if h in preserve and _txt(ov) != '':
                    if _txt(nv) != '' and _txt(nv) != _txt(ov):
                        stats['conflicts'] += 1
                    row.append(_keep(ov))       # 人工填写过的列：旧值非空优先
                else:
                    if _txt(nv) == '' and _txt(ov) != '':
                        stats['blanked_nonempty'] += 1   # 旧值被新表的空值清掉 → 要提醒用户
                    row.append(_keep(nv))       # 其余列：以新表为准
            merged.append(row)
            stats['matched'] += 1
        else:
            merged.append([_keep(g(r, oh.get(h))) for h in new_headers])
            stats['kept_old_only'] += 1
        for h in old_only_cols:
            if _txt(g(r, oh[h])) != '':
                stats['dropped_nonempty'] += 1

    for i, r in enumerate(new_rows):
        if i not in used:
            merged.append([_keep(g(r, nh[h])) for h in new_headers])
            stats['added'] += 1

    return merged, stats


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
