#!/usr/bin/env bash
# supervision 部署同步（gateway 模型，模式同 hqz-survey；C03 不影响其他业务）
# 踩坑固化：rsync --exclude 用 '/'-锚定模式（模板教训：裸文件名/带目录前缀易错）
set -euo pipefail
REMOTE=${REMOTE:-www@forest.bibook.top}
DEST=${DEST:-/home/www/bibook_deploy/apps/supervision}
APPSPEC=${APPSPEC:-./deploy/supervision.appspec}

# 1. 同步应用代码（main.py / param_parser.py / sync_cloud.py / templates / static；数据目录不覆盖）
rsync -avz --exclude '__pycache__/' --exclude '*.pyc' --exclude '.DS_Store' \
  ./main.py ./param_parser.py ./sync_cloud.py ./track_export.py "$REMOTE:$DEST/"
rsync -avz --exclude '__pycache__/' --exclude '.DS_Store' ./templates/ "$REMOTE:$DEST/templates/"
rsync -avz --exclude '__pycache__/' --exclude '.DS_Store' ./static/ "$REMOTE:$DEST/static/"

# 2. appspec 丢进 gateway apps_root（新项目接入 = 一份 appspec，零改 gateway）
scp -q "$APPSPEC" "$REMOTE:/home/www/bibook_deploy/apps/gateway/apps_root/supervision.appspec"

# 3. 首页 apps 表登记（幂等：已存在则跳过）
ssh "$REMOTE" "sqlite3 /home/www/bibook_deploy/apps/forest-data/web/forest_web.db \
  'INSERT OR IGNORE INTO apps(code,name,description,base_path,is_active,created_at) \
   VALUES(\"supervision\",\"监督验收\",\"通用Excel网格填表+拍照轨迹\",\"/supervision\",1,datetime(\"now\",\"localtime\"))'"

# 4. 重启 gateway（影响面：所有 app 短暂重启，与 survey/cam 部署同风险等级）
ssh "$REMOTE" "systemctl --user restart gateway.service"

# 5. 生产验证
sleep 2
code=$(curl -s -o /dev/null -w "%{http_code}" https://forest.bibook.top/supervision)
echo "[deploy] synced to $DEST, gateway restarted; GET /supervision → $code"
