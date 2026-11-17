package dev.hmos.migrate;

import android.Manifest;
import android.app.*;
import android.app.role.RoleManager;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.*;
import android.provider.Settings;
import android.provider.Telephony;
import android.view.*;
import android.widget.*;
import java.io.*;
import java.nio.channels.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.*;

public class MainActivity extends Activity {
  static final AtomicBoolean EXPORTING = new AtomicBoolean(false);
  static final int PICK = 10,
      PROFILE_EXPORT = 11,
      BACKUP_EXPORT = 12,
      PHONE_PERMISSION = 20,
      DATA_PERMISSION = 21,
      SMS_ROLE = 22;
  LinearLayout layout, mappings;
  TextView summary, status;
  CheckBox acceptPartial, acceptConflicts, confirmMapping, acceptMerge;
  Button prepare, commit, restore, export;
  PackageData data;
  String inputName, probeNonce = "manual";
  JSONObject profile, draftMapping;
  String exportRunId, lastRoleState;
  final Map<Integer, SlotChoice> choices = new TreeMap<>();
  Handler handler = new Handler();
  final Runnable refresh =
      new Runnable() {
        public void run() {
          refreshStatus();
          handler.postDelayed(this, 1000);
        }
      };

  class SlotChoice {
    CheckBox unassigned;
    Spinner subscriptions, accounts;
    ArrayList<JSONObject> subs = new ArrayList<>(), phones = new ArrayList<>();
  }

  int dp(int v) {
    return (int) (v * getResources().getDisplayMetrics().density);
  }

  void text(String value, int size) {
    TextView view = new TextView(this);
    view.setText(value);
    view.setTextSize(size);
    view.setPadding(0, dp(8), 0, dp(8));
    layout.addView(view);
  }

  Button button(String label, View.OnClickListener action) {
    Button b = new Button(this);
    b.setText(label);
    b.setAllCaps(false);
    layout.addView(b);
    b.setOnClickListener(action);
    return b;
  }

  @Override
  public void onCreate(Bundle state) {
    super.onCreate(state);
    ScrollView scroll = new ScrollView(this);
    layout = new LinearLayout(this);
    layout.setOrientation(LinearLayout.VERTICAL);
    layout.setPadding(dp(20), dp(28), dp(20), dp(30));
    scroll.addView(layout);
    setContentView(scroll);
    scroll.setOnApplyWindowInsetsListener(
        (view, insets) -> {
          android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
          view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
          WindowInsetsController controller = view.getWindowInsetsController();
          if (controller != null) {
            int appearance =
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                    | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            controller.setSystemBarsAppearance(appearance, appearance);
          }
          return insets;
        });
    text("鸿蒙 → Android 本地迁移", 24);
    text(usbEnabled() ? "USB 版 · 支持电脑传入和手动选文件" : "普通版 · 使用系统文件选择器导入", 14);
    text("先选迁移文件，再核对 SIM 和预检查。数据只在本机处理，不发送短信、不拨号。", 16);
    button(
        "选择迁移文件（migration.json）",
        v -> {
          if (busy()) return;
          Intent pick =
              new Intent(Intent.ACTION_OPEN_DOCUMENT)
                  .setType("*/*")
                  .addCategory(Intent.CATEGORY_OPENABLE);
          pick.putExtra(
              Intent.EXTRA_MIME_TYPES,
              new String[] {"application/json", "application/octet-stream", "text/plain"});
          startActivityForResult(pick, PICK);
        });
    button("读取/刷新目标 SIM 信息", v -> requestPhone());
    button(
        "导出目标 SIM 信息",
        v -> {
          if (profile == null) {
            requestPhone();
            return;
          }
          createDocument("application/json", "target-profile.json", PROFILE_EXPORT);
        });
    summary = new TextView(this);
    summary.setTextSize(16);
    layout.addView(summary);
    button(
        "查看源转换报告",
        v -> {
          if (data == null) {
            status.setText("请先选择迁移文件。");
            return;
          }
          try {
            JSONObject report = data.document.getJSONObject("conversion");
            JSONArray issues = report.optJSONArray("issues");
            TreeMap<String, Integer> counts = new TreeMap<>();
            if (issues != null)
              for (int i = 0; i < issues.length(); i++) {
                String reason = issues.getJSONObject(i).optString("reason");
                counts.put(reason, counts.getOrDefault(reason, 0) + 1);
              }
            StringBuilder text =
                new StringBuilder(report.optBoolean("complete") ? "源转换完整。" : "源转换不完整，以下记录未包含：");
            for (String reason : counts.keySet())
              text.append('\n').append(reason).append("：").append(counts.get(reason));
            text.append("\n详情在电脑端 report.json；这里不显示通信正文。");
            new AlertDialog.Builder(this)
                .setTitle("源转换报告")
                .setMessage(text.toString())
                .setPositiveButton("知道了", null)
                .show();
          } catch (Exception e) {
            showError(e);
          }
        });
    mappings = new LinearLayout(this);
    mappings.setOrientation(LinearLayout.VERTICAL);
    layout.addView(mappings);
    confirmMapping = new CheckBox(this);
    confirmMapping.setText("已核对源卡与目标卡的对应关系");
    layout.addView(confirmMapping);
    acceptMerge = new CheckBox(this);
    acceptMerge.setText("允许多张源卡合并到同一个目标订阅/账户");
    layout.addView(acceptMerge);
    acceptPartial = new CheckBox(this);
    acceptPartial.setText("接受源转换报告中明确列出的未包含记录");
    layout.addView(acceptPartial);
    acceptConflicts = new CheckBox(this);
    acceptConflicts.setText("接受跨卡/彩信身份不同的相似记录：保留原记录并新增");
    layout.addView(acceptConflicts);
    text("写入短信/彩信前，请开启飞行模式并关闭 Wi-Fi。导入器仅临时接管短信角色；完成后先恢复原短信应用，再恢复网络。", 15);
    button("打开飞行模式设置", v -> startActivity(new Intent(Settings.ACTION_AIRPLANE_MODE_SETTINGS)));
    button("打开 Wi-Fi 设置", v -> startActivity(new Intent(Settings.ACTION_WIFI_SETTINGS)));
    prepare = button("1. 备份目标数据并预检查", v -> prepare());
    commit = button("2. 确认写入 / 继续中断批次", v -> commit());
    prepare.setEnabled(false);
    commit.setEnabled(false);
    export = button("导出备份与报告", v -> chooseExport());
    restore = button("3. 恢复原默认短信应用", v -> restoreDefault());
    button("结束当前批次，选择其他文件", v -> clearActive());
    status = new TextView(this);
    status.setTextSize(16);
    status.setPadding(0, dp(12), 0, dp(12));
    layout.addView(status);
    getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    try {
      if (MigrationService.configFile(this).isFile()) {
        JSONObject config = currentConfig();
        inputName = config.getString("input_name");
        data = new PackageData(new File(getFilesDir(), inputName));
      } else if (new File(getFilesDir(), "draft.json").isFile()) {
        JSONObject draft = LocalFiles.json(new File(getFilesDir(), "draft.json"));
        inputName = draft.getString("input_name");
        data = new PackageData(new File(getFilesDir(), inputName));
        draftMapping = draft.optJSONObject("mapping");
        confirmMapping.setChecked(draft.optBoolean("confirmed"));
        acceptMerge.setChecked(draft.optBoolean("merge"));
        acceptPartial.setChecked(draft.optBoolean("partial"));
        acceptConflicts.setChecked(draft.optBoolean("conflicts"));
      }
      if (new File(getFilesDir(), "device-profile.json").exists())
        profile = LocalFiles.json(new File(getFilesDir(), "device-profile.json"));
    } catch (Exception e) {
      showError(e);
    }
    if (state != null) {
      exportRunId = state.getString("export_run");
      probeNonce = state.getString("probe_nonce", "manual");
    }
    if (state == null && !MigrationService.configFile(this).exists())
      confirmMapping.setChecked(false);
    handleIntent(getIntent());
    renderMapping();
  }

