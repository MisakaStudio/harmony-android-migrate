package dev.hmos.migrate;

import android.content.*;
import android.database.Cursor;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.provider.Settings;
import android.util.Base64;
import android.util.JsonWriter;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.json.*;

final class MigrationEngine {
  interface Progress {
    void update(String state, int completed, int total);
  }

  final Context context;
  final ContentResolver resolver;
  final PackageData input;
  final JSONObject mapping;
  final File runDir;
  final Progress progress;
  final Map<String, JSONArray> records = new LinkedHashMap<>();
  JSONObject journal;

  MigrationEngine(
      Context context, PackageData input, JSONObject mapping, File runDir, Progress progress)
      throws Exception {
    this.context = context.getApplicationContext();
    resolver = context.getContentResolver();
    this.input = input;
    this.mapping = mapping;
    this.runDir = runDir;
    this.progress = progress;
    for (String kind : PackageData.KINDS) records.put(kind, input.mapped(mapping, kind));
    File file = new File(runDir, "journal.json");
    String mappingHash = LocalFiles.sha(mapping.toString().getBytes(StandardCharsets.UTF_8));
    if (file.exists()) {
      journal = LocalFiles.json(file);
      PackageData.require(input.inputHash.equals(journal.getString("input_hash")), "input_changed");
      PackageData.require(mappingHash.equals(journal.getString("mapping_hash")), "mapping_changed");
      PackageData.require(
          "per-record-v1".equals(journal.optString("operation_storage")),
          "unsupported_journal_version");
    } else {
      PackageData.require(!new File(runDir, "operations").exists(), "journal_header_missing");
      journal =
          new JSONObject()
              .put("version", 1)
              .put("input_hash", input.inputHash)
              .put("mapping_hash", mappingHash)
              .put("operation_storage", "per-record-v1");
    }
  }

  static String base(String kind) {
    return kind.equals("calls") ? "content://call_log/calls" : "content://" + kind;
  }

  void guard() throws Exception {
    PackageData.require(
        context.getSystemService(android.os.UserManager.class).isSystemUser(), "unsupported_user");
    if (input.messages()) {
      PackageData.require(
          Settings.Global.getInt(resolver, Settings.Global.AIRPLANE_MODE_ON, 0) == 1,
          "airplane_required");
      WifiManager wifi = context.getSystemService(WifiManager.class);
      PackageData.require(wifi == null || !wifi.isWifiEnabled(), "wifi_must_be_off");
      PackageData.require(
          context
              .getSystemService(android.app.role.RoleManager.class)
              .isRoleHeld(android.app.role.RoleManager.ROLE_SMS),
          "sms_role_required");
    }
  }

  void validateTarget() throws Exception {
    JSONObject profile = DeviceProfile.collect(context, "validate");
    JSONArray subs = profile.getJSONArray("subscriptions"),
        accounts = profile.getJSONArray("phone_accounts");
    for (Integer slot : input.sourceSlots.keySet()) {
      JSONObject selected = mapping.getJSONObject("slots").getJSONObject(String.valueOf(slot));
      if (selected.optBoolean("unassigned")) continue;
      if (input.sourceSlots.get(slot).contains("sms")
          || input.sourceSlots.get(slot).contains("mms")) {
        boolean found = false;
        for (int i = 0; i < subs.length(); i++)
          found |= subs.getJSONObject(i).getInt("sub_id") == selected.getInt("sub_id");
        PackageData.require(found, "target_subscription_changed");
      }
      if (input.sourceSlots.get(slot).contains("calls")) {
        JSONObject a = selected.getJSONObject("phone_account");
        boolean found = false;
        for (int i = 0; i < accounts.length(); i++) {
          JSONObject active = accounts.getJSONObject(i);
          if (active.getString("id").equals(a.getString("id"))
              && active.getString("component").equals(a.getString("component"))
              && active.optBoolean("enabled", true)) {
            found = true;
            if (!active.isNull("sub_id") && selected.has("sub_id"))
              PackageData.require(
                  active.getInt("sub_id") == selected.getInt("sub_id"),
                  "subscription_account_mismatch");
            if (selected.has("call_extras")) {
              JSONObject extra = selected.getJSONObject("call_extras");
              for (String field : Collections.list(new IteratorEnumeration(extra.keys()))) {
                try (Cursor c =
                    resolver.query(
                        Uri.parse(base("calls")), new String[] {field}, null, null, null)) {
                  PackageData.require(
                      c != null && c.getColumnIndex(field) >= 0, "vendor_field_unavailable");
                }
              }
              JSONObject observed = active.optJSONObject("call_extras");
              if (observed != null && observed.has("simid"))
                PackageData.require(
                    observed.getInt("simid") == extra.getInt("simid"), "vendor_field_conflict");
            }
          }
        }
        PackageData.require(found, "target_phone_account_changed");
      }
    }
  }

