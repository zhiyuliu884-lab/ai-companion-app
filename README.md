# AI 角色手机 App

给 **你自己的 AI 角色**做一个专属手机 App：能打字、发语音、发图片、**打电话**，
她还能回你语音、画图、发表情。

> 本仓库**不含任何角色人格、形象、声音、聊天记录**。
> 头像、壁纸、名字都由你自己在 App 里上传；人格由你自己的文件或 AstrBot 提供。

```
手机 App（或浏览器）
      │  HTTPS
      ▼
后端 server/app.py  ── 人格（你自己的）── 记忆 ── LLM API
      │                    └── TTS（可选，你自己的）
      └────────────────────  └── 出图（可选，你自己的）
```

---

> 📖 **第一次用请先看 [使用说明.md](使用说明.md)**（从解压到手机能连，20 分钟）

## 功能

| 功能 | 需要什么 | 说明 |
|---|---|---|
| **打电话** | 你的 TTS 服务 | 全屏通话、可打断、安静 12 秒她会主动说话 |
| **视频通话（她能看到你）** | 多模态模型 | 通话里开摄像头，她能看见你并聊到画面内容 |
| **情绪语音** | 你的 TTS 服务 | 开心/不高兴/撒娇/害羞/难过，语气跟着内容变 |
|---|---|---|
| 文字聊天（流式） | 只要 LLM | 回复逐字出现 |
| **自定义外观** | 无 | App 内直接上传头像 / 背景 / 改名字 |
| 语音回复 | 你的 TTS 服务 | 没配 TTS 就纯文字 |
| 语音输入 | 本地 whisper | 按住说话 |
| 图片理解 | 多模态模型 | 发图给她看 |
| 打电话 | TTS | 连续对话，可打断，3-4 秒开口 |
| 她画图 | 你的出图服务 | 她在回复里标记，自动出图 |
| 记忆面板 | 无 | 时间轴 / 搜索 / 记一条 / 忘掉 / 周总结 |
| 推送通知 | Android App | 她主动找你时弹通知 |
| 访问口令 | 无 | 防止公网地址被陌生人用 |

---

## 换成你自己的立绘

App 里那个会呼吸、会说话的角色，用的是 `assets/placeholder.png`（灰色占位图）。

**换成你自己的**：

1. 画一张立绘，导出 **720 × 989 的 PNG，背景透明**
2. 覆盖 `assets/placeholder.png`，或者设环境变量 `AC_APPEARANCE` 指向你的图
3. 重启后端，App 里就是你了

> 想换表情/五官分层（眨眼、口型）也可以：把 `static/avatar/blank.png` 换成你自己的
> 眼皮/嘴部贴图，并在 `page.html` 里改 `AV` 那一行的坐标。

> 不想要角色形象？把 `assets/placeholder.png` 换成任意一张图，或者直接用默认灰色剪影。

---

## 快速开始

### 1. 环境

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

还需要 **ffmpeg** 在 PATH 里（语音输入用）。

### 2. 配置

```powershell
copy config.example.json config.json
notepad config.json
```

最少改这三个：

```json
{
  "access_token": "你自己设的口令",
  "llm": { "api_key": "sk-你的密钥", "model": "deepseek-chat" },
  "persona": { "file": "persona.txt" }
}
```

把你角色的设定写进 `persona.txt`（参考 `persona.example.txt`）。

### 3. 启动

```powershell
cd server
python app.py
```

打开 <http://127.0.0.1:6191>，输入口令。

### 4. 换外观

App 里点右上角 **⚙**：

```
名字    改她的称呼
头像    上传方图，自动裁圆
背景    上传竖图，完整显示（留白处自动模糊填充）
```

图片存在 `data/branding/`，不进仓库。

### 5. 手机访问

- **同一个 Wi-Fi**：`http://你的内网IP:6191`
- **外网**：
  ```powershell
  cloudflared tunnel --url http://127.0.0.1:6191
  ```

### 6. 装成 App（可选）

`android/` 是 WebView 壳 + 后台通知服务，打包见 [`android/README.md`](android/README.md)。

**打包前记得改两处：**

```java
// MainActivity.java
public static final String DEFAULT_URL = "https://你的地址/";
```

```
// AndroidManifest.xml 与 Java 包名
com.你的名字.aicompanion
```

图标换成你自己的（`res/mipmap-xxxhdpi/ic_launcher.png`）。

---

## 配置说明

