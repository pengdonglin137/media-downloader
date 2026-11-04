# Media Downloader

一个基于 `yt-dlp`、`curl` 和 `ffmpeg` 的通用媒体下载工具。

## 支持内容

- `yt-dlp` 支持的网站，例如 Bilibili、YouTube 等
- X/Twitter 视频
- X/Twitter 图片帖子和图片直链
- X Article：正文、标题、作者、发布时间和封面
- X 公共视频优先从网页提取 CDN 直链，不依赖 X API
- 断点续传、CDN 节点切换和已存在文件保护
- 自动截断过长的视频标题，避免 Linux 文件名超过长度限制

## 安装依赖

确保以下命令可以直接执行：

```bash
yt-dlp --version
curl --version
ffmpeg -version
python3 --version
```

X 的公开视频和图片可以优先使用网页直链下载；Bilibili、YouTube 等其他网站，以及 X 页面无法直接提取时的兜底方案，需要 `yt-dlp`。如果没有安装，运行下载器时会显示安装提示。也可以使用项目脚本安装到本地虚拟环境：

```bash
./setup.sh
```

手动安装方式：

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y yt-dlp

# 用户级 Python 安装
python3 -m pip install --user -U yt-dlp

# 安装到本项目，不污染系统 Python
python3 -m venv .venv
.venv/bin/pip install -U yt-dlp
```

下载器会优先使用项目内的 `bin/yt-dlp`，其次使用 `.venv/bin/yt-dlp`，最后使用系统 PATH 中的 `yt-dlp`。

## 使用

```bash
./download_media.sh '媒体链接'
```

指定输出目录：

```bash
./download_media.sh '媒体链接' ~/Downloads
```

例如：

```bash
./download_media.sh 'https://b23.tv/RvOkuw9'
./download_media.sh 'https://x.com/user/status/123/video/1'
./download_media.sh 'https://x.com/user/status/123?s=46'
```

旧名称仍然可用：

```bash
./download_x_video.sh 'X/Twitter 链接'
```

## 登录和代理

需要浏览器登录状态时：

```bash
COOKIES_FROM_BROWSER=chrome ./download_media.sh '媒体链接'
```

使用 HTTP 或 SOCKS 代理时：

```bash
YTDLP_PROXY=http://127.0.0.1:7890 ./download_media.sh '媒体链接'
```

如果 X 的视频 CDN 节点被当前网络重置，脚本会自动探测多个 IPv4 节点，并使用 `.part` 文件断点续传。也可以用 `X_CDN_IPS` 覆盖自动探测顺序，多个节点用逗号分隔：

```bash
X_CDN_IPS=151.101.76.158 ./download_media.sh 'X 视频链接'
```

## 限制

私密内容、会员内容、DRM、地区限制、已删除内容和网络不可达的 CDN 不能保证下载成功。下载内容时请遵守目标网站的服务条款和版权规定。

## 英文视频转中文配音

`translate_to_zh.py` 把英文视频转成中文配音视频：faster-whisper 识别英文（`asr.py`），`codex exec` 或 `claude -p` 翻译，Edge-TTS 逐句配音（`dub.py`），ffmpeg 合成。依赖本机的 pyVideoTrans（默认 `/vol_1t/pyvideotrans`，可用 `PVT_DIR` 覆盖）和已配置好的 codex 或 claude 命令行。

```bash
./translate_to_zh.py 'https://youtu.be/xxxx'                 # 默认输出到 /vol_1t/Videos
./translate_to_zh.py ~/Videos/talk.mp4 --subs hard           # 本地文件，双语字幕烧进画面
./translate_to_zh.py 'URL' --engine claude                 # 改用 claude 翻译，模型由 CLAUDE_MODEL 指定，默认 opus
./translate_to_zh.py 'URL' --sub-pos top --model large-v3 --title 'Linux 内核调度器'
```

- 视频在网站上有人工英文字幕时直接使用，否则用 faster-whisper 识别（`--no-source-subs` 强制识别）。
- 识别前由模型从标题和简介中提取专有名词作为热词（`--hotwords` 可手动指定），识别结果按词级时间戳切成完整句子。
- 翻译前先通读全文生成术语表（工作目录里的 `glossary.txt`），统一术语译法并纠正识别错误；`--no-glossary` 关闭。
- 热词有时会让识别漏掉正文。视频超过 10 分钟时，先用一段 5 分钟的音频分别试识别（加热词和不加热词），加热词明显更差就不用热词；整段识别后仍发现异常（只剩热词、语速过低），会去掉热词重新识别。
- 配音逐句合成并统一响度，放不进时间段时用 Edge-TTS 自带语速参数重新合成；需要提速超过 15% 的句子交给模型缩短后重配（`--shorten`）。统计结果在 `tts_stats.json`。
- 播放器里显示的字幕按每屏不超过 26 个汉字拆分，每屏至少显示约 1.5 秒。
- 默认按音高判断每句话是男声还是女声，分别用 `--male-voice`（默认云希）和 `--female-voice`（默认晓晓）配音；`--voice zh-CN-YunyangNeural` 可统一用一个音色。
- 成品有两条音轨（中文配音、English 原声）和三条字幕轨（中文、English、中英双语），在播放器里切换。
- 翻译时按每句时长限制字数，避免配音被迫加速。
- 画面默认用 x264 `crf 23` 重新压缩，讲座类视频体积约为原来的 1/3，`--crf 0` 直接复制原画面。画面短边超过 1080 像素（2K、4K）时缩小到 1080p，`--max-height 0` 保持原分辨率。中文配音 64k。英文原声和中文配音响度相差 1 dB 以上时调到相同响度再编码，否则直接复制；`--keep-en-volume` 保持原音量。
- 可以一次传多个链接或文件依次处理，单个失败不影响后面的；`--clean` 在成功后删除工作目录。
- 用 `--subs hard` 把双语字幕烧进画面；原视频底部已有字幕时可加 `--sub-pos top`。

每一步的结果保存在输出目录下的 `.zh_work_*` 里，中途失败后重新运行会从断点继续。
