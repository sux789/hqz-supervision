# hqz-supervision 长期记忆

> 详细版见 `doc/018-功能总览（开发交接）.md` 与 `STATE.md`。本文件只留**不能从代码/文档直接推导的判断与教训**。

## 定位与部署
- 通用 Excel 网格填表工具（无样地/GDB 业务，行为全由参数 sheet 驱动），appId `top.bibook.supervision`，后端 https://forest.bibook.top/supervision；gateway 进程内挂载（gunicorn:8090 + DispatcherMiddleware，appspec `auth: global`）。
- 部署走 `./deploy.sh`（rsync 代码 + 重启 gateway，**不碰数据库**），部署前必备份 `/home/www/bibook_deploy/backups/`。访问日志 `/home/www/bibook_deploy/apps/gateway/logs/access.log`。
- **forest.bibook.top 证书 2026-11-30 到期**（90 天免费），需提前续。本机无 Android SDK，原生改动需在能出包的机器上做。

## 环境陷阱
- 工作目录名末尾含 **U+200C 零宽字符**，shell 手写路径会 No such file；脚本里用 `glob.glob("/Users/sux/Desktop/hqz-supervision*")` 取真实路径。
- 模板自带 `inspect.py` 与标准库冲突，目录内跑 python 需 `cd /tmp`。
- 用 Edit 往 `CONSTRAINTS.md` 追加行时 **old_string 必须锚定上一行完整行尾**，只写行首会截断整行（犯过两次）；行内 `|` 须写成 `\|`，加完用脚本校验列数 == 4。

## 后台卡片可见性（v0.29）
- **可见**：模板管理 / 图片压缩 / 视频压缩 / 云同步 / 验收变更日志；**注释隐藏**：用户管理（`tblUsers`）、轨迹下载（`tblTrack`/`btnZip`）。
- 做法 = 整块 `<!-- -->` 注释，后端接口与数据全保留；恢复**只需解锁注释、不必改 JS**（JS 早已用 `on()` 与 `if (!$('#…')) return;` 守卫）。
- 校验"元素是否生效"必须先 `re.sub(r'<!--.*?-->','',html)` **去注释**再查，否则误报。

## 云同步事实
- 排查"照片没同步"**第一步永远先看 `sync_enabled`** —— 关闭时 App 拿到 `/api/sync/enabled=false` 就静默跳过，表现为 `/api/photo` 零请求。
- `photo_sync` 里 61 条 `received` 是 09-13 遗留 **video** 记录（视频不上云，永远停 received），**不是待推送照片**。
- 路径 = `app_dir(/apps/supervision/) + prefix + 目录模板`；app_dir 由百度 app_key 固定，改配置前先确认。v0.29.2 已做斜杠折叠（用户会填 `/apps/x/` 导致 `//`）。空 secret 一律跳过（前端不回填密码框，否则会覆盖真密钥）。
- `sync_cloud.py` / `track_export.py` 必须**纯标准库**（服务器 shared_venv 无 requests/qiniu）。

