# AGENTS — AI 代理入口（自动加载）

> 本文件遵循 AGENTS.md 约定，可被支持该标准的 AI 编码工具自动注入上下文。
> 适配器：Claude Code 用 `ln -s AGENTS.md CLAUDE.md`；Cursor 旧版复制到 `.cursor/rules/project.mdc`；TRAE 复制到 `.trae/rules/project.md`。

## 任何代码改动前，按顺序必读

1. `AI_INSTRUCTIONS.md` — 协作协议与铁律
2. `CONSTRAINTS.md` — 硬约束注册表（违反=返工）
3. `BLOCKS.md` — 积木注册表（改动先报积木编号）
4. `STATE.md` — 当前状态与已知问题

## 改动完成后，全绿才提交

- `bash tests/run_all.sh` — 全量回归（单元 + e2e + EXIF，当前 237 项；数量随版本增长，见 STATE.md「回归」行）
- 写 `doc/NNN-版本_主题.md`（模板见 doc/001-迭代文档模板.md，七段式：需求/验证/设计/代码/测试/反转/部署），同步更新 `STATE.md` 与 `CONSTRAINTS.md`
- 注：`compare.py` / `check_invariants.py` / `make_baseline.sh` 是数据管线类项目的「双跑对比 + 勾稽断言」模板，本项目未启用（CHECKS 为空、生成命令与 Flask 入口不匹配）；未来若有"生成输出目录"类需求再接线
