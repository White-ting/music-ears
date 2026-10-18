"""music-ears 第一层：脚本"量"。给一首歌（音频 + 可选的 LRC 歌词），量出客观的东西，写成 measure.json。

  python measure.py 歌曲目录        目录里要有 audio.*（mp3/m4a/wav），可选 lyric.json（网易云格式）或 lyric.lrc

量什么：
  分轨（Demucs htdemucs，人声 / 伴奏）→ 拍速和拍点、调、按拍的和弦（伴奏轨）
  → 人声逐帧音高（torchcrepe）→ 切成音符（音名、几分音偏差、起止）→ 换气、长音、颤音、音域
  → 段落（按相似度切）→ 按歌词每一行挂上去。
只量，不判断好坏；"听感"留给第二层的模型。
"""
import json
import os
import re
import subprocess
import sys

import librosa
import numpy as np
import torch
import torchcrepe

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl–Kessler 调式轮廓
MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
SR = 22050


def note_name(midi):
    m = int(round(midi))
    return NAMES[m % 12] + str(m // 12 - 1)


def find_audio(d):
    # wav 优先：容器里没装 ffmpeg，mp3/m4a 先在 Mac 上用 afconvert 转成 audio.wav
    for ext in ("wav", "flac", "mp3", "m4a", "aac", "mp4"):
        p = os.path.join(d, "audio." + ext)
        if os.path.exists(p):
            return p
    raise SystemExit("目录里没有 audio.*")


def separate(audio, d):
    """Demucs htdemucs 分两轨：vocals / no_vocals。做过就不重做。
    直接调模型、用 soundfile 读写（新版 torchaudio 的读写要另装 torchcodec，demucs 4.0.1 的命令行用不了）。"""
    import soundfile as sf
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    out = os.path.join(d, "stems")
    v, a = os.path.join(out, "vocals.wav"), os.path.join(out, "no_vocals.wav")
    if os.path.exists(v) and os.path.exists(a):
        return v, a
    model = get_model("htdemucs")
    model.eval()
    wav, sr = librosa.load(audio, sr=model.samplerate, mono=False)
    if wav.ndim == 1:
        wav = np.stack([wav, wav])
    x = torch.tensor(wav, dtype=torch.float32)
    ref = x.mean(0)
    x = (x - ref.mean()) / (ref.std() + 1e-8)
    with torch.no_grad():
        src = apply_model(model, x[None], device="cpu", progress=False, num_workers=0)[0]
    src = src * (ref.std() + 1e-8) + ref.mean()
    names = model.sources
    voc = src[names.index("vocals")]
    rest = sum(src[k] for k in range(len(names)) if names[k] != "vocals")
    os.makedirs(out, exist_ok=True)
    sf.write(v, voc.numpy().T, model.samplerate)
    sf.write(a, rest.numpy().T, model.samplerate)
    return v, a


def estimate_tempo(y, sr):
    """打击乐部分的起音强度做自相关，取最强的周期，再折进 70–140 BPM（太快减半、太慢翻倍）。
    librosa 默认的 beat_track 在一首实测的歌上给 117.5，人耳听着"空了几下"；最强周期 184.6 听着太密，
    折半 92.3 最舒服；合成的 90 BPM 测试量出 89.1。"""
    _, yp = librosa.effects.hpss(y)
    oe = librosa.onset.onset_strength(y=yp, sr=sr)
    fps = sr / 512
    ac = librosa.autocorrelate(oe, max_size=int(fps * 3))
    lags = np.arange(len(ac)) / fps
    ok = (lags > 60 / 240) & (lags < 60 / 40)
    if not ok.any() or ac[ok].max() <= 0:
        return 0.0
    bpm = 60 / lags[ok][np.argmax(ac[ok])]
    while bpm >= 140:
        bpm /= 2
    while bpm < 70:
        bpm *= 2
    return float(bpm)


def detect_key(chroma):
    prof = chroma.mean(axis=1)
    best = None
    for i in range(12):
        for mode, p in (("大调", MAJ), ("小调", MIN)):
            r = np.corrcoef(prof, np.roll(p, i))[0, 1]
            if best is None or r > best[0]:
                best = (r, i, mode)
    r, tonic, mode = best
    return {"tonic": NAMES[tonic], "mode": mode, "name": NAMES[tonic] + " " + mode, "confidence": round(float(r), 3)}, tonic


def chords_by_beat(chroma, beat_frames):
    """每拍一个和弦：大三、小三和弦模板里挑最像的；太弱就记 N（没有和弦/静）。"""
    tmpl = []
    for i in range(12):
        for q, iv in (("", (0, 4, 7)), ("m", (0, 3, 7))):
            t = np.zeros(12)
            t[[(i + x) % 12 for x in iv]] = 1
            tmpl.append((NAMES[i] + q, t / np.linalg.norm(t)))
    sync = librosa.util.sync(chroma, beat_frames, aggregate=np.median)
    out = []
    for col in sync.T:
        n = np.linalg.norm(col)
        if n < 1e-6:
            out.append("N")
            continue
        sims = [(float(np.dot(col / n, t)), name) for name, t in tmpl]
        s, name = max(sims)
        out.append(name if s > 0.6 else "N")
    return out


def vocal_pitch(vocals_path):
    y, sr = librosa.load(vocals_path, sr=16000, mono=True)
    audio = torch.tensor(y)[None]
    hop = 160  # 10ms
    f0, per = torchcrepe.predict(audio, sr, hop, 65.0, 1000.0, "full", batch_size=1024,
                                 device="cpu", return_periodicity=True)
    per = torchcrepe.filter.median(per, 3)
    f0 = torchcrepe.filter.mean(f0, 3)
    f0, per = f0[0].numpy(), per[0].numpy()
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0][:len(f0)]
    db = librosa.amplitude_to_db(rms + 1e-9, ref=1.0)
    voiced = (per > 0.5) & (db > -45)
    midi = np.where(voiced, librosa.hz_to_midi(np.maximum(f0, 1e-3)), np.nan)
    times = np.arange(len(midi)) * 0.01
    # 整体偏音（实测一首童声合唱满屏 #2 #4）：整首唱得偏高/偏低一点时，四舍五入会整片落到旁边的半音。
    # 有声帧离最近半音的偏差取中位数，整体扣掉再切音符；偏了多少写进结果
    dev = midi[~np.isnan(midi)] - np.round(midi[~np.isnan(midi)])
    tuning = float(np.median(dev)) if len(dev) else 0.0
    return times, midi - tuning, db, tuning