## 最重要的教训（按代价排序）
- **C19 读值回写**：jspreadsheet 每行是 **[行号, 字段名, 值] 三格**，`children[1]` 是字段名。v0.28 按两列取值 + 自动保存 → 线上写坏 12 处。读值优先官方 API `getData()`；DOM 禁写死下标；必须有 sanity 闸（值==字段名即判读错）；验证用临时数据；审计日志必记 `old_value`（全靠它还原）。还原脚本思路：`new_value == field` 是污染特征 → 按 (工作簿,行,字段) 取最早一条的 `old_value`。
- **C17 不可假定第三方组件**：关键数据不依赖编辑器派不派发事件；离场边界直接读 DOM；关键写入有 localStorage 落盘草稿（启动/切回前台自动补交）；切后台**先收值再冲刷**；验证不得自证循环。
- **C18 用户打扰幂等**：只做一次的引导必须持久化到 localStorage（键名带版本号），不能只记内存变量——国产 ROM 会重载页面。
- **C12 多应用同域**：一切跳转用 `window.SUP_BASE` 锚定本应用，禁硬编码 `/`（`'/'` 是网关首页，会把用户丢进别的应用再反复试密码）。
- **C14 保存纪律**：防抖保存必须在返回/路由/切后台/关页四条路径冲刷；任务快照 wid/ridx/values；卸载用 `fetch(keepalive)`，**禁用 `sendBeacon`**（带不了 `X-Sup-Token`）；`datetime` 判断先于 `date`；中文下拉**不能用字典序**（六标/七标/十标 会排错）。
- **C13 可见性 ≠ 权限**：边界只在服务端；禁 URL 参数当权限开关；`int(r['a'] or 1)` 把 0 吃成 1；不可逆操作照名确认 + 自建弹层（`window.prompt` 在 Android WebView 不生效）。
- **C16 脚本健壮**：可能不存在的元素必须 `on()`/空值守卫，否则顶层裸 `addEventListener` 抛 TypeError → **整脚本加载失败、整页失效**，症状像后端问题。
- **C20 EXIF**：Canvas 压缩必然丢 EXIF；回写时 Orientation 强制 1、删尺寸标签、删 MakerNote；失败静默退回原行为。压缩图 EXIF 是 v0.30 用 piexifjs 回写的；坐标另作为**水印文字**烙在画面上。
- **C15 就地更新**：「更新 Excel」必须保留原工作簿 id（重新上传永不覆盖、永远新建，实测 ids `[2,3]`→`[2,3,66]`）；以既有行顺序为基准合并；只有「可编辑列」旧值非空优先；写库前自动备份可回滚。
- **两条事实备查**：①导出的 Excel **自带「参数」sheet** → "下载→改参数→重传"一直可用；②`/api/workbooks`（App 端）**只列未下架的**，别误判成"被删"。
- **排查"登录不上"**：先看日志有没有失败登录请求。一条都没有 ⇒ 请求没到服务器 ⇒ 问题在客户端/落点，**不在鉴权**。
- **写测试**：多身份必须各用独立 `test_client`（session 会互相覆盖，表现为"管理员被判 403"）；不写死模板文件名与期望；每套用例配自家表头的基底。
- **`validate.py`/`check_invariants.py`/`compare.py`/`make_baseline.sh` 仍是空骨架**（模板遗留，`REQUIRED=["关键列1"]`、`main.py --out` 不存在），本项目用 `tests/run_all.sh`。
- 回归：`bash tests/run_all.sh`（**229 项**：unit 108 + e2e 101 + exif 20），全绿才提交。模板体检 `python tools/check_template.py data/*.xlsx`。
- 未决：用户报"视频保存后华为弹出应用信息"。JS 侧已修（引导幂等化）；原生 `openAutostartSettings()` 已加"已满足就不跳"（69da392 / v0.28.1）；**v0.9.0（2026-10-14）已带该修复出包，此条已了结**；若用户再报，需弹窗原文/截图。

## 验收流程设计（2026-10-14，doc/019）
- **v0.31 已上线**：wb=6 就地更新为含流程版本（**id 仍为 6、600 行不变、已自动备份可 rollback**），`#/wb/6` 现在显示 8 环节验收界面。
- 需求表 `data/验收流程设计/造林项目-验收模板-含验收流程.xlsx`：8 sheet，`流程-人工造林` **8 环节 × 12 列**（第 12 列=「填报控件」驱动界面）；另有 `流程-退化林修复`(9)、`流程-四旁植树`(5)、`因子控件对照`(23 因子)。
- 产出：`flow_parser.py`（B18/B19）+ `static/flow.js`（B20，独立模块**不塞进 app.js**）+ `main.py` 的 `GET/POST /api/workbooks/<id>/flow`。`workbooks.flows` 列（空→退回网格填表）、`flow_records` 表。
- **⚠️ 上线踩的 5 个真 bug（都是"看着没事实际坏了"，已全修）**：
  1. **`api()` 只是 fetch 包装，不自动序列化 JSON** —— 裸传对象 → **415**，症状"点提交没反应也不报错"。必须 `JSON.stringify` + `Content-Type: application/json`
  2. **`datetime.time` 未处理** —— 「混交比例」列 number_format 被误设 `h:mm`，openpyxl 把 0.29375 读成 `time(7,3,0)` → 落库 `'07:03:00'`。`_norm_cell` 补 time 分支
  3. **`init_db` 插默认用户 `VALUES(?,?,?)` 3 值但表已 4 列**（v0.21 加 `pwd_ver` 后遗留）→ **全新库首启必炸**，已列名化
  4. **校验顺序**：转序闸门必须排在必填校验**之前**，否则前序没做时先报"你填错了"
  5. **`read_only` 工作簿流只能顺序读一次** —— 为看表头二次 `iter_rows` → 空值 → 误报"缺填报控件列"
