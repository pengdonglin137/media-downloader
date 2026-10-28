# Media Downloader

一个基于 `yt-dlp`、`curl` 和 `ffmpeg` 的通用媒体下载工具。

## 支持内容

- `yt-dlp` 支持的网站，例如 Bilibili、YouTube 等
- X/Twitter 视频
- X/Twitter 图片帖子和图片直链
- X Article：正文、标题、作者、发布时间和封面
- X 公共视频优先从网页提取 CDN 直链，不依赖 X API
- 断点续传、CDN 节点切换和已存在文件保护

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

下载器会优先使用项目内的 `.venv/bin/yt-dlp`，其次使用 `bin/yt-dlp`，最后使用系统 PATH 中的 `yt-dlp`。

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
