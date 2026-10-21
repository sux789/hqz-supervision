#!/usr/bin/env bash
# 三层日志一键排查（v0.32.1，doc/021），省去手工 grep
# 用法: tools/log.sh [app|js|flow|exc|all] [-n N]   默认 all / N=30
#   app   服务端关键动作（data/logs/app.log）
#   js    前端 JS 错误（data/logs/js.log）
#   flow  验收提交各结局（app.log 中 [flow] 行）
#   exc   服务端未捕获异常（app.log 中 [EXC] 行）
set -euo pipefail
cd "$(dirname "$0")/.."

APP="data/logs/app.log"
JS="data/logs/js.log"
N=30
WHAT="all"
while [ $# -gt 0 ]; do
  case "$1" in
    app|js|flow|exc|all) WHAT="$1"; shift ;;
    -n) N="$2"; shift 2 ;;
    *) echo "用法: tools/log.sh [app|js|flow|exc|all] [-n N]"; exit 1 ;;
  esac
done

show() { # $1=标题 $2=文件 $3=额外grep(可空)
  echo "──────── $1 ────────"
  if [ ! -f "$2" ]; then echo "（无此日志文件）"; return; fi
  if [ -n "$3" ]; then
    grep "$3" "$2" | tail -n "${N}" || true
    grep -c "$3" "$2" | awk '{print "（共 " $1 " 条）"}' || true
  else
    tail -n "${N}" "$2"
  fi
}

case "${WHAT}" in
  app)  show "app.log 最近 ${N} 条" "${APP}" "" ;;
  js)   show "js.log 最近 ${N} 条" "${JS}" "" ;;
  flow) show "[flow] 最近 ${N} 条" "${APP}" "\[flow\]" ;;
  exc)  show "[EXC] 最近 ${N} 条" "${APP}" "\[EXC\]" ;;
  all)  show "js.log（前端错误）最近 ${N} 条" "${JS}" ""
        echo
        show "[EXC] 最近 ${N} 条" "${APP}" "\[EXC\]"
        echo
        show "[flow] 最近 ${N} 条" "${APP}" "\[flow\]" ;;
esac
