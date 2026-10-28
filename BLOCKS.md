# BLOCKS — 积木注册表

> 规则：新增积木必须登记，否则视为不存在；AI 提问/指令先指向编号；≤25 行。
> 状态：**部分登记**（先登记 v0.23 通用化涉及的真实积木；其余待 MVP 盘点补齐，示例行已删）。

| 编号 | 职责 | 文件 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| B1 | 参数解析与校验 | `param_parser.py` | 参数 sheet 行 + 数据表头 | config dict（违规抛 `ParamError` 带行号） | — |
| B2 | 生效值解析（C11） | `param_parser.py` | config + headers | `row_key_column` / `log_fields_of` / `percent_cols_of` / `unique_key_of` | B1 |
| B3 | 唯一键校验（C11） | `param_parser.py::check_unique_column` | rows + headers + config | `None` 或带行号错误串 | B1、B2 |
| B4 | 工作簿摄入 | `wb_parse.py::parse_workbook_storage` | xlsx 字节流 | `(sheet_name, headers, rows, config, param_rows, key_column)` | B1、B2、B3 |
| B5 | 数据 sheet 定位（C11） | `wb_parse.py::_pick_data_sheet` | sheetnames | 数据 sheet 名（歧义则抛 `ParamError`） | — |
| B6 | 模板体检（只读工具） | `tools/check_template.py` | xlsx 路径 | FATAL/PENDING/WARN/SKIP 报告 + 退出码 | B1、B2、B3、B5 |
| B7 | 前端行标识（C11） | `static/app.js::keyCol/keyVal` | `cur.key_column` | 唯一键列名 / 当前行键值 | B4（经 `/api/workbooks/<id>`） |
| B8 | 前端结果联动（C11） | `static/app.js::linkFields/syncAcceptCols` | `cur.log_fields` + 列名 | 自动填「人」列=当前用户、「日期」列=今天；选空则清 | B4 |
| B9 | 回归网 | `tests/test_unit.py`、`tests/test_e2e.py`、`tests/test_exif.js`、`tests/run_all.sh` | 仓库根 | 通过/失败计数 + 退出码（run_all.sh 含 node EXIF 步骤） | B1–B8、B17 |
| B10 | 下架守卫（C13） | `main.py::_inactive_for` | con + wid | True/False（已下架且非管理员） | — |
| B11 | 危险操作确认（C13） | `static/admin.js::confirmByName` + `templates/admin.html#dangerModal` | 对象名称 | Promise<boolean> | — |
| B12 | 防抖保存与离场冲刷（C14） | `static/app.js::scheduleRowSave/flushRowSave/flushRowSaveUnloading` | 编辑中的行（wid/ridx/values 快照） | 落库请求（可 await / keepalive） | B7 |
| B13 | 导出筛选（C14） | `param_parser.py::export_filters_of/filter_options/filter_rows` | headers + config + 筛选条件 | 过滤后的行 / 字段清单 | B1 |
| B14 | 后台下载筛选弹框 | `static/admin.js::openFilterDialog` + `templates/admin.html#filterModal` | `GET /admin/api/workbooks/<id>/filter-options` | 带查询参数的下载 URL | B13 |
| B15 | 按唯一键合并更新（C15） | `param_parser.py::merge_rows` | 旧/新 headers+rows + 唯一键 + 保护列 | `(merged_rows, stats)` | — |
| B16 | 更新与回滚接口（C15） | `main.py::api_update / api_rollback` + `workbook_backups` 表 | xlsx 字节流 + confirm 名称 | 就地更新的工作簿 / 还原 | B4、B15 |
| B17 | 照片 EXIF 读取/回写（doc/017） | `static/photo_exif.js` + `static/vendor/piexif.js` + `app.js::exifOf/preserveExif` | 原片 JPEG（Blob/dataUrl） | 带原片 EXIF 的压缩图（Orientation 归一、无 MakerNote） | 拍照流程 |
| B18 | 验收流程表解析（doc/019） | `flow_parser.py::parse_flows/parse_registry/parse_flow_sheet` | 流程 sheet + 注册表 | `{registry, flows:{sheet:[环节]}}`，环节含 controls/attachments | B1 |
| B19 | 流程匹配与体检（B19） | `flow_parser.py::match_flow/flow_warnings/std_lookup` | 注册表 + 流程定义 + data 行 | 命中的流程（含标准值）/ 配置坑清单 | B18 |
| B20 | 流程填报界面与闸门 | `static/flow.js` + `templates/index.html#flowPanel` + `main.py::api_flow/api_flow_save` | 环节定义 + data 行 | 控件实例 + 提交落库 `flow_records` | B18、B19、B12（离场冲刷） |
| B21 | 卫星持续跟踪 + 轨迹手动记录（v0.46） | `BgLocationPlugin.java` + `BgLocationService.java` + `static/app.js::attachTrack/flushTrack/flushTrackIfAny` | 定位权限（跟踪常开）＋ GPS fix ＋ 记录开关 `track_on` | `track_buffer.csv` / 实时点流 / GPX `/api/track` | — |

