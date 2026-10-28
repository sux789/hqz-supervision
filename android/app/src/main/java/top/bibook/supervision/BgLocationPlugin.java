package top.bibook.supervision;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.Looper;

import androidx.core.content.ContextCompat;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 自研后台定位插件（平台 LocationManager，无 Google Play Services 依赖）。
 * 移植自 hqz-survey（2026-09-13，用户要求：轨迹用底层记录、不记录网络定位、息屏继续）。
 *
 * 背景：生产设备为无 GMS 的华为/鸿蒙，社区插件
 * @capacitor-community/background-geolocation 依赖 gms FusedLocationProviderClient，
 * 在该类设备上静默无回调（前台服务在跑、通知在，但定位点一个不来）。
 *
 * 定位策略：**只注册 GPS_PROVIDER（纯 GNSS）**——GPS 源返回 WGS-84 坐标（provider="gps"），
 * 无需纠偏；网络源（HMS/基站/WiFi，GCJ-02 且纠偏后绝对精度仍 50~1000m）一概不采，
 * 宁可 GPS 无 fix 期间轨迹空白，不记偏点（2026-08-25 用户决策，v0.32 全链路闭环：
 * 网页侧 watchPosition 回退已删，本插件是唯一定位来源）。
 *
 * v0.46「卫星跟踪常开，轨迹记录手动」（v0.33 是记录开关同时启停卫星采集，2026-10-28 用户修订）：
 *   · 卫星跟踪 = **权限决定**：授权即注册 GPS_PROVIDER 并常驻前台服务，保持星历/钟差热态，
 *     后续打卡/水印/拍照的一次性定位更快更准；不再随记录开关启停。
 *   · 记录开关 = SharedPreferences track_on（默认关）——**只决定卫星点是否落缓冲/推网页**，
 *     网页按钮只是遥控器，页面重载/切后台/息屏都不丢开关状态；点「结束」结算上传。
 *   · 权限仍是前置条件：没权限时 setTracking(on) 直接拒绝；权限被撤（resume 检测）停跟踪、缓冲保留。
 *   · 采样不变：GPS_PROVIDER 1s/2m（纯 GNSS，无网络源）。
 *   · 点先进内存缓冲 + files/track_buffer.csv（进程被杀也不丢），网页 attach 后按水位线消费
 * 网页侧契约（static/app.js 轨迹段）：startWatcher=附着（幂等，仅观察）/
 * setTracking=记录开关（持久化）/ drain=取回未消费点 / status=状态（含 trackOn）/
 * clearBuffer=上传成功后清缓冲 / gpsState 事件=GPS 开关变化。
 */
@CapacitorPlugin(
        name = "BgLocation",
        permissions = {
                @Permission(
                        strings = { Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION },
                        alias = "location"
                )
        }
)
public class BgLocationPlugin extends Plugin {
    private static final String BUFFER_FILE = "track_buffer.csv";
    private static final String PREFS = "track";
    private static final String KEY_ON = "track_on";   // 记录开关，持久化（v0.46 起不控制卫星跟踪）
    private static final int MEM_CAP = 50000;        // 内存缓冲上限（约 14h @1Hz，超出丢最旧）
    private static final int FILE_CAP = 100000;      // 文件行数上限，超出压缩回内存尾 MEM_CAP 条
    private static final long NOTIFY_INTERVAL_MS = 10000;

    private LocationManager locationManager;
    private LocationListener listener;
    private PluginCall watcherCall;                 // 网页附着时的实时回调（可空：没页面也照采）
    private boolean tracking = false;               // GPS 监听是否已注册

    private final Object lock = new Object();
    private final List<double[]> buffer = new ArrayList<>();   // {lat,lng,ele,acc,timeMs}
    private int fileLines = 0;
    private ExecutorService io;
    private long lastNotifyAt = 0;

    /* ── 生命周期：三处自启入口 ─────────────────────────────────────── */

    @Override
    public void load() {
        if (io == null || io.isShutdown()) io = Executors.newSingleThreadExecutor();
        loadBufferFileSync();
        ensureTrackingIfPermitted();   // v0.46：已授权即恢复卫星跟踪（与记录开关无关）
    }

