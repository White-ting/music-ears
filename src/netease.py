"""网易云：认链接、取歌曲信息和带时间的歌词、取音频。只用标准库。

免费歌的音频不用登录（outer/url）；会员歌要登录信息（Cookie 里的 MUSIC_U），放在
~/.config/music-ears/netease-cookie（权限 600，只存本机）——这是非官方用法，开源版默认不带。
网易云在国内，走直连，不走代理（走代理出口在国外，版权限制更多）。
"""
import json
import os
import re
import urllib.request

COOKIE_FILE = os.path.expanduser("~/.config/music-ears/netease-cookie")
LINK = re.compile(r"(?:https?://)?(?:163cn\.tv/\w+|(?:y\.)?music\.163\.com/[^\s]*?(?:song\?|song/)[^\s]*)")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"

_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url, cookie=None, binary=False, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://music.163.com/",
                                               **({"Cookie": cookie} if cookie else {})})
    with _direct.open(req, timeout=timeout) as r:
        data = r.read()
        return (data, r.geturl()) if binary else (data.decode("utf-8"), r.geturl())


def find_link(text):
    m = LINK.search(text or "")
    return m.group(0) if m else None


def song_id(link_or_id):
    """链接（短链 163cn.tv 也行）或纯数字 → 歌曲编号。"""
    s = str(link_or_id).strip()
    if s.isdigit():
        return s
    if not s.startswith("http"):
        s = "https://" + s
    m = re.search(r"[?&]id=(\d+)|song/(\d+)", s)
    if not m:
        _, s = _get(s)   # 短链跳一下
        m = re.search(r"[?&]id=(\d+)|song/(\d+)", s)
    if not m:
        raise ValueError("认不出歌曲编号：" + s[:80])
    return m.group(1) or m.group(2)


def info(sid):
    d = json.loads(_get("https://music.163.com/api/song/detail?ids=%5B" + sid + "%5D")[0])
    s = (d.get("songs") or [None])[0]
    if not s:
        raise ValueError("网易云没有这首：" + sid)
    return {"id": sid, "name": s["name"], "artists": [a["name"] for a in s.get("artists", [])],
            "album": (s.get("album") or {}).get("name", ""), "duration": round(s.get("duration", 0) / 1000, 1),
            "vip": s.get("fee") in (1, 4)}   # fee 1 会员、4 付费专辑；0/8 免费


def free_audio(sid):
    """免费外链能不能拿到音频（只看跳到哪，不下载）。标着免费的歌也可能有版权限制拿不到。"""
    req = urllib.request.Request("https://music.163.com/song/media/outer/url?id=%s.mp3" % sid, method="HEAD",
                                 headers={"User-Agent": UA, "Referer": "https://music.163.com/"})
    try:
        with _direct.open(req, timeout=10) as r:
            return not r.geturl().endswith("/404")
    except OSError:
        return False


def lyric(sid):
    return json.loads(_get("https://music.163.com/api/song/lyric?id=%s&lv=1&tv=-1" % sid)[0])


# ---------- 扫码登录（黑胶播放器用）----------
# 不加密的旧接口能出二维码、能问状态，但扫码授权那一刻回 8821"请切换其他登录方式或升级新版本"→ 改走 weapi 加密接口（nmcrypto.py）
# unikey → 二维码内容 https://music.163.com/login?codekey=<key>；问状态：800 过期、801 等扫、802 扫了等确认、803 成了（Set-Cookie 里有 MUSIC_U）

PC_COOKIE = "os=pc; appver=3.1.17.204416; osver=Microsoft-Windows-10-Professional-build-19045-64bit; channel=netease"


