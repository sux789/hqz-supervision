#!/usr/bin/env python3
"""改动评审单：把 git diff 翻成「风险 / 影响面 / 必跑 / 待确认」，给人 30 秒看懂。

不是让模型评价代码好坏（那不可靠），而是**按规则算风险**：
改了哪个文件 → 命中哪些积木与约束 → 必须跑什么 → 有哪些只有人能拍板的问题。

用法
  python3 tools/checks/explain.py            # 写 doc/review.md
  python3 tools/checks/explain.py --print
  python3 tools/checks/explain.py --staged   # 只看已暂存（pre-commit 可复用）
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

ENTRY = set(C.ENTRY_FILES)
FILE_CONSTRAINTS = [
    (r"^main\.py$", ["C05", "C11", "C13", "C14", "C15", "C19"], "后端接口/录入/导出，改动面最大"),
    (r"^param_parser\.py$", ["C01", "C02", "C11", "C15"], "参数驱动契约本体"),
    (r"^flow_parser\.py$", ["C02", "C18"], "流程表解析"),
    (r"^static/app\.js$", ["C08", "C12", "C14", "C16", "C17", "C19", "C22"], "App 主脚本，老内核约束"),
    (r"^static/flow\.js$", ["C08", "C12", "C16", "C17", "C22"], "验收流程界面"),
    (r"^static/admin\.js$", ["C05", "C08", "C13", "C22"], "后台，危险操作确认"),
    (r"^static/photo_exif\.js$", ["C19", "C20"], "EXIF 回写"),
    (r"^templates/.*\.html$", ["C08", "C12", "C16"], "模板改动要同步 ?v="),
    (r"^android/", ["C09", "C10", "C21"], "原生改动必须出包（versionCode 递增）"),
    (r"^tests/", [], "改测试=可能放宽断言，必须说明为什么原断言不成立"),
    (r"^data/.*\.xlsx$", ["C02", "C11"], "模板是客户契约，改前先体检"),
    (r"^deploy\.sh$", [], "部署纪律：不碰数据库、先备份"),
    (r"^(AGENTS|AI_INSTRUCTIONS|CONSTRAINTS|BLOCKS|GLOSSARY|STATE)\.md$", [],
     "入口文件=所有人的上下文，只许集成者改"),
]
REQUIRED = {
    "js": "bash tests/run_all.sh（前端改动必须实跑；并确认 index.html 的 ?v= 已递增）",
    "py": "bash tests/run_all.sh（单元 + e2e）",
    "android": "改 versionCode → push tag → `gh release view` 复核直链（STATE 发版铁律）",
    "xlsx": "python3 tools/check_template.py data/*.xlsx",
    "html": "本地 `bash tools/dev.sh start` 后用 test_client 渲染一遍再部署",
    "data": "线上改 data/ 前：bash tools/snapshot.sh save + 备份线上库",
}


def parse_status(staged_only: bool) -> list[dict]:
    # 注意：不能用 C.git()——它会 strip 掉首行的前导空格，把 " M x" 误读成 "M  x"
    _rc, out = C.sh(["git", "status", "--porcelain"])
    rows = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        if "->" in path:
            path = path.split("->")[-1].strip()
        path = path.strip('"')
        if staged_only and code[0] in " ?":
            continue
        rows.append({"code": code, "path": path})
    return rows


def numstat() -> dict[str, tuple[int, int]]:
    out = C.git("diff", "HEAD", "--numstat")
    res = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            try:
                res[parts[2]] = (int(parts[0]), int(parts[1]))
            except ValueError:
                res[parts[2]] = (0, 0)
    return res


def blocks_map() -> dict[str, str]:
    m = {}
    for row in C.table_rows(C.read("BLOCKS.md")):
        if len(row) < 3 or not row[0].startswith("B"):
            continue
        for token in re.findall(r"`([^`]+)`", row[2]):
            base = os.path.basename(token.split("::")[0])
            m.setdefault(base, row[0])
    return m


def constraints_for(path: str) -> list[str]:
    for pat, ids, _ in FILE_CONSTRAINTS:
        if re.search(pat, path):
            return ids
    return []


def classify(changes: list[dict], stats: dict) -> tuple[str, list[str]]:
    reasons, level = [], "LOW"
    files = [c["path"] for c in changes]
    added = sum(a for a, _ in stats.values())
    removed = sum(d for _, d in stats.values())
    total = added + removed

    def bump(new):
        order = {"LOW": 0, "MED": 1, "HIGH": 2}
        return new if order[new] > order[level] else level

    if any(c["code"].strip().startswith("D") for c in changes):
        level = bump("HIGH"); reasons.append("有文件被删除（需进退役表并说明理由）")
    if any(f.startswith("android/") for f in files):
        level = bump("HIGH"); reasons.append("原生改动：网页修不了，必须出包")
    if any(f.startswith("tests/") for f in files):
        level = bump("HIGH"); reasons.append("测试被改：可能放宽了断言")
    if any(f in ENTRY for f in files):
        level = bump("HIGH"); reasons.append("入口文件被改：所有会话的上下文变了")
    if any(f.startswith("data/") and f.endswith(".xlsx") for f in files):
        level = bump("HIGH"); reasons.append("模板/数据被改：客户契约与线上工作簿受影响")
    if total > 300:
        level = bump("HIGH"); reasons.append(f"改动 {total} 行，超过一次评审的注意力预算")
    if any(f in ("main.py", "static/app.js") for f in files) and total > 120:
        level = bump("MED"); reasons.append("核心文件大改，必须逐段自审")
    if any(re.search(r"^(main\.py|static/.*\.(js|css)|templates/.*\.html|.*\.py)$", f) for f in files):
        level = bump("MED")
    if not reasons:
        reasons.append("仅文档/工具/低风险文件")
    return level, reasons


def build(staged_only: bool = False) -> tuple[str, dict]:
    changes = parse_status(staged_only)
    stats = numstat()
    bmap = blocks_map()
    total = sum(a + d for a, d in stats.values())
    level, reasons = classify(changes, stats)

    touched_blocks, touched_cons, required = {}, set(), set()
    for c in changes:
        f = c["path"]
        base = os.path.basename(f)
        if base in bmap:
            touched_blocks.setdefault(bmap[base], f)
        for cid in constraints_for(f):
            touched_cons.add(cid)
        if f.endswith((".js", ".css", ".html")) and f.startswith(("static/", "templates/")):
            required.add(REQUIRED["js"])
        elif f.endswith(".py"):
            required.add(REQUIRED["py"])
        elif f.startswith("android/"):
            required.add(REQUIRED["android"])
        elif f.endswith(".xlsx"):
            required.add(REQUIRED["xlsx"])
            required.add(REQUIRED["data"])
        elif f.startswith("templates/"):
            required.add(REQUIRED["html"])
    required.add("bash tools/run.sh（全量闸门）")

    questions = []
    files = [c["path"] for c in changes]
    if any(f.startswith("tests/") for f in files):
        questions.append("测试改动是否只是补新行为？原有断言为什么不再成立？（放宽断言=掩盖回归）")
    if any(c["code"].strip().startswith("D") for c in changes):
        questions.append("删除的文件是否已在 doc/024 退役表登记？是否有人还在引用？")
    if any(f in ENTRY for f in files):
        questions.append("入口文件改动后，是否重跑 `tools/ai.sh brief` 让所有人看到新版本？")
    if any(f.startswith("data/") for f in files):
        questions.append("线上已有工作簿用的是旧模板：本次改动是否需要「更新 Excel」而不是重新上传？")
    if "main.py" in files and total > 120:
        questions.append("main.py 单文件已 2000+ 行：本次是否应该先拆出积木再改？（拆与改不要同一版）")
    if not questions:
        questions.append("无（未命中需要人拍板的规则）")

    lines = [
        "# 改动评审单（自动生成，勿手改）",
        f"> {C.now()} ｜ `bash tools/ai.sh explain` ｜ git {C.git('rev-parse', '--short', 'HEAD') or '-'}",
        "",
        f"## 风险等级：{level}",
    ]
    lines += [f"- {r}" for r in reasons]
    lines += ["", f"## 变更清单（{len(changes)} 文件 / {total} 行）", "", "| 文件 | 状态 | +/- | 积木 | 约束 |", "|---|---|---|---|---|"]
    for c in changes:
        a, d = stats.get(c["path"], (0, 0))
        blk = bmap.get(os.path.basename(c["path"]), "—")
        cons = ",".join(constraints_for(c["path"])) or "—"
        lines.append(f"| `{c['path']}` | {c['code'].strip() or '?'} | +{a}/-{d} | {blk} | {cons} |")
    if not changes:
        lines.append("| （工作区干净） | — | — | — | — |")

    lines += ["", "## 必跑（跑完把结果贴进迭代文档）"]
    lines += [f"- [ ] {r}" for r in sorted(required)]
    lines += ["", "## 待拍板（人来答，AI 不代答）"]
    lines += [f"- {q}" for q in questions]
    lines += ["", "## 禁止项（命中即停手）",
              "- 手改派生视图（brief / review / TRIAGE / last_gates.json / align.html）",
              "- 改基线或放宽断言来让闸门变绿",
              "- 改 tools/hooks 后不跑 `bash tools/selftest.sh`",
              ""]
    if touched_blocks:
        lines.insert(6, "- 命中积木：" + "、".join(f"{k}({v})" for k, v in touched_blocks.items()))
    if touched_cons:
        lines.insert(7, "- 命中约束：" + "、".join(sorted(touched_cons)))
    text = "\n".join(lines) + "\n"
    meta = {"level": level, "files": len(changes), "lines": total, "constraints": sorted(touched_cons)}
    return text, meta


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    text, meta = build(staged)
    if "--print" in argv:
        print(text)
    else:
        C.write(C.REVIEW_PATH, text)
        print(f"已生成 {C.REVIEW_PATH} ｜ 风险 {meta['level']} ｜ "
              f"{meta['files']} 文件 / {meta['lines']} 行")
        if meta["constraints"]:
            print("命中约束：" + "、".join(meta["constraints"]))
        if meta["level"] == "HIGH":
            print("⚠️ HIGH：逐条回应「待拍板」后再提交；拿不准就先别提交")
    return 0


if __name__ == "__main__":
    C.main_guard(lambda: main(sys.argv[1:]))
