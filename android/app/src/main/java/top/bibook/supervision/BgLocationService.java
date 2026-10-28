package top.bibook.supervision;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;

/**
 * 后台定位前台服务壳：仅负责常驻通知（foregroundServiceType=location）保住
 * 进程优先级（不被系统冻结/回收），定位采集在 BgLocationPlugin（同进程）内进行。
 * 通知点击回到应用主界面。
 *
 * v0.32：通知构建抽成静态助手（ensureChannel/buildNotification/updateNotification），
 * 插件在跟踪过程中按 10s 节流更新同 ID 通知（记录点数/GPS 开关状态）。
 * v0.46：通知语义改为「卫星持续跟踪中」——跟踪由权限决定常开，记录轨迹只是其中一种用途。
 */
public class BgLocationService extends Service {
    static final String CHANNEL_ID = "bg_location";
    static final int NOTIFICATION_ID = 28352;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String title = (intent != null && intent.getStringExtra("title") != null)
                ? intent.getStringExtra("title") : "卫星持续跟踪中";
        String message = (intent != null && intent.getStringExtra("message") != null)
                ? intent.getStringExtra("message") : "正在保持卫星定位（仅 GPS），后续定位更准";
        ensureChannel(this);
        Notification n = buildNotification(this, title, message);
        try {
            // API 29+ 用 location 型前台服务：灭屏/后台持续定位的合法通道；
            // API 34+ 需 Manifest 声明 FOREGROUND_SERVICE_LOCATION（已声明，装机自动授予）
            ServiceCompat.startForeground(this, NOTIFICATION_ID, n,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        } catch (Exception e) {
            try { startForeground(NOTIFICATION_ID, n); } catch (Exception ignore) {}
        }
        return START_NOT_STICKY;
    }

    static void ensureChannel(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(new NotificationChannel(
                        CHANNEL_ID, "卫星持续跟踪（仅 GPS）", NotificationManager.IMPORTANCE_HIGH));
            }
        }
    }

    static Notification buildNotification(Context ctx, String title, String message) {
        ensureChannel(ctx);
        PendingIntent pi = null;
        Intent launch = ctx.getPackageManager().getLaunchIntentForPackage(ctx.getPackageName());
        if (launch != null) {
            launch.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
            pi = PendingIntent.getActivity(ctx, 0, launch,
                    PendingIntent.FLAG_CANCEL_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        }
        return new NotificationCompat.Builder(ctx, CHANNEL_ID)
                .setContentTitle(title)
                .setContentText(message)
                .setOngoing(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setWhen(System.currentTimeMillis())
                .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                .setContentIntent(pi)
                .build();
    }

    /** 插件侧更新同 ID 通知（服务已在跑时安全；不在跑时 notify 一条常驻通知也无副作用） */
    static void updateNotification(Context ctx, String title, String message) {
        try {
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) nm.notify(NOTIFICATION_ID, buildNotification(ctx, title, message));
        } catch (Exception ignore) {}
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
