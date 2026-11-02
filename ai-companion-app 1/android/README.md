# 打包 Android App

这里给两条路：**用 Gradle（推荐，别人接手方便）** 或 **不用 Gradle（我实测能过，无需下载几百 MB）**。

---

## 一、改三处再打包

```
1. app/src/main/AndroidManifest.xml    → package="com.你的名字.aicompanion"
2. app/src/main/java/com/example/aicompanion/*.java   → 第一行 package 同步改
3. MainActivity.java 里的 DEFAULT_URL   → 换成你的公网地址
```

```java
public static final String DEFAULT_URL = "https://你的地址.trycloudflare.com/";
```

> 地址错了也没关系：App 连不上会弹框让你手动输入，并记住。

图标替换：`app/src/main/res/mipmap-xxxhdpi/ic_launcher.png`（512×512 以上方形）。

---

## 二、Gradle 方式

1. 用 Android Studio 打开 `android/` 目录（它会自动生成 Gradle 工程）
2. 直接 Run / Build APK

或者命令行：

```bash
cd android
./gradlew assembleDebug        # Windows: gradlew.bat assembleDebug
```

产物在 `app/build/outputs/apk/debug/app-debug.apk`。

---

## 三、不用 Gradle（只用 Android SDK 工具链）

需要：JDK 17、Android SDK（platform-tools + platforms;android-34 + build-tools;34.0.0）。

```powershell
# 变量
$SDK="C:\Android\sdk"; $BT="$SDK\build-tools\34.0.0"; $PLAT="$SDK\platforms\android-34\android.jar"
$JAVA="C:\Android\jdk17\jdk-17.0.13+11"   # 你本机的 JDK 17
$SRC="android\app\src\main"

# 0) 图标 + 字符串 -> 资源包
& "$BT\aapt2.exe" compile --dir "$SRC\res" -o build\res.zip
& "$BT\aapt2.exe" link -o build\app-res.apk -I $PLAT --manifest "$SRC\AndroidManifest.xml" `
   build\res.zip --java build\gen --min-sdk-version 24 --target-sdk-version 34 --auto-add-overlay

# 1) 编译 Java（含自动生成的 R.java）
$srcs = @(Get-ChildItem "$SRC\java","build\gen" -Recurse -Filter *.java | % { $_.FullName })
& "$JAVA\bin\javac.exe" -encoding UTF-8 -source 11 -target 11 -classpath $PLAT -d build\classes @srcs

# 2) 转 dex
$cls = Get-ChildItem build\classes -Recurse -Filter *.class | % { $_.FullName }
& "$BT\d8.bat" --lib $PLAT --min-api 24 --output build\dex @cls

# 3) 打包 + 塞 dex
& "$BT\aapt2.exe" link -o build\app-unsigned.apk -I $PLAT --manifest "$SRC\AndroidManifest.xml" `
   build\res.zip --min-sdk-version 24 --target-sdk-version 34 --auto-add-overlay
Copy-Item build\app-unsigned.apk build\app-dex.apk
& "$JAVA\bin\jar.exe" uf build\app-dex.apk -C build\dex classes.dex

# 4) 对齐 + 签名
& "$BT\zipalign.exe" -f 4 build\app-dex.apk build\app-aligned.apk
keytool -genkeypair -keystore my.jks -alias aicompanion -keyalg RSA -keysize 2048 -validity 10000 `
        -storepass 你的密码 -keypass 你的密码 -dname "CN=Me"
& "$BT\apksigner.bat" sign --ks my.jks --ks-pass pass:你的密码 --key-pass pass:你的密码 `
   --out AiCompanion.apk build\app-aligned.apk
```

> `my.jks` 千万别提交到仓库，丢了就没法覆盖升级已装的 App。

---

## 四、关键权限说明

| 权限 | 用途 |
|---|---|
| `INTERNET` | 连后端 |
| `RECORD_AUDIO` | 语音输入、打电话 |
| `POST_NOTIFICATIONS` | 推送（Android 13+ 要动态申请） |
| `FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_DATA_SYNC` | 后台轮询她的消息 |

`NotifyService` 每 45 秒请求一次 `/api/pending`，有内容就弹通知。

---

## 五、常见问题

**Q：App 白屏 / 连不上**
A：`DEFAULT_URL` 必须带 `https://` 和结尾 `/`；隧道没开也会白屏。

**Q：语音条点不动**
A：网页版需要点击手势解锁音频；App 里已在 WebView 设置 `setMediaPlaybackRequiresUserGesture(false)`。

**Q：打电话没声音**
A：先确认后端 `/api/tts` 能返回音频（`tts.endpoint` 配了没），再看手机媒体音量。

**Q：推送不弹**
A：检查通知权限、电池优化（把它加入白名单）、以及后台服务是否被杀。
