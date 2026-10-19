"""music-ears serve：黑胶播放器的后端。只用标准库。

  music-ears serve [--host 127.0.0.1] [--port 8790] [--token 口令]

浏览器打开 http://127.0.0.1:8790/ 就是播放器。手机要用的话 --host 0.0.0.0，并且必须设口令
（--token，或 config.json 里 "serve": {"token": "..."}），第一次打开带 ?key=口令，之后记在 cookie 里。

接口（都回 JSON，除了 /cover 和 /api/card）：
  GET  /api/vinyl?op=playlists|tracks|url|lyric|search&arg=…   网易云：歌单、歌、播放地址、歌词、搜索
  GET  /api/login?op=start | check&key=… | me                网易云扫码登录
  GET  /cover?u=网易云图片地址&s=100|200|500|512               封面代取（有些环境直连网易云图床会卡住），缓存在 cache/covers
  GET  /api/cards                                             拆过的卡
  GET  /api/card?dir=文件夹名                                  某张卡全文（text/markdown）
  POST /api/listen {id, name, artists}                        一起听：拆过就 ready，没拆过就后台拆（making）
  GET  /api/listen?id=…                                       一起听的状态：making / ready / failed / none
  POST /api/pos {id, pos, dur, line, playing, ended|stop}      播放器报位置
  GET  /api/now[?format=text]                                 给 AI 的：现在放到哪 + 乐谱卡里前后十秒
"""
import hashlib
import http.cookies
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

HOME = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
WEB = os.path.join(HOME, "web")
CACHE = os.path.join(HOME, "cache")
COVERS = os.path.join(CACHE, "covers")
CLI = os.path.join(HOME, "music-ears")
STATIC = {"index.html": "text/html; charset=utf-8", "vinyl.js": "text/javascript; charset=utf-8",
          "vinyl.css": "text/css; charset=utf-8", "qrcode.min.js": "text/javascript; charset=utf-8"}

_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 网易云图床在国内，直连
JOBS = {}     # 歌编号 → making / ready / failed
NOW = {}      # 一起听的那首：{id, name, artists, pos, dur, line, playing, at, ended}
LOCK = threading.Lock()


def cli(*args, timeout=40):
    r = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, timeout=timeout)
    return r


def cli_json(*args, timeout=40):
    try:
        return json.loads(cli(*args, timeout=timeout).stdout or "{}")
    except (subprocess.TimeoutExpired, ValueError):
        return {"ok": False, "error": "music-ears 没回话"}


def card_path(sid):
    for d in (sid, "rec-" + sid):
        p = os.path.join(CACHE, d, "card.md")
        if os.path.isfile(p):
            return p
    return None


_parsed = {}   # card.md → (mtime, 段落听感 [(起, 止, 字)], 逐句 [(秒, 歌词, [细节行])])


def card_sections(sid):
    """读乐谱卡「逐句」那节：段落听感和逐句。"""
    path = card_path(sid)
    if not path:
        return None
    mt = os.path.getmtime(path)
    hit = _parsed.get(path)
    if hit and hit[0] == mt:
        return hit[1:]
    sec = lambda t: int(t.split(":")[0]) * 60 + float(t.split(":")[1])
    parts, lines, on = [], [], False
    for ln in open(path, encoding="utf-8").read().splitlines():
        if ln.startswith("## 逐句"):
            on = True
            continue
        if not on:
            continue
        if ln.startswith("## "):
            break
        m = re.match(r"^〔(\d+:\d+)–(\d+:\d+) 听感〕(.*)$", ln)
        if m:
            parts.append((sec(m.group(1)), sec(m.group(2)), m.group(3).strip()))
            continue
        m = re.match(r"^\[(\d+:\d+)\] (.*)$", ln)
        if m:
            lines.append((sec(m.group(1)), m.group(2).strip(), []))
        elif ln.startswith("    ") and lines:
            lines[-1][2].append(ln.strip())
    _parsed[path] = (mt, parts, lines)
    return parts, lines


