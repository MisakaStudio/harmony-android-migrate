package dev.hmos.migrate;

import android.util.Base64;
import java.io.File;
import java.util.*;
import org.json.*;

final class PackageData {
  final JSONObject document;
  final String inputHash;
  final Map<Integer, Set<String>> sourceSlots = new TreeMap<>();
  static final String[] KINDS = {"sms", "calls", "mms"};
  static final Set<String> SMS =
      set(
          "protocol,address,date,date_sent,type,subject,body,service_center,read,status,locked,sub_id");
  static final Set<String> CALLS =
      set(
          "number,date,duration,type,presentation,subscription_id,subscription_component_name,simid");
  static final Set<String> MMS =
      set(
          "date,date_sent,msg_box,m_type,read,locked,sub_id,ct_t,text_only,sub,sub_cs,m_size,m_id,tr_id");

  static Set<String> set(String text) {
    return new HashSet<>(Arrays.asList(text.split(",")));
  }

  static void require(boolean condition, String code) {
    if (!condition) throw new IllegalArgumentException(code);
  }

  static void fields(JSONObject value, Set<String> allowed) {
    Iterator<String> keys = value.keys();
    while (keys.hasNext()) require(allowed.contains(keys.next()), "unknown_provider_field");
  }

  PackageData(File file) throws Exception {
    inputHash = LocalFiles.sha(file);
    document = LocalFiles.json(file);
    require(
        "hmos-migrate/native".equals(document.optString("schema"))
            && document.optInt("version") == 1,
        "unsupported_schema");
    JSONObject units = document.getJSONObject("date_units");
    require(
        "milliseconds".equals(units.optString("sms"))
            && "milliseconds".equals(units.optString("calls"))
            && "seconds".equals(units.optString("mms")),
        "invalid_date_units");
    require(document.getJSONObject("conversion").has("complete"), "missing_conversion_report");
    JSONObject records = document.getJSONObject("records");
    Set<String> ids = new HashSet<>(), mmsIds = new HashSet<>();
    long mediaBytes = 0;
    int count = 0;
    for (String kind : KINDS) {
      JSONArray rows = records.getJSONArray(kind);
      count += rows.length();
      for (int i = 0; i < rows.length(); i++) {
        JSONObject r = rows.getJSONObject(i), v = r.getJSONObject("values");
        String id = r.getString("source_id");
        require(id.matches("[a-f0-9]{64}") && ids.add(id), "duplicate_or_invalid_source_id");
        int slot = r.getInt("source_slot");
        require(slot >= -1, "invalid_source_slot");
        sourceSlots.computeIfAbsent(slot, key -> new HashSet<>()).add(kind);
        fields(v, kind.equals("sms") ? SMS : kind.equals("calls") ? CALLS : MMS);
        require(v.getLong("date") > 0, "invalid_date");
        if (kind.equals("sms")) {
          require(v.getInt("type") == 1 || v.getInt("type") == 2, "unsafe_sms_type");
          require(
              v.get("body") instanceof String
                  && v.get("address") instanceof String
                  && !v.getString("address").isEmpty(),
              "invalid_sms");
          require(v.getInt("read") == 0 || v.getInt("read") == 1, "invalid_read_state");
        } else if (kind.equals("calls")) {
          require(
              v.get("number") instanceof String
                  && v.getLong("duration") >= 0
                  && v.getInt("type") >= 1
                  && v.getInt("type") <= 7,
              "invalid_call");
          require(
              v.getInt("presentation") >= 1 && v.getInt("presentation") <= 5,
              "invalid_presentation");
        } else {
          require(v.getInt("msg_box") == 1 && v.getInt("m_type") == 132, "unsafe_mms_type");
          String expectedId = "hmos-" + id.substring(0, 32);
          require(
              expectedId.equals(v.getString("m_id"))
                  && expectedId.equals(v.getString("tr_id"))
                  && mmsIds.add(expectedId),
              "invalid_mms_identity");
          JSONArray parts = r.getJSONArray("parts"), addresses = r.getJSONArray("addresses");
          require(
              parts.length() > 0
                  && parts.length() <= 100
                  && addresses.length() > 0
                  && addresses.length() <= 100,
              "invalid_mms_parts");
          boolean from = false;
          Set<Integer> sequences = new HashSet<>();
          for (int j = 0; j < addresses.length(); j++) {
            JSONObject a = addresses.getJSONObject(j);
            fields(a, set("address,type,charset"));
            require(
                a.get("address") instanceof String && !a.getString("address").isEmpty(),
                "invalid_mms_address");
            int type = a.getInt("type");
            require(
                type == 137 || type == 151 || type == 130 || type == 129, "invalid_address_type");
            from |= type == 137;
          }
          require(from, "missing_mms_sender");
          for (int j = 0; j < parts.length(); j++) {
            JSONObject p = parts.getJSONObject(j), pv = p.getJSONObject("values");
            fields(pv, set("seq,ct,cl,cid,name"));
            String ct = pv.getString("ct");
            require(ct.equals("image/jpeg") || ct.equals("image/png"), "unsupported_mms_part");
            require(
                pv.getString("cl").matches("[A-Za-z0-9_.-]{1,100}")
                    && sequences.add(pv.getInt("seq")),
                "invalid_part_name_or_sequence");
            byte[] data = Base64.decode(p.getString("data"), Base64.NO_WRAP);
            require(
                data.length > 0
                    && data.length <= 20 * 1024 * 1024
                    && LocalFiles.sha(data).equals(p.getString("sha256")),
                "attachment_checksum_failed");
            boolean jpeg =
                data.length >= 3
                    && (data[0] & 255) == 255
                    && (data[1] & 255) == 216
                    && (data[2] & 255) == 255;
            boolean png =
                data.length >= 8
                    && Arrays.equals(
                        Arrays.copyOf(data, 8),
                        new byte[] {(byte) 137, 80, 78, 71, 13, 10, 26, 10});
            require(ct.equals("image/jpeg") ? jpeg : png, "attachment_type_mismatch");
            mediaBytes += data.length;
          }
        }
      }
    }
    require(count <= 50000 && mediaBytes <= 20 * 1024 * 1024, "package_limit_exceeded");
  }

