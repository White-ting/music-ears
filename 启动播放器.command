#!/bin/zsh
# 双击启动黑胶播放器。用的时候这个窗口要一直开着。
cd "$(dirname "$0")"
python3 music-ears serve
