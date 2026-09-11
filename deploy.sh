#!/bin/bash
# 部署骨架 —— 踩坑已固化（forest-data 实测）：
# 1) rsync --exclude 只写文件名，不带目录前缀（"app.db" 对，"web/app.db" 错）
# 2) 多 worker 部署必须共享 .secret_key 文件（落盘，不进 git），否则 session 互踢
# 3) 新服务器系统依赖（如 Pillow）写进下行注释，首次部署先装：
#    pip install pillow openpyxl
set -e

DEST=user@server:/path/to/app/   # ← 按项目改

rsync -avz \
  --exclude "*.db" \
  --exclude ".secret_key" \
  --exclude "__pycache__/" \
  --exclude "data/" \
  --exclude "doc/" \
  ./ "$DEST"

# ssh user@server "systemctl restart myapp"   # ← 按项目改重启方式
echo "部署完成 $(date '+%F %T')"
