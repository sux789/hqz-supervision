/* 照片 EXIF 读取与回写（v0.30）
 *
 * 依赖 piexifjs（MIT，static/vendor/piexif.js）—— 通过参数注入，浏览器 / node 通用，可直接自动化测试。
 *
 * 背景（doc/017）：App 压缩是 Canvas 像素重绘（drawImage → 画水印 → toBlob('image/jpeg')），
 * 该管线**必然丢掉全部 EXIF**（经纬度/拍摄时间/机型…），不是 bug，也没有开关。
 * 这里做两件事：
 *   ① 水印的坐标/时间**优先取原片 EXIF** —— 相机定位通常比浏览器 geolocation 权威，
 *      且不依赖定位权限与超时；EXIF 缺失时由调用方退回原行为（getCoords / 当前时间）
 *   ② 把原片 EXIF **回写进压缩图** —— 云端那份因此是机器可读的
 *
 * 回写时必须清理的三类标签（照抄会出错的坑）：
 *   · Orientation 强制 1  —— 像素已被 createImageBitmap(from-image) 摆正，
 *                            保留原方向标签会让支持 EXIF 的查看器**再次旋转**
 *   · 删 ImageWidth/ImageLength/PixelX/PixelYDimension —— 像素已缩放，旧尺寸是脏数据
 *   · 删 MakerNote —— 厂商私有块内部偏移与原文件绑定，整段拷贝会解析出乱码
 */
(function (global) {
  'use strict';

  /** "YYYY:MM:DD HH:MM:SS"（EXIF 标准写法，日期段是冒号）→ Date | null */
  function exifTime(s) {
    const m = /^(\d{4}):(\d{2}):(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(String(s || ''));
    if (!m) return null;
    const d = new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
    return isNaN(d.getTime()) ? null : d;
  }

  /** Date → "YYYY-MM-DD HH:MM:SS"（与水印现行格式一致） */
  function toStamp(d) {
    const p = (n) => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
      + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  /** 从**原片**读定位与拍摄时间。→ {lat, lng, takenAt:Date} | null
   *  lat/lng 为十进制度（南/西半球为负）；两者必须同时有效才给出；
   *  takenAt 取 DateTimeOriginal，退而取 DateTime。 */
  function exifOfDataUrl(url, P) {
    try {
      const segs = P.load(url);
      const gps = segs.GPS || {}, ex = segs.Exif || {};
      let lat = P.GPSHelper.dmsRationalToDeg(gps[P.GPSIFD.GPSLatitude],
                                            gps[P.GPSIFD.GPSLatitudeRef] || 'N');
      let lng = P.GPSHelper.dmsRationalToDeg(gps[P.GPSIFD.GPSLongitude],
                                            gps[P.GPSIFD.GPSLongitudeRef] || 'E');
      if (typeof lat !== 'number' || isNaN(lat)) lat = null;
      if (typeof lng !== 'number' || isNaN(lng)) lng = null;
      if (lat === null || lng === null) { lat = null; lng = null; }
      const takenAt = exifTime(ex[P.ExifIFD.DateTimeOriginal] || ex[P.ImageIFD.DateTime] || '');
      if (lat === null && !takenAt) return null;
      return { lat: lat, lng: lng, takenAt: takenAt };
    } catch (e) {
      return null;                       // 原片 EXIF 损坏 / 不是 JPEG → 视同无 EXIF
    }
  }

  /** 把原片 EXIF 回写进压缩图的 data URL。→ 新 dataUrl（失败/无 EXIF 时原样返回 outUrl） */
  function preserveExifDataUrl(origUrl, outUrl, P) {
    const segs = P.load(origUrl);
    const exifObj = {};
    for (const k of ['0th', 'Exif', 'GPS', 'Interop']) {
      if (segs[k] && Object.keys(segs[k]).length) exifObj[k] = segs[k];
    }
    if (!exifObj['0th'] && !exifObj.Exif && !exifObj.GPS) return outUrl;
    const t = exifObj['0th'] || (exifObj['0th'] = {});
    t[P.ImageIFD.Orientation] = 1;                 // 像素已摆正，方向必须归一
    delete t[P.ImageIFD.ImageWidth];               // 像素已缩放
    delete t[P.ImageIFD.ImageLength];
    if (exifObj.Exif) {
      delete exifObj.Exif[P.ExifIFD.PixelXDimension];
      delete exifObj.Exif[P.ExifIFD.PixelYDimension];
      delete exifObj.Exif[P.ExifIFD.MakerNote];    // 厂商私有块：偏移绑定原文件
    }
    return P.insert(P.dump(exifObj), outUrl);
  }

  const api = { exifTime: exifTime, toStamp: toStamp, exifOfDataUrl: exifOfDataUrl,
                preserveExifDataUrl: preserveExifDataUrl };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.PhotoExif = api;

})(typeof window !== 'undefined' ? window : globalThis);
