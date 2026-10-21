# hqz-supervision 长期记忆

> 详细版：`doc/018-功能总览` + `STATE.md` + 每日日志 `.workbuddy/memory/YYYY-MM-DD.md` + 迭代文档 `doc/NNN`。本文件只留**不能从代码/文档直接推导的判断与教训**。

## 定位与部署
- Excel 网格填表工具（无样地/GDB 业务，行为全由参数 sheet 驱动）；appId `top.bibook.supervision`，后端 https://forest.bibook.top/supervision（gateway 进程内挂载，gunicorn:8090 + DispatcherMiddleware）。
- 部署只走 `./deploy.sh`（rsync 代码 + 重启 gateway，**不碰数据库**），部署前备 `/home/www/bibook_deploy/backups/`。**forest.bibook.top 证书 2026-11-30 到期需续**。本机无 Android SDK，原生改动要在能出包的机器上做。
- `sync_cloud.py` / `track_export.py` 必须**纯标准库**（服务器 shared_venv 无 requests/qiniu）。

## 环境陷阱
- 工作目录名末尾含 **U+200C 零宽字符**，shell 手写路径必 No such file；脚本用 `glob.glob("/Users/sux/Desktop/hqz-supervision*")` 取真实路径。
- 模板自带 `inspect.py` 与标准库冲突，目录内跑 python 先 `cd /tmp`。
- Edit 往 `CONSTRAINTS.md` 追加行：old_string 必须锚定上一行**完整行尾**（只写行首会截断整行，犯过两次）；行内 `|` 写成 `\|`，加完校验列数 == 4。

## 排查顺序（先查这些，能省小时）
- **「登录不上」先看服务端日志有没有失败登录请求**：一条都没有 ⇒ 请求没到服务器 ⇒ 问题在客户端/落点，**不在鉴权**。
- **「照片没同步」第一步看 `sync_enabled`**：关闭时 App 拿到 false 静默跳过（`/api/photo` 零请求）。`photo_sync` 里 61 条 `received` 是 09-13 遗留 **video** 记录（视频不上云，永远停 received），不是待推照片。
- **「点了没任何反应且 jslog 无错」⇒ await 原生桥 Promise 永不落定**（try/catch 盲区）⇒ 所有跨 JS↔原生调用包 `nativeTimeout` 限时（status/drain 2s、权限 request 10s）。
- 红色报错排查顺序：后台「运行日志」卡片 → `data/logs/js.log`（前端，含脚本语法错误）→ `data/logs/app.log`（`[EXC]`/`[flow]`）。
- **API 全绿 ≠ 界面对**：改渲染路径必须 Chrome 真实窄屏（430px）截图，**每个 tab / 每个环节都点一遍**——v0.36 五个真 UI bug 全是接口测全绿之后才抓到（含「同一字段两条路径各判一次」）。
- 后台隐藏卡片是整块 `<!-- -->` 注释（接口与数据全保留）；校验「元素是否生效」必须先去注释再查，否则误报。

## 不可再犯的教训（细则见 CONSTRAINTS.md 对应条目）
- **C19 读值回写**：jspreadsheet 每行是 [行号, 字段名, 值] 三格，`children[1]` 是字段名；优先官方 API `getData()`，DOM 禁写死下标；必有 sanity 闸（值==字段名即判读错）；审计日志必记 `old_value`（还原全靠它；污染特征 = new_value == field）。
- **C17** 关键数据不依赖第三方组件派发事件，边界直接读 DOM；**C18** 一次性引导必须持久化 localStorage（国产 ROM 会重载页面）；**C12** 一切跳转用 `window.SUP_BASE` 锚定（硬编码 `/` 会把用户丢进网关首页别的应用）；**C14** 防抖保存必须在返回/路由/切后台/关页四路径冲刷，卸载用 `fetch(keepalive)`、**禁 sendBeacon**（带不了 token）；**C13** 可见性 ≠ 权限，边界只在服务端，`int(r['a'] or 1)` 把 0 吃成 1；**C16** 可能不存在的元素必须 `on()`/空值守卫（顶层裸 addEventListener 抛错 ⇒ 整脚本失败、整页失效）。
- **C20** Canvas 压缩必丢 EXIF，用 piexifjs 回写（Orientation 强制 1、删尺寸标签、删 MakerNote），失败静默退回原行为；坐标另烙为水印文字。
- **C15** 「更新 Excel」就地更新保留原 id（重新上传永远新建）；以既有行序为基准合并，写库前自动备份可回滚。
- **括号感知分割**：`字段=类型(参数;参数)` 不能按 `;` 裸切（`下拉(符合设计;部分完成)` 会丢选项），必须先数括号深度——凡「分隔符 + 可带参值」的配置语法都适用。
- **改流程唯一键/查询键前先问老数据怎么活**：v0.36 加入口后缀曾让老记录一条读不到（现场表现"昨天填的全没了"），靠 `flow_build --apply` 自动迁移救回。
- **写测试**：多身份各用独立 `test_client`（session 互覆盖，表现为管理员被判 403）；不写死模板文件名与期望；选测试文件必须排除 `data/workbooks/`、`.~`、副本.xlsx，并校验表头含关键列。
- **批量正则改代码必须逐行复核**（perl 替换曾丢默认值）。

## 发版纪律（GitHub Actions）
- 仓库 `git@github.com:sux789/hqz-supervision.git`（**公开**）；`gh` 已登录 sux789。
- 发版三步缺一不可：改 `android/app/build.gradle` versionCode（必须递增）+ versionName → commit+push → **打 `vX.Y.Z` tag 并 push tag** → release.yml 自动构建签名 APK（约 2 分钟）。v0.10 曾改了号忘打 tag，现场全是旧包——**出包后必须 `gh release view` 复核直链可下**。
- 签名 Secret 早已配好（缺 Secret 自动退回 debug 签名）；产物名固定 `app-release.apk`，公开直链可下。
- **公开仓库有意未入库**：`doc/合同-*.png`、`doc/*监理规划/实施细则.docx`、`data/验收流程设计/…副本.xlsx`（干扰测试选文件）、`data/backups/`、`.trae/`。要入库需用户显式确认。

## 各版精华（细节在 doc/NNN 与当日日志）
- **v0.34**（doc/024/026）：`bash tools/run.sh` 七门是唯一验证入口（判定只看 `doc/health/last_gates.json` 的 id+status）；`bash tools/ai.sh brief` 开场 ≤4KB 替代通读入口文件；并行任务卡同文件 active 重叠即拒绝，main.py 同时只许 1 个 agent；派生视图（brief/review/map/board、health/、align.html）禁手改禁入库。
- **v0.35**（doc/027）：业务员 5 张窄表 → `flow_config.py` → `tools/flow_build.py --apply <wid>`（自动备份 db）；转序语义=前置结论必须合格才放行；老 12 列格式「单行文本一律选填」不要一把改（动线上流程）。
- **v0.36**（doc/028）：入口维度（验收/巡视同挂小班档案时 `match_flow` 只回一条，不加入口维度巡视**永久不可达**）；「白名单留空=全部」必须配 `expert` 标记下发（否则老流程突然冒全部列）；唯一性校验取**环节内第一个日期/批次控件**（写死字段名=随机拦）；校验 DSL 服务端权威；规模 9 流程/25 环节/73 核验项。**材料入口 App 界面未做**（后端 `?entry=材料` 已可用）。
