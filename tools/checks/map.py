#!/usr/bin/env python3
"""符号地图：给「三级坐标」提供行号——积木 → 文件:行 → 字段。

main.py 2000+ 行、app.js 1900+ 行，靠肉眼搜索定位的代价高且容易改错位置。
本工具生成「符号 → 行区间」表；并行开工时用它把任务卡范围写成具体行号。

用法
  python3 tools/checks/map.py            # 写 doc/map.md
  python3 tools/checks/map.py main.py    # 只打印某个文件的符号表
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

PY_TARGETS = ["main.py", "param_parser.py", "flow_parser.py", "track_export.py", "sync_cloud.py"]
PY_PAT = re.compile(
    r"^(?:async\s+)?def\s+(?P<fn>\w+)|^class\s+(?P<cls>\w+)|^@app\.(?:route|get|post|delete)\((?P<route>[^)]*)"
)
JS_PAT = re.compile(
    r"^(?:async\s+)?function\s+(?P<a>\w+)|^(?:const|let|var)\s+(?P<b>\w+)\s*=\s*(?:async\s*)?(?:function|\()"
    r"|^\s{1,4}(?:async\s+)?function\s+(?P<c>\w+)"
)


def js_files() -> list[str]:
    base = C.p("static")
    if not os.path.isdir(base):
        return []
    return sorted("static/" + f for f in os.listdir(base) if f.endswith(".js"))


def symbols(path: str) -> list[tuple[int, str]]:
    pat = JS_PAT if path.endswith(".js") else PY_PAT
    out = []
    for i, line in enumerate(C.read(path).splitlines(), 1):
        m = pat.match(line)
        if not m:
            continue
        if m.groupdict().get("route") is not None:
            name = "route " + m.group("route").strip().strip("'\"").split("'")[0].split('"')[0]
        else:
            name = next((v for v in m.groupdict().values() if v), "?")
        out.append((i, name))
    return out


def render(path: str) -> list[str]:
    syms = symbols(path)
    total = len(C.read(path).splitlines())
    lines = [f"### {path}（{total} 行，{len(syms)} 个符号）", "", "| 行 | 符号 | 行数 |", "|---|---|---|"]
    for idx, (ln, name) in enumerate(syms):
        end = (syms[idx + 1][0] - 1) if idx + 1 < len(syms) else total
        lines.append(f"| {ln} | `{name}` | {end - ln + 1} |")
    lines.append("")
    return lines


def main(argv: list[str]) -> int:
    targets = [a for a in argv if not a.startswith("-")] or PY_TARGETS + js_files()
    if len(targets) == 1 and "--print" in argv:
        print("\n".join(render(targets[0])))
        return 0
    lines = [
        "# 符号地图（自动生成，勿手改）",
        f"> {C.now()} ｜ `bash tools/ai.sh map` ｜ 用途：任务卡范围写 `文件:起始行-结束行`",
        "",
    ]
    for path in targets:
        if not C.exists(path):
            lines.append(f"### {path}（不存在）\n")
            continue
        lines += render(path)
    text = "\n".join(lines) + "\n"
    C.write("doc/map.md", text)
    print(f"已生成 doc/map.md（{len(targets)} 个文件，{len(text.encode('utf-8'))}B）")
    for path in targets[:6]:
        syms = symbols(path)
        print(f"  {path}: {len(syms)} 个符号，最大行 {len(C.read(path).splitlines())}")
    return 0


if __name__ == "__main__":
    C.main_guard(lambda: main(sys.argv[1:]))
