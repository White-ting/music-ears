"""在容器里把任意音频转成 16 位 wav：给不是 macOS 的电脑用（macOS 直接用系统的 afconvert）。
用 PyAV（pip 包 av，自带 ffmpeg 的解码器），mp3、m4a、flac、aac、ogg 都认。

  python convert.py 输入 输出 [采样率=44100] [声道：1 单声道，不写就是立体声]
"""
import sys
import wave

import av


def main(src, dst, rate=44100, mono=False):
    layout = "mono" if mono else "stereo"
    rs = av.AudioResampler(format="s16", layout=layout, rate=rate)
    with av.open(src) as c, wave.open(dst, "wb") as w:
        w.setnchannels(1 if mono else 2)
        w.setsampwidth(2)
        w.setframerate(rate)
        for frame in c.decode(audio=0):
            for o in rs.resample(frame):
                w.writeframes(o.to_ndarray().tobytes())
        for o in rs.resample(None):
            w.writeframes(o.to_ndarray().tobytes())


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], int(a[2]) if len(a) > 2 else 44100, len(a) > 3 and a[3] == "1")
