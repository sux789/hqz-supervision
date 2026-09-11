"""结构探测：不懂输入/模板结构时先跑这个，不要猜。
用法: python inspect.py 文件 [文件2 ...]
Excel: 打印 sheet 名、维度、合并区、前 N 行非空单元格（带坐标）；文本/CSV: 打印行数与前 N 行。"""
import sys
from pathlib import Path

N = 5  # 每个 sheet 打印的行数

def inspect_excel(path: Path):
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    print(f"\n=== {path.name} | sheets: {wb.sheetnames} ===")
    for ws in wb.worksheets:
        print(f"\n--- {ws.title} ({ws.max_row}r x {ws.max_column}c) ---")
        if ws.merged_cells.ranges:
            print(f"合并区: {[str(r) for r in list(ws.merged_cells.ranges)[:20]]}")
        for row in ws.iter_rows(min_row=1, max_row=min(N, ws.max_row)):
            vals = [(c.coordinate, c.value) for c in row if c.value is not None]
            if vals:
                print(vals)

def inspect_text(path: Path):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n=== {path.name} ({len(lines)} 行) ===")
    for i, line in enumerate(lines[: N * 4], 1):
        print(f"{i}: {line}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.suffix in (".xlsx", ".xlsm"):
            inspect_excel(p)
        else:
            inspect_text(p)
