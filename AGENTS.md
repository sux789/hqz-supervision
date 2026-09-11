# AGENTS — AI 代理入口（自动加载）

> 本文件遵循 AGENTS.md 约定，可被支持该标准的 AI 编码工具自动注入上下文。
> 适配器：Claude Code 用 `ln -s AGENTS.md CLAUDE.md`；Cursor 旧版复制到 `.cursor/rules/project.mdc`；TRAE 复制到 `.trae/rules/project.md`。

## 任何代码改动前，按顺序必读

1. `AI_INSTRUCTIONS.md` — 协作协议与铁律
2. `CONSTRAINTS.md` — 硬约束注册表（违反=返工）
3. `BLOCKS.md` — 积木注册表（改动先报积木编号）
4. `STATE.md` — 当前状态与已知问题

## 改动完成后，全绿才提交

- `python check_invariants.py 输出目录/` — 业务勾稽断言（证明输出对）
- `python compare.py baseline/latest/ 输出目录/` — 双跑对比（证明没改坏；基线由 `bash make_baseline.sh` 生成）
- 写 `doc/NNN-版本_主题.md`（模板见 doc/001），同步更新 `STATE.md` 与 `CONSTRAINTS.md`
