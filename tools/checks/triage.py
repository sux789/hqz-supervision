#!/usr/bin/env python3
"""失败分诊：把 last_gates.json 的 FAIL 变成「事实 + 候选原因 + 处置顺序」。

纪律：只产证据，不产结论；不自动改代码、不自动重建基线（人拍板）。

用法
  python3 tools/checks/triage.py
  python3 tools/checks/triage.py --print
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

REMEDY = {
    "regression": [
        "先看失败用例名：单元（后端逻辑）/ e2e（接口与页面）/ EXIF（node）三类，定位到具体断言",
        "只跑单套复现：`python3 tests/test_unit.py` 或 `python3 tests/test_e2e.py`",
        "用 `git stash` 二分：改动前是否也红？红=环境/数据问题，不红=本次改动引入",
        "禁止改断言或删用例来变绿；断言不成立必须写出「为什么原口径错了」",
    ],
    "js": [
        "`node --check <文件>` 得到行号，先修语法再谈业务",
        "C22 命中：`??` → `x != null ? x : 默认`；`?.` → 显式判空；`replaceAll` → `split/join`",
        "改完 `grep -n '??\\|?\\.' static/*.js` 复核，再跑回归（node --check 不能证明老内核可用）",
    ],
    "index": [
        "BLOCKS 报文件不存在：改名/移动后同步 BLOCKS.md，或把该积木标为已退役",
        "doc 编号重复：改新文件的编号，不要改历史文件的编号（反转链会断）",
        "doc 引用不存在：确认是笔误还是文档被删；被删的要在 024 退役表登记",
    ],
    "placeholder": [
        "定位 `文件:行号`，把 `[中文占位]` 换成本项目真实内容",
        "确属正常（如文档里讲占位符语法）→ 行尾加 `<!-- ok -->`，不要放宽整门",
    ],
    "secret": [
        "立刻确认该文件是否已推到远端：已推 = 密钥视为泄露，先轮换再清历史",
        "签名/密钥文件：从 git 移除跟踪（`git rm --cached`）并确认 .gitignore 命中",
        "把新出现的模式补进 gates.py 的 SECRET_PATTERNS，避免下次再漏",
    ],
    "budget": [
        "看哪几个文件最大：入口文件只放「每会话必读」，决策史进 doc/、工具进 tools/",
        "单文件 >6KB 就拆（保留索引 + 链接到详解），不要靠删内容达标",
        "brief 超预算：精简 STATE 的「下一步」，或把长表格移进 doc/",
    ],
    "template": [
        "先跑 `python3 tools/check_template.py data/<文件>` 看 FATAL 明细",
        "模板 FATAL 不等于代码回归：在迭代文档记录「模板待修项」，不要为了让门变绿去改数据",
    ],
}


def main(argv: list[str]) -> int:
    if not C.exists(C.GATES_JSON):
        print("无体检结果：先跑 bash tools/run.sh")
        return 2
    try:
        data = json.loads(C.read(C.GATES_JSON))
    except ValueError:
        print("last_gates.json 解析失败（重跑 bash tools/run.sh）")
        return 2

    bad = [g for g in data.get("gates", []) if g["status"] == "FAIL"]
    if not bad:
        if C.exists(C.TRIAGE_PATH):
            os.remove(C.p(C.TRIAGE_PATH))
            print("全绿，已清除过期 TRIAGE.md")
        else:
            print("全绿，无需分诊")
        return 0

    lines = [
        "# 失败分诊单（自动生成，勿手改）",
        f"> {C.now()} ｜ 来源 `{C.GATES_JSON}` ｜ 总耗时 {data.get('elapsed_ms')}ms",
        "> 本文件只含**事实与候选原因**，结论与取舍由人定。",
        "",
        "## 红的门",
    ]
    for g in bad:
        lines.append(f"- **{g['name']}**（{g['id']}）：{g['detail']}")
    lines.append("")

    for i, g in enumerate(bad, 1):
        lines.append(f"## {i}. {g['name']} — 处置顺序")
        lines.append(f"**事实**：{g['detail']}")
        lines.append("**候选原因 / 动作**：")
        for j, step in enumerate(REMEDY.get(g["id"], ["未登记的门：先在 triage.py 的 REMEDY 里补上处置路径"]), 1):
            lines.append(f"{j}. {step}")
        lines.append(f"**单独复跑**：`python3 tools/checks/gates.py --only={g['id']}`")
        lines.append("")

    lines += [
        "## 收尾",
        "1. 修完重跑 `bash tools/run.sh`（全量）",
        "2. 差异/失败若归因到「口径」，先改 CONSTRAINTS + 迭代文档，再改代码",
        "3. 同一门连续 3 次红：把这条教训下沉成断言或钩子（024 §9）",
        "",
    ]
    text = "\n".join(lines)
    if "--print" in argv:
        print(text)
    else:
        C.write(C.TRIAGE_PATH, text)
        print(f"已生成 {C.TRIAGE_PATH}（{len(bad)} 门红）")
    return 1


if __name__ == "__main__":
    C.main_guard(lambda: main(sys.argv[1:]))
