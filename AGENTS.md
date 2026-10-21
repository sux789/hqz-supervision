# AGENTS — AI 代理入口（自动加载）

> 本文件遵循 AGENTS.md 约定，可被支持该标准的 AI 编码工具自动注入上下文。
> 适配器：Claude Code 用 `ln -s AGENTS.md CLAUDE.md`；Cursor 旧版复制到 `.cursor/rules/project.mdc`；TRAE 复制到 `.trae/rules/project.md`。

## 开工第一件事：读 4KB，不读 30KB

```bash
bash tools/ai.sh brief      # → doc/brief.md：现在 / 必须知道 / 下一步 / 命令
```

它由 `STATE.md + 闸门结果 + git + 约束表` 现算，**替代「通读全部入口文件」**。
下面清单改为**按需回查**（简报指到哪条看哪条）：

1. `doc/024-AI高效协作手册.md` — 方法与判据（改代码前读 §4 流水线，并行前读 §5）
2. `AI_INSTRUCTIONS.md` — 协作协议 + 完成定义 DOD（★ 项由机器判定）
3. `CONSTRAINTS.md` — 硬约束注册表（违反 = 返工）
4. `BLOCKS.md` — 积木注册表（指令先报编号，定位再用 `bash tools/ai.sh map`）
5. `GLOSSARY.md` — 术语表（名词有歧义先查）
6. `STATE.md` — 当前状态（简报已摘要）

## 一条命令判定「做完」

```bash
bash tools/run.sh              # 七门：回归 / JS+C22 / 索引 / 占位符 / 密钥 / 预算 / 模板
bash tools/run.sh --fast       # 提交前快查（跳过回归，<1s）
```

结果写 `doc/health/last_gates.json`（机器读 `id+status`，人读中文）。**全绿之前不要说完成，也不要手工替它判断。**

## 并行开发（多 Agent）

```bash
bash tools/ai.sh task new --title "..." --scope "main.py:1780-1830" --by <名字>
bash tools/ai.sh task claim T-20261018-01 --by <名字>   # 范围重叠会当场拒绝
bash tools/ai.sh task check                            # 集成前复核
```

铁律：**一个文件同时只有一个 owner**；入口文件（本文件、AI_INSTRUCTIONS、CONSTRAINTS、BLOCKS、GLOSSARY、STATE）
只许集成者改，worker 把建议写进任务卡 notes。详见 `doc/024` §5。

## 交付前 / 排障时

- `bash tools/ai.sh explain` — 改动评审单 `doc/review.md`（风险等级由规则算，不是模型说）
- `bash tools/ai.sh triage` — 把 FAIL 变成 `doc/health/TRIAGE.md`（事实 + 候选原因 + 处置顺序）
- `bash tools/log.sh [app|js|flow|exc]` — 三层日志（排查顺序 [EXC] → js.log → [flow]）
- `bash tools/align.sh --open` — 人机对齐看板 `doc/align.html`
- `bash tools/selftest.sh` — 改了 `tools/` 必跑
- `bash tools/dev.sh start|stop|status` — 本地服务 8720；`bash tools/snapshot.sh save` — 危险操作前快照
- `python tools/check_template.py data/*.xlsx` — 模板体检（改模板后上传前必跑）

## 改动完成后

写 `doc/NNN-版本_主题.md`（模板 `doc/001-迭代文档模板.md`，七段式，反转记录不可省），
同步 `STATE.md` 与 `CONSTRAINTS.md`；提交信息带 `Refs: doc/NNN`（commit-msg 强制；纯格式类用 `[no-refs]` 并写明原因）。
