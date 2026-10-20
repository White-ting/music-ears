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
  POST /api/listen {id, name, artists}                        一起听（要先有卡：没拆过回 need_card）
  GET  /api/listen?id=…                                       这首卡的状态：ready / queued / making / failed / none
  POST /api/make {id, name, dur}                              拆乐谱卡：只拆、存着（一次拆一首，排队）
  GET  /api/queue                                             拆歌进度：making{name, elapsed, est}、queued、done
  POST /api/pos {id, pos, dur, line, playing, ended|stop}      播放器报位置
  GET  /api/now[?format=text][&mode=window|book]             给 AI 的：现在放到哪 + 前后十秒摘录（window）或书签（book）
  GET  /api/cmd?since=序号                                     播放器问：AI 有没有点歌、换没换外观
  GET  /skin.css、/skin-img/名字/bg.xxx                        外观（docs/skin.md）
  POST /mcp                                                   MCP（streamable HTTP，只回 JSON）：搜歌、拆歌、读卡、一起听
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
import skin

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
CMD = {"seq": 0}   # AI 点的歌（MCP 的 play）：{seq, track, mode, note, at}；播放器问 /api/cmd 拿
SEEN = {"at": 0}   # 播放器上次来问是什么时候（判断它开没开着）
SKINV = {"v": int(time.time())}   # 外观换了就加一，播放器看到变了重新拿 /skin.css
BOOT = "%x" % int(time.time())   # 这次启动的编号：重启后序号从 0 数，播放器靠它知道要重新数
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
    """读乐谱卡「逐句」那节：段落听感和逐句。sid 也可以直接给 card.md 的路径。"""
    path = sid if str(sid).endswith("card.md") and os.path.isfile(sid) else card_path(sid)
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


def listen_mode(want=None):
    """一起听怎么报进度：window 每次附前后十秒摘录（默认）；book 先给整张卡、之后只报书签。
    调用时给的优先，其次 config.json 的 "serve": {"listen_mode": "book"}。"""
    m = (want or (config.load().get("serve") or {}).get("listen_mode") or "window").strip()
    return m if m in ("window", "book") else "window"


