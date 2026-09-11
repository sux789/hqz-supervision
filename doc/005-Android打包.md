# 005 Android 打包（Capacitor 壳）

> 模式同 hqz-survey：**远程 URL 壳**——android 工程只是 WebView 壳，实际前端/后端由服务器提供，APK 不含业务代码，升级 Web 无需重新发版。

## 1. 工程结构

```
package.json               # Capacitor 6 依赖（@capacitor/core/android/cli + geolocation）
capacitor.config.json      # appId top.bibook.supervision，server.url 指向后端
www/index.html             # 壳加载页（WebView 随即跳转 server.url）
android/                   # cap add android 生成的原生工程（已提交，直接可构建）
```

## 2. 与 hqz-survey 壳的差异（防重复，C03/C05）

| 项 | hqz-survey | hqz-supervision |
|---|---|---|
| appId | top.bibook.survey | **top.bibook.supervision** |
| appName | 验收APP | **监督验收** |
| server.url | https://forest.bibook.top/survey | **https://forest.bibook.top/supervision**（部署后启用） |
| 前端桥接 | waitForCapacitor() 等 bridge 注入 | 同款约定（B2 拍照/B3 轨迹沿用） |

## 3. 构建步骤（需装有 Android Studio / SDK 的机器）

```bash
# 本机（Mac）当前无 Android SDK，仅生成工程；构建在 Windows/装 SDK 的机器执行
npm install
npx cap sync android        # 同步插件与 www
npx cap open android        # 打开 Android Studio → Build APK
# 或命令行：cd android && ./gradlew assembleDebug
```

产物：`android/app/build/outputs/apk/debug/app-debug.apk`

## 3.1 云端打包（GitHub Actions，推荐——本机无需 Android SDK）

`.github/workflows/android-build.yml`：推送触及 `android/**`、`www/**`、`capacitor.config.json` 等自动触发；Actions 页也可手动 Run workflow。

流程：`npm install → npx cap sync android → ./gradlew assembleDebug → 上传工件`（JDK 17 + Node 22，ubuntu-latest 自带 SDK 34）。

取 APK：仓库页 **Actions → Android APK #N → Artifacts → 监督验收-debug-apk**（保留 30 天），下载解压得 `app-debug.apk`，可直接安装。

注意：`android/.gitignore`（Capacitor 自带）忽略了 `assets/capacitor.config.json` 与 `capacitor-cordova-android-plugins/`，由 CI 的 `cap sync` 再生成——**不要绕过 cap sync 直接 gradlew**（CI 与本机构建都要先 sync）。

## 4. 本地联调（未部署时）

`capacitor.config.json` 临时改为：

```json
"server": {
  "url": "http://10.0.2.2:8720",
  "cleartext": true
}
```

- `10.0.2.2` = 模拟器访问宿主机 localhost；真机用局域网 IP
- 本地服务：`python main.py`（127.0.0.1:8720）
- **提交前务必改回生产 URL + cleartext:false**

## 5. 前端桥接约定（B2/B3 实现时遵循）

- 原生能力入口统一 `waitForCapacitor()`（见 hqz-survey app.js L98）——远程 URL 模式下 bridge 可能在页面脚本之后注入
- 定位：`@capacitor/geolocation` 已装（轨迹用）；拍照 B2 时按 hqz-survey 同款权限申请（AppPermissions）
- WebView 里 `navigator.geolocation` 亦可直接用，二选一在 B3 定稿
