# bump: 见 git log；EXIF 测试用 node（4/4）
# 全量测试：模板体检 + 单元 + 端到端 + EXIF(node)。全绿退出码 0。
# 用法：bash tests/run_all.sh
set -e
cd "$(dirname "$0")/.."

PY="${PY:-python3}"
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi
# 当前解释器缺 openpyxl 时，自动退到受管 venv（省去每次手动 PY=…）
if ! "$PY" -c "import openpyxl" >/dev/null 2>&1; then
  for _CAND in /Users/sux/.workbuddy/binaries/python/envs/default/bin/python \
               /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if [ -x "$_CAND" ] && "$_CAND" -c "import openpyxl" >/dev/null 2>&1; then
      PY="$_CAND"; break
    fi
  done
fi

echo "########## 1/4 模板体检（只读；非模板文件自动跳过）##########"
$PY tools/check_template.py data/*.xlsx || true   # 模板有 FATAL 不算测试失败，下面单列

echo
echo "########## 2/4 单元测试 ##########"
$PY tests/test_unit.py

echo
echo "########## 3/4 端到端测试 ##########"
$PY tests/test_e2e.py

echo
echo "########## 4/4 照片 EXIF 读取/回写（node）##########"
NODE_BIN="${NODE:-node}"
if [ -x "/Users/sux/.workbuddy/binaries/node/versions/22.12.0/bin/node" ]; then
  NODE_BIN="/Users/sux/.workbuddy/binaries/node/versions/22.12.0/bin/node"
fi
"$NODE_BIN" tests/test_exif.js

echo
echo "✅ 全部通过（模板体检的 FATAL 数见上，属模板待修项）"