def bookmark(sid, pos, dur=0):
    """书签：第几句、卡里哪一行。没有逐句就空。"""
    got = card_sections(sid)
    if not got or not got[1]:
        return ""
    lines = got[1]
    for k, (t, text, _) in enumerate(lines):
        end = lines[k + 1][0] if k + 1 < len(lines) else min(t + 8, dur or t + 8)
        if t <= pos < end:
            return "第 %d/%d 句（卡里 [%d:%02d] 那行）「%s」" % (k + 1, len(lines), int(t) // 60, int(t) % 60, text)
        if pos < t:
            return "第 %d 句之前的间奏（下一句是卡里 [%d:%02d]）" % (k + 1, int(t) // 60, int(t) % 60)
    return "最后一句之后（尾奏）"


def now_info(mode=None):
    """给 AI 的"现在放到哪"。没在一起听就 None。mode：window / book，见 listen_mode。"""
    mode = listen_mode(mode)
    with LOCK:
        x = dict(NOW)
    if not x:
        return None
    fmt = lambda t: "%d:%02d" % (int(t) // 60, int(t) % 60)
    song = "《%s》%s" % (x.get("name", ""), " — " + " / ".join(x["artists"]) if x.get("artists") else "")
    who = config.name()
    if x.get("ended"):
        return {"state": "ended", "id": x["id"], "text": "〔一起听：%s那边%s放完了〕" % (who, song)}
    if x.get("switched"):
        return {"state": "switched", "id": x["id"], "text": "〔一起听：%s换到别的歌了，%s这首的一起听结束〕" % (who, song)}
    if not x.get("at"):
        if card_path(x["id"]) and time.time() - x.get("session", time.time()) > 600:
            return {"state": "unknown", "id": x["id"], "text": "〔一起听：%s的卡好了，但一直没收到%s的播放进度，不知道她听到哪了〕" % (song, who)}
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
    win, mark = [], ""
    if mode == "book":
        mark = bookmark(x["id"], pos, x.get("dur") or 0)
        if mark:
            text += "\n书签：" + mark
    else:
        win = window(x["id"], pos, dur=x.get("dur") or 0)
        if win:
            text += "\n前后十秒（摘自乐谱卡）：\n" + "\n".join("  " + w for w in win)
    return {"state": "playing" if x.get("playing") else "paused", "id": x["id"], "pos": round(pos, 1), "mode": mode,
            "dur": x.get("dur") or 0, "line": x.get("line") or "", "window": win, "bookmark": mark, "text": text + "〕"}


# 拆歌排队：一次只拆一首（同时拆几首会互相抢电脑，都变慢）
QUEUE = []        # [(歌编号, 歌名, 歌长秒)]
CUR = {}          # 正在拆的：{sid, name, dur, since}
DONE = []         # 最近拆好的：[{sid, title, ok, at}]
CV = threading.Condition(LOCK)
_worker = [None]


def est_secs(dur):
    """拆一首大概多久：一般是歌长的两倍上下（4 分钟的歌 8 分钟），最少按 4 分钟算。"""
    return max(240, (dur or 240) * 2.1)


def start_job(sid, name="", dur=0):
    """排进去拆。回前面还有几首（0 = 马上开始）；已经在拆或在排回 -1。"""
    with CV:
        if CUR.get("sid") == sid or any(j[0] == sid for j in QUEUE):
            return -1
        ahead = len(QUEUE) + (1 if CUR else 0)
        QUEUE.append((sid, name or sid, dur))
        JOBS[sid] = "queued"
        if not (_worker[0] and _worker[0].is_alive()):
            _worker[0] = threading.Thread(target=worker, daemon=True)
            _worker[0].start()
        CV.notify()
    return ahead


def job_state(sid):
    """(making / queued / None, 前面还有几首)"""
    with CV:
        if CUR.get("sid") == sid:
            return "making", 0
        for k, j in enumerate(QUEUE):
            if j[0] == sid:
                return "queued", k + (1 if CUR else 0)
    return None, 0


def worker():
    while True:
        with CV:
            while not QUEUE:
                CV.wait()
            sid, name, dur = QUEUE.pop(0)
            CUR.update(sid=sid, name=name, dur=dur, since=time.time())
            JOBS[sid] = "making"
        try:
            r = cli(sid, timeout=1800)
            ok = r.returncode == 0 and card_path(sid)
            if not ok:
                sys.stderr.write("拆《%s》没成：%s\n" % (name, (r.stderr or r.stdout or "")[-300:]))
        except subprocess.TimeoutExpired:
            ok = False
        title = name
        if ok:
            try:
                title = open(card_path(sid), encoding="utf-8").readline().replace("# 乐谱卡：", "").split(" — ")[0].strip() or name
            except OSError:
                pass
        with CV:
            JOBS[sid] = "ready" if ok else "failed"
            CUR.clear()
            DONE[:] = [d for d in DONE if time.time() - d["at"] < 3600][-20:] + [{"sid": sid, "title": title, "ok": bool(ok), "at": time.time()}]


# ---------- MCP：给能接工具的 AI（Aru、Claude App 的自定义连接器、Claude Code……） ----------

MCP_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MCP_INSTRUCTIONS = (
    "music-ears 把歌拆成给 AI 读的乐谱卡（调、拍速、和弦、逐句简谱、换气、长音、颤音，加上模型听的听感）。"
    "用户想跟你聊一首歌：先 search_song 找到编号；list_cards 看拆没拆过；没拆过 make_card（要 5–8 分钟，过几分钟再 get_card）。"
    "get_card 读整张卡，或者给 at（比如 1:20）只读那一段。"
    "用户在黑胶播放器里点了「一起听」时，now_playing 告诉你放到哪一句：book 模式第一次给整张卡、之后只给书签，window 模式每次附前后十秒摘录。"
    "想给她点歌就用 play（默认排下一首，别随便打断她正在听的）。"
    "她想换播放器的样子：先 skin_guide 看能改什么，再 set_skin 写一套（马上换上，播放器开着就实时变），list_skins / use_skin 换回以前的。"
    "卡里的数字是脚本量的，滑音、合唱、童声会冒出不存在的调外音，拿不准就说拿不准。")

MCP_TOOLS = [
    {"name": "search_song", "description": "在网易云搜歌。返回编号、歌名、歌手、专辑、时长，编号给别的工具用。",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "歌名、歌手"},
                                                       "limit": {"type": "integer", "description": "最多几首，默认 8"}},
                     "required": ["query"]}},
    {"name": "list_cards", "description": "已经拆过（有乐谱卡）的歌。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "make_card", "description": "开始拆一首歌、写乐谱卡。要 5–8 分钟，立刻返回；过几分钟用 get_card 看好了没有。",
     "inputSchema": {"type": "object", "properties": {"song": {"type": "string", "description": "网易云歌曲编号或链接"}},
                     "required": ["song"]}},
    {"name": "get_card", "description": "读一首歌的乐谱卡。不给 at 就是整张；给 at（比如 \"1:20\" 或秒数）只回卡头和那个时间前后 span 秒的逐句。",
     "inputSchema": {"type": "object", "properties": {"song": {"type": "string", "description": "网易云歌曲编号、链接，或 list_cards 里的文件夹名、歌名"},
                                                       "at": {"type": "string", "description": "只看这个时间附近，比如 1:20"},
                                                       "span": {"type": "integer", "description": "at 前后各几秒，默认 20"}},
                     "required": ["song"]}},
    {"name": "play", "description": "给用户点一首歌：放进她的黑胶播放器。mode=next（默认）排在正在放的后面，mode=now 马上切过去。note 是想对她说的一句话，播放器上会显示。播放器没开着的话，她下次打开时会收到。",
     "inputSchema": {"type": "object", "properties": {"song": {"type": "string", "description": "网易云歌曲编号或链接"},
                                                       "mode": {"type": "string", "enum": ["next", "now"], "description": "next 排下一首（默认），now 马上放"},
                                                       "note": {"type": "string", "description": "点歌时想说的一句话（可以不写）"}},
                     "required": ["song"]}},
    {"name": "skin_guide", "description": "黑胶播放器的外观说明：能改哪些变量（颜色、唱片、封面形状、歌词、进度条……）、例子、规矩。写 set_skin 之前先看。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "set_skin", "description": "给黑胶播放器做一套外观并换上。css 只能是挂在 .mev 下的规则（主要是改 --mev-* 变量），不能 url()、@import 这类从外面加载东西的写法；不过关会告诉你哪里不行。可以带一张背景图（base64 的 png/jpg/webp，≤4MB），会铺在播放页背景上，想让图清楚就在 css 里写 --mev-bg-filter: none。同名的会被覆盖。",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "外观的名字，1–20 个字（中文、字母、数字、- _）"},
                                                       "css": {"type": "string", "description": "CSS，比如 .mev { --mev-accent: #e0607e; }"},
                                                       "image": {"type": "string", "description": "可选：背景图，base64"},
                                                       "use": {"type": "boolean", "description": "存完马上换上，默认 true"}},
                     "required": ["name", "css"]}},
    {"name": "list_skins", "description": "存过的外观，哪套正在用。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "use_skin", "description": "换上一套存过的外观；name 写 default 换回默认样子。",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "delete_skin", "description": "删掉一套存过的外观（正在用的话先换回默认）。",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "now_playing", "description": "一起听：用户在黑胶播放器里放到哪一句。两种报法（mode）：window 每次附上乐谱卡里前后十秒的旋律、和弦、听感；book 这一轮第一次调用时给你整张乐谱卡，之后只报书签（第几句、卡里哪一行），你自己翻卡。不填就用设置里的默认。没在一起听就说没有。",
     "inputSchema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["window", "book"], "description": "window 摘录 / book 整本加书签"},
                                                       "card": {"type": "boolean", "description": "book 模式下要不要再给一遍整张卡（比如你的对话被压缩过、卡丢了）"}}}},
]


