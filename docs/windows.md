# 在 Windows 上装 music-ears

写给没用过命令行的人。一共 7 步，第一次装要下载 2～3GB 的东西，网慢的话留出一两个小时。

## 1. 装 Docker Desktop

1. 去 <https://www.docker.com/products/docker-desktop/> 下载 Windows 版，双击安装，一路默认。中途让你装 **WSL** 就同意，装完按提示**重启电脑**。
2. 重启后打开 Docker Desktop，等左下角变成绿色的 **Engine running**。
3. **在国内**：Docker 下载镜像可能很慢甚至失败。Docker Desktop → 右上角齿轮（Settings）→ **Docker Engine**，在那段文字的大括号里加一行镜像源（注意前后的逗号），点 Apply & restart：
   ```json
   "registry-mirrors": ["https://docker.1ms.run"]
   ```

## 2. 装 Python

1. 去 <https://www.python.org/downloads/> 下载最新的 Python 3（3.12 或更新都行）。
2. 安装第一页**一定要勾上最下面的 "Add python.exe to PATH"**，再点 Install Now。

## 3. 下载 music-ears

1. 打开 <https://github.com/White-ting/music-ears>，点绿色的 **Code** → **Download ZIP**。
2. 解压到一个**英文、没有空格**的地方，比如 `D:\music-ears`（别放在桌面或者带中文名的文件夹里）。

## 4. 在这个文件夹里打开 PowerShell

打开 `D:\music-ears` 这个文件夹，在**地址栏**里点一下，把路径删掉，输入 `powershell`，回车。会打开一个蓝色窗口，路径就在这个文件夹。

> 下面所有命令都在这个窗口里粘贴（右键就是粘贴）、回车。

## 5. 装测量要的库（最久的一步）

```
docker run --rm -v "${PWD}:/w" python:3.12-slim sh -c "python -m venv --copies /w/venv && /w/venv/bin/pip install -r /w/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --extra-index-url https://download.pytorch.org/whl/cpu"
```

- 第一次会先下载一个约 50MB 的镜像，再装 PyTorch 等库，大约 1～2GB，耐心等它跑完，回到能输入的状态才算完。
- `-i` 那段是清华的镜像，国内快；`--extra-index-url` 那段是只装 CPU 版的 PyTorch（不加的话会装几个 GB 的显卡版，用不上）。
- 中途断了就再跑一次这条，已经装好的不会重装。

## 6. 检查一遍

```
python music-ears doctor
```

一行一行看：
- `✓` 是好的；
- `！` 是可选的（比如没配钥匙、没登录网易云），不影响用；
- `✗` 是必须处理的，下面会写怎么补。

最后一行显示"没问题，可以用了"就装好了。

## 7. 用起来

**开播放器：**
```
python music-ears serve
```
浏览器打开 <http://127.0.0.1:8790/>。歌单页点「扫码登录」，用网易云 App 扫一下，就能看到自己的歌单。这个窗口要一直开着，关了播放器就停了。

**拆一首歌**（另开一个 PowerShell 窗口，照第 4 步打开）：
```
python music-ears 网易云歌曲链接
```
第一次会下载约 500MB 的模型，之后一首 4 分钟的歌大约 6～8 分钟。做好会打印乐谱卡的位置。

**接给 AI**（Aru、Claude 手机 App）：见 [mcp.md](mcp.md)。手机要连的话，要给 serve 设口令、再开一个 HTTPS 地址；已经给 Aru Host 建过 Cloudflare 隧道的，在同一条隧道里多加一个公共主机名就行（比如 `ears.你的域名` → `http://localhost:8790`）。

## 模型钥匙（可选）

不配钥匙也能出卡，只是没有"模型听"的那部分（听感、审查）。要配的话：

1. 在文件资源管理器地址栏输入 `%USERPROFILE%\.config\music-ears`，回车（提示不存在就先建这几层文件夹：`.config`、`music-ears`、`keys`）。
2. 在 `keys` 文件夹里新建两个文本文件，**只写一行钥匙**：
   - `dashscope.key`：阿里云百炼的钥匙（听、认字）；
   - `gemini.key`：Gemini 的钥匙（审查）。
   - 新建文本文件时注意别变成 `dashscope.key.txt`：在资源管理器"查看"里勾上"文件扩展名"再改名。
3. 再跑一次 `python music-ears doctor`，钥匙那几行变成 ✓ 就好了。

## 常见问题

| 看到什么 | 怎么办 |
|---|---|
| `python` 不是内部或外部命令 | 第 2 步没勾 "Add python.exe to PATH"：重装 Python，勾上 |
| `docker` 不是内部或外部命令 / Docker 连不上 | 先打开 Docker Desktop，等它变绿 |
| 第 5 步下载很慢、卡住 | 检查第 1 步的镜像源；或者开着代理再试 |
| doctor 说 venv 里缺库 | 再跑一次第 5 步 |
| 乐谱卡里的中文是乱码 | 用 `python music-ears …` 跑，别双击文件；还不行的话在 GitHub 提 Issue，附上 doctor 的输出 |
