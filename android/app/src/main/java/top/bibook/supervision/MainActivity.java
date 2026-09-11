package top.bibook.supervision;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // 注册原生能力插件（同 hqz-survey）：savePhoto 写系统相册 Pictures/{参数目录}/
        registerPlugin(AppPermissionsPlugin.class);
    }
}
