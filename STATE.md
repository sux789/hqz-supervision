# STATE — 当前状态快照

> 每次迭代后更新，≤25 行。跨会话恢复先读本文件，不翻 doc/ 归档。

- **版本**：v0.8.1 ｜ **更新**：2026-09-12
- **能跑**：`python main.py` → http://127.0.0.1:8720（雷华雄/lhx123 登录）：上传 Excel→列表选择→参数驱动网格填表→保存；后台 /admin 模板管理+下载 Excel+轨迹下载+验收变更日志
- **v0.8.1 变化**：拍摄记录改为显示完整文件 path（App: Pictures/{目录}/{文件名}.jpg；浏览器: 文件名.jpg），强化 C07 无预览（修复线上 v0.7.x 相册预览大图导致手机 WebView 死机）；缓存熔断 v=0.8.1
- **v0.8 变化**：相片不落服务器（App 存相册/浏览器下载，页面仅路径提示，C07）；导出按上传模板回填（前端 /export + 后台 download 同链路，格式全保留）；验收联动（选结果填人/日期，选空清三字段）；验收4字段变更日志（accept_logs，后台可查）；图片压缩 1600px/0.85；{{拍照人}} 占位符
- **已知问题**：v0.8 前上传的旧工作簿无 source 模板，导出需重新上传；轨迹记录不支持后台运行（页面切换即停）；v0.8.1 前的旧拍摄记录仍只存文件名（无完整 path）
- **能跑**：https://forest.bibook.top/supervision 已上线 v0.8.1（2026-09-12 ./deploy.sh 部署并 curl 验证）；账号 雷华雄/lhx123
- **下一步**：1. [x] ./deploy.sh 上线 v0.8（含 v0.8.1） 2. [ ] GitHub Secrets 配置（KEYSTORE_BASE64/KEY_ALIAS/KEYSTORE_PASSWORD/KEY_PASSWORD）→ tag 触发 CI 出正式签名 APK 3. [ ] APP 备案（阿里云/域名备案主体入口，填包名 top.bibook.supervision + SHA256 指纹，见 android/keystore-info.txt）
- **最近迭代文档**：doc/006-v0.8_拍照本地化与验收联动.md
- **活跃约束**：C01–C07（详见 CONSTRAINTS.md）

## 资产活跃度（每 5 版评审时更新）

| 资产 | 上次发挥作用的版本 | 作用 | 处置 |
|---|---|---|---|
| compare.py | — | 尚未跑通 MVP | 观察 |
| CONSTRAINTS.md | v0.1 | 注册 C01/C02 | 保留 |

> 连续 5 版「未发挥作用」→ 按 README「资产进化」三选一：修复 / 合并 / 删除。
