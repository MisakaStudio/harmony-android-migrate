package dev.hmos.migrate;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.*;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.json.*;

/** Explicitly emulator-only, synthetic Provider integration tests; excluded from normal APKs. */
public class SmokeTests extends Instrumentation {
  Bundle arguments;
  Context context;
  JSONObject template;
  JSONArray passed = new JSONArray();
  String nonce = UUID.randomUUID().toString();
  int sequence;
  MigrationEngine roundTripEngine;

  @Override
  public void onCreate(Bundle args) {
    super.onCreate(args);
    arguments = args;
    start();
  }

  void check(boolean value, String message) {
    if (!value) throw new AssertionError(message);
  }

  JSONObject empty() {
    try {
      return new JSONObject()
          .put("sms", new JSONArray())
          .put("calls", new JSONArray())
          .put("mms", new JSONArray());
    } catch (Exception e) {
      throw new RuntimeException(e);
    }
  }

  JSONObject cloneRecord(String kind, int offset) throws Exception {
    JSONObject r =
        new JSONObject(
            template.getJSONObject("records").getJSONArray(kind).getJSONObject(0).toString());
    String id =
        LocalFiles.sha((nonce + ":" + kind + ":" + (sequence++)).getBytes(StandardCharsets.UTF_8));
    r.put("source_id", id);
    JSONObject v = r.getJSONObject("values");
    v.put("date", v.getLong("date") + offset);
    if (kind.equals("mms"))
      v.put("m_id", "hmos-" + id.substring(0, 32)).put("tr_id", "hmos-" + id.substring(0, 32));
    return r;
  }

  MigrationEngine engine(String label, JSONObject records) throws Exception {
    File folder = new File(context.getFilesDir(), "smoke/" + nonce + "/" + label);
    folder.mkdirs();
    JSONObject document = new JSONObject(template.toString()).put("records", records);
    File file = new File(folder, "input.json");
    LocalFiles.save(file, document);
    PackageData input = new PackageData(file);
    JSONObject slots = new JSONObject();
    for (Integer slot : input.sourceSlots.keySet())
      slots.put(String.valueOf(slot), new JSONObject().put("unassigned", true));
    JSONObject mapping =
        new JSONObject()
            .put("schema", "hmos-migrate/sim-map")
            .put("version", 1)
            .put("slots", slots);
    return new MigrationEngine(context, input, mapping, folder, (s, d, t) -> {});
  }

  void done(String name) {
    passed.put(name);
    Bundle status = new Bundle();
    status.putString("test", name);
    sendStatus(0, status);
  }

  void roundTrip() throws Exception {
    JSONObject rows = empty();
    JSONObject sms = cloneRecord("sms", 1000), duplicate = new JSONObject(sms.toString());
    duplicate.put(
        "source_id", LocalFiles.sha((nonce + "duplicate").getBytes(StandardCharsets.UTF_8)));
    JSONObject otp = cloneRecord("sms", 1100);
    otp.getJSONObject("values")
        .put("body", "Your verification code is 123456. Synthetic test only.");
    rows.getJSONArray("sms").put(sms).put(duplicate).put(otp);
    rows.getJSONArray("calls").put(cloneRecord("calls", 1000));
    rows.getJSONArray("mms").put(cloneRecord("mms", 1000));
    MigrationEngine e = engine("roundtrip", rows);
    e.guard();
    e.backup();
    JSONObject plan = e.plan();
    check(plan.getJSONObject("sms").getInt("insert") == 3, "source duplicates collapsed");
    e.commit(false);
    JSONObject retry = e.plan();
    for (String k : PackageData.KINDS)
      check(retry.getJSONObject(k).getInt("insert") == 0, "retry would insert " + k);
    check(
        e.query(
                    "content://sms",
                    "date=? AND address=?",
                    sms.getJSONObject("values").getString("date"),
                    sms.getJSONObject("values").getString("address"))
                .length()
            == 2,
        "SMS multiplicity lost");
    e.commit(false);
    roundTripEngine = e;
    done("provider_roundtrip_and_idempotence");
  }

  void smilFormatting() throws Exception {
    MigrationEngine e = roundTripEngine;
    JSONObject record = e.records.get("mms").getJSONObject(0);
    String uri = e.operation(record.getString("source_id")).getString("uri");
    JSONObject part = e.query(uri + "/part", "ct=?", "application/smil").getJSONObject(0);
    String original = part.getString("text");
    ContentValues changed = new ContentValues();
    changed.put("text", original.replace("/>", " />"));
    context
        .getContentResolver()
        .update(Uri.parse("content://mms/part/" + part.getLong("_id")), changed, null, null);
    check(e.plan().getJSONObject("mms").getInt("insert") == 0, "equivalent SMIL rejected");
    changed.put("text", original.replace("image_0.png", "unexpected.png"));
    context
        .getContentResolver()
        .update(Uri.parse("content://mms/part/" + part.getLong("_id")), changed, null, null);
    boolean blocked = false;
    try {
      e.plan();
    } catch (IllegalArgumentException expected) {
      blocked = true;
    }
    check(blocked, "external change to completed MMS not blocked");
    changed.put("text", original);
    context
        .getContentResolver()
        .update(Uri.parse("content://mms/part/" + part.getLong("_id")), changed, null, null);
    done("smil_semantics_and_completed_change_guard");
  }

