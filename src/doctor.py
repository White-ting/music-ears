"""music-ears doctor：装好了没有，一条一条查，缺什么就说怎么补。

  music-ears doctor          全查（会在 Docker 里试着 import 一遍测量要的库，十几秒）
  music-ears doctor --quick  不进 Docker 试库
"""
import os
import subprocess
import sys

import config

HOME = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
OK, NO, WARN = "✓", "✗", "！"
IMAGE = "python:3.12-slim"


def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "找不到命令：%s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "等了 %d 秒没回" % timeout


def main(a):
    quick = "--quick" in a
    bad = 0

    def say(mark, what, fix=""):
        nonlocal bad
        if mark == NO:
            bad += 1
        print("%s %s" % (mark, what))
        if fix:
            for ln in fix.splitlines():
                print("    " + ln)

    c = config.load()
    docker = c["docker"]
    win = os.name == "nt"

    # Python
    v = sys.version_info
    say(OK if v >= (3, 9) else NO, "Python %d.%d" % (v[0], v[1]), "" if v >= (3, 9) else "要 Python 3.9 或更新")

    # 转音频格式
    if sys.platform == "darwin" and os.path.exists("/usr/bin/afconvert"):
        say(OK, "转音频：系统自带的 afconvert")
    else:
        say(OK, "转音频：在 Docker 里用 PyAV（下面查库时一起查）")

    # Docker
    code, out = run([docker, "version", "--format", "{{.Server.Version}}"], 20)
    if code == 0:
        say(OK, "Docker 在跑（%s）" % out.strip().splitlines()[-1])
    else:
        hint = "打开 Docker Desktop，等左下角变绿再试" if win or sys.platform == "darwin" else "启动 docker 服务"
        if code == 127:
            hint = "没装 Docker，或者 docker 命令不在 PATH 里（装了的话在 config.json 写 \"docker\": \"完整路径\"）"
        say(NO, "Docker 连不上", hint + "\n原话：" + out.strip()[-160:])
        docker = None

    if docker:
        code, _ = run([docker, "image", "inspect", IMAGE], 20)
        say(OK if code == 0 else WARN, "镜像 %s %s" % (IMAGE, "已下载" if code == 0 else "还没下载"),
            "" if code == 0 else "第一次量歌时会自动下载（约 50MB），也可以先跑：docker pull " + IMAGE)

    # venv（建在 Docker 里，所以是 Linux 的目录结构）
    # venv/bin/python 是指向容器里 /usr/local/bin 的链接，在本机看是断的，所以认 pyvenv.cfg
    if not os.path.exists(os.path.join(HOME, "venv", "pyvenv.cfg")):
        mount = '"${PWD}"' if win else '"$PWD"'
        say(NO, "venv 还没建（测量要的库装在这里）",
            "在 music-ears 目录里跑：\n"
            "docker run --rm -v %s:/w %s sh -c \"python -m venv /w/venv && /w/venv/bin/pip install -r /w/requirements.txt\"" % (mount, IMAGE))
    elif docker and not quick:
        code, out = run([docker, "run", "--rm", "-v", HOME + ":/w", IMAGE, "/w/venv/bin/python", "-c",
                         "import demucs, librosa, torchcrepe, soundfile, av; print('ok')"], 180)
        if code == 0 and "ok" in out:
            say(OK, "venv 里测量要的库都在（demucs、librosa、torchcrepe、soundfile、av）")
        else:
            miss = [m for m in ("demucs", "librosa", "torchcrepe", "soundfile", "av") if "No module named '%s'" % m in out]
            say(NO, "venv 里缺库" + ("：" + "、".join(miss) if miss else ""),
                "补装：docker run --rm -v <music-ears 目录>:/w %s /w/venv/bin/python -m pip install -r /w/requirements.txt\n原话：%s"
                % (IMAGE, out.strip()[-200:]))
    else:
        say(OK, "venv 在（--quick：没进 Docker 试库）")

    models = os.path.join(HOME, "models")
    say(OK if os.path.isdir(models) and os.listdir(models) else WARN,
        "分轨、音高模型" + ("已下载" if os.path.isdir(models) and os.listdir(models) else "还没下载"),
        "" if os.path.isdir(models) and os.listdir(models) else "第一次量歌时自动下载到 models/，约 500MB")

    # 钥匙
    for name, what in (("listen", "听（模型听每一段）"), ("review", "审查（另一家模型重听核对）"), ("asr", "认字（只有 --sung 自己唱的才用）")):
        try:
            m = config.model(name)
            say(OK, "钥匙 · %s：%s" % (what, m["model"]))
        except SystemExit as e:
            say(WARN, "钥匙 · %s：没配" % what, str(e) + "\n不配也能出卡，只是没有这一部分")

    # 网易云
    try:
        import netease
        if netease.cookie():
            try:
                me = netease.account()
                say(OK, "网易云已登录：%s%s" % (me.get("name", ""), "（会员）" if me.get("vip") else "") if me else "网易云登录信息在，但没认出账号（可能过期了）",
                    "" if me else "重新扫码：music-ears serve 打开播放器 → 歌单 → 扫码登录")
            except Exception as e:
                say(WARN, "网易云登录信息在，但没连上网易云：%s" % str(e)[:80])
        else:
            say(WARN, "网易云没登录", "不登录也能用：免费的歌照样能拆，会员歌要登录或者自己给录音\n要登录：music-ears serve 打开播放器 → 歌单 → 扫码登录")
    except Exception as e:
        say(WARN, "网易云模块出错：%s" % str(e)[:100])

    print()
    print("没问题，可以用了。" if not bad else "有 %d 项要先处理（✗ 那几行）。" % bad)
    return 1 if bad else 0
