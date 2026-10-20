# 给黑胶播放器换外观

播放器的颜色、唱片、封面形状、歌词、进度条都由 30 多个 **CSS 变量**决定，默认值就是现在的样子。想换外观时，只改你想改的那几个，其余保持默认。

这份说明写给想自己动手的人，也写给帮你改的 AI：把这一页发给它，说清楚想要什么感觉，让它照着写一段 CSS。

## 一、写在哪

**用 `music-ears serve` 自带的播放器**：在 `~/.config/music-ears/` 下新建 `skin.css`（Windows 在 `%USERPROFILE%\.config\music-ears\skin.css`），保存后刷新页面就能看到效果，不用重启 serve。不想用了，删掉或者清空这个文件就会变回默认。这个文件在 music-ears 目录外面，更新 music-ears 时不会被覆盖。

**嵌进你自己的网页**：在引入 `vinyl.css` 之后，再写一段同样的 CSS：

```html
<link rel="stylesheet" href="http://127.0.0.1:8790/vinyl.css">
<style>
  .mev { --mev-accent: #e0607e; }
</style>
```

不管写在哪里，都要写在 `.mev { … }` 里面，因为变量是挂在 `.mev` 上的。

## 二、最小的例子

只换强调色和唱片封面的形状：

```css
.mev {
  --mev-accent: #7aa2f7;
  --mev-label-radius: 14%;
}
```

## 三、全部变量

每一项都可以不写，不写就用右边的默认值。

### 整体

| 变量 | 默认 | 管什么 |
|---|---|---|
| `--mev-font` | 系统字体（苹方 / 微软雅黑） | 字体 |
| `--mev-accent` | `#e8936f` | 强调色：小唱片上"一起听"的标记、拆卡进度条 |
| `--mev-fg` | `#fff` | 主要文字 |
| `--mev-dim` | `rgba(255,255,255,.6)` | 次要文字：歌手、时间 |
| `--mev-icon` | `rgba(255,255,255,.9)` | 图标、按钮 |
| `--mev-radius` | `12px` | 列表项的圆角 |

### 背景

| 变量 | 默认 | 管什么 |
|---|---|---|
| `--mev-bg` | `#121110` | 播放页底色 |
| `--mev-bg-fallback` | `#2a2522` | 没有封面时的背景 |
| `--mev-bg-filter` | `blur(70px) saturate(1.5) brightness(.5)` | 封面铺满背景时怎么处理：模糊、饱和度、亮度。写 `none` 就是清楚的原图 |
| `--mev-bg-shade` | 上浅下深的黑色渐变 | 背景上再盖的一层，让字看得清 |

### 唱片

| 变量 | 默认 | 管什么 |
|---|---|---|
| `--mev-disc-size` | `min(76vw, 320px, 52vh)` | 唱片多大 |
| `--mev-disc-inner` | `#1b1b1b` | 唱片从中心 |
| `--mev-disc-outer` | `#090909` | 到边缘的颜色 |
| `--mev-disc-groove` | `rgba(255,255,255,.035)` | 唱片纹路 |
| `--mev-disc-shine` | `rgba(255,255,255,.06)` | 唱片上的反光 |
| `--mev-disc-shadow` | `0 24px 60px rgba(0,0,0,.5)` | 唱片下面的影子 |
| `--mev-label-size` | `64%` | 中间封面占唱片多大 |
| `--mev-label-radius` | `50%` | 封面的圆角：`50%` 是圆的，`12%` 是圆角方块，`0` 是直角方块 |
| `--mev-spin` | `18s` | 转一圈几秒，数字越小转得越快 |
| `--mev-arm` | 白色渐变 | 唱臂的颜色，写一个颜色或者一个渐变都行 |

### 歌词

| 变量 | 默认 | 管什么 |
|---|---|---|
| `--mev-title-size` | `17px` | 顶上歌名的字号 |
| `--mev-lyric-size` | `17px` | 歌词字号 |
| `--mev-lyric` | `rgba(255,255,255,.42)` | 其他歌词的颜色 |
| `--mev-lyric-cur` | `#fff` | 正在唱的那句 |
| `--mev-lyric-scale` | `1.06` | 正在唱的那句放大多少，`1` 就是不放大 |

### 进度条和按钮