  static class IteratorEnumeration implements Enumeration<String> {
    final Iterator<String> it;

    IteratorEnumeration(Iterator<String> it) {
      this.it = it;
    }

    public boolean hasMoreElements() {
      return it.hasNext();
    }

    public String nextElement() {
      return it.next();
    }
  }

  JSONObject cursorRow(Cursor c) throws Exception {
    JSONObject row = new JSONObject();
    for (int i = 0; i < c.getColumnCount(); i++) {
      Object value;
      switch (c.getType(i)) {
        case Cursor.FIELD_TYPE_NULL:
          value = JSONObject.NULL;
          break;
        case Cursor.FIELD_TYPE_INTEGER:
          value = c.getLong(i);
          break;
        case Cursor.FIELD_TYPE_FLOAT:
          value = c.getDouble(i);
          break;
        case Cursor.FIELD_TYPE_BLOB:
          value =
              new JSONObject()
                  .put("blob_base64", Base64.encodeToString(c.getBlob(i), Base64.NO_WRAP));
          break;
        default:
          value = c.getString(i);
      }
      row.put(c.getColumnName(i), value);
    }
    return row;
  }

  JSONArray query(String uri, String selection, String... args) throws Exception {
    JSONArray rows = new JSONArray();
    try (Cursor c = resolver.query(Uri.parse(uri), null, selection, args, null)) {
      if (c == null) throw new IOException("provider_query_failed");
      while (c.moveToNext()) {
        if (rows.length() >= 10000) throw new IOException("too_many_candidate_records");
        rows.put(cursorRow(c));
      }
    }
    return rows;
  }

  byte[] binary(String uri) throws Exception {
    try (InputStream stream = resolver.openInputStream(Uri.parse(uri))) {
      if (stream == null) throw new IOException("attachment_unreadable");
      return LocalFiles.read(stream, 20 * 1024 * 1024);
    }
  }

  ContentValues contentValues(JSONObject v) throws Exception {
    ContentValues result = new ContentValues();
    Iterator<String> keys = v.keys();
    while (keys.hasNext()) {
      String key = keys.next();
      Object value = v.get(key);
      if (value == JSONObject.NULL) result.putNull(key);
      else if (value instanceof Number) result.put(key, ((Number) value).longValue());
      else if (value instanceof String) result.put(key, (String) value);
      else throw new IllegalArgumentException("invalid_provider_value");
    }
    return result;
  }

  String insert(String uri, JSONObject values) throws Exception {
    guard();
    Uri inserted = resolver.insert(Uri.parse(uri), contentValues(values));
    if (inserted == null) throw new IOException("provider_insert_denied");
    return inserted.toString();
  }

  void saveJournal() throws Exception {
    LocalFiles.save(new File(runDir, "journal.json"), journal);
  }

  void jsonValue(JsonWriter w, Object value) throws Exception {
    if (value == null || value == JSONObject.NULL) w.nullValue();
    else if (value instanceof JSONObject) {
      w.beginObject();
      Iterator<String> it = ((JSONObject) value).keys();
      while (it.hasNext()) {
        String k = it.next();
        w.name(k);
        jsonValue(w, ((JSONObject) value).get(k));
      }
      w.endObject();
    } else if (value instanceof Number) w.value((Number) value);
    else if (value instanceof Boolean) w.value((Boolean) value);
    else w.value(String.valueOf(value));
  }