def _weapi(path, obj, cookie=None):
    """POST 加密接口；回 (JSON, Set-Cookie 列表)。带"电脑客户端"的标识（开源客户端解 8821 的做法）。"""
    import urllib.parse
    import nmcrypto
    data = urllib.parse.urlencode(nmcrypto.weapi(obj)).encode()
    req = urllib.request.Request("https://music.163.com/weapi/" + path, data=data, method="POST", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": "https://music.163.com/", "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": PC_COOKIE + ("; " + cookie if cookie else "")})
    with _direct.open(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8")), (r.headers.get_all("Set-Cookie") or [])


def qr_key():
    d, _ = _weapi("login/qrcode/unikey", {"type": 1, "noCheckToken": True})
    if d.get("code") != 200:
        raise ValueError("网易云没给二维码：%s" % d.get("code"))
    return d["unikey"], "https://music.163.com/login?codekey=" + d["unikey"]


def qr_check(key):
    """回 (状态码, 说明)；803 时把登录信息存进 COOKIE_FILE（600，只存本机）。"""
    d, cookies = _weapi("login/qrcode/client/login", {"key": key, "type": 1, "noCheckToken": True})
    code = d.get("code")
    if code == 803:
        jar = {}
        for c in cookies:
            k, _, v = c.split(";", 1)[0].partition("=")
            if k.strip() in ("MUSIC_U", "__csrf", "NMTID", "MUSIC_A_T", "MUSIC_R_T"):
                jar[k.strip()] = v.strip()
        if not jar.get("MUSIC_U"):
            return 0, "扫成了但没拿到登录信息"
        os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
        fd = os.open(COOKIE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("; ".join("%s=%s" % kv for kv in jar.items()))
    return code, d.get("message") or {800: "二维码过期了", 801: "等你扫", 802: "扫了，在手机上点确认", 803: "登录好了"}.get(code, "")


def account():
    """登录信息还管不管用：回 {uid, name, vip} 或 None。"""
    c = cookie()
    if not c:
        return None
    d, _ = _weapi("w/nuser/account/get", {}, cookie=c)
    p = d.get("profile") or {}
    if not p.get("userId"):
        return None
    return {"uid": p["userId"], "name": p.get("nickname", ""), "vip": bool((d.get("account") or {}).get("vipType"))}


def cookie():
    try:
        with open(COOKIE_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def download(sid, path):
    """先试免费的外链；不行再看有没有登录信息。成功返回 "free"/"login"，拿不到返回 None。"""
    try:
        data, url = _get("https://music.163.com/song/media/outer/url?id=%s.mp3" % sid, binary=True, timeout=60)
        if not url.endswith("/404") and len(data) > 100000:
            open(path, "wb").write(data)
            return "free"
    except OSError:
        pass
    c = cookie()
    if not c:
        return None
    d, _ = _weapi("song/enhance/player/url/v1", {"ids": "[%s]" % sid, "level": "exhigh", "encodeType": "mp3"}, cookie=c)
    x = (d.get("data") or [{}])[0]
    u = None if x.get("freeTrialInfo") else x.get("url")   # 只给试听片段的不要
    if not u:
        return None
    data, _ = _get(u, binary=True, timeout=120)
    if len(data) < 100000:
        return None
    open(path, "wb").write(data)
    return "login"


# ---------- 黑胶播放器用：歌单、歌单里的歌、播放地址、搜索 ----------

def _song(t):
    al = t.get("al") or t.get("album") or {}
    return {"id": str(t["id"]), "name": t.get("name", ""), "artists": [a.get("name", "") for a in (t.get("ar") or t.get("artists") or [])],
            "album": al.get("name", ""), "cover": (al.get("picUrl") or "").replace("http://", "https://"),
            "dur": round((t.get("dt") or t.get("duration") or 0) / 1000, 1), "pop": t.get("pop") or t.get("popularity") or 0}


def playlists():
    me = account()
    if not me:
        return None
    d, _ = _weapi("user/playlist", {"uid": me["uid"], "limit": 300, "offset": 0}, cookie=cookie())
    return [{"id": str(p["id"]), "name": p["name"], "count": p.get("trackCount", 0),
             "cover": (p.get("coverImgUrl") or "").replace("http://", "https://"), "mine": p.get("userId") == me["uid"]}
            for p in d.get("playlist") or []]


def tracks(pid):
    c = cookie()
    d, _ = _weapi("v6/playlist/detail", {"id": pid, "n": 100000, "s": 8}, cookie=c)
    pl = d.get("playlist") or {}
    have = {str(t["id"]): _song(t) for t in pl.get("tracks") or []}
    ids = [str(x["id"]) for x in pl.get("trackIds") or []]
    miss = [i for i in ids if i not in have]
    for k in range(0, len(miss), 400):   # 歌单详情只带前一部分歌的信息，剩下的按编号批量补
        part = miss[k:k + 400]
        e, _ = _weapi("v3/song/detail", {"c": json.dumps([{"id": int(i)} for i in part])}, cookie=c)
        for t in e.get("songs") or []:
            have[str(t["id"])] = _song(t)
    return [have[i] for i in ids if i in have] or list(have.values())


def song_url(sid):
    """能直接放的地址（https，带签名、二十来分钟过期）；拿不到或只给试听回 None。"""
    d, _ = _weapi("song/enhance/player/url/v1", {"ids": "[%s]" % sid, "level": "exhigh", "encodeType": "mp3"}, cookie=cookie())
    x = (d.get("data") or [{}])[0]
    if not x.get("url"):
        return None
    return {"url": x["url"].replace("http://", "https://"), "trial": bool(x.get("freeTrialInfo"))}


VERSION_WORDS = ("cover", "翻唱", "翻自", "原唱", "伴奏", "dj", "remix", "live", "现场", "演唱会", "歌友会", "片段", "钢琴", "吉他版", "纯音乐", "铃声",
                 "童声", "女声版", "男声版", "抖音", "深情版", "加速", "降调", "升调")


def search(q):
    """搜歌名、歌手（先看歌名和歌手对不对得上，再看专辑）。多取 60 条，自己重新排：
    歌名 > 歌手 > 专辑；翻唱/伴奏/DJ/Live 这类没搜就往后放；分一样按网易云原来的顺序。"""
    d, _ = _weapi("cloudsearch/get/web", {"s": q, "type": 1, "limit": 60, "offset": 0}, cookie=cookie())
    songs = [_song(t) for t in (d.get("result") or {}).get("songs") or []]
    toks = [t.lower() for t in q.split() if t.strip()]
    ql = q.lower()

    def score(s):
        name, arts, alb = s["name"].lower(), [a.lower() for a in s["artists"]], s["album"].lower()
        base = name.split("(")[0].split("（")[0].strip()
        sc = 0
        for t in toks:
            art = 60 if any(t == a for a in arts) else 30 if any(t in a for a in arts) else 0
            sc += art
            if t == base or t == name:
                sc += 100
            elif t in name and not art:   # 已经对上歌手的词，不再在歌名里重复算（"晴天 (2022周杰伦…现场)"）
                sc += 40
            elif t in alb and not art:    # 歌名、歌手都没对上才看专辑（不然专辑也叫"我爱你"的单曲压过王菲）
                sc += 10
        if any(w in name for w in VERSION_WORDS) and not any(w in ql for w in VERSION_WORDS):
            sc -= 45
        return sc
    order = sorted(range(len(songs)), key=lambda i: (-score(songs[i]), -songs[i]["pop"], i))   # 分一样看热度
    return [songs[i] for i in order][:30]
