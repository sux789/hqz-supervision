#!/bin/bash
# 全量测试：模板体检 + 单元 + 端到端。全绿退出码 0。
# 用法：bash tests/run_all.sh
set -e
cd "$(dirname "$0")/.."

PY="${PY:-python3}"
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi

echo "########## 1/3 模板体检（只读；非模板文件自动跳过）##########"
$PY tools/check_template.py data/*.xlsx || true   # 模板有 FATAL 不算测试失败，下面单列

echo
echo "########## 2/3 单元测试 ##########"
$PY tests/test_unit.py

echo
echo "########## 3/3 端到端测试 ##########"
$PY tests/test_e2e.py

echo
echo "✅ 全部通过（模板体检的 FATAL 数见上，属模板待修项）"
