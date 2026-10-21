#!/usr/bin/env python3
"""任务卡：多 Agent 并行的最小协调层（文件级所有权 + 行范围租约）。

为什么不是"再写一份分工文档"：文档不会阻止两个 agent 同时改 main.py。
任务卡把「谁在改什么、改到哪几行」变成可查询的记录，并在认领时**当场拒绝**范围重叠。

用法
  bash tools/ai.sh task new --title "修导出表头" --scope "main.py:1780-1830" --by codex
  bash tools/ai.sh task claim T-20261018-01 --by claude
  bash tools/ai.sh task list
  bash tools/ai.sh task check          # 有重叠 → 退出码 1
  bash tools/ai.sh task done T-20261018-01 --note "已合并 582d584"

存储：.agent/tasks/T-YYYYMMDD-NN.json（本地协调状态，不入 git）
派生视图：bash tools/ai.sh task board --md > doc/board.md
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

LOCK = ".agent/.lock"


def _ensure() -> str:
    d = C.p(C.TASK_DIR)
    os.makedirs(d, exist_ok=True)
    os.makedirs(C.p(".agent"), exist_ok=True)
    return d


class Lock:
    def __enter__(self):
        _ensure()
        self.f = open(C.p(LOCK), "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def _next_id() -> str:
    _ensure()
    prefix = "T-" + C.today().replace("-", "")
    n = 0
    for name in os.listdir(C.p(C.TASK_DIR)):
        m = re.match(prefix + r"-(\d+)\.json$", name)
        if m:
            n = max(n, int(m.group(1)))
    return f"{prefix}-{n + 1:02d}"


def _save(task: dict) -> None:
    _ensure()
    task["updated"] = C.now()
    with open(C.p(C.TASK_DIR, task["id"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(task, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _find(task_id: str) -> dict | None:
    for t in C.load_tasks():
        if t["id"] == task_id:
            return t
    return None


def overlaps(a: dict, b: dict) -> list[str]:
    """返回两卡冲突的文件说明。整文件（0,0）与任何同文件范围都冲突。"""
    out = []
    for sa in a.get("scope", []):
        for sb in b.get("scope", []):
            if sa["file"] != sb["file"]:
                continue
            if not sa.get("from") or not sb.get("from"):
                out.append(sa["file"])
                continue
            if sa["from"] <= sb["to"] and sb["from"] <= sa["to"]:
                out.append(f"{sa['file']}:{max(sa['from'], sb['from'])}-{min(sa['to'], sb['to'])}")
    return out


def _against(task: dict, tasks: list[dict]) -> list[str]:
    """单个任务与其它 active 任务的冲突（认领前必须过这一关）。"""
    msgs = []
    for o in tasks:
        if o["id"] == task["id"] or o.get("status") != "active":
            continue
        for f in overlaps(task, o):
            msgs.append(f"{task['id']}({task.get('owner')}) ↔ {o['id']}({o.get('owner')}) 争用 {f}")
    return msgs


def conflicts(tasks: list[dict], ignore: str | None = None) -> list[str]:
    active = [t for t in tasks if t.get("status") == "active" and t["id"] != ignore]
    msgs = []
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            for f in overlaps(a, b):
                msgs.append(f"{a['id']}({a.get('owner')}) ↔ {b['id']}({b.get('owner')}) 争用 {f}")
    return msgs


def entry_file_warnings(task: dict) -> list[str]:
    warn = []
    for s in task.get("scope", []):
        if s["file"] in C.ENTRY_FILES:
            warn.append(f"{s['file']} 是入口文件：只许集成者改；worker 把建议写进任务卡 notes")
    return warn


# ---------------------------------------------------------------- 子命令

def cmd_new(args: dict) -> int:
    title = args.get("title") or ""
    if not title:
        print("用法: task new --title <标题> [--scope \"main.py:1-50,static/app.js\"] [--by 名字] [--accept \"命令\"]")
        return 2
    scope = C.parse_scope(args.get("scope", ""))
    if not scope:
        print("必须声明 --scope（并行协作的前提：先划边界）")
        return 2
    with Lock():
        t = {
            "id": _next_id(),
            "title": title,
            "status": "open",
            "owner": args.get("by") or "",
            "scope": scope,
            "acceptance": args.get("accept", "bash tools/run.sh"),
            "notes": args.get("note", ""),
            "created": C.now(),
        }
        _save(t)
        blocked = []
        if t["owner"]:
            blocked = _against(t, C.load_tasks())
            if not blocked:
                t["status"] = "active"
                _save(t)
    print(f"已建任务卡 {t['id']}（{t['status']}）范围：")
    for s in scope:
        print(f"  - {s['file']}" + (f":{s['from']}-{s['to']}" if s["from"] else "（整文件）"))
    for w in entry_file_warnings(t):
        print("⚠️ " + w)
    if blocked:
        print("⚠️ 与进行中的任务范围重叠，已建为 open（未授予租约）：")
        for m in blocked:
            print("  - " + m)
        print("  处理：改 --scope 写细行号，或等对方 task done 后再 claim")
    return 0


def cmd_claim(args: dict) -> int:
    tid = args.get("_", "")
    if not tid:
        print("用法: task claim <任务号> --by <名字>")
        return 2
    with Lock():
        t = _find(tid)
        if not t:
            print(f"无此任务：{tid}")
            return 2
        t.setdefault("scope", [])
        t.setdefault("owner", args.get("by") or "agent")
        others = _against(t, C.load_tasks())
        if others:
            print("拒绝认领：与进行中的任务范围重叠")
            for m in others:
                print("  - " + m)
            print("先把范围写细（行号）或等对方完成，不要两人同改一处。")
            return 1
        t["owner"] = args.get("by") or t.get("owner") or "agent"
        t["status"] = "active"
        _save(t)
    print(f"{tid} 已认领 → {t['owner']}（active）")
    return 0


def cmd_done(args: dict) -> int:
    tid = args.get("_", "")
    t = _find(tid) if tid else None
    if not t:
        print("用法: task done <任务号> [--note 说明]")
        return 2
    t["status"] = "done"
    if args.get("note"):
        t["notes"] = (t.get("notes", "") + "\n" + args["note"]).strip()
    _save(t)
    print(f"{tid} 已完成" + ("｜" + args["note"] if args.get("note") else ""))
    return 0


def cmd_list(_args: dict) -> int:
    tasks = C.load_tasks()
    if not tasks:
        print("暂无任务卡。开工：bash tools/ai.sh task new --title ... --scope ... --by ...")
        return 0
    print(f"{'任务号':<16}{'状态':<8}{'负责人':<10}{'范围':<40}标题")
    print("-" * 100)
    for t in sorted(tasks, key=lambda x: x["id"]):
        scope = ",".join(s["file"] + (f":{s['from']}-{s['to']}" if s.get("from") else "")
                         for s in t.get("scope", []))
        print(f"{t['id']:<16}{t.get('status', '?'):<8}{t.get('owner') or '-':<10}{scope[:38]:<40}"
              f"{t.get('title', '')[:40]}")
    msgs = conflicts(tasks)
    if msgs:
        print("\n⚠️ 范围重叠（必须人工消除）：")
        for m in msgs:
            print("  - " + m)
    return 0


def cmd_check(_args: dict) -> int:
    tasks = C.load_tasks()
    msgs = conflicts(tasks)
    active = [t for t in tasks if t.get("status") == "active"]
    if not active:
        print("无进行中任务（单 agent 模式）")
        return 0
    print(f"进行中 {len(active)} 个任务：" + "、".join(f"{t['id']}/{t.get('owner')}" for t in active))
    if msgs:
        print("❌ 范围重叠：")
        for m in msgs:
            print("  - " + m)
        return 1
    print("✅ 无范围重叠")
    return 0


def cmd_board(_args: dict) -> int:
    tasks = [t for t in C.load_tasks() if t.get("status") != "done"]
    print("| 任务 | 状态 | 负责人 | 范围 | 验收 | 标题 |")
    print("|---|---|---|---|---|---|")
    for t in sorted(tasks, key=lambda x: x["id"]):
        scope = "<br>".join(s["file"] + (f":{s['from']}-{s['to']}" if s.get("from") else "")
                            for s in t.get("scope", []))
        print(f"| {t['id']} | {t.get('status')} | {t.get('owner') or '—'} | `{scope}` | "
              f"`{t.get('acceptance', '')}` | {t.get('title', '')} |")
    if not tasks:
        print("| （空） | — | — | — | — | 先建任务卡 |")
    return 0


def parse_args(argv: list[str]) -> dict:
    out: dict = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[key] = argv[i + 1]
                i += 2
                continue
            out[key] = "1"
        elif "_" not in out:
            out["_"] = a
        i += 1
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print("用法: task new|claim|done|list|check|board ...")
        return 2
    cmd, rest = argv[0], argv[1:]
    args = parse_args(rest)
    return {
        "new": cmd_new, "claim": cmd_claim, "done": cmd_done,
        "list": cmd_list, "check": cmd_check, "board": cmd_board,
    }.get(cmd, lambda _a: (print(f"未知子命令 {cmd}"), 2)[1])(args)


if __name__ == "__main__":
    C.main_guard(lambda: main(sys.argv[1:]))