- **括号感知分割**（同 C19 本质）：`字段=类型(参数;参数)` **不能按 `;` 裸切**（`下拉(符合设计;部分完成;未完成)` 会丢选项）。必须先数括号深度。凡"分隔符+可带参值"的配置语法都适用。
- **规则表顺序敏感**：自然语言解析的兜底规则（`照片|现场`）放最后且**命中即停**，否则每环节多一个冗余"现场照片"按钮。
- **测试选文件的坑**：`data/**/*.xlsx` 会先匹配到 `data/workbooks/<id>/source.xlsx`（无流程 sheet）或「副本.xlsx」（**11 列无「填报控件」列**）→ controls 全空、必填校验失真。必须排除 `workbooks/` 与 `.~`，并校验表头含「填报控件」。
- **7 个数据坑**：①`总株数  ` 表头**尾随 2 空格**（key 长度 5），列名匹配必须 trim ②注册表第 2/6 行同名同优先级，第 6 行是演示行 ③**第 2 行条件字段=1（无条件）→ 600 行全部小班都跑人工造林**（配置问题，需管理层改）④`验收结果选项` 参数页缺 `整改后合格` ⑤`混交比例` number_format 误设 `h:mm`（已修）⑥`功能` 参数缺「打卡」，📍按钮暂不受功能开关约束 ⑦data 脏数据（项目类型尾随空格 / 澧江街与澧江街道 / 南昏与南昏村）。
- 附件三类按钮：`photo`（App 内实时拍摄禁相册）+ `upload`（照片/PDF 仍留痕，**PDF 上传通道尚未实现**）+ `punch`（定位打卡）。**成活率/保存率自动算不可手填**；提交闸门**逐项列出缺什么**。
- 回归：`bash tests/run_all.sh`（**229 项**：unit 108 + e2e 101 + exif 20），全绿才提交。
- 未决：用户报"视频保存后华为弹出应用信息"。JS 侧已修；原生 `openAutostartSettings()` 已改；**v0.9.0 已带修复出包，已了结**。

## 安卓发版（GitHub Actions，2026-10-14 起）
- 仓库 `git@github.com:sux789/hqz-supervision.git`（**公开**）；`gh` 已登录 sux789。
- **发版流程**：改 `android/app/build.gradle` 的 versionCode（必须递增，覆盖升级）+ versionName → commit → `git push origin main` → `git tag -a v0.x.y` → `git push origin v0.x.y` → `release.yml` 自动构建**正式签名** APK 并发布到 GitHub Releases（约 2 分钟）。
- 签名 Secret 早已配好（KEYSTORE_BASE64/KEY_ALIAS/KEYSTORE_PASSWORD/KEY_PASSWORD，与 v0.8.0 同一 keystore）；重建用 `set_gh_secrets.sh`。缺 Secret 时 release.yml 自动退回 debug 签名。
- 产物名固定 `app-release.apk`；直链 `https://github.com/sux789/hqz-supervision/releases/download/<tag>/app-release.apk`（公开可下，无需登录）。
- main 推送且 paths 命中 `android/**`、`www/**`、`package.json`、`capacitor.config.json` 时，额外触发 `android-build.yml` 出 **debug** 包（Actions 工件，要登录才能下）——只要正式版即可，别把工件链接当交付物。
- **有意未入库**（公开仓库）：`doc/合同-*.png`（签署合同）、`doc/*监理规划/实施细则.docx`、`data/验收流程设计/…副本.xlsx`（本地工作副本，且干扰测试选文件）。要入库需用户显式确认。
- `.DS_Store` 已于 v0.9.0 提交取消跟踪（v0.9.0 = 3868809）。
## v0.32.1（2026-10-14）：C22 语法兼容 + 错误日志
- **C22**：静态 JS 只写 Chrome 73（项目最老实测 WebView，SM-G9860）支持的语法——禁 `?.`/`??`/`replaceAll`/`Array.at`，等价写 `x != null ? x : 默认`；`node --check` 不能证明老内核可用。
- 日志现场：前端错误（含**脚本语法错误**）→ `/api/jslog` → `data/logs/js.log`；服务端异常与验收提交每种结局 → `data/logs/app.log`（`[flow]`/`[EXC]`）。后台「运行日志」卡片可看各 200 行。排查红色报错顺序：后台卡片 → js.log → app.log。
- 当天 POST flow 两次 200 = 提交其实成功；"红色报错拿不到现场"正是加日志的动机。批量正则改代码必须逐行复核（perl 替换曾丢默认值）。
