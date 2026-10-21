#!/usr/bin/env python3
"""闸门体检：一条命令给出「现在能不能提交」。

设计要点
- 判定（PASS/FAIL/SKIP）与措辞分离：机器读 JSON 的 id+status，人读中文。
- 缺前置条件记 SKIP，不假装通过；连续 SKIP 由 --status 提示。
- 只做本项目真实需要的门：回归 / JS 语法 / 索引 / 占位符 / 密钥 / 上下文预算 / 模板体检。
  数据管线那套双跑对比（validate/compare/check_invariants）本项目没有「输出目录」，不接。

用法
  python3 tools/checks/gates.py                # 全量（含回归，约 7s）
  python3 tools/checks/gates.py --fast         # 跳过回归（提交前用，<2s）
  python3 tools/checks/gates.py --only=js,index
  python3 tools/checks/gates.py --json-only
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

SCHEMA_VERSION = 1
LEGACY_SYNTAX = [
    (r"\?\?", "?? 空值合并"),
    (r"\?\.", "?. 可选链"),
    (r"replaceAll\(", "String.replaceAll"),
    (r"\.at\(", "Array.at"),
]
SECRET_PATTERNS = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥块"),
    (r"AKIA[0-9A-Z]{16}", "AWS AccessKey"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "GitHub Token"),
    (r"sk-[A-Za-z0-9]{24,}", "OpenAI 风格 Key"),
]
SENSITIVE_FILES = (".jks", ".keystore", "keystore.properties", "keystore-info.txt", ".env")


# ---------------------------------------------------------------- 各门实现

def gate_regression() -> dict:
    t = C.Timer()
    rc, out = C.sh(["bash", "tests/run_all.sh"], timeout=900)
    passed = sum(int(a) for a, _ in re.findall(r"通过\s*(\d+)\s*/\s*失败\s*(\d+)", out))
    failed = sum(int(b) for _, b in re.findall(r"通过\s*(\d+)\s*/\s*失败\s*(\d+)", out))
    if rc == 0 and failed == 0:
        return dict(status="PASS", detail=f"{passed} 项全绿", ms=t.ms)
    tail = [ln for ln in out.splitlines() if "❌" in ln or "Error" in ln or "FAIL" in ln][-3:]
    return dict(status="FAIL", detail=f"{passed} 通过 / {failed} 失败：{'; '.join(tail)[:160]}", ms=t.ms)


def gate_js() -> dict:
    t = C.Timer()
    files = [f for f in sorted(glob.glob(C.p("static/*.js"))) if "/vendor/" not in f]
    if not files:
        return dict(status="SKIP", detail="static/ 下无 JS", ms=t.ms)
    node = shutil.which("node") or "/Users/sux/.workbuddy/binaries/node/versions/22.12.0/bin/node"
    if not os.path.exists(node) and not shutil.which("node"):
        return dict(status="SKIP", detail="无 node，跳过语法检查", ms=t.ms)

    bad, legacy = [], []
    for path in files:
        rel = os.path.relpath(path, C.ROOT)
        rc, out = C.sh([node, "--check", rel], timeout=60)
        if rc != 0:
            bad.append(f"{rel}: {out.strip().splitlines()[:1]}")
        src = C.read(rel)
        for i, line in enumerate(src.splitlines(), 1):
            if line.lstrip().startswith("//"):
                continue
            for pat, label in LEGACY_SYNTAX:
                if re.search(pat, line):
                    legacy.append(f"{rel}:{i} {label}")
    if bad:
        return dict(status="FAIL", detail="语法错误 " + "; ".join(bad)[:200], ms=t.ms)
    if legacy:
        return dict(status="FAIL",
                    detail=f"C22 旧内核不支持 {len(legacy)} 处：" + "; ".join(legacy[:3]), ms=t.ms)
    return dict(status="PASS", detail=f"{len(files)} 个文件语法通过，无 C22 禁用语法", ms=t.ms)


def _file_index() -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for rel in C.walk_files():
        idx.setdefault(os.path.basename(rel), []).append(rel)
    return idx


def gate_index() -> dict:
    t = C.Timer()
    idx = _file_index()
    problems = []
    checked = 0
    for row in C.table_rows(C.read("BLOCKS.md")):
        if len(row) < 3 or not row[0].startswith("B"):
            continue
        for token in re.findall(r"`([^`]+)`", row[2]):
            path = token.split("::")[0].strip()
            if not re.search(r"\.(py|js|java|html|sh|css|xlsx|md)$", path):
                continue
            checked += 1
            if os.sep in path or "/" in path:
                if not C.exists(path):
                    problems.append(f"BLOCKS {row[0]} 指向不存在的 {path}")
            elif path not in idx:
                problems.append(f"BLOCKS {row[0]} 文件 {path} 全仓未找到")

    doc_refs = set()
    for f in C.ENTRY_FILES:
        for num in re.findall(r"doc/(\d{3})", C.read(f)):
            doc_refs.add(num)
    for num in sorted(doc_refs):
        if not glob.glob(C.p(f"doc/{num}-*")):
            problems.append(f"引用了 doc/{num} 但文件不存在")

    seen: dict[str, list[str]] = {}
    for f in sorted(glob.glob(C.p("doc/*.md"))):
        m = re.match(r"(\d{3})-", os.path.basename(f))
        if m:
            seen.setdefault(m.group(1), []).append(os.path.basename(f))
    for num, names in sorted(seen.items()):
        if len(names) > 1:
            problems.append(f"doc 编号 {num} 重复：{', '.join(names)}")

    if problems:
        return dict(status="FAIL", detail="；".join(problems[:4]), ms=t.ms)
    return dict(status="PASS",
                detail=f"{checked} 个积木文件引用有效，{len(doc_refs)} 个 doc 编号可解析", ms=t.ms)


def gate_placeholder() -> dict:
    t = C.Timer()
    hits = []
    for f in C.ENTRY_FILES:
        for i, line in enumerate(C.read(f).splitlines(), 1):
            if "<!-- ok -->" in line:
                continue
            body = C.strip_inline_code(line)
            for m in re.finditer(r"\[([^\]\n]*[\u4e00-\u9fff][^\]\n]*)\]", body):
                if body[m.end():m.end() + 1] == "(":
                    continue
                hits.append(f"{f}:{i} {m.group(0)[:40]}")
    if hits:
        return dict(status="FAIL", detail=f"{len(hits)} 处未替换占位符：" + "; ".join(hits[:3]), ms=t.ms)
    return dict(status="PASS", detail=f"{len(C.ENTRY_FILES)} 个入口文件无残留占位符", ms=t.ms)


def gate_secret() -> dict:
    t = C.Timer()
    tracked = [f for f in C.git("ls-files").splitlines() if f.strip()]
    leaked = [f for f in tracked if f.endswith(SENSITIVE_FILES) or "/keystore" in f]
    if leaked:
        return dict(status="FAIL", detail="签名/密钥文件已入库：" + ", ".join(leaked[:5]), ms=t.ms)

    hits = []
    for rel in tracked:
        if rel.endswith((".png", ".jpg", ".jpeg", ".xlsx", ".apk", ".zip", ".jks", ".ttf")):
            continue
        if C.size(rel) > 512 * 1024:
            continue
        text = C.read(rel)
        for pat, label in SECRET_PATTERNS:
            if re.search(pat, text):
                hits.append(f"{rel}（{label}）")
    if hits:
        return dict(status="FAIL", detail="疑似密钥：" + ", ".join(hits[:5]), ms=t.ms)
    return dict(status="PASS", detail=f"{len(tracked)} 个入库文件无密钥模式", ms=t.ms)


def gate_budget() -> dict:
    t = C.Timer()
    sizes = [(f, C.size(f)) for f in C.ENTRY_FILES]
    total = sum(s for _, s in sizes)
    brief = C.size(C.BRIEF_PATH)
    big = [f"{f} {s // 1024}KB" for f, s in sorted(sizes, key=lambda x: -x[1])[:3]]
    problems = []
    if total > C.ENTRY_MAX_BYTES:
        problems.append(f"入口合计 {total // 1024}KB > {C.ENTRY_MAX_BYTES // 1024}KB")
    if brief and brief > C.BRIEF_MAX_BYTES:
        problems.append(f"brief {brief}B > {C.BRIEF_MAX_BYTES}B")
    if problems:
        return dict(status="FAIL", detail="；".join(problems) + f"（最大：{', '.join(big)}）", ms=t.ms)
    return dict(status="PASS",
                detail=f"入口 {total // 1024}KB/{C.ENTRY_MAX_BYTES // 1024}KB，brief "
                       f"{brief or '未生成'}B/{C.BRIEF_MAX_BYTES}B", ms=t.ms)


def gate_template() -> dict:
    t = C.Timer()
    files = sorted(glob.glob(C.p("data/*.xlsx")))
    if not files:
        return dict(status="SKIP", detail="data/ 下无 xlsx", ms=t.ms)
    cmd = [sys.executable, "tools/check_template.py"] + [os.path.basename(f) for f in files]
    # check_template.py 用相对 data/ 的路径解析，直接在仓库根跑
    rc, out = C.sh([sys.executable, "tools/check_template.py"] +
                   [os.path.relpath(f, C.ROOT) for f in files], timeout=180)
    # 只数真正的失败标记「[FATAL」——数 "FATAL" 会把「0 FATAL / 3 WARN」这类摘要行也计入
    fatal = out.count("[FATAL")
    if rc != 0 and "Traceback" in out:
        return dict(status="FAIL", detail="模板体检脚本异常：" + out.strip().splitlines()[-1][:120], ms=t.ms)
    if fatal:
        return dict(status="WARN",
                    detail=f"{len(files)} 个模板，FATAL {fatal} 条（模板待修，非代码回归）", ms=t.ms)
    return dict(status="PASS", detail=f"{len(files)} 个模板无 FATAL", ms=t.ms)


GATES = [
    ("regression", "回归", gate_regression),
    ("js", "JS 语法/C22", gate_js),
    ("index", "索引一致性", gate_index),
    ("placeholder", "占位符", gate_placeholder),
    ("secret", "密钥", gate_secret),
    ("budget", "上下文预算", gate_budget),
    ("template", "模板体检", gate_template),
]
ICON = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ ", "SKIP": "⏭️ "}


# ---------------------------------------------------------------- 输出

def main(argv: list[str]) -> int:
    fast = "--fast" in argv
    only = ""
    for a in argv:
        if a.startswith("--only="):
            only = a.split("=", 1)[1]
    selected = {s.strip() for s in only.split(",") if s.strip()}

    json_only = "--json-only" in argv
    overall_t = C.Timer()
    gates = []
    if not json_only:
        print()
    for gid, name, fn in GATES:
        if selected and gid not in selected:
            continue
        if fast and gid == "regression":
            gates.append(dict(id=gid, name=name, status="SKIP", detail="--fast 跳过", ms=0))
            continue
        if not json_only:
            print(f"  · {name} …", end="", flush=True)
        try:
            res = fn()
        except Exception as exc:  # 单门炸掉不拖垮整体
            res = dict(status="FAIL", detail=f"门自身异常：{type(exc).__name__}: {exc}", ms=0)
        res.update(id=gid, name=name)
        gates.append(res)
        if not json_only:
            print(f"\r  {ICON.get(res['status'], '?')} {name:<10} {res['detail'][:110]:<110} {C.fmt_ms(res['ms'])}")

    has_fail = any(g["status"] == "FAIL" for g in gates)
    has_warn = any(g["status"] == "WARN" for g in gates)
    overall = "FAIL" if has_fail else ("WARN" if has_warn else "PASS")
    elapsed = overall_t.ms

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": C.now(),
        "overall": overall,
        "elapsed_ms": elapsed,
        "commit": C.git("rev-parse", "--short", "HEAD"),
        "branch": C.git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(C.git("status", "--porcelain")),
        "fast": fast,
        "gates": gates,
    }
    if "--json-only" not in argv:
        if "--no-write" not in argv:
            C.write(C.GATES_JSON, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        target = "（--no-write，未落盘）" if "--no-write" in argv else f"→ {C.GATES_JSON}"
        print(f"\n  结论：{overall}   总耗时 {C.fmt_ms(elapsed)}   {target}")
        fails = [g for g in gates if g["status"] == "FAIL"]
        if fails:
            print("  下一步：bash tools/ai.sh triage   # 把失败整理成待判断项")
        elif any(g["status"] == "WARN" for g in gates):
            print("  有 WARN：可提交，但需在迭代文档里写明为什么不处理")
        else:
            print("  可以提交：bash tools/ai.sh explain 过一遍评审单")
        print()
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 1 if has_fail else 0


if __name__ == "__main__":
    C.main_guard(lambda: main(sys.argv[1:]))
