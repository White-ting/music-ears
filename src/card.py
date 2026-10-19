"""把 meta.json + measure.json 写成乐谱卡 card.md（给 AI 读的文字版）。只用标准库。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402


def mmss(t):
    return "%d:%02d" % (int(t) // 60, int(t) % 60)


def seg_block(sg):
    """一段的听感（几句歌词前面一小块）；审查改过的标出来。"""
    h = sg["heard"] if isinstance(sg["heard"], dict) else {}
    r = sg.get("review") or {}
    if "error" in h:
        return "〔%s–%s 这段没听成〕" % (mmss(sg["start"]), mmss(sg["end"]))
    keys = [("emotion", "情绪"), ("breath", "气息"), ("technique", "唱法"), ("timbre", "音色"), ("accompaniment", "伴奏"), ("lyric_melody", "词和旋律")]
    bits = ["%s：%s" % (n, h[k]) for k, n in keys if h.get(k) and h[k] not in ("无", "")]
    text = "〔%s–%s 听感〕%s" % (mmss(sg["start"]), mmss(sg["end"]), "　".join(bits) or h.get("raw", ""))
    bad = [x for x in r.get("claims", []) if isinstance(x, dict) and x.get("ok") is False]
    if bad:
        text += "\n〔审查重听后改正〕%s（原说法：%s；%s）" % (r.get("fix") or "—", "；".join(x.get("claim", "")[:30] for x in bad), r.get("cause") or "")
    return text


def render(d):
    meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    m = json.load(open(os.path.join(d, "measure.json"), encoding="utf-8"))
    key = m["key"]
    title = meta.get("name") or "（没有歌名）"
    who = " / ".join(meta.get("artists") or [])
    me = config.name()
    src = {"free": "网易云免费音频", "login": "网易云（登录取的）", "recording": "%s发的录音（原曲）" % me, "sung": "%s自己唱的录音" % me}.get(meta.get("source"), meta.get("source", ""))
    out = ["# 乐谱卡：%s%s%s" % (("%s唱《%s》" % (me, title)) if meta.get("source") == "sung" else title, "" if meta.get("source") == "sung" else (" — " + who) if who else "",
                               "（原唱 %s）" % who if meta.get("source") == "sung" and who else ""), ""]
    if meta.get("album"):
        out.append("- 专辑：" + meta["album"])
    out += [
        "- 音频来源：%s（这份量的是 %s 长）" % (src, mmss(m["duration"])),
        "- 调：%s（简谱 %s，调判断的把握 %.2f）" % (key["name"], key.get("jianpu", ""), key["confidence"]),
        "- 拍速：约 %s BPM%s" % (m["tempo_bpm"], "（自己唱的，节奏随性、录音也短，仅供参考）" if meta.get("source") == "sung" else ""),
        "- 整体音准：人声比标准音高%s（量音符前已整体校正）" % ("偏高 %d 音分" % m["tuning_cents"] if m.get("tuning_cents", 0) > 0 else "偏低 %d 音分" % -m["tuning_cents"] if m.get("tuning_cents", 0) < 0 else "基本没偏"),
        "- 人声音域：%s" % (" – ".join(m["vocal_range"]) if m.get("vocal_range") else "没量到人声"),
        "- 结构（按声音相似度切的，同一个字母 = 同一段重复）：" + " ".join("%s(%s)" % (s["label"], mmss(s["start"])) for s in m["sections"]),
    ]
    lis = None
    if os.path.exists(os.path.join(d, "listen.json")):
        lis = json.load(open(os.path.join(d, "listen.json"), encoding="utf-8"))
        if lis.get("skipped"):
            out.append("- 没有听感：%s。这张卡只有脚本量的数字；配好钥匙后删掉这首的缓存文件夹再做一次，就有模型听的部分" % lis["skipped"].split("：")[0])
        if lis.get("summary"):
            out.append("- 整体听感（%s 听，%s 逐段重听审查）：%s" % (lis["listen_model"], lis["review_model"], lis["summary"].replace("\n", " ")))
    if m.get("lyric_by_ear"):
        out.append("- 歌词是听%s唱的字、逐句对上的（对不上的那段写〔没对上歌词〕）" % me)
    elif m.get("lyric_offset"):
        out.append("- 歌词时间整体挪了 %+.1f 秒对上录音" % m["lyric_offset"])
    if not m.get("lyric_aligned"):
        out.append("- 歌词没对上（录音多半是从歌中间开始的），下面只有旋律，没有逐句歌词")
    out += ["", "怎么读：旋律是简谱唱名（1–7，#/b 是升降），' 是高八度、, 是低八度；数字是脚本量的，"
            "滑音有时会被切成一个短音，所以偶尔冒出的调外音不一定真唱了。〔听感〕是模型听的（不报数字），另一家模型逐段重听审查过。", ""]
    seg_at = {}       # 每段从哪句开始 → 那段
    line_note = {}    # 每句的听感点评
    if lis:
        for sg in lis.get("segments") or []:
            if sg["lines"]:
                seg_at[sg["lines"][0]] = sg
            for x in (sg["heard"].get("lines") or []) if isinstance(sg["heard"], dict) else []:
                if isinstance(x, dict) and x.get("note"):
                    line_note[str(x.get("t"))] = x["note"]
    if m["lines"]:
        out.append("## 逐句")
        out.append("")
        for l in m["lines"]:
            sg = seg_at.get(l["t"])
            if sg:
                out.append(seg_block(sg))
            notes = []
            if l["long_notes"]:
                notes.append("长音 " + "、".join("%s %.1f秒%s" % (x["note"], x["dur"], "（颤音）" if x["vibrato"] else "") for x in l["long_notes"]))
            if l["breaths"]:
                notes.append("换气 %d 处" % len(l["breaths"]))
            if l["off_pitch"]:
                notes.append("偏了 30 音分以上的音 %d 个" % len(l["off_pitch"]))
            out.append("[%s] %s" % (mmss(l["t"]), l["text"]))
            out.append("    旋律：%s" % (l["melody"] or "（这句没量到人声）"))
            if l["range"]:
                out.append("    音域：%s–%s　和弦：%s" % (l["range"][0], l["range"][1], " ".join(l["chords"]) or "—"))
            if notes:
                out.append("    " + "；".join(notes))
            if line_note.get(mmss(l["t"])):
                out.append("    听：" + line_note[mmss(l["t"])])
        out.append("")
    else:
        out.append("## 旋律（没有歌词，按时间）")
        line, start = [], None
        for n in m["notes"]:
            if start is None or n["t"] - start > 8:
                if line:
                    out.append("[%s] %s" % (mmss(start), " ".join(line)))
                line, start = [], n["t"]
            line.append(n["degree"])
        if line:
            out.append("[%s] %s" % (mmss(start), " ".join(line)))
    open(os.path.join(d, "card.md"), "w", encoding="utf-8").write("\n".join(out) + "\n")
    return os.path.join(d, "card.md")


if __name__ == "__main__":
    print(render(sys.argv[1]))
