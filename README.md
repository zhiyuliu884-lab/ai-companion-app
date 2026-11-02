# 大肥鱼 App

给 **AI 角色**（自己搭的聊天机器人人格）做一个专属手机 App：
能打字、发语音、发图片、**打电话**，她还能回你语音、画图、发表情。
可接你自己的 AstrBot / 本地 TTS / 本地出图，也可以完全独立跑。

```
手机 App（或浏览器）
      │  HTTPS
      ▼
后端 server/app.py  ── 人格 ── 记忆 ── LLM API
      │                └── TTS（可选）
      └────────────────  └── 出图（可选）
```

> 这个仓库只包含「手机 App + 后端」。**不包含**任何人的角色人格、API Key、模型权重、聊天记录。

---

## 功能

| 功能 | 是否需要额外服务 | 说明 |
|---|---|---|
| 文字聊天（流式） | 只需要 LLM | 回复逐字出现 |
| 语音回复 | 可选 TTS | 没有 TTS 也能用，只是纯文字 |
| 语音输入 | 本地 whisper | 按住说话，自动转文字 |
| 图片理解 | 多模态模型 | 发图给她看 |
| 打电话 | TTS | 连续对话，可打断，约 3-4 秒开口 |
| 她画图 | 可选出图服务 | 她在回复里标记，自动出图 |
| 表情包 | 可选图片目录 | 随机发 |
| 记忆面板 | 无 | 时间轴 / 搜索 / 记一条 / 忘掉 / 周总结 |
| 推送通知 | Android App | 她主动找你时弹通知 |
| 访问口令 | 无 | 防止公网地址被陌生人用 |

---

## 快速开始（Windows）

### 1. 环境

```powershell
# Python 3.10+
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

需要 **ffmpeg**（在 PATH 里），语音输入靠它解码。

### 2. 配置

```powershell
copy config.example.json config.json
notepad config.json
```

最少要改这三项：

```json
{
  "access_token": "你自己设一个口令",
  "llm": { "api_key": "sk-你的密钥", "model": "deepseek-chat" },
  "persona": { "file": "persona.txt" }
}
```

把角色人格写进 `persona.txt`（纯文本，几千字都行）。

### 3. 跑起来

```powershell
cd server
python app.py
```

打开 <http://127.0.0.1:6191>，第一次会问口令。

### 4. 手机访问

- **同一个 Wi-Fi**：`http://你的内网IP:6191`
- **外网**：
  ```powershell
  cloudflared tunnel --url http://127.0.0.1:6191
  ```
  会给你一个 `https://xxx.trycloudflare.com` 地址（免费，但重启会变）。

### 5. 装成 App（可选）

`android/` 里是一个最小的 WebView 壳，含后台通知服务。
按 [`android/README.md`](android/README.md) 打包，或用现成的 Gradle 工程导入。

---

## 配置说明

| 键 | 说明 |
|---|---|
| `access_token` | 访问口令。留空=不校验（**公网暴露时务必设置**） |
| `persona.source` | `file` 读 `persona.txt`；`astrbot_db` 直接读 AstrBot 的 `data_v4.db` |
| `persona.name` | `source=astrbot_db` 时，读哪个人格的 `persona_id` |
| `llm.*` | 任何 OpenAI 兼容接口都能用（DeepSeek / 通义 / 本地 vLLM） |
| `tts.endpoint` | 你自己的 TTS 服务，需实现 `POST /tts {"text":...,"emotion":...}` 返回音频二进制 |
| `image.endpoint` | 你自己的出图服务，需实现 `POST /generate {"prompt":...}` 返回 `{"path": "..."}` |
| `image.identity_prefix` | 画图时固定拼在前面的形象描述（LoRA 触发词等） |
| `stt.model` | `tiny` / `base` / `small`，越大越准越慢 |
| `memory.astrbot_livingmemory_db` | 接 AstrBot 的 livingmemory，实现跨平台共享记忆；留空则只用本地记忆 |
| `proactive.*` | 主动消息：空闲多久后、每多久、几点到几点 |

---

## 接进 AstrBot（可选）

如果你用 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 管机器人：

```json
"persona": { "source": "astrbot_db", "name": "你的角色名",
             "astrbot_db": "C:/.../core/data/data_v4.db" },
"memory":  { "astrbot_livingmemory_db": "C:/.../plugin_data/astrbot_plugin_livingmemory/livingmemory.db" }
```

这样 **QQ / 微信 / App 三边共用同一份人格和记忆**。

---

## 目录结构

```
server/
  app.py          后端（单文件，标准库，无框架）
  page.html       前端（单文件）
assets/           图标、壁纸
android/          WebView 壳 + 通知服务
scripts/          自愈 / 备份脚本（Windows，可选）
docs/             更多说明
```

---

## 已知限制

- 隧道地址会变（免费版），换地址后要在 App 里手动改
- 打电话延迟 3-4 秒，受 TTS 速度限制
- 语音条是网页实现，不是系统原生播放器
- 推送依赖 Android 常驻服务，省电模式可能被杀
- 只做了 Windows 脚本，其他系统要自己写守护

---

## 安全提示

1. **一定要设 `access_token`**，否则公网地址等于公开聊天记录
2. API Key 只放在 `config.json`（已在 `.gitignore` 里）
3. 别把签好的 APK 和 keystore 提交到仓库
4. 出图/语音服务如果暴露公网，也要加口令

---

## 许可

MIT，见 [LICENSE](LICENSE)。

角色人格、形象、声音的版权归各自作者，请勿用来冒充真人。
