package dev.hmos.migrate;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.UserManager;
import android.telecom.*;
import android.telephony.*;
import java.util.*;
import org.json.*;

final class DeviceProfile {
  static JSONObject collect(Context context, String nonce) throws Exception {
    JSONObject result =
        new JSONObject()
            .put("schema", "hmos-migrate/target")
            .put("version", 1)
            .put("origin", "android-api")
            .put("request_id", nonce)
            .put("captured_at", System.currentTimeMillis())
            .put(
                "device",
                new JSONObject()
                    .put("manufacturer", Build.MANUFACTURER)
                    .put("model", Build.MODEL)
                    .put("sdk", Build.VERSION.SDK_INT));
    JSONArray subscriptions = new JSONArray(), accounts = new JSONArray(), issues = new JSONArray();
    result
        .put("subscriptions", subscriptions)
        .put("phone_accounts", accounts)
        .put("issues", issues);
    if (!context.getSystemService(UserManager.class).isSystemUser()) {
      issues.put("unsupported_user");
      return result;
    }
    if (context.checkSelfPermission(Manifest.permission.READ_PHONE_STATE)
        != PackageManager.PERMISSION_GRANTED) {
      issues.put("phone_state_permission_required");
      return result;
    }
    SubscriptionManager sm = context.getSystemService(SubscriptionManager.class);
    TelephonyManager tm = context.getSystemService(TelephonyManager.class);
    TelecomManager telecom = context.getSystemService(TelecomManager.class);
    try {
      List<SubscriptionInfo> active = sm.getActiveSubscriptionInfoList();
      if (active != null)
        for (SubscriptionInfo s : active)
          subscriptions.put(
              new JSONObject()
                  .put("sub_id", s.getSubscriptionId())
                  .put("logical_slot_index", s.getSimSlotIndex())
                  .put("label", String.valueOf(s.getDisplayName()))
                  .put("carrier", String.valueOf(s.getCarrierName()))
                  .put("embedded", s.isEmbedded())
                  .put("evidence", "SubscriptionManager"));
    } catch (SecurityException e) {
      issues.put("subscriptions_denied");
    }
    try {
      for (PhoneAccountHandle h : telecom.getCallCapablePhoneAccounts()) {
        if (!h.getUserHandle().equals(android.os.Process.myUserHandle())) continue;
        int sub = SubscriptionManager.INVALID_SUBSCRIPTION_ID;
        try {
          sub = tm.getSubscriptionId(h);
        } catch (RuntimeException ignored) {
        }
        // This public association needs READ_PHONE_STATE. getPhoneAccount() additionally
        // requires READ_PHONE_NUMBERS for target 31+, which is unnecessary for ID discovery.
        if (sub < 0) issues.put("phone_account_association_unavailable");
        String label = sub < 0 ? "未关联订阅 · " + h.getComponentName().getPackageName() : "订阅 " + sub;
        for (int i = 0; i < subscriptions.length(); i++)
          if (subscriptions.getJSONObject(i).getInt("sub_id") == sub)
            label = subscriptions.getJSONObject(i).optString("label", label);
        JSONObject item =
            new JSONObject()
                .put("id", h.getId())
                .put("component", h.getComponentName().flattenToString())
                .put("user_id", 0)
                .put("enabled", true)
                .put("label", label)
                .put("id_state", "exact")
                .put("sub_id", sub >= 0 ? sub : JSONObject.NULL)
                .put("association", sub >= 0 ? "android_api" : "unresolved");
        accounts.put(item);
      }
    } catch (SecurityException e) {
      issues.put("phone_accounts_denied");
    }
    // A vendor extension is accepted only if existing target records consistently
    // associate it with that exact account. Never derive it from slot+1 or subId.
    if (context.checkSelfPermission(Manifest.permission.READ_CALL_LOG)
        == PackageManager.PERMISSION_GRANTED) {
      Map<String, Set<Integer>> observed = new HashMap<>();
      try (Cursor c =
          context
              .getContentResolver()
              .query(
                  Uri.parse("content://call_log/calls"),
                  new String[] {"subscription_id", "subscription_component_name", "simid"},
                  null,
                  null,
                  "date DESC")) {
        if (c != null)
          for (int i = 0; i < 512 && c.moveToNext(); i++) {
            if (c.isNull(0) || c.isNull(1) || c.isNull(2) || c.getInt(2) < 0) continue;
            String key = c.getString(1) + "\n" + c.getString(0);
            observed.computeIfAbsent(key, k -> new HashSet<>()).add(c.getInt(2));
          }
        for (int i = 0; i < accounts.length(); i++) {
          JSONObject a = accounts.getJSONObject(i);
          Set<Integer> values = observed.get(a.getString("component") + "\n" + a.getString("id"));
          if (values != null && values.size() == 1)
            a.put("call_extras", new JSONObject().put("simid", values.iterator().next()))
                .put("call_extras_evidence", "consistent_existing_target_records");
        }
      } catch (RuntimeException e) {
        result.put("call_extensions_status", "standard_fields_only");
      }
    }
    boolean linked = accounts.length() > 0;
    for (int i = 0; i < accounts.length(); i++)
      linked &= "android_api".equals(accounts.getJSONObject(i).optString("association"));
    result.put("status", subscriptions.length() > 0 && linked ? "complete" : "partial");
    return result;
  }
}
