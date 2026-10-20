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
import threading
import urllib.parse

CONF = os.path.expanduser("~/.config/music-ears")
OWN = os.path.join(CONF, "skin.css")
DIR = os.path.join(CONF, "skins")
ACTIVE = os.path.join(DIR, "active")
NAME = re.compile(r"^[\w一-鿿-]{1,20}$")
IMG = {"png": b"\x89PNG", "jpg": b"\xff\xd8\xff", "webp": b"RIFF"}
CSS_MAX = 30000
IMG_MAX = 4 << 20
# 外观只许"改样子"：白名单。选择器必须从 .mev 往里走，属性不许动位置和大小（不然能盖住、假冒嵌入它的页面），
# 值里的函数只许颜色、渐变、滤镜这几类，没有任何能从外面加载东西的写法。
PROPS = {"color", "background", "background-color", "background-image", "background-size", "background-position",
         "background-repeat", "background-blend-mode", "border", "border-color", "border-width", "border-style",
         "border-radius", "border-top", "border-bottom", "border-left", "border-right", "outline", "outline-color",
         "box-shadow", "text-shadow", "opacity", "filter", "backdrop-filter", "-webkit-backdrop-filter", "mix-blend-mode",
         "font", "font-family", "font-size", "font-weight", "font-style", "letter-spacing", "line-height", "text-align",
         "text-decoration", "text-transform", "word-spacing", "accent-color", "caret-color"}
FUNCS = {"rgb", "rgba", "hsl", "hsla", "var", "calc", "min", "max", "clamp", "color-mix", "linear-gradient",
         "radial-gradient", "conic-gradient", "repeating-linear-gradient", "repeating-radial-gradient",
         "repeating-conic-gradient", "blur", "saturate", "brightness", "contrast", "grayscale", "sepia",
         "hue-rotate", "invert", "drop-shadow", "opacity"}
PSEUDO = {"hover", "active", "focus", "focus-visible", "disabled", "checked", "empty", "first-child", "last-child",
          ":before", ":after", ":placeholder", ":selection", ":-webkit-slider-thumb", ":-webkit-slider-runnable-track",
          ":-moz-range-thumb", ":-moz-range-track", ":-moz-range-progress"}
COMPOUND = re.compile(r"^(?:[a-z][a-z0-9]*)?(?:\.[a-zA-Z][\w-]*|::?[a-z-]+(?:\([0-9n+ -]+\))?)*$")
MEDIA = re.compile(r"^@media(?=[\s(])[a-z0-9\s():,.-]+$", re.I)
# 小唱片、提示条是 fixed 浮在页面上的：这几样调得特别大就能盖住嵌入它的页面，所以长度有上限、不许用函数绕
SIZED = {"border", "border-width", "border-top", "border-bottom", "border-left", "border-right", "outline",
         "box-shadow", "text-shadow", "filter", "backdrop-filter", "-webkit-backdrop-filter",
         "font", "font-size", "letter-spacing", "word-spacing", "line-height"}
SIZED_FUNCS = {"rgb", "rgba", "hsl", "hsla", "color-mix", "blur", "saturate", "brightness", "contrast", "grayscale",
               "sepia", "hue-rotate", "invert", "drop-shadow", "opacity"}
LEN_MAX = {"px": 32, "pt": 24, "em": 2, "rem": 2, "ch": 2, "ex": 4, "lh": 2, "deg": 360, "turn": 1, "rad": 7, "%": 400}


def _selector(sel):
    """一个选择器（逗号已经切开）：第一节必须正好是 .mev（可以再带别的类、伪类），往后只能往里走。"""
    sel = re.sub(r"\([^()]*\)", lambda m: m.group(0).replace(" ", "").replace("+", "\0"), sel.strip())   # 括号里的 + 不是组合符
    toks = re.split(r"\s*([>+~])\s*|\s+", sel)
    parts = [t.replace("\0", "+") for t in toks if t]
    if not parts or not re.match(r"^\.mev(?![\w-])", parts[0]):
        raise ValueError("选择器要从 .mev 开始（比如 .mev .mev-cover），这条不是：%s" % sel[:60])
    if len(parts) > 1 and parts[1] in "+~":
        raise ValueError(".mev 后面不能接 + 或 ~（那会选到播放器外面）：%s" % sel[:60])
    for t in parts:
        if t in (">", "+", "~"):
            continue
        if not COMPOUND.match(t):
            raise ValueError("选择器里有不认的写法（只能用类名、标签名和常见的 :hover ::before 这类）：%s" % t[:40])
        for ps in re.findall(r":(:?[a-z-]+)", t):
            if ps not in PSEUDO and not re.match(r"^nth-(last-)?child$", ps):
                raise ValueError("不认 :%s（:has :is :not :root 这类不能用）" % ps)


