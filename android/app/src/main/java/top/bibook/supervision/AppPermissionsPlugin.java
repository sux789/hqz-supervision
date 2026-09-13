package top.bibook.supervision;

import android.Manifest;
import android.app.Activity;
import android.content.ContentValues;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.PowerManager;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.provider.Settings;
import android.util.Base64;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import androidx.activity.result.ActivityResult;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MimeTypes;
import androidx.media3.effect.ScaleAndRotateTransformation;
import androidx.media3.transformer.Composition;
import androidx.media3.transformer.DefaultEncoderFactory;
import androidx.media3.transformer.EditedMediaItem;
import androidx.media3.transformer.Effects;
import androidx.media3.transformer.ExportException;
import androidx.media3.transformer.ExportResult;
import androidx.media3.transformer.Transformer;
import androidx.media3.transformer.VideoEncoderSettings;


import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.Arrays;
import java.util.Collections;
import android.media.MediaMetadataRetriever;
import java.io.OutputStream;

/**
 * 原生能力插件，供 WebView 内的页面调用：
 *   - check({type})         查询定位/相机权限状态
 *   - request({type})       申请定位/相机权限
 *   - openSettings()        打开本 App 的系统权限设置页
 *   - savePhoto({base64,name,subdir})  照片写入系统相册 Pictures/{subdir}/（多级子目录，如
 *                                      2022年度/人工造林/1号调查小班），返回真实绝对路径
 *   - saveFile({base64,name})   导出文件（xlsx/zip/mp4）写入公共下载 Download/验收导出/，返回真实绝对路径
 *   - saveVideo({base64,name,subdir,base})  视频写入相册 Pictures/{subdir}/（与照片同目录；base 可改顶层目录）
 *   - ensureMedia()         一次性申请「相机 + 麦克风」授权（录制视频前调用）
 *   - recordVideo({name,subdir,maxSeconds,quality,transcode,maxHeight,bitrateK})  原生录像+转码 → Pictures/{subdir}/
 *   - getLastVideo({consume})   取最近一次录像结果（页面被系统重载后补记文件名）
 *   - ensureBackground()     申请后台运行相关权限：通知 + 电池优化白名单 + 厂商自启动页（v0.18）
 * type: 'location' | 'camera' | 'microphone'
 */
@CapacitorPlugin(
    name = "AppPermissions",
    permissions = {
        @Permission(alias = "location", strings = {
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.ACCESS_FINE_LOCATION
        }),
        @Permission(alias = "camera", strings = {
            Manifest.permission.CAMERA
        }),
        @Permission(alias = "microphone", strings = {
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.MODIFY_AUDIO_SETTINGS
        }),
        @Permission(alias = "notifications", strings = {
            Manifest.permission.POST_NOTIFICATIONS
        })
    }
)
public class AppPermissionsPlugin extends Plugin {

    private String resolveAlias(String type) {
        if ("camera".equals(type)) return "camera";
        if ("microphone".equals(type) || "mic".equals(type) || "audio".equals(type)) return "microphone";
        return "location";
    }

    @PluginMethod
    public void check(PluginCall call) {
        String type = call.getString("type", "location");
        PermissionState state = getPermissionState(resolveAlias(type));
        if (state == null) {
            state = PermissionState.DENIED;
        }
        JSObject ret = new JSObject();
        ret.put("type", resolveAlias(type));
        ret.put("state", state.toString());
        ret.put("granted", state == PermissionState.GRANTED);
        call.resolve(ret);
    }

    @PluginMethod
    public void request(PluginCall call) {
        String type = call.getString("type", "location");
        String alias = resolveAlias(type);
        saveCall(call);
        requestPermissionForAlias(alias, call, "permCallback");
    }

    @PermissionCallback
    private void permCallback(PluginCall call) {
        String type = call.getString("type", "location");
        PermissionState state = getPermissionState(resolveAlias(type));
        if (state == null) {
            state = PermissionState.DENIED;
        }
        JSObject ret = new JSObject();
        ret.put("type", resolveAlias(type));
        ret.put("state", state.toString());
        ret.put("granted", state == PermissionState.GRANTED);
        call.resolve(ret);
    }

