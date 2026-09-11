"""新旧输出对比：改动后必跑。用法: python compare.py old_out/ new_out/
文本/CSV 按行对比；Excel 按 sheet!cell 对比（需 openpyxl）。退出码非 0 = 有差异。"""
import sys
from pathlib import Path

TOL = 1e-4  # 数值容差

def load_units(path: Path) -> dict:
    """输出 → {单元位置: 值}。按项目输出格式选实现。"""
    units = {}
    if path.suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if c.value is not None:
                        units[f"{ws.title}!{c.coordinate}"] = c.value
    else:  # 文本/CSV/md/json 按行
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            units[f"L{i}"] = line
    return units

def diff(a: dict, b: dict) -> list:
    """差异列表: (位置, 旧值, 新值)。区分 缺失/None/类型/数值。"""
    diffs = []
    for key in sorted(set(a) | set(b)):
        va, vb = a.get(key, "<缺失>"), b.get(key, "<缺失>")
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            if abs(float(va) - float(vb)) > TOL:
                diffs.append((key, va, vb))
        elif va != vb or type(va) is not type(vb):
            diffs.append((key, va, vb))
    return diffs

if __name__ == "__main__":
    old_dir, new_dir = Path(sys.argv[1]), Path(sys.argv[2])
    total, all_diffs = 0, []
    names = {p.name for p in old_dir.iterdir()} | {p.name for p in new_dir.iterdir()}
    for name in sorted(names):
        fo, fn = old_dir / name, new_dir / name
        if not fo.exists() or not fn.exists():
            all_diffs.append((name, "文件缺失", ""))
            continue
        ua, ub = load_units(fo), load_units(fn)
        total += len(set(ua) | set(ub))
        all_diffs += [(f"{name}:{k}", va, vb) for k, va, vb in diff(ua, ub)]
    rate = 1 - len(all_diffs) / max(total, 1)
    print(f"总单元: {total} | 差异: {len(all_diffs)} | 一致率: {rate:.2%}")
    for d in all_diffs[:50]:
        print(d)
    sys.exit(1 if all_diffs else 0)  # 非 0 退出码 → 可挂 pre-commit/CI