  @Override
  public void onNewIntent(Intent intent) {
    super.onNewIntent(intent);
    setIntent(intent);
    handleIntent(intent);
  }

  void handleIntent(Intent intent) {
    if (intent == null) return;
    if (Intent.ACTION_SENDTO.equals(intent.getAction())) {
      try {
        IncomingStore.save(
            this,
            intent,
            new JSONObject()
                .put("requires_followup", true)
                .put("reason", "send_request_not_executed"));
      } catch (Exception e) {
        showError(e);
      }
      status.setText("导入器不发送短信。请恢复原默认短信应用后发送，原请求已保留在本地报告中。");
      return;
    }
    if (!usbEnabled()
        && (intent.hasExtra("probe_only")
            || intent.hasExtra("probe_nonce")
            || intent.hasExtra("load_usb"))) {
      status.setText("普通版不接受 USB 调试入口，请手动选择迁移文件。需要电脑传入时，请在结束批次后安装同版本 USB 版。");
      return;
    }
    if (intent.hasExtra("probe_nonce")) probeNonce = intent.getStringExtra("probe_nonce");
    if (intent.getBooleanExtra("probe_only", false)) {
      if (!"manual".equals(probeNonce)
          && probeNonce.equals(getPreferences(MODE_PRIVATE).getString("requested_probe", "")))
        return;
      getPreferences(MODE_PRIVATE).edit().putString("requested_probe", probeNonce).apply();
      requestPhone();
      return;
    }
    String incoming = intent.getStringExtra("load_usb");
    if (incoming != null) {
      try {
        PackageData.require(incoming.matches("incoming-[a-f0-9]{32}\\.json"), "invalid_usb_input");
        if (incoming.equals(getPreferences(MODE_PRIVATE).getString("consumed_usb", ""))
            && !new File(getFilesDir(), incoming).exists()) return;
        if (busy()) return;
        loadBytes(LocalFiles.read(new File(getFilesDir(), incoming), LocalFiles.INPUT_LIMIT));
        PackageData.require(
            getPreferences(MODE_PRIVATE).edit().putString("consumed_usb", incoming).commit(),
            "draft_write_failed");
        intent.removeExtra("load_usb");
        new File(getFilesDir(), incoming).delete();
      } catch (Exception e) {
        showError(e);
      }
    }
  }

