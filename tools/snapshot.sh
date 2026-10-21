#!/usr/bin/env bash
# 可变数据一键快照/恢复（改库、改工作簿等危险操作前先 save，出事可回滚）
# 用法:
#   tools/snapshot.sh save [标签]           → data/.snapshots/snap_<时间>_<标签>/
#   tools/snapshot.sh list
#   tools/snapshot.sh restore <名字|latest>  覆盖当前 data（需输 y 确认，--force 跳过）
# 内容: app.sqlite3 + workbooks/ + tracks/ + pending/（logs/ 不含）
set -euo pipefail
cd "$(dirname "$0")/.."

SNAP_DIR="data/.snapshots"
ITEMS="app.sqlite3 workbooks tracks pending"

case "${1:-}" in
  save)
    tag="${2:-manual}"
    ts="$(date +%Y%m%d_%H%M%S)"
    dst="${SNAP_DIR}/snap_${ts}_${tag}"
    mkdir -p "${dst}"
    for it in ${ITEMS}; do
      [ -e "data/${it}" ] && cp -R "data/${it}" "${dst}/"
    done
    du -sh "${dst}" | awk '{print "✅ 快照: " $2 " (" $1 ")"}'
    ;;
  list)
    if [ ! -d "${SNAP_DIR}" ] || [ -z "$(ls -A "${SNAP_DIR}" 2>/dev/null)" ]; then
      echo "（无快照）"; exit 0
    fi
    for d in "${SNAP_DIR}"/*; do
      printf '%s  %s\n' "$(basename "$d")" "$(du -sh "$d" | cut -f1)"
    done
    ;;
  restore)
    name="${2:-}"
    [ -z "${name}" ] && { echo "用法: tools/snapshot.sh restore <名字|latest> [--force]"; exit 1; }
    [ "${name}" = "latest" ] && name="$(ls -1 "${SNAP_DIR}" 2>/dev/null | sort | tail -1)"
    [ -z "${name}" ] && { echo "❌ 无快照可恢复"; exit 1; }
    src="${SNAP_DIR}/${name}"
    [ -d "${src}" ] || { echo "❌ 快照不存在: ${src}"; exit 1; }
    echo "将用 ${name} 覆盖当前 data/（app.sqlite3 + workbooks/tracks/pending）"
    if [ "${3:-}" != "--force" ]; then
      read -r -p "确认覆盖？输 y 继续: " ok
      [ "${ok}" = "y" ] || { echo "已取消"; exit 1; }
    fi
    for it in ${ITEMS}; do
      if [ -e "${src}/${it}" ]; then
        rm -rf "data/${it}"
        cp -R "${src}/${it}" "data/${it}"
      fi
    done
    echo "✅ 已恢复: ${name}"
    ;;
  *)
    echo "用法: tools/snapshot.sh save [标签] | list | restore <名字|latest> [--force]"
    exit 1
    ;;
esac
