# -*- coding: utf-8 -*-
"""flow_judge —— v2「通过判断」规则：模板写规则，代码只解释。

模板「流程-*」的控件列在数值类控件后面接 `→规则`：

    数值(株→≥设计值)            实测株数 ≥ 本小班设计值 → 正确
    数值(株→≥设计值×0.95)       允许 5% 误差
    数值(株→≥设计值-5)          允许差 5 株
    百分数(0-100→≥85)           成活率 ≥ 85% → 正确
    自动(公式→≥85)              自动算出的值同样可判

    选项(...)→首项通过          选项控件：首项=正确，其余=不正确（参数「选项判定」
                               可让整份模板默认启用；控件写 →不判定 退出）

规则语法（空格无关）：`比较符 阈值`，阈值可以是「设计值」或数字，
后面可再乘系数（×0.95）或加减偏移（-5）：

    ≥ 设计值 [× 系数 | +- 偏移]       ≥ 数字 [× 系数 | +- 偏移]

比较符：≥ > ≤ < =（>= <= 等价）。不写 `→` = 不自动判断，行为与旧模板一致。

设计值取「设计值表头」那一列在当前小班的值；模板这里留空或写了常量，
规则又引用了「设计值」→ 判定为「无法判定」，提交「通过」时会被拦下并说明原因。

为什么单独一个模块：语法解析和比较只在服务端权威实现一份（main.py 调用），
前端拿到的是已解析好的 op / 阈值 / 系数，不再各写一套正则；单测直接覆盖本文件。
"""
import re

_OPS = ('≥', '>', '≤', '<', '=')
_RULE_RE = re.compile(
    r'^(>=|≥|<=|≤|>|<|=)\s*(设计值|\d+(?:\.\d+)?)'
    r'(?:\s*[×*]\s*(\d+(?:\.\d+)?)|\s*([+-])\s*(\d+(?:\.\d+)?))?$')


def parse(text):
    """规则文本 → 结构化规则；看不懂返回 None（调用方负责报错/告警）。"""
    s = re.sub(r'\s+', '', str(text or ''))
    if not s:
        return None
    m = _RULE_RE.match(s)
    if not m:
        return None
    op = {'>=': '≥', '<=': '≤'}.get(m.group(1), m.group(1))
    ref = m.group(2)
    delta = None
    if m.group(4):
        delta = float(m.group(5)) * (1 if m.group(4) == '+' else -1)
    return {
        'op': op,
        'is_design': ref == '设计值',
        'value': None if ref == '设计值' else float(ref),
        'factor': float(m.group(3)) if m.group(3) else None,
        'delta': delta,
        'text': s,
    }


def fields(rule):
    """结构化规则 → 控件上要带出去的字段（前端只做算术，不再解析语法）。"""
    if not rule:
        return {}
    return {
        'judge': rule['text'],
        'judge_op': rule['op'],
        'judge_ref': '设计值' if rule['is_design'] else '数值',
        'judge_value': rule['value'],
        'judge_factor': rule['factor'],
        'judge_delta': rule['delta'],
    }


def fmt(x):
    """6076.0 → '6076'；99.5 → '99.5'（提示文案里不出现多余小数）。"""
    v = float(x)
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else ('%g' % round(v, 4))


def threshold(rule, design):
    """规则阈值；引用设计值但设计值缺失/非数字 → None。"""
    base = design if rule.get('is_design') else rule.get('value')
    if base is None or str(base).strip() == '':
        return None
    try:
        t = float(str(base).strip())
    except (TypeError, ValueError):
        return None
    if rule.get('factor') is not None:
        t *= rule['factor']
    if rule.get('delta') is not None:
        t += rule['delta']
    return t


def evaluate(rule, actual, design):
    """→ (ok, 说明)。ok=None 表示判不了（实测缺失 / 设计值缺失 / 非数字）。"""
    if not rule:
        return True, ''
    if actual is None or str(actual).strip() == '':
        return None, '实测值未填'
    try:
        a = float(str(actual).strip())
    except (TypeError, ValueError):
        return None, f'实测值「{actual}」不是数字'
    t = threshold(rule, design)
    if t is None:
        return None, '设计值缺失，无法判定'
    op = rule['op']
    ok = {'≥': a >= t, '>': a > t, '≤': a <= t, '<': a < t,
          '=': abs(a - t) < 1e-9}[op]
    if ok:
        return True, f'实测 {fmt(a)} {op} {fmt(t)}'
    return False, f'实测 {fmt(a)} 不满足「{op} {fmt(t)}」'


def evaluate_control(c, actual, design):
    """按控件上已解析的字段判定（main.py 用；字段来自 flow_tpl.fields）。"""
    if not c.get('judge'):
        return True, ''
    rule = {'op': c.get('judge_op') or '≥', 'is_design': c.get('judge_ref') == '设计值',
            'value': c.get('judge_value'), 'factor': c.get('judge_factor'),
            'delta': c.get('judge_delta')}
    return evaluate(rule, actual, design)


def evaluate_option(c, actual):
    """选项「首项通过」判定：首项=通过，其余=不通过；未选归必填，不在这里判。"""
    opts = c.get('options') or []
    if not opts:
        return None, '选项未配置'
    a = '' if actual is None else str(actual).strip()
    if a == '':
        return None, '未选择'
    first = str(opts[0]).strip()
    if a == first:
        return True, f'选择「{a}」'
    return False, f'选择「{a}」，首项「{first}」才算通过'
