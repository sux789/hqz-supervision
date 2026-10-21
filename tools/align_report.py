#!/usr/bin/env python3
"""生成「人机对齐看板」HTML（纯本地、无外网依赖，file:// 直接可看）。
用法: python tools/align_report.py <项目根> [--regress 输出文件] [--gitlog F] [--gitdate F]
输出: 写到 stdout（由 tools/align.sh 重定向到 doc/align.html）

排序原则：人打开第一眼回答「现在什么情况、我该做什么」——状态灯与下一步在前，
术语表随后（词一致），明细（文档/工具/git）最后折叠。全部从仓库文件自动生成，
不手写——人与 AI 以同一份为准：词一致（GLOSSARY）/ 契约一致（BLOCKS/Cxx）/
完成一致（回归闸实况）。"""
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(sys.argv[1])
ARGS = sys.argv[2:]

CSS = """body{margin:0;padding:0 20px 64px;font:15px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#2c2c2a;background:#fff}
.wrap{max-width:1000px;margin:0 auto}
header{padding:26px 0 14px;border-bottom:2px solid #d3d1c7;margin-bottom:16px}
h1{margin:0 0 4px;font-size:22px}.meta{color:#5f5e5a;font-size:13px;margin:0}
.lights{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:16px 0}
.light{display:flex;align-items:center;gap:8px;background:#fafaf8;border:1px solid #d3d1c7;border-radius:10px;padding:12px 14px}
.dot{width:11px;height:11px;border-radius:50%;flex:none;background:#b4b2a9}
.ok{background:#1d9e75}.warn{background:#ef9f27}.bad{background:#e24b4a}
.light b{font-size:14px;display:block}.light span{font-size:12.5px;color:#5f5e5a}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:0 0 8px}
.metric{background:#fafaf8;border:1px solid #d3d1c7;border-radius:10px;padding:10px 12px}
.metric b{display:block;font-size:19px}.metric span{font-size:12px;color:#5f5e5a}
h2{font-size:17px;margin:28px 0 10px;padding-left:9px;border-left:4px solid #185fa5}
table{width:100%;border-collapse:collapse;font-size:13.5px}
td,th{border:1px solid #d3d1c7;padding:6px 9px;text-align:left;vertical-align:top}
th{background:#fafaf8}
pre{background:#f4f3ee;border:1px solid #d3d1c7;border-radius:8px;padding:10px 12px;font-size:12.5px;overflow-x:auto;white-space:pre-wrap}
.lead{padding:10px 14px;background:#fafaf8;border-left:3px solid #185fa5;font-size:13.5px;color:#444}"""


def read(name):
    p = ROOT / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def grab(text, label):
    m = re.search(r"\*\*%s\*\*：([^\n]*)" % re.escape(label), text)
    return m.group(1).strip() if m else ""


state = read("STATE.md")
constraints = read("CONSTRAINTS.md")
blocks = read("BLOCKS.md")
glossary = read("GLOSSARY.md")
instr = read("AI_INSTRUCTIONS.md")

version = (grab(state, "版本") or "?").split("｜")[0].strip()
updated = grab(state, "更新") or "?"
regress_line = grab(state, "回归") or ""
next_steps = grab(state, "下一步") or "（STATE.md 未填「下一步」）"
online = grab(state, "能跑") or ""

n_c = len(set(re.findall(r"\|\s*(C\d+)", constraints)))
n_b = len(set(re.findall(r"\|\s*(B\d+)", blocks)))
n_g = len(re.findall(r"^\|(?!\s*术语)", glossary, re.M)) - 2
n_docs = len(list((ROOT / "doc").glob("[0-9][0-9][0-9]-*.md")))
n_tools = len(list((ROOT / "tools").glob("*.sh"))) + len(list((ROOT / "tools").glob("*.py")))

regress_ok = "全绿" in regress_line and re.search(r"\d+\s*项", regress_line)
regress_html = f'<span class="dot ok"></span>' if regress_ok else '<span class="dot warn"></span>'
regress_detail = html.escape(regress_line) or "（STATE.md 未填回归行）"

# 闸门实况（派生视图：doc/health/last_gates.json）
gates_data, overall, gate_rows = {}, "未体检", ""
gp = ROOT / "doc" / "health" / "last_gates.json"
if gp.exists():
    try:
        gates_data = json.loads(gp.read_text(encoding="utf-8"))
        overall = gates_data.get("overall", "?")
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️"}
        gate_rows = "".join(
            f"<tr><td>{icon.get(g['status'], '?')} {html.escape(g['name'])}</td>"
            f"<td>{g['status']}</td><td>{html.escape(str(g['detail']))[:160]}</td>"
            f"<td>{g.get('ms', 0)}ms</td></tr>"
            for g in gates_data.get("gates", []))
    except (ValueError, KeyError):
        overall = "解析失败"