    @Override
    protected void handleOnResume() {
        if (!hasLocationPermission()) {
            if (tracking) stopGps();      // 权限被撤 → 停采（缓冲文件保留，数据不丢）
            return;
        }
        ensureTrackingIfPermitted();   // v0.46：回前台自愈，已授权即恢复卫星跟踪
    }

    @Override
    protected void handleOnDestroy() {
        releaseWatcher();
        if (locationManager != null && listener != null) {
            try { locationManager.removeUpdates(listener); } catch (Exception ignore) {}
        }
        listener = null;
        tracking = false;
        try { getContext().stopService(new Intent(getContext(), BgLocationService.class)); } catch (Exception ignore) {}
        if (io != null) { io.shutdown(); }
    }

    /* ── 网页侧 API ─────────────────────────────────────────────────── */

    /** 附着：幂等。已在跟踪=只换回调；没跟踪=顺带开跟踪。权限没给 → reject（弹窗归 ensurePermissionsUpfront，C18） */
    @PluginMethod(returnType = PluginMethod.RETURN_CALLBACK)
    public void startWatcher(PluginCall call) {
        if (!hasLocationPermission()) {
            call.reject("定位权限未授予", "NOT_AUTHORIZED");
            return;
        }
        call.setKeepAlive(true);
        PluginCall old = watcherCall;
        watcherCall = call;
        if (old != null && old != call) {
            try { old.release(getBridge()); } catch (Exception ignore) {}
        }
        ensureTrackingIfPermitted();
        JSObject ok = new JSObject();
        ok.put("attached", true);
        call.resolve(ok);
    }

    /** 老页面兼容：彻底停止（释放回调 + 关记录 + 停卫星跟踪）。缓冲保留；下次回前台按权限自愈。 */
    @PluginMethod
    public void stopWatcher(PluginCall call) {
        releaseWatcher();
        setTrackOnPref(false);
        stopGps();
        call.resolve();
    }

    /** 记录开关（持久化）：只决定卫星点是否落缓冲，不控制卫星跟踪（v0.46）。
        on=true 需已授权 + GPS 开；off 仅暂停记点/上传，卫星跟踪继续。 */
    @PluginMethod
    public void setTracking(PluginCall call) {
        boolean on = Boolean.TRUE.equals(call.getBoolean("on", Boolean.FALSE));
        setTrackOnPref(on);
        if (!on) {
            if (tracking) updateNotificationThrottled(true);   // 跟踪没跑就别留常驻通知
            JSObject off = new JSObject();
            off.put("trackOn", false);
            call.resolve(off);
            return;
        }
        if (!hasLocationPermission()) {
            call.reject("定位权限未授予", "NOT_AUTHORIZED");
            return;
        }
        if (!isGpsEnabled()) {
            call.reject("GPS 已关闭，请先打开系统定位", "GPS_OFF");
            return;
        }
        ensureTrackingIfPermitted();
        JSObject ok = new JSObject();
        ok.put("trackOn", true);
        ok.put("tracking", tracking);
        call.resolve(ok);
    }

    /** 取回原生缓冲里的点并清空缓冲（网页 attach 时调一次；按 GPS 时间戳由网页去重） */
    @PluginMethod
    public void drain(PluginCall call) {
        List<double[]> snap;
        synchronized (lock) {
            snap = new ArrayList<>(buffer);
            buffer.clear();
            fileLines = 0;
        }
        if (io != null && !io.isShutdown()) io.execute(this::deleteBufferFile);
        JSObject ret = new JSObject();
        org.json.JSONArray arr = new org.json.JSONArray();
        for (double[] p : snap) arr.put(toJson(p));
        ret.put("points", arr);
        ret.put("count", snap.size());
        call.resolve(ret);
    }

    /** 上传 GPX 成功后调用：清掉已结算的点（迟到点已在网页会话里，不重复清） */
    @PluginMethod
    public void clearBuffer(PluginCall call) {
        synchronized (lock) {
            buffer.clear();
            fileLines = 0;
        }
        if (io != null && !io.isShutdown()) io.execute(this::deleteBufferFile);
        call.resolve();
    }

