#!/usr/bin/env node
/* 照片 EXIF 读取/回写 的自动化测试（v0.30，node 直跑；run_all.sh 会调用）
 *
 * 覆盖 static/photo_exif.js 的纯逻辑层：
 *   · dms 度分秒 ↔ 十进制（含半球取负）
 *   · EXIF 时间解析 / 水印格式化
 *   · exifOfDataUrl：从原片读 GPS + DateTimeOriginal
 *   · preserveExifDataUrl：回写 EXIF，并验证三类"照抄会坏"的标签已正确处理
 *     （Orientation 归一 / 尺寸标签删除 / MakerNote 删除）
 *   · 无 EXIF 原片 → 原样返回；坏 EXIF → 不抛出
 *
 * 夹具：tests/fixtures/photo_16x16.jpg（16×16 纯色 JPEG，EXIF 由本测试用 piexif 注入，
 *       因此不依赖任何"自带元数据的照片"）。
 */
const fs = require('fs');
const path = require('path');
const piexif = require('../static/vendor/piexif.js');
const PX = require('../static/photo_exif.js');

const FIXTURE = path.join(__dirname, 'fixtures', 'photo_16x16.jpg');
const plainUrl = 'data:image/jpeg;base64,' + fs.readFileSync(FIXTURE).toString('base64');

let pass = 0, fail = 0;
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  \u2705 ' + name + (detail ? '  ' + detail : '')); }
  else { fail++; console.log('  \u274c ' + name + (detail ? '  ' + detail : '')); }
}
const approx = (a, b, eps) => Math.abs(a - b) <= (eps || 1e-6);

// ---- 造一个带 EXIF 的"原片"：GPS 24°7'24.42"N 101°40'44.10"E（≈24.12345, 101.67892）、
//      拍摄时间 2026:10:09 15:00:00、方向=6（竖拍旋转）、含 MakerNote 与尺寸标签（应被清理）
const EXIF = {
  '0th': { 271: 'TestCam', 274: 6, 256: 4000, 257: 3000 },              // Make / Orientation / ImageWidth / ImageLength
  Exif: { 36867: '2026:10:09 15:00:00', 40962: 4000, 40963: 3000, 37500: 'MakerNote-Bytes' },
  GPS: { 1: 'N', 2: [[24, 1], [7, 1], [2442, 100]],
         3: 'E', 4: [[101, 1], [40, 1], [4410, 100]] },
};
const origUrl = piexif.insert(piexif.dump(EXIF), plainUrl);

console.log('\n=== A. 度分秒 ↔ 十进制 ===');
const dms = piexif.GPSHelper.degToDmsRational(24.12345);
ok('A1 degToDmsRational → dmsRationalToDeg 往返一致',
   approx(piexif.GPSHelper.dmsRationalToDeg(dms, 'N'), 24.12345, 1e-6),
   JSON.stringify(dms));
ok('A2 南半球取负', approx(piexif.GPSHelper.dmsRationalToDeg(dms, 'S'), -24.12345, 1e-6));

console.log('\n=== B. EXIF 时间 ===');
ok('B1 解析 EXIF 标准格式（日期段冒号）',
   PX.exifTime('2026:10:09 15:00:00') instanceof Date
   && PX.exifTime('2026:10:09 15:00:00').getFullYear() === 2026
   && PX.exifTime('2026:10:09 15:00:00').getMinutes() === 0);
ok('B2 水印格式化输出 YYYY-MM-DD HH:MM:SS',
   PX.toStamp(new Date(2026, 9, 9, 15, 0, 0)) === '2026-10-09 15:00:00',
   PX.toStamp(new Date(2026, 9, 9, 15, 0, 0)));
ok('B3 垃圾输入 → null', PX.exifTime('不是时间') === null && PX.exifTime('') === null
   && PX.exifTime(null) === null);

console.log('\n=== C. 从原片读 EXIF ===');
const meta = PX.exifOfDataUrl(origUrl, piexif);
ok('C1 读出纬度 24.12345', !!meta && approx(meta.lat, 24.12345, 1e-5),
   meta && String(meta.lat));
ok('C2 读出经度 101.67892', !!meta && approx(meta.lng, 101.6789166, 1e-4),
   meta && String(meta.lng));
ok('C3 读出拍摄时间 2026-10-09 15:00:00',
   !!meta && !!meta.takenAt && PX.toStamp(meta.takenAt) === '2026-10-09 15:00:00',
   meta && meta.takenAt && PX.toStamp(meta.takenAt));
ok('C4 纯色 JPEG（无 EXIF）→ null', PX.exifOfDataUrl(plainUrl, piexif) === null);
ok('C5 坏数据 → null（不抛出）', PX.exifOfDataUrl('data:image/jpeg;base64,QUJD', piexif) === null);

console.log('\n=== D. 回写 EXIF 到压缩图 ===');
const outUrl = PX.preserveExifDataUrl(origUrl, plainUrl, piexif);
ok('D1 回写后确实带上了 EXIF', outUrl !== plainUrl);
const back = PX.exifOfDataUrl(outUrl, piexif);
ok('D2 回写后的 GPS 与原片一致',
   !!back && approx(back.lat, 24.12345, 1e-5) && approx(back.lng, 101.6789166, 1e-4));
ok('D3 回写后的拍摄时间与原片一致',
   !!back && !!back.takenAt && PX.toStamp(back.takenAt) === '2026-10-09 15:00:00');
const segs = piexif.load(outUrl);
ok('D4 方向标签已归一为 1（像素已被 canvas 摆正，保留 6 会被再次旋转）',
   segs['0th'][piexif.ImageIFD.Orientation] === 1,
   'Orientation=' + segs['0th'][piexif.ImageIFD.Orientation]);
ok('D5 尺寸标签已删除（像素已缩放，旧尺寸是脏数据）',
   segs['0th'][piexif.ImageIFD.ImageWidth] === undefined
   && segs['0th'][piexif.ImageIFD.ImageLength] === undefined
   && segs.Exif[piexif.ExifIFD.PixelXDimension] === undefined
   && segs.Exif[piexif.ExifIFD.PixelYDimension] === undefined);
ok('D6 MakerNote 已删除（厂商私有块偏移绑定原文件，整段拷贝会坏）',
   segs.Exif[piexif.ExifIFD.MakerNote] === undefined);
ok('D7 相机厂商等普通标签正常保留', segs['0th'][piexif.ImageIFD.Make] === 'TestCam');
ok('D8 结果仍是合法 JPEG 且能被 piexif 再解析', Array.isArray(segs['0th'] ? Object.keys(segs['0th']) : []) );

console.log('\n=== E. 边界 ===');
ok('E1 原片无 EXIF → 压缩图原样返回（不加空 EXIF）',
   PX.preserveExifDataUrl(plainUrl, 'data:image/jpeg;base64,WFhY', piexif)
     === 'data:image/jpeg;base64,WFhY');
ok('E2 原片只有 GPS 没有时间 → 仍回写 GPS',
   (() => {
     const o = piexif.insert(piexif.dump({ GPS: EXIF.GPS }), plainUrl);
     const r = PX.exifOfDataUrl(PX.preserveExifDataUrl(o, plainUrl, piexif), piexif);
     return !!r && approx(r.lat, 24.12345, 1e-5) && !r.takenAt;
   })());

console.log('\n' + '='.repeat(62));
console.log('通过 ' + pass + ' / 失败 ' + fail);
process.exit(fail ? 1 : 0);
