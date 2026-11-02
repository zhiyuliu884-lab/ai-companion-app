# 对接自己的 TTS / 出图服务

后端只要求两个可选 HTTP 接口，协议很简单，用任何语言实现都行。

---

## 1. TTS：`POST /tts`

请求：

```json
{ "text": "要说的话", "emotion": "charm", "fast": false }
```

- `emotion` 由后端按文本关键词猜，取值：`charm` / `sob` / `sultry` / `happy` / `coquettish` / `sleepy` / `angry`
- `fast` 为 `true` 时表示"通话里要快"，可以走更省时的路径

响应：**音频二进制**（mp3 最好），状态码 200。

最小实现（伪代码）：

```python
if path == "/tts":
    text = req["text"]
    wav = my_tts(text, emotion=req.get("emotion"))
    return mp3_bytes(wav)
```

---

## 2. 出图：`POST /generate`

请求：

```json
{ "prompt": "mychar, 1girl, ...", "model": "anime" }
```

- `prompt` 已经包含了 `image.identity_prefix`，你直接拿去生成即可
- 想加负向提示、步数等参数，自己在这个接口里定

响应：

```json
{ "path": "C:/output/xxx.jpg" }
```

必须是**后端能读到的本地路径**（同机部署最简单）。也可以用返回 base64 的变体，但要改 `server/app.py` 的 `draw_image()`。

---

## 3. 共享记忆（可选）

如果想和 AstrBot 的 livingmemory 共用记忆，把 `memory.astrbot_livingmemory_db` 指到：

```
<AstrBot>/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db
```

后端只做两件事：

- **读**：`SELECT text FROM documents ORDER BY id DESC LIMIT N`，按字面重合度排序
- **写**：往 `documents` 插一行，`metadata.session_id = "livingmemory:global"`

字段结构见 AstrBot 的 livingmemory 插件；如果它改了表结构，改 `lm_recall()` / `lm_write()` 即可。
