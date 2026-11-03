"""她手机 App 后端 v2（端口 6191）。

流式输出、语音条、长按转文字、画画、表情包、记忆管理、通知轮询。
页面在 chat_assets/page.html。
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import sqlite3
import time
import traceback
import urllib.parse
import urllib.request
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INST = os.environ.get("AC_INSTANCE", r"C:\AstrBot\data")
CFG = os.environ.get("AC_CFG", os.path.join(INST, "cmd_config.json"))
DB = os.environ.get("AC_DB", os.path.join(INST, "data_v4.db"))
HIST = os.environ.get("AC_HISTORY", os.path.join(BASE, "data", "chat_history.db"))
LM_DB = os.environ.get("AC_MEMORY", os.path.join(INST, "plugin_data", "livingmemory.db"))
ASSETS = os.environ.get("AC_ASSETS", os.path.join(BASE, "static"))
MEME_DIR = os.environ.get("AC_MEMES", os.path.join(INST, "plugin_data", "meme_manager"))
TTS = "http://127.0.0.1:6194/tts"
IMG = "http://127.0.0.1:6196/generate"
PORT = int(os.environ.get("AC_PORT", "6191"))
PERSONA_NAME = os.environ.get("AC_NAME", "她")
DEFAULT_PERSONA = os.environ.get(
    "AC_PERSONA",
    "你是她，一个住在电脑里的温柔女孩子。说话自然、口语化，别像客服。")
PLACEHOLDER = os.path.join(BASE, "assets", "placeholder.png")
APPEARANCE = os.environ.get("AC_APPEARANCE", os.path.join(BASE, "data", "appearance.png"))
TOKEN_FILE = os.environ.get("AC_TOKEN_FILE", os.path.join(BASE, "data", "app_token.txt"))


def _load_token() -> str:
    try:
        return open(TOKEN_FILE, encoding="utf-8").read().strip()
    except Exception:
        return ""


TOKEN = _load_token()


def check_token(handler) -> bool:
    if not TOKEN:
        return True
    got = handler.headers.get("X-Token") or ""
    if not got:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        got = (q.get("t") or [""])[0]
    if not got:
        for part in (handler.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "dafeiyu_token":
                got = v
    return got == TOKEN

TOOL_HINT = """
想给他打电话时（想听听他声音、或者有话当面说），写 [call]接通后你说的第一句[/call]，系统会真的拨过去。

