# 黑胶播放器和一起听

music-ears 自带一个**黑胶播放器**网页，可以和 AI **一起听歌**：歌放到哪，AI 就"听"到哪。

## 一、打开

```
music-ears serve
```

浏览器打开 <http://127.0.0.1:8790/>。

- **登录网易云（可选）**：歌单页点「扫码登录」，用网易云音乐 App 扫码确认。登录信息只存在跑 music-ears 的这台电脑上（`~/.config/music-ears/netease-cookie`）。不登录也能搜歌、放免费的歌，只是看不到你的歌单，会员歌只能放试听片段。
- **放歌**：右上角进歌单 / 搜索 / 正在放。每首右边「⋯」：下一首播放、加到列表末尾、一起听。
- **播放页**：唱片在转，背景是封面放大后的磨砂模糊；点一下唱片切到**全屏歌词**（当前句高亮，带翻译，可以手滑，松手 5 秒后滚回唱到的那句）。左上角缩小后是一个浮着的小唱片，能拖；点一下回到播放页，长按冒出「关掉」和「一起听」。
- 锁屏、控制中心、耳机线控都能切歌、暂停（MediaSession）。
- **AI 点歌**：接了 MCP 的 AI 用 `play` 给你点的歌，会排进"下一首"（或者马上放），屏幕下方冒一句它想说的话。
- 播放地址大约二十分钟过期，过期了会自动重新要一个、从原来的秒数接着放。

### 在手机上用

手机和电脑在同一个 Wi-Fi 下：

```
music-ears serve --host 0.0.0.0 --token 一串口令
```

口令可以用 `openssl rand -hex 16` 生成，也可以写进 `~/.config/music-ears/config.json`：`"serve": {"token": "..."}`。让别的设备连时**必须**设口令，不设不让开。手机打开 `http://电脑的局域网IP:8790/?key=口令`，之后口令记在浏览器里，不用再带。可以在 Safari 里「添加到主屏幕」，当一个 App 用。

在外面也想用，可以给这台电脑配 Tailscale，或者用 Cloudflare Tunnel 开一个 HTTPS 地址。

## 二、一起听

1. 在歌的「⋯」里，或者长按小唱片，选「一起听」。
2. 这首拆过，就直接开始放；没拆过，后台先拆，大约 5–8 分钟（要 Docker 和模型钥匙，见 README），拆好再放。
3. 放的过程中，播放器每隔几秒，以及暂停、拖动、换句的时候，会把位置报给后端。
4. **AI 那边**读这个地址：

```
GET http://127.0.0.1:8790/api/now?format=text
```

拿到的是一段可以直接附在对话里的文字：

```
〔一起听：小明在放《某首歌》 — 某歌手，放到 1:02 / 4:00，这句：「……」
前后十秒（摘自乐谱卡）：
  这一段的听感：……
  [0:54] 某句歌词 ｜ 旋律 4 5 6 4 4 ｜ 音域 A3–C#4　和弦：F#m ｜ 听 ……
  [0:57] 某句歌词（在这句） ｜ ……〕
```

不带 `format=text` 是 JSON（`state`、`pos`、`dur`、`line`、`window` 逐行、`text`）。没在一起听时 `now` 是 `null`；放完了 `state` 是 `ended`。

怎么让 AI 每次都看到：
- **你自己的聊天前端或机器人**：用户每发一条消息，后端先 GET 一次 `/api/now?format=text`，不是空的就附在消息后面。
- **能调用工具的 AI**（Claude Code 等）：让它想知道放到哪的时候自己 `curl` 一下。
- 乐谱卡全文：`GET /api/card?dir=歌曲编号`。"一起听"开始前先把整张卡给 AI 读一遍，效果最好。

## 三、嵌进你自己的网页

播放器是一个独立的脚本，不依赖任何框架：

```html
<link rel="stylesheet" href="http://127.0.0.1:8790/vinyl.css">
<script src="http://127.0.0.1:8790/vinyl.js"></script>
<script>
  const vn = MusicEarsVinyl.mount({
    base: 'http://127.0.0.1:8790/',   // music-ears serve 的地址，结尾带 /
    open: false,                      // 先不弹出来，你的按钮里调 vn.open()
    onListen: async (track) => {      // 可选：自己接"一起听"，比如先把乐谱卡递给 AI，resolve 后开始放
      // track = {id, name, artists, album, cover, dur}
    },
  });
</script>
```

- 样式都挂在 `.mev` 下面，不会跟你的页面互相影响。播放页盖满整个窗口，小唱片浮在右下角。
- `vn.open()` / `vn.minimize()` / `vn.close()`；`vn.play(track)` 放一首；`vn.listen(track)` 一起听；`vn.state()` 拿现在放到哪（`{track, pos, dur, playing, line}`）。
- 跨域嵌入时注意：浏览器默认不让别的网站带 cookie 访问，最简单是把你的前端和 music-ears serve 放在同一个域名下（反向代理），或者 serve 只在本机用、不设口令。

## 四、接口

都回 JSON（`{"ok": true, ...}` 或 `{"ok": false, "error": "..."}`），除了封面和乐谱卡全文。

| 地址 | 做什么 |
|---|---|
| `GET /api/vinyl?op=playlists` | 你的歌单 |
| `GET /api/vinyl?op=tracks&arg=歌单编号` | 歌单里的歌 |
| `GET /api/vinyl?op=search&arg=关键词` | 搜索（按 歌名 > 歌手 > 专辑 重排，没搜"翻唱/现场/伴奏"时把这些往后放） |
| `GET /api/vinyl?op=url&arg=歌曲编号` | 播放地址 |
| `GET /api/vinyl?op=lyric&arg=歌曲编号` | LRC 歌词和翻译 |
| `GET /api/login?op=start` / `check&key=…` / `me` | 网易云扫码登录：801 等扫码、802 已扫待确认、803 成功、800 过期 |
| `GET /cover?u=网易云图片地址&s=100\|200\|500\|512` | 封面代取 + 缓存（有些 App 里直接加载网易云的图会一直挂着） |
| `GET /api/cards` | 拆过的卡 |
| `GET /api/card?dir=文件夹名` | 某张卡全文（Markdown） |
| `POST /api/listen {id, name, artists}` | 开始一起听：拆过回 `have: true`，没拆过后台开始拆 |
| `GET /api/listen?id=…` | 拆到哪了：`making` / `ready` / `failed` / `none` |
| `POST /api/pos {id, pos, dur, line, playing}` | 播放器报位置；`{id, ended: true}` 放完，`{id, stop: true}` 不听了 |
| `GET /api/now[?format=text]` | 给 AI：现在放到哪 + 前后十秒的乐谱卡 |
| `GET /api/cmd?since=序号` | 播放器每 4 秒问一次：AI（MCP 的 `play`）有没有点歌 |

命令行也有对应的：`music-ears vinyl playlists | tracks | url | lyric | search`、`music-ears login start | check | me`、`music-ears list --json`、`music-ears card 文件夹名`。

## 关于网易云

这里用到的接口都不是网易云官方公开的接口，只为个人学习使用。音频由你的浏览器直接从网易云取，不经过、也不保存在 music-ears 里。请尊重版权：别拿它批量下载、转存、传播音乐。
