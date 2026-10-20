"""黑胶播放器的外观（换装）。说明见 docs/skin.md。

~/.config/music-ears/skin.css        自己手写的，一直叠在 vinyl.css 后面
~/.config/music-ears/skins/<名字>/    AI 用 MCP 存的外观：skin.css，可能还有一张背景图 bg.png|jpg|webp
~/.config/music-ears/skins/active    现在用哪套（一行名字；空的或没有就是只用手写的 / 默认）

播放器拿 /skin.css 时 = 手写的 + 正在用的那套，后写的盖前面的。
"""
import base64
import os
import re
import shutil
import urllib.parse

CONF = os.path.expanduser("~/.config/music-ears")
OWN = os.path.join(CONF, "skin.css")
DIR = os.path.join(CONF, "skins")
ACTIVE = os.path.join(DIR, "active")
NAME = re.compile(r"^[\w一-鿿-]{1,20}$")
IMG = {"png": b"\x89PNG", "jpg": b"\xff\xd8\xff", "webp": b"RIFF"}
CSS_MAX = 30000
IMG_MAX = 4 << 20
BAD = [(r"<", "不能有 < 这个字符"), (r"\\", "不能用反斜杠转义"), (r"url\s*\(", "不能写 url()（背景图用 image 参数传）"),
       (r"@import", "不能 @import"), (r"@font-face", "不能放字体文件"), (r"javascript\s*:", "不能有脚本"),
       (r"expression\s*\(", "不能有脚本（expression）"), (r"behavior\s*:", "不能有 behavior"), (r"-moz-binding", "不能有 -moz-binding"),
       (r"image-set\s*\(", "不能用 image-set"), (r"\bsrc\s*\(", "不能用 src()"), (r"@namespace", "不能用 @namespace")]
AT_OK = ("@media", "@supports", "@keyframes", "@-webkit-keyframes")


def check_css(css):
    """不过就 raise ValueError（说给 AI 听的原因）。只认挂在 .mev 下的规则，不许从外面加载任何东西。"""
    if len(css) > CSS_MAX:
        raise ValueError("CSS 太长了（最多 %d 字）" % CSS_MAX)
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    low = body.lower()
    for pat, why in BAD:
        if re.search(pat, low):
            raise ValueError(why)
    stack, buf = ["top"], ""   # 每层里面是什么：top 规则 / kf 关键帧 / decl 声明
    for ch in body:
        if ch == "{":
            pre, ctx = buf.strip(), stack[-1]
            if ctx == "top" and pre.startswith("@"):
                if not pre.lower().startswith(AT_OK):
                    raise ValueError("不认 %s（只能用 @media、@supports、@keyframes）" % pre.split()[0])
                stack.append("kf" if "keyframes" in pre.lower() else "top")
            elif ctx == "top":
                if not pre or not all(".mev" in x for x in pre.split(",")):
                    raise ValueError("每条规则都要挂在 .mev 下面（比如 .mev { --mev-accent: … } 或 .mev .mev-cover { … }），这条不是：%s" % pre[:60])
                stack.append("decl")
            else:
                stack.append("decl")   # 关键帧里的 0% {…}，或者规则里嵌套的规则（已经在 .mev 下面了）
            buf = ""
        elif ch == "}":
            if len(stack) == 1:
                raise ValueError("大括号对不上")
            stack.pop()
            buf = ""
        elif ch == ";":
            buf = ""
        else:
            buf += ch
    if len(stack) != 1:
        raise ValueError("大括号对不上")
    return css.strip()


def active():
    try:
        with open(ACTIVE, encoding="utf-8") as f:
            n = f.read().strip()
        return n if n and os.path.isdir(os.path.join(DIR, n)) else ""
    except OSError:
        return ""


def names():
    if not os.path.isdir(DIR):
        return []
    return sorted(n for n in os.listdir(DIR) if os.path.isfile(os.path.join(DIR, n, "skin.css")))


def _img(name):
    for ext in IMG:
        if os.path.isfile(os.path.join(DIR, name, "bg." + ext)):
            return "bg." + ext
    return ""


def save(name, css, image="", use=True):
    name = str(name or "").strip()
    if not NAME.match(name) or name in ("active", "default", "默认"):
        raise ValueError("名字只用中文、字母、数字、- _，1 到 20 个字，不能叫 default / 默认")
    css = check_css(str(css or ""))
    data, ext = b"", ""
    if image:
        try:
            data = base64.b64decode(re.sub(r"^data:[^,]*,", "", str(image)), validate=False)
        except ValueError:
            raise ValueError("image 要 base64")
        if len(data) > IMG_MAX:
            raise ValueError("背景图超过 4MB 了，缩小一点再传")
        ext = next((e for e, magic in IMG.items() if data.startswith(magic) and (e != "webp" or data[8:12] == b"WEBP")), "")
        if not ext:
            raise ValueError("背景图只认 png、jpg、webp")
    d = os.path.join(DIR, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "skin.css"), "w", encoding="utf-8") as f:
        f.write(css + "\n")
    if data:
        for e in IMG:
            p = os.path.join(d, "bg." + e)
            if os.path.exists(p):
                os.remove(p)
        with open(os.path.join(d, "bg." + ext), "wb") as f:
            f.write(data)
    if use:
        use_skin(name)
    return name


def use_skin(name):
    name = str(name or "").strip()
    if name in ("", "default", "默认"):
        name = ""
    elif name not in names():
        raise ValueError("没有叫「%s」的外观（list_skins 看有哪些）" % name)
    os.makedirs(DIR, exist_ok=True)
    with open(ACTIVE, "w", encoding="utf-8") as f:
        f.write(name)
    return name


def delete(name):
    name = str(name or "").strip()
    if name not in names():
        raise ValueError("没有叫「%s」的外观" % name)
    if active() == name:
        use_skin("")
    shutil.rmtree(os.path.join(DIR, name))


def css():
    """播放器拿的 /skin.css：手写的 + 正在用的那套（带背景图的话补一条背景规则）。"""
    out = []
    if os.path.isfile(OWN):
        with open(OWN, encoding="utf-8", errors="replace") as f:
            out.append(f.read(200000))
    n = active()
    if n:
        with open(os.path.join(DIR, n, "skin.css"), encoding="utf-8") as f:
            out.append("/* 外观：%s */\n" % n + f.read())
        img = _img(n)
        if img:
            out.append('.mev .mev-bg { background-image: url("skin-img/%s/%s") !important; }'
                       % (urllib.parse.quote(n), img))
    return "\n".join(out)


def image(path):
    """/skin-img/<名字>/bg.xxx → (类型, 字节)；不对就 None。"""
    parts = urllib.parse.unquote(path).split("/")
    if len(parts) != 2 or parts[0] not in names() or parts[1] not in ("bg." + e for e in IMG):
        return None
    p = os.path.join(DIR, parts[0], parts[1])
    if not os.path.isfile(p):
        return None
    ext = parts[1][3:]
    with open(p, "rb") as f:
        return ("image/jpeg" if ext == "jpg" else "image/" + ext), f.read()
