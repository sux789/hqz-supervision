"""公共小工具：仓库定位、读写、命令执行、表格解析。

约束：只用标准库（服务器 shared_venv 没有第三方包）。
所有脚本通过 __file__ 反推仓库根，**不硬编码绝对路径**（目录名含零宽字符）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENTRY_FILES = [
    "AGENTS.md",
    "AI_INSTRUCTIONS.md",
    "CONSTRAINTS.md",
    "BLOCKS.md",
    "GLOSSARY.md",
    "STATE.md",
]
BRIEF_PATH = "doc/brief.md"
REVIEW_PATH = "doc/review.md"
HEALTH_DIR = "doc/health"
GATES_JSON = "doc/health/last_gates.json"
TRIAGE_PATH = "doc/health/TRIAGE.md"
TASK_DIR = ".agent/tasks"

BRIEF_MAX_BYTES = 4096
ENTRY_MAX_BYTES = 36864

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".worktrees", "vendor",
    "build", ".gradle", ".idea", ".playwright-cli", "dist",
}


def p(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


def read(path: str, default: str = "") -> str:
    try:
        with open(p(path), "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def write(path: str, text: str) -> int:
    full = p(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    return len(text.encode("utf-8"))


def size(path: str) -> int:
    try:
        return os.path.getsize(p(path))
    except OSError:
        return 0


def exists(path: str) -> bool:
    return os.path.exists(p(path))


def sh(args, timeout: int = 900, env=None) -> tuple[int, str]:
    """跑命令，返回 (退出码, 合并输出)。不抛异常（除超时→124）。"""
    e = dict(os.environ)
    e.setdefault("PYTHONIOENCODING", "utf-8")
    if env:
        e.update(env)
    try:
        cp = subprocess.run(
            args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=e
        )
        return cp.returncode, (cp.stdout or "") + (cp.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）：{' '.join(args)}"
    except FileNotFoundError as exc:
        return 127, f"命令不存在：{exc}"


def git(*args: str, timeout: int = 60) -> str:
    rc, out = sh(["git", *args], timeout=timeout)
    return out.strip() if rc == 0 else ""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def fmt_ms(ms: int) -> str:
    return f"{ms}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


def table_rows(md: str) -> list[list[str]]:
    """解析 markdown 表格，返回去掉表头与分隔行的数据行（按未转义竖线切分）。"""
    rows = []
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("|") or not s.endswith("|"):
            continue
        cells = re.split(r"(?<!\\)\|", s)[1:-1]
        cells = [c.strip().replace("\\|", "|") for c in cells]
        if not cells or all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        if cells[0] in ("编号", "术语", "资产", "门", "档", "#"):
            continue
        rows.append(cells)
    return rows


def strip_inline_code(line: str) -> str:
    return re.sub(r"`[^`]*`", "", line)


def walk_files(suffixes: tuple[str, ...] = ()) -> list[str]:
    """遍历仓库文件（相对路径），跳过噪音目录。"""
    out = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if suffixes and not name.endswith(suffixes):
                continue
            out.append(os.path.relpath(os.path.join(base, name), ROOT))
    return sorted(out)


def load_tasks() -> list[dict]:
    d = p(TASK_DIR)
    tasks = []
    if not os.path.isdir(d):
        return tasks
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), "r", encoding="utf-8") as f:
                t = json.load(f)
            t.setdefault("id", name[:-5])
            tasks.append(t)
        except (OSError, ValueError):
            continue
    return tasks


def parse_scope(text: str) -> list[dict]:
    """'main.py:100-200,static/app.js' → [{file, from, to}]"""
    out = []
    for part in re.split(r"[,，]", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.*?):(\d+)-(\d+)$", part)
        if m:
            out.append({"file": m.group(1), "from": int(m.group(2)), "to": int(m.group(3))})
        else:
            out.append({"file": part, "from": 0, "to": 0})
    return out


def main_guard(fn):
    try:
        sys.exit(fn())
    except KeyboardInterrupt:
        sys.exit(130)


class Timer:
    def __init__(self):
        self.t0 = time.time()

    @property
    def ms(self) -> int:
        return int((time.time() - self.t0) * 1000)