  void backup() throws Exception {
    File destination = new File(runDir, "before.json");
    if (journal.optBoolean("backup_complete")) {
      PackageData.require(
          destination.isFile()
              && LocalFiles.sha(destination).equals(journal.getString("backup_sha256")),
          "backup_checksum_failed");
      return;
    }
    progress.update("backup", 0, 0);
    android.util.AtomicFile atomic = new android.util.AtomicFile(destination);
    FileOutputStream file = null;
    try {
      file = atomic.startWrite();
      JsonWriter writer = new JsonWriter(new OutputStreamWriter(file, StandardCharsets.UTF_8));
      writer
          .beginObject()
          .name("schema")
          .value("hmos-migrate/provider-backup")
          .name("version")
          .value(1)
          .name("captured_at")
          .value(System.currentTimeMillis());
      for (String kind : PackageData.KINDS) {
        writer.name(kind).beginArray();
        if (input.count(kind) > 0)
          try (Cursor c = resolver.query(Uri.parse(base(kind)), null, null, null, null)) {
            if (c == null) throw new IOException("backup_query_failed");
            while (c.moveToNext()) jsonValue(writer, cursorRow(c));
          }
        writer.endArray();
      }
      writer.name("mms_parts").beginArray();
      if (input.count("mms") > 0)
        try (Cursor c = resolver.query(Uri.parse("content://mms/part"), null, null, null, null)) {
          if (c == null) throw new IOException("backup_query_failed");
          while (c.moveToNext()) {
            JSONObject row = cursorRow(c);
            if (!row.isNull("_data") && !row.optString("_data").isEmpty())
              row.put(
                  "backup_binary",
                  Base64.encodeToString(
                      binary("content://mms/part/" + row.getLong("_id")), Base64.NO_WRAP));
            jsonValue(writer, row);
          }
        }
      writer.endArray().name("mms_addresses").beginArray();
      if (input.count("mms") > 0)
        try (Cursor c =
            resolver.query(Uri.parse("content://mms"), new String[] {"_id"}, null, null, null)) {
          while (c != null && c.moveToNext()) {
            JSONArray addresses = query("content://mms/" + c.getLong(0) + "/addr", null);
            for (int i = 0; i < addresses.length(); i++)
              jsonValue(writer, addresses.getJSONObject(i));
          }
        }
      writer.endArray().endObject();
      writer.flush();
      atomic.finishWrite(file);
      file = null;
    } catch (Exception e) {
      if (file != null) atomic.failWrite(file);
      throw e;
    }
    journal.put("backup_complete", true).put("backup_sha256", LocalFiles.sha(destination));
    saveJournal();
  }

  static String body(String s) {
    return s.startsWith("\uFEFF") ? s.substring(1) : s;
  }

  boolean sameSim(String kind, JSONObject desired, JSONObject actual) throws Exception {
    for (String field :
        kind.equals("calls")
            ? new String[] {"subscription_id", "subscription_component_name", "simid"}
            : new String[] {"sub_id"})
      if (desired.has(field)
          && !(field.equals("sub_id") && desired.getInt(field) < 0)
          && !String.valueOf(desired.get(field)).equals(String.valueOf(actual.opt(field))))
        return false;
    return true;
  }

  JSONArray candidates(String kind, JSONObject row) throws Exception {
    if (kind.equals("sms")) {
      String normalized = body(row.getString("body"));
      return query(
          base(kind),
          "date=? AND address=? AND type=? AND (body=? OR body=?)",
          row.getString("date"),
          row.getString("address"),
          row.getString("type"),
          normalized,
          "\uFEFF" + normalized);
    }
    if (kind.equals("calls"))
      return query(
          base(kind),
          "date=? AND number=? AND duration=? AND type=?",
          row.getString("date"),
          row.getString("number"),
          row.getString("duration"),
          row.getString("type"));
    return query(
        base(kind), "date=? AND msg_box=?", row.getString("date"), row.getString("msg_box"));
  }