| 变量 | 默认 | 管什么 |
|---|---|---|
| `--mev-track` | `rgba(255,255,255,.22)` | 进度条的底 |
| `--mev-track-fill` | `rgba(255,255,255,.9)` | 进度条已经放过的部分；歌单页选中的标签也用它 |
| `--mev-glass` | `rgba(255,255,255,.14)` | 中间大播放键的玻璃底 |
| `--mev-surface` | `rgba(255,255,255,.08)` | 标签栏的底、按下去时的底色 |

### 列表、菜单、小卡片

| 变量 | 默认 | 管什么 |
|---|---|---|
| `--mev-cur-item` | `#f6c9b4` | 列表里正在放的那首的歌名 |
| `--mev-menu-bg` | `rgba(44,40,38,.94)` | 「⋯」菜单的底 |
| `--mev-card-bg` | `#f6f2ee` | 扫码登录、"要不要先拆"这类小卡片的底 |
| `--mev-card-fg` | `#1d1b1a` | 小卡片上的字 |
| `--mev-toast-bg` | `rgba(30,28,26,.92)` | 底部提示、拆卡进度条的底 |

颜色可以写 `#rrggbb`、`rgb(…)`、`rgba(…)`，要半透明就用 `rgba`。

## 四、几套现成的

**浅色奶油**（浅底深字，背景不用封面模糊）：

```css
.mev {
  --mev-fg: #2b2622;
  --mev-dim: rgba(43,38,34,.55);
  --mev-icon: rgba(43,38,34,.85);
  --mev-bg: #f4eee6;
  --mev-bg-fallback: #e9e0d4;
  --mev-bg-filter: blur(80px) saturate(1.2) brightness(1.35);
  --mev-bg-shade: linear-gradient(180deg, rgba(244,238,230,.35), rgba(244,238,230,.7));
  --mev-lyric: rgba(43,38,34,.38);
  --mev-lyric-cur: #2b2622;
  --mev-track: rgba(43,38,34,.18);
  --mev-track-fill: rgba(43,38,34,.85);
  --mev-glass: rgba(43,38,34,.08);
  --mev-surface: rgba(43,38,34,.07);
  --mev-cur-item: #b5653f;
  --mev-menu-bg: rgba(250,246,240,.96);
  --mev-arm: linear-gradient(#5a524c, #8a827b);
}
```

> 浅色时 `--mev-menu-bg` 换成了浅色，菜单上的字跟着 `--mev-fg` 是深色，所以能看清。改成浅底时这两个要一起改。

**樱花粉**（只换点颜色，其余照旧）：

```css
.mev {
  --mev-accent: #f08aa3;
  --mev-cur-item: #ffc2d1;
  --mev-lyric-cur: #ffe3ea;
  --mev-track-fill: #ffc2d1;
  --mev-disc-inner: #2a1c21;
  --mev-disc-outer: #120b0e;
  --mev-arm: linear-gradient(#ffd6e0, #f39bb2);
}
```

**方块唱片**（封面不裁成圆形，转得慢一点）：

```css
.mev {
  --mev-label-size: 78%;
  --mev-label-radius: 10%;
  --mev-spin: 30s;
}
```

## 五、想改得更细

变量管不到的地方，也可以直接写普通的 CSS 规则。播放器里的东西都以 `.mev-` 开头，比如 `.mev-disc`（唱片）、`.mev-cover`（中间的封面）、`.mev-arm`（唱臂）、`.mev-lyric`（歌词）、`.mev-bar`（进度条那一行）、`.mev-ctl`（播放按钮那一行）、`.mev-mini`（缩小后的小唱片）、`.mev-item`（列表里的一首）。前面要加 `.mev`，才能盖过原来的样式：

```css
.mev .mev-lyric .ln.cur { letter-spacing: .04em; }
.mev .mev-cover { box-shadow: 0 0 0 3px rgba(255,255,255,.5); }
```

这些类名以后可能会改，变量会尽量保持不变。能用变量就用变量。

## 六、给帮你改的 AI

- 只输出一段 CSS，全部写在 `.mev { … }` 里；用上面列出的变量名，不要自己编变量名。
- 没提到的就不写，保留默认。
- 不要用 `@import`、`url(http…)` 这类从外网加载东西的写法，页面可能是在没网或者只连局域网的手机上开的。
- 改成浅色背景时，记得把文字、图标、歌词、进度条、菜单底色一起改深或改浅，不然会看不清。
