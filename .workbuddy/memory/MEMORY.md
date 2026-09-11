# hqz-supervision 长期记忆

- 定位：林业监督验收 App（网格填表 + 拍照 + 轨迹），从 hqz-survey 迁移，参照 project-template 方法论。
- 核心契约（C01/C02）：数据 sheet 行为一律由「参数」sheet 驱动，禁硬编码；参数语法固定——表头 key|value|类型|默认值|说明|示例，多值 `;`、子字段 `|`、目录 `/`、文件名段 `_`、占位符 `{{列名}}/{{sheet名称}}/{{时间}}`，未知 key/全角分隔符/占位符列名不匹配必须带行号报错。
- 参数 8 键：相片备注/相片文件名/目录/验收结果选项/可编辑列/不显示列/功能(拍照;轨迹)/搜索选项。
- 关键文档：doc/001-参数sheet配置规范.md、doc/002-拍照轨迹功能迁移设计.md（迁移批次 B1 param_parser+grid_render → B2 photo_mgr → B3 track_mgr）。
- hqz-survey 硬编码位置备查：app.js buildPhotoName L3033 / photoSaveSubdir L3005 / PHOTO_SAVE_DIR L2984 / EXPORT_SAVE_DIR L3031。
- 模板自带 inspect.py 与 stdlib 冲突：目录内跑 python 需 cd /tmp。
