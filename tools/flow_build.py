#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业务员流程文档 → 引擎配置（B21 CLI，doc/027）

一条命令走完「校验 → 生成 → 生效」：

    python tools/flow_build.py data/验收流程设计/流程配置-业务员填写版.xlsx --check
    python tools/flow_build.py 该文件.xlsx --emit build/flows.json
    python tools/flow_build.py 该文件.xlsx --apply 6        # 写到线上工作簿 id=6
    python tools/flow_build.py 该文件.xlsx --apply 6 --dry-run

`--apply` 只改 `workbooks.flows` 一列（数据行、模板、照片一概不动），
改前自动把 sqlite 文件复制到 data/backups/，出问题直接把备份拷回来即可。
"""

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import flow_config as fc  # noqa: E402

DB = BASE / 'data' / 'app.sqlite3'
BACKUP_DIR = BASE / 'data' / 'backups'


def _print_summary(res):
    s = res.get('summary') or {}
    print(f'流程 {s.get("flows", 0)} 条 / 环节 {s.get("steps", 0)} 个 / '
          f'核验项 {s.get("fields", 0)} 个 / 多次环节 {s.get("multi_steps", 0)} 个')
    print(f'作用对象：{"、".join(s.get("subjects") or []) or "—"}')
    for r in res.get('registry', []):
        tag = '独立入口' if r.get('mount') == '独立入口' else f'条件 {r["cond_field"]}={r["cond_val"]}'
        print(f'  · {"启用" if r["enabled"] else "停用"} {r["sheet"]}  优先级 {r["prio"]}  {tag}')


def cmd_check(path):
    res = fc.parse_path(path)
    _print_summary(res)
    if res['warnings']:
        print(f'\n提示 {len(res["warnings"])} 条（不影响生成）：')
        print(fc.check_lines({'errors': [], 'warnings': res['warnings']}, limit=50))
    if res['errors']:
        print(f'\n✗ 错误 {len(res["errors"])} 条 —— 改完再跑：')
        print(fc.check_lines({'errors': res['errors'], 'warnings': []}, limit=50))
        return 1
    print('\n✓ 校验通过，可以直接 --emit / --apply')
    return 0


def cmd_emit(path, out):
    res = fc.parse_path(path)
    if res['errors']:
        print(fc.check_lines(res))
        return 1
    payload = {
        'schema': 'flow_config/1',
        'source': str(Path(path).name),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'registry': res['registry'],
        'flows': res['flows'],
        'warn': [{'level': 'info', 'text': w['msg']} for w in res['warnings']],
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'已生成 {out}（{out.stat().st_size / 1024:.1f} KB）')
    return 0


def _backup_db(db):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / f'app.sqlite3.bak_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    shutil.copy2(db, dst)
    return dst


def _plan_migrate(con, wid, old_reg, new_reg):
    """老记录 `flow_sheet` 没有入口后缀（v0.35）→ 补成 `·验收`。

    **为什么必须迁**：v0.36 的流程唯一键带入口后缀（`流程-X·验收`），
    引擎查记录用 `WHERE flow_sheet=?` 拿新名字去查，老记录一条都读不到 ——
    现场表现就是"昨天填的全没了"，比报错更吓人。

    归属选「验收」：v0.35 时一份文档只有验收流程，语义无歧义；
    巡视是 v0.36 新开的入口，不可能有老记录。
    """
    try:
        new_sheets = {r['sheet'] for r in new_reg}
        plan = []
        for r in (old_reg or []):
            os_ = r.get('sheet') or ''
            if not os_ or '·' in os_:
                continue                      # 已经是新格式，不动
            cand = [s for s in new_sheets if s.rsplit('·', 1)[0] == os_]
            if not cand:
                continue                      # 新配置里没这条流程了，留着不动
            tgt = next((s for s in cand if s.endswith('·验收')), cand[0])
            n = con.execute('SELECT COUNT(*) AS n FROM flow_records'
                            ' WHERE workbook_id=? AND flow_sheet=?', (wid, os_)).fetchone()['n']
            if n:
                plan.append((os_, tgt, int(n)))
        return plan
    except sqlite3.OperationalError:
        return []                             # 老库还没有 flow_records 表


def cmd_apply(path, wid, db=DB, dry_run=False):
    res = fc.parse_path(path)
    if res['errors']:
        print(fc.check_lines(res))
        return 1
    db = Path(db)
    if not db.exists():
        print(f'✗ 找不到数据库 {db}')
        return 1
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute('SELECT id,name,flows FROM workbooks WHERE id=?', (wid,)).fetchone()
    if not row:
        con.close()
        print(f'✗ 工作簿 id={wid} 不存在')
        return 1
    try:
        old = json.loads(row['flows'] or '{}')
    except ValueError:
        old = {}
    payload = {
        'schema': 'flow_config/1',
        'source': str(Path(path).name),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'registry': res['registry'],
        'flows': res['flows'],
        'warn': [{'level': 'info', 'text': w['msg']} for w in res['warnings']],
    }
    print(f'工作簿 #{wid}「{row["name"]}」')
    print(f'  现有流程：{[r.get("sheet") for r in (old.get("registry") or [])]}')
    print(f'  即将写入：{[r["sheet"] for r in res["registry"]]}')
    plan = _plan_migrate(con, wid, old.get('registry'), res['registry'])
    if plan:
        print('  已有记录要补入口后缀（不迁就读不到）：')
        for o, t, n in plan:
            print(f'    {o} → {t}（{n} 条记录）')
    if dry_run:
        print('  --dry-run：只看不做')
        con.close()
        return 0
    bak = _backup_db(db)
    print(f'  已备份数据库 → {bak}')
    con.execute('UPDATE workbooks SET flows=? WHERE id=?',
                (json.dumps(payload, ensure_ascii=False), wid))
    moved = 0
    for o, t, _n in plan:
        cur = con.execute('UPDATE flow_records SET flow_sheet=?'
                          ' WHERE workbook_id=? AND flow_sheet=?', (t, wid, o))
        moved += cur.rowcount
    con.commit()
    con.close()
    if moved:
        print(f'  ✓ 已迁移 {moved} 条历史记录到带入口后缀的流程名')
    print('  ✓ 已写入 workbooks.flows —— App 下次打开该工作簿即按新流程显示（无需重新上传 Excel）')
    return 0


def main():
    ap = argparse.ArgumentParser(description='业务员流程文档 → 引擎配置')
    ap.add_argument('doc', help='流程配置-业务员填写版.xlsx')
    ap.add_argument('--check', action='store_true', help='只校验（默认动作）')
    ap.add_argument('--emit', metavar='JSON', help='生成 flows 配置 JSON')
    ap.add_argument('--apply', metavar='WID', type=int, help='写入指定工作簿 id')
    ap.add_argument('--db', default=str(DB))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    p = Path(a.doc)
    if not p.exists():
        print(f'✗ 文件不存在：{p}')
        return 1
    if a.apply:
        return cmd_apply(p, a.apply, a.db, a.dry_run)
    if a.emit:
        return cmd_emit(p, a.emit)
    return cmd_check(p)


if __name__ == '__main__':
    sys.exit(main())
