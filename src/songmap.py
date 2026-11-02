"""段落地图：一首歌从头到尾分成几段，每段是什么（前奏、主歌、副歌、间奏、桥段、尾奏），只用标准库。

两种依据，卡里分开写，别让 AI 把推断当成测量：
- 前奏、间奏、尾奏：按「人声有没有在唱」切的——第一句之前、两句之间空了一大截（≥ 8 拍，至少 4.5 秒）、最后一句之后。
  依据是脚本量的人声音符和歌词时间，算测量。
- 主歌、副歌、桥段：按「歌词重复」推的——整首里出现两次以上的句子连成一块算副歌，其余算主歌；
  后半首只出现一次、夹在副歌之间的算桥段。这是推断，不一定准（有的歌主歌也重复、副歌每次词不一样）。
没有歌词的（纯音乐、对不上歌词的录音）：按声音相似度切的段落列出来，不起名字。

输入是 measure.json 的内容；新版 measure.py 还会存 beat_chords（每拍和弦）和 loud_by_sec（每秒响度），有就顺带写进每段。
"""
import re

GAP_BEATS = 8      # 两句之间空了这么多拍（至少 GAP_MIN 秒）算间奏
GAP_MIN = 4.5
EDGE_MIN = 3.0     # 前奏、尾奏至少这么长才写


def _norm(text):
    return re.sub(r"[\W_]+", "", text or "").lower()


def _sing_span(l, notes):
    """这句真正唱的起止：第一个音到最后一个音唱完；没量到音就用歌词时间。"""
    ln = [n for n in notes if l["t"] <= n["t"] < l["end"]]
    if not ln:
        return l["t"], l["end"]
    return min(l["t"], ln[0]["t"]), ln[-1]["t"] + ln[-1]["dur"]


def _extras(p, m):
    """这段的和弦走向、平均响度（新版 measure 才有）。"""
    bc = m.get("beat_chords") or []
    if bc:
        ch = [c for t, c in bc if p["start"] <= t < p["end"] and c != "N"]
        p["chords"] = [c for k, c in enumerate(ch) if k == 0 or c != ch[k - 1]][:16]
    lb = m.get("loud_by_sec") or []
    if lb:
        xs = [x for x in lb[int(p["start"]):max(int(p["start"]) + 1, int(p["end"]))] if x is not None]
        if xs:
            p["loudness_db"] = round(sum(xs) / len(xs), 1)
    return p


def _blocks(m):
    """先按人声有无切：前奏、间奏、尾奏（lines 空），中间是唱的大块（lines = 这块里每句的开始时间）。这一步是测量。"""
    dur = m["duration"]
    lines = [l for l in m.get("lines") or [] if l.get("text")]
    notes = m.get("notes") or []
    gap = max(GAP_MIN, GAP_BEATS * 60.0 / (m.get("tempo_bpm") or 100))
    spans = [_sing_span(l, notes) for l in lines]
    out = []
    if lines[0]["t"] >= EDGE_MIN:
        out.append({"start": 0.0, "end": lines[0]["t"], "kind": "前奏", "lines": []})
    cur = [0]
    for i in range(1, len(lines) + 1):
        if i == len(lines) or lines[i]["t"] - spans[i - 1][1] >= gap:
            out.append({"start": lines[cur[0]]["t"], "end": spans[cur[-1]][1], "kind": "唱", "lines": [lines[k]["t"] for k in cur],
                        "_spans": [spans[k] for k in cur]})
            if i < len(lines):
                out.append({"start": spans[i - 1][1], "end": lines[i]["t"], "kind": "间奏", "lines": []})
                cur = [i]
        else:
            cur.append(i)
    if dur - spans[-1][1] >= EDGE_MIN:
        out.append({"start": spans[-1][1], "end": dur, "kind": "尾奏", "lines": []})
    return out


def _by_repeat(m, blk):
    """没有模型时的退路：按"整首里出现两次以上的句子"把一大块分成副歌 / 非副歌。返回 [(是否副歌, [句子时间])]。"""
    lines = {l["t"]: l for l in m["lines"]}
    cnt = {}
    for l in m["lines"]:
        cnt[_norm(l["text"])] = cnt.get(_norm(l["text"]), 0) + 1
    flags = [cnt[_norm(lines[t]["text"])] >= 2 and len(_norm(lines[t]["text"])) >= 2 for t in blk["lines"]]
    for k in range(1, len(flags) - 1):   # 夹在中间的单独一句随两边
        if flags[k - 1] == flags[k + 1] != flags[k]:
            flags[k] = flags[k - 1]
    runs, cur_f, cur = [], None, []
    for t, f in zip(blk["lines"], flags):
        if cur and f != cur_f:
            runs.append((cur_f, cur))
            cur = []
        cur_f = f
        cur.append(t)
    if cur:
        runs.append((cur_f, cur))
    return runs


