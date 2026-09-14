# hqz-supervision 长期记忆

- **当前线上版本 v0.23.1（2026-09-14 23:14 部署）**：https://forest.bibook.top/supervision 。部署前备份线上代码到 `/home/www/bibook_deploy/backups/supervision_<时间戳>.tar.gz`（回滚用）。部署走 `./deploy.sh`（rsync 代码 + 重启 gateway，**不碰数据库**）。
- **C12 多应用同域跳转铁律（v0.23.1）**：本项目与 survey/cam/forest 等**共用域名 forest.bibook.top，靠 gateway 按前缀分发**。应用内一切跳转/回退/下载地址**必须**用 `window.SUP_BASE`（= Flask 注入的 `request.script_root`，如 `/supervision`）锚定本应用，**禁止硬编码 `'/'` 或裸路径**——`'/'` 是网关首页（所有应用入口），用户会被丢进别的应用、再拿本应用账号反复试密码。2026-09-14 何明星「退出」即踩此坑（`app.js:129`/`admin.js:303` 已修）。
- **排查"登录不上"的方法论（务必先做）**：**先看访问日志有没有失败的登录请求**（`grep "POST /supervision/api/login" access.log`）。一条失败都没有 ⇒ 请求根本没到服务器 ⇒ 问题在客户端/落点/缓存，**不在鉴权**，不要去改密码或翻鉴权代码。日志路径 `/home/www/bibook_deploy/apps/gateway/logs/access.log`。
- **gateway 部署事实**：应用进程内挂载在 `/home/www/bibook_deploy/apps/gateway`（gunicorn:8090 + Werkzeug DispatcherMiddleware 按 `.appspec` 前缀剥离分发），`.appspec` 里 `auth: global`。
- 定位（v0.2 修正）：通用 Excel 网格填表工具——上传多个 xlsx 选择进入，无样地/GDB/调查业务，行为全由参数 sheet 驱动；不影响 hqz-survey 线上，独立 appId 不重复。
- 核心契约（C01/C02）：数据 sheet 行为一律由「参数」sheet 驱动，禁硬编码；参数语法固定——表头 key|value|类型|默认值|说明|示例，多值 `;`、子字段 `|`、目录 `/`、文件名段 `_`、占位符 `{{列名}}/{{sheet名称}}/{{时间}}`，未知 key/全角分隔符/占位符列名不匹配必须带行号报错。
- 参数 13 键（v0.23）：相片备注/相片文件名/目录/验收结果选项/可编辑列/不显示列/功能/搜索选项/压缩最长边/压缩质量 + **unique-key**（唯一键列名，只认英文写法）/**日志字段**（两用：变更日志审计 + 前端结果联动，由后端 `log_fields` 下发）/**百分比列**。后三个均**可选**，缺省走内置默认。
- **必填 key 按需（v0.23）**：`可编辑列`、`功能` 恒必填；`相片备注`/`相片文件名`/`目录` **仅在「功能」含 拍照/视频 时必填**——纯填表不拍照的 Excel 不必硬写拍照模板。
- **C11 唯一键与通用化（v0.23，doc/010）总目标：同一套 App 装不同 Excel 尽量不改代码**：①数据 sheet 优先精确取名为 `data`（多余 sheet 可容忍），否则要求"有且仅有 1 个非「参数」sheet"，否则明确报错；旧写法 `next(n for n in sheetnames if n != '参数')` 一带说明页就静默取错表。②`unique-key` 声明后该列**必须非空且唯一**，违反**拒绝上传**并报错带 Excel 行号；且不得出现在「可编辑列」中；**未声明则不校验**（用户口径）。③行标识全链路参数化：`keyCol()/keyVal()`（参数 → 后端 `key_column` → 兜底小班号），前端原有 12 处 `rowVal('小班号')` 已清干净。④结果联动按列名判角色（含 人/员→当前用户；含 日期/时间→今天），换列名不改代码。⑤`workbooks` 表加 `key_column`（纯加列，老工作簿回落小班号）。⑥`api_delete` 连带清源模板目录 + `accept_logs`/`photo_sync` 关联行（原只删 DB 行，会留孤儿目录）。
- **工具与回归网（v0.23 新增）**：`tools/check_template.py` 模板体检器（只读，FATAL/PENDING/WARN，非模板自动 SKIP，退出码 0=可上传）；`tests/test_unit.py`(31) / `tests/test_e2e.py`(19) / `tests/run_all.sh` 一键全量。**改模板先跑体检器；改代码先跑 run_all.sh**。
- **目录名陷阱**：`/Users/sux/Desktop/hqz-supervision` 末尾含 **U+200C 零宽不连字符**，shell 里手写路径会 "No such file or directory"；脚本里用 `glob.glob("/Users/sux/Desktop/hqz-supervision*")` 取真实路径。
- `validate.py` / `check_invariants.py` / `compare.py` / `make_baseline.sh` **仍是未填的占位骨架**（`validate.py` 里是 `REQUIRED=["关键列1"]`；`make_baseline.sh` 的生成命令是 `python main.py --data data/ --out`，而本项目 main.py 是 Flask app 不接受 `--out`）——用到前必须先改写。
- 关键文档：doc/001-参数sheet配置规范.md、doc/002-拍照轨迹功能迁移设计.md（迁移批次 B1 param_parser+grid_render → B2 photo_mgr → B3 track_mgr）、doc/007-相片云同步设计.md、doc/010-唯一键参数化与通用化.md。
- v0.9 相片云同步（已上线）：App 拍照 → /api/photo 中转 → 七牛 zz-1 暂存 + data/pending 缓冲 → 百度网盘 /apps/book_translator/supervision/{参数目录}/（app_key 决定固定目录）；状态机 received→qiniu_ok→baidu_ok，百度成功删 pending、七牛副本 30 天清理；sync_cloud.py 纯标准库（服务器 shared_venv 无 requests/qiniu）；凭证在 settings 表（双端已配，enabled=1）；七牛 io 回读域名未开放（需绑下载域名），pending 为主要回读源。
- hqz-survey 硬编码位置备查：app.js buildPhotoName L3033 / photoSaveSubdir L3005 / PHOTO_SAVE_DIR L2984 / EXPORT_SAVE_DIR L3031。
- 模板自带 inspect.py 与 stdlib 冲突：目录内跑 python 需 cd /tmp。
- 运维时间点：**forest.bibook.top 的 HTTPS 证书 2026-11-30 到期**（90 天免费证书，需提前续，否则 App 打不开）；登录令牌 180 天、会话 cookie 30 天；debug APK 签名证书有效至 2056（无试用期）。APK 分发走自建短链 /supervision/apk（CI 产物 scp 到 apps/supervision/static/，旧包保留可回滚，/apk 取版本号最大者）。
- Android 打包（v0.3.1）：Capacitor6 远程URL壳模式（同 hqz-survey），appId top.bibook.supervision / appName 监督验收 / URL 占位 forest.bibook.top/supervision；android/ 已生成并提交，构建需装 SDK 的机器（本机无 Android SDK）；前端桥接约定 waitForCapacitor()。GitHub 远程 git@github.com:sux789/hqz-supervision.git，建仓后 push。