  void bomVariants() throws Exception {
    JSONObject rows = empty();
    JSONObject plain = cloneRecord("sms", 2000), bom = cloneRecord("sms", 2000);
    String body = "synthetic-bom-" + nonce;
    plain.getJSONObject("values").put("body", body);
    bom.getJSONObject("values").put("body", "\uFEFF" + body);
    rows.getJSONArray("sms").put(plain).put(bom);
    MigrationEngine e = engine("bom", rows);
    e.insert("content://sms", plain.getJSONObject("values"));
    e.insert("content://sms", bom.getJSONObject("values"));
    JSONObject plan = e.plan();
    check(
        plan.getJSONObject("sms").getInt("already_exists") == 2, "BOM variant matching incomplete");
    check(plan.getJSONObject("sms").getInt("insert") == 0, "BOM variant duplicate insertion");
    e.backup();
    e.commit(false);
    done("symmetric_bom_multiplicity");
  }

  void mmsIdentity() throws Exception {
    JSONObject a = cloneRecord("mms", 3000), b = cloneRecord("mms", 3000);
    JSONObject first = empty();
    first.getJSONArray("mms").put(b);
    MigrationEngine seed = engine("mms-seed", first);
    seed.backup();
    seed.commit(false);
    JSONObject both = empty();
    both.getJSONArray("mms").put(a).put(b);
    MigrationEngine e = engine("mms-identity", both);
    e.backup();
    JSONObject plan = e.plan();
    check(
        plan.getJSONObject("mms").getInt("already_exists") == 1, "different MMS identities merged");
    check(plan.getJSONObject("mms").getInt("insert") == 1, "MMS plan incorrect");
    e.commit(false);
    check(
        e.query("content://mms", "m_id=?", a.getJSONObject("values").getString("m_id")).length()
            == 1,
        "MMS A missing");
    check(
        e.query("content://mms", "m_id=?", b.getJSONObject("values").getString("m_id")).length()
            == 1,
        "MMS B duplicated");
    done("mms_identity_allocation");
  }

  void recoverParentAndSmil() throws Exception {
    JSONObject rows = empty(), mms = cloneRecord("mms", 4000);
    rows.getJSONArray("mms").put(mms);
    MigrationEngine e = engine("mms-recovery", rows);
    e.backup();
    JSONObject values =
        new JSONObject(e.records.get("mms").getJSONObject(0).getJSONObject("values").toString());
    String sender = mms.getJSONArray("addresses").getJSONObject(0).getString("address");
    JSONArray thread =
        e.query(
            Uri.parse("content://mms-sms/threadID")
                .buildUpon()
                .appendQueryParameter("recipient", sender)
                .build()
                .toString(),
            null);
    values.put("thread_id", thread.getJSONObject(0).getLong("_id"));
    JSONObject op =
        new JSONObject()
            .put("source_id", mms.getString("source_id"))
            .put("kind", "mms")
            .put("before_max_id", -1)
            .put("state", "prepared");
    e.saveOperation(op);
    String parent =
        e.insert("content://mms", values); // Simulate process death before the URI is journaled.
    MigrationEngine recovered =
        new MigrationEngine(context, e.input, e.mapping, e.runDir, (s, d, t) -> {});
    check(
        recovered.plan().getJSONObject("mms").getInt("resume_incomplete") == 1,
        "partial parent not identified");
    recovered.commit(false);
    check(
        recovered.query("content://mms", "m_id=?", values.getString("m_id")).length() == 1,
        "partial MMS parent duplicated");
    JSONArray parts = recovered.query(parent + "/part", "ct=?", "application/smil");
    check(parts.length() == 1, "SMIL not created");
    context
        .getContentResolver()
        .delete(
            Uri.parse("content://mms/part/" + parts.getJSONObject(0).getLong("_id")),
            null,
            null); // Synthetic test-owned part only.
    recovered.saveOperation(
        recovered.operation(mms.getString("source_id")).put("state", "inserted"));
    MigrationEngine noSmil =
        new MigrationEngine(context, e.input, e.mapping, e.runDir, (s, d, t) -> {});
    check(
        noSmil.plan().getJSONObject("mms").getInt("resume_incomplete") == 1,
        "missing SMIL treated as complete");
    noSmil.commit(false);
    check(
        noSmil.query(parent + "/part", "ct=?", "application/smil").length() == 1,
        "SMIL not repaired");
    done("mms_crash_and_missing_smil_recovery");
  }

