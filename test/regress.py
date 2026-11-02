"""回归测试：改了量的算法、换了模型以后，拿几首固定的歌比一比有没有变差（10-24 起）。

歌有版权，不进仓库：测试集就是你本机 cache/ 里挑的几首，基准存在 cache/_regress/baseline.json（也不进仓库）。
挑歌时尽量覆盖容易出错的：独唱、合唱、童声、说唱、强混响、纯伴奏、转调、自由节奏、前奏间奏长的、长音换气多的。

  python3 test/regress.py save [文件夹名...]     把这几首现在的结果存成基准（不给就是 cache 里全部）
  python3 test/regress.py check                 跟基准比：调、拍速、音域、音符数、歌词句数、逐句旋律、段落地图
  python3 test/regress.py check --remeasure     先把每首复制到临时文件夹重新量一遍（要 Docker，一首几分钟），再比

只比脚本量的东西和段落地图；模型写的听感每次措辞都不一样，不比。哪项变了打出来，人来判断是变好还是变坏
（两次一样不等于对：基准本身也是脚本量的，第一次存之前最好人对着歌听一遍，记在 notes 里）。
"""
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HOME, "src"))
import config   # noqa: E402
import songmap  # noqa: E402

CACHE = config.cache_dir() if hasattr(config, "cache_dir") else os.path.join(HOME, "cache")
BASE = os.path.join(CACHE, "_regress", "baseline.json")


def summary(d):
    m = json.load(open(os.path.join(d, "measure.json"), encoding="utf-8"))
    meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    labels = None
    if os.path.exists(os.path.join(d, "map.json")):
        labels = {float(t): k for t, k in json.load(open(os.path.join(d, "map.json"), encoding="utf-8"))["labels"].items()}
    return {
        "name": meta.get("name"), "duration": m["duration"], "key": m["key"]["name"], "key_conf": m["key"].get("confidence"),
        "tempo": m["tempo_bpm"], "range": m.get("vocal_range"), "notes": len(m.get("notes") or []),
        "lines": len(m.get("lines") or []), "melody": [l["melody"] for l in m.get("lines") or []],
        "map": songmap.map_line(songmap.song_map(m, labels)),
        "map_measured": songmap.map_line([p for p in songmap.song_map(m, labels) if not p["lines"]]),
    }


def compare(old, new):
    out = []
    for k in ("duration", "key", "tempo", "range", "notes", "lines"):
        a, b = old.get(k), new.get(k)
        if k == "tempo" and a and b and abs(a - b) <= 2:
            continue
        if k == "notes" and a and b and abs(a - b) <= max(3, a * 0.05):
            continue
        if a != b:
            out.append("%s：%s → %s" % (k, a, b))
    if old.get("melody") and new.get("melody"):
        sim = difflib.SequenceMatcher(None, " | ".join(old["melody"]).split(), " | ".join(new["melody"]).split(), autojunk=False).ratio()
        if sim < 0.97:
            out.append("逐句旋律相似度 %.2f" % sim)
    if old.get("map_measured") != new.get("map_measured"):
        out.append("前奏/间奏/尾奏：%s → %s" % (old.get("map_measured"), new.get("map_measured")))
    if old.get("map") != new.get("map"):
        out.append("段落地图变了（主歌副歌是推断，变了不一定是坏事）")
    return out


def remeasure(d):
    """复制到临时文件夹重新量（不动原来的缓存）。"""
    tmp = tempfile.mkdtemp(prefix="regress-", dir=CACHE)
    for f in os.listdir(d):
        if f in ("meta.json", "lyric.json", "lyric.lrc", "lyric.aligned.lrc", "audio.mp3", "audio.wav", "map.json"):
            shutil.copy(os.path.join(d, f), tmp)
    r = subprocess.run([sys.executable, os.path.join(HOME, "music-ears"), "--remeasure-dir", tmp], capture_output=True, text=True)
    if r.returncode or not os.path.exists(os.path.join(tmp, "measure.json")):
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:])
    return tmp


def main(a):
    if not a or a[0] not in ("save", "check"):
        print(__doc__)
        return
    base = json.load(open(BASE, encoding="utf-8")) if os.path.exists(BASE) else {}
    if a[0] == "save":
        want = a[1:] or [x for x in os.listdir(CACHE) if os.path.exists(os.path.join(CACHE, x, "measure.json")) and not x.startswith(("_", "regress-"))]
        for x in want:
            base[x] = summary(os.path.join(CACHE, x))
            base[x]["notes_by_human"] = base.get(x, {}).get("notes_by_human", "")
        os.makedirs(os.path.dirname(BASE), exist_ok=True)
        json.dump(base, open(BASE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("存了 %d 首的基准：%s" % (len(want), BASE))
        return
    if not base:
        raise SystemExit("还没有基准：先 python3 test/regress.py save")
    bad = 0
    for x, old in sorted(base.items()):
        d = os.path.join(CACHE, x)
        tmp = None
        try:
            if "--remeasure" in a:
                tmp = d = remeasure(d)
            diffs = compare(old, summary(d))
        except Exception as e:
            diffs = ["没跑成：%s" % str(e)[:200]]
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
        bad += bool(diffs)
        print("%s《%s》%s" % ("✗" if diffs else "✓", old.get("name"), "" if not diffs else "\n    " + "\n    ".join(diffs)))
    print("%d 首里 %d 首有变化" % (len(base), bad))


if __name__ == "__main__":
    main(sys.argv[1:])
