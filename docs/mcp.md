# 接给 AI：MCP

`music-ears serve` 同时是一个 MCP 服务，地址是 `/mcp`（streamable HTTP，只回 JSON，不开 SSE）。能接 MCP 的 AI（Claude Code、Claude Desktop、Claude 手机 App 的自定义连接器、Aru……）接上以后，可以自己搜歌、拆歌、读卡、跟你一起听。

## 工具

| 工具 | 做什么 |
|---|---|
| `search_song` | 在网易云搜歌，返回编号、歌名、歌手、专辑、时长 |
| `list_cards` | 拆过哪些（有乐谱卡的） |
| `make_card` | 开始拆一首（5–8 分钟，立刻返回"开始拆了"；过几分钟再 `get_card`） |
| `get_card` | 读乐谱卡：整张；或者给 `at`（比如 `1:20`）只读卡头和那个时间前后 `span` 秒（默认 20） |
| `play` | **给你点歌**：放进你的黑胶播放器。默认排在正在放的后面（不打断你），`mode=now` 才马上切；可以带一句话（`note`），播放器上会显示。播放器没开着的话，半小时内打开都会收到 |
| `now_playing` | 一起听：你在黑胶播放器里放到哪一句。两种报法，见下面 |

### 一起听的两种报法

| | **摘录模式** `window`（默认） | **书签模式** `book` |
|---|---|---|
| 像什么 | 每次指给 AI 看"这一段" | 先把整本书给 AI，之后只报页码 |
| 第一次 | 现在的位置 + 前后十秒的旋律、和弦、听感 | **整张乐谱卡** + 书签 |
| 之后每次 | 同上，每次都带摘录（约 700 字） | 只有书签：第几句、卡里哪一行、这句歌词（约 80 字） |
| 适合 | AI 记不住长内容，或者对话常被压缩 | AI 能把整张卡留在上下文里，一首歌里来回聊 |

选法：`~/.config/music-ears/config.json` 里写 `"serve": {"listen_mode": "book"}` 改默认；或者调用时给 `now_playing` 传 `mode`。书签模式下 AI 觉得卡丢了（比如对话被压缩过），传 `card: true` 会再给一遍整张卡。网页接口 `/api/now` 也认 `?mode=book`。

连上时服务会把一段用法说明（`instructions`）交给 AI，它知道先搜、再看拆没拆、拆的时候要等。

## 本机：Claude Code、Claude Desktop

先跑 `music-ears serve`，然后：

```
claude mcp add --transport http music-ears http://127.0.0.1:8790/mcp
```

Claude Desktop 在设置 → 开发者 → 编辑配置里加：

```json
{ "mcpServers": { "music-ears": { "type": "http", "url": "http://127.0.0.1:8790/mcp" } } }
```

（有的 Desktop 版本只认本地命令，那就用 `npx mcp-remote http://127.0.0.1:8790/mcp` 包一层。）

## 手机：Claude App、Aru

手机上的 App 连不到你电脑的 `127.0.0.1`，要一个**公网 HTTPS 地址**，并且**一定要设口令**：

1. 开服务：`music-ears serve --host 0.0.0.0 --token 一串口令`（口令用 `openssl rand -hex 16` 生成）。
2. 给它一个 HTTPS 地址。最省事的是 Cloudflare Tunnel：在 Cloudflare 的 Zero Trust → Networks → Tunnels 建一条隧道，在电脑上装好 cloudflared，公共主机名填一个子域名，服务填 `http://localhost:8790`。
3. MCP 地址就是 `https://你的子域名/mcp?key=口令`。
   - **Claude App**：claude.ai 网页版 → 设置 → 连接器 → 添加自定义连接器，填这个地址。手机 App 会跟着账号同步。
   - **Aru**：总设置 → 连接 → MCP，添加远程服务（streamable HTTP），填这个地址，测试连接后启用，再允许你的协作者使用，打开工具箱里的 MCP。
4. 播放器在手机上打开 `https://你的子域名/?key=口令`。Aru 里也可以在「应用与连接」把它当网页应用接进去。

口令带在地址里，等于钥匙：别把这个地址发到群里、贴进截图。不想用了，换一个口令重开 serve，旧地址就失效了。

## 已知的

- 拆一首要几分钟，比一般工具调用的等待时间长，所以 `make_card` 只负责开拆，AI 要过一会儿再来 `get_card`。
- 一起听要你在播放器里点「一起听」，`now_playing` 才有内容；同一个 serve 同一时间只记一首。
- 手机网页退到后台还放不放得出声，看浏览器和 App：Safari 和「添加到主屏幕」的网页一般能接着放；在别的 App 里打开的网页（比如 Aru 的网页应用）没测过。