def _decls(block):
    for d in block.split(";"):
        if not d.strip():
            continue
        if ":" not in d:
            raise ValueError("这一句看不懂：%s" % d.strip()[:40])
        k, v = d.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if not (re.match(r"^--mev-[a-z0-9-]+$", k) or k in PROPS):
            raise ValueError("不能改 %s（只能改 --mev-* 变量和颜色、边框、阴影、字体这类，不能动位置和大小）" % k)
        for f in re.findall(r"([a-z-]*)\s*\(", v.lower()):
            if f not in FUNCS:
                raise ValueError("%s 里不能用 %s()" % (k, f or "括号"))
        if not re.match(r"^[\w\s#%.,()+*/!-]*$", v):
            raise ValueError("%s 的值里有不认的字符（引号也不行，字体名不用加引号）：%s" % (k, v[:40]))
        if k in SIZED:
            for f in re.findall(r"([a-z-]*)\s*\(", v.lower()):
                if f not in SIZED_FUNCS:
                    raise ValueError("%s 里不能用 %s()，直接写数" % (k, f))
            nums = re.sub(r"#[0-9a-f]+", " ", v.lower())
            for n, u in re.findall(r"(\d*\.?\d+)([a-z%]*)", nums):
                if not u:
                    if k == "line-height" and float(n) > 3:
                        raise ValueError("line-height 最多 3")
                    continue
                if u not in LEN_MAX:
                    raise ValueError("%s 里不能用 %s 这个单位（用 px 或 em）" % (k, u))
                if float(n) > LEN_MAX[u]:
                    raise ValueError("%s 的 %s%s 太大了（最多 %s%s，太大能盖住整个页面）" % (k, n, u, LEN_MAX[u], u))


def check_css(css):
    """不过就 raise ValueError（说给 AI 听的原因）。"""
    if len(css) > CSS_MAX:
        raise ValueError("CSS 太长了（最多 %d 字）" % CSS_MAX)
    if "\\" in css or "<" in css or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", css):
        raise ValueError("不能有反斜杠、< 和控制字符")
    if "/*" in css or "*/" in css or '"' in css or "'" in css:   # 引号里的 /* 浏览器不当注释，解析器会看岔：都不许
        raise ValueError("不能有注释和引号（字体名直接写，比如 font-family: PingFang SC, serif）")
    body = css
    i, media = 0, False
    while True:
        j = body.find("{", i)
        tail = body[i:j] if j >= 0 else body[i:]
        if j < 0:
            rest = tail.strip()
            if rest == "}" and media:
                media, rest = False, ""
            if rest:
                raise ValueError("大括号对不上，或者多了：%s" % rest[:40])
            if media:
                raise ValueError("@media 没有收尾的 }")
            return css.strip()
        pre = tail.strip()
        if media and pre.startswith("}"):   # @media 的收尾
            media, pre = False, pre[1:].strip()
        if pre.startswith("@"):
            if media or not MEDIA.match(pre):
                raise ValueError("只能用 @media（比如 @media (prefers-color-scheme: light)），这条不行：%s" % pre[:60])
            media, i = True, j + 1
            continue
        if "}" in pre or ";" in pre:
            raise ValueError("大括号对不上：%s" % pre[:40])
        for sel in pre.split(","):
            _selector(sel)
        k = body.find("}", j)
        if k < 0 or "{" in body[j + 1:k]:
            raise ValueError("规则里不能再套规则：%s" % pre[:60])
        _decls(body[j + 1:k])
        i = k + 1


def active():
    try:
        with open(ACTIVE, encoding="utf-8") as f:
            n = f.read().strip()
    except OSError:
        return ""
    if n and n not in names():   # 文件夹被手动删了：清掉，免得以后建同名的直接算"正在用"
        _write(ACTIVE, b"")
        return ""
    return n


def _write(path, data):
    """先写临时文件再换上：播放器正好来拿时不会读到半截。"""
    tmp = "%s.%d.tmp" % (path, threading.get_ident())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


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
            data = base64.b64decode(re.sub(r"\s+", "", re.sub(r"^data:[^,]*,", "", str(image))), validate=True)
        except ValueError:   # binascii.Error 也是 ValueError
            raise ValueError("image 要 base64")
        if len(data) > IMG_MAX:
            raise ValueError("背景图超过 4MB 了，缩小一点再传")
        ext = next((e for e, magic in IMG.items() if data.startswith(magic) and (e != "webp" or data[8:12] == b"WEBP")), "")
        if not ext:
            raise ValueError("背景图只认 png、jpg、webp")
    d = os.path.join(DIR, name)
    os.makedirs(d, exist_ok=True)
    _write(os.path.join(d, "skin.css"), (css + "\n").encode("utf-8"))
    if data:
        for e in IMG:
            p = os.path.join(d, "bg." + e)
            if os.path.exists(p):
                os.remove(p)
        _write(os.path.join(d, "bg." + ext), data)
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
    _write(ACTIVE, name.encode("utf-8"))
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
            text = f.read()
        try:
            check_css(text)   # 旧版存下的、手动改过的，过不了就不用
        except ValueError:
            return "\n".join(out)
        out.append(text)
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
