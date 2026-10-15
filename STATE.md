# STATE — 当前状态快照

> 每次迭代后更新，≤25 行。跨会话恢复先读本文件，不翻 doc/ 归档。v0.29 及以前的历史行已迁至 `doc/STATE历史归档.md`。

- **版本**：v0.32.5 ｜ **更新**：2026-10-15
- **v0.32.3~0.32.5（网页三连修，2026-10-14~15）**：③v0.32.3 修「带过来的数据看着填了、提交判没填」——数值×3 标准值改「实测·长/宽/深」占位、下拉未选改「请选择」，与 F.val 所见即所填（C22；方法论：线上真实数据离线重放 gate() 二分）②v0.32.4 验收步骤条：flex-wrap 换行、三态配色+图例、**未解锁环节可点开只读预览**；style.css 两个模板补 ?v=；改模板后先本地 test_client 渲染再部署 ①v0.32.5 提交留痕审计：flow_records 加 geo/ip 列（纯加列迁移），提交瞬间 best-effort 一次性定位（3s 超时为空不阻塞）+ 服务端取 X-Forwarded-For 第一跳 IP，落环节级行 + app.log [flow] 条目；e2e H12b/c
- **v0.32.1（doc/021，C22）**：老内核（Android 9 / WebView Chrome 73）`??`×18 致 app.js 整段 SyntaxError，全改 `x != null ? x : 默认`；三层日志（前端错误→js.log、未捕获异常→app.log、验收提交各结局→[flow] 条目）+ 后台「运行日志」卡片。排查顺序：后台运行日志 → js.log 找 SyntaxError/资源失败 → app.log 找 [flow]/[EXC]
- **v0.32（doc/020，C21，B21）**：授权即持续 GNSS 跟踪——采集生死权在原生 BgLocationPlugin（load/resume/授权回调三处幂等自启，仅 GPS_PROVIDER），缓冲先落 `files/track_buffer.csv` 再谈内存；网页只是观察者+上传者（attachTrack 按 GPS 时间戳去重+localStorage 水位线，**上传成功才 clearBuffer**）；versionCode 10。明确不做：开机自启/打卡水印走 Web 定位/手动停止按钮。**待真机验证 7 条见 doc/020 §二**
- **v0.31（doc/019，B18/B19/B20）**：验收流程界面**已上线**（wb=6 就地更新）；`flow_parser.py` + `static/flow.js` + `flow_records` 表；转序闸门 409 / 必填逐项 400 / ≥标准自动判。上线踩的 5 个真 bug（api() 必须 JSON.stringify、datetime.time 还原、init_db 列名化、闸门先于必填、read_only 工作簿流只读一次）详见 doc/019
- **v0.30（doc/017，C20，B17）**：piexifjs 保留真 EXIF + 水印坐标/时间优先取原片 EXIF；回写必处理 Orientation=1/删尺寸标签/删 MakerNote 三类标签，任何失败静默退回原行为
- **v0.24~0.29 摘要**：下架三层（C13）、保存纪律（C14）、更新 Excel 就地合并+回滚（C15）、事故复盘 C19、EXIF/C20、云同步面板恢复+工作簿级目录占位符 `{{工作簿id}}_{{工作簿名}}/{{小班号}}`——详见 CONSTRAINTS.md 与 doc/011~016、`doc/STATE历史归档.md`。**⚠️ 云同步线上事实：`sync_enabled=1`，排查"照片没同步"第一步永远先看它**
- **能跑**：https://forest.bibook.top/supervision 已上线 v0.32.5；本地 `python main.py` → http://127.0.0.1:8720（雷华雄/lhx123）
- **回归**：`bash tests/run_all.sh` = 单元 108 + e2e 109 + EXIF 20 = **237 项全绿**（2026-10-15 复核）
- **活跃约束**：C01–C22（详见 CONSTRAINTS.md）
- **最近迭代文档**：doc/021-v0.32.1语法兼容与错误日志、doc/020-v0.32授权即持续GNSS跟踪、doc/019-验收流程界面设计、doc/018-功能总览（开发交接）
- **下一步**：1. [ ] v0.32 出包后真机验证 7 条（doc/020 §二）2. [ ] APP 备案（包名 top.bibook.supervision + SHA256，见 android/keystore-info.txt）3. [ ] 开机自启 BOOT_COMPLETED（v0.32 列为后续项）
- **v0.31 未决**：「功能」缺「打卡」，📍 按钮不受「功能」开关约束（待管理层定）；PDF 上传通道未实现（upload 类型只留痕清单）
- **历史已知问题**：七牛回读域名未开放（pending 缓冲为主回读源）；百度 mkdir 偶发时间戳副本目录（无害）；详见归档

## 资产活跃度（每 5 版评审时更新）

| 资产 | 状态 | 处置 |
|---|---|---|
| tests/run_all.sh | 每版必跑（提交闸，AGENTS.md） | 保留 |
| compare.py / check_invariants.py / make_baseline.sh | 数据管线模板，本项目未启用（AGENTS.md 已标注） | 观察 |