def song_map(m, labels=None):
    """labels：模型按歌词分的 {句子时间: 段名}（parse_labels 的结果）；没有就按歌词重复推。"""
    lines = [l for l in m.get("lines") or [] if l.get("text")]
    if not lines:
        return [_extras({"start": s["start"], "end": s["end"], "kind": "段落 " + s["label"], "basis": "声音相似度切的", "lines": []}, m)
                for s in m.get("sections") or []]
    span_of = {}
    parts, n = [], {}
    blocks = _blocks(m)
    for blk in blocks:
        if blk["kind"] != "唱":
            parts.append(dict(blk, basis="人声有无（测量）"))
            continue
        span_of.update(zip(blk["lines"], blk.pop("_spans")))
        if labels:
            runs, cur = [], None
            for t in blk["lines"]:
                if t in labels and (cur is None or labels[t] != cur[0]):
                    cur = (labels[t], [])
                    runs.append(cur)
                cur[1].append(t)
            runs2 = []
            for kind, ts in runs:
                n[kind] = n.get(kind, 0) + 1
                runs2.append(("%s%d" % (kind, n[kind]), ts))
            runs = runs2
            basis = "模型按歌词分的（推断）"
        else:
            runs, basis = [], "按歌词重复推的（推断，不一定准）"
            chorus_seen = sum(1 for p in parts if p["kind"].startswith("副歌"))
            for f, ts in _by_repeat(m, blk):
                if f:
                    kind = "副歌"
                elif chorus_seen >= 2 and ts[0] > m["duration"] * 0.45:
                    kind = "桥段"
                else:
                    kind = "主歌"
                chorus_seen += kind == "副歌"
                n[kind] = n.get(kind, 0) + 1
                runs.append(("%s%d" % (kind, n[kind]), ts))
        for kind, ts in runs:
            parts.append({"start": ts[0], "end": span_of[ts[-1]][1], "kind": kind, "lines": ts, "basis": basis})
    # 只出现一次的不带编号（副歌1 → 副歌）
    base = lambda k: re.sub(r"\d+$", "", k)
    tally = {}
    for p in parts:
        tally[base(p["kind"])] = tally.get(base(p["kind"]), 0) + 1
    for p in parts:
        if p["lines"] and tally[base(p["kind"])] == 1:
            p["kind"] = base(p["kind"])
    parts.sort(key=lambda p: p["start"])
    for a, b in zip(parts, parts[1:]):   # 同一大块里相邻两段首尾接上
        if a["lines"] and b["lines"] and b["start"] - a["end"] < 3:
            a["end"] = b["start"]
    for p in parts:
        p["start"], p["end"] = round(p["start"], 2), round(p["end"], 2)
        _extras(p, m)
    return parts


def mmss(t):
    return "%d:%02d" % (int(t) // 60, int(t) % 60)


# ---------- 唱的部分交给模型按歌词分（10-24：只靠"歌词重复"分不准——第二遍主歌词一样就跟副歌连成一大段，
# 开头一句后面再出现就被当成一小段副歌）。前奏、间奏、尾奏仍按人声有无量，模型只分唱的那几大块 ----------

LABEL_Q = """下面是一首歌的歌词，按时间排好：每句前面是开始时间，括号里是这句在整首里一共出现几次。
已经按"人声有没有在唱"分成了几大块（用 ===== 隔开，块和块之间是前奏、间奏或尾奏）。
请把每一大块再分成段，每段选一个：主歌、预副歌、副歌、桥段、说唱、念白、哼唱。同类按出现顺序编号（主歌1、主歌2、副歌1……），整首只有一段的也写 1。
按歌词内容、重复和位置判断：副歌通常是整首反复出现、点题的那几句；主歌每遍词不一样、在副歌前面；桥段在后半、只出现一次、跟前面都不像。
用 JSON 回答，不要多余的话：{"parts": [{"start": "这一段第一句的时间（必须是下面某句前面写的时间，原样照抄）", "kind": "副歌1"}]}
按时间顺序；每一大块的第一句必须是某一段的开始。

%s"""


def label_prompt(m):
    lines = [l for l in m.get("lines") or [] if l.get("text")]
    if not lines:
        return None
    cnt = {}
    for l in lines:
        cnt[_norm(l["text"])] = cnt.get(_norm(l["text"]), 0) + 1
    vocal = [p for p in _blocks(m) if p["lines"]]
    out = []
    for p in vocal:
        out.append("=====")
        for l in lines:
            if l["t"] in p["lines"]:
                out.append("[%s] %s（%d 次）" % (_ts(l["t"]), l["text"], cnt[_norm(l["text"])]))
    return LABEL_Q % "\n".join(out)


def _ts(t):
    return "%d:%04.1f" % (int(t) // 60, t % 60)   # 带一位小数：同一秒里两句也分得开


def parse_labels(m, raw):
    """模型回的 [{"start": "m:ss.s", "kind": ...}] → {句子时间: 哪一类}。编号不信它的（常把三段副歌都叫副歌1），
    只取类别，编号在 song_map 里按出现顺序重排。时间按最接近的那句对（差 1.5 秒以内）。
    一大块的开头它没给名字的，接上一段的类别。一个都对不上返回 None（退回按歌词重复推）。"""
    import json as _j
    try:
        got = _j.loads(re.search(r"\{.*\}", raw, re.S).group(0))["parts"]
    except Exception:
        return None
    ts = sorted(l["t"] for l in m.get("lines") or [] if l.get("text"))
    out = {}
    for x in got:
        mm = re.match(r"^\s*\[?(\d+):(\d+(?:\.\d+)?)", str(x.get("start", "")))   # 有的模型会带上 [ ]
        kind = re.sub(r"\d+$", "", str(x.get("kind", "")).strip())
        if not mm or kind not in ("主歌", "预副歌", "副歌", "桥段", "说唱", "念白", "哼唱"):
            continue
        want = int(mm.group(1)) * 60 + float(mm.group(2))
        t = min(ts, key=lambda v: abs(v - want))
        if abs(t - want) <= 1.5:
            out[t] = kind
    if not out:
        return None
    for blk in _blocks(m):
        if blk["lines"] and blk["lines"][0] not in out:
            prev = [out[t] for t in sorted(out) if t < blk["lines"][0]]
            out[blk["lines"][0]] = prev[-1] if prev else "主歌"
    return out



def map_line(parts):
    return " · ".join("%s %s–%s" % (p["kind"], mmss(p["start"]), mmss(p["end"])) for p in parts)
