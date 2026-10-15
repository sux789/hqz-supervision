# STATE 历史归档（v0.29.x 及以前）

> 2026-10-15 从 STATE.md 迁出（STATE 自身承诺 ≤25 行）。跨会话恢复一般不需要读本文件；要考古时再翻。

- **v0.29.x 摘要（2026-09-17 下午，详录见 doc/013~016 与当日记忆）**：①v0.29 后台恢复「云同步」卡片（v0.22 注释的解锁，JS 守卫早已就位）②v0.29.1 修「开启失败缺配置」——前端不回填密码框提交空串，后端校验 probe 却拿空串覆盖真实密钥 → 口径与落库对齐（空 secret 跳过）+ 报错改人话（`_SYNC_LABELS`）③v0.29.2 远端路径折叠重复斜杠（面板提示曾显示 `//`）④**v0.29.3 新增工作簿级保留占位符 `{{工作簿id}}/{{工作簿名}}/{{工作簿文件名}}`**，并把在线工作簿 6/7 的「目录」改为 `{{工作簿id}}_{{工作簿名}}/{{小班号}}`（用「更新 Excel」+ 各自当前源模板做基底，100% 匹配零冲突、有备份可回滚）→ 百度远端路径变为 `/apps/supervision/{工作簿id}_{Excel名}/{小班号}/文件.jpg`；「网盘子目录」已清空（去掉重复的 supervision）
- **云同步线上事实（2026-10-09）**：`sync_enabled=1`、`app_dir=/apps/supervision/`、`prefix=''`；全链路已通（09-17 12:53/12:54 两张照片 `baidu_ok`）。**排查"照片没同步"第一步永远先看 `sync_enabled`**（关闭时 App 拿到 false 就静默跳过，`/api/photo` 零请求）。`photo_sync` 里 61 条 `received` 是 09-13 遗留 video 记录（C09 视频不上云），不是待推送照片
- **v0.28.2 事故复盘（doc/016，C19）**：v0.28 把「字段名」当值写坏线上 12 处（jspreadsheet 每行是 **[行号,字段名,值] 三格**，按两列取 `children[1]` 读到字段名 → 自动落库）；已用 `accept_logs.old_value` 全部精确还原。教训：读值优先官方 API、结构必须实测、必须有 sanity 闸、验证用临时数据、审计日志记 old_value。**已全部精确还原**（执行前备份 DB `data/app.sqlite3.bak_20260917_111631`），修复三重保险：官方 API 取值 / 取最后一格 input / 值等于字段名即丢弃告警。**三条自我失误**：又一次替第三方组件假设结构（刚写完 C17 就犯）、**看到异常信号没追**、**用真实工作簿做调试**
- **v0.28.1 变化（修"视频保存后华为弹出应用信息"，doc/015 §六，C18）**：根因是权限引导幂等标志只存内存（`permsAsked`），页面重载归零 → 每次回来再弹。修复：JS 侧改 localStorage 持久化（`hqz_sup_perms_asked_v1`，改网页即生效）+ 原生 `openAutostartSettings()` 已在白名单不跳（随 v0.9.0 出包生效）
- **v0.28 变化（编辑不丢：保存可靠性重做，doc/015，C17）**：用户第二次报"填了备注切走再回来就没了"。三条不依赖第三方组件事件行为的路径：①离场/切后台直接从 DOM 读回值 `syncGridIntoAllRows()` ②文档级捕获监听 input/keyup/focusout ③落盘草稿 localStorage（启动/切回前台自动补交，成功才清，超 12 小时丢弃）。`visibilitychange` hidden 时**先收值再冲刷**；`flushRowSaveUnloading` 先写草稿再发 keepalive；新增 `buildSaveJob()`
- **v0.27 变化（后台隐藏轨迹卡片，doc/014，C16）**：整张「轨迹下载」卡片注释（后端 `/admin/api/tracks*` 与数据保留）；顺带修 `admin.js` 顶层裸 `addEventListener` 的 TypeError 地雷（C16 由此而来）
- **v0.26 变化（更新 Excel：按唯一键合并 + 回滚，doc/013，C15）**：`merge_rows()` 纯函数 + `POST /api/workbooks/<id>/update`（`?dry=1` 预览/名称确认）+ `POST /rollback` + `workbook_backups` 表（每簿 5 份）。以既有行顺序为基准原地合并、新行追加末尾、旧行保留、按列名对齐；只有「可编辑列」（旧∪新）旧值非空优先
- **v0.25 变化（保存不再丢 + 后台导出筛选 + 回跳，doc/012，C14）**：防抖保存四条离场冲刷路径 + 待保存任务快照 wid/ridx/values + 逐键捕获；「下载 Excel」筛选弹框（参数 `导出筛选`，只筛行）；`/admin` 登录 `?next=admin` 回跳
- **v0.24 变化（模板下架 + 危险操作确认，doc/011，C13）**：`workbooks.is_active` 软删除 + `?purge=1` 彻底删除 + 照输模板名确认（自建 DOM 弹层，禁 window.prompt）。两个坑：`x or 1` 把 0 吃成 1；Flask test_client 共享 cookie 测多身份要独立 client
- **v0.23 变化（唯一键参数化 + 通用化，doc/010，C11）**：数据 sheet 定位（优先 `data`）、`unique-key` 参数、行标识全链路 `keyCol()/keyVal()`、日志字段/百分比列参数化、删除工作簿连带清理；新增 `tools/check_template.py` 体检器与仓库内回归网（tests/）
- **v0.22 变化**：导出改 fetch 文件流 → 原生 saveFile（App 壳禁止外跳）；注释后台「云同步/用户管理」卡片（接口与数据保留）
- **v0.21 变化**：修改密码（自己改/管理员重置）+ 令牌 3650 天 + `pwd_ver` 版本化令牌（改密即失效旧令牌）
- **v0.20 变化**：轨迹导出改 SHP（`track_export.py` 手写 shapefile，对齐 hqz-survey R17）；功能互斥（录像/切小班/返回列表自动停轨迹并上传）
- **v0.19 变化**：轨迹改原生底层记录（BgLocationPlugin/BgLocationService，仅 GPS_PROVIDER，前台服务保活）
- **v0.18 变化**：录像可靠性三项——前台服务保活（KeepAliveService）/ 后台权限自动申请 ensureBackground() / 补记时序修复
- **v0.17 变化**：修"录像返回后跳列表/文件名没进记录"——系统相机重载 WebView 丢 hash；localStorage 记位回跳 + 原生落盘录像结果 + 压缩效果提示
- **v0.16 变化**：压缩与同步解耦（C10）——图片压缩/视频压缩/云同步后台三块各自保存；新增 `/api/compress/params`
- **v0.15/v0.15.1 变化**：长期令牌（180 天存 localStorage，`X-Sup-Token`）+ `/api/me` 恢复身份 + 登录后一次性申请权限；启动闪屏替换（tools/make_app_icons.py）
- **v0.14.x 变化**：原生录像压缩（Media3 Transformer）；启动图标重做
- **v0.13.x 变化**：30 天持久 cookie；`recordVideo()` 原生录制不走 WebView 文件回传；`video_rec_mode` 可切系统相机/页面录制
- **v0.12 变化**：视频容器只认 MP4/H.264，不支持则自动转系统相机
- **v0.11/v0.11.1 变化**：视频只存手机本机不上传（C09）；Manifest 加 RECORD_AUDIO，插件 ensureMedia()/saveVideo()
- **v0.10.x 变化**：视频功能上线（服务器 ffmpeg 压缩）；改手机端录制压缩为主（MediaRecorder 边录边压）
- **v0.9.x 变化**：相片云同步上线（doc/007，七牛 zz-1 暂存 + data/pending 缓冲 → 百度网盘，状态机 received→qiniu_ok→baidu_ok）；水印版式优化（半透明白底块+实心黑字，默认压缩 1440/0.80）；时间戳水印首行（C06）；积压补推后台线程
- **历史已知问题**：v0.8 前旧工作簿无 source 模板导出需重传；v0.8.2 前已保存的行验收人为空；七牛回读域名未开放（pending 缓冲为主回读源）；百度 mkdir 对已存在目录偶发时间戳副本（无害）
- **旧「能跑」行（已被 v0.32.5 行取代）**：v0.9 起上线 https://forest.bibook.top/supervision；账号 雷华雄/lhx123
