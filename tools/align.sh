#!/usr/bin/env bash
# 生成「人机对齐看板」HTML：纯本地、无外网依赖，file:// 直接打开。
# 人与 AI 以同一份为准：词一致（GLOSSARY）/ 契约一致（BLOCKS/Cxx）/ 完成一致（回归闸）。
# 用法:
#   bash tools/align.sh              # 快速生成（回归状态取自 STATE.md）
#   bash tools/align.sh --regress    # 先实跑 tests/run_all.sh 再生成（慢，但看板带闸门实况）
#   bash tools/align.sh --open       # 生成后用系统默认浏览器打开
set -uo pipefail
cd "$(dirname "$0")/.."

PY="python3"
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi

OPEN=0; DO_REGRESS=0
for a in "$@"; do
  case "$a" in
    --open) OPEN=1 ;;
    --regress) DO_REGRESS=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "未知参数: $a"; exit 2 ;;
  esac
done

mkdir -p doc
TMP="$(mktemp /tmp/hqz-align-regress.XXXXXX)"
trap 'rm -f "$TMP"' EXIT

if [ "$DO_REGRESS" = "1" ]; then
  echo "实跑回归闸（bash tests/run_all.sh）……"
  if bash tests/run_all.sh > "$TMP" 2>&1; then
    printf '\n✅ 回归闸全绿\n' >> "$TMP"
  else
    printf '\n❌ 回归闸有失败，不许提交\n' >> "$TMP"
  fi
  tail -15 "$TMP" || true
fi

git log --oneline -8 > /tmp/hqz-align-gitlog.$$ 2>/dev/null || : > /tmp/hqz-align-gitlog.$$
git log -1 --format=%cI > /tmp/hqz-align-gitdate.$$ 2>/dev/null || : > /tmp/hqz-align-gitdate.$$

"$PY" tools/align_report.py . \
  $( [ "$DO_REGRESS" = "1" ] && echo --regress "$TMP" ) \
  --gitlog /tmp/hqz-align-gitlog.$$ --gitdate /tmp/hqz-align-gitdate.$$ \
  > doc/align.html
rm -f /tmp/hqz-align-gitlog.$$ /tmp/hqz-align-gitdate.$$

echo "已生成: doc/align.html"
echo "file://$(pwd)/doc/align.html"
[ "$OPEN" = "1" ] && open doc/align.html
exit 0