  boolean usbEnabled() {
    return (getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0;
  }

  @Override
  protected void onResume() {
    super.onResume();
    handler.post(refresh);
  }

  @Override
  protected void onPause() {
    persistDraft();
    handler.removeCallbacks(refresh);
    super.onPause();
  }

  @Override
  protected void onSaveInstanceState(Bundle state) {
    persistDraft();
    state.putString("export_run", exportRunId);
    state.putString("probe_nonce", probeNonce);
    super.onSaveInstanceState(state);
  }

  boolean busy() {
    if (MigrationService.RUNNING.get() || EXPORTING.get()) {
      status.setText("正在处理，请等待完成。");
      return true;
    }
    if (MigrationService.configFile(this).exists()) {
      status.setText("已有批次。请先完成/恢复默认应用，再结束当前批次。");
      return true;
    }
    return false;
  }

  JSONObject currentConfig() throws Exception {
    return LocalFiles.json(MigrationService.configFile(this));
  }

  void loadBytes(byte[] bytes) throws Exception {
    String hash = LocalFiles.sha(bytes), name = "input-" + hash + ".json";
    File target = new File(getFilesDir(), name);
    boolean created = !target.exists();
    if (created) LocalFiles.save(target, bytes);
    PackageData selected;
    try {
      PackageData.require(LocalFiles.sha(target).equals(hash), "stored_input_checksum_failed");
      selected = new PackageData(target);
    } catch (Exception e) {
      if (created) target.delete();
      throw e;
    }
    data = selected;
    inputName = name;
    draftMapping = null;
    confirmMapping.setChecked(false);
    acceptPartial.setChecked(false);
    renderMapping();
    persistDraft();
    status.setText("文件校验通过。请读取目标 SIM 并核对映射。");
  }

  void requestPhone() {
    if (checkSelfPermission(Manifest.permission.READ_PHONE_STATE)
        != PackageManager.PERMISSION_GRANTED)
      requestPermissions(new String[] {Manifest.permission.READ_PHONE_STATE}, PHONE_PERMISSION);
    else refreshProfile();
  }

  void refreshProfile() {
    try {
      if (data != null && !choices.isEmpty() && !MigrationService.configFile(this).exists())
        draftMapping = collectMapping(false);
      profile = DeviceProfile.collect(this, probeNonce);
      LocalFiles.save(new File(getFilesDir(), "device-profile.json"), profile);
      if (!MigrationService.configFile(this).exists()) confirmMapping.setChecked(false);
      renderMapping();
      persistDraft();
      status.setText("目标信息已刷新。已选卡按稳定 ID 保留，请重新核对；未能关联的账户需手动选择。");
    } catch (Exception e) {
      showError(e);
    }
  }

  String accountLabel(JSONObject a) {
    String id = a.optString("id");
    return a.optString("label", a.optString("component"))
        + " · ID "
        + (id.length() > 8 ? "…" + id.substring(id.length() - 4) : id);
  }

  void renderMapping() {
    if (summary == null) return;
    try {
      if (data == null) {
        for (CheckBox box :
            new CheckBox[] {confirmMapping, acceptMerge, acceptPartial, acceptConflicts})
          box.setVisibility(View.GONE);
        summary.setText("尚未选择迁移文件。SIM 探测可单独使用。");
        return;
      }
      summary.setText(
          "短信 "
              + data.count("sms")
              + " · 通话 "
              + data.count("calls")
              + " · 彩信 "
              + data.count("mms")
              + (data.partial() ? "\n源转换不完整，请阅读未包含记录清单。" : ""));
      acceptPartial.setVisibility(data.partial() ? View.VISIBLE : View.GONE);
      confirmMapping.setVisibility(View.VISIBLE);
      acceptConflicts.setVisibility(View.GONE);
      acceptMerge.setVisibility(data.sourceSlots.size() > 1 ? View.VISIBLE : View.GONE);
      mappings.removeAllViews();
      choices.clear();
      if (MigrationService.configFile(this).exists()) {
        JSONObject config = currentConfig(),
            slots = config.getJSONObject("mapping").getJSONObject("slots");
        for (Integer slot : data.sourceSlots.keySet()) {
          JSONObject entry = slots.getJSONObject(String.valueOf(slot));
          TextView view = new TextView(this);
          view.setTextSize(16);
          String detail =
              entry.optBoolean("unassigned")
                  ? "不保留卡归属"
                  : "订阅 "
                      + (entry.has("sub_id") ? entry.getInt("sub_id") : "未使用")
                      + (entry.has("phone_account")
                          ? "；账户 " + accountLabel(entry.getJSONObject("phone_account"))
                          : "");
          view.setText(
              "已冻结：源槽 "
                  + slot
                  + " → "
                  + detail
                  + (entry.has("call_extras") ? "；含通话标签扩展" : "")
                  + "\n");
          mappings.addView(view);
        }
        confirmMapping.setChecked(true);
        confirmMapping.setEnabled(false);
        acceptMerge.setEnabled(false);
        acceptPartial.setEnabled(false);
        acceptPartial.setChecked(config.optBoolean("allow_partial"));
        return;
      }
      confirmMapping.setEnabled(true);
      acceptMerge.setEnabled(true);
      acceptPartial.setEnabled(true);
      JSONObject suggested =
          draftMapping != null ? draftMapping : data.document.optJSONObject("sim_map");
      for (Integer slot : data.sourceSlots.keySet()) {
        TextView title = new TextView(this);
        title.setText(slot < 0 ? "来源：未知卡槽" : "来源：逻辑槽 " + slot + "（通常显示为卡 " + (slot + 1) + "）");
        title.setTextSize(18);
        mappings.addView(title);
        SlotChoice choice = new SlotChoice();
        choices.put(slot, choice);
        choice.unassigned = new CheckBox(this);
        choice.unassigned.setText("明确不保留这组记录的卡归属");
        mappings.addView(choice.unassigned);
        choice.subscriptions = new Spinner(this);
        choice.accounts = new Spinner(this);
        ArrayList<String> subLabels = new ArrayList<>(), accountLabels = new ArrayList<>();
        subLabels.add("请选择目标短信订阅");
        accountLabels.add("请选择目标通话账户");
        if (profile != null) {
          JSONArray subs = profile.getJSONArray("subscriptions"),
              accounts = profile.getJSONArray("phone_accounts");
          for (int i = 0; i < subs.length(); i++) {
            JSONObject sub = subs.getJSONObject(i);
            choice.subs.add(sub);
            subLabels.add(
                "逻辑槽 "
                    + sub.optInt("logical_slot_index", -1)
                    + " · "
                    + sub.optString("label", "SIM")
                    + " · 订阅 "
                    + sub.getInt("sub_id"));
          }
          for (int i = 0; i < accounts.length(); i++) {
            JSONObject a = accounts.getJSONObject(i);
            if (a.optBoolean("enabled", true) && a.optInt("user_id") == 0) {
              choice.phones.add(a);
              accountLabels.add(accountLabel(a));
            }
          }
        }
        choice.subscriptions.setAdapter(
            new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, subLabels));
        choice.accounts.setAdapter(
            new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, accountLabels));
        if (data.sourceSlots.get(slot).contains("sms")
            || data.sourceSlots.get(slot).contains("mms")) mappings.addView(choice.subscriptions);
        if (data.sourceSlots.get(slot).contains("calls")) mappings.addView(choice.accounts);
        JSONObject entry =
            suggested == null
                ? null
                : suggested.optJSONObject("slots") == null
                    ? null
                    : suggested.getJSONObject("slots").optJSONObject(String.valueOf(slot));
        if (entry != null) {
          choice.unassigned.setChecked(entry.optBoolean("unassigned"));
          for (int i = 0; i < choice.subs.size(); i++)
            if (choice.subs.get(i).getInt("sub_id") == entry.optInt("sub_id", -2))
              choice.subscriptions.setSelection(i + 1);
          JSONObject pa = entry.optJSONObject("phone_account");
          if (pa != null)
            for (int i = 0; i < choice.phones.size(); i++)
              if (pa.optString("id").equals(choice.phones.get(i).getString("id"))
                  && pa.optString("component").equals(choice.phones.get(i).getString("component"))
                  && pa.optInt("user_id") == choice.phones.get(i).getInt("user_id"))
                choice.accounts.setSelection(i + 1);
        }
        choice.subscriptions.setEnabled(!choice.unassigned.isChecked());
        choice.accounts.setEnabled(!choice.unassigned.isChecked());
        choice.unassigned.setOnCheckedChangeListener(
            (view, checked) -> {
              choice.subscriptions.setEnabled(!checked);
              choice.accounts.setEnabled(!checked);
              confirmMapping.setChecked(false);
            });
        mappingListener(choice.subscriptions);
        mappingListener(choice.accounts);
      }
    } catch (Exception e) {
      showError(e);
    }
  }

  void mappingListener(Spinner spinner) {
    final int[] last = {spinner.getSelectedItemPosition()};
    spinner.setOnItemSelectedListener(
        new AdapterView.OnItemSelectedListener() {
          public void onNothingSelected(AdapterView<?> view) {}

          public void onItemSelected(AdapterView<?> view, View selected, int position, long id) {
            if (position != last[0]) {
              confirmMapping.setChecked(false);
              last[0] = position;
            }
          }
        });
  }

  JSONObject mapping() throws Exception {
    return collectMapping(true);
  }

  JSONObject collectMapping(boolean complete) throws Exception {
    if (complete) PackageData.require(confirmMapping.isChecked(), "mapping_confirmation_required");
    JSONObject slots = new JSONObject();
    for (Integer slot : choices.keySet()) {
      SlotChoice choice = choices.get(slot);
      JSONObject item = new JSONObject();
      if (choice.unassigned.isChecked()) item.put("unassigned", true);
      else {
        if (data.sourceSlots.get(slot).contains("sms")
            || data.sourceSlots.get(slot).contains("mms")) {
          int index = choice.subscriptions.getSelectedItemPosition() - 1;
          if (complete) PackageData.require(index >= 0, "select_target_subscription");
          if (index >= 0) item.put("sub_id", choice.subs.get(index).getInt("sub_id"));
        }
        if (data.sourceSlots.get(slot).contains("calls")) {
          int index = choice.accounts.getSelectedItemPosition() - 1;
          if (complete) PackageData.require(index >= 0, "select_target_phone_account");
          if (index < 0) {
            slots.put(String.valueOf(slot), item);
            continue;
          }
          JSONObject a = choice.phones.get(index);
          item.put(
              "phone_account",
              new JSONObject()
                  .put("id", a.getString("id"))
                  .put("component", a.getString("component"))
                  .put("user_id", a.getInt("user_id")));
          if (a.has("call_extras")) item.put("call_extras", a.getJSONObject("call_extras"));
          else {
            JSONObject fileMap = data.document.optJSONObject("sim_map");
            JSONObject fileSlots = fileMap == null ? null : fileMap.optJSONObject("slots");
            JSONObject entry =
                fileSlots == null ? null : fileSlots.optJSONObject(String.valueOf(slot));
            if (entry != null && entry.has("call_extras") && entry.has("phone_account")) {
              JSONObject original = entry.getJSONObject("phone_account"),
                  current = item.getJSONObject("phone_account");
              if (original.optString("id").equals(current.getString("id"))
                  && original.optString("component").equals(current.getString("component"))
                  && original.optInt("user_id") == current.getInt("user_id"))
                item.put("call_extras", entry.getJSONObject("call_extras"));
            }
          }
        }
      }
      slots.put(String.valueOf(slot), item);
    }
    Set<Integer> subs = new HashSet<>();
    Set<String> accounts = new HashSet<>();
    boolean merge = false;
    Iterator<String> keys = slots.keys();
    while (keys.hasNext()) {
      JSONObject entry = slots.getJSONObject(keys.next());
      if (entry.has("sub_id")) merge |= !subs.add(entry.getInt("sub_id"));
      if (entry.has("phone_account")) {
        JSONObject a = entry.getJSONObject("phone_account");
        merge |=
            !accounts.add(
                a.getString("component") + "\n" + a.getString("id") + "\n" + a.getInt("user_id"));
      }
    }
    if (complete)
      PackageData.require(!merge || acceptMerge.isChecked(), "sim_merge_confirmation_required");
    return new JSONObject()
        .put("schema", "hmos-migrate/sim-map")
        .put("version", 1)
        .put("slots", slots)
        .put("allow_merge", acceptMerge.isChecked());
  }

  void persistDraft() {
    if (data == null
        || MigrationService.configFile(this).exists()
        || MigrationService.RUNNING.get()) return;
    try {
      draftMapping = collectMapping(false);
      LocalFiles.save(
          new File(getFilesDir(), "draft.json"),
          new JSONObject()
              .put("input_name", inputName)
              .put("mapping", draftMapping)
              .put("confirmed", confirmMapping.isChecked())
              .put("merge", acceptMerge.isChecked())
              .put("partial", acceptPartial.isChecked())
              .put("conflicts", acceptConflicts.isChecked()));
    } catch (Exception ignored) {
    }
  }

  boolean offline() throws Exception {
    if (!data.messages()) return true;
    WifiManager wifi = getSystemService(WifiManager.class);
    if (Settings.Global.getInt(getContentResolver(), Settings.Global.AIRPLANE_MODE_ON, 0) != 1
        || (wifi != null && wifi.isWifiEnabled())) {
      status.setText("请先开启飞行模式，并关闭 Wi-Fi，再临时切换短信应用。");
      return false;
    }
    return true;
  }

  void prepare() {
    try {
      if (data == null || MigrationService.RUNNING.get() || EXPORTING.get()) return;
      if (data.count("sms") + data.count("calls") + data.count("mms") == 0) {
        status.setText("文件没有可导入记录，无需写入。可查看源转换报告了解未支持项。");
        return;
      }
      PackageData.require(
          !data.partial() || acceptPartial.isChecked(), "partial_source_not_confirmed");
      if (!offline()) return;
      if (!MigrationService.configFile(this).exists()) {
        JSONObject map = mapping();
        String runId =
            LocalFiles.sha(
                (data.inputHash + map.toString() + UUID.randomUUID())
                    .getBytes(StandardCharsets.UTF_8));
        String original = Telephony.Sms.getDefaultSmsPackage(this);
        PackageData.require(
            !getSystemService(RoleManager.class).isRoleHeld(RoleManager.ROLE_SMS),
            "restore_old_sms_role_first");
        JSONObject config =
            new JSONObject()
                .put("run_id", runId)
                .put("input_name", inputName)
                .put("input_hash", data.inputHash)
                .put("mapping", map)
                .put("allow_partial", acceptPartial.isChecked())
                .put("allow_cross_sim", acceptConflicts.isChecked())
                .put("original_sms", original == null ? JSONObject.NULL : original);
        LocalFiles.save(MigrationService.configFile(this), config);
        LocalFiles.save(
            new File(MigrationService.runFolder(this, config), "run-config.json"), config);
        renderMapping();
      }
      if (data.messages()
          && !getSystemService(RoleManager.class).isRoleHeld(RoleManager.ROLE_SMS)) {
        JSONObject config = currentConfig();
        String original = Telephony.Sms.getDefaultSmsPackage(this);
        config.put("original_sms", original == null ? JSONObject.NULL : original);
        LocalFiles.save(MigrationService.configFile(this), config);
        LocalFiles.save(
            new File(MigrationService.runFolder(this, config), "run-config.json"), config);
        startActivityForResult(
            getSystemService(RoleManager.class).createRequestRoleIntent(RoleManager.ROLE_SMS),
            SMS_ROLE);
        return;
      }
      ArrayList<String> missing = new ArrayList<>();
      if (data.messages()
          && checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED)
        missing.add(Manifest.permission.READ_SMS);
      if (data.count("calls") > 0)
        for (String p :
            new String[] {Manifest.permission.READ_CALL_LOG, Manifest.permission.WRITE_CALL_LOG})
          if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) missing.add(p);
      if (!missing.isEmpty()) {
        requestPermissions(missing.toArray(new String[0]), DATA_PERMISSION);
        return;
      }
      enrichObservedCallFields();
      startForegroundService(new Intent(this, MigrationService.class).setAction("prepare"));
    } catch (Exception e) {
      showError(e);
    }
  }

  void enrichObservedCallFields() throws Exception {
    if (data.count("calls") == 0) return;
    JSONObject config = currentConfig();
    File folder = MigrationService.runFolder(this, config);
    if (new File(folder, "journal.json").exists()) return;
    JSONObject fresh = DeviceProfile.collect(this, "preflight");
    JSONArray accounts = fresh.getJSONArray("phone_accounts");
    JSONObject slots = config.getJSONObject("mapping").getJSONObject("slots");
    boolean changed = false;
    Iterator<String> keys = slots.keys();
    while (keys.hasNext()) {
      JSONObject entry = slots.getJSONObject(keys.next());
      JSONObject chosen = entry.optJSONObject("phone_account");
      if (chosen == null || entry.has("call_extras")) continue;
      for (int i = 0; i < accounts.length(); i++) {
        JSONObject a = accounts.getJSONObject(i);
        if (chosen.getString("id").equals(a.getString("id"))
            && chosen.getString("component").equals(a.getString("component"))
            && chosen.optInt("user_id") == a.getInt("user_id")
            && a.has("call_extras")) {
          entry
              .put("call_extras", a.getJSONObject("call_extras"))
              .put("call_extras_evidence", "observed_target_records");
          changed = true;
        }
      }
    }
    if (changed) {
      LocalFiles.save(MigrationService.configFile(this), config);
      LocalFiles.save(new File(folder, "run-config.json"), config);
      renderMapping();
    }
  }

  void commit() {
    try {
      if (MigrationService.RUNNING.get() || EXPORTING.get() || !offline()) return;
      JSONObject config = currentConfig();
      File folder = MigrationService.runFolder(this, config);
      JSONObject plan = LocalFiles.json(new File(folder, "plan.json"));
      PackageData.require(
          (plan.optInt("cross_sim_conflicts") == 0 && plan.optInt("mms_identity_conflicts") == 0)
              || acceptConflicts.isChecked(),
          "duplicate_conflict_confirmation_required");
      config.put("allow_cross_sim", acceptConflicts.isChecked());
      LocalFiles.save(MigrationService.configFile(this), config);
      new AlertDialog.Builder(this)
          .setTitle("确认追加导入")
          .setMessage("已备份目标数据。继续将按预检查清单追加记录，原有记录不会被清空。")
          .setNegativeButton("取消", null)
          .setPositiveButton(
              "开始",
              (d, w) ->
                  startForegroundService(
                      new Intent(this, MigrationService.class).setAction("commit")))
          .show();
    } catch (Exception e) {
      showError(e);
    }
  }

  void refreshStatus() {
    if (status == null) return;
    if (!MigrationService.configFile(this).exists()) {
      prepare.setEnabled(data != null && !MigrationService.RUNNING.get() && !EXPORTING.get());
      commit.setEnabled(false);
      if (getSystemService(RoleManager.class).isRoleHeld(RoleManager.ROLE_SMS))
        status.setText("导入器当前是默认短信应用。若不正在迁移，请先恢复原短信应用。");
      return;
    }
    try {
      prepare.setEnabled(!MigrationService.RUNNING.get() && !EXPORTING.get());
      JSONObject config = currentConfig();
      File folder = MigrationService.runFolder(this, config);
      File stateFile = new File(folder, "status.json");
      File planFile = new File(folder, "plan.json");
      if (planFile.isFile()) {
        JSONObject currentPlan = LocalFiles.json(planFile);
        acceptConflicts.setVisibility(
            currentPlan.optInt("cross_sim_conflicts") + currentPlan.optInt("mms_identity_conflicts")
                    > 0
                ? View.VISIBLE
                : View.GONE);
      }
      if (!stateFile.exists()) return;
      JSONObject state = LocalFiles.json(stateFile);
      String s = state.optString("state");
      String current = Telephony.Sms.getDefaultSmsPackage(this);
      boolean ours = getSystemService(RoleManager.class).isRoleHeld(RoleManager.ROLE_SMS);
      StringBuilder display = new StringBuilder();
      if (s.equals("prepared") || s.equals("imported")) {
        display.append(s.equals("prepared") ? "预检查完成\n" : "写入与校验完成\n");
        for (String k : PackageData.KINDS) {
          JSONObject row = state.optJSONObject(k);
          if (row != null)
            display
                .append(Messages.kind(k))
                .append("：源 ")
                .append(row.optInt("source"))
                .append("，已有 ")
                .append(row.optInt("already_exists"))
                .append("，续传 ")
                .append(row.optInt("resume_incomplete"))
                .append("，新增 ")
                .append(row.optInt("insert"))
                .append('\n');
        }
        display.append("跨卡相似记录：").append(state.optInt("cross_sim_conflicts")).append('\n');
        display.append("彩信身份冲突候选：").append(state.optInt("mms_identity_conflicts")).append('\n');
      } else if (s.equals("failed"))
        display
            .append("已停止：")
            .append(Messages.error(state.optString("error")))
            .append("\n备份与已提交日志仍保留，可排查后继续预检查。\n");
      else
        display
            .append(MigrationService.RUNNING.get() ? "处理中：" : "上次中断于：")
            .append(s)
            .append(' ')
            .append(state.optInt("completed"))
            .append('/')
            .append(state.optInt("total"))
            .append('\n');
      if (ours) display.append("结束前必须恢复原默认短信应用。");
      else if (s.equals("imported")) {
        boolean restored =
            !config.isNull("original_sms")
                && Objects.equals(current, config.optString("original_sms"));
        int pending = IncomingStore.pending(this);
        display.append(
            pending > 0
                ? "有待处理的新到消息/彩信通知，请先导出报告，不要卸载。"
                : restored ? "原短信角色已恢复，可恢复网络。" : "已离开导入器短信角色；请核对当前默认应用。");
        getSystemService(NotificationManager.class).cancel(MigrationService.NOTICE);
        String key = current + ":" + restored;
        if (!key.equals(lastRoleState) && !MigrationService.RUNNING.get() && !EXPORTING.get()) {
          LocalFiles.save(
              new File(folder, "role-state.json"),
              new JSONObject()
                  .put("sms_role_released", true)
                  .put("original_sms_restored", restored)
                  .put("checked_at", System.currentTimeMillis()));
          lastRoleState = key;
        }
      }
      status.setText(display.toString());
      commit.setEnabled(!MigrationService.RUNNING.get() && new File(folder, "plan.json").exists());
    } catch (Exception ignored) {
    }
  }

  void restoreDefault() {
    if (MigrationService.RUNNING.get()) {
      status.setText("请先等待当前操作结束。");
      return;
    }
    String label = "原来的短信应用";
    try {
      String original = currentConfig().optString("original_sms");
      label =
          String.valueOf(
              getPackageManager()
                  .getApplicationLabel(getPackageManager().getApplicationInfo(original, 0)));
    } catch (Exception ignored) {
    }
    new AlertDialog.Builder(this)
        .setTitle("恢复默认短信应用")
        .setMessage("请在系统设置中选择：" + label + "。恢复后再关闭飞行模式。")
        .setNegativeButton("取消", null)
        .setPositiveButton(
            "打开设置",
            (d, w) -> startActivity(new Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS)))
        .show();
  }

  void clearActive() {
    if (MigrationService.RUNNING.get() || EXPORTING.get()) {
      status.setText("正在处理，不能结束批次。");
      return;
    }
    if (IncomingStore.pending(this) > 0) {
      status.setText("存在待处理的新到消息/通知，请先导出备份和报告并处理，不要卸载。");
      return;
    }
    if (getSystemService(RoleManager.class).isRoleHeld(RoleManager.ROLE_SMS)) {
      status.setText("先恢复原默认短信应用，再结束批次。");
      return;
    }
    new AlertDialog.Builder(this)
        .setTitle("结束当前批次")
        .setMessage("备份和日志会保留。若批次尚未完成，建议先导出报告，不要盲目开启另一批导入。")
        .setNegativeButton("取消", null)
        .setPositiveButton(
            "结束",
            (d, w) -> {
              new android.util.AtomicFile(MigrationService.configFile(this)).delete();
              new android.util.AtomicFile(new File(getFilesDir(), "draft.json")).delete();
              data = null;
              inputName = null;
              draftMapping = null;
              mappings.removeAllViews();
              choices.clear();
              summary.setText("请选择迁移文件。");
              status.setText("备份与日志仍保留，可用导出按钮选择历史批次。");
            })
        .show();
  }

  void chooseExport() {
    if (MigrationService.RUNNING.get() || EXPORTING.get()) {
      status.setText("请等待操作结束后导出一致的备份和日志。");
      return;
    }
    ArrayList<File> folders = new ArrayList<>();
    File[] all = new File(getFilesDir(), "runs").listFiles();
    if (all != null)
      for (File f : all)
        if (f.isDirectory()
            && f.getName().matches("[a-f0-9]{64}")
            && (new File(f, "before.json").isFile()
                || new File(f, "status.json").isFile()
                || new File(f, "journal.json").isFile())) folders.add(f);
    folders.sort((a, b) -> Long.compare(b.lastModified(), a.lastModified()));
    ArrayList<String> labels = new ArrayList<>();
    for (File f : folders)
      labels.add(
          java.text.DateFormat.getDateTimeInstance().format(new Date(f.lastModified()))
              + " · "
              + f.getName().substring(0, 8));
    if (IncomingStore.pending(this) > 0) {
      folders.add(null);
      labels.add("仅导出待处理的新到消息/通知");
    }
    if (folders.isEmpty()) {
      status.setText("目前没有可导出的备份或日志。");
      return;
    }
    new AlertDialog.Builder(this)
        .setTitle("选择需要导出的批次")
        .setItems(
            labels.toArray(new String[0]),
            (d, index) -> {
              exportRunId = folders.get(index) == null ? "incoming" : folders.get(index).getName();
              createDocument("application/zip", "hmos-backup-report.zip", BACKUP_EXPORT);
            })
        .show();
  }

  void exportReport(Uri uri) {
    if (!EXPORTING.compareAndSet(false, true)) {
      status.setText("正在导出，请稍候。");
      return;
    }
    status.setText("正在导出，请保持应用在前台。");
    final String selected = exportRunId;
    new Thread(
            () -> {
              try (RandomAccessFile file =
                      new RandomAccessFile(new File(getFilesDir(), "migration.lock"), "rw");
                  FileChannel channel = file.getChannel();
                  FileLock lock = channel.tryLock()) {
                PackageData.require(lock != null, "export_wait_for_import");
                File folder;
                if ("incoming".equals(selected)) {
                  folder = new File(getFilesDir(), "empty-report");
                  folder.mkdirs();
                } else {
                  PackageData.require(
                      selected != null && selected.matches("[a-f0-9]{64}"), "invalid_export_batch");
                  folder = new File(getFilesDir(), "runs/" + selected);
                  PackageData.require(folder.isDirectory(), "export_batch_missing");
                }
                OutputStream out = getContentResolver().openOutputStream(uri);
                if (out == null) throw new IOException();
                LocalFiles.zip(folder, new File(getFilesDir(), "incoming"), out);
                runOnUiThread(() -> status.setText("备份和报告已导出。"));
              } catch (Exception e) {
                runOnUiThread(() -> showError(e));
              } finally {
                EXPORTING.set(false);
              }
            },
            "export-backup")
        .start();
  }

  void createDocument(String mime, String name, int request) {
    startActivityForResult(
        new Intent(Intent.ACTION_CREATE_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType(mime)
            .putExtra(Intent.EXTRA_TITLE, name),
        request);
  }

  @Override
  protected void onActivityResult(int request, int result, Intent intent) {
    super.onActivityResult(request, result, intent);
    if (request == SMS_ROLE) {
      if (result == RESULT_OK) prepare();
      else status.setText("未取得短信角色，未写入记录。");
      return;
    }
    if (result != RESULT_OK || intent == null || intent.getData() == null) return;
    Uri uri = intent.getData();
    try {
      if (request == PICK) {
        try (InputStream in = getContentResolver().openInputStream(uri)) {
          if (in == null) throw new IOException();
          loadBytes(LocalFiles.read(in, LocalFiles.INPUT_LIMIT));
        }
      } else if (request == PROFILE_EXPORT) {
        try (OutputStream out = getContentResolver().openOutputStream(uri)) {
          out.write(profile.toString(2).getBytes(StandardCharsets.UTF_8));
        }
        status.setText("目标信息已导出，含 SIM/账户标识和系统标签，不含短信正文。分享前请脱敏。");
      } else if (request == BACKUP_EXPORT) exportReport(uri);
    } catch (Exception e) {
      showError(e);
    }
  }

  @Override
  public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
    super.onRequestPermissionsResult(request, permissions, results);
    boolean granted = results.length > 0;
    for (int r : results) granted &= r == PackageManager.PERMISSION_GRANTED;
    if (!granted) {
      status.setText("系统未授予必要权限。请检查系统授权与安装来源策略；普通版和 USB 版都不会绕过权限限制，也可选择 XML 恢复路径。");
      return;
    }
    if (request == PHONE_PERMISSION) refreshProfile();
    else if (request == DATA_PERMISSION) prepare();
  }

  void showError(Exception e) {
    if (status != null)
      status.setText(
          "无法继续：" + Messages.error(MigrationService.safeError(e)) + "\n未清空目标数据。请查看说明或导出报告。");
  }
}
