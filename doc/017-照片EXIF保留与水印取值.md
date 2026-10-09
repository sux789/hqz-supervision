# 017-照片EXIF保留与水印取值（v0.30）

> 版本：v0.30 ｜ 日期：2026-10-09 ｜ 状态：已实现并本地实测通过
> 关联约束：C20（新增）、C02、C08

## 一、需求

用户：**"3 真exif 保留，水印的经纬度和时间 优先从相片exif 里面提取"**

即两件事：
1. 压缩后的照片要**保留真实 EXIF**（此前是全丢的，见 doc/015 同期答复：Canvas 像素重绘必然丢）
2. 水印里的**坐标/时间优先从原片 EXIF 提取**（相机定位通常比浏览器 geolocation 权威，
   且不依赖定位权限与 2.5 秒超时）

## 二、为什么此前会丢、为什么选这个方案

| 方案 | 说明 | 结论 |
|---|---|---|
| 原生 `ExifInterface` 拷贝 | 需要 JS 把原片+压缩图都交给原生插件 → **要重新出包 APK**（本机无 SDK） | 暂不采用 |
| 服务器端注入 | App 还需额外上传原片 EXIF 字段；服务端用 Pillow 写 EXIF，CPU 与复杂度都上去了 | 不采用 |
| **JS 内直接读写 EXIF（piexifjs，MIT）** | 纯 JS、可离线打包进 `static/vendor/`、**改网页端即生效（无需出包）** | ✅ 采用 |

压缩是 Canvas 像素重绘（`drawImage` → 画水印 → `toBlob('image/jpeg')`），`toBlob` 产物只含像素，
这是管线的确定性行为。所以在**压缩完成之后、上传之前**，用 piexifjs 把原片 EXIF 回写进压缩图。

## 三、实现

```
static/vendor/piexif.js   ← piexifjs 1.0.6（MIT，hMatoba），原样引入，未改动
static/photo_exif.js      ← 纯逻辑层（浏览器/node 通用，可自动化测试）
static/app.js             ← Blob/FileReader 薄封装 + 拍照流程挂钩
templates/index.html      ← 引入两个新脚本（在 app.js 之前）
```

### 3.1 纯逻辑层 `photo_exif.js`

| 函数 | 作用 |
|---|---|
| `exifTime(s)` | `"YYYY:MM:DD HH:MM:SS"`（EXIF 日期段是**冒号**）→ Date；垃圾输入 → null |
| `toStamp(d)` | Date → `"YYYY-MM-DD HH:MM:SS"`（与水印现行格式一致） |
| `exifOfDataUrl(url, P)` | 读原片：GPS（度分秒→十进制，南/西为负）+ `DateTimeOriginal`（退而取 `DateTime`）。lat/lng **必须同时有效**才给出；原片损坏/无 EXIF → null（不抛） |
| `preserveExifDataUrl(origUrl, outUrl, P)` | 把原片 EXIF 结构回写进压缩图；无 EXIF 时原样返回 |

依赖通过参数注入（`P` = piexif），因此 **node 可直接 require 做自动化测试**。

### 3.2 回写时的三类标签清理（照抄会出错的坑，doc/017 §五单列）

| 标签 | 为什么要处理 | 处理 |
|---|---|---|
| `Orientation` | 像素已被 `createImageBitmap(from-image)` **摆正**；保留原方向标签（如 6）会让支持 EXIF 的查看器**再转一次** | 强制 `= 1` |
| `ImageWidth/Length`、`PixelX/YDimension` | 像素已缩放（最长边 1440 等），旧尺寸是脏数据 | 删除 |
| `MakerNote` | 厂商私有块内部偏移与**原文件**绑定，整段拷贝会解析出乱码 | 删除 |

（相机厂商 Make/Model 等普通标签正常保留；缩略图不拷贝。）

### 3.3 拍照流程挂钩

```
exifOf(file)                              ← ① 读原片 EXIF
coords = EXIF 有坐标 ? "lat,lng" : getCoords()
takenAt = EXIF 有拍摄时间 ? 该时间 : new Date()
blob = drawWatermark(file, remark, coords, {maxSide, quality, takenAt})   ← 水印用 EXIF 时间
blob = preserveExif(file, blob)           ← ② 回写 EXIF（此后存相册/下载/云同步全是这份）
```

`drawWatermark` 新增 `opts.takenAt`（Date）：给了就用它格式化首行时间，否则 `new Date()`（原行为）。

## 四、验证

### 4.1 自动化（`bash tests/run_all.sh`，第 4/4 步，node 直跑）

`tests/test_exif.js` **20 项**：度分秒往返/半球取负、EXIF 时间解析与拒绝、
从原片读 GPS 与时间、回写后 GPS/时间一致、**方向归一/尺寸删除/MakerNote 删除**、
普通标签保留、结果可被再次解析、无 EXIF 原样返回、仅 GPS 无时间仍回写。

另：`run_all.sh` 顺手做成**自动探测 python 解释器**（缺 openpyxl 时退到受管 venv），
以后 `bash tests/run_all.sh` 不必再手动 `PY=…`。

### 4.2 浏览器端到端（真实函数、真实管线）

造一张 200×300 照片，注入 EXIF：GPS `24°7'24.42"N 101°40'44.10"E`、拍摄时间 `2026:10:09 15:00:00`、
**方向=6（竖拍旋转）** → 跑真实流程：

| 断言 | 结果 |
|---|---|
| `exifOf` 读出坐标 24.12345, 101.67892 | ✅ |
| `exifOf` 读出拍摄时间 2026-10-09 15:00:00 | ✅ |
| 水印使用的坐标 = EXIF 坐标（而非设备定位） | ✅ |
| 压缩图 `方向标签 = 1`（**不是 6**，不会被二次旋转） | ✅ |
| 压缩图 EXIF 里坐标/时间与原片一致 | ✅ |
| 控制台无报错 | ✅ |

全量：单元 77 + 端到端 85 + EXIF 20 = **182 项全绿**。

## 五、效果与边界

- **新拍照片**（系统相机或相册选取的 JPEG）：压缩图与云端副本都带真实 EXIF
  （GPS/拍摄时间/机型等），水印坐标与时间**来自 EXIF**
- EXIF 缺失（部分机型/截图/非 JPEG）→ 行为与从前完全一致（设备定位 + 当前时间），不会失败
- **HEIC**（iPhone 默认格式）不会被处理（只认 JPEG）——本项目 App 在 Android 上，相机产出 JPEG
- **已在云端的旧照片**仍是旧格式（无 EXIF），不会回填
- 体积代价：EXIF 段约 1–2 KB，可忽略

## 六、部署

已部署上线；`?v=` → **0.30**。**改的是网页端，App 壳加载的就是网页，无需重新出包**；
用户重新进入页面（或杀掉 App 重开）即会加载新版。
