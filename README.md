# hqz-supervision · 野外验收监管系统

> Excel 驱动的通用网格填表 + 验收流程 App（Flask 线上多用户 / Android WebView 壳）。  
> 管理员上传含「数据 sheet + 参数 sheet」的 Excel 即成为工作簿；用户在 App 里按参数配置填表、走验收流程、  
> 拍照水印、录像、记录轨迹；照片经服务器中转上云（七牛 → 百度网盘），关键动作留痕审计。  
> **不含调查专属业务**（无样地/GDB），行为由参数表驱动，换一类 Excel 尽量不改代码。

- **线上**：<https://forest.bibook.top/supervision（网关挂载，跳转必须> `window.SUP_BASE`）
- **本地**：`bash tools/dev.sh start` → <http://127.0.0.1:8720（雷华雄> / lhx123）
- **版本**：业务 v0.33 ｜ 协作基 v0.34（`STATE.md` 为准）

## 三条命令

```bash
bash tools/ai.sh brief     # 开工：4KB 现状（现在 / 必须知道 / 下一步）
bash tools/run.sh          # 体检：七门（回归 237 项 / JS / 索引 / 占位符 / 密钥 / 预算 / 模板）
bash tools/ai.sh explain   # 交付：评审单（风险 / 影响面 / 必跑 / 待拍板）
```

## 结构（根目录只放「每会话必读」，其余进 tools/ 与 doc/）

```text
AGENTS.md              AI 入口（先 brief，再按需回查）
AI_INSTRUCTIONS.md     协作协议 + DOD（完成定义，机器判定）
CONSTRAINTS.md         硬约束 C01–C25（违反=返工）
BLOCKS.md              积木 B1–B27（指令坐标）
GLOSSARY.md            术语表（词一致）
STATE.md               现状快照 + 资产活跃度
doc/024-…手册.md       方法与判据：三旋钮 / 三本账 / 六步流水线 / 多 Agent 并行
main.py                Flask 后端（接口 / 摄入 / 导出 / 审计）
param_parser.py        参数 sheet 解析与合并
flow_parser.py         验收流程表解析
static/                app.js / flow.js / admin.js / photo_exif.js
android/               Capacitor 壳 + 原生 BgLocationPlugin
tests/                 run_all.sh（单元/e2e/EXIF）+ 用例
tools/                 ai.sh / run.sh / dev.sh / log.sh / snapshot.sh / checks/ / hooks/
data/                  工作簿、模板、库（不入 git）
```

## 常用

| 目的      | 命令                                                   |      |          |        |
| ------- | ---------------------------------------------------- | ---- | -------- | ------ |
| 全量回归    | `bash tests/run_all.sh`（237 项）                       |      |          |        |
| 改模板后体检  | `python3 tools/check_template.py data/*.xlsx`        |      |          |        |
| 本地服务    | \`bash tools/dev.sh start                            | stop | status\` |        |
| 危险操作前快照 | `bash tools/snapshot.sh save <标签>`                   |      |          |        |
| 排障日志    | \`bash tools/log.sh [app                             | js   | flow     | exc]\` |
| 并行任务    | `bash tools/ai.sh task list`                         |      |          |        |
| 人机看板    | `bash tools/align.sh --open`                         |      |          |        |
| 部署      | `bash deploy.sh`（网页改动无需出包；原生改动推 `v*` tag 由 CI 出 APK） |      |          |        |

## 文档地图

- 协作方法：`doc/024-AI高效协作手册.md`（人 3 分钟 / AI 40 秒 / 新 Agent 5 分钟）
- 约束详解：`doc/023-约束详解.md` ｜ 参数规范：`doc/025-参数sheet配置规范.md`
- 功能总览：`doc/018-功能总览（开发交接）.md` ｜ 迭代模板：`doc/001-迭代文档模板.md`
- 历史：`doc/STATE历史归档.md`、`doc/NNN-版本_主题.md`
