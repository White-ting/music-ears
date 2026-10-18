"""自己唱的录音：听出唱的是哪几句，对上歌词（自己唱多半从中间、或挑几句唱，按第一句对齐会错位）。

  python align.py 歌曲目录      要先有 measure.json（音符）和 lyric.json（网易云歌词）或 lyric.lrc；写 lyric.aligned.lrc

做法：按音符之间的停顿把唱的切成一句句 → 每句交给语音识别（默认百炼 qwen3-asr-flash）认字 →
跟歌词逐句比相似度（一句也可能正好跨两句歌词），往后找优先（多半顺着唱），够像才算 →
写一份按录音时间排的歌词。对不上的那段写"〔没对上歌词〕"，旋律照样显示。
"""
import base64
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
from listen import cut  # noqa: E402

GAP = 0.35      # 音符之间空这么久算换句（试过 0.7、一段到 14 秒：36 秒只切出 3 段、中间几句被吞）
MAXLEN = 7      # 一段最长几秒
MAXK = 4        # 一段最多对上连续几句
MIN_OK = 0.45   # 相似度够这么多才算对上


def asr_conf():
    return config.model("asr")


def asr(c, wav):
    body = {"model": c["model"], "stream": False, "asr_options": {"enable_itn": False},
            "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {
                "data": "data:audio/wav;base64," + base64.b64encode(wav).decode()}}]}]}
    req = urllib.request.Request(c["base"] + "/chat/completions", json.dumps(body).encode(),
                                 {"Authorization": "Bearer " + c["key"], "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}) if c.get("no_proxy", True) else urllib.request.ProxyHandler())   # 默认直连（国内服务）
    for _ in range(2):
        try:
            with opener.open(req, timeout=30) as r:
                return str(json.loads(r.read())["choices"][0]["message"]["content"] or "").strip()
        except Exception:
            pass
    return ""


def phrases(notes):
    out, cur = [], None
    for n in notes:
        if cur and n["t"] - cur["end"] < GAP and n["t"] + n["dur"] - cur["start"] < MAXLEN:
            cur["end"] = n["t"] + n["dur"]
            cur["onsets"].append(n["t"])
        else:
            cur = {"start": n["t"], "end": n["t"] + n["dur"], "onsets": [n["t"]]}
            out.append(cur)
    return [p for p in out if p["end"] - p["start"] >= 0.8]


def plain(t):
    return re.sub(r"[^\w]", "", t).lower()


def lyric_lines(d):
    p = os.path.join(d, "lyric.json")
    if os.path.exists(p):
        raw = (json.load(open(p, encoding="utf-8")).get("lrc") or {}).get("lyric", "")
    else:   # 别的平台的歌，用户自己给的 LRC
        raw = open(os.path.join(d, "lyric.lrc"), encoding="utf-8").read()
    out = []
    for ln in raw.splitlines():
        text = re.sub(r"\[[^\]]*\]", "", ln).strip()
        if text and not re.match(r"^[^：:]{1,12}[：:]", text):
            out.append(text)
    return out


def match(heard, lines, prev):
    """→ (起始句号, 几句, 相似度)。一段可能是一句，也可能跨相邻两句。顺着唱的多，往后找的加一点分。"""
    h = plain(heard)
    if len(h) < 2:
        return None
    best = None
    for i in range(len(lines)):
        for k in range(1, MAXK + 1):
            if i + k > len(lines):
                continue
            ref = plain("".join(lines[i:i + k]))
            if not ref:
                continue
            r = SequenceMatcher(None, h, ref).ratio()
            r += 0.06 if prev is not None and i > prev else 0
            if best is None or r > best[2]:
                best = (i, k, r)
    return best if best and best[2] >= MIN_OK else None


def main(d):
    m = json.load(open(os.path.join(d, "measure.json"), encoding="utf-8"))
    lines = lyric_lines(d)
    ph = phrases(m["notes"])
    if not ph or not lines:
        print("没法对：没量到人声或没有歌词")
        return
    wav = os.path.join(d, "stems", "vocals.wav") if os.path.exists(os.path.join(d, "stems", "vocals.wav")) else os.path.join(d, "audio.wav")
    c = asr_conf()
    with ThreadPoolExecutor(4) as ex:
        heard = list(ex.map(lambda p: asr(c, cut(wav, max(0, p["start"] - 0.2), p["end"] + 0.3)), ph))
    out, prev, ok = [], None, 0
    for p, h in zip(ph, heard):
        mt = match(h, lines, prev)
        if not mt:
            out.append((p["start"], "〔没对上歌词〕"))
            continue
        i, k, _ = mt
        ok += 1
        if k == 1 and prev == i and out and out[-1][1] == lines[i]:
            continue   # 紧挨着的又是同一句：这句中间换了口气被切成两段，接着算
        prev = i + k - 1
        # 跨几句：按字数在这段的音符里分（第 n 个字大约落在第 n 个音上）
        lens = [max(1, len(plain(x))) for x in lines[i:i + k]]
        tot, acc, on = sum(lens), 0, p["onsets"]
        for j in range(k):
            at = on[min(len(on) - 1, int(acc / tot * len(on)))] if j else p["start"]
            out.append((at, lines[i + j]))
            acc += lens[j]
    with open(os.path.join(d, "lyric.aligned.lrc"), "w", encoding="utf-8") as f:
        for t, text in out:
            f.write("[%02d:%05.2f]%s\n" % (int(t // 60), t % 60, text))
    json.dump([{"t": p["start"], "heard": h} for p, h in zip(ph, heard)], open(os.path.join(d, "align.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("对歌词：%d 段，对上 %d 段" % (len(ph), ok))


if __name__ == "__main__":
    main(sys.argv[1])