# 并行任务（C24：文件级租约）
tasks = []
tdir = ROOT / ".agent" / "tasks"
if tdir.is_dir():
    for f in sorted(tdir.glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            if t.get("status") != "done":
                tasks.append(t)
        except ValueError:
            pass
task_rows = "".join(
    f"<tr><td>{html.escape(t.get('id', ''))}</td><td>{t.get('status')}</td>"
    f"<td>{html.escape(t.get('owner') or '未认领')}</td>"
    f"<td><code>{html.escape(','.join(x['file'] + (':' + str(x['from']) + '-' + str(x['to']) if x.get('from') else '') for x in t.get('scope', [])))}</code></td>"
    f"<td>{html.escape(t.get('title', ''))}</td></tr>" for t in tasks)

gate_html = ""
if "--regress" in ARGS:
    gf = Path(ARGS[ARGS.index("--regress") + 1])
    gate_txt = gf.read_text(encoding="utf-8", errors="replace") if gf.exists() else ""
    ok = "✅" in gate_txt
    gate_html = f"""
<h2>回归闸实况（本次生成时实跑）</h2>
<div class="lead">{'✅ 全绿' if ok else '❌ 有失败（红着不许提交）'}</div>
<pre>{html.escape(gate_txt[-2500:])}</pre>"""

gitlog, gitdate = "", ""
if "--gitlog" in ARGS:
    gl = Path(ARGS[ARGS.index("--gitlog") + 1])
    gd = Path(ARGS[ARGS.index("--gitdate") + 1])
    gitlog = gl.read_text(encoding="utf-8", errors="replace") if gl.exists() else ""
    gitdate = gd.read_text(encoding="utf-8", errors="replace").strip() if gd.exists() else ""

rows = re.findall(r"^\| ([^|]+) \| ([^|]+) \| ([^|]+) \|$", glossary, re.M)
gloss_rows = "".join(
    f"<tr><td><b>{html.escape(a.strip())}</b></td><td>{html.escape(b.strip())}</td>"
    f"<td>{html.escape(c.strip())}</td></tr>" for a, b, c in rows)

todo_items = "".join(
    f"<li>{html.escape(s.strip(' ;、；'))}</li>"
    for s in re.split(r"[；;]|(?=\d+\.\s*\[)", next_steps) if s.strip(" ；;、"))
if not todo_items:
    todo_items = f"<li>{html.escape(next_steps)}</li>"

print(f"""<!doctype html><html lang="zh"><meta charset="utf-8">
<title>hqz-supervision 人机对齐看板</title><style>{CSS}</style>
<div class="wrap">
<header>
  <h1>hqz-supervision · 人机对齐看板</h1>
  <p class="meta">自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ 数据源：STATE / CONSTRAINTS / BLOCKS / GLOSSARY / git（不手写，跑 <code>bash tools/align.sh</code> 刷新）</p>
</header>

<div class="lights">
  <div class="light"><span class="dot ok"></span><div><b>版本 {html.escape(version)}</b><span>STATE 更新 {html.escape(updated)}</span></div></div>
  <div class="light">{regress_html}<div><b>回归闸</b><span>{regress_detail}</span></div></div>
  <div class="light"><span class="dot {'ok' if overall == 'PASS' else ('warn' if overall in ('WARN', '未体检') else 'bad')}"></span><div><b>闸门 {html.escape(overall)}</b><span>{html.escape((gates_data.get('generated_at') or '先跑 bash tools/run.sh'))}</span></div></div>
  <div class="light"><span class="dot ok"></span><div><b>线上</b><span>{html.escape('已上线' if 'bibook.top' in online else '未确认')}</span></div></div>
  <div class="light"><span class="dot {'ok' if n_docs >= n_b else 'warn'}"></span><div><b>文档同步</b><span>{n_docs} 份迭代文档 / {n_b} 块积木</span></div></div>
</div>

<h2>下一步（STATE.md）</h2>
<ul>{todo_items}</ul>

<div class="metrics">
  <div class="metric"><b>{n_c}</b><span>活跃约束 Cxx</span></div>
  <div class="metric"><b>{n_b}</b><span>登记积木 Bxx</span></div>
  <div class="metric"><b>{max(n_g, 0)}</b><span>术语（GLOSSARY）</span></div>
  <div class="metric"><b>{n_docs}</b><span>迭代文档 doc/NNN</span></div>
  <div class="metric"><b>{n_tools}</b><span>自动化工具 tools/</span></div>
</div>

<h2>闸门实况（<code>bash tools/run.sh</code>）</h2>
<table><tr><th>门</th><th>判定</th><th>详情</th><th>耗时</th></tr>{gate_rows or '<tr><td colspan=4>尚未体检</td></tr>'}</table>

<h2>并行任务（文件级租约，C24）</h2>
<table><tr><th>任务</th><th>状态</th><th>负责人</th><th>范围</th><th>标题</th></tr>{task_rows or '<tr><td colspan=5>无进行中任务（单 agent 模式）</td></tr>'}</table>

{gate_html}

<h2>术语表（词一致 = 对齐第一锚点）</h2>
<table><tr><th>术语</th><th>一句话判定</th><th>权威出处</th></tr>{gloss_rows}</table>

<h2>协作入口（任何 agent 按同样方式工作）</h2>
<div class="lead">开工只读一页：<code>bash tools/ai.sh brief</code>（4KB）；方法与判据见 <b>doc/024-AI高效协作手册</b>；
「做完」由 <code>bash tools/run.sh</code> 判定（不靠任何人宣布）；并行先建任务卡 <code>bash tools/ai.sh task new</code>。
工具：<code>tools/ai.sh</code>（brief/explain/triage/task/map）· <code>tools/dev.sh</code>（起停服务）· <code>tools/snapshot.sh</code>（快照）· <code>tools/log.sh</code>（三层日志）· <code>tools/check_template.py</code>（模板体检）</div>

{'<h2>最近提交</h2><pre>' + html.escape(gitlog + ("\\n" + gitdate if gitdate else "")) + "</pre>" if gitlog else ""}
</div></html>""")