| B22 | 闸门体检（唯一验证入口） | `tools/checks/gates.py` + `tools/run.sh` + `tools/hooks/*` | 仓库工作区 | 七门结论 → `doc/health/last_gates.json`（id+status） | B9 |
| B23 | 开场简报（上下文压缩） | `tools/checks/brief.py` | STATE + last_gates + 约束表 + 任务卡 | `doc/brief.md`（≤4KB） | B22、B26 |
| B24 | 改动评审单 | `tools/checks/explain.py` | `git status/diff` + BLOCKS + 文件→约束规则 | `doc/review.md`（风险/影响面/必跑/待拍板） | B22 |
| B25 | 失败分诊 | `tools/checks/triage.py` | `last_gates.json` | `doc/health/TRIAGE.md`（事实+候选原因+顺序） | B22 |
| B26 | 并行任务卡 | `tools/checks/tasks.py` | `--scope 文件:行` | `.agent/tasks/*.json` + 范围重叠判定 | — |
| B27 | 符号地图 | `tools/checks/map.py` | 核心源文件 | `doc/map.md`（符号→行区间，供任务卡写范围） | — |
| B28 | 业务专家流程配置 | `flow_config.py` + `tools/build_flow_doc.py` + `tools/flow_build.py` + `static/flow.js` | 业务专家填写版 xlsx（①流程/②环节/③核验项/④选项/⑤字段字典） | `{registry, flows}`（同 B18）+ 带行号校验 / `workbooks.flows` / 入口 tab | B18、B20 |
| B29 | 项目级流程入口（v0.37） | `flow_hit.py::_project_std/_project_hit/_hit_payload` + `static/app.js::openFlowView/#btnMaterial` + `static/flow.js`（rowIdx<0 分支） | `GET/POST /flow?row=-1&entry=材料` + 批次 values（关联小班多选） | 常量列 std / row_idx=-1 批次记录 | B20、B28 |
| B30 | v2 流程模板 | `flow_tpl.py` + `tools/tpl/tpl_tool.py` + `flow_hit.py::_tpl_hit` + `main.py::api_flow_batch` + `static/flow.js::renderV2/submitV2` | `设计值` + `流程路由` + `参数` + `流程-*` + `flow_audit` | `flows`（flow_tpl/1）/ 级联搜索 / 整条提交 / 日志 | B18、B20 |
| B31 | 流程附件上传通道 | `flow_hit.py::_att_sig/_att_gate/_att_sig_cls` + `main.py::api_flow_att/api_attachment_get` + `static/flow.js::shootAtt(上传分支)/attHtmlV2/v2Gate` + `flow_parser.py::_att_types` | 模板「附件」列 `上传:标签@N(图片≥A;PDF≥B)` + 上传文件 | `flow_att`（id 认领/绑 rec_no）+ 数量/类型闸门 + 登录下载 | B18、B30 |
| B32 | 通过判断（v0.45） | `flow_judge.py` + `flow_tpl.py::_judge_fields` + `main.py` 闸门 + `static/flow.js::v2JudgeOf/v2JudgeFails` | `数值(株→≥设计值)` / `百分数(0-100→≥85)` / `自动(公式→≥85)` | `judge_*` 字段 / 拦「通过」/ 特殊可「不通过」+备注 | B30 |
