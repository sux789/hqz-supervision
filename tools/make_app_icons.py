#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从原始素材生成 Android 全套启动图标（可重复执行）。

背景（2026-09-13）：素材 generated-images/Flat_modern_Android_app_icon_*.png 是
AI 生成的 1024×1024 全幅绿底图，右下角带"AI生成"水印；直接当自适应图标前景会
①水印入图 ②emblem 占高 76% 超出安全区（64%）→ 被圆形遮罩切掉盾牌上下尖角。

做法：
  1. 抹除水印（用平滑背景模型填充右下角，再轻微模糊过渡）
  2. 自适应前景 = 素材缩到画布 84%（emblem 正好 62%，安全区内不被切）+ 边缘延展填满
     （延展用素材自身边缘像素，避免出现"内嵌方块"接缝）
  3. 传统 ic_launcher(.png) 同款；ic_launcher_round 再加圆形遮罩
  4. 自适应背景色 = 素材四角绿

用法：python3 tools/make_app_icons.py [素材路径]
依赖：Pillow、numpy
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BASE = Path(__file__).resolve().parent.parent
DEFAULT_SRC = BASE / 'generated-images' / 'Flat_modern_Android_app_icon___2026-09-12T00-49-25.png'
RES = BASE / 'android' / 'app' / 'src' / 'main' / 'res'
INSET = 0.84          # 素材占画布比例（emblem 76% × 0.84 ≈ 62%，落在自适应安全区内）
DPI_SCALES = (('mdpi', 1), ('hdpi', 1.5), ('xhdpi', 2), ('xxhdpi', 3), ('xxxhdpi', 4))


def clean_watermark(art: Image.Image) -> Image.Image:
    """抹掉右下角"AI生成"水印：用低分辨率背景模型填充该区域。"""
    w, h = art.size
    a = np.asarray(art).copy()
    bg = np.asarray(art.resize((16, 16), Image.BOX).resize((w, h), Image.BILINEAR))
    a[int(h * 0.82):, int(w * 0.74):] = bg[int(h * 0.82):, int(w * 0.74):]
    out = Image.fromarray(a)
    box = (int(w * 0.70), int(h * 0.78), w, h)
    out.paste(out.crop(box).filter(ImageFilter.GaussianBlur(6)), box[:2])
    return out


def foreground(art: Image.Image, green, n: int) -> Image.Image:
    """自适应前景：素材缩到 INSET + 边缘延展填满画布（无内嵌接缝）。"""
    w, h = art.size
    length = round(n * INSET)
    off = (n - length) // 2
    base = Image.new('RGB', (n, n), green)
    base.paste(art.resize((length, length), Image.LANCZOS), (off, off))
    a = np.asarray(base)
    sub = a[off:off + length, off:off + length]
    iy = np.clip(np.arange(n) - off, 0, length - 1)
    ix = np.clip(np.arange(n) - off, 0, length - 1)
    ext = Image.fromarray(sub[np.ix_(iy, ix)])
    mask = Image.new('L', (n, n), 0)
    ImageDraw.Draw(mask).rectangle((off, off, off + length - 1, off + length - 1), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(1, int(n * 0.015))))
    blurred = ext.filter(ImageFilter.GaussianBlur(max(1, int(n * 0.01))))
    return Image.composite(ext, blurred, mask).convert('RGBA')


def circle_mask(size: int, ss: int = 4) -> Image.Image:
    m = Image.new('L', (size * ss, size * ss), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size * ss - 1, size * ss - 1), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def main(src_path: str = str(DEFAULT_SRC)) -> None:
    art = clean_watermark(Image.open(src_path).convert('RGB'))
    green = tuple(int(v) for v in np.asarray(art)[:80, :80].reshape(-1, 3).mean(axis=0))
    for dpi, scale in DPI_SCALES:
        big = foreground(art, green, round(108 * scale))
        big.save(RES / f'mipmap-{dpi}' / 'ic_launcher_foreground.png')
        small = foreground(art, green, round(48 * scale)).convert('RGB')
        small.save(RES / f'mipmap-{dpi}' / 'ic_launcher.png')
        rounded = small.convert('RGBA')
        rounded.putalpha(circle_mask(round(48 * scale)))
        rounded.save(RES / f'mipmap-{dpi}' / 'ic_launcher_round.png')
    (RES / 'values' / 'ic_launcher_background.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        f'    <color name="ic_launcher_background">#{green[0]:02X}{green[1]:02X}{green[2]:02X}</color>\n'
        '</resources>\n', encoding='utf-8')
    print(f'图标已生成（5 档密度）；背景色 #{green[0]:02X}{green[1]:02X}{green[2]:02X}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_SRC))
