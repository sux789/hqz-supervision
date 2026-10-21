#!/usr/bin/env bash
# 人机交接统一入口：开场 / 评审 / 分诊 / 闸门 / 任务卡 / 符号地图
#
# 用法:
#   bash tools/ai.sh brief              开场简报 → doc/brief.md（AI 每会话第一读）
#   bash tools/ai.sh gates [--fast]     全量闸门 → doc/health/last_gates.json
#   bash tools/ai.sh explain            改动评审单 → doc/review.md
#   bash tools/ai.sh triage             失败分诊 → doc/health/TRIAGE.md
#   bash tools/ai.sh task <子命令>      并行任务卡（new/claim/done/list/check/board）
#   bash tools/ai.sh map                符号地图 → doc/map.md
#   bash tools/ai.sh help
#
# 纪律：本脚本只生成派生视图（brief/review/TRIAGE/json/map），禁止手改这些文件。
set -uo pipefail
cd "$(dirname "$0")/.."

PY="python3"
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi
if ! "$PY" -c "import sys" >/dev/null 2>&1; then PY="python3"; fi

cmd="${1:-help}"
[ $# -gt 0 ] && shift

case "$cmd" in
  brief)   exec "$PY" tools/checks/brief.py "$@" ;;
  gates)   exec "$PY" tools/checks/gates.py "$@" ;;
  run)     exec "$PY" tools/checks/gates.py "$@" ;;
  explain|review) exec "$PY" tools/checks/explain.py "$@" ;;
  triage|why)     exec "$PY" tools/checks/triage.py "$@" ;;
  task|tasks)     exec "$PY" tools/checks/tasks.py "$@" ;;
  map)     exec "$PY" tools/checks/map.py "$@" ;;
  help|-h|--help)
    awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"
    ;;
  *)
    echo "未知子命令: $cmd"; echo "用 bash tools/ai.sh help 看用法"; exit 2 ;;
esac
