package dev.hmos.migrate;

import android.app.*;
import android.content.*;
import android.os.IBinder;
import java.io.*;
import java.nio.channels.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.*;

public class MigrationService extends Service {
  static final AtomicBoolean RUNNING = new AtomicBoolean(false);
  static final int NOTICE = 31;

  public IBinder onBind(Intent intent) {
    return null;
  }

  static File configFile(Context context) {
    return new File(context.getFilesDir(), "active.json");
  }

  static File runFolder(Context context, JSONObject config) throws Exception {
    String id = config.getString("run_id");
    PackageData.require(id.matches("[a-f0-9]{64}"), "invalid_run_id");
    File folder = new File(context.getFilesDir(), "runs/" + id);
    folder.mkdirs();
    return folder;
  }

  Notification notification(String text) {
    NotificationManager manager = getSystemService(NotificationManager.class);
    manager.createNotificationChannel(
        new NotificationChannel("migration", "本地迁移", NotificationManager.IMPORTANCE_LOW));
    PendingIntent open =
        PendingIntent.getActivity(
            this,
            0,
            new Intent(this, MainActivity.class),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    return new Notification.Builder(this, "migration")
        .setSmallIcon(android.R.drawable.stat_sys_upload)
        .setContentTitle("鸿蒙本地迁移")
        .setContentText(text)
        .setContentIntent(open)
        .setOngoing(true)
        .build();
  }

  public int onStartCommand(Intent intent, int flags, int startId) {
    if (!RUNNING.compareAndSet(false, true)) return START_NOT_STICKY;
    startForeground(NOTICE, notification("正在备份或核对，请勿关闭应用"));
    boolean commit = intent != null && "commit".equals(intent.getAction());
    new Thread(
            () -> {
              File folder = null;
              try (RandomAccessFile lockFile =
                      new RandomAccessFile(new File(getFilesDir(), "migration.lock"), "rw");
                  FileChannel channel = lockFile.getChannel();
                  FileLock lock = channel.tryLock()) {
                if (lock == null) throw new IllegalStateException("migration_already_running");
                JSONObject config = LocalFiles.json(configFile(this));
                folder = runFolder(this, config);
                LocalFiles.save(
                    new File(folder, "status.json"),
                    new JSONObject().put("state", commit ? "validating" : "preparing"));
                final File run = folder;
                String name = config.getString("input_name");
                PackageData.require(
                    name.matches("input-[a-f0-9]{64}\\.json"), "invalid_input_path");
                PackageData data = new PackageData(new File(getFilesDir(), name));
                PackageData.require(
                    data.inputHash.equals(config.getString("input_hash")), "input_changed");
                PackageData.require(
                    !data.partial() || config.optBoolean("allow_partial"),
                    "partial_source_not_confirmed");
                MigrationEngine engine =
                    new MigrationEngine(
                        this,
                        data,
                        config.getJSONObject("mapping"),
                        folder,
                        (state, done, total) -> {
                          try {
                            LocalFiles.save(
                                new File(run, "status.json"),
                                new JSONObject()
                                    .put("state", state)
                                    .put("completed", done)
                                    .put("total", total));
                          } catch (Exception e) {
                            throw new IllegalStateException("status_write_failed", e);
                          }
                          if (done % 10 == 0)
                            getSystemService(NotificationManager.class)
                                .notify(NOTICE, notification("正在处理 " + done + " / " + total));
                        });
                engine.guard();
                engine.validateTarget();
                engine.backup();
                JSONObject result =
                    commit ? engine.commit(config.optBoolean("allow_cross_sim")) : engine.plan();
                result.put("state", commit ? "imported" : "prepared");
                LocalFiles.save(new File(folder, "status.json"), result);
              } catch (Exception error) {
                if (folder != null)
                  try {
                    LocalFiles.save(
                        new File(folder, "status.json"),
                        new JSONObject().put("state", "failed").put("error", safeError(error)));
                  } catch (Exception ignored) {
                  }
              } finally {
                stopForeground(STOP_FOREGROUND_DETACH);
                getSystemService(NotificationManager.class)
                    .notify(NOTICE, notification("请查看结果，并恢复原默认短信应用"));
                stopSelf();
                RUNNING.set(false);
              }
            },
            "hmos-migration")
        .start();
    return START_NOT_STICKY;
  }

  static String safeError(Exception error) {
    String message = error.getMessage();
    if (message != null && message.matches("[a-z0-9_:]{1,100}")) return message;
    if (error instanceof SecurityException) return "permission_denied";
    if (error instanceof IOException) return "file_or_storage_error";
    return "unsupported_data_or_device";
  }
}