def window(sid, pos, span=10, dur=0):
    """放到的那个点前后十秒，从乐谱卡里摘：这几句的旋律、音域、听感，加上那一段的整体听感。"""
    got = card_sections(sid)
    if not got:
        return []
    parts, lines = got
    out = []
    part = next((p for p in parts if p[0] <= pos < p[1]), None)
    if part:
        out.append("这一段的听感：" + part[2][:160])
    for k, (t, text, info) in enumerate(lines):
        # 最后一句后面没有下一个时间戳：按歌曲总长截，别把结尾都算进最后一句
        end = lines[k + 1][0] if k + 1 < len(lines) else min(t + 8, dur or t + 8)
        if end < pos - span or t > pos + span:
            continue
        here = "（在这句）" if t <= pos < end else ""
        bits = []
        for x in info:
            if x.startswith("旋律："):
                notes = x[3:].split()
                bits.append("旋律 " + " ".join(notes[:20]) + (" …" if len(notes) > 20 else ""))
            elif x.startswith(("音域：", "听：", "长音")):
                bits.append(x.replace("：", " ", 1)[:60])
        out.append("[%d:%02d] %s%s ｜ %s" % (int(t) // 60, int(t) % 60, text, here, " ｜ ".join(bits)))
    return out


def now_info():
    """给 AI 的"现在放到哪"。没在一起听就 None。"""
    with LOCK:
        x = dict(NOW)
    if not x:
        return None
    fmt = lambda t: "%d:%02d" % (int(t) // 60, int(t) % 60)
    song = "《%s》%s" % (x.get("name", ""), " — " + " / ".join(x["artists"]) if x.get("artists") else "")
    who = config.name()
    if x.get("ended"):
        return {"state": "ended", "id": x["id"], "text": "〔一起听：%s那边%s放完了〕" % (who, song)}
    if not x.get("at"):
        return {"state": "starting", "id": x["id"], "text": "〔一起听：%s准备开始放%s〕" % (who, song)}
    pos, ago = x.get("pos") or 0, time.time() - x["at"]
    note = ""
    if x.get("playing"):
        pos += ago   # 上次报的是那时的位置，一直在放的话现在已经往后走了
        if x.get("dur") and pos > x["dur"]:
            return {"state": "stale", "id": x["id"], "text": "〔一起听：%s按时间算应该放完了（%d 分钟没报位置）〕" % (song, ago // 60)}
        if ago > 120:
            note = "（%d 分钟没报位置了，可能锁屏了，位置是估的）" % (ago // 60)
    text = "〔一起听：%s在放%s，放到 %s / %s" % (who, song, fmt(pos), fmt(x.get("dur") or 0))
    if x.get("line") and (not x.get("playing") or ago < 20):
        text += "，这句：「%s」" % x["line"]
    if not x.get("playing"):
        text += "（暂停着）"
    text += note
    win = window(x["id"], pos, dur=x.get("dur") or 0)
    if win:
        text += "\n前后十秒（摘自乐谱卡）：\n" + "\n".join("  " + w for w in win)
    return {"state": "playing" if x.get("playing") else "paused", "id": x["id"], "pos": round(pos, 1),
            "dur": x.get("dur") or 0, "line": x.get("line") or "", "window": win, "text": text + "〕"}


def start_job(sid):
    """后台拆这首（几分钟）。同一首不拆两遍。"""
    with LOCK:
        if JOBS.get(sid) == "making":
            return
        JOBS[sid] = "making"

    def go():
        try:
            r = cli(sid, timeout=1800)
            ok = r.returncode == 0 and card_path(sid)
            if not ok:
                sys.stderr.write("拆《%s》没成：%s\n" % (sid, (r.stderr or r.stdout or "")[-300:]))
        except subprocess.TimeoutExpired:
            ok = False
        with LOCK:
            JOBS[sid] = "ready" if ok else "failed"
    threading.Thread(target=go, daemon=True).start()


class H(BaseHTTPRequestHandler):
    server_version = "music-ears"

    def log_message(self, fmt, *a):
        pass

    def send(self, code, ctype, data, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if "json" in ctype or "markdown" in ctype else "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json(self, o, code=200):
        self.send(code, "application/json; charset=utf-8", json.dumps(o, ensure_ascii=False).encode())

    def authed(self, q):
        tok = self.server.token
        if not tok:
            return True
        got = (q.get("key") or [""])[0] or (self.headers.get("Authorization") or "").replace("Bearer ", "")
        if not got:
            c = http.cookies.SimpleCookie(self.headers.get("Cookie") or "")
            got = c["me_key"].value if "me_key" in c else ""
        return got == tok

    def route(self, method):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path.lstrip("/") or "index.html"
        if not self.authed(q):
            return self.json({"ok": False, "error": "要口令：第一次打开时在地址后面加 ?key=口令"}, 401)
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
            except ValueError:
                return self.json({"ok": False, "error": "要 JSON"}, 400)
        if method == "GET" and p in STATIC:
            extra = {}
            if p == "index.html" and q.get("key"):   # 带口令打开：记进 cookie，之后不用再带
                extra["Set-Cookie"] = "me_key=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax" % q["key"][0]
            with open(os.path.join(WEB, p), "rb") as f:
                return self.send(200, STATIC[p], f.read(), extra)
        if p == "cover":
            return self.cover(q)
        if p == "api/vinyl":
            op, arg = (q.get("op") or [""])[0], (q.get("arg") or [""])[0]
            if op not in ("playlists", "tracks", "url", "lyric", "search"):
                return self.json({"ok": False, "error": "不认识 " + op}, 400)
            return self.json(cli_json("vinyl", op, *([arg] if arg else [])))
        if p == "api/login":
            op = (q.get("op") or ["me"])[0]
            if op == "check":
                return self.json(cli_json("login", "check", (q.get("key") or [""])[0]))
            return self.json(cli_json("login", "start" if op == "start" else "me"))
        if p == "api/cards":
            return self.json(cli_json("list", "--json"))
        if p == "api/card":
            d = os.path.basename((q.get("dir") or [""])[0])
            path = os.path.join(CACHE, d, "card.md")
            if not d or not os.path.isfile(path):
                return self.json({"ok": False, "error": "没有这张卡"}, 404)
            with open(path, "rb") as f:
                return self.send(200, "text/markdown; charset=utf-8", f.read())
        if p == "api/listen":
            if method == "POST":
                sid = str(body.get("id") or "")
                if not sid.isdigit():
                    return self.json({"ok": False, "error": "要网易云歌曲编号"}, 400)
                with LOCK:
                    NOW.clear()
                    NOW.update(id=sid, name=str(body.get("name") or "")[:60], artists=[str(a)[:30] for a in (body.get("artists") or [])][:4])
                have = bool(card_path(sid))
                if not have:
                    start_job(sid)
                return self.json({"ok": True, "have": have})
            sid = (q.get("id") or [""])[0]
            st = "ready" if card_path(sid) else JOBS.get(sid, "none")
            return self.json({"ok": True, "state": st})
        if p == "api/pos" and method == "POST":
            sid = str(body.get("id") or "")
            with LOCK:
                if NOW.get("id") != sid:
                    return self.json({"ok": True, "together": False})
                if body.get("ended"):
                    NOW["ended"] = True
                elif body.get("stop"):
                    NOW.clear()
                else:
                    NOW.update(pos=float(body.get("pos") or 0), dur=float(body.get("dur") or 0),
                               line=str(body.get("line") or "")[:80], playing=bool(body.get("playing")), at=time.time())
            return self.json({"ok": True, "together": True})
        if p == "api/now":
            info = now_info()
            if (q.get("format") or [""])[0] == "text":
                return self.send(200, "text/plain; charset=utf-8", ((info or {}).get("text") or "").encode())
            return self.json({"ok": True, "now": info})
        return self.json({"ok": False, "error": "没有这个地址"}, 404)

    def cover(self, q):
        u = (q.get("u") or [""])[0]
        size = (q.get("s") or ["500"])[0]
        if not re.match(r"^https://p\d+\.music\.126\.net/[\w=+\-/]+\.(?:jpg|jpeg|png|webp)$", u) or size not in ("100", "200", "500", "512"):
            return self.json({"ok": False, "error": "not found"}, 404)
        path = os.path.join(COVERS, hashlib.sha1((u + "|" + size).encode()).hexdigest() + ".jpg")
        if not os.path.isfile(path):
            try:
                req = urllib.request.Request(u + "?param=%sy%s" % (size, size), headers={"User-Agent": "Mozilla/5.0"})
                with _direct.open(req, timeout=15) as r:
                    data = r.read(4 << 20)
            except Exception:
                return self.json({"ok": False, "error": "没取到"}, 502)
            os.makedirs(COVERS, exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
        with open(path, "rb") as f:
            self.send(200, "image/jpeg", f.read(), {"Cache-Control": "public, max-age=2592000"})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")


def main(a):
    host, port, token = "127.0.0.1", 8790, (config.load().get("serve") or {}).get("token") or ""
    if "--host" in a:
        host = a[a.index("--host") + 1]
    if "--port" in a:
        port = int(a[a.index("--port") + 1])
    if "--token" in a:
        token = a[a.index("--token") + 1]
    if host not in ("127.0.0.1", "localhost", "::1") and not token:
        raise SystemExit("让别的设备连（--host %s）必须设口令：--token 口令，或 config.json 里 \"serve\": {\"token\": \"...\"}。"
                         "口令可以用 openssl rand -hex 16 生成。" % host)
    srv = ThreadingHTTPServer((host, port), H)
    srv.token = token
    print("黑胶播放器：http://%s:%d/%s" % ("127.0.0.1" if host in ("0.0.0.0", "::") else host, port, "?key=口令" if token else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
