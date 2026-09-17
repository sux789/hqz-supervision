# BLOCKS — 积木注册表

> 规则：新增积木必须登记，否则视为不存在；AI 提问/指令先指向编号；≤25 行。
> 状态：**部分登记**（先登记 v0.23 通用化涉及的真实积木；其余待 MVP 盘点补齐，示例行已删）。

| 编号 | 职责 | 文件 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| B1 | 参数解析与校验 | `param_parser.py` | 参数 sheet 行 + 数据表头 | config dict（违规抛 `ParamError` 带行号） | — |
| B2 | 生效值解析（C11） | `param_parser.py` | config + headers | `row_key_column` / `log_fields_of` / `percent_cols_of` / `unique_key_of` | B1 |
| B3 | 唯一键校验（C11） | `param_parser.py::check_unique_column` | rows + headers + config | `None` 或带行号错误串 | B1、B2 |
| B4 | 工作簿摄入 | `main.py::parse_workbook_storage` | xlsx 字节流 | `(sheet_name, headers, rows, config, param_rows, key_column)` | B1、B2、B3 |
| B5 | 数据 sheet 定位（C11） | `main.py::_pick_data_sheet` | sheetnames | 数据 sheet 名（歧义则抛 `ParamError`） | — |
| B6 | 模板体检（只读工具） | `tools/check_template.py` | xlsx 路径 | FATAL/PENDING/WARN/SKIP 报告 + 退出码 | B1、B2、B3、B5 |
| B7 | 前端行标识（C11） | `static/app.js::keyCol/keyVal` | `cur.key_column` | 唯一键列名 / 当前行键值 | B4（经 `/api/workbooks/<id>`） |
| B8 | 前端结果联动（C11） | `static/app.js::linkFields/syncAcceptCols` | `cur.log_fields` + 列名 | 自动填「人」列=当前用户、「日期」列=今天；选空则清 | B4 |
| B9 | 回归网 | `tests/test_unit.py`、`tests/test_e2e.py`、`tests/run_all.sh` | 仓库根 | 通过/失败计数 + 退出码 | B1–B8 |
| B10 | 下架守卫（C13） | `main.py::_inactive_for` | con + wid | True/False（已下架且非管理员） | — |
| B11 | 危险操作确认（C13） | `static/admin.js::confirmByName` + `templates/admin.html#dangerModal` | 对象名称 | Promise<boolean> | — |
| B12 | 防抖保存与离场冲刷（C14） | `static/app.js::scheduleRowSave/flushRowSave/flushRowSaveUnloading` | 编辑中的行（wid/ridx/values 快照） | 落库请求（可 await / keepalive） | B7 |
| B13 | 导出筛选（C14） | `param_parser.py::export_filters_of/filter_options/filter_rows` | headers + config + 筛选条件 | 过滤后的行 / 字段清单 | B1 |
| B14 | 后台下载筛选弹框 | `static/admin.js::openFilterDialog` + `templates/admin.html#filterModal` | `GET /admin/api/workbooks/<id>/filter-options` | 带查询参数的下载 URL | B13 |
| B15 | 按唯一键合并更新（C15） | `param_parser.py::merge_rows` | 旧/新 headers+rows + 唯一键 + 保护列 | `(merged_rows, stats)` | — |
| B16 | 更新与回滚接口（C15） | `main.py::api_update / api_rollback` + `workbook_backups` 表 | xlsx 字节流 + confirm 名称 | 就地更新的工作簿 / 还原 | B4、B15 |
