"""造一首"标准答案"：A 小调，90 BPM，旋律、和弦、拍子都已知，用来验 measure.py 量得准不准。"""
import json, sys
import numpy as np, soundfile as sf
SR, BPM = 44100, 90.0
beat = 60 / BPM
# 旋律（MIDI, 拍数）：A3 C4 E4 D4 | C4 B3 A3(2) | E4 G4 A4 G4 | E4 D4 C4(2) ×2 句
mel = [(57,1),(60,1),(64,1),(62,1),(60,1),(59,1),(57,2),(64,1),(67,1),(69,1),(67,1),(64,1),(62,1),(60,2)] * 2
chords = [("Am",[57,60,64]),("F",[53,57,60]),("C",[48,52,55]),("G",[55,59,62])] * 4   # 每个和弦 4 拍
lead_in = 2 * beat
total = lead_in + sum(b for _, b in mel) * beat + 2 * beat
t = np.arange(int(total * SR)) / SR
voice = np.zeros_like(t); pad = np.zeros_like(t); drum = np.zeros_like(t)
pos = lead_in
for m, b in mel:
    n0, n1 = int(pos * SR), int((pos + b * beat - 0.04) * SR)
    tt = t[n0:n1] - pos
    f = 440 * 2 ** ((m - 69) / 12) * (1 + (0.006 * np.sin(2 * np.pi * 5.5 * tt) if b >= 2 else np.zeros_like(tt)))
    ph = 2 * np.pi * np.cumsum(f) / SR
    env = np.minimum(1, tt / 0.03) * np.minimum(1, (tt[-1] - tt) / 0.05 + 1e-9)
    voice[n0:n1] += env * (np.sin(ph) + 0.5 * np.sin(2 * ph) + 0.25 * np.sin(3 * ph) + 0.12 * np.sin(4 * ph))
    pos += b * beat
for k, (name, ns) in enumerate(chords):
    a = lead_in + k * 4 * beat
    n0, n1 = int(a * SR), min(len(t), int((a + 4 * beat) * SR))
    for m in ns:
        f = 440 * 2 ** ((m - 69) / 12)
        pad[n0:n1] += 0.25 * np.sin(2 * np.pi * f * t[n0:n1]) + 0.1 * np.sin(2 * np.pi * 2 * f * t[n0:n1])
k = 0
while k * beat < total:
    n0 = int(k * beat * SR); n = min(int(0.12 * SR), len(t) - n0)
    tt = np.arange(n) / SR
    drum[n0:n0 + n] += 0.8 * np.sin(2 * np.pi * 60 * tt * (1 - tt * 3)) * np.exp(-tt * 30)
    k += 1
mix = 0.5 * voice / np.abs(voice).max() + 0.35 * pad / np.abs(pad).max() + 0.3 * drum
mix /= np.abs(mix).max() * 1.1
d = sys.argv[1]
sf.write(d + "/audio.wav", np.stack([mix, mix], 1), SR)
import os; os.makedirs(d + "/dry", exist_ok=True)
v = voice / np.abs(voice).max() * 0.8
sf.write(d + "/dry/audio.wav", np.stack([v, v], 1), SR)   # 只有旋律，不过分轨，单验音高
# 歌词：每句 8 拍一行
lines, pos = [], lead_in
for i in range(4):
    lines.append("[%02d:%05.2f]第%d句" % (int(pos // 60), pos % 60, i + 1)); pos += 8 * beat
open(d + "/lyric.lrc", "w").write("\n".join(lines))
json.dump({"bpm": BPM, "key": "A 小调", "melody_midi": [m for m, _ in mel], "chords": [c for c, _ in chords]}, open(d + "/answer.json", "w"), ensure_ascii=False)
print("造好了", round(total, 1), "秒")