    /* ── 录像保活 + 后台运行权限（v0.18） ── */

    /** 录像前启动前台服务保活（国产 ROM 省电策略会杀后台进程，导致页面被重载、回调丢失）。 */
    private void startKeepAlive() {
        try {
            Intent i = new Intent(getContext(), KeepAliveService.class);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                getContext().startForegroundService(i);
            } else {
                getContext().startService(i);
            }
        } catch (Exception e) {
            android.util.Log.e("HqzSupervision", "startKeepAlive failed", e);
        }
    }

    private void stopKeepAlive() {
        try {
            getContext().stopService(new Intent(getContext(), KeepAliveService.class));
        } catch (Exception ignored) {
        }
    }

    /**
     * 自动申请「后台运行」相关能力（v0.18）：Android 没有单一的"后台运行权限"，
     * 实际由三件事组成——①通知权限（Android 13+，前台服务通知需要）②电池优化白名单
     * （系统对话框，同意后进程不再被省电策略回收）③厂商自启动/后台运行管理页（跳转引导，无法静默授权）。
     * 本方法依次处理，返回各项结果，页面据此提示用户。
     */
    @PluginMethod
    public void ensureBackground(PluginCall call) {
        if (Build.VERSION.SDK_INT >= 33
                && getPermissionState("notifications") != PermissionState.GRANTED) {
            saveCall(call);
            requestPermissionForAlias("notifications", call, "backgroundCallback");
            return;
        }
        resolveBackground(call);
    }

    @PermissionCallback
    private void backgroundCallback(PluginCall call) {
        resolveBackground(call);
    }

    private void resolveBackground(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("notifications", Build.VERSION.SDK_INT < 33
                || getPermissionState("notifications") == PermissionState.GRANTED);
        PowerManager pm = (PowerManager) getContext().getSystemService(Context.POWER_SERVICE);
        String pkg = getContext().getPackageName();
        boolean ignoring = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                && pm != null && pm.isIgnoringBatteryOptimizations(pkg);
        ret.put("batteryWhitelisted", ignoring);
        if (!ignoring) {
            ret.put("batteryAsked", requestIgnoreBatteryOptimizations(pkg));
        }
        ret.put("autostartOpened", openAutostartSettings());
        call.resolve(ret);
    }

    /** 弹系统对话框请求加入电池优化白名单（用户可拒绝；拒绝也不影响功能）。 */
    private boolean requestIgnoreBatteryOptimizations(String pkg) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return false;
        try {
            Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:" + pkg));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(i);
            return true;
        } catch (Exception e) {
            try {
                Intent i2 = new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
                i2.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                getContext().startActivity(i2);
                return true;
            } catch (Exception e2) {
                return false;
            }
        }
    }

    /** 跳厂商"自启动/后台运行管理"页（华为/小米/OPPO/vivo/魅族），都不匹配则退回本应用详情页。 */
    private boolean openAutostartSettings() {
        String[][] cands = {
                {"com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"},
                {"com.huawei.systemmanager", "com.huawei.systemmanager.optimize.process.ProtectActivity"},
                {"com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity"},
                {"com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity"},
                {"com.oppo.safe", "com.oppo.safe.permission.startup.StartupAppListActivity"},
                {"com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.BgStartUpManagerActivity"},
                {"com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity"},
                {"com.meizu.safe", "com.meizu.safe.security.SecureMainActivity"},
        };
        for (String[] c : cands) {
            try {
                Intent i = new Intent();
                i.setComponent(new ComponentName(c[0], c[1]));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                if (getContext().getPackageManager().resolveActivity(i, 0) != null) {
                    getContext().startActivity(i);
                    return true;
                }
            } catch (Exception ignored) {
            }
        }
        try {   // 兜底：本应用详情页（用户可手动设置自启动/后台运行）
            Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:" + getContext().getPackageName()));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(i);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * 标准化媒体权限申请：一次性确保「相机 + 麦克风」都拿到授权（v0.11）。
     * 返回 {camera, microphone, granted}；页面在调用 getUserMedia 录制视频前先调本方法，
     * 用户只需点一次系统弹窗，避免"录到一半才弹权限"的割裂体验。
     */
    @PluginMethod
    public void ensureMedia(PluginCall call) {
        java.util.List<String> need = new java.util.ArrayList<>();
        if (getPermissionState("camera") != PermissionState.GRANTED) need.add("camera");
        if (getPermissionState("microphone") != PermissionState.GRANTED) need.add("microphone");
        if (!need.isEmpty()) {
            requestPermissionForAliases(need.toArray(new String[0]), call, "mediaCallback");
            return;
        }
        resolveMediaStates(call);
    }

    @PermissionCallback
    private void mediaCallback(PluginCall call) {
        resolveMediaStates(call);
    }

    private void resolveMediaStates(PluginCall call) {
        boolean cam = getPermissionState("camera") == PermissionState.GRANTED;
        boolean mic = getPermissionState("microphone") == PermissionState.GRANTED;
        JSObject ret = new JSObject();
        ret.put("camera", cam);
        ret.put("microphone", mic);
        ret.put("granted", cam && mic);
        call.resolve(ret);
    }

    @PluginMethod
    public void openSettings(PluginCall call) {
        try {
            Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
            intent.setData(Uri.parse("package:" + getContext().getPackageName()));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject("无法打开系统设置", e);
        }
    }

    /**
     * 照片写入系统相册 Pictures/{subdir}/，返回真实绝对路径。
     * subdir 为多级子目录（如 "2022年度/人工造林/1号调查小班"），缺省 "验收照片"；
     * 每段自动清理文件系统非法字符并防路径穿越（. / ..）。
     * Android 10+ 走 MediaStore（自有媒体免存储权限且立即可见于相册，多级目录自动创建）；
     * 旧版本回退应用外部私有目录。
     */
    @PluginMethod
    public void savePhoto(PluginCall call) {
        String base64 = call.getString("base64");
        String name = call.getString("name", "photo.jpg");
        if (base64 == null || base64.isEmpty()) {
            call.reject("缺少照片数据");
            return;
        }
        String subdir = sanitizeSubdir(call.getString("subdir", "验收照片"));
        String mime = name.toLowerCase().endsWith(".png") ? "image/png" : "image/jpeg";
        try {
            byte[] bytes = Base64.decode(base64, Base64.DEFAULT);
            ContentValues values = new ContentValues();
            values.put(MediaStore.Images.Media.DISPLAY_NAME, name);
            values.put(MediaStore.Images.Media.MIME_TYPE, mime);
            Uri uri;
            String realPath;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                values.put(MediaStore.Images.Media.RELATIVE_PATH,
                        Environment.DIRECTORY_PICTURES + "/" + subdir);
                uri = getContext().getContentResolver().insert(
                        MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values);
                if (uri == null) {
                    call.reject("创建相册记录失败");
                    return;
                }
                try (OutputStream os = getContext().getContentResolver().openOutputStream(uri)) {
                    os.write(bytes);
                    os.flush();
                }
                realPath = queryFileRealPath(uri, new File(new File(
                        Environment.getExternalStorageDirectory(),
                        Environment.DIRECTORY_PICTURES), subdir));
            } else {
                File dir = new File(getContext().getExternalFilesDir(Environment.DIRECTORY_PICTURES), subdir);
                if (!dir.exists()) dir.mkdirs();
                File f = new File(dir, name);
                try (FileOutputStream fos = new FileOutputStream(f)) {
                    fos.write(bytes);
                    fos.flush();
                }
                uri = Uri.fromFile(f);
                realPath = f.getAbsolutePath();
            }
            JSObject ret = new JSObject();
            ret.put("path", realPath);
            ret.put("uri", uri.toString());
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("保存照片失败: " + e.getMessage(), e);
        }
    }

    /** 清理子目录串：去每段非法字符、空段与路径穿越（. / ..），异常回退「验收照片」。 */
    private String sanitizeSubdir(String raw) {
        if (raw == null || raw.trim().isEmpty()) return "验收照片";
        String[] segs = raw.split("/");
        StringBuilder sb = new StringBuilder();
        for (String s : segs) {
            String t = s.trim().replaceAll("[\\\\/:*?\"<>|\\s]+", "_");
            if (t.isEmpty() || t.equals(".") || t.equals("..")) continue;
            if (t.startsWith("_") && t.length() > 1) t = t.substring(1);
            if (sb.length() > 0) sb.append('/');
            sb.append(t);
        }
        return sb.length() > 0 ? sb.toString() : "验收照片";
    }

    /**
     * 导出文件（xlsx/zip）写入公共下载目录 Download/验收导出/，返回真实绝对路径。
     * Android 10+ 走 MediaStore Downloads（自有文件免存储权限，文件管理器立即可见）；
     * 旧版本回退应用外部私有目录。
     */
    /**
     * 取最近一次录像结果（v0.17）：供页面在"被系统重载后"补记文件名。
     * 相机是独立 Activity，系统可能把 WebView 重载 → JS 回调丢失，但视频已落盘；
     * 页面启动时调用本方法即可把结果补回拍摄记录。consume=1 时取完即清。
     */
    @PluginMethod
    public void getLastVideo(PluginCall call) {
        SharedPreferences p = prefs();
        JSObject ret = new JSObject();
        ret.put("pending", p.getBoolean("pending", false));
        String raw = p.getString("last_video", "");
        if (raw != null && !raw.isEmpty()) {
            try {
                ret.put("video", new JSObject(raw));
            } catch (Exception ignored) {
            }
        }
        if (call.getBoolean("consume", true)) {
            prefs().edit().putString("last_video", "").putBoolean("pending", false).apply();
        }
        call.resolve(ret);
    }

    /**
     * 原生录像（v0.13.2，需重打包 APK 生效）：调系统相机录像 → **流式复制**到
     * Pictures/{subdir}/{name} → 返回真实路径。
     * 相比 WebView 的 <input capture> 文件回传：不经过页面/WebView 重载（系统相机回来后
     * 原方案会丢文件结果甚至要求重新登录），且容器是相机原生 **MP4/H.264**（任何相册可播）。
     * 参数：{name, subdir, maxSeconds, quality(0/1)}
     */
    @PluginMethod
    public void recordVideo(PluginCall call) {
        Intent intent = new Intent(MediaStore.ACTION_VIDEO_CAPTURE);
        if (intent.resolveActivity(getContext().getPackageManager()) == null) {
            call.reject("本机没有可用的相机应用");
            return;
        }
        intent.putExtra(MediaStore.EXTRA_VIDEO_QUALITY, call.getInt("quality", 1));   // 1=最高质量录制（源码率足，转码后更清晰）
        int maxSeconds = call.getInt("maxSeconds", 60);
        if (maxSeconds > 0) {
            intent.putExtra("android.intent.extra.durationLimit", maxSeconds);
        }
        startKeepAlive();      // 录像期间保活：避免本进程被省电策略杀掉（页面被重载 → 回调丢失）
        startActivityForResult(call, intent, "videoCaptureResult");
    }

    @ActivityCallback
    private void videoCaptureResult(PluginCall call, ActivityResult result) {
        if (call == null) return;
        if (result.getResultCode() != Activity.RESULT_OK || result.getData() == null
                || result.getData().getData() == null) {
            call.reject("已取消录制");
            return;
        }
        if (result.getResultCode() != Activity.RESULT_OK) {
            stopKeepAlive();   // 用户取消 → 立即停止保活
        }
        Uri src = result.getData().getData();
        String subdir = sanitizeSubdir(call.getString("subdir", "验收照片"));
        String name = call.getString("name", "video.mp4");
        long origSize = 0;
        try (Cursor c = getContext().getContentResolver().query(src, null, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int idx = c.getColumnIndex(android.provider.OpenableColumns.SIZE);
                if (idx >= 0) origSize = c.getLong(idx);
            }
        } catch (Exception ignored) {
        }
        markPending(name, subdir);
        boolean transcode = call.getInt("transcode", 1) == 1;
        int maxHeight = call.getInt("maxHeight", 720);
        int bitrateK = call.getInt("bitrateK", 2500);
        if (transcode) {
            transcodeAndSave(call, src, subdir, name, maxHeight, bitrateK, origSize);
        } else {
            saveIntoAlbum(call, src, subdir, name, null, false, origSize);
        }
    }

    /**
     * 原生转码压缩（v0.14，Media3 Transformer）：缩放到 maxHeight 以内 + H.264 + 目标码率，
     * 保留声音，输出 MP4；失败自动回退「直接保存原片」，绝不丢视频。
     */
    private void transcodeAndSave(PluginCall call, Uri src, String subdir, String name,
                                  int maxHeight, int bitrateK, long origSize) {
        final File tmp = new File(getContext().getCacheDir(), "sup_tc_" + System.currentTimeMillis() + ".mp4");
        int srcH = 0;
        try (MediaMetadataRetriever mmr = new MediaMetadataRetriever()) {
            mmr.setDataSource(getContext(), src);
            String h = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT);
            if (h != null) srcH = Integer.parseInt(h);
        } catch (Exception ignored) {
        }
        float scale = (maxHeight > 0 && srcH > maxHeight) ? (float) maxHeight / srcH : 1f;
        try {
            MediaItem item = MediaItem.fromUri(src);
            EditedMediaItem.Builder itemBuilder = new EditedMediaItem.Builder(item);
            if (scale < 1f) {
                // 注意参数顺序：Effects(audioProcessors, videoEffects)（已按 media3 1.4.1 类签名核对）
                itemBuilder.setEffects(new Effects(
                        Collections.emptyList(),
                        Arrays.asList(new ScaleAndRotateTransformation.Builder()
                                .setScale(scale, scale).build())));
            }
            Transformer transformer = new Transformer.Builder(getContext())
                    .setVideoMimeType(MimeTypes.VIDEO_H264)
                    .setAudioMimeType(MimeTypes.AUDIO_AAC)
                    .setEncoderFactory(new DefaultEncoderFactory.Builder(getContext())
                            .setRequestedVideoEncoderSettings(new VideoEncoderSettings.Builder()
                                    .setBitrate(Math.max(200, bitrateK) * 1000)
                                    .build())
                            .build())
                    .addListener(new Transformer.Listener() {
                        @Override
                        public void onCompleted(Composition composition, ExportResult result) {
                            saveIntoAlbum(call, Uri.fromFile(tmp), subdir, name, tmp, true, origSize);
                        }

                        @Override
                        public void onError(Composition composition, ExportResult result,
                                            ExportException exception) {
                            notifyListeners("videoStage", new JSObject().put("stage", "transcode_failed"));
                            saveIntoAlbum(call, src, subdir, name, tmp, false, origSize);   // 回退：保存原片
                        }
                    })
                    .build();
            notifyListeners("videoStage", new JSObject().put("stage", "transcoding"));
            getActivity().runOnUiThread(() -> transformer.start(itemBuilder.build(), tmp.getAbsolutePath()));
        } catch (Exception e) {
            saveIntoAlbum(call, src, subdir, name, tmp, false, origSize);
        }
    }

    /** 把（原片或转码产物）流式复制进 Pictures/{subdir}/{name}，写入 last_video 供页面补记。 */
    private void saveIntoAlbum(PluginCall call, Uri src, String subdir, String name, File tmpToDelete,
                               boolean transcoded, long origSize) {
        try {
            Uri outUri;
            String realPath;
            long size = 0;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentValues values = new ContentValues();
                values.put(MediaStore.Video.Media.DISPLAY_NAME, name);
                values.put(MediaStore.Video.Media.MIME_TYPE, "video/mp4");
                values.put(MediaStore.Video.Media.RELATIVE_PATH,
                        Environment.DIRECTORY_PICTURES + "/" + subdir);
                outUri = getContext().getContentResolver().insert(
                        MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values);
                if (outUri == null) {
                    call.reject("创建相册记录失败");
                    return;
                }
                size = copyUri(src, outUri);
                realPath = queryFileRealPath(outUri, new File(new File(
                        Environment.getExternalStorageDirectory(),
                        Environment.DIRECTORY_PICTURES), subdir));
            } else {
                File dir = new File(getContext().getExternalFilesDir(Environment.DIRECTORY_PICTURES), subdir);
                if (!dir.exists()) dir.mkdirs();
                File f = new File(dir, name);
                outUri = Uri.fromFile(f);
                size = copyUri(src, outUri);
                realPath = f.getAbsolutePath();
            }
            JSObject ret = new JSObject();
            ret.put("path", realPath);
            ret.put("uri", outUri.toString());
            ret.put("name", name);
            ret.put("subdir", subdir);
            ret.put("size", size);
            ret.put("origSize", origSize);
            ret.put("transcoded", transcoded);
            markDone(ret);
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("保存录像失败: " + e.getMessage(), e);
        } finally {
            stopKeepAlive();       // 转码+写盘完成（或失败）后停止保活
            if (tmpToDelete != null && tmpToDelete.exists()) {
                //noinspection ResultOfMethodCallIgnored
                tmpToDelete.delete();
            }
        }
    }

    /** 流式复制（大视频不整块读内存），返回复制的字节数，并顺带触发媒体扫描。 */
    private long copyUri(Uri src, Uri dst) throws Exception {
        long total = 0;
        try (InputStream in = getContext().getContentResolver().openInputStream(src);
             OutputStream out = getContext().getContentResolver().openOutputStream(dst)) {
            if (in == null || out == null) throw new Exception("无法打开视频流");
            byte[] buf = new byte[256 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) { out.write(buf, 0, n); total += n; }
            out.flush();
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            getContext().sendBroadcast(new Intent(Intent.ACTION_MEDIA_SCANNER_SCAN_FILE, dst));
        }
        return total;
    }

    private SharedPreferences prefs() {
        return getContext().getSharedPreferences("sup_video", android.content.Context.MODE_PRIVATE);
    }

    /** 录像前登记"进行中"（页面若被系统重载，可由 getLastVideo 补记）。 */
    private void markPending(String name, String subdir) {
        prefs().edit().putBoolean("pending", true)
                .putString("p_name", name).putString("p_subdir", subdir)
                .putLong("p_ts", System.currentTimeMillis()).apply();
    }

    private void markDone(JSObject ret) {
        prefs().edit().putBoolean("pending", false)
                .putString("last_video", ret.toString()).apply();
    }

    /**
     * 视频写入系统相册（v0.11，需重打包 APK 生效）。
     * 默认与照片**完全同目录**：Pictures/{subdir}/（即相册中照片、视频同处一个文件夹），
     * 可用 base 参数改为 Movies 等其它顶层目录；失败由页面回退 saveFile/浏览器下载。
     */
    @PluginMethod
    public void saveVideo(PluginCall call) {
        String base64 = call.getString("base64");
        String name = call.getString("name", "video.mp4");
        if (base64 == null || base64.isEmpty()) {
            call.reject("缺少视频数据");
            return;
        }
        String subdir = sanitizeSubdir(call.getString("subdir", "验收照片"));
        // 与照片保持一致的顶层目录（默认 Pictures，可传 base=Movies 等）
        String base = call.getString("base", Environment.DIRECTORY_PICTURES);
        if (base == null || base.trim().isEmpty()) base = Environment.DIRECTORY_PICTURES;
        base = base.trim();
        String lower = name.toLowerCase();
        String mime = lower.endsWith(".webm") ? "video/webm"
                : (lower.endsWith(".mov") ? "video/quicktime" : "video/mp4");
        try {
            byte[] bytes = Base64.decode(base64, Base64.DEFAULT);
            ContentValues values = new ContentValues();
            values.put(MediaStore.Video.Media.DISPLAY_NAME, name);
            values.put(MediaStore.Video.Media.MIME_TYPE, mime);
            Uri uri;
            String realPath;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                values.put(MediaStore.Video.Media.RELATIVE_PATH, base + "/" + subdir);
                uri = getContext().getContentResolver().insert(
                        MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values);
                if (uri == null) {
                    call.reject("创建视频相册记录失败");
                    return;
                }
                try (OutputStream os = getContext().getContentResolver().openOutputStream(uri)) {
                    os.write(bytes);
                    os.flush();
                }
                realPath = queryFileRealPath(uri, new File(new File(
                        Environment.getExternalStorageDirectory(), base), subdir));
            } else {
                File dir = new File(getContext().getExternalFilesDir(base), subdir);
                if (!dir.exists()) dir.mkdirs();
                File f = new File(dir, name);
                try (FileOutputStream fos = new FileOutputStream(f)) {
                    fos.write(bytes);
                    fos.flush();
                }
                uri = Uri.fromFile(f);
                realPath = f.getAbsolutePath();
            }
            JSObject ret = new JSObject();
            ret.put("path", realPath);
            ret.put("uri", uri.toString());
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("保存视频失败: " + e.getMessage(), e);
        }
    }

    @PluginMethod
    public void saveFile(PluginCall call) {
        String base64 = call.getString("base64");
        String name = call.getString("name", "export.bin");
        if (base64 == null || base64.isEmpty()) {
            call.reject("缺少文件数据");
            return;
        }
        String lower = name.toLowerCase();
        String mime;
        if (lower.endsWith(".xlsx")) {
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
        } else if (lower.endsWith(".zip")) {
            mime = "application/zip";
        } else if (lower.endsWith(".mp4")) {
            mime = "video/mp4";        // v0.11：视频本地保存
        } else if (lower.endsWith(".webm")) {
            mime = "video/webm";
        } else if (lower.endsWith(".mov")) {
            mime = "video/quicktime";
        } else {
            mime = "application/octet-stream";
        }
        try {
            byte[] bytes = Base64.decode(base64, Base64.DEFAULT);
            Uri uri;
            String realPath;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentValues values = new ContentValues();
                values.put(MediaStore.Downloads.DISPLAY_NAME, name);
                values.put(MediaStore.Downloads.MIME_TYPE, mime);
                values.put(MediaStore.Downloads.RELATIVE_PATH,
                        Environment.DIRECTORY_DOWNLOADS + "/验收导出");
                uri = getContext().getContentResolver().insert(
                        MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values);
                if (uri == null) {
                    call.reject("创建下载记录失败");
                    return;
                }
                try (OutputStream os = getContext().getContentResolver().openOutputStream(uri)) {
                    os.write(bytes);
                    os.flush();
                }
                realPath = queryFileRealPath(uri, new File(new File(
                        Environment.getExternalStorageDirectory(),
                        Environment.DIRECTORY_DOWNLOADS), "验收导出"));
            } else {
                File dir = new File(getContext().getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "验收导出");
                if (!dir.exists()) dir.mkdirs();
                File f = new File(dir, name);
                try (FileOutputStream fos = new FileOutputStream(f)) {
                    fos.write(bytes);
                    fos.flush();
                }
                uri = Uri.fromFile(f);
                realPath = f.getAbsolutePath();
            }
            JSObject ret = new JSObject();
            ret.put("path", realPath);
            ret.put("uri", uri.toString());
            call.resolve(ret);
        } catch (Exception e) {
            call.reject("保存文件失败: " + e.getMessage(), e);
        }
    }

    /** 查询 MediaStore 记录的真实文件路径（DATA 列），失败时返回 fallbackDir。 */
    private String queryFileRealPath(Uri uri, File fallbackDir) {
        try (Cursor c = getContext().getContentResolver().query(
                uri, new String[]{MediaStore.MediaColumns.DATA}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                int idx = c.getColumnIndex(MediaStore.MediaColumns.DATA);
                if (idx >= 0) {
                    String p = c.getString(idx);
                    if (p != null && !p.isEmpty()) return p;
                }
            }
        } catch (Exception ignored) {
        }
        return fallbackDir.getAbsolutePath();
    }
}
