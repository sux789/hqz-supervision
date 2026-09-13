# hqz-supervision 长期记忆

- 定位（v0.2 修正）：通用 Excel 网格填表工具——上传多个 xlsx 选择进入，无样地/GDB/调查业务，行为全由参数 sheet 驱动；纯前端零后端（jspreadsheet-ce@4 + SheetJS + IndexedDB），先本地预览不部署；不影响 hqz-survey 线上，独立 appId 不重复。
- 核心契约（C01/C02）：数据 sheet 行为一律由「参数」sheet 驱动，禁硬编码；参数语法固定——表头 key|value|类型|默认值|说明|示例，多值 `;`、子字段 `|`、目录 `/`、文件名段 `_`、占位符 `{{列名}}/{{sheet名称}}/{{时间}}`，未知 key/全角分隔符/占位符列名不匹配必须带行号报错。
- 参数 8 键：相片备注/相片文件名/目录/验收结果选项/可编辑列/不显示列/功能(拍照;轨迹)/搜索选项。
- 关键文档：doc/001-参数sheet配置规范.md、doc/002-拍照轨迹功能迁移设计.md（迁移批次 B1 param_parser+grid_render → B2 photo_mgr → B3 track_mgr）、doc/007-相片云同步设计.md。
- v0.9 相片云同步（已上线）：App 拍照 → /api/photo 中转 → 七牛 zz-1 暂存 + data/pending 缓冲 → 百度网盘 /apps/book_translator/supervision/{参数目录}/（app_key 决定固定目录）；状态机 received→qiniu_ok→baidu_ok，百度成功删 pending、七牛副本 30 天清理；sync_cloud.py 纯标准库（服务器 shared_venv 无 requests/qiniu）；凭证在 settings 表（双端已配，enabled=1）；七牛 io 回读域名未开放（需绑下载域名），pending 为主要回读源。
- hqz-survey 硬编码位置备查：app.js buildPhotoName L3033 / photoSaveSubdir L3005 / PHOTO_SAVE_DIR L2984 / EXPORT_SAVE_DIR L3031。
- 模板自带 inspect.py 与 stdlib 冲突：目录内跑 python 需 cd /tmp。
- 运维时间点：**forest.bibook.top 的 HTTPS 证书 2026-11-30 到期**（90 天免费证书，需提前续，否则 App 打不开）；登录令牌 180 天、会话 cookie 30 天；debug APK 签名证书有效至 2056（无试用期）。APK 分发走自建短链 /supervision/apk（CI 产物 scp 到 apps/supervision/static/，旧包保留可回滚，/apk 取版本号最大者）。
- Android 打包（v0.3.1）：Capacitor6 远程URL壳模式（同 hqz-survey），appId top.bibook.supervision / appName 监督验收 / URL 占位 forest.bibook.top/supervision；android/ 已生成并提交，构建需装 SDK 的机器（本机无 Android SDK）；前端桥接约定 waitForCapacitor()。GitHub 远程 git@github.com:sux789/hqz-supervision.git，建仓后 push。
