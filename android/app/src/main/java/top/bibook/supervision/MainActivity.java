package top.bibook.supervision;

import android.os.Bundle;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        // 注册原生能力插件（同 hqz-survey）：savePhoto 写系统相册 Pictures/{参数目录}/
        registerPlugin(AppPermissionsPlugin.class);
        super.onCreate(savedInstanceState);

        // 强制所有导航留在 App WebView 内，不调起系统浏览器（hqz-survey 踩坑：
        // 鸿蒙 4.2 会把重定向交给外部浏览器打开；且相片查看 window.open 也会跳出到浏览器）。
        // 关键：继承 Capacitor 的 BridgeWebViewClient，保留其全部默认行为，仅覆盖外跳判断。
        try {
            if (bridge != null && bridge.getWebView() != null) {
                bridge.getWebView().setWebViewClient(new BridgeWebViewClient(bridge) {
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                        return false; // 一律在 WebView 内加载
                    }

                    @Override
                    public boolean shouldOverrideUrlLoading(WebView view, String url) {
                        return false; // 一律在 WebView 内加载
                    }
                });
            }
        } catch (Exception e) {
            android.util.Log.e("HqzSupervision", "setWebViewClient failed", e);
        }
    }
}
