package dev.hmos.migrate;

import android.content.*;
import android.net.Uri;
import android.provider.Telephony;
import android.telephony.SmsMessage;
import java.io.File;
import org.json.JSONObject;

public class SmsReceiver extends BroadcastReceiver {
  public void onReceive(Context context, Intent intent) {
    PendingResult pending = goAsync();
    new Thread(
            () -> {
              try {
                SmsMessage[] parts = Telephony.Sms.Intents.getMessagesFromIntent(intent);
                if (parts == null || parts.length == 0) return;
                StringBuilder body = new StringBuilder();
                for (SmsMessage part : parts) body.append(part.getMessageBody());
                Object subscription =
                    intent.getExtras() == null ? null : intent.getExtras().get("subscription");
                int sub = subscription instanceof Number ? ((Number) subscription).intValue() : -1;
                JSONObject parsed =
                    new JSONObject()
                        .put("address", parts[0].getOriginatingAddress())
                        .put("body", body.toString())
                        .put("date", System.currentTimeMillis())
                        .put("date_sent", parts[0].getTimestampMillis())
                        .put("sub_id", sub);
                File backup = IncomingStore.save(context, intent, parsed);
                ContentValues values = new ContentValues();
                values.put("address", parsed.getString("address"));
                values.put("body", body.toString());
                values.put("date", parsed.getLong("date"));
                values.put("date_sent", parsed.getLong("date_sent"));
                values.put("type", 1);
                values.put("read", 0);
                values.put("seen", 0);
                values.put("sub_id", sub);
                Uri result =
                    context.getContentResolver().insert(Telephony.Sms.Inbox.CONTENT_URI, values);
                if (result != null) {
                  JSONObject stored = LocalFiles.json(backup);
                  stored.put("stored_to_provider", true).put("uri", result.toString());
                  LocalFiles.save(backup, stored);
                }
              } catch (Exception ignored) {
              } finally {
                pending.finish();
              }
            },
            "incoming-sms-preservation")
        .start();
  }
}
