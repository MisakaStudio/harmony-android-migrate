package dev.hmos.migrate;

import android.content.*;
import android.os.Bundle;
import android.util.Base64;
import java.io.File;
import java.util.UUID;
import org.json.*;

final class IncomingStore {
  static File save(Context context, Intent intent, JSONObject parsed) throws Exception {
    JSONObject row =
        new JSONObject()
            .put("action", intent.getAction())
            .put("data_uri", intent.getDataString())
            .put("received_at", System.currentTimeMillis())
            .put("parsed", parsed)
            .put("stored_to_provider", false);
    JSONObject extras = new JSONObject();
    Bundle bundle = intent.getExtras();
    if (bundle != null)
      for (String key : bundle.keySet()) {
        Object value = bundle.get(key);
        if (value instanceof byte[])
          extras.put(
              key,
              new JSONObject()
                  .put("base64", Base64.encodeToString((byte[]) value, Base64.NO_WRAP)));
        else if (value instanceof Object[]) {
          JSONArray items = new JSONArray();
          for (Object part : (Object[]) value)
            if (part instanceof byte[])
              items.put(Base64.encodeToString((byte[]) part, Base64.NO_WRAP));
          extras.put(key, items);
        } else if (value instanceof String || value instanceof Number || value instanceof Boolean)
          extras.put(key, value);
      }
    row.put("extras", extras);
    File folder = new File(context.getFilesDir(), "incoming");
    folder.mkdirs();
    File file = new File(folder, UUID.randomUUID() + ".json");
    LocalFiles.save(file, row);
    return file;
  }

  static int pending(Context context) {
    File[] files = new File(context.getFilesDir(), "incoming").listFiles();
    int count = 0;
    if (files != null)
      for (File f : files)
        try {
          if (!LocalFiles.json(f).optBoolean("stored_to_provider")) count++;
        } catch (Exception e) {
          count++;
        }
    return count;
  }
}
