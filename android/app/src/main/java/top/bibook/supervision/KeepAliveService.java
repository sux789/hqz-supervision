package top.bibook.supervision;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/**
 * 录像期间的保活前台服务（v0.18）。
 *
 * 背景：调用系统相机录制时，本 App 退到后台，国产 ROM（华为/小米等）的省电策略会**杀掉本进程**，
 * 导致 WebView 被销毁 → 相机返回后页面重新加载（hash 丢失、JS 回调丢失）→ 表现为"录像确认后跳列表、
 * 文件名没记录、要重启 App 才补上"。以带通知的前台服务维持进程存活即可从根上避免（前台服务不会被
 * 普通省电策略回收）。录音像结束（拿到结果或取消）后立即停止本服务。
 */
public class KeepAliveService extends Service {
    private static final String CHANNEL_ID = "sup_keepalive";
    private static final int NOTIFICATION_ID = 0x5151;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                NotificationManager nm = getSystemService(NotificationManager.class);
                if (nm != null && nm.getNotificationChannel(CHANNEL_ID) == null) {
                    nm.createNotificationChannel(new NotificationChannel(
                            CHANNEL_ID, "录像中", NotificationManager.IMPORTANCE_LOW));
                }
            }
            Notification n = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                    ? new Notification.Builder(this, CHANNEL_ID)
                    : new Notification.Builder(this))
                    .setContentTitle("正在录制视频")
                    .setContentText("录制结束后会自动保存到相册")
                    .setSmallIcon(android.R.drawable.ic_menu_camera)
                    .setOngoing(true)
                    .build();
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            } else {
                startForeground(NOTIFICATION_ID, n);
            }
        } catch (Exception e) {
            android.util.Log.e("HqzSupervision", "keepAlive startForeground failed", e);
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                stopForeground(STOP_FOREGROUND_REMOVE);
            } else {
                stopForeground(true);
            }
        } catch (Exception ignored) {
        }
        super.onDestroy();
    }
}