  boolean messages() throws Exception {
    return count("sms") + count("mms") > 0;
  }

  int count(String kind) throws Exception {
    return document.getJSONObject("records").getJSONArray(kind).length();
  }

  boolean partial() throws Exception {
    return !document.getJSONObject("conversion").getBoolean("complete");
  }

  JSONArray mapped(JSONObject mapping, String kind) throws Exception {
    JSONArray result = new JSONArray();
    JSONArray rows = document.getJSONObject("records").getJSONArray(kind);
    JSONObject slots = mapping.getJSONObject("slots");
    for (int i = 0; i < rows.length(); i++) {
      JSONObject r = new JSONObject(rows.getJSONObject(i).toString()),
          v = r.getJSONObject("values");
      JSONObject target = slots.getJSONObject(String.valueOf(r.getInt("source_slot")));
      if (kind.equals("calls")) {
        v.remove("subscription_id");
        v.remove("subscription_component_name");
        v.remove("simid");
        if (!target.optBoolean("unassigned")) {
          JSONObject a = target.getJSONObject("phone_account");
          require(a.optInt("user_id") == 0, "unsupported_user");
          v.put("subscription_id", a.getString("id"))
              .put("subscription_component_name", a.getString("component"));
          JSONObject extras = target.optJSONObject("call_extras");
          if (extras != null) {
            fields(extras, set("simid"));
            require(extras.getInt("simid") >= 0, "invalid_simid");
            v.put("simid", extras.getInt("simid"));
          }
        }
      } else v.put("sub_id", target.optBoolean("unassigned") ? -1 : target.getInt("sub_id"));
      result.put(r);
    }
    return result;
  }
}