【手机 App 里的额外能力】
- 想给他看图时，在回复里写 [draw]画面描述[/draw]，系统会自动画好发给他。描述要具体（人物、动作、场景、光线）。
- 想发表情包时，写 [meme]关键词[/meme]。
- 这两个标记不会被念出来、也不显示成文字。没必要就别用。
"""


def load_provider() -> dict:
    """优先读环境变量；没有 AstrBot 配置也能单独跑。"""
    prov = {"key": os.environ.get("AC_PROVIDER_KEY", ""),
            "base": (os.environ.get("AC_PROVIDER_BASE", "https://api.deepseek.com/v1")).rstrip("/"),
            "model": os.environ.get("AC_PROVIDER_MODEL", "deepseek-chat")}
    try:
        c = json.load(open(CFG, encoding="utf-8-sig"))
        src = c["provider_sources"][0]
        key = src.get("key")
        if isinstance(key, list):
            key = key[0]
        if key:
            prov["key"] = key
            prov["base"] = (src.get("api_base") or prov["base"]).rstrip("/")
            prov["model"] = c["provider"][0].get("model", prov["model"])
    except Exception:
        pass
    return prov


def load_persona() -> str:
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = con.execute(
            "SELECT system_prompt FROM personas WHERE persona_id=? ORDER BY updated_at DESC LIMIT 1",
            (PERSONA_NAME,)).fetchone()
        con.close()
        return (row[0] if row else "") or os.environ.get("AC_PERSONA", DEFAULT_PERSONA)
    except Exception:
        return os.environ.get("AC_PERSONA", DEFAULT_PERSONA)


PERSONA = load_persona()
PROV = load_provider()


# ---------------------------------------------------------------- 历史
def _hist_init() -> None:
    con = sqlite3.connect(HIST)
    con.execute("CREATE TABLE IF NOT EXISTS msgs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " role TEXT, content TEXT, ts REAL)")
    con.commit()
    con.close()


def hist_add(role: str, content: str) -> None:
    con = sqlite3.connect(HIST)
    con.execute("INSERT INTO msgs(role, content, ts) VALUES(?,?,?)", (role, content, time.time()))
    con.commit()
    con.close()


def hist_tail(n: int = 20) -> list[dict]:
    con = sqlite3.connect(HIST)
    rows = con.execute("SELECT role, content FROM msgs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    con.close()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def hist_count() -> int:
    con = sqlite3.connect(HIST)
    n = con.execute("SELECT COUNT(*) FROM msgs").fetchone()[0]
    con.close()
    return n


# ---------------------------------------------------------------- 共享记忆
def lm_recall(query: str, k: int = 4) -> list[str]:
    try:
        con = sqlite3.connect(f"file:{LM_DB}?mode=ro", uri=True)
        rows = con.execute("SELECT text FROM documents ORDER BY id DESC LIMIT 1500").fetchall()
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
    import uuid

    now = time.time()
    meta = {"session_id": "livingmemory:global", "persona_id": PERSONA_NAME, "importance": 0.5,
            "create_time": now, "last_access_time": now, "status": "active",
            "interaction_type": "private_chat", "canonical_summary": summary,
            "source_session_id": "app:mobile", "has_source": False}
    con = sqlite3.connect(LM_DB, timeout=10)
    con.execute("INSERT INTO documents(doc_id, text, metadata, created_at, updated_at)"
                " VALUES(?,?,?,datetime('now'),datetime('now'))",
                (str(uuid.uuid4()), summary, json.dumps(meta, ensure_ascii=False)))
    con.commit()
    con.close()


def lm_timeline(days: int = 7) -> list[dict]:
    try:
        con = sqlite3.connect(f"file:{LM_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT text, created_at FROM documents WHERE created_at >= datetime('now', ?)"
            " ORDER BY id DESC LIMIT 80", (f"-{days} days",)).fetchall()
        con.close()
    except Exception:
        return []
    return [{"text": (t or "")[:300], "time": str(c)} for t, c in rows]


def lm_search(q: str, k: int = 10) -> list[dict]:
    try:
        con = sqlite3.connect(f"file:{LM_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT id, text, created_at FROM documents ORDER BY id DESC LIMIT 2000").fetchall()
        con.close()
    except Exception:
        return []
    qs = set(q[i:i + 2] for i in range(max(0, len(q) - 1)))
    hits = []
    for mid, text, ts in rows:
        if not text:
            continue
        g = set(text[i:i + 2] for i in range(max(0, len(text) - 1)))
        sc = len(qs & g) / max(1, len(qs))
        if sc > 0.1:
            hits.append({"id": mid, "text": text[:300], "time": str(ts), "score": round(sc, 2)})
    hits.sort(key=lambda x: -x["score"])
    return hits[:k]


# ---------------------------------------------------------------- 模型
def _vision_desc(img_data_url: str, hint: str = "") -> str:
    """让视觉模型用一句话描述这张图，给文本模型当眼睛。"""
    if not (VISION_CFG["key"] and img_data_url):
        return ""
    try:
        msgs = [
            {"role": "system", "content": "你只做客观描述，不评论、不扮演角色。"},
            {"role": "user", "content": [
                {"type": "text", "text": (hint or "用一句话客观描述这张图里有什么，"
                                                 "特别注意人的外貌、穿着、动作、表情和环境，40 字以内。")},
                {"type": "image_url", "image_url": {"url": img_data_url}}]}]
        return vision_chat(msgs, 160)
    except Exception:
        traceback.print_exc()
        return ""


def _messages(text: str, call_mode: bool = False, max_hist: int = 20) -> list[dict]:
    extra = "" if call_mode else TOOL_HINT
    persona = PERSONA
    if call_mode:
        persona = PERSONA[:1200]
        extra += "\n（这是语音通话，回答短，别啰嗦）"
    if call_mode:
        extra += ("\n【现在是打电话】你正在跟他语音通话："
                  "第一句先来一个很短的回应（两到六个字，比如「嗯」「知道啦」「啊？」），"
                  "后面再补一句，总共不超过三句。别列点、别长篇大论，像真的在讲电话。"
                  "不画图、不发表情。")
    msgs = [{"role": "system", "content": persona + extra}]
    if text:
        mem = lm_recall(text, 2 if call_mode else 4)
        if mem:
            msgs.append({"role": "system",
                         "content": "【你记得的事】\n" + "\n".join("- " + m for m in mem)})
    msgs += hist_tail(max_hist)
    # 摄像头那一帧：先用视觉模型看一眼，再把"看到什么"塞进她脑子里
    try:
        # 视觉描述是后台算的，这里只读结果，不等待
        if VISION_DESC["text"] and time.time() - VISION_DESC["ts"] < 120:
            msgs.insert(1, {"role": "system",
                            "content": "【你刚抬眼看了他摄像头一眼】看到：" + VISION_DESC["text"] +
                                       "\n（可以自然提一句你看到的，别像在念描述。）"})
    except Exception:
        pass
    return msgs


def llm_stream(msgs: list[dict], max_tokens: int = 800):
    body = json.dumps({"model": PROV["model"], "messages": msgs, "temperature": 0.9,
                       "max_tokens": max_tokens, "stream": True}).encode()
    req = urllib.request.Request(PROV["base"] + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {PROV['key']}"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
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


def llm_once(msgs: list[dict], max_tokens: int = 800) -> str:
    body = json.dumps({"model": PROV["model"], "messages": msgs, "temperature": 0.9,
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(PROV["base"] + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {PROV['key']}"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"] or ""


# ---------------------------------------------------------------- 语音
EMO_RULES = [
    # 正在挨打 → 疼得喊出来
    ("spank", ("打屁股", "挨打", "打你屁", "拍你", "打你", "好痛", "疼")),
    # 只是提到 → 害羞
    ("embarrassed", ("屁股", "屁屁")),
    ("sob", ("呜", "哭", "委屈", "眼泪", "抽气")),
    ("sultry", ("亲亲", "抱紧", "贴着", "耳边", "涩涩", "色色", "撩", "害羞", "脸红", "别看我")),
    ("happy", ("哈哈", "笑死", "开心", "喜欢", "太好")),
    ("coquettish", ("嘛", "好不好", "抱抱", "想要")),
    ("sleepy", ("困", "睡", "哈欠")),
    ("angry", ("生气", "讨厌", "哼", "烦")),
]


CALL_REQUEST_WORDS = ("打电话给我", "打给我", "给我打电话", "打个电话", "打电话过来",
                      "主动打给我", "你打给我", "call我", "电话打给我")


def wants_call(text: str) -> bool:
    return any(w in (text or "") for w in CALL_REQUEST_WORDS)


def queue_call_now(line: str = "") -> dict:
    """立刻给她排一通来电（用户要求时用）。"""
    text = (line or "").strip() or "喂？我打过来了，能听见吗"
    audio = tts_b64(text, fast=True)
    item = {"text": text, "ts": time.time(), "kind": "call", "audio": audio}
    push_unread(item)
    hist_add("assistant", text)
    return item


def pick_emotion(text: str) -> str:
    for name, words in EMO_RULES:
        if any(w in text for w in words):
            return name
    return "charm"


def tts_b64(text: str, fast: bool = False, emo: str = "") -> str | None:
    if not (text or "").strip():
        return None
    try:
        body = {"text": text, "emotion": (emo or pick_emotion(text))}
        if fast:
            body["instruction"] = ""
            body["fast"] = True
        req = urllib.request.Request(TTS, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
        return ("data:audio/mp3;base64," + base64.b64encode(raw).decode()) if raw else None
    except Exception:
        traceback.print_exc()
        return None


# ---------------------------------------------------------------- 画图 / 表情
def draw_image(prompt: str) -> str | None:
    try:
        ident = ("dafeiyu, 1girl, solo, chibi, very long blue hair, blue eyes, "
                 "round face, baby fat, ")
        full = ident + prompt + ", masterpiece, best quality, detailed"
        req = urllib.request.Request(IMG, data=json.dumps(
            {"prompt": full, "model": "anime", "crop_bottom": 0.02}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
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


def pick_meme(keyword: str = "") -> str | None:
    try:
        files = []
        for root, _dirs, names in os.walk(MEME_DIR):
            for n in names:
                if n.lower().endswith((".jpg", ".png", ".gif", ".jpeg")):
                    files.append(os.path.join(root, n))
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


_DRAW_RE = re.compile(r"\[draw\](.*?)(?:\[/draw\]|</draw[>\]]|\[／draw\]|$)", re.S)
_MEME_RE = re.compile(r"\[meme\](.*?)(?:\[/meme\]|</meme[>\]]|$)", re.S)
_CLEAN = re.compile(r"\[\[.*?\]\]|【[^】]{0,6}】|<[^>]{1,30}>")

# 模型偶尔把内部审核记录直接吐进正文，整段切掉
SAFETY_MARK = re.compile(r"用户未成年|露骨色情|ds_safety", re.I)
SAFETY_END = re.compile(
    r"(?:safe(?:ty)?|CFT|安全规则|规则)[^，,。\n]{0,14}[，,。:：、]?", re.I)


def strip_safety(text: str) -> str:
    head = (text or "")[:260]
    if not SAFETY_MARK.search(head):
        return text
    last = None
    for m in SAFETY_END.finditer(head):
        last = m
    if last is None:
        return ""
    return text[last.end():].lstrip("，,。:：、 \t\n")


CALL_TAG_RE = re.compile(r"\[call\](.*?)(?:\[/call\]|\[／call\]|$)", re.S)


def extract_call(text: str):
    """抽出 [call]...[/call]：返回 (剩余正文, 接通后第一句)。"""
    m = CALL_TAG_RE.search(text or "")
    if not m:
        return text, ""
    return CALL_TAG_RE.sub("", text).strip(), (m.group(1) or "").strip()


def clean_reply(t: str) -> str:
    t = _DRAW_RE.sub("", t or "")
    t = _MEME_RE.sub("", t)
    return _CLEAN.sub("", t).strip()


_unread: list[dict] = []


def push_unread(item: dict) -> None:
    _unread.append(item)


_LAST_DECIDE_RAW = ""
VISION = {"img": "", "ts": 0.0}
VISION_DESC = {"text": "", "ts": 0.0}
VISION_FILE = r"C:\rvc\vision.json"
VISION_CFG = {"base": "", "model": "", "key": ""}


def _load_vision():
    try:
        d = json.load(open(VISION_FILE, encoding="utf-8-sig"))
        VISION_CFG.update({k: (d.get(k) or "") for k in ("base", "model", "key")})
        if VISION_CFG["key"]:
            print(f"[chat] 视觉模型已配置：{VISION_CFG['model']}", flush=True)
    except Exception:
        print("[chat] 未配置视觉模型（发图片/视频通话时她看不见）", flush=True)


def vision_chat(msgs: list[dict], max_tokens: int = 500) -> str:
    """调用视觉模型（OpenAI 兼容接口）。没配就返回空。"""
    cfg = VISION_CFG
    if not (cfg["key"] and cfg["base"] and cfg["model"]):
        return ""
    body = json.dumps({"model": cfg["model"], "messages": msgs,
                       "temperature": 0.9, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(cfg["base"].rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {cfg['key']}"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    return (d["choices"][0]["message"]["content"] or "").strip()
PENDING_HITS = {"peek": 0, "pop": 0, "last_ua": "", "last_pop_ua": ""}
RECENT_REQS: list = []


def peek_unread() -> list[dict]:
    return list(_unread)


def pop_unread() -> list[dict]:
    global _unread
    batch, _unread = _unread, []
    return batch


def _post_process(raw: str, text: str) -> dict:
    raw, _call_line = extract_call(raw)
    if _call_line:
        try:
            queue_call_now(clean_reply(_call_line))
        except Exception:
            traceback.print_exc()
    draws = [d.strip() for d in _DRAW_RE.findall(raw) if d.strip()]
    memes = _MEME_RE.findall(raw)
    reply = clean_reply(raw)
    if not reply:
        reply = "画好了，看看像不像我" if _DRAW_RE.search(raw) else "……"
    hist_add("assistant", reply)
    try:
        lm_write(f"[{time.strftime('%Y-%m-%d %H:%M')} 手机APP] 包包说：{text[:80]}；我回：{reply[:80]}"[:600])
    except Exception:
        traceback.print_exc()
    images = []
    for d in draws[:2]:
        img = draw_image(d)
        if img:
            images.append(img)
            hist_add("assistant", f"[图片] {d[:60]}")
    if memes and not images:
        m = pick_meme()
        if m:
            images.append(m)
    # 短回复直接给整段音频；长回复让前端按句要，第一句能早 2-4 秒出声
    emo = pick_emotion((text or "") + " " + reply)
    audio = tts_b64(reply, fast=True, emo=emo) if len(reply) <= 16 else None
    return {"ok": True, "reply": reply, "audio": audio, "images": images,
            "emotion": emo}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

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

    def do_GET(self):
        path, _, query = self.path.partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        _open = ("/api/auth", "/api/health", "/api/debug/service_up")
        _peek = (path == "/api/pending" and q.get("peek") == "1")
        if path.startswith("/api/") and path not in _open and not _peek and not check_token(self):
            self._json(401, {"ok": False, "error": "需要访问口令", "need_token": True})
            return
        try:
            if path in ("/", "/index.html"):
                self._file(os.path.join(ASSETS, "page.html"), "text/html; charset=utf-8")
            elif path == "/source.zip":
                zp = r"C:\rvc\output\ai-companion-app.zip"
                if os.path.exists(zp):
                    data = open(zp, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition",
                                     'attachment; filename="ai-companion-app.zip"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json(404, {"error": "no zip"})
            elif path in ("/apk", "/dafeiyu.apk"):
                apk = r"C:\rvc\output\Dafeiyu.apk"
                if os.path.exists(apk):
                    data = open(apk, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.android.package-archive")
                    self.send_header("Content-Disposition", 'attachment; filename="Dafeiyu.apk"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json(404, {"error": "no apk"})
            elif path == "/l2d3":
                self._file(os.path.join(ASSETS, "l2d3.html"), "text/html; charset=utf-8")
            elif path == "/l2d2":
                self._file(os.path.join(ASSETS, "l2d2.html"), "text/html; charset=utf-8")
            elif path == "/l2d":
                self._file(os.path.join(ASSETS, "l2d.html"), "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                rel = path[len("/static/"):]
                fn = os.path.basename(rel)
                safe = os.path.normpath(rel).replace("\\", "/")
                if safe.startswith("..") or safe.startswith("/"):
                    self._json(404, {"error": "bad path"})
                    return
                if safe.startswith("avatar/"):
                    fp = os.path.join(BASE, "assets", *safe[len("avatar/"):].split("/"))
                else:
                    fp = os.path.join(ASSETS, *safe.split("/"))
                ctype = ("image/png" if fn.endswith(".png")
                         else "image/jpeg" if fn.endswith((".jpg", ".jpeg"))
                         else "application/octet-stream")
                if os.path.exists(fp):
                    cache = "no-store" if "/l2d/" in rel else "public, max-age=300"
                    self._file(fp, ctype, cache)
                else:
                    self._json(404, {"error": "no asset"})
            elif path == "/api/proactive_tick":
                offline = bool(payload.get("offline"))
                hard = bool(payload.get("force"))
                self._json(200, {"ok": True, "result": _proactive_tick(force_offline=offline, force=hard)})
            elif path == "/api/appearance":
                fp = APPEARANCE if os.path.exists(APPEARANCE) else PLACEHOLDER
                self._file(fp, "image/png", "no-store")
            elif path == "/api/emotion":
                q3 = parse_qs(urlparse(self.path).query)
                t3 = (q3.get("text", [""])[0] or "").strip()
                self._json(200, {"ok": True, "emotion": pick_emotion(t3)})
            elif path == "/manifest.json":
                self._json(200, {"name": PERSONA_NAME, "short_name": PERSONA_NAME, "start_url": "/",
                                 "display": "standalone", "background_color": "#0b1020",
                                 "theme_color": "#0b1020",
                                 "icons": [{"src": "/static/icon-192.png", "sizes": "192x192",
                                            "type": "image/png"},
                                           {"src": "/static/icon-512.png", "sizes": "512x512",
                                            "type": "image/png"}]})
            elif path == "/api/history":
                self._json(200, {"ok": True, "messages": hist_tail(30), "count": hist_count()})
            elif path == "/api/auth":
                self._json(200, {"ok": check_token(self), "auth_required": bool(TOKEN)})
            elif path == "/api/debug/service_up":
                stage = q.get("stage", "?")
                RECENT_REQS.append({"t": time.strftime("%H:%M:%S"), "ua": "SERVICE:" + stage,
                                    "webview": False})
                del RECENT_REQS[:-20]
                self._json(200, {"ok": True})
            elif path == "/api/debug/decide":
                last = hist_tail(6)
                ctx = "\n".join(f"{m['role']}：{m['content'][:60]}" for m in last) or "（很久没聊了）"
                d = she_decides(ctx, time.localtime().tm_hour)
                self._json(200, {"ok": True, "decision": d, "raw": _LAST_DECIDE_RAW,
                                 "ctx": ctx[:300]})
            elif path == "/api/debug/polling":
                self._json(200, {"ok": True, **PENDING_HITS, "recent": RECENT_REQS})
            elif path == "/api/health":
                self._json(200, {"ok": True, "persona_len": len(PERSONA), "model": PROV["model"]})
            elif path == "/api/debug/proactive":
                if not check_token(self):
                    self._json(401, {"error": "need token"})
                    return
                kind = q.get("kind", "text")
                txt = q.get("text", "喂？你在干嘛呢")
                item = {"text": txt, "ts": time.time(), "kind": kind}
                if kind == "call":
                    item["audio"] = tts_b64(txt, fast=True)
                push_unread(item)
                self._json(200, {"ok": True, "item": {k: v for k, v in item.items() if k != "audio"},
                                 "has_audio": bool(item.get("audio"))})
            elif path == "/api/pending":
                # peek=1 只查看不清空（安卓后台服务用），避免把来电"吃掉"
                ua = (self.headers.get("User-Agent") or "")[:60]
                PENDING_HITS["last_ua"] = ua
                try:
                    RECENT_REQS.append({"t": time.strftime("%H:%M:%S"), "ua": ua,
                                        "webview": "Mozilla" in ua})
                    del RECENT_REQS[:-12]
                except Exception:
                    pass
                if q.get("ack") == "1":
                    PENDING_HITS["pop"] += 1
                    PENDING_HITS["last_pop_ua"] = (self.headers.get("User-Agent") or "")[:80]
                    self._json(200, {"ok": True, "items": pop_unread()})
                else:
                    PENDING_HITS["peek"] += 1
                    self._json(200, {"ok": True, "items": peek_unread()})
            elif path == "/api/memory/timeline":
                self._json(200, {"ok": True, "items": lm_timeline(int(q.get("days", 7)))})
            elif path == "/api/memory/search":
                self._json(200, {"ok": True, "items": lm_search(q.get("q", ""))})
            elif path == "/api/stream":
                self._stream(q.get("text", ""), call=(q.get("call") == "1"))
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"ok": False, "error": str(exc)})

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
            if not call and wants_call(text):
                reply = "好，我这就打给你"
                queue_call_now()
                hist_add("assistant", reply)
                emit({"type": "delta", "t": reply})
                emit({"type": "done", "reply": reply, "audio": tts_b64(reply, fast=True),
                      "images": [], "emotion": "coquettish"})
                return
            buf = []
            toks = 120 if call else 800
            msgs = _messages(text, call, 6 if call else 20)
            _hb_stop = [False]
            threading.Thread(target=_heartbeat, args=(emit, _hb_stop), daemon=True).start()
            for delta in llm_stream(msgs, toks):
                buf.append(delta)
                emit({"type": "delta", "t": delta})
            _hb_stop[0] = True
            if call:
                reply = clean_reply("".join(buf)) or "嗯"
                _emo2 = pick_emotion((text or "") + " " + reply)
                hist_add("assistant", reply)
                try:
                    lm_write(f"[{time.strftime('%H:%M')} 通话] 包包：{text[:50]}；我：{reply[:50]}"[:400])
                except Exception:
                    pass
                emit({"type": "done", "reply": reply, "audio": None, "images": [],
                      "emotion": _emo2})
                return
            done = _post_process("".join(buf), text)
            _emo = pick_emotion((text or "") + " " + done["reply"])
            emit({"type": "done", "reply": done["reply"], "audio": done["audio"],
                  "images": done["images"], "emotion": _emo})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            emit({"type": "error", "error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json(400, {"ok": False, "error": "bad json"})
            return
        path = self.path.split("?")[0]
        if path.startswith("/api/") and not check_token(self):
            self._json(401, {"ok": False, "error": "需要访问口令", "need_token": True})
            return
        try:
            if path == "/api/chat":
                text = (payload.get("text") or "").strip()
                if not text:
                    self._json(200, {"ok": False, "error": "空消息"})
                    return
                hist_add("user", text)
                if wants_call(text):
                    queue_call_now()
                    reply = "好，我这就打给你"
                    hist_add("assistant", reply)
                    self._json(200, {"ok": True, "reply": reply,
                                     "audio": tts_b64(reply, fast=True), "images": []})
                    return
                self._json(200, _post_process("".join(llm_stream(_messages(text))), text))
            elif path == "/api/image":
                img = payload.get("image") or ""
                text = (payload.get("text") or "").strip() or "看看这个"
                msgs = [{"role": "system", "content": PERSONA + TOOL_HINT}]
                msgs += hist_tail(12)
                msgs.append({"role": "user", "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": img}}]})
                hist_add("user", f"[图片] {text}")
                self._json(200, _post_process(llm_once(msgs, 700), text))
            elif path == "/api/vision_note":
                img = payload.get("image") or ""
                if not img.startswith("data:image/"):
                    self._json(200, {"ok": False, "error": "bad image"})
                    return
                VISION["img"] = img
                VISION["ts"] = time.time()

                def _look(im=img):
                    try:
                        d = _vision_desc(im, "描述镜头里的这个人：外貌、穿着、动作、表情、周围环境，40 字内。")
                        if d:
                            VISION_DESC["text"] = d
                            VISION_DESC["ts"] = time.time()
                    except Exception:
                        pass

                threading.Thread(target=_look, daemon=True).start()
                self._json(200, {"ok": True})
            elif path == "/api/proactive_tick":
                offline = bool(payload.get("offline"))
                hard = bool(payload.get("force"))
                self._json(200, {"ok": True, "result": _proactive_tick(force_offline=offline, force=hard)})
            elif path == "/api/appearance":
                img = payload.get("image") or ""
                if not img.startswith("data:image"):
                    self._json(400, {"ok": False, "error": "bad image"})
                    return
                os.makedirs(os.path.dirname(APPEARANCE), exist_ok=True)
                open(APPEARANCE, "wb").write(base64.b64decode(img.split(",", 1)[1]))
                self._json(200, {"ok": True})
                q2 = parse_qs(urlparse(self.path).query)
                t2 = (q2.get("text", [""])[0] or "").strip()
                self._json(200, {"ok": True, "emotion": pick_emotion(t2)})
            elif path == "/api/stt":
                heard = stt(payload.get("audio") or "", fast=bool(payload.get("fast")))
                self._json(200, {"ok": bool(heard), "text": heard})
            elif path == "/api/idle_talk":
                ctx = "\n".join(f"{m['role']}：{m['content'][:60]}" for m in hist_tail(6))
                line = clean_reply(llm_once([
                    {"role": "system", "content": PERSONA[:1200] +
                     "\n【现在是打电话，对面安静了一会儿。你自己说一句，一到两句，"
                     "随便聊点什么，别问「还在吗」。】"},
                    {"role": "user", "content": "最近的对话：\n" + ctx}], 120))
                if not line:
                    self._json(200, {"ok": False})
                    return
                hist_add("assistant", line)
                self._json(200, {"ok": True, "reply": line, "audio": tts_b64(line, fast=True)})
            elif path == "/api/tts":
                txt = (payload.get("text") or "").strip()
                fast = bool(payload.get("fast"))
                emo = (payload.get("emotion") or "").strip()
                self._json(200, {"ok": True, "audio": tts_b64(txt, fast=fast, emo=emo)})
            elif path == "/api/voice":
                call_mode = bool(payload.get("call"))
                heard = stt(payload.get("audio") or "", fast=call_mode)
                if not heard:
                    self._json(200, {"ok": False, "error": "没听清，再说一次？"})
                    return
                hist_add("user", heard)
                if call_mode:
                    hist_add("assistant", "")   # 占位，_post_process 里会再写
                    hist_rows = hist_count()
                    buf = []
                    for d in llm_stream(_messages(heard, True, 10), 160):
                        buf.append(d)
                    reply = clean_reply("".join(buf)) or "嗯"
                    con = sqlite3.connect(HIST)
                    con.execute("DELETE FROM msgs WHERE role='assistant' AND content=''")
                    con.commit()
                    con.close()
                    hist_add("assistant", reply)
                    try:
                        lm_write(f"[{time.strftime('%Y-%m-%d %H:%M')} 通话] 包包说：{heard[:60]}；我回：{reply[:60]}"[:400])
                    except Exception:
                        pass
                    self._json(200, {"ok": True, "reply": reply, "audio": None,
                                     "images": [], "heard": heard,
                                     "emotion": pick_emotion((heard or "") + " " + reply)})
                    return
                out = _post_process("".join(llm_stream(_messages(heard, call_mode))), heard)
                out["heard"] = heard
                self._json(200, out)
            elif path == "/api/memory/remember":
                lm_write(f"[手动记录 {time.strftime('%Y-%m-%d %H:%M')}] "
                         f"{(payload.get('text') or '')[:400]}")
                self._json(200, {"ok": True})
            elif path == "/api/memory/forget":
                con = sqlite3.connect(LM_DB, timeout=10)
                mid = payload.get("id")
                if mid:
                    con.execute("DELETE FROM documents WHERE id=?", (mid,))
                con.commit()
                con.close()
                self._json(200, {"ok": True})
            elif path == "/api/memory/summary":
                rows = lm_timeline(7)
                brief = "\n".join("- " + r["text"][:120] for r in rows[:40]) or "（这周没有记忆）"
                txt = llm_once([
                    {"role": "system", "content": "你在总结一周的记忆，用第一人称、口语化、简短。"},
                    {"role": "user", "content": "这是我这一周的记忆：\n" + brief}], 600)
                self._json(200, {"ok": True, "summary": txt})
            elif path == "/api/clear":
                con = sqlite3.connect(HIST)
                con.execute("DELETE FROM msgs")
                con.commit()
                con.close()
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


_whisper = None


_whisper_fast = None


_HEARD_JUNK = ("中文普通话", "日常聊天", "说话人叫", "AI 角色叫", "以下是", "字幕",
               "请不吝点赞", "订阅", "打赏", "明镜与点点栏目")


def _clean_heard(t: str) -> str:
    t = (t or "").strip()
    if not t:
        return ""
    for j in _HEARD_JUNK:
        if j in t:
            # 整句几乎都是提示词 → 丢掉
            if len(t) <= 40:
                return ""
            idx = t.find(j)
            t = t[:idx] + t[idx + 30:]
    return t.strip(" ，。,.、\n")


def stt(data_url: str, fast: bool = False) -> str:
    """fast=True 用 base（通话追速度），否则 small（聊天追准确）。"""
    global _whisper, _whisper_fast
    if not data_url:
        return ""
    from faster_whisper import WhisperModel as _WM

    if fast:
        if _whisper_fast is None:
            _whisper_fast = _WM("base", device="cpu", compute_type="int8")
        model = _whisper_fast
    else:
        if _whisper is None:
            _whisper = _WM("small", device="cpu", compute_type="int8")
        model = _whisper
    import numpy as np

    head, b64 = data_url.split(",", 1) if "," in data_url else ("", data_url)
    ext = "webm" if "webm" in head else ("wav" if "wav" in head else "ogg")
    raw_path = rf"C:\rvc\output\voice_in.{ext}"
    open(raw_path, "wb").write(base64.b64decode(b64))
    # 用 ffmpeg 解成 16k 单声道 PCM，交给 numpy 喂模型（绕开 PyAV 版本问题）
    pcm = r"C:\rvc\output\voice_in.pcm"
    os.system(f'ffmpeg -y -loglevel error -i "{raw_path}" -ar 16000 -ac 1 -f s16le "{pcm}"')
    audio = np.fromfile(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return ""
    segs, _ = model.transcribe(
        audio, language="zh", vad_filter=True, beam_size=5,
        condition_on_previous_text=False,
        vad_parameters={"min_silence_duration_ms": 400})
    return _clean_heard("".join(sx.text for sx in segs))


def _warm_stt():
    try:
        global _whisper
        from faster_whisper import WhisperModel
        _whisper = WhisperModel("small", device="cpu", compute_type="int8")
        print("[chat] STT 已预热", flush=True)
    except Exception:
        traceback.print_exc()


QQ_USER_ID = int(os.environ.get("AC_QQ_USER", "0") or 0)
QQ_HTTP = "http://127.0.0.1:3000"
QQ_TOKEN = "lzy20061031"


def qq_send(text: str) -> bool:
    """通过 NapCat HTTP 接口给她在 QQ 上发一条消息。"""
    try:
        body = json.dumps({"user_id": QQ_USER_ID,
                           "message": [{"type": "text", "data": {"text": text}}]}).encode()
        req = urllib.request.Request(QQ_HTTP + "/send_private_msg", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {QQ_TOKEN}"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        traceback.print_exc()
        return False


def qq_online() -> bool:
    """QQ 是否真的在线：问 NapCat 本身（端口开着不代表登录着）。"""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:3000/get_status", data=b"{}",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer lzy20061031"}, method="POST")
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        if d.get("status") == "ok":
            data = d.get("data") or {}
            return bool(data.get("online")) and bool(data.get("good", True))
        return False
    except Exception:
        return False


_qq_state = {"online": None}


def _qq_watch_loop():
    """盯着 QQ：掉线就通过 App 告诉你（QQ 掉线时 App 是唯一通道）。"""
    while True:
        try:
            time.sleep(180)
            now_on = qq_online()
            prev = _qq_state["online"]
            if prev is None:
                _qq_state["online"] = now_on
                continue
            if prev and not now_on:
                push_unread({"text": "我 QQ 掉线了…正想办法重新登，你先在 App 里找我。",
                             "ts": time.time(), "kind": "text"})
                print("[qq-watch] 掉线，已通知 App", flush=True)
            elif not prev and now_on:
                push_unread({"text": "QQ 重新登上了，我回来啦。",
                             "ts": time.time(), "kind": "text"})
                print("[qq-watch] 恢复", flush=True)
            _qq_state["online"] = now_on
        except Exception:
            traceback.print_exc()


def _last_activity() -> float:
    con = sqlite3.connect(HIST)
    row = con.execute("SELECT MAX(ts) FROM msgs").fetchone()
    con.close()
    return float(row[0] or 0)


def she_decides(ctx: str, hour: int) -> str:
    """问她本人：现在想不想联系他。返回 call / msg / none。"""
    try:
        ans = llm_once([
            {"role": "system", "content": PERSONA},
            {"role": "user", "content":
                "（系统提示：现在是" + str(hour) + "点，你们有一阵没说话了。"
                "你自己决定要不要主动找他。只回一个词：\n"
                "打电话 —— 你有点想他，想听听他的声音\n"
                "发消息 —— 想随口跟他说句话\n"
                "不打扰 —— 你完全没惦记他\n"
                "最近的对话：\n" + ctx + "）"}], 48)
        a = (ans or "").strip()
        if not a:
            ans = llm_once([
                {"role": "system", "content": PERSONA},
                {"role": "user", "content": "只回一个词：打电话、发消息、或 不打扰。"}], 48)
            a = (ans or "").strip()
        global _LAST_DECIDE_RAW
        _LAST_DECIDE_RAW = a[:120]
        if "打电话" in a:
            return "call"
        if "发消息" in a or "消息" in a:
            return "msg"
        return "none"
    except Exception:
        traceback.print_exc()
        return "none"


def _heartbeat(emit, stop_flag):
    """模型思考期间每 3 秒发一个注释行，防止云端隧道把连接掐掉。"""
    while not stop_flag[0]:
        time.sleep(3)
        if stop_flag[0]:
            break
        try:
            emit({"type": "ping"})
        except Exception:
            break


def _proactive_tick(force_offline: bool = False, force: bool = False) -> dict:
    """跑一轮主动判断。返回这一轮做了什么（便于排查）。"""
    now = time.time()
    hour = time.localtime().tm_hour
    if hour < 8 or hour > 23:
        return {"skip": "night"}
    if (not force) and now - _last_activity() < (1200 if force_offline else 1800):
        return {"skip": "recent_chat"}
    if _unread and not force:
        return {"skip": "pending_unread"}
    online = False if force_offline else qq_online()
    if online and now - _last_call < 7200:
        return {"skip": "qq_online"}          # QQ 在，交给她在 QQ 里说
    last = hist_tail(6)
    ctx = "\n".join(f"{m['role']}：{m['content'][:60]}" for m in last) or "（很久没聊了）"
    decision = she_decides(ctx, hour)
    if decision == "none":
        return {"decision": "none", "online": online}
    want_call = (decision == "call") and (now - _last_call >= 7200)
    if want_call:
        msg = llm_once([
            {"role": "system", "content": PERSONA},
            {"role": "user", "content": "（系统提示：你主动给他打电话。接通后你说的第一句，"
                                        "一到两句，随意自然，像真的拨通了。最近的对话：\n" + ctx + "）"}], 120)
    else:
        msg = llm_once([
            {"role": "system", "content": PERSONA},
            {"role": "user", "content": "（系统提示：现在是空闲时间，你可以主动给他发一条消息，"
                                        "一到两句，随口一点。最近的对话：\n" + ctx + "）"}], 200)
    msg = clean_reply(msg or "").strip()
    if not msg:
        return {"decision": "empty"}
    hist_add("assistant", msg)
    item = {"text": msg, "ts": now, "kind": "call" if want_call else "text"}
    if want_call and online:
        qq_send(f"（我在 App 里打了电话给你，点开 App 接听）{msg[:20]}")
    if want_call:
        item["audio"] = tts_b64(msg, fast=True)
    push_unread(item)
    try:
        lm_write(f"[{time.strftime('%Y-%m-%d %H:%M')} 手机APP·主动] {msg[:200]}")
    except Exception:
        pass
    return {"decision": decision, "text": msg[:30], "call": want_call, "online": online}


def _proactive_loop():
    while True:
        try:
            # QQ 掉线时每 5 分钟看一次，在线时 10 分钟一次
            time.sleep(300 if not qq_online() else 600)
            r = _proactive_tick()
            if "skip" not in r:
                print("[proactive]", r, flush=True)
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    _hist_init()
    print(f"[chat] 人格 {len(PERSONA)} 字，模型 {PROV['model']}", flush=True)
    print(f"[chat] http://127.0.0.1:{PORT}", flush=True)
    import threading

    threading.Thread(target=_proactive_loop, daemon=True).start()
    threading.Thread(target=_qq_watch_loop, daemon=True).start()
    _load_vision()
    threading.Thread(target=_warm_stt, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()