"""大肥鱼手机 App 后端（可开源版）

一个"给 AI 角色做个手机聊天 App"的通用后端：
  - 聊天（流式输出）
  - 语音回复（可接本地 TTS 服务，不接就纯文字）
  - 语音输入（faster-whisper 本地转写，可选）
  - 图片理解（多模态模型）
  - 让角色画图 / 发表情包（可选，接本地出图服务）
  - 记忆：时间轴 / 搜索 / 手动记 / 遗忘 / 周总结
  - 主动消息 + 推送队列
  - 访问口令（防止公网地址被陌生人用掉）

所有与本机相关的路径、地址、人格都放在 config.json 里。
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import sqlite3
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.environ.get("DAFEIYU_CONFIG") or os.path.join(ROOT, "..", "config.json")


# ------------------------------------------------------------------ 配置
DEFAULTS = {
    "port": 6191,
    "host": "0.0.0.0",
    "access_token": "",
    "persona": {
        "name": "大肥鱼",
        "source": "file",            # file | astrbot_db
        "file": "persona.txt",       # source=file 时读取的文本文件（相对仓库根）
        "astrbot_db": "",            # source=astrbot_db 时，AstrBot 的 data_v4.db 路径
    },
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 0.9,
        "max_tokens": 800,
        "call_max_tokens": 120,
    },
    "tts": {
        "endpoint": "",              # 例如 http://127.0.0.1:6194/tts，留空=不发声
        "emotion_enabled": True,
    },
    "image": {
        "endpoint": "",              # 例如 http://127.0.0.1:6196/generate，留空=不画图
        "identity_prefix": "",       # 例如 "mychar, 1girl, "，画图时拼在描述前面
        "model": "anime",
    },
    "stt": {
        "enabled": True,
        "model": "base",
        "ffmpeg": "ffmpeg",
    },
    "memory": {
        "astrbot_livingmemory_db": "",   # 留空=只用本地记忆
        "local_db": "data/chat.db",
        "recall_pool": 1500,
    },
    "meme_dir": "",                  # 表情包图片目录，留空=不用表情包
    "assets_dir": "../assets",       # 放 icon/wallpaper 的目录
    "proactive": {
        "enabled": True,
        "idle_minutes": 30,
        "interval_minutes": 10,
        "active_hours": [8, 23],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    cfg = DEFAULTS
    if os.path.exists(CFG_PATH):
        cfg = _deep_merge(DEFAULTS, json.load(open(CFG_PATH, encoding="utf-8-sig")))
    for env, path in (
        ("DAFEIYU_TOKEN", ("access_token",)),
        ("DAFEIYU_LLM_KEY", ("llm", "api_key")),
        ("DAFEIYU_LLM_BASE", ("llm", "base_url")),
        ("DAFEIYU_LLM_MODEL", ("llm", "model")),
        ("DAFEIYU_TTS", ("tts", "endpoint")),
        ("DAFEIYU_IMAGE", ("image", "endpoint")),
        ("DAFEIYU_PORT", ("port",)),
    ):
        val = os.environ.get(env)
        if val:
            node = cfg
            for part in path[:-1]:
                node = node.setdefault(part, {})
            node[path[-1]] = int(val) if path[-1] == "port" else val
    return cfg


CFG = load_config()
PERSONA_CFG = CFG["persona"]
LLM = CFG["llm"]
TTS_CFG = CFG["tts"]
IMG_CFG = CFG["image"]
MEM_CFG = CFG["memory"]
_repo = os.path.dirname(ROOT)
_ad = CFG.get("assets_dir") or "assets"
ASSETS = _ad if os.path.isabs(_ad) else os.path.normpath(os.path.join(_repo, _ad))
_ld = MEM_CFG.get("local_db") or "data/chat.db"
LOCAL_DB = _ld if os.path.isabs(_ld) else os.path.normpath(os.path.join(_repo, _ld))
TOKEN = (CFG.get("access_token") or "").strip()
PORT = int(CFG.get("port") or 6191)
HOST = CFG.get("host") or "0.0.0.0"


# ------------------------------------------------------------------ 人格
def load_persona() -> str:
    src = PERSONA_CFG.get("source", "file")
    if src == "astrbot_db" and PERSONA_CFG.get("astrbot_db"):
        try:
            db = PERSONA_CFG["astrbot_db"]
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            row = con.execute(
                "SELECT system_prompt FROM personas WHERE persona_id=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (PERSONA_CFG.get("name", ""),)).fetchone()
            con.close()
            if row and row[0]:
                return row[0]
        except Exception:
            traceback.print_exc()
    path = PERSONA_CFG.get("file") or "persona.txt"
    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(os.path.dirname(ROOT), path))
    if os.path.exists(path):
        return open(path, encoding="utf-8").read().strip()
    return "你是一个住在电脑里的 AI 角色，说话自然、简短、有性格。"


PERSONA = load_persona()


def tool_hint() -> str:
    hints = []
    if IMG_CFG.get("endpoint"):
        hints.append("- 想给他看图时，在回复里写 [draw]画面描述[/draw]，系统会自动画好发给他。")
        if CFG.get("meme_dir"):
            hints.append("- 想发表情包时，写 [meme]关键词[/meme]。")
        hints.append("- 这两个标记不会被念出来、也不显示成文字。没必要就别用。")
    return ("\n【额外能力】\n" + "\n".join(hints)) if hints else ""


TOOL_HINT = tool_hint()


# ------------------------------------------------------------------ 本地存储（历史）
os.makedirs(os.path.dirname(LOCAL_DB) or ".", exist_ok=True)


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(LOCAL_DB, timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS msgs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " role TEXT, content TEXT, ts REAL)")
    return con


def hist_add(role: str, content: str) -> None:
    con = _db()
    con.execute("INSERT INTO msgs(role, content, ts) VALUES(?,?,?)", (role, content, time.time()))
    con.commit()
    con.close()


def hist_tail(n: int = 20) -> list[dict]:
    con = _db()
    rows = con.execute("SELECT role, content FROM msgs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    con.close()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def hist_count() -> int:
    con = _db()
    n = con.execute("SELECT COUNT(*) FROM msgs").fetchone()[0]
    con.close()
    return n


def last_activity() -> float:
    con = _db()
    row = con.execute("SELECT MAX(ts) FROM msgs").fetchone()
    con.close()
    return float(row[0] or 0)


# ------------------------------------------------------------------ 共享记忆（可选）
LM_DB = MEM_CFG.get("astrbot_livingmemory_db") or ""


def lm_enabled() -> bool:
    return bool(LM_DB) and os.path.exists(LM_DB)


def lm_recall(query: str, k: int = 4) -> list[str]:
    if not lm_enabled():
        return []
    try:
        con = sqlite3.connect(f"file:{LM_DB}?mode=ro", uri=True)
        rows = con.execute("SELECT text FROM documents ORDER BY id DESC LIMIT ?",
                           (int(MEM_CFG.get("recall_pool", 1500)),)).fetchall()
        con.close()
    except Exception:
        return []
    q = set(query[i:i + 2] for i in range(max(0, len(query) - 1)))
    if not q:
        return []
    total = len(rows)
    scored = []
    for idx, (text,) in enumerate(rows):
        if not text:
            continue
        grams = set(text[i:i + 2] for i in range(max(0, len(text) - 1)))
        overlap = len(q & grams) / max(1, len(q))
        if overlap > 0.05:
            scored.append((overlap * 0.75 + (1 - idx / max(1, total)) * 0.25, text))
    scored.sort(key=lambda x: -x[0])
    return [t[:500] for _, t in scored[:k]]


def lm_write(summary: str) -> None:
    if lm_enabled():
        try:
            now = time.time()
            meta = {"session_id": "livingmemory:global", "persona_id": PERSONA_CFG.get("name", ""),
                    "importance": 0.5, "create_time": now, "last_access_time": now,
                    "status": "active", "interaction_type": "private_chat",
                    "canonical_summary": summary, "source_session_id": "app:mobile",
                    "has_source": False}
            con = sqlite3.connect(LM_DB, timeout=10)
            con.execute("INSERT INTO documents(doc_id, text, metadata, created_at, updated_at)"
                        " VALUES(?,?,?,datetime('now'),datetime('now'))",
                        (str(uuid.uuid4()), summary, json.dumps(meta, ensure_ascii=False)))
            con.commit()
            con.close()
        except Exception:
            traceback.print_exc()
    # 本地也留一份（没接 livingmemory 时，记忆面板靠它）
    try:
        con = _db()
        con.execute("CREATE TABLE IF NOT EXISTS memory (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " text TEXT, ts REAL)")
        con.execute("INSERT INTO memory(text, ts) VALUES(?,?)", (summary, time.time()))
        con.commit()
        con.close()
    except Exception:
        traceback.print_exc()


def memory_rows(limit: int = 80) -> list[dict]:
    if lm_enabled():
        try:
            con = sqlite3.connect(f"file:{LM_DB}?mode=ro", uri=True)
            rows = con.execute("SELECT id, text, created_at FROM documents "
                               "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            con.close()
            return [{"id": r[0], "text": (r[1] or "")[:300], "time": str(r[2])} for r in rows]
        except Exception:
            pass
    con = _db()
    rows = con.execute("SELECT id, text, ts FROM memory ORDER BY id DESC LIMIT ?",
                       (limit,)).fetchall()
    con.close()
    return [{"id": r[0], "text": (r[1] or "")[:300],
             "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[2] or 0))} for r in rows]


def memory_search(q: str, k: int = 10) -> list[dict]:
    rows = memory_rows(2000)
    qs = set(q[i:i + 2] for i in range(max(0, len(q) - 1)))
    hits = []
    for r in rows:
        g = set(r["text"][i:i + 2] for i in range(max(0, len(r["text"]) - 1)))
        sc = len(qs & g) / max(1, len(qs))
        if sc > 0.1:
            hits.append({**r, "score": round(sc, 2)})
    hits.sort(key=lambda x: -x["score"])
    return hits[:k]


def memory_delete(mid) -> None:
    table = "documents" if lm_enabled() else "memory"
    db = LM_DB if lm_enabled() else LOCAL_DB
    con = sqlite3.connect(db, timeout=10)
    con.execute(f"DELETE FROM {table} WHERE id=?", (mid,))
    con.commit()
    con.close()


# ------------------------------------------------------------------ 模型
def _messages(text: str, call_mode: bool = False, max_hist: int = 20) -> list[dict]:
    persona = PERSONA[:1200] if call_mode else PERSONA
    extra = "" if call_mode else TOOL_HINT
    if call_mode:
        extra = ("\n【现在是打电话】第一句先来一个很短的回应（两到六个字），"
                 "后面再补一句，总共不超过三句。别列点、别长篇大论。")
    msgs = [{"role": "system", "content": persona + extra}]
    if text and not call_mode:
        mem = lm_recall(text)
        if mem:
            msgs.append({"role": "system",
                         "content": "【你记得的事】\n" + "\n".join("- " + m for m in mem)})
    msgs += hist_tail(6 if call_mode else max_hist)
    return msgs


def _llm_request(messages, max_tokens, stream=False):
    body = json.dumps({"model": LLM["model"], "messages": messages,
                       "temperature": LLM.get("temperature", 0.9),
                       "max_tokens": max_tokens, "stream": stream}).encode()
    req = urllib.request.Request(
        LLM["base_url"].rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {LLM['api_key']}"}, method="POST")
    return urllib.request.urlopen(req, timeout=180)


def llm_stream(msgs: list[dict], max_tokens: int = 800):
    with _llm_request(msgs, max_tokens, stream=True) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"].get("content")
            except Exception:
                continue
            if delta:
                yield delta


def friendly_llm_error(exc: Exception) -> str:
    msg = str(exc)
    if "401" in msg or "invalid_api_key" in msg.lower():
        return "模型密钥不对，去 config.json 里改 llm.api_key"
    if "402" in msg or "quota" in msg.lower() or "余额" in msg:
        return "模型账号没额度了"
    if "timeout" in msg.lower():
        return "模型响应超时，再试一次"
    return f"模型调用失败：{msg[:120]}"


def llm_once(msgs: list[dict], max_tokens: int = 800) -> str:
    with _llm_request(msgs, max_tokens, stream=False) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"] or ""


# ------------------------------------------------------------------ 语音
EMO_RULES = [
    ("sob", ("呜", "哭", "委屈", "眼泪", "抽气")),
    ("sultry", ("亲亲", "抱紧", "贴着", "耳边", "撩", "害羞", "脸红", "别看我")),
    ("happy", ("哈哈", "笑死", "开心", "喜欢", "太好")),
    ("coquettish", ("嘛", "好不好", "抱抱", "想要")),
    ("sleepy", ("困", "睡", "哈欠")),
    ("angry", ("生气", "讨厌", "哼", "烦")),
]


def pick_emotion(text: str) -> str:
    for name, words in EMO_RULES:
        if any(w in text for w in words):
            return name
    return "charm"


def tts_b64(text: str, fast: bool = False) -> str | None:
    if not (text or "").strip() or not TTS_CFG.get("endpoint"):
        return None
    try:
        body = {"text": text, "emotion": "charm" if fast else pick_emotion(text)}
        if fast:
            body["fast"] = True
        req = urllib.request.Request(TTS_CFG["endpoint"],
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
        return ("data:audio/mp3;base64," + base64.b64encode(raw).decode()) if raw else None
    except Exception:
        traceback.print_exc()
        return None


# ------------------------------------------------------------------ 画图 / 表情
_DRAW_RE = re.compile(r"\[draw\](.*?)(?:\[/draw\]|$)", re.S)
_MEME_RE = re.compile(r"\[meme\](.*?)(?:\[/meme\]|$)", re.S)
_CLEAN = re.compile(r"\[\[.*?\]\]|【[^】]{0,6}】|<[^>]{1,30}>")


def clean_reply(t: str) -> str:
    t = _DRAW_RE.sub("", t or "")
    t = _MEME_RE.sub("", t)
    return _CLEAN.sub("", t).strip()


def draw_image(prompt: str) -> str | None:
    if not IMG_CFG.get("endpoint"):
        return None
    try:
        full = (IMG_CFG.get("identity_prefix") or "") + prompt + ", masterpiece, best quality"
        req = urllib.request.Request(IMG_CFG["endpoint"],
                                     data=json.dumps({"prompt": full,
                                                      "model": IMG_CFG.get("model", "anime")}).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.loads(r.read().decode())
        path = data.get("path")
        if not path or not os.path.exists(path):
            return None
        from PIL import Image

        img = Image.open(path).convert("RGB")
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        traceback.print_exc()
        return None


def pick_meme() -> str | None:
    d = CFG.get("meme_dir") or ""
    if not d or not os.path.isdir(d):
        return None
    try:
        files = [os.path.join(r, n) for r, _ds, ns in os.walk(d) for n in ns
                 if n.lower().endswith((".jpg", ".png", ".jpeg", ".gif"))]
        if not files:
            return None
        from PIL import Image

        img = Image.open(random.choice(files)).convert("RGB")
        img.thumbnail((512, 512))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


_unread: list[dict] = []


def post_process(raw: str, text: str) -> dict:
    draws = [d.strip() for d in _DRAW_RE.findall(raw) if d.strip()]
    memes = _MEME_RE.findall(raw)
    reply = clean_reply(raw)
    if not reply:
        reply = "画好了" if draws else "……"
    hist_add("assistant", reply)
    try:
        lm_write(f"[{time.strftime('%Y-%m-%d %H:%M')} App] 用户：{text[:80]}；我：{reply[:80]}"[:600])
    except Exception:
        pass
    images = []
    for d in draws[:2]:
        img = draw_image(d)
        if img:
            images.append(img)
    if memes and not images and not images:
        m = pick_meme()
        if m:
            images.append(m)
    return {"ok": True, "reply": reply, "audio": tts_b64(reply), "images": images,
            "emotion": pick_emotion(reply)}


# ------------------------------------------------------------------ 鉴权
def check_token(handler) -> bool:
    if not TOKEN:
        return True
    got = handler.headers.get("X-Token") or ""
    if not got:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        got = (q.get("t") or [""])[0]
    if not got:
        cookie = handler.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "dafeiyu_token":
                got = v
    return got == TOKEN


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---------- 输出工具 ----------
    def _json(self, code: int, payload: dict):
        b = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _file(self, path: str, ctype: str, cache: str = "no-store"):
        data = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _need_auth(self) -> bool:
        if check_token(self):
            return False
        self._json(401, {"ok": False, "error": "需要访问口令", "need_token": True})
        return True

    # ---------- GET ----------
    def do_GET(self):
        path, _, query = self.path.partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        try:
            if path in ("/", "/index.html"):
                self._file(os.path.join(ROOT, "page.html"), "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                fn = os.path.basename(path)
                fp = os.path.join(ASSETS, fn)
                if not os.path.exists(fp):
                    self._json(404, {"error": "no asset"})
                    return
                ctype = ("image/png" if fn.endswith(".png")
                         else "image/jpeg" if fn.endswith((".jpg", ".jpeg"))
                         else "application/octet-stream")
                self._file(fp, ctype, "public, max-age=300")
            elif path == "/apk":
                apk = os.path.normpath(os.path.join(os.path.dirname(ROOT), "Dafeiyu.apk"))
                if not os.path.exists(apk):
                    self._json(404, {"error": "apk not built"})
                    return
                data = open(apk, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", 'attachment; filename="Dafeiyu.apk"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif path == "/manifest.json":
                self._json(200, {
                    "name": PERSONA_CFG.get("name", "AI"), "short_name": PERSONA_CFG.get("name", "AI"),
                    "start_url": "/", "display": "standalone",
                    "background_color": "#0b1020", "theme_color": "#0b1020",
                    "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                              {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]})
            elif path == "/api/health":
                self._json(200, {"ok": True, "persona_len": len(PERSONA), "model": LLM["model"],
                                 "tts": bool(TTS_CFG.get("endpoint")),
                                 "image": bool(IMG_CFG.get("endpoint")),
                                 "shared_memory": lm_enabled(),
                                 "auth": bool(TOKEN)})
            elif path == "/api/auth":
                self._json(200, {"ok": check_token(self), "auth_required": bool(TOKEN)})
            elif path == "/api/history":
                if self._need_auth():
                    return
                self._json(200, {"ok": True, "messages": hist_tail(30), "count": hist_count()})
            elif path == "/api/pending":
                if self._need_auth():
                    return
                global _unread
                batch, _unread = _unread, []
                self._json(200, {"ok": True, "items": batch})
            elif path == "/api/memory/timeline":
                if self._need_auth():
                    return
                days = int(q.get("days", 7))
                self._json(200, {"ok": True, "items": memory_rows(80), "days": days})
            elif path == "/api/memory/search":
                if self._need_auth():
                    return
                self._json(200, {"ok": True, "items": memory_search(q.get("q", ""))})
            elif path == "/api/stream":
                if self._need_auth():
                    return
                self._stream(q.get("text", ""), call=(q.get("call") == "1"))
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"ok": False, "error": str(exc)})

    # ---------- 流式 ----------
    def _stream(self, text: str, call: bool = False):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(obj: dict):
            try:
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
            except Exception:
                pass

        text = (text or "").strip()
        if not text:
            emit({"type": "error", "error": "空消息"})
            return
        try:
            hist_add("user", text)
            buf = []
            toks = int(LLM.get("call_max_tokens", 120)) if call else int(LLM.get("max_tokens", 800))
            for delta in llm_stream(_messages(text, call, 6 if call else 20), toks):
                buf.append(delta)
                emit({"type": "delta", "t": delta})
            if call:
                reply = clean_reply("".join(buf)) or "嗯"
                hist_add("assistant", reply)
                try:
                    lm_write(f"[{time.strftime('%H:%M')} 通话] 用户：{text[:60]}；我：{reply[:60]}"[:400])
                except Exception:
                    pass
                emit({"type": "done", "reply": reply, "audio": None, "images": [],
                      "emotion": pick_emotion(reply)})
                return
            done = post_process("".join(buf), text)
            emit({"type": "done", "reply": done["reply"], "audio": done["audio"],
                  "images": done["images"], "emotion": done["emotion"]})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            emit({"type": "error", "error": friendly_llm_error(exc)})

    # ---------- POST ----------
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json(400, {"ok": False, "error": "bad json"})
            return
        path = self.path.split("?")[0]
        if self._need_auth():
            return
        try:
            if path == "/api/chat":
                text = (payload.get("text") or "").strip()
                if not text:
                    self._json(200, {"ok": False, "error": "空消息"})
                    return
                hist_add("user", text)
                try:
                    self._json(200, post_process("".join(llm_stream(_messages(text))), text))
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    self._json(200, {"ok": False, "error": friendly_llm_error(exc)})
            elif path == "/api/image":
                if not payload.get("image"):
                    self._json(400, {"ok": False, "error": "没有图片"})
                    return
                text = (payload.get("text") or "").strip() or "看看这个"
                msgs = [{"role": "system", "content": PERSONA + TOOL_HINT}]
                msgs += hist_tail(12)
                msgs.append({"role": "user", "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": payload["image"]}}]})
                hist_add("user", f"[图片] {text}")
                self._json(200, post_process(llm_once(msgs, 700), text))
            elif path == "/api/tts":
                txt = (payload.get("text") or "").strip()
                self._json(200, {"ok": True, "audio": tts_b64(txt, fast=bool(payload.get("fast")))})
            elif path == "/api/stt":
                heard = stt(payload.get("audio") or "")
                self._json(200, {"ok": bool(heard), "text": heard})
            elif path == "/api/voice":
                call_mode = bool(payload.get("call"))
                heard = stt(payload.get("audio") or "")
                if not heard:
                    self._json(200, {"ok": False, "error": "没听清，再说一次？"})
                    return
                hist_add("user", heard)
                if call_mode:
                    reply = clean_reply("".join(llm_stream(_messages(heard, True, 6),
                                                           int(LLM.get("call_max_tokens", 120)))) or "嗯")
                    hist_add("assistant", reply)
                    self._json(200, {"ok": True, "reply": reply, "audio": None, "images": [],
                                     "heard": heard, "emotion": pick_emotion(reply)})
                    return
                out = post_process("".join(llm_stream(_messages(heard))), heard)
                out["heard"] = heard
                self._json(200, out)
            elif path == "/api/memory/remember":
                lm_write(f"[手动记录 {time.strftime('%Y-%m-%d %H:%M')}] "
                         f"{(payload.get('text') or '')[:400]}")
                self._json(200, {"ok": True})
            elif path == "/api/memory/forget":
                if payload.get("id"):
                    memory_delete(payload["id"])
                self._json(200, {"ok": True})
            elif path == "/api/memory/summary":
                rows = memory_rows(60)
                brief = "\n".join("- " + r["text"][:120] for r in rows[:40]) or "（没有记忆）"
                txt = llm_once([
                    {"role": "system", "content": "用第一人称、口语化、简短地总结这些记忆。"},
                    {"role": "user", "content": brief}], 600)
                self._json(200, {"ok": True, "summary": txt})
            elif path == "/api/clear":
                con = _db()
                con.execute("DELETE FROM msgs")
                con.commit()
                con.close()
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


# ------------------------------------------------------------------ 语音转文字
_whisper = None


def stt(data_url: str) -> str:
    global _whisper
    if not data_url or not CFG["stt"].get("enabled", True):
        return ""
    if _whisper is None:
        from faster_whisper import WhisperModel

        _whisper = WhisperModel(CFG["stt"].get("model", "base"), device="cpu", compute_type="int8")
    import numpy as np

    head, b64 = data_url.split(",", 1) if "," in data_url else ("", data_url)
    ext = "webm" if "webm" in head else ("wav" if "wav" in head else "ogg")
    tmp = os.path.join(ROOT, "..", "data")
    os.makedirs(tmp, exist_ok=True)
    raw_path = os.path.join(tmp, f"voice_in.{ext}")
    pcm = os.path.join(tmp, "voice_in.pcm")
    open(raw_path, "wb").write(base64.b64decode(b64))
    ff = CFG["stt"].get("ffmpeg", "ffmpeg")
    os.system(f'"{ff}" -y -loglevel error -i "{raw_path}" -ar 16000 -ac 1 -f s16le "{pcm}"')
    audio = np.fromfile(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return ""
    segs, _ = _whisper.transcribe(audio, language="zh", vad_filter=True)
    return "".join(s.text for s in segs).strip()


def warm_stt():
    try:
        global _whisper
        from faster_whisper import WhisperModel

        _whisper = WhisperModel(CFG["stt"].get("model", "base"), device="cpu", compute_type="int8")
        print("[app] STT 已预热", flush=True)
    except Exception:
        traceback.print_exc()


# ------------------------------------------------------------------ 主动消息
def proactive_loop():
    global _unread
    pc = CFG.get("proactive", {})
    if not pc.get("enabled"):
        return
    idle = float(pc.get("idle_minutes", 30)) * 60
    every = float(pc.get("interval_minutes", 10)) * 60
    lo, hi = pc.get("active_hours", [8, 23])
    while True:
        try:
            time.sleep(every)
            now = time.time()
            hour = time.localtime().hour
            if hour < lo or hour > hi or _unread or now - last_activity() < idle:
                continue
            ctx = "\n".join(f"{m['role']}：{m['content'][:60]}" for m in hist_tail(6)) or "（很久没聊了）"
            msg = clean_reply(llm_once([
                {"role": "system", "content": PERSONA},
                {"role": "user", "content": "（系统提示：现在空闲，你可以主动给他发一条消息，一到两句，"
                                            "随口一点。最近对话：\n" + ctx + "）"}], 200))
            if not msg:
                continue
            hist_add("assistant", msg)
            _unread.append({"text": msg, "ts": now})
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    print(f"[app] 人格 {len(PERSONA)} 字 | 模型 {LLM['model']} | TTS {'开' if TTS_CFG.get('endpoint') else '关'}"
          f" | 出图 {'开' if IMG_CFG.get('endpoint') else '关'} | 共享记忆 {'开' if lm_enabled() else '关'}"
          f" | 口令 {'开' if TOKEN else '关'}", flush=True)
    print(f"[app] http://{HOST}:{PORT}", flush=True)
    threading.Thread(target=proactive_loop, daemon=True).start()
    if CFG["stt"].get("enabled", True):
        threading.Thread(target=warm_stt, daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
