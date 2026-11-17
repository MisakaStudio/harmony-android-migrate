package dev.hmos.migrate;

import android.util.AtomicFile;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import org.json.JSONObject;

final class LocalFiles {
  static final int INPUT_LIMIT = 32 * 1024 * 1024;

  static byte[] read(InputStream in, int limit) throws IOException {
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    byte[] buffer = new byte[65536];
    int n;
    while ((n = in.read(buffer)) != -1) {
      if (out.size() + n > limit) throw new IOException("file_too_large");
      out.write(buffer, 0, n);
    }
    return out.toByteArray();
  }

  static synchronized byte[] read(File file, int limit) throws IOException {
    try (InputStream in = new AtomicFile(file).openRead()) {
      return read(in, limit);
    }
  }

  static JSONObject json(File file) throws Exception {
    return new JSONObject(new String(read(file, INPUT_LIMIT), StandardCharsets.UTF_8));
  }

  static synchronized void save(File file, JSONObject data) throws Exception {
    save(file, data.toString().getBytes(StandardCharsets.UTF_8));
  }

  static synchronized void save(File file, byte[] data) throws IOException {
    AtomicFile atomic = new AtomicFile(file);
    FileOutputStream out = null;
    try {
      out = atomic.startWrite();
      out.write(data);
      atomic.finishWrite(out);
    } catch (IOException e) {
      if (out != null) atomic.failWrite(out);
      throw e;
    }
  }

  static String sha(byte[] data) throws Exception {
    MessageDigest digest = MessageDigest.getInstance("SHA-256");
    return hex(digest.digest(data));
  }

  static String sha(File file) throws Exception {
    MessageDigest digest = MessageDigest.getInstance("SHA-256");
    try (InputStream in = new FileInputStream(file)) {
      byte[] buffer = new byte[65536];
      int n;
      while ((n = in.read(buffer)) != -1) digest.update(buffer, 0, n);
    }
    return hex(digest.digest());
  }

  static String hex(byte[] bytes) {
    StringBuilder out = new StringBuilder();
    for (byte b : bytes) out.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
    return out.toString();
  }

  static void zip(File folder, File incoming, OutputStream output) throws Exception {
    try (ZipOutputStream zip = new ZipOutputStream(output)) {
      for (String name :
          new String[] {
            "before.json",
            "plan.json",
            "journal.json",
            "status.json",
            "run-config.json",
            "role-state.json"
          }) {
        File file = new File(folder, name);
        if (!file.isFile()) continue;
        zip.putNextEntry(new ZipEntry(name));
        try (InputStream in = new FileInputStream(file)) {
          byte[] buffer = new byte[65536];
          int n;
          while ((n = in.read(buffer)) != -1) zip.write(buffer, 0, n);
        }
        zip.closeEntry();
      }
      File operations = new File(folder, "operations");
      File[] shards = operations.listFiles();
      if (shards != null)
        for (File shard : shards) {
          if (!shard.isDirectory() || !shard.getName().matches("[a-f0-9]{2}")) continue;
          File[] entries = shard.listFiles();
          if (entries == null) continue;
          for (File entry : entries) {
            if (!entry.isFile() || !entry.getName().matches("[a-f0-9]{64}\\.json")) continue;
            zip.putNextEntry(new ZipEntry("operations/" + shard.getName() + "/" + entry.getName()));
            try (InputStream in = new FileInputStream(entry)) {
              byte[] buffer = new byte[65536];
              int n;
              while ((n = in.read(buffer)) != -1) zip.write(buffer, 0, n);
            }
            zip.closeEntry();
          }
        }
      File[] pending = incoming.listFiles();
      if (pending != null)
        for (File file : pending) {
          if (!file.isFile() || !file.getName().matches("[a-f0-9-]{36}\\.json")) continue;
          zip.putNextEntry(new ZipEntry("incoming/" + file.getName()));
          try (InputStream in = new FileInputStream(file)) {
            byte[] buffer = new byte[65536];
            int n;
            while ((n = in.read(buffer)) != -1) zip.write(buffer, 0, n);
          }
          zip.closeEntry();
        }
    }
  }
}