  boolean mmsComplete(JSONObject record, JSONObject actual) throws Exception {
    JSONObject values = record.getJSONObject("values");
    String uri = "content://mms/" + actual.getLong("_id");
    if (!values.getString("m_id").equals(actual.optString("m_id"))
        || !values.getString("tr_id").equals(actual.optString("tr_id"))) return false;
    if (values.getLong("date") != actual.optLong("date")
        || values.getInt("msg_box") != actual.optInt("msg_box")) return false;
    if (values.getInt("m_type") != actual.optInt("m_type")) return false;
    if (!(values.isNull("sub") ? "" : values.optString("sub"))
        .equals(actual.isNull("sub") ? "" : actual.optString("sub"))) return false;
    if (!sameSim("mms", values, actual)) return false;
    JSONArray expectedAddresses = record.getJSONArray("addresses"),
        addresses = query(uri + "/addr", null);
    Set<String> expected = new HashSet<>(), got = new HashSet<>();
    for (int i = 0; i < expectedAddresses.length(); i++) {
      JSONObject a = expectedAddresses.getJSONObject(i);
      expected.add(a.getInt("type") + ":" + a.getString("address"));
    }
    for (int i = 0; i < addresses.length(); i++) {
      JSONObject a = addresses.getJSONObject(i);
      got.add(a.getInt("type") + ":" + a.getString("address"));
    }
    if (!expected.equals(got)) return false;
    JSONArray parts = query(uri + "/part", null), source = record.getJSONArray("parts");
    Map<Integer, String> hashes = new TreeMap<>(), sourceHashes = new TreeMap<>();
    int smilCount = 0;
    String smilText = null;
    for (int i = 0; i < source.length(); i++) {
      JSONObject p = source.getJSONObject(i);
      sourceHashes.put(p.getJSONObject("values").getInt("seq"), p.getString("sha256"));
    }
    for (int i = 0; i < parts.length(); i++) {
      JSONObject p = parts.getJSONObject(i);
      String ct = p.getString("ct");
      if (ct.equals("application/smil")) {
        smilCount++;
        smilText = p.optString("text");
        continue;
      }
      if (!ct.equals("image/jpeg") && !ct.equals("image/png")) return false;
      if (hashes.put(
              p.getInt("seq"), LocalFiles.sha(binary("content://mms/part/" + p.getLong("_id"))))
          != null) return false;
    }
    return sourceHashes.equals(hashes) && smilCount == 1 && smilMatches(record, smilText);
  }

  boolean smilMatches(JSONObject record, String text) throws Exception {
    if (text == null
        || text.length() > 65536
        || text.toUpperCase(java.util.Locale.ROOT).contains("<!DOCTYPE")
        || text.toUpperCase(java.util.Locale.ROOT).contains("<!ENTITY")) return false;
    TreeMap<Integer, String> names = new TreeMap<>();
    JSONArray parts = record.getJSONArray("parts");
    for (int i = 0; i < parts.length(); i++) {
      JSONObject p = parts.getJSONObject(i).getJSONObject("values");
      names.put(p.getInt("seq"), p.getString("cl"));
    }
    ArrayList<String> actual = new ArrayList<>();
    boolean root = false;
    try {
      org.xmlpull.v1.XmlPullParser parser = android.util.Xml.newPullParser();
      parser.setInput(new java.io.StringReader(text));
      for (int event = parser.getEventType();
          event != org.xmlpull.v1.XmlPullParser.END_DOCUMENT;
          event = parser.next()) {
        if (event == org.xmlpull.v1.XmlPullParser.START_TAG) {
          String tag = parser.getName();
          if (!Arrays.asList("smil", "head", "body", "par", "img").contains(tag)) return false;
          if (parser.getDepth() == 1) {
            if (!tag.equals("smil")) return false;
            root = true;
          }
          if (tag.equals("img")) actual.add(parser.getAttributeValue(null, "src"));
        } else if (event == org.xmlpull.v1.XmlPullParser.TEXT && !parser.getText().trim().isEmpty())
          return false;
      }
    } catch (Exception e) {
      return false;
    }
    return root && actual.equals(new ArrayList<>(names.values()));
  }

