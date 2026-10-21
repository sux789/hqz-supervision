# AI 协作协议

> 开工第一件事：`bash tools/ai.sh brief`（≤4KB 事实卡）。本文件与 CONSTRAINTS/BLOCKS/GLOSSARY/STATE
> 改为**按需回查**，不再要求整篇读入。方法与判据见 `doc/024-AI高效协作手册.md`。

## 指挥原则

1. 遇错先执行拿真实报错，不猜。
2. 改动方向：参数配置驱动优先、最小改动；边界：不改数据 sheet 内容与参数 sheet 语法约定，只改代码。
3. 提交前对照原始数据自审，推导/默认值逻辑重点审。
4. 超过 3 个文件或涉及数据结构变化，先出 5 行方案文档确认再动手。
5. 指令精确到三级坐标：积木编号 → 文件:行号 → 单元格/字段名，不说「改输出」。
6. 拿不准的业务判断列待确认清单（问题/候选方案/建议/依据），不替用户做决定。
7. 「做完」由 `bash tools/run.sh` 判定，不由任何人的判断代替（含 AI 自己）。

## 项目铁律（违反=返工）

- 校验失败原样报错+列可选值，不自动映射/推导。
- 坏数据入口炸掉带行号，不静默跳过；所有记录被跳过时必须抛错，不生成空输出。
- 改算法前先读原始公式/数据/模板结构，证据先行，不凭记忆。
- 同一事实只算一次，跨表/跨处引用不重算；展示层能用公式用公式。
- 数值在源头一次取整，显示值=计算值，下游不再取整；表间勾稽在显示值上成立。
- 每次运行全新状态，全局缓存/配置入口重置，不跨运行残留。
- 改动后必须跑 `bash tools/run.sh`，全绿才提交；红了先分诊，不许改断言/改约束变绿。
- 对比出差异先归因（代码bug/数据问题/基准错/合理多解），不默认改代码对齐。
- WARN 级问题汇总成报告文件，不逐个打断、不淹没控制台。
- 模板/生成物只填值不动样式；写入前先确认标签位/值位。
- 派生视图（brief/review/TRIAGE/last_gates/map/board/align）禁止手改，只能重跑命令（C23）。

## 完成定义 DOD（★ 机器校验，其余靠人）

| # | 判据 | 校验方式 |
|---|---|---|
| 1 | 回归 237 项全绿 | ★ `run.sh` G1 |
| 2 | 静态 JS 语法通过且无 C22 禁用语法 | ★ G2 |
| 3 | 索引一致（积木文件存在、doc 编号可解析且不重复） | ★ G3 |
| 4 | 入口文件无未替换占位符 | ★ G4 |
| 5 | 无密钥/签名文件入库 | ★ G5 |
| 6 | 入口 ≤36KB、brief ≤4KB | ★ G6 |
| 7 | 模板 FATAL 已记录（WARN 可提交） | ★ G7 |
| 8 | 写了 `doc/NNN-版本_主题.md`，反转记录不省 | 人工 |
| 9 | 新增/变更约束已同步 `CONSTRAINTS.md` | 人工 |
| 10 | 新增业务名词已进 `GLOSSARY.md` | 人工 |
| 11 | `STATE.md` 已更新（版本/回归数/下一步） | 人工 |
| 12 | 评审单 HIGH 项逐条回应 | 人工 `bash tools/ai.sh explain` |
| 13 | 并行开发时任务卡已 `done`（范围/验收留痕） | 命令 `task done` |

一条命令跑完全部机器项：`bash tools/run.sh`（`--fast` 跳过回归）。
某门连续 3 次 SKIP 视为该门该修或该退役，不许当成常态。

## 每次迭代必须

