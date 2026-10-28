# Media Downloader

一个基于 `yt-dlp`、`curl` 和 `ffmpeg` 的通用媒体下载工具。

## 支持内容

- `yt-dlp` 支持的网站，例如 Bilibili、YouTube 等
- X/Twitter 视频
- X/Twitter 图片帖子和图片直链
- X Article：正文、标题、作者、发布时间和封面
- 断点续传和已存在文件保护

## 安装依赖

确保以下命令可以直接执行：

```bash
yt-dlp --version
curl --version
ffmpeg -version
python3 --version
```

如果没有安装 `yt-dlp`，运行下载器时会显示安装提示。也可以提前选择一种方式安装：

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

## 限制

私密内容、会员内容、DRM、地区限制、已删除内容和网络不可达的 CDN 不能保证下载成功。下载内容时请遵守目标网站的服务条款和版权规定。