def segment_notes(times, midi):
    """连续有声、音高在半音以内的一段算一个音；短于 80ms 不算。"""
    notes, i, n = [], 0, len(midi)
    while i < n:
        if np.isnan(midi[i]):
            i += 1
            continue
        j = i
        ref = midi[i]
        while j + 1 < n and not np.isnan(midi[j + 1]) and abs(midi[j + 1] - np.nanmedian(midi[i:j + 1])) < 0.6:
            j += 1
        seg = midi[i:j + 1]
        dur = (j - i + 1) * 0.01
        if dur >= 0.08:
            med = float(np.median(seg))
            nearest = round(med)
            notes.append({"t": round(float(times[i]), 2), "dur": round(dur, 2), "note": note_name(med),
                          "midi": nearest, "cents": int(round((med - nearest) * 100)),
                          "vibrato": vibrato(seg) if dur >= 0.4 else None})
        i = j + 1
    return notes


def vibrato(seg):
    """长音里音高 4.5–8 Hz 的抖动，幅度（分音，峰到峰的一半）≥ 15 才算。"""
    c = (seg - np.mean(seg)) * 100
    c = c - np.convolve(c, np.ones(15) / 15, mode="same")
    spec = np.abs(np.fft.rfft(c * np.hanning(len(c))))
    freqs = np.fft.rfftfreq(len(c), 0.01)
    band = (freqs >= 4.5) & (freqs <= 8)
    if not band.any():
        return None
    k = np.argmax(np.where(band, spec, 0))
    amp = float(np.percentile(np.abs(c), 90))
    if amp < 15 or spec[k] < 3 * np.median(spec[1:]):
        return None
    return {"rate_hz": round(float(freqs[k]), 1), "depth_cents": int(amp)}


def breaths(times, midi, db, start, end):
    """一行里人声的空隙 ≥ 0.15 秒算换气（行首行尾不算）。"""
    idx = (times >= start) & (times < end)
    on = ~np.isnan(midi[idx])
    t = times[idx]
    if not on.any():
        return []
    first, last = np.argmax(on), len(on) - 1 - np.argmax(on[::-1])
    out, k = [], first
    while k <= last:
        if not on[k]:
            m = k
            while m <= last and not on[m]:
                m += 1
            if (m - k) * 0.01 >= 0.15:
                out.append({"t": round(float(t[k]), 2), "dur": round((m - k) * 0.01, 2)})
            k = m
        else:
            k += 1
    return out


