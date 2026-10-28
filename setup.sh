#!/usr/bin/env bash

set -Eeuo pipefail

script_directory=$(CDPATH=; cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
venv_directory="$script_directory/.venv"

if ! command -v python3 >/dev/null 2>&1; then
    printf '错误: 找不到 python3。\n' >&2
    printf 'Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-venv\n' >&2
    exit 1
fi

if [[ ! -x "$venv_directory/bin/python" ]]; then
    printf '正在创建项目虚拟环境: %s\n' "$venv_directory"
    if ! python3 -m venv "$venv_directory"; then
        printf '创建虚拟环境失败，请安装 python3-venv 后重试。\n' >&2
        printf 'Ubuntu/Debian: sudo apt update && sudo apt install -y python3-venv\n' >&2
        exit 1
    fi
fi

printf '正在安装或更新项目内 yt-dlp...\n'
"$venv_directory/bin/python" -m pip install --upgrade yt-dlp
printf '\n安装完成。现在可以运行:\n  %s/download_media.sh "媒体链接"\n' "$script_directory"