    /** 状态：tracking/缓冲点数/provider/权限/GPS 开关/记录开关 */
    @PluginMethod
    public void status(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("tracking", tracking);
        ret.put("trackOn", trackOnPref());
        synchronized (lock) { ret.put("points", buffer.size()); }
        ret.put("provider", "gps");
        ret.put("permission", hasLocationPermission() ? "granted" : "denied");
        ret.put("gpsEnabled", isGpsEnabled());
        call.resolve(ret);
    }

    /* ── 采集引擎 ───────────────────────────────────────────────────── */

    private boolean hasLocationPermission() {
        try {
            if (getPermissionState("location") == PermissionState.GRANTED) return true;
        } catch (Exception ignore) {}
        try {   // 不依赖 Capacitor 权限缓存：直接问系统（跨插件授权也要可见）
            return ContextCompat.checkSelfPermission(getContext(),
                    Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        } catch (Exception e) {
            return false;
        }
    }

    private boolean isGpsEnabled() {
        try {
            return locationManager != null && locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER);
        } catch (Exception e) {
            return false;
        }
    }

    private boolean trackOnPref() {
        try {
            return getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_ON, false);
        } catch (Exception e) { return false; }
    }

    private void setTrackOnPref(boolean v) {
        try {
            getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                    .edit().putBoolean(KEY_ON, v).apply();
        } catch (Exception ignore) {}
    }

    /** 幂等自启：已授权且没在跟踪 → 注册 GPS 监听 + 拉起前台服务（v0.46：与记录开关无关） */
    private void ensureTrackingIfPermitted() {
        if (tracking) return;
        if (!hasLocationPermission()) return;
        startGps();
    }

    private void startGps() {
        if (tracking) return;
        Context ctx = getContext();
        if (ctx == null) return;
        if (locationManager == null) {
            locationManager = (LocationManager) ctx.getSystemService(Context.LOCATION_SERVICE);
        }
        if (locationManager == null) return;
        listener = new LocationListener() {
            @Override
            public void onLocationChanged(Location location) {
                if (!tracking || location == null) return;
                if (!trackOnPref()) return;   // v0.46：跟踪常开，只有记录开关 ON 才落点/推网页
                double[] p = {
                        location.getLatitude(),
                        location.getLongitude(),
                        location.hasAltitude() ? location.getAltitude() : Double.NaN,
                        location.hasAccuracy() ? location.getAccuracy() : Double.NaN,
                        (double) location.getTime()
                };
                synchronized (lock) {
                    if (buffer.size() >= MEM_CAP) buffer.remove(0);
                    buffer.add(p);
                }
                appendPointAsync(p);
                if (watcherCall != null) {
                    try { watcherCall.resolve(toJson(p)); } catch (Exception ignore) {}
                }
                updateNotificationThrottled(false);
            }
            @Override public void onStatusChanged(String provider, int status, Bundle extras) {}
            @Override public void onProviderEnabled(String provider) { notifyGpsState(true); }
            @Override public void onProviderDisabled(String provider) { notifyGpsState(false); }
        };
        try {
            // 纯 GNSS 采集（无网络兜底）：GPS 无 fix 期间（冷启动 30s~几分钟/深林遮挡）
            // 不出点，宁可轨迹空白也不记偏点；minDistance=2m 抑制静止抖动毛团，
            // 行走速度下每 1~2 秒仍有一个真实位移点
            locationManager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER, 1000, 2f, listener, Looper.getMainLooper());
        } catch (SecurityException e) {
            listener = null;
            return;   // 理论上进不来（前面查过权限），防御性返回
        } catch (IllegalArgumentException e) {
            listener = null;
            notifyGpsState(false);   // 设备无 GPS 定位源
            return;
        }
        tracking = true;
        try {
            androidx.core.content.ContextCompat.startForegroundService(
                    ctx, new Intent(ctx, BgLocationService.class));
        } catch (Exception ignore) {
            // 前台服务启动失败不阻塞：前台采集仍有效，仅后台可能被冻结
        }
        updateNotificationThrottled(true);
    }

    private void stopGps() {
        if (locationManager != null && listener != null) {
            try { locationManager.removeUpdates(listener); } catch (Exception ignore) {}
        }
        listener = null;
        tracking = false;
        try { getContext().stopService(new Intent(getContext(), BgLocationService.class)); } catch (Exception ignore) {}
    }

    private void releaseWatcher() {
        if (watcherCall != null) {
            try { watcherCall.release(getBridge()); } catch (Exception ignore) {}
            watcherCall = null;
        }
    }

    private void notifyGpsState(boolean enabled) {
        notifyListeners("gpsState", new JSObject().put("enabled", enabled));
        updateNotificationThrottled(true);
    }

    /* ── 通知（10s 节流；启停/GPS 变化即时） ─────────────────────────── */

    private void updateNotificationThrottled(boolean force) {
        long now = System.currentTimeMillis();
        if (!force && now - lastNotifyAt < NOTIFY_INTERVAL_MS) return;
        lastNotifyAt = now;
        int n;
        boolean gpsOn = isGpsEnabled();
        boolean recording = trackOnPref();
        synchronized (lock) { n = buffer.size(); }
        String msg;
        if (!gpsOn) {
            msg = "GPS 已关闭，卫星跟踪暂停（请打开系统定位）";
        } else if (recording) {
            msg = "仅 GPS · 保持卫星定位 · 记录轨迹中" + (n > 0 ? " · 已录 " + n + " 点" : "");
        } else {
            msg = "仅 GPS · 保持卫星定位，后续打卡/拍照坐标更准";
        }
        BgLocationService.updateNotification(getContext(), "卫星持续跟踪中", msg);
    }

    /* ── 点 ↔ JSON/CSV ──────────────────────────────────────────────── */

    private JSObject toJson(double[] p) {
        JSObject o = new JSObject();
        o.put("latitude", p[0]);
        o.put("longitude", p[1]);
        o.put("altitude", Double.isNaN(p[2]) ? JSONObject.NULL : p[2]);
        o.put("accuracy", Double.isNaN(p[3]) ? JSONObject.NULL : p[3]);
        o.put("provider", "gps");
        o.put("time", (long) p[4]);
        return o;
    }

    private static String csv(double[] p) {
        return p[0] + "," + p[1] + ","
                + (Double.isNaN(p[2]) ? "" : String.valueOf(p[2])) + ","
                + (Double.isNaN(p[3]) ? "" : String.valueOf(p[3])) + ","
                + (long) p[4];
    }

    private static double[] parseCsv(String line) {
        try {
            String[] f = line.split(",", -1);
            if (f.length < 5) return null;
            return new double[]{
                    Double.parseDouble(f[0]),
                    Double.parseDouble(f[1]),
                    f[2].isEmpty() ? Double.NaN : Double.parseDouble(f[2]),
                    f[3].isEmpty() ? Double.NaN : Double.parseDouble(f[3]),
                    Double.parseDouble(f[4])
            };
        } catch (Exception e) {
            return null;
        }
    }

    /* ── 文件缓冲（进程被杀也不丢已采点） ────────────────────────────── */

    private File bufferFile() {
        return new File(getContext().getFilesDir(), BUFFER_FILE);
    }

    /** load() 同步读取：冷启动一次，读完再开采，避免与追加竞态 */
    private void loadBufferFileSync() {
        File f = bufferFile();
        if (!f.exists()) return;
        List<double[]> loaded = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new FileReader(f))) {
            String line;
            while ((line = r.readLine()) != null) {
                if (line.isEmpty()) continue;
                double[] p = parseCsv(line);
                if (p != null) loaded.add(p);
            }
        } catch (Exception ignore) {}
        synchronized (lock) {
            buffer.clear();
            int from = Math.max(0, loaded.size() - MEM_CAP);
            buffer.addAll(loaded.subList(from, loaded.size()));
            fileLines = loaded.size();
        }
    }

    private void appendPointAsync(final double[] p) {
        if (io == null || io.isShutdown()) return;
        io.execute(() -> {
            try {
                synchronized (lock) {
                    File f = bufferFile();
                    boolean compact = fileLines >= FILE_CAP;
                    try (BufferedWriter w = new BufferedWriter(new FileWriter(f, !compact))) {
                        if (compact) {
                            for (double[] q : buffer) w.write(csv(q) + "\n");
                            fileLines = buffer.size();
                        } else {
                            w.write(csv(p) + "\n");
                            fileLines++;
                        }
                    }
                }
            } catch (Exception ignore) {}
        });
    }

    private void deleteBufferFile() {
        try {
            File f = bufferFile();
            if (f.exists()) f.delete();
        } catch (Exception ignore) {}
    }
}
