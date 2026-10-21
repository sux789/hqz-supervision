#!/usr/bin/env python3
"""开场简报：把「现在什么情况 / 不能违反什么 / 下一步」压成 ≤4KB 的一页。

为什么需要它：跨会话恢复靠"重读 6 个入口文件"是最贵的做法（27KB，且漏掉关键 3 行）。
简报是**派生视图**：每次现算、不入 git、禁止手改（C23）。

用法
  python3 tools/checks/brief.py            # 写 doc/brief.md
  python3 tools/checks/brief.py --print    # 只打印
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

MAX = C.BRIEF_MAX_BYTES


def state_fields() -> dict[str, str]:
    out = {}
    for line in C.read("STATE.md").splitlines():
        for key, val in re.findall(r"\*\*(.+?)\*\*：([^｜]*)", line):
            out.setdefault(key.strip(), val.strip())
    return out


def business_line() -> str:
    m = re.search(r"\*\*业务一句话\*\*：(.*)", C.read("AI_INSTRUCTIONS.md"))
    if not m:
        return "（AI_INSTRUCTIONS 未填业务一句话）"
    line = re.sub(r"\*\*", "", m.group(1)).strip()
    line = re.split(r"——", line)[0]  # 破折号后是细节，简报只留定位
    return line[:120]


def iron_rules(limit: int = 6) -> list[str]:
    body = C.read("AI_INSTRUCTIONS.md")
    m = re.search(r"## 项目铁律.*?\n(.*?)\n##", body, re.S)
    if not m:
        return []
    rules = [ln.strip()[2:].strip() for ln in m.group(1).splitlines() if ln.strip().startswith("- ")]
    out = []
    for r in rules[:limit]:
        r = re.sub(r"（[^）]{20,}）", "", r)  # 去掉长括号补充，保留规则本体
        out.append(r[:88])
    return out


def constraints(limit: int = 30) -> list[str]:
    out = []
    for row in C.table_rows(C.read("CONSTRAINTS.md")):
        if len(row) < 2 or not re.match(r"^C\d{2}$", row[0]):
            continue
        rule = row[1].replace("**", "")
        rule = re.split(r"[；;]", rule)[0]
        if len(rule) > 30:
            rule = rule[:30] + "…"
        out.append(f"{row[0]} {rule}")
    return out[:limit]


def gates_line() -> tuple[str, list[str]]:
    if not C.exists(C.GATES_JSON):
        return "未体检（先跑 bash tools/run.sh）", []
    try:
        data = json.loads(C.read(C.GATES_JSON))
    except ValueError:
        return "last_gates.json 解析失败（重跑 bash tools/run.sh）", []
    bad = [f"{g['name']}({g['status']})" for g in data.get("gates", []) if g["status"] in ("FAIL", "WARN")]
    line = f"{data.get('overall')} @ {data.get('generated_at')} ({data.get('commit') or '-'})"
    if data.get("fast"):
        line += "（快查，未含回归）"
    if bad:
        line += " ｜ " + "、".join(bad)
    return line, bad


def task_lines() -> list[str]:
    tasks = [t for t in C.load_tasks() if t.get("status") in ("active", "open")]
    out = []
    for t in tasks:
        scope = ",".join(s["file"] + (f":{s['from']}-{s['to']}" if s.get("from") else "")
                         for s in t.get("scope", []))
        out.append(f"{t['id']} [{t.get('status')}/{t.get('owner') or '未认领'}] {t.get('title', '')[:40]}"
                   f" → {scope[:60]}")
    return out


def build() -> str:
    st = state_fields()
    overall, bad = gates_line()
    tasks = task_lines()

    head = [
        "# 开工简报（自动生成，勿手改）",
        f"> {C.now()} ｜ 生成：`bash tools/ai.sh brief` ｜ 预算 {MAX}B",
        "",
        "## 现在",
        f"- **业务**：{business_line()}",
        f"- **版本**：{st.get('版本', '?')} ｜ 业务 {st.get('业务', '?')} ｜ 更新 {st.get('更新', '?')} ｜ git {C.git('rev-parse', '--short', 'HEAD') or '-'}"
        f"{'（有未提交改动）' if C.git('status', '--porcelain') else ''}",
        f"- **闸门**：{overall}",
        f"- **回归**：{st.get('回归', '见 STATE')}",
        "",
        "## 必须知道（违反=返工）",
    ]
    body = [f"- {r}" for r in iron_rules()]

    tail = []
    if tasks:
        tail += ["", "## 活跃任务（并行时先认领再动手）"] + [f"- {t}" for t in tasks]
    tail += ["", "## 下一步"]
    nxt = st.get("下一步", "")
    tail += [f"- {nxt[:260]}" if nxt else "- STATE 未填下一步"]

    tail += [
        "",
        "## 命令",
        "- 体检：`bash tools/run.sh`（提交前 `--fast`）｜ 回归：`bash tests/run_all.sh`",
        "- 评审单：`bash tools/ai.sh explain` ｜ 分诊：`bash tools/ai.sh triage`",
        "- 并行任务：`bash tools/ai.sh task list` ｜ 定位：`bash tools/ai.sh map`",
        "",
        "## 按需回查（不要通读）",
        "- 改代码前：`doc/024-AI高效协作手册.md` §4（流水线）、§5（多 Agent 并行）",
        "- 约束细则：`doc/023-约束详解.md` ｜ 术语：`GLOSSARY.md` ｜ 积木：`BLOCKS.md`",
        "- 迭代模板：`doc/001-迭代文档模板.md` ｜ 参数规范：`doc/025-参数sheet配置规范.md`",
    ]

    cons = constraints()
    cons_block = ["", "## 约束索引（全文见 CONSTRAINTS.md）"] + [f"- {c}" for c in cons]

    # 预算控制：先砍约束索引尾部，再砍铁律尾部
    text = "\n".join(head + body + cons_block + tail) + "\n"
    while len(text.encode("utf-8")) > MAX and cons:
        cons = cons[:-2]
        cons_block = ["", "## 约束索引（全文见 CONSTRAINTS.md）"] + [f"- {c}" for c in cons]
        text = "\n".join(head + body + cons_block + tail) + "\n"
    while len(text.encode("utf-8")) > MAX and len(body) > 4:
        body = body[:-1]
        text = "\n".join(head + body + cons_block + tail) + "\n"
    if len(text.encode("utf-8")) > MAX:
        text = text.encode("utf-8")[: MAX - 80].decode("utf-8", "ignore") + "\n…（超预算已截断）\n"
    return text


def main(argv: list[str]) -> int:
    text = build()
    n = len(text.encode("utf-8"))
    if "--print" in argv:
        print(text)
    else:
        C.write(C.BRIEF_PATH, text)
        print(f"已生成 {C.BRIEF_PATH}（{n}B / {MAX}B）")
        if n > MAX:
            print("⚠️ 超预算：请精简 STATE 或约束表")
            return 1
    if "--quiet" not in argv:
        print("—" * 46)
        print(text if "--print" not in argv else "")
    return 0


if __name__ == "__main__":
    C.main_guard(lambda: main(sys.argv[1:]))