def sections(y, sr):
    """按音色+和声相似度切段，再按段和段像不像贴 A/B/C 标签（一样的标签 = 同一段旋律重复）。"""
    hop = 512
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=13)
    feat = np.vstack([librosa.util.normalize(chroma, axis=0), librosa.util.normalize(mfcc, axis=1)])
    dur = len(y) / sr
    k = max(4, min(12, int(dur // 20)))
    bounds = librosa.segment.agglomerative(feat, k)
    bt = librosa.frames_to_time(bounds, sr=sr, hop_length=hop).tolist() + [dur]
    segs, means = [], []
    for a, b in zip(bt[:-1], bt[1:]):
        fa, fb = librosa.time_to_frames([a, b], sr=sr, hop_length=hop)
        means.append(feat[:, fa:max(fa + 1, fb)].mean(axis=1))
        segs.append([round(a, 2), round(b, 2)])
    labels, protos = [], []
    for m in means:
        for li, p in enumerate(protos):
            if np.dot(m, p) / (np.linalg.norm(m) * np.linalg.norm(p) + 1e-9) > 0.97:
                labels.append(chr(65 + li))
                break
        else:
            protos.append(m)
            labels.append(chr(65 + len(protos) - 1))
    return [{"start": s[0], "end": s[1], "label": l} for s, l in zip(segs, labels)]


def load_lyrics(d):
    raw = ""
    p = os.path.join(d, "lyric.json")
    if os.path.exists(os.path.join(d, "lyric.aligned.lrc")):   # 自己唱的：已经按录音的时间对好了（align.py）
        raw = open(os.path.join(d, "lyric.aligned.lrc"), encoding="utf-8").read()
    elif os.path.exists(p):
        raw = (json.load(open(p, encoding="utf-8")).get("lrc") or {}).get("lyric", "")
    elif os.path.exists(os.path.join(d, "lyric.lrc")):
        raw = open(os.path.join(d, "lyric.lrc"), encoding="utf-8").read()
    lines = []
    for ln in raw.splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", ln)
        text = re.sub(r"\[[^\]]*\]", "", ln).strip()
        for m, s in stamps:
            lines.append((int(m) * 60 + float(s), text))
    lines.sort()
    # 作词作曲那种信息行（含冒号的开头几行）标出来，不当歌词
    return [{"t": round(t, 2), "text": x, "credit": bool(re.match(r"^[^：:]{1,12}[：:]", x))} for t, x in lines]


def degree(midi, do, base):
    """简谱唱名：do 是"1"的音级（小调用关系大调，主音唱 6）。base 是"不加点"那个八度的起点（MIDI），
    按这首歌人声的中间音域定，大多数音不带点；高八度加 '，低八度加 ,。"""
    scale = {0: "1", 1: "#1", 2: "2", 3: "#2", 4: "3", 5: "4", 6: "#4", 7: "5", 8: "#5", 9: "6", 10: "b7", 11: "7"}
    rel = midi - base
    octv = rel // 12
    s = scale[(midi - do) % 12]
    return s + ("'" * octv if octv > 0 else "," * (-octv))


def degree_base(midis, do):
    """挑一个"1"开头的八度，让落在里面的音最多。"""
    best = None
    for o in range(1, 8):
        b = do + 12 * o
        k = sum(1 for m in midis if b <= m < b + 12)
        if best is None or k > best[0]:
            best = (k, b)
    return best[1]


def main(d):
    audio = find_audio(d)
    if os.environ.get("NO_SEP") == "1":   # 清唱 / 测试：不分轨，整段当人声
        vocals = accomp = audio
    else:
        vocals, accomp = separate(audio, d)
    y, sr = librosa.load(audio, sr=SR, mono=True)
    ya, _ = librosa.load(accomp, sr=SR, mono=True)
    dur = len(y) / sr

    tempo = estimate_tempo(y, sr)
    _, beats = librosa.beat.beat_track(y=y, sr=sr, bpm=tempo, units="frames")
    beat_t = librosa.frames_to_time(beats, sr=sr)
    chroma_a = librosa.feature.chroma_cqt(y=ya, sr=sr)
    key, tonic = detect_key(chroma_a)
    chords = chords_by_beat(chroma_a, beats)
    loud = librosa.amplitude_to_db(librosa.feature.rms(y=y)[0] + 1e-9, ref=1.0)
    loud_t = librosa.frames_to_time(np.arange(len(loud)), sr=sr)

    times, midi, vdb, tuning = vocal_pitch(vocals)
    notes = segment_notes(times, midi)
    voiced = [n["midi"] for n in notes]
    do = (tonic + 3) % 12 if key["mode"] == "小调" else tonic   # 小调按关系大调记，1 = 关系大调主音
    base = degree_base(voiced, do) if voiced else 60 + do
    key["jianpu"] = "1=" + NAMES[do]
    for n in notes:
        n["degree"] = degree(n["midi"], do, base)

    lyr = load_lyrics(d)
    sung = [x for x in lyr if not x["credit"] and x["text"]]
    # 用户发的音频（录屏、自己唱）：开头不一定跟原曲对齐。用第一个人声音符对第一句歌词，差在 ±8 秒内就整体平移歌词
    offset = 0.0
    meta = {}
    if os.path.exists(os.path.join(d, "meta.json")):
        meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    aligned = os.path.exists(os.path.join(d, "lyric.aligned.lrc"))
    if meta.get("source") in ("recording", "sung") and sung and notes and not aligned:
        off = notes[0]["t"] - sung[0]["t"]
        if abs(off) <= 8 or meta.get("source") == "sung":   # 自己清唱多半没前奏、从第一句唱起：差多少都按第一句对上
            offset = round(off, 2)
            for x in sung:
                x["t"] = round(x["t"] + offset, 2)
        else:
            sung = []   # 对不上（多半是从中间录的），先不挂歌词，乐谱卡里会写明
    sung = [x for x in sung if x["t"] < dur]
    # 最后一句后面没有下一句来截断：原来一直算到歌曲结尾，尾奏、和声、结尾哼的音全被算进最后一句。
    # 现在最长按"一句的常见长度"的 1.6 倍（至少 8 秒、最多 20 秒），中间停了 2.5 秒以上就当这句唱完了
    gaps = sorted(sung[i + 1]["t"] - sung[i]["t"] for i in range(len(sung) - 1))
    last_cap = min(20.0, max(8.0, 1.6 * gaps[len(gaps) // 2])) if gaps else 12.0
    lines = []
    for i, x in enumerate(sung):
        end = sung[i + 1]["t"] if i + 1 < len(sung) else min(dur, x["t"] + last_cap)
        ln = [n for n in notes if x["t"] <= n["t"] < end]
        if i + 1 == len(sung):
            for k in range(1, len(ln)):
                if ln[k]["t"] - (ln[k - 1]["t"] + ln[k - 1]["dur"]) >= 2.5:
                    end, ln = ln[k - 1]["t"] + ln[k - 1]["dur"] + 0.5, ln[:k]
                    break
        sing_end = (ln[-1]["t"] + ln[-1]["dur"] + 0.5) if ln else end   # 和弦只算到这句唱完，后面接间奏的不算进来
        ch = [chords[k] for k, bt in enumerate(beat_t[:len(chords)]) if x["t"] <= bt < min(end, sing_end)]
        ch_seq = [c for k, c in enumerate(ch) if c != "N" and (k == 0 or c != ch[k - 1])]
        lm = (loud_t >= x["t"]) & (loud_t < end)
        lines.append({
            "t": x["t"], "end": round(end, 2), "text": x["text"],
            "melody": " ".join(n["degree"] for n in ln),
            "range": [note_name(min(n["midi"] for n in ln)), note_name(max(n["midi"] for n in ln))] if ln else None,
            "long_notes": [{"t": n["t"], "note": n["note"], "dur": n["dur"], "vibrato": n["vibrato"]} for n in ln if n["dur"] >= 0.6],
            "off_pitch": [{"t": n["t"], "note": n["note"], "cents": n["cents"]} for n in ln if abs(n["cents"]) >= 30 and n["dur"] >= 0.15],
            "breaths": breaths(times, midi, vdb, x["t"], end),
            "chords": ch_seq,
            "loudness_db": round(float(np.mean(loud[lm])), 1) if lm.any() else None,
        })

    out = {
        "duration": round(dur, 2),
        "lyric_offset": offset,
        "lyric_aligned": bool(sung) or not lyr,
        "lyric_by_ear": aligned,
        "tempo_bpm": round(tempo, 1),
        "tuning_cents": int(round(tuning * 100)),
        "key": key,
        "vocal_range": [note_name(min(voiced)), note_name(max(voiced))] if voiced else None,
        "sections": sections(y, sr),
        "lines": lines,
        "notes": notes,
    }
    with open(os.path.join(d, "measure.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("量好了：%s，%s BPM，%d 个音，%d 行歌词" % (key["name"], out["tempo_bpm"], len(notes), len(lines)))


if __name__ == "__main__":
    main(sys.argv[1])
