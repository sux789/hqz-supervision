# bump: 见 git log；EXIF 测试用 node（4/4）
# 全量测试：模板体检 + 单元 + 端到端 + EXIF(node)。全绿退出码 0。
# 用法：bash tests/run_all.sh
set -e
cd "$(dirname "$0")/.."

PY="${PY:-python3}"
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi
# 解释器必须同时有 openpyxl + flask（e2e 要拉起 Flask test_client）。
# `tools/run.sh` 会把 PY 透传成系统 python3，系统 python3 常常有 openpyxl 没有 flask，
# 只查 openpyxl 会漏判 → 闸门报"0 通过 / 0 失败"（看着像没跑测试，其实是 import 就炸了）。
if ! "$PY" -c "import openpyxl, flask" >/dev/null 2>&1; then
  for _CAND in /Users/sux/.workbuddy/binaries/python/envs/default/bin/python \
               /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if [ -x "$_CAND" ] && "$_CAND" -c "import openpyxl, flask" >/dev/null 2>&1; then
      PY="$_CAND"; break
    fi
  done
fi
if ! "$PY" -c "import openpyxl, flask" >/dev/null 2>&1; then
  echo "❌ 找不到同时具备 openpyxl + flask 的 Python。请建 .venv 或 pip install -r requirements.txt" >&2
  exit 2
fi

echo "########## 1/5 模板体检（只读；非模板文件自动跳过）##########"
$PY tools/check_template.py data/*.xlsx || true   # 模板有 FATAL 不算测试失败，下面单列

echo
echo "########## 2/5 单元测试 ##########"
$PY tests/test_unit.py

echo
echo "########## 3/5 流程配置（业务专家填写版，v0.36）##########"
$PY tests/test_flow_config.py

echo
echo "########## 4/5 端到端测试 ##########"
$PY tests/test_e2e.py
$PY tests/test_flow_e2e.py

echo
echo "########## 5/5 照片 EXIF 读取/回写（node）##########"
NODE_BIN="${NODE:-node}"
if [ -x "/Users/sux/.workbuddy/binaries/node/versions/22.12.0/bin/node" ]; then
  NODE_BIN="/Users/sux/.workbuddy/binaries/node/versions/22.12.0/bin/node"
fi
"$NODE_BIN" tests/test_exif.js

echo
echo "✅ 全部通过（模板体检的 FATAL 数见上，属模板待修项）"
