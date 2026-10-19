# 第三方说明

music-ears 本身不包含下面这些库和模型的代码或权重，运行时由使用者自己安装、下载。这里列出它们和各自的许可证，供参考。

| 名称 | 用途 | 许可证 |
|---|---|---|
| [Demucs](https://github.com/facebookresearch/demucs)（htdemucs 预训练模型） | 分出人声和伴奏 | MIT |
| [librosa](https://github.com/librosa/librosa) | 拍速、拍点、调、和弦、音频读取 | ISC |
| [torchcrepe](https://github.com/maxrmorrison/torchcrepe)（CREPE 模型） | 人声逐帧音高 | MIT |
| [PyTorch](https://github.com/pytorch/pytorch) | 跑上面两个模型 | BSD-3-Clause |
| [NumPy](https://github.com/numpy/numpy) | 数值计算 | BSD-3-Clause |
| [SciPy](https://github.com/scipy/scipy) | 信号处理 | BSD-3-Clause |
| [python-soundfile](https://github.com/bastibe/python-soundfile) | 读写 wav | BSD-3-Clause |

黑胶播放器网页（web/）里带着的：

| 名称 | 用途 | 许可证 |
|---|---|---|
| [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) 1.4.4（web/qrcode.min.js，原样附带） | 网易云扫码登录的二维码 | MIT，© Kazuhiko Arase |
| [Lucide](https://lucide.dev) 图标（web/vinyl.js 里的几个 SVG 路径） | 播放、暂停、搜索等图标 | ISC |

调用的在线模型（通义千问 Omni、Gemini）按各自服务商的条款使用，需要使用者自己的钥匙。

网易云音乐相关的接口不是官方公开接口，见 README 里的说明。
