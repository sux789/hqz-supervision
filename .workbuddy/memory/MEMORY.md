# hqz-supervision 长期记忆

- 定位（v0.2 修正）：通用 Excel 网格填表工具——上传多个 xlsx 选择进入，无样地/GDB/调查业务，行为全由参数 sheet 驱动；纯前端零后端（jspreadsheet-ce@4 + SheetJS + IndexedDB），先本地预览不部署；不影响 hqz-survey 线上，独立 appId 不重复。
- 核心契约（C01/C02）：数据 sheet 行为一律由「参数」sheet 驱动，禁硬编码；参数语法固定——表头 key|value|类型|默认值|说明|示例，多值 `;`、子字段 `|`、目录 `/`、文件名段 `_`、占位符 `{{列名}}/{{sheet名称}}/{{时间}}`，未知 key/全角分隔符/占位符列名不匹配必须带行号报错。
- 参数 8 键：相片备注/相片文件名/目录/验收结果选项/可编辑列/不显示列/功能(拍照;轨迹)/搜索选项。
- 关键文档：doc/001-参数sheet配置规范.md、doc/002-拍照轨迹功能迁移设计.md（迁移批次 B1 param_parser+grid_render → B2 photo_mgr → B3 track_mgr）。
- hqz-survey 硬编码位置备查：app.js buildPhotoName L3033 / photoSaveSubdir L3005 / PHOTO_SAVE_DIR L2984 / EXPORT_SAVE_DIR L3031。
- 模板自带 inspect.py 与 stdlib 冲突：目录内跑 python 需 cd /tmp。
- Android 打包（v0.3.1）：Capacitor6 远程URL壳模式（同 hqz-survey），appId top.bibook.supervision / appName 监督验收 / URL 占位 forest.bibook.top/supervision；android/ 已生成并提交，构建需装 SDK 的机器（本机无 Android SDK）；前端桥接约定 waitForCapacitor()。GitHub 远程 git@github.com:sux789/hqz-supervision.git，建仓后 push。