  String expectedSmil(JSONObject record) throws Exception {
    TreeMap<Integer, String> names = new TreeMap<>();
    JSONArray parts = record.getJSONArray("parts");
    for (int i = 0; i < parts.length(); i++) {
      JSONObject p = parts.getJSONObject(i).getJSONObject("values");
      names.put(p.getInt("seq"), p.getString("cl"));
    }
    StringBuilder smil = new StringBuilder("<smil><head/><body>");
    for (String name : names.values())
      smil.append("<par dur=\"5000ms\"><img src=\"").append(name).append("\"/></par>");
    return smil.append("</body></smil>").toString();
  }

  JSONObject plan() throws Exception {
    validateTarget();
    JSONObject plan = new JSONObject();
    int conflicts = 0, mmsConflicts = 0;
    Set<String> assigned = new HashSet<>(), sourceMmsIds = new HashSet<>();
    for (int i = 0; i < records.get("mms").length(); i++)
      sourceMmsIds.add(
          records.get("mms").getJSONObject(i).getJSONObject("values").getString("m_id"));
    for (String kind : PackageData.KINDS) {
      JSONArray rows = records.get(kind);
      int existing = 0, resuming = 0;
      for (int i = 0; i < rows.length(); i++) {
        JSONObject r = rows.getJSONObject(i), v = r.getJSONObject("values");
        JSONArray candidates = candidates(kind, v);
        String chosen = null, resumeUri = null;
        JSONObject owned = operation(r.getString("source_id"));
        String preferred = owned == null ? null : owned.optString("uri", null);
        for (int j = 0; j < candidates.length(); j++) {
          JSONObject old = candidates.getJSONObject(j);
          if (!sameSim(kind, v, old)) {
            conflicts++;
            continue;
          }
          if (kind.equals("mms") && !mmsComplete(r, old)) {
            String uri = base(kind) + "/" + old.getLong("_id");
            if (v.getString("m_id").equals(old.optString("m_id"))) {
              PackageData.require(
                  owned == null || !"complete".equals(owned.optString("state")),
                  "completed_record_changed");
              PackageData.require(
                  owned != null
                      && context.getPackageName().equals(old.optString("creator"))
                      && (uri.equals(preferred)
                          || old.getLong("_id") > owned.optLong("before_max_id", Long.MAX_VALUE)),
                  "existing_incomplete_mms_conflict");
              PackageData.require(resumeUri == null, "ambiguous_mms_identity");
              resumeUri = uri;
            } else if (!sourceMmsIds.contains(old.optString("m_id"))) mmsConflicts++;
            continue;
          }
          String uri = base(kind) + "/" + old.getLong("_id");
          if (!assigned.contains(uri) && (chosen == null || uri.equals(preferred))) chosen = uri;
        }
        PackageData.require(chosen == null || resumeUri == null, "ambiguous_mms_identity");
        if (chosen != null) {
          assigned.add(chosen);
          existing++;
        } else if (resumeUri != null) {
          PackageData.require(assigned.add(resumeUri), "ambiguous_mms_identity");
          resuming++;
        }
      }
      plan.put(
          kind,
          new JSONObject()
              .put("source", rows.length())
              .put("already_exists", existing)
              .put("resume_incomplete", resuming)
              .put("insert", rows.length() - existing - resuming));
    }
    plan.put("cross_sim_conflicts", conflicts)
        .put("mms_identity_conflicts", mmsConflicts)
        .put("partial_source", input.partial());
    LocalFiles.save(new File(runDir, "plan.json"), plan);
    return plan;
  }

  JSONObject operation(String id) throws Exception {
    File file = operationFile(id);
    if (!file.exists()) return null;
    JSONObject op = LocalFiles.json(file);
    PackageData.require(id.equals(op.optString("source_id")), "journal_identity_mismatch");
    return op;
  }

  File operationFile(String id) {
    PackageData.require(id.matches("[a-f0-9]{64}"), "invalid_source_id");
    return new File(runDir, "operations/" + id.substring(0, 2) + "/" + id + ".json");
  }

