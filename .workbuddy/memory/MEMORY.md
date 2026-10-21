# hqz-supervision 长期记忆

> 详情：`STATE.md` + 每日日志 `.workbuddy/memory/YYYY-MM-DD.md` + 迭代文档 `doc/NNN`。只留**不能从代码/文档推导的教训**。

## 定位与部署
- Excel 网格填表工具（无样地/GDB，行为全由参数 sheet 驱动）；appId `top.bibook.supervision`，后端 https://forest.bibook.top/supervision（gateway 进程内挂载）。
- 部署只走 `./deploy.sh`（rsync+重启 gateway，**不碰数据库**）。**forest.bibook.top 证书 2026-11-30 到期需续**。`sync_cloud.py`/`track_export.py` 必须纯标准库（服务器 shared_venv 无 requests/qiniu）。

## 环境陷阱
- 工作目录名末尾含 **U+200C 零宽字符**，shell 手写路径必失败；脚本用 `glob.glob("/Users/sux/Desktop/hqz-supervision*")` 取真实路径。
- 模板自带 `inspect.py` 与标准库冲突，目录内跑 python 先 `cd /tmp`。
- Edit 往 CONSTRAINTS.md 追加行：old_string 必须锚定上一行**完整行尾**（只写行首会截断整行，犯过两次）；行内 `|` 写 `\|`。

## 排查顺序（先查）
- **「登录不上」先看服务端日志有没有失败登录请求**：没有 ⇒ 请求没到 ⇒ 在客户端/落点，**不在鉴权**。
- **「照片没同步」第一步看 `sync_enabled`**：关闭时 App 静默跳过。`photo_sync` 的 61 条 received 是遗留 video 记录，不是待推照片。
- **「点了没反应且 jslog 无错」⇒ await 原生桥 Promise 永不落定**（try/catch 盲区）⇒ 跨 JS↔原生调用一律 `nativeTimeout` 限时。
- 红色报错顺序：后台运行日志卡 → `js.log` → `app.log`。
- **API 全绿 ≠ 界面对**：改渲染路径必须 Chrome 430px 截图，**每个 tab/每个环节都点一遍**（v0.36 五个 UI bug 皆如此抓到）。
- 测中文 URL 参数必须显式编码（`entry=%E6%9D%90%E6%96%99`）——curl 裸传会被 shell 弄成乱码，表现为「接口坏了」。

## 不可再犯的教训（细则见 CONSTRAINTS.md）
- **C19 读值回写**：jspreadsheet 每行=[行号,字段名,值] 三格；优先官方 API `getData()`，DOM 禁写死下标；必有 sanity 闸（值==字段名即读错）；审计日志必记 `old_value`。
- **C17** 关键数据不依赖第三方组件派事件；**C18** 一次性引导持久化 localStorage；**C12** 跳转用 `window.SUP_BASE`（硬编码 `/` 会丢用户进别的应用）；**C14** 保存四路径冲刷 + `fetch(keepalive)` 禁 sendBeacon；**C13** 可见性≠权限，边界只在服务端；**C16** 可能不存在的元素必须 `on()`/空值守卫（裸 addEventListener ⇒ 整页失效）。
- **C20** Canvas 压缩必丢 EXIF，piexifjs 回写（Orientation=1、删尺寸/MakerNote），失败静默退回。**C15** 「更新 Excel」就地更新保留原 id（重传永远新建）。
- **括号感知分割**：`字段=类型(参数;参数)` 不能按 `;` 裸切（下拉选项会丢），必须先数括号深度。
- **改流程唯一键/查询键前先问老数据怎么活**（v0.36 加入口后缀差点吃掉全部老记录）。
- **写测试**：多身份各用独立 test_client；不写死模板文件名；选测试文件排除 `data/workbooks/`、`.~`、副本.xlsx。批量正则改代码逐行复核。

## 发版纪律（GitHub Actions）
- 仓库 `git@github.com:sux789/hqz-supervision.git`（公开），`gh` 已登录。
- 三步缺一不可：改 versionCode（递增）+ versionName → commit+push → **打 tag 并 push tag**（v0.10 曾忘 tag，现场全是旧包）；出包后 `gh release view` 复核直链。查发版史必须 `git tag --sort=-v:refname`（字典序把 v0.10/v0.11 排到 v0.9 后）。当前到 v0.11。www/ 只是占位页，App 走远程 URL，发版不需同步 www。
- 改了静态 JS/CSS 必须 bump `templates/index.html` 里的 `?v=`，否则旧 App serve 陈旧文件。
- **公开仓库有意未入库**：`doc/合同-*.png`、`doc/*监理规划/实施细则.docx`、`doc/使用说明.docx`（含临时密码）、`data/验收流程设计/…副本.xlsx`、`data/backups/`、`.trae/`。要入库需用户显式确认。

## 各版精华（细节在 doc/NNN 与当日日志）
- **v0.34**：`bash tools/run.sh` 七门是唯一验证入口（判定只看 last_gates.json 的 id+status）；`ai.sh brief` 开场 ≤4KB；任务卡同文件 active 重叠即拒绝；派生视图禁手改禁入库。
- **v0.35**：业务员 5 张窄表 → `flow_config.py` → `flow_build --apply`；转序必须合格才放行；老 12 列格式「单行文本一律选填」不要一把改。
- **v0.36**：入口维度（同挂小班档案时 match_flow 只回一条，不加入口巡视**永久不可达**）；白名单留空=全部必须配 `expert` 标记；唯一性校验取环节内第一个日期/批次控件。
- **v0.37**：`row_idx=-1` = 项目级哨兵（材料检验按批次，不挂小班）——GET/POST `/flow?row=-1&entry=材料`；项目级匹配显式认领注册条件=「独立入口」的流程；基本信息只给全表同值常量列；提交后回读**必须带 entry**（多入口否则读到另一条流程）。
- 回归 `bash tests/run_all.sh` = **352 项**（unit 108 + 流程配置 77 + e2e 109 + 流程 e2e 38 + exif 20），全绿才提交。
