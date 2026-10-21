#!/usr/bin/env bash
# 本地服务一键起停（省去每次手动 python main.py + curl 探活）
# 用法: tools/dev.sh start|stop|status
#   start   未在跑则后台启动并探活，打印地址；已在跑直接报地址
#   stop    停掉 8720 端口上的本服务
#   status  查看运行状态
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=8720
URL="http://127.0.0.1:${PORT}"
OUT="data/.dev.out"

alive() { lsof -ti tcp:"${PORT}" >/dev/null 2>&1; }

health() {
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "${URL}/" || true)
  [ -n "${code}" ] && [ "${code}" != "000" ]
}

pick_py() {
  if [ -x ".venv/bin/python" ]; then echo ".venv/bin/python"; return; fi
  local c
  for c in /Users/sux/.workbuddy/binaries/python/envs/default/bin/python \
           /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if [ -x "$c" ] && "$c" -c "import openpyxl" >/dev/null 2>&1; then echo "$c"; return; fi
  done
  echo python3
}

case "${1:-status}" in
  start)
    if health; then
      echo "已在运行 → ${URL}"
      exit 0
    fi
    if alive; then
      echo "端口 ${PORT} 被其他进程占用：$(lsof -ti tcp:${PORT} | head -1)"; exit 1
    fi
    PY="$(pick_py)"
    echo "启动: ${PY} main.py（日志 ${OUT}）"
    nohup "${PY}" main.py > "${OUT}" 2>&1 &
    for _ in $(seq 1 30); do
      sleep 0.5
      if health; then echo "✅ 已就绪 → ${URL}"; exit 0; fi
    done
    echo "❌ 15s 内未探活，看启动输出："; tail -20 "${OUT}"; exit 1
    ;;
  stop)
    if alive; then
      lsof -ti tcp:"${PORT}" | xargs kill
      echo "已停止（端口 ${PORT}）"
    else
      echo "未在运行"
    fi
    ;;
  status)
    if health; then echo "运行中 → ${URL}"
    elif alive; then echo "端口 ${PORT} 被占用但未探活（可能还在启动或非本服务）"
    else echo "未运行"; fi
    ;;
  *)
    echo "用法: tools/dev.sh start|stop|status"; exit 1
    ;;
esac