- 一版解决 1–3 个问题，版本号递增。
- 写 `doc/NNN-版本_主题.md`（模板见 `doc/001-迭代文档模板.md`，七段式：需求/验证/设计/代码/测试/反转/部署）。
- 新决策若推翻旧决策，写明反转链；同步更新 `CONSTRAINTS.md`（旧条目不删，标「已被 Cxx 反转」）。
- 改口径必同步三处：模板 + 代码 + 测试断言。
- 迭代文档末尾写「本版减掉了哪个人工动作」（`doc/024` §8.2）。
- 提交信息带 `Refs: doc/NNN`（commit-msg 钩子强制）。

## 并行协作（多 Agent）

- 动手前先建任务卡并声明范围（`bash tools/ai.sh task new --scope "文件:行"`）；范围重叠会被 `claim` 拒绝。
- **一个文件同时只有一个 owner**；`main.py` 是瓶颈区，同一时刻只许 1 个 agent 改。
- 入口文件（AGENTS/AI_INSTRUCTIONS/CONSTRAINTS/BLOCKS/GLOSSARY/STATE）只许集成者改，worker 把建议写进任务卡 notes。
- 接口/字段/错误码先定契约再各自实现；合并顺序由人定，agent 不自动 merge。
- 测试只许新增，不许放宽；合并后集成者必须重跑全量闸门。
- 完整协议见 `doc/024-AI高效协作手册.md` §5。

## 参数 sheet 配置约定（本项目核心契约）

数据 sheet 的行为由「参数」sheet 驱动，**代码中禁止硬编码这些行为**（与 hqz-survey 最大差异点）。
**权威规范见 `doc/025-参数sheet配置规范.md`**，要点：

- 表头固定 6 列：`key | value | 类型 | 默认值 | 说明 | 示例`
- 分隔符语法：多值 `;`；项内子字段 `|`；目录 `/`；文件名段 `_`；占位符 `{{列名}}`、`{{时间}}`、`{{工作簿id}}`；禁止全角标点
- 改口径必同步三处：模板参数 sheet + `param_parser.py` + 测试断言

## 项目专属信息

- **业务一句话**：Excel 驱动的野外验收监管系统（Flask 线上多用户）——管理员上传含「数据 sheet + 参数 sheet」的 Excel 成为工作簿，用户在 App 里按参数配置填表/走验收流程/拍照水印/录像/轨迹跟踪，照片上云（七牛中转→百度网盘）、验收留痕审计；不含调查专属业务（无样地/GDB）。
- **输入**：用户上传的 .xlsx（`main.py::parse_workbook_storage`）；真实用例 `data/中幼林抚育-网格填表模板2.xlsx`（1172 行）、`data/造林项目-验收模板.xlsx`（600 行，含流程 sheet）。
- **输出**：线上 https://forest.bibook.top/supervision（跳转必须 `window.SUP_BASE`，C12）；导出按原模板回填（C08）；轨迹 GPX/SHP；照片按参数目录上云。
- **技术栈**：Flask + SQLite（`data/app.sqlite3`）+ openpyxl（`main.py`/`flow_parser.py`/`param_parser.py`/`track_export.py`，纯标准库风格）；前端原生 JS（`static/app.js`/`flow.js`/`admin.js`/`photo_exif.js`）+ jspreadsheet-ce@4 + piexifjs；Android Capacitor（`android/`，出包走 GitHub Actions tag）。
- **权威信息源排序**：现场/客户口头需求（P0，需落成文字）> 合同与监理细则（P1）> 已上线行为与回归断言（P2）> 代码注释与历史文档（P3）。冲突时向上取，不向下迁就。
- **运行**：`bash tools/dev.sh start` → http://127.0.0.1:8720（雷华雄/lhx123）
- **体检**：`bash tools/run.sh`（提交前 `--fast`）｜ **回归**：`bash tests/run_all.sh`（237 项）
- **模板体检**：`python3 tools/check_template.py data/*.xlsx`（改模板后上传前必跑）
- **部署**：`bash deploy.sh`（网页改动无需出包；原生改动推 `v*` tag 由 CI 出 APK）
