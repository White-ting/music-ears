"""music-ears 第二、三层：模型"听" + 审查。只用标准库（切音频：macOS 用系统的 afconvert，别的系统纯 Python 降采样）。

  python listen.py 歌曲目录        要先有 measure.json；写 listen.json（每段听感 + 审查）

规矩（试出来的）：调、拍速、音高这些数字只认脚本量的——试过的四个模型报的调和拍速全不一样。
模型只描述听感（几个人唱、音色、气息、情绪、唱法、歌词和旋律怎么呼应）；审查换一家模型，每段自己也听一遍，
从第一家的描述里摘说法，拿"自己听到的 + 脚本的数"核对，不对的给改正和原因，不改数字。
（一开始让审查只看文字、整首一次对账：审查模型偷懒，把脚本的数抄一遍再跟脚本比，全说对。所以改成逐段带音频。）

模型和钥匙在 ~/.config/music-ears/config.json 里设（见 config.py）。
"""
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

SEG_MAX = 32      # 一段最长多少秒
WHO = ""


def conf():
    return {"listen": config.model("listen"), "review": config.model("review")}


def mmss(t):
    return "%d:%02d" % (int(t) // 60, int(t) % 60)


# ---------- 切段 ----------

def segments(m):
    """有歌词：按段落边界把相邻几句并成一段（≤ SEG_MAX 秒）；没歌词：按段落切。"""
    lines = m["lines"]
    bounds = [s["start"] for s in m["sections"]]
    segs = []
    if lines:
        cur = None
        for l in lines:
            end = min(l["end"], l["t"] + 12)
            new_sec = cur and any(cur["start"] < b <= l["t"] for b in bounds)
            if cur is None or new_sec or end - cur["start"] > SEG_MAX:
                cur = {"start": l["t"], "end": end, "lines": []}
                segs.append(cur)
            cur["lines"].append(l)
            cur["end"] = end
    else:
        for s in m["sections"]:
            a = s["start"]
            while a < s["end"] - 3:
                segs.append({"start": a, "end": min(s["end"], a + SEG_MAX), "lines": [], "label": s["label"]})
                a += SEG_MAX
    return [x for x in segs if x["end"] - x["start"] >= 3]   # 不到 3 秒的零碎不听


def read_pcm(path):
    """读 wav 的 PCM：自己找 fmt / data 两块。afconvert 转 m4a 时写的是 WAVE_FORMAT_EXTENSIBLE（65534），
    标准库 wave 不认，这里两种都按 PCM 读。"""
    import struct
    with open(path, "rb") as f:
        buf = f.read()
    i, fmt, data = 12, None, None
    while i + 8 <= len(buf):
        cid, n = buf[i:i + 4], struct.unpack("<I", buf[i + 4:i + 8])[0]
        if cid == b"fmt ":
            _, ch, sr, _, _, bits = struct.unpack("<HHIIHH", buf[i + 8:i + 24])
            fmt = (sr, ch, bits // 8)
        elif cid == b"data":
            data = buf[i + 8:i + 8 + n]
        i += 8 + n + (n & 1)
    if not fmt or data is None:
        raise ValueError("读不了这个 wav")
    return fmt + (data,)


def cut(wav_path, a, b):
    """从 audio.wav 切 [a, b) 秒，转成 16k 单声道 wav，返回字节。"""
    sr, ch, sw, data = read_pcm(wav_path)
    step = ch * sw
    frames = data[int(a * sr) * step:int(b * sr) * step]
    with tempfile.TemporaryDirectory() as td:
        raw, small = os.path.join(td, "a.wav"), os.path.join(td, "b.wav")
        with wave.open(raw, "wb") as o:
            o.setnchannels(ch); o.setsampwidth(sw); o.setframerate(sr); o.writeframes(frames)
        if sys.platform == "darwin" and os.path.exists("/usr/bin/afconvert") and not os.environ.get("MUSIC_EARS_NO_AFCONVERT"):
            subprocess.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", raw, small], check=True)
            return open(small, "rb").read()
    return to16k_mono(frames, sr, ch, sw)


def to16k_mono(frames, sr, ch, sw):
    """不是 macOS 时：纯 Python 混成单声道、降到 16k（每个输出点取它覆盖的那几个原始点的平均，顺带滤掉高频）。"""
    import array
    from itertools import accumulate
    if sw != 2:
        raise ValueError("只认 16 位的 wav")
    a = array.array("h")
    a.frombytes(frames[:len(frames) - len(frames) % (2 * ch)])
    if sys.byteorder == "big":
        a.byteswap()
    mono = a if ch == 1 else [sum(x) // ch for x in zip(*(a[k::ch] for k in range(ch)))]
    pre = [0] + list(accumulate(mono))
    r, n = sr / 16000, int(len(mono) * 16000 / sr)
    out = array.array("h")
    for k in range(n):
        s, e = int(k * r), max(int(k * r) + 1, int((k + 1) * r))
        e = min(e, len(mono))
        out.append(int((pre[e] - pre[s]) / max(1, e - s)))
    if sys.byteorder == "big":
        out.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as o:
        o.setnchannels(1); o.setsampwidth(2); o.setframerate(16000); o.writeframes(out.tobytes())
    return buf.getvalue()


# ---------- 调模型 ----------

def ask(c, audio, text, timeout=150):
    """c：listen 或 review 的配置。audio 为 None 就只发文字。返回模型的文字。"""
    b64 = base64.b64encode(audio).decode() if audio else None
    if c["proto"] == "gemini":
        parts = ([{"inline_data": {"mime_type": "audio/wav", "data": b64}}] if audio else []) + [{"text": text}]
        url = "%s/models/%s:generateContent" % (c["base"], c["model"])
        body = {"contents": [{"role": "user", "parts": parts}]}
        hdr = {"x-goog-api-key": c["key"], "Authorization": "Bearer " + c["key"]}
    else:
        content = ([{"type": "input_audio", "input_audio": {"data": c.get("audio_prefix", "") + b64, "format": "wav"}}] if audio else []) \
            + [{"type": "text", "text": text}]
        url = c["base"] + "/chat/completions"
        body = {"model": c["model"], "messages": [{"role": "user", "content": content}], "stream": True, "modalities": ["text"]}
        hdr = {"Authorization": "Bearer " + c["key"]}
    req = urllib.request.Request(url, json.dumps(body).encode(), {**hdr, "Content-Type": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
            if c["proto"] == "gemini":
                d = json.loads(raw)
                return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            return "".join((json.loads(l[6:]).get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                           for l in raw.splitlines() if l.startswith("data: {"))
        except Exception as e:  # 中转站偶尔断连、503：歇一下再试，最多三次
            last = e
            time.sleep(3 + attempt * 4)
    raise RuntimeError("模型连不上：%s" % repr(last)[:160])


def json_part(s):
    m = re.search(r"\{.*\}", s, re.S)
    return json.loads(m.group(0)) if m else None


# ---------- 听 ----------

def facts(seg):
    """这一段脚本量的数，写成给模型看的几行。"""
    out = []
    for l in seg["lines"]:
        bits = ["旋律 " + (l["melody"] or "—")]
        if l["range"]:
            bits.append("音域 %s–%s" % tuple(l["range"]))
        if l["long_notes"]:
            bits.append("长音 " + "、".join("%s %.1f秒%s" % (x["note"], x["dur"], "带颤音" if x["vibrato"] else "") for x in l["long_notes"]))
        if l["breaths"]:
            bits.append("中间换气 %d 处" % len(l["breaths"]))
        if l["off_pitch"]:
            bits.append("偏离半音 30 音分以上的音 %d 个" % len(l["off_pitch"]))
        if l["loudness_db"] is not None:
            bits.append("响度 %.0f dB" % l["loudness_db"])
        out.append("[%s] %s ｜ %s" % (mmss(l["t"]), l["text"], "；".join(bits)))
    return "\n".join(out) or "（这段没有歌词，可能是前奏、间奏或尾奏）"


LISTEN_Q = """你在帮一个 AI 理解一首歌。这是{who}《{title}》的一段（{a}–{b}）。
脚本已经量好了客观的数（调 {key}、拍速约 {bpm} BPM，下面是这段每句的旋律简谱和测量）：
{facts}

请只描述你听到的感受，不要报调、拍速、音高这些数字（数字以脚本为准）。每一项不超过 40 个字，每句的 note 不超过 30 个字，写最要紧的。
用 JSON 回答，不要多余的话：
{{"singers": "几个人在唱、什么人（男/女/童声、大概年龄）", "timbre": "音色", "breath": "气息",
 "emotion": "情绪和这段里的变化", "technique": "唱法（真假声、转音、滑音、气声……没有就写无）",
 "accompaniment": "伴奏是什么、怎么配合", "lyric_melody": "歌词和旋律怎么呼应（哪个字落在高音/长音上、为什么）",
 "lines": [{{"t": "句子开头的时间 m:ss", "note": "这一句特别值得说的地方，没有就空着"}}]}}"""


def listen_seg(c, wav, seg, m, title):
    q = LISTEN_Q.format(who=WHO, title=title, a=mmss(seg["start"]), b=mmss(seg["end"]), key=m["key"]["name"], bpm=m["tempo_bpm"], facts=facts(seg))
    txt = ask(c, cut(wav, seg["start"], seg["end"]), q)
    return json_part(txt) or {"raw": txt.strip()[:1200]}


# ---------- 审查 ----------

REVIEW_Q = """你是审查，在听《{title}》的一段（{a}–{b}）。脚本量的客观数（数字以它为准，你不能改）：
{facts}

另一个模型（下面叫"甲"）听这段写的描述：
{desc}

请你自己听这段，然后从**甲的描述原文**里摘出 2–4 条具体说法（比如某个字落在高音/长音上、上扬/下滑、颤音、气息稳不稳、
几个人在唱、男女童声、情绪在哪里推到最高、伴奏是什么），逐条用"你听到的"和"脚本的数"核对。
简谱里数字越大、带 ' 越高，带 , 越低；"长音"是 0.6 秒以上的音。不要摘脚本里本来就写着的数字，要摘甲自己的判断。

用 JSON 回答，不要多余的话：
{{"claims": [{{"claim": "甲的原话（摘录）", "heard": "你听到的", "evidence": "对着脚本哪个数", "ok": true/false}}],
  "fix": "如果有不对的，改正后的说法（不超过 40 字）；都对就空着",
  "cause": "不对的原因：描述错了 / 脚本可能量偏了（分轨没分干净、滑音被切成短音……）/ 空"}}"""


def review_seg(c, wav, seg, heard, m, title):
    q = REVIEW_Q.format(title=title, a=mmss(seg["start"]), b=mmss(seg["end"]), facts=facts(seg),
                        desc=json.dumps(heard, ensure_ascii=False))
    return json_part(ask(c, cut(wav, seg["start"], seg["end"]), q)) or {}


def overall(c, segs, heard, m, title):
    """整首的一段话：从各段听感里归纳（不再听音频，省钱）。"""
    q = ("下面是%s《%s》各段的听感（脚本：调 %s、拍速约 %s BPM）。请用三四句话概括整首：谁在唱、整体音色和情绪、情绪怎么推进、"
         "最有特点的地方。不要报数字。直接写，不要标题。\n\n%s") % (
        WHO, title, m["key"]["name"], m["tempo_bpm"],
        "\n".join("%s–%s：%s" % (mmss(s["start"]), mmss(s["end"]), json.dumps(h, ensure_ascii=False)) for s, h in zip(segs, heard)))
    return ask(c, None, q).strip()


def main(d):
    m = json.load(open(os.path.join(d, "measure.json"), encoding="utf-8"))
    meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    title = meta.get("name") or "这首歌"
    global WHO   # 自己唱的：让模型知道在听用户本人（不是原唱歌手），描述这个人的声音和唱法
    WHO = "%s（不是原唱歌手，是用户本人）唱的" % config.name() if meta.get("source") == "sung" else ""
    wav = os.path.join(d, "audio.wav")
    c = conf()
    segs = segments(m)
    t0 = time.time()

    def safe(s):
        try:
            return listen_seg(c["listen"], wav, s, m, title)
        except Exception as e:
            return {"error": str(e)[:120]}
    with ThreadPoolExecutor(4) as ex:
        heard = list(ex.map(safe, segs))
    t1 = time.time()

    def rsafe(a):
        s, h = a
        if "error" in h:
            return {"error": "这段没听成，没审"}
        try:
            return review_seg(c["review"], wav, s, h, m, title)
        except Exception as e:
            return {"error": str(e)[:120]}
    with ThreadPoolExecutor(4) as ex:
        reviews = list(ex.map(rsafe, zip(segs, heard)))
    t2 = time.time()
    # 有一段没听成就不做总结：试过两段都没听成时，模型照样写出有模有样的总结——是编的
    summary = ""
    if heard and all(isinstance(h, dict) and "error" not in h and "raw" not in h for h in heard):
        try:
            summary = overall(c["listen"], segs, heard, m, title)
        except Exception:
            pass
    out = {"listen_model": c["listen"]["model"], "review_model": c["review"]["model"],
           "segments": [{"start": s["start"], "end": s["end"], "lines": [l["t"] for l in s["lines"]], "heard": h, "review": r}
                        for s, h, r in zip(segs, heard, reviews)],
           "summary": summary,
           "seconds": {"listen": round(t1 - t0), "review": round(t2 - t1)}}
    json.dump(out, open(os.path.join(d, "listen.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = sum(1 for r in reviews for x in r.get("claims", []) if x.get("ok") is False)
    n = sum(len(r.get("claims", [])) for r in reviews)
    print("听好了：%d 段；审查核了 %d 条，对不上 %d 条；听 %d 秒、审查 %d 秒" % (len(segs), n, bad, t1 - t0, t2 - t1))


if __name__ == "__main__":
    main(sys.argv[1])