  void mappingBinding() throws Exception {
    JSONObject rows = empty();
    rows.getJSONArray("calls").put(cloneRecord("calls", 5000));
    MigrationEngine e = engine("mapping", rows);
    e.backup();
    JSONObject changed = new JSONObject(e.mapping.toString()).put("unexpected_change", true);
    boolean rejected = false;
    try {
      new MigrationEngine(context, e.input, changed, e.runDir, (s, d, t) -> {});
    } catch (IllegalArgumentException expected) {
      rejected = true;
    }
    check(rejected, "journal accepted changed mapping");
    done("journal_mapping_binding");
  }

  void incrementalJournal() throws Exception {
    JSONObject rows = empty();
    rows.getJSONArray("calls").put(cloneRecord("calls", 7000));
    MigrationEngine e = engine("incremental-journal", rows);
    e.backup();
    String last = null;
    for (int i = 0; i < 128; i++) {
      last = LocalFiles.sha((nonce + "op" + i).getBytes(StandardCharsets.UTF_8));
      e.saveOperation(
          new JSONObject()
              .put("source_id", last)
              .put("state", "prepared")
              .put("kind", "calls")
              .put("before_max_id", -1));
    }
    check(
        new File(e.runDir, "journal.json").length() < 2048,
        "journal header grows with operation history");
    check(e.operation(last) != null, "operation lookup failed");
    new android.util.AtomicFile(new File(e.runDir, "journal.json")).delete();
    boolean rejected = false;
    try {
      new MigrationEngine(context, e.input, e.mapping, e.runDir, (s, d, t) -> {});
    } catch (IllegalArgumentException expected) {
      rejected = true;
    }
    check(rejected, "missing binding header accepted");
    done("incremental_journal_and_header_guard");
  }

  void atomicStatus() throws Exception {
    File file = new File(context.getFilesDir(), "smoke-atomic-status.json");
    LocalFiles.save(file, new JSONObject().put("sequence", -1));
    java.util.concurrent.atomic.AtomicBoolean finished =
        new java.util.concurrent.atomic.AtomicBoolean(false);
    AtomicReference<Throwable> failure = new AtomicReference<>();
    Thread writer =
        new Thread(
            () -> {
              try {
                for (int i = 0; i < 200; i++)
                  LocalFiles.save(file, new JSONObject().put("sequence", i));
              } catch (Throwable e) {
                failure.set(e);
              } finally {
                finished.set(true);
              }
            });
    Thread reader =
        new Thread(
            () -> {
              try {
                while (!finished.get()) LocalFiles.json(file).getInt("sequence");
              } catch (Throwable e) {
                failure.set(e);
              }
            });
    writer.start();
    reader.start();
    writer.join();
    reader.join();
    check(failure.get() == null, "concurrent atomic status access failed");
    check(LocalFiles.json(file).getInt("sequence") == 199, "status updates lost");
    done("atomic_status_reader_writer");
  }

  void profile() throws Exception {
    Bundle info = new Bundle();
    info.putString("context_package", context.getPackageName());
    info.putString("default_sms", android.provider.Telephony.Sms.getDefaultSmsPackage(context));
    info.putString(
        "role_held",
        String.valueOf(
            context
                .getSystemService(android.app.role.RoleManager.class)
                .isRoleHeld(android.app.role.RoleManager.ROLE_SMS)));
    sendStatus(0, info);
    JSONObject result = DeviceProfile.collect(context, "synthetic-probe");
    check("hmos-migrate/target".equals(result.getString("schema")), "profile schema failed");
    check(result.has("subscriptions") && result.has("phone_accounts"), "profile arrays missing");
    check(
        result.getJSONArray("subscriptions").length() > 0
            && result.getJSONArray("phone_accounts").length() > 0,
        "emulator_SIM_profile_incomplete");
    done("public_api_device_profile");
  }