  void saveOperation(JSONObject operation) throws Exception {
    File file = operationFile(operation.getString("source_id"));
    file.getParentFile().mkdirs();
    LocalFiles.save(file, operation);
  }

  void fillMms(JSONObject record, String uri) throws Exception {
    JSONArray expected = record.getJSONArray("addresses"), actual = query(uri + "/addr", null);
    Set<String> present = new HashSet<>();
    for (int i = 0; i < actual.length(); i++) {
      JSONObject a = actual.getJSONObject(i);
      present.add(a.getInt("type") + ":" + a.getString("address"));
    }
    for (int i = 0; i < expected.length(); i++) {
      JSONObject a = expected.getJSONObject(i);
      if (!present.contains(a.getInt("type") + ":" + a.getString("address")))
        insert(uri + "/addr", a);
    }
    JSONArray parts = record.getJSONArray("parts");
    for (int i = 0; i < parts.length(); i++) {
      JSONObject p = parts.getJSONObject(i), v = p.getJSONObject("values");
      JSONArray found =
          query(
              uri + "/part",
              "seq=? AND ct=? AND cl=?",
              v.getString("seq"),
              v.getString("ct"),
              v.getString("cl"));
      PackageData.require(found.length() <= 1, "ambiguous_partial_mms");
      String partUri =
          found.length() == 1
              ? "content://mms/part/" + found.getJSONObject(0).getLong("_id")
              : insert(uri + "/part", v);
      byte[] expectedBytes = Base64.decode(p.getString("data"), Base64.NO_WRAP);
      boolean correct = false;
      try {
        correct = LocalFiles.sha(binary(partUri)).equals(p.getString("sha256"));
      } catch (IOException ignored) {
      }
      if (!correct) {
        guard();
        try (OutputStream out = resolver.openOutputStream(Uri.parse(partUri), "wt")) {
          if (out == null) throw new IOException("attachment_write_denied");
          out.write(expectedBytes);
        }
        PackageData.require(
            LocalFiles.sha(binary(partUri)).equals(p.getString("sha256")),
            "attachment_verification_failed");
      }
    }
    JSONArray smilParts = query(uri + "/part", "ct=?", "application/smil");
    PackageData.require(smilParts.length() <= 1, "ambiguous_partial_mms");
    JSONObject smilValues =
        new JSONObject()
            .put("seq", -1)
            .put("ct", "application/smil")
            .put("cl", "smil.xml")
            .put("text", expectedSmil(record));
    if (smilParts.length() == 0) insert(uri + "/part", smilValues);
    else
      PackageData.require(
          resolver.update(
                  Uri.parse("content://mms/part/" + smilParts.getJSONObject(0).getLong("_id")),
                  contentValues(smilValues),
                  null,
                  null)
              == 1,
          "smil_finalize_failed");
    JSONObject v = record.getJSONObject("values");
    ContentValues notify = new ContentValues();
    notify.put("date", v.getLong("date"));
    PackageData.require(
        resolver.update(Uri.parse(uri), notify, null, null) == 1, "mms_finalize_failed");
  }

  void verify(String kind, JSONObject record, String uri) throws Exception {
    JSONArray found = query(uri, null);
    PackageData.require(found.length() == 1, "record_missing_after_write");
    JSONObject actual = found.getJSONObject(0), expected = record.getJSONObject("values");
    if (kind.equals("mms"))
      PackageData.require(mmsComplete(record, actual), "mms_verification_failed");
    Iterator<String> fields = expected.keys();
    while (fields.hasNext()) {
      String f = fields.next();
      Object a = actual.opt(f), e = expected.opt(f);
      if (f.equals("sub_id") && expected.getInt(f) < 0) continue;
      boolean equal =
          Objects.equals(a, e)
              || (a != null && e != null && String.valueOf(a).equals(String.valueOf(e)));
      if (f.equals("body")) equal = body(String.valueOf(a)).equals(body(String.valueOf(e)));
      PackageData.require(equal, "record_field_verification_failed:" + f);
    }
  }

