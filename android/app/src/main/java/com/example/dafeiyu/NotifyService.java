package com.example.dafeiyu;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/** 后台轮询她有没有主动发消息，有就弹通知。 */
public class NotifyService extends Service {

    private static final String CHANNEL = "dafeiyu_msg";
    private static final String CHANNEL_SVC = "dafeiyu_svc";
    private volatile boolean running = true;
    private Thread worker;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannels();
        startForegroundQuietly();
        worker = new Thread(this::loop);
        worker.setDaemon(true);
        worker.start();
    }

    private void createChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationManager nm = getSystemService(NotificationManager.class);
            NotificationChannel c1 = new NotificationChannel(
                    CHANNEL, "她的消息", NotificationManager.IMPORTANCE_HIGH);
            c1.enableVibration(true);
            nm.createNotificationChannel(c1);
            NotificationChannel c2 = new NotificationChannel(
                    CHANNEL_SVC, "后台常驻", NotificationManager.IMPORTANCE_MIN);
            nm.createNotificationChannel(c2);
        }
    }

    private void startForegroundQuietly() {
        try {
            Notification n = build("大肥鱼", "在后台陪着你", CHANNEL_SVC, false);
            startForeground(1, n);
        } catch (Exception ignored) {
        }
    }

    private Notification build(String title, String text, String channel, boolean alert) {
        Intent open = new Intent(this, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, flags);
        Notification.Builder b = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                ? new Notification.Builder(this, channel)
                : new Notification.Builder(this);
        b.setContentTitle(title).setContentText(text).setSmallIcon(R.mipmap.ic_launcher)
                .setAutoCancel(true).setContentIntent(pi);
        if (alert) b.setDefaults(Notification.DEFAULT_ALL);
        return b.build();
    }

    private String baseUrl() {
        SharedPreferences sp = getSharedPreferences("dafeiyu", MODE_PRIVATE);
        return sp.getString("url", MainActivity.DEFAULT_URL);
    }

    private void loop() {
        while (running) {
            try {
                String url = baseUrl();
                if (!url.endsWith("/")) url = url + "/";
                HttpURLConnection conn = (HttpURLConnection) new URL(url + "api/pending").openConnection();
                conn.setConnectTimeout(12000);
                conn.setReadTimeout(12000);
                if (conn.getResponseCode() == 200) {
                    StringBuilder sb = new StringBuilder();
                    try (BufferedReader r = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"))) {
                        String line;
                        while ((line = r.readLine()) != null) sb.append(line);
                    }
                    JSONObject obj = new JSONObject(sb.toString());
                    JSONArray items = obj.optJSONArray("items");
                    if (items != null && items.length() > 0) {
                        JSONObject first = items.getJSONObject(0);
                        String text = first.optString("text", "");
                        if (!text.isEmpty()) {
                            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
                            nm.notify((int) (System.currentTimeMillis() % 100000),
                                    build("大肥鱼", text, CHANNEL, true));
                        }
                    }
                }
                conn.disconnect();
            } catch (Exception ignored) {
            }
            try {
                Thread.sleep(45000);
            } catch (InterruptedException e) {
                break;
            }
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}

