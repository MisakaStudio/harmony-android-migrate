package dev.hmos.migrate;

import android.content.*;
import org.json.JSONObject;

/** Fallback evidence only, not an MMS client; the import workflow requires radios offline. */
public class WapReceiver extends BroadcastReceiver {
  public void onReceive(Context context, Intent intent) {
    PendingResult pending = goAsync();
    new Thread(
            () -> {
              try {
                IncomingStore.save(
                    context, intent, new JSONObject().put("requires_followup", true));
              } catch (Exception ignored) {
              } finally {
                pending.finish();
              }
            },
            "incoming-mms-preservation")
        .start();
  }
}