def _sid(song):
    import netease
    song = str(song or "").strip()
    if re.match(r"^\d{3,}$", song):
        return song
    link = netease.find_link(song)
    return netease.song_id(link) if link else ""


def _card_dir(song):
    """编号 / 链接 → 卡的文件夹；也认 list_cards 里的文件夹名、歌名。"""
    song = str(song or "").strip()
    sid = _sid(song)
    if sid and card_path(sid):
        return os.path.dirname(card_path(sid)), sid
    if song and os.path.isfile(os.path.join(CACHE, os.path.basename(song), "card.md")):
        return os.path.join(CACHE, os.path.basename(song)), sid
    for c in (cli_json("list", "--json").get("cards") or []):
        if song and song in c.get("name", ""):
            return os.path.join(CACHE, c["dir"]), sid
    return None, sid


def _secs(t):
    t = str(t).strip()
    if ":" in t:
        m, s = t.split(":", 1)
        return int(m) * 60 + float(s)
    return float(t)


def mcp_call(name, a):
    if name == "search_song":
        d = cli_json("vinyl", "search", str(a.get("query") or ""))
        if not d.get("ok"):
            raise ValueError(d.get("error") or "没搜到")
        songs = (d.get("songs") or [])[:max(1, min(30, int(a.get("limit") or 8)))]
        if not songs:
            return "没搜到。"
        fmt = lambda t: "%d:%02d" % (int(t) // 60, int(t) % 60)
        return "\n".join("%s ｜ %s — %s ｜ %s ｜ %s" % (x["id"], x["name"], " / ".join(x.get("artists") or []), x.get("album", ""), fmt(x.get("dur") or 0))
                         for x in songs)
    if name == "list_cards":
        rows = cli_json("list", "--json").get("cards") or []
        if not rows:
            return "还没拆过歌。"
        return "\n".join("%s ｜ %s%s ｜ %s" % (r["dir"], "（%s唱的）" % config.name() if r.get("sung") else "", r["name"]
                                              + (" — " + " / ".join(r["artists"]) if r.get("artists") and not r.get("sung") else ""), r.get("made", ""))
                         for r in rows)
    if name == "make_card":
        sid = _sid(a.get("song"))
        if not sid:
            raise ValueError("要网易云歌曲编号或链接（先用 search_song 找编号）")
        if card_path(sid):
            return "这首拆过了，直接 get_card。"
        if JOBS.get(sid) == "making":
            return "这首正在拆，过几分钟再 get_card。"
        i = cli_json("info", sid)
        if i.get("ok") and not i.get("free_audio") and not i.get("login"):
            raise ValueError("《%s》是会员歌，拿不到音频：先在播放器里扫码登录网易云会员号" % i.get("name", sid))
        ahead = start_job(sid, i.get("name") or sid, i.get("duration") or 0)
        wait = round(est_secs(i.get("duration")) / 60)
        return ("开始拆《%s》了，约 %d 分钟。" % (i.get("name") or sid, wait) if ahead <= 0 else
                "《%s》排上了，前面还有 %d 首，每首要好几分钟。" % (i.get("name") or sid, ahead)) + "过一会儿用 get_card 看。"
    if name == "get_card":
        d, sid = _card_dir(a.get("song"))
        if not d:
            if sid and JOBS.get(sid) == "making":
                return "还在拆，再等几分钟。"
            if sid and JOBS.get(sid) == "failed":
                raise ValueError("这首没拆成（看跑 music-ears serve 的那个窗口里的报错）")
            raise ValueError("没有这首的卡：先 make_card")
        text = open(os.path.join(d, "card.md"), encoding="utf-8").read()
        if not a.get("at"):
            return text
        at, span = _secs(a["at"]), int(a.get("span") or 20)
        head = text.split("\n## 逐句", 1)[0].strip()
        win = window(os.path.join(d, "card.md"), at, span)
        return head + "\n\n## %s 前后 %d 秒\n" % (a["at"], span) + ("\n".join(win) if win else "（这段没有逐句内容）")
    if name == "play":
        import netease
        sid = _sid(a.get("song"))
        if not sid:
            raise ValueError("要网易云歌曲编号或链接（先用 search_song 找编号）")
        i = netease.info(sid)
        track = {"id": sid, "name": i["name"], "artists": i.get("artists") or [], "album": i.get("album", ""),
                 "cover": i.get("cover", ""), "dur": i.get("duration") or 0}
        mode = "now" if a.get("mode") == "now" else "next"
        with LOCK:
            CMD.update(seq=CMD["seq"] + 1, track=track, mode=mode, note=str(a.get("note") or "")[:80], at=time.time())
        open_ = time.time() - SEEN["at"] < 30
        tail = "" if open_ else "（播放器现在没开着，她打开以后就会收到）"
        if i.get("vip") and not cli_json("info", sid).get("login"):
            tail += "（这首是会员歌，她没登录网易云的话只能放试听片段）"
        return "点好了：《%s》— %s，%s。%s" % (i["name"], " / ".join(track["artists"]), "马上放" if mode == "now" else "排在下一首", tail)
    if name == "skin_guide":
        with open(os.path.join(HOME, "docs", "skin.md"), encoding="utf-8") as f:
            return f.read()
    if name in ("set_skin", "use_skin", "delete_skin"):
        if name == "set_skin":
            n = skin.save(a.get("name"), a.get("css"), a.get("image") or "", a.get("use", True) is not False)
            msg = "存好了「%s」" % n + ("，换上了。" if a.get("use", True) is not False else "，没换上（use_skin 换）。")
        elif name == "use_skin":
            n = skin.use_skin(a.get("name"))
            msg = "换上了「%s」。" % n if n else "换回默认样子了。"
        else:
            skin.delete(a.get("name"))
            msg = "删了「%s」。" % a.get("name")
        with LOCK:
            SKINV["v"] += 1
        open_ = time.time() - SEEN["at"] < 30
        return msg + ("播放器开着，几秒内就变。" if open_ else "播放器现在没开着，下次打开就是这样。")
    if name == "list_skins":
        ns, cur = skin.names(), skin.active()
        own = "（另外她自己写了 skin.css，一直叠在下面）" if os.path.isfile(skin.OWN) else ""
        if not ns:
            return "还没存过外观，现在是默认样子。" + own
        return "\n".join(("● " if x == cur else "○ ") + x for x in ns) + "\n" + ("正在用：" + cur if cur else "现在是默认样子") + own
    if name == "now_playing":
        mode = listen_mode(a.get("mode"))
        info = now_info(mode)
        if not info:
            return "现在没在一起听（用户要在黑胶播放器里点「一起听」）。"
        if mode == "book":
            with LOCK:
                first = not NOW.get("card_given")
            path = card_path(info["id"])
            if (first or a.get("card")) and path:
                with LOCK:
                    NOW["card_given"] = True
                card = open(path, encoding="utf-8").read().strip()
                return ("这一轮一起听的乐谱卡，整本给你；之后我只报书签（第几句、卡里哪一行），你翻到那一行看。\n\n%s\n\n%s"
                        % (card, info["text"]))
            if first and not path:
                return info["text"] + "\n（乐谱卡还在拆，拆好以后再调一次 now_playing 就会给你整张卡）"
        return info["text"]
    raise KeyError(name)


def mcp_handle(msg):
    """一条 JSON-RPC。通知（没有 id）回 None。"""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    ok = lambda r: {"jsonrpc": "2.0", "id": mid, "result": r}
    err = lambda code, m: {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": m}}
    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({"protocolVersion": want if want in MCP_VERSIONS else MCP_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "music-ears", "version": "0.2"},
                   "instructions": MCP_INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": MCP_TOOLS})
    if method == "tools/call":
        try:
            text = mcp_call(params.get("name"), params.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": text}], "isError": False})
        except KeyError:
            return err(-32602, "没有这个工具：%s" % params.get("name"))
        except Exception as e:
            return ok({"content": [{"type": "text", "text": "出错了：%s" % e}], "isError": True})
    return err(-32601, "不支持 %s" % method)


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
        if p == "mcp":
            if method != "POST":
                return self.send(405, "text/plain; charset=utf-8", "MCP 只收 POST（不开 SSE）".encode(), {"Allow": "POST"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                msg = json.loads(self.rfile.read(min(n, 8 << 20)) or b"null")   # set_skin 会带背景图
            except ValueError:
                return self.json({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "不是 JSON"}}, 400)
            out = [r for r in (mcp_handle(m) for m in msg) if r] if isinstance(msg, list) else mcp_handle(msg if isinstance(msg, dict) else {})
            if not out:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self.json(out)
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
        if method == "GET" and p == "skin.css":
            return self.send(200, "text/css; charset=utf-8", skin.css().encode())
        if method == "GET" and p.startswith("skin-img/"):
            got = skin.image(p[len("skin-img/"):])
            return self.send(200, got[0], got[1]) if got else self.json({"ok": False, "error": "没有这张图"}, 404)
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
                if not card_path(sid):   # 一起听要先有卡：没拆过就让播放器提示先拆，这时不算一起听
                    st, ahead = job_state(sid)
                    return self.json({"ok": False, "need_card": True, "making": st, "ahead": ahead})
                with LOCK:
                    NOW.clear()
                    NOW.update(session=time.time(), card_given=False, id=sid, name=str(body.get("name") or "")[:60], artists=[str(a)[:30] for a in (body.get("artists") or [])][:4])
                return self.json({"ok": True, "have": True})
            sid = (q.get("id") or [""])[0]
            st = "ready" if card_path(sid) else JOBS.get(sid, "none")
            return self.json({"ok": True, "state": st})
        if p == "api/make" and method == "POST":   # 拆乐谱卡（只拆、存着）
            sid = str(body.get("id") or "")
            if not sid.isdigit():
                return self.json({"ok": False, "error": "要网易云歌曲编号"}, 400)
            if card_path(sid):
                return self.json({"ok": True, "have": True})
            try:
                dur = float(body.get("dur") or 0)
            except (TypeError, ValueError):
                dur = 0
            return self.json({"ok": True, "ahead": start_job(sid, str(body.get("name") or sid)[:60], dur)})
        if p == "api/queue":   # 拆歌进度：在拆哪首、拆了多久、预计多久，还排着哪些，最近拆好了哪些
            with CV:
                cur, q = dict(CUR), [{"sid": j[0], "name": j[1]} for j in QUEUE]
                done = [d for d in DONE if time.time() - d["at"] < 3600]
            making = {"sid": cur["sid"], "name": cur["name"], "elapsed": round(time.time() - cur["since"]), "est": round(est_secs(cur.get("dur")))} if cur else None
            return self.json({"ok": True, "making": making, "queued": q, "done": done})
        if p == "api/pos" and method == "POST":
            sid = str(body.get("id") or "")
            with LOCK:
                if NOW.get("id") and NOW.get("id") != sid and NOW.get("at") and body.get("playing") and not NOW.get("ended"):
                    NOW["switched"] = True   # 一起听的那首放过了，现在在放别的：她换歌了
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
        if p == "api/cmd":
            SEEN["at"] = time.time()
            since = int((q.get("since") or ["0"])[0] or 0)
            with LOCK:
                c = dict(CMD)
            fresh = c.get("seq", 0) > since and time.time() - c.get("at", 0) < 1800   # 半小时前点的就算了
            return self.json({"ok": True, "boot": BOOT, "seq": c.get("seq", 0), "skin": SKINV["v"], "cmd": {k: c[k] for k in ("seq", "track", "mode", "note")} if fresh else None})
        if p == "api/now":
            info = now_info((q.get("mode") or [None])[0])
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