  JSONObject commit(boolean allowConflicts) throws Exception {
    guard();
    validateTarget();
    backup();
    JSONObject fresh = plan();
    PackageData.require(
        allowConflicts
            || (fresh.getInt("cross_sim_conflicts") == 0
                && fresh.optInt("mms_identity_conflicts") == 0),
        "duplicate_conflict_confirmation_required");
    int total = 0, done = 0;
    for (JSONArray rows : records.values()) total += rows.length();
    Set<String> used = new HashSet<>();
    for (String kind : PackageData.KINDS)
      for (int i = 0; i < records.get(kind).length(); i++) {
        guard();
        JSONObject record = records.get(kind).getJSONObject(i),
            values = record.getJSONObject("values");
        String id = record.getString("source_id"), uri = null;
        JSONObject op = operation(id);
        JSONArray possible = candidates(kind, values);
        if (op != null && op.has("uri")) uri = op.getString("uri");
        if (uri == null) {
          for (int j = 0; j < possible.length(); j++) {
            JSONObject old = possible.getJSONObject(j);
            String candidate = base(kind) + "/" + old.getLong("_id");
            if (used.contains(candidate) || !sameSim(kind, values, old)) continue;
            if (kind.equals("mms") && !mmsComplete(record, old)) continue;
            uri = candidate;
            break;
          }
        }
        if (uri == null && op != null) {
          long previousMax = op.getLong("before_max_id");
          List<String> recovered = new ArrayList<>();
          for (int j = 0; j < possible.length(); j++) {
            JSONObject row = possible.getJSONObject(j);
            if (row.getLong("_id") > previousMax
                && context.getPackageName().equals(row.optString("creator"))
                && sameSim(kind, values, row)) recovered.add(base(kind) + "/" + row.getLong("_id"));
          }
          PackageData.require(recovered.size() <= 1, "uncertain_interrupted_insert");
          if (recovered.size() == 1) uri = recovered.get(0);
        }
        boolean inserted = uri == null;
        if (inserted) {
          if (op == null) {
            long previousMax = -1;
            for (int j = 0; j < possible.length(); j++)
              previousMax = Math.max(previousMax, possible.getJSONObject(j).getLong("_id"));
            op =
                new JSONObject()
                    .put("source_id", id)
                    .put("kind", kind)
                    .put("before_max_id", previousMax)
                    .put("state", "prepared");
            saveOperation(op);
          }
          JSONObject v = new JSONObject(values.toString());
          if (kind.equals("mms")) {
            String sender = "";
            JSONArray addresses = record.getJSONArray("addresses");
            for (int j = 0; j < addresses.length(); j++)
              if (addresses.getJSONObject(j).getInt("type") == 137)
                sender = addresses.getJSONObject(j).getString("address");
            JSONArray thread =
                query(
                    Uri.parse("content://mms-sms/threadID")
                        .buildUpon()
                        .appendQueryParameter("recipient", sender)
                        .build()
                        .toString(),
                    null);
            PackageData.require(thread.length() == 1, "mms_thread_failed");
            v.put("thread_id", thread.getJSONObject(0).getLong("_id"));
          }
          uri = insert(base(kind), v);
          op.put("uri", uri).put("state", "inserted");
          saveOperation(op);
        }
        if (op != null) {
          if (!op.has("uri")) {
            op.put("uri", uri);
            saveOperation(op);
          }
          if (kind.equals("mms") && !mmsComplete(record, query(uri, null).getJSONObject(0))) {
            PackageData.require(
                !"complete".equals(op.optString("state")), "completed_record_changed");
            JSONObject owned = query(uri, null).getJSONObject(0);
            PackageData.require(
                context.getPackageName().equals(owned.optString("creator"))
                    && values.getString("m_id").equals(owned.optString("m_id"))
                    && values.getLong("date") == owned.optLong("date")
                    && sameSim(kind, values, owned),
                "cannot_repair_unowned_mms");
            fillMms(record, uri);
          }
          verify(kind, record, uri);
          op.put("state", "complete");
          saveOperation(op);
        }
        used.add(uri);
        progress.update("importing", ++done, total);
      }
    journal.put("completed", true);
    saveJournal();
    return fresh.put("verified", true).put("completed_records", done);
  }
}