  void activityLifecycle() throws Exception {
    check(
        !MigrationService.configFile(context).exists(),
        "previous_active_UI_batch_requires_cleanup");
    JSONObject rows = empty();
    rows.getJSONArray("sms").put(cloneRecord("sms", 6000));
    JSONObject document = new JSONObject(template.toString()).put("records", rows);
    String incoming = "incoming-" + UUID.randomUUID().toString().replace("-", "") + ".json";
    LocalFiles.save(new File(context.getFilesDir(), incoming), document);
    Intent launch =
        new Intent(context, MainActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra("load_usb", incoming);
    MainActivity first = (MainActivity) startActivitySync(launch);
    waitForIdleSync();
    check(first.data != null, "USB file was not loaded");
    String inputName = first.inputName;
    runOnMainSync(
        () -> {
          for (MainActivity.SlotChoice c : first.choices.values()) c.unassigned.setChecked(true);
          first.confirmMapping.setChecked(true);
          first.persistDraft();
        });
    AtomicReference<MainActivity> current = new AtomicReference<>();
    CountDownLatch created = new CountDownLatch(1);
    Application app = (Application) context.getApplicationContext();
    Application.ActivityLifecycleCallbacks callback =
        new Application.ActivityLifecycleCallbacks() {
          public void onActivityCreated(Activity a, Bundle b) {
            if (a instanceof MainActivity && a != first) {
              current.set((MainActivity) a);
              created.countDown();
            }
          }

          public void onActivityStarted(Activity a) {}

          public void onActivityResumed(Activity a) {}

          public void onActivityPaused(Activity a) {}

          public void onActivityStopped(Activity a) {}

          public void onActivitySaveInstanceState(Activity a, Bundle b) {}

          public void onActivityDestroyed(Activity a) {}
        };
    app.registerActivityLifecycleCallbacks(callback);
    runOnMainSync(first::recreate);
    check(created.await(10, TimeUnit.SECONDS), "Activity was not recreated");
    waitForIdleSync();
    MainActivity second = current.get();
    check(
        second.data != null && inputName.equals(second.inputName),
        "input selection lost on recreation");
    check(second.confirmMapping.isChecked(), "draft confirmation lost without mapping change");
    runOnMainSync(second::refreshProfile);
    check(!second.confirmMapping.isChecked(), "refresh did not clear mapping confirmation");
    for (MainActivity.SlotChoice c : second.choices.values())
      check(c.unassigned.isChecked(), "refresh overwrote selected mapping");
    JSONObject slots = new JSONObject().put("0", new JSONObject().put("unassigned", true));
    String id = LocalFiles.sha((nonce + "UI").getBytes(StandardCharsets.UTF_8));
    JSONObject config =
        new JSONObject()
            .put("run_id", id)
            .put("input_name", second.inputName)
            .put("input_hash", second.data.inputHash)
            .put("mapping", new JSONObject().put("slots", slots))
            .put("allow_partial", false)
            .put("original_sms", "com.android.messaging");
    LocalFiles.save(MigrationService.configFile(context), config);
    runOnMainSync(second::renderMapping);
    check(
        second.choices.isEmpty()
            && second.mappings.getChildCount() > 0
            && !second.confirmMapping.isEnabled(),
        "active mapping is not visibly frozen");
    runOnMainSync(second::finish);
    waitForIdleSync();
    app.unregisterActivityLifecycleCallbacks(callback);
    new android.util.AtomicFile(MigrationService.configFile(context)).delete();
    new android.util.AtomicFile(new File(context.getFilesDir(), "draft.json")).delete();
    done("activity_recreation_refresh_and_frozen_mapping");
  }

  @Override
  public void onStart() {
    Bundle result = new Bundle();
    try {
      check(
          "ranchu".equals(Build.HARDWARE) && Build.PRODUCT.toLowerCase(Locale.ROOT).contains("sdk"),
          "EMULATOR_ONLY");
      check(
          arguments != null && "synthetic-only".equals(arguments.getString("mode")),
          "explicit_test_mode_required");
      context = getTargetContext();
      template = LocalFiles.json(new File(context.getFilesDir(), "smoke-input.json"));
      profile();
      roundTrip();
      smilFormatting();
      bomVariants();
      mmsIdentity();
      recoverParentAndSmil();
      mappingBinding();
      incrementalJournal();
      atomicStatus();
      activityLifecycle();
      JSONObject report =
          new JSONObject().put("passed", passed).put("sdk", Build.VERSION.SDK_INT).put("ok", true);
      LocalFiles.save(new File(context.getFilesDir(), "smoke-result.json"), report);
      result.putString("ok", "true");
      result.putString("report", report.toString());
      finish(Activity.RESULT_OK, result);
    } catch (Throwable e) {
      result.putString("ok", "false");
      result.putString(
          "error", e.getClass().getSimpleName() + ":" + String.valueOf(e.getMessage()));
      StringBuilder trace = new StringBuilder();
      for (StackTraceElement frame : e.getStackTrace())
        if (frame.getClassName().startsWith("dev.hmos.migrate")) trace.append(frame).append('\n');
      result.putString("trace", trace.toString());
      finish(Activity.RESULT_CANCELED, result);
    }
  }
}