| 键 | 说明 |
|---|---|
| `access_token` | 访问口令。**公网暴露时必须设置** |
| `persona.source` | `file` 读 `persona.txt`；`astrbot_db` 直接读 AstrBot 的 `data_v4.db` |
| `persona.name` | `astrbot_db` 模式下读哪个人格 |
| `llm.*` | 任意 OpenAI 兼容接口（DeepSeek / 通义 / 本地 vLLM） |
| `tts.endpoint` | 你的 TTS 服务，`POST /tts {"text":...}` 返回音频二进制 |
| `image.endpoint` | 你的出图服务，`POST /generate {"prompt":...}` 返回 `{"path":"..."}` |
| `image.identity_prefix` | 画图时固定拼在前面的形象描述（LoRA 触发词等） |
| `stt.model` | `tiny` / `base` / `small` |
| `memory.astrbot_livingmemory_db` | 接 AstrBot 的 livingmemory 实现跨平台记忆；留空用本地记忆 |
| `branding.dir` | 头像/壁纸存放目录，默认 `data/branding` |
| `proactive.*` | 主动消息的空闲阈值、频率、时段 |

详见 [`docs/custom-services.md`](docs/custom-services.md)：如何实现自己的 TTS / 出图。

---

## 接进 AstrBot（可选）

```json
"persona": { "source": "astrbot_db", "name": "你的角色名",
             "astrbot_db": "C:/.../core/data/data_v4.db" },
"memory":  { "astrbot_livingmemory_db": "C:/.../plugin_data/astrbot_plugin_livingmemory/livingmemory.db" }
```

这样 **QQ / 微信 / App 共用同一份人格和记忆**。

---

## 目录结构

```
server/
  app.py          后端（单文件，只用标准库 + pillow + faster-whisper）
  page.html       前端（单文件）
assets/           默认占位图标（无角色形象，可自行替换）
android/          WebView 壳 + 通知服务
scripts/          自愈 / 备份（Windows，可选）
docs/             自定义服务对接说明
data/             运行数据（自动创建，已被 .gitignore）
```

---

## 已知限制

- 免费隧道地址会变，换地址要在 App 里改
- 打电话延迟 3-4 秒，受 TTS 速度限制
- 语音条是网页实现，不是系统原生播放器
- 推送依赖 Android 常驻服务，省电模式可能被杀
- 守护脚本只写了 Windows

---

## 安全

1. **一定设 `access_token`**，否则拿到地址的人能看到聊天记录
2. API Key 只放 `config.json`（已 gitignore）
3. 别把签好的 APK 和 keystore 提交上来
4. 出图/语音服务如果暴露公网，也要加口令

---

## 许可

MIT，见 [LICENSE](LICENSE)。

角色设定、形象、声音的版权归你自己，请勿用于冒充真人。

---

## 配置（环境变量）

后端读环境变量，不依赖任何配置文件：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AC_PORT` | 6191 | 监听端口 |
| `AC_NAME` | 她 | 角色名字（显示在标题/通知） |
| `AC_PERSONA` | 内置示例 | 人格提示词（也可以用你自己的 AstrBot 数据库） |
| `AC_PROVIDER_BASE` | api.deepseek.com/v1 | LLM 接口地址 |
| `AC_PROVIDER_KEY` | 空 | LLM 密钥 |
| `AC_PROVIDER_MODEL` | deepseek-chat | 模型名 |
| `AC_TOKEN_FILE` | data/app_token.txt | 访问口令存放位置（没有就自动生成） |
| `AC_ASSETS` | static | 静态文件目录（`page.html` 在这里） |
| `AC_APPEARANCE` | data/appearance.png | 你上传/指定的立绘 |
| `AC_HISTORY` | data/chat_history.db | 聊天记录 |
| `AC_INSTANCE` | C:\AstrBot\data | 有 AstrBot 时指向它的 data 目录 |
| `AC_QQ_USER` | 0 | 你的 QQ 号（QQ 提醒用，可选） |
| `AC_TTS` | http://127.0.0.1:6194/tts | 你的 TTS 服务 |
| `AC_IMG` | http://127.0.0.1:6196/generate | 你的出图服务 |

PowerShell 里这样起：

```powershell
$env:AC_NAME="小七"
$env:AC_PROVIDER_KEY="sk-你的密钥"
$env:AC_PROVIDER_MODEL="deepseek-chat"
python server/app.py
```

