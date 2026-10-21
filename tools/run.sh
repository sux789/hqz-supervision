#!/usr/bin/env bash
# 唯一验证入口（DOD 的可执行化）：等价于 bash tools/ai.sh gates
# 用法: bash tools/run.sh [--fast] [--only=js,index] [--json-only]
exec bash "$(dirname "$0")/ai.sh" gates "$@"
