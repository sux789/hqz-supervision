# STATE — 当前状态快照

> 每次迭代后更新，≤25 行。跨会话恢复先读本文件，不翻 doc/ 归档。

- **版本**：v0.9 ｜ **更新**：2026-09-12（深夜）
- **能跑**：`python main.py` → http://127.0.0.1:8720（雷华雄/lhx123 登录）：上传 Excel→列表选择→参数驱动网格填表→保存；后台 /admin 模板管理+下载 Excel+轨迹下载+验收变更日志+**相片云同步设置/状态面板**
- **v0.9 变化**：相片云同步上线（doc/007 实现）——App 拍照在开关开启时 POST /api/photo 中转：七牛 zz-1 暂存 + data/pending 缓冲 → 百度网盘 /apps/book_translator/supervision/{参数目录}/{文件名}.jpg（与手机相册 Pictures/ 镜像）；状态机 received→qiniu_ok→baidu_ok，百度成功删 pending、七牛副本按保留期（30 天）清理；后台同步设置卡（开关/凭证/token 导入/状态面板/手动重推）；参数 sheet 新增「压缩最长边/压缩质量」；纯标准库实现（服务器 shared_venv 无 requests/qiniu）；本地与线上均 curl 全链路验证 state=baidu_ok
- **已知问题**：v0.8 前上传的旧工作簿无 source 模板，导出需重新上传；轨迹记录不支持后台运行；v0.8.2 前已保存的行验收人为空；七牛 io 回读域名未开放（需绑定下载域名），当前回读兜底不可用——pending 缓冲为主回读源；百度 mkdir 对已存在目录偶发产生 `名字_时间戳` 副本目录（无害，生产文件名带时间戳不碰撞）
- **能跑**：https://forest.bibook.top/supervision 已上线 v0.9（2026-09-12 ./deploy.sh 部署；云凭证已配置、同步开关已开启；线上拍照实测 baidu_ok）；账号 雷华雄/lhx123
- **下一步**：1. [x] 相片云同步上线（doc/007） 2. [ ] GitHub Secrets 配置（KEYSTORE_BASE64/KEY_ALIAS/KEYSTORE_PASSWORD/KEY_PASSWORD）→ tag 触发 CI 出正式签名 APK 3. [ ] APP 备案（阿里云/域名备案主体入口，填包名 top.bibook.supervision + SHA256 指纹，见 android/keystore-info.txt）
- **最近迭代文档**：doc/007-相片云同步设计.md
- **活跃约束**：C01–C08（详见 CONSTRAINTS.md）

## 资产活跃度（每 5 版评审时更新）

| 资产 | 上次发挥作用的版本 | 作用 | 处置 |
|---|---|---|---|
| compare.py | — | 尚未跑通 MVP | 观察 |
| CONSTRAINTS.md | v0.1 | 注册 C01/C02 | 保留 |

> 连续 5 版「未发挥作用」→ 按 README「资产进化」三选一：修复 / 合并 / 删除。
