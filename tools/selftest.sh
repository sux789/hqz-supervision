#!/usr/bin/env bash
# 工具自测：改了 tools/ 必跑（模板也过一遍自己的防线）。
# 覆盖：脚本语法 / 闸门可跑且 JSON 合法 / 简报在预算内 / 任务卡重叠判定 / 钩子可执行
set -uo pipefail
cd "$(dirname "$0")/.."

PY="python3"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"
pass=0; fail=0
ok()   { pass=$((pass+1)); printf '  ✅ %s\n' "$1"; }
bad()  { fail=$((fail+1)); printf '  ❌ %s\n' "$1"; }

echo "== 1. shell 语法 =="
for f in tools/*.sh tools/hooks/*; do
  if bash -n "$f" 2>/dev/null; then ok "$f"; else bad "$f 语法错误"; fi
done

echo "== 2. python 语法 =="
if "$PY" -m py_compile tools/checks/*.py 2>/tmp/hqz-pycompile.err; then
  ok "tools/checks/*.py 编译通过"
else
  bad "python 编译失败：$(head -3 /tmp/hqz-pycompile.err)"
fi

echo "== 3. 闸门可跑 + JSON 契约 =="
if "$PY" tools/checks/gates.py --fast --json-only 2>/dev/null | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
assert d['schema_version']==1, 'schema_version'
assert d['overall'] in ('PASS','WARN','FAIL'), 'overall'
assert all('id' in g and 'status' in g for g in d['gates']), 'gates 契约'
assert all(g['status'] in ('PASS','FAIL','WARN','SKIP') for g in d['gates']), 'status 枚举'
print(len(d['gates']))
"; then ok "gates --json 契约成立"; else bad "gates JSON 契约不成立"; fi

echo "== 4. 简报预算 =="
if "$PY" tools/checks/brief.py --quiet >/tmp/hqz-brief.out 2>&1; then
  n=$(wc -c < doc/brief.md | tr -d ' ')
  if [ "$n" -le 4096 ]; then ok "brief ${n}B ≤ 4096B"; else bad "brief ${n}B 超预算"; fi
  grep -q "## 现在" doc/brief.md && grep -q "## 命令" doc/brief.md \
    && ok "brief 含「现在/命令」两段" || bad "brief 缺必需段落"
else
  bad "brief 生成失败：$(tail -1 /tmp/hqz-brief.out)"
fi

echo "== 5. 任务卡重叠判定 =="
if "$PY" - <<'PYEOF'
import sys
sys.path.insert(0, "tools/checks")
from tasks import overlaps
a = {"scope": [{"file": "main.py", "from": 100, "to": 200}]}
b = {"scope": [{"file": "main.py", "from": 180, "to": 260}]}
c = {"scope": [{"file": "main.py", "from": 300, "to": 400}]}
d = {"scope": [{"file": "main.py", "from": 0, "to": 0}]}
assert overlaps(a, b), "相邻区间应重叠"
assert not overlaps(a, c), "不重叠区间不应判冲突"
assert overlaps(a, d), "整文件与任何范围都冲突"
assert not overlaps(a, {"scope": [{"file": "app.js", "from": 1, "to": 9}]}), "不同文件不冲突"
sys.exit(0)
PYEOF
then ok "重叠判定 4 条断言"; else bad "重叠判定错误"; fi

echo "== 6. 钩子可执行 =="
for h in tools/hooks/*; do
  [ -x "$h" ] && ok "$h 可执行" || bad "$h 缺执行位"
done

echo ""
echo "==== 通过 $pass / 失败 $fail ===="
[ "$fail" -eq 0 ] || exit 1
