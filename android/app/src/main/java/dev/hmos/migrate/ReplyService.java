package dev.hmos.migrate;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.widget.Toast;
import org.json.JSONObject;

public class ReplyService extends Service {
  public IBinder onBind(Intent intent) {
    return null;
  }

  public int onStartCommand(Intent intent, int flags, int id) {
    try {
      IncomingStore.save(
          this,
          intent,
          new JSONObject()
              .put("requires_followup", true)
              .put("reason", "reply_not_sent_during_migration"));
    } catch (Exception ignored) {
    }
    Toast.makeText(this, "导入器不发送短信，请先恢复原默认短信应用。", Toast.LENGTH_LONG).show();
    stopSelf(id);
    return START_NOT_STICKY;
  }
}
