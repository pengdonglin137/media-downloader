#!/usr/bin/env bash

set -Eeuo pipefail

print_usage() {
    printf '用法: %s <媒体链接> [输出目录]\n' "${0##*/}" >&2
    printf '示例: %s "https://x.com/user/status/123/video/1"\n' "${0##*/}" >&2
    printf '示例: %s "https://www.bilibili.com/video/BVxxxxxxxxxx" ~/Videos\n' "${0##*/}" >&2
    printf '\n可选环境变量:\n' >&2
    printf '  COOKIES_FROM_BROWSER=chrome   从浏览器读取登录状态\n' >&2
    printf '  YTDLP_PROXY=http://127.0.0.1:7890  同时设置 yt-dlp 和网页请求代理\n' >&2
    printf '  X_CDN_IPS=IP1,IP2              覆盖 X CDN 探测顺序\n' >&2
    exit 2
}

fail() {
    printf '错误: %s\n' "$1" >&2
    exit 1
}

is_x_url() {
    [[ $1 =~ ^https?://([^/]+\.)?(x|twitter)\.com/ ]]
}

find_ytdlp() {
    local candidate

    for candidate in \
        "$script_directory/bin/yt-dlp" \
        "$script_directory/.venv/bin/yt-dlp"; do
        if [[ -x $candidate ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    command -v yt-dlp 2>/dev/null || return 1
}

require_ytdlp() {
    if ! ytdlp_command=$(find_ytdlp); then
        printf '错误: 未找到 yt-dlp，无法下载这个网站的媒体。\n' >&2
        printf '\n请选择一种安装方式:\n' >&2
        printf '  Ubuntu/Debian: sudo apt update && sudo apt install -y yt-dlp\n' >&2
        printf '  用户级安装:    python3 -m pip install --user -U yt-dlp\n' >&2
        printf '  项目内安装:    python3 -m venv .venv && .venv/bin/pip install -U yt-dlp\n' >&2
        printf '\n安装后重新运行:\n  %s <媒体链接>\n' "${0##*/}" >&2
        exit 1
    fi
}

cdn_candidate_ips() {
    local host=$1
    local resolver_url response ip
    local -a candidates=()

    if [[ -n ${X_CDN_IPS:-} ]]; then
        IFS=',' read -r -a candidates <<< "$X_CDN_IPS"
        for ip in "${candidates[@]}"; do
            ip=${ip//[[:space:]]/}
            [[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || continue
            printf '%s\n' "$ip"
        done | awk '!seen[$0]++'
        return 0
    fi

    if [[ -z ${ytdlp_proxy:-} ]]; then
        if command -v getent >/dev/null 2>&1; then
            while read -r ip; do
                [[ -n $ip ]] && candidates+=("$ip")
            done < <(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | awk '!seen[$0]++')
        fi

        for resolver_url in \
            "https://cloudflare-dns.com/dns-query?name=$host&type=A" \
            "https://dns.google/resolve?name=$host&type=A"; do
            response=$(curl --fail --silent --show-error --max-time 8 \
                -H 'accept: application/dns-json' "$resolver_url" 2>/dev/null || true)
            while read -r ip; do
                [[ -n $ip ]] && candidates+=("$ip")
            done < <(printf '%s' "$response" | grep -oE '"data":"[0-9.]+"' | sed -E 's/.*"data":"([0-9.]+)"/\1/' | awk '!seen[$0]++')
        done
    fi

    if [[ $host == 'video.twimg.com' || $host == 'pbs.twimg.com' ]]; then
        candidates+=(
            151.101.76.158
            151.101.0.158
            151.101.64.158
            151.101.128.158
            199.232.148.158
            199.232.160.158
            140.248.164.158
        )
    fi

    printf '%s\n' "${candidates[@]}" | sed '/^$/d' | awk '!seen[$0]++'
}

download_cdn_file() {
    local source_url=$1
    local destination=$2
    local host partial ip resume_status
    local -a candidates=()
    local -a curl_args=()

    if [[ -s $destination ]]; then
        printf '[download] %s 已存在，跳过\n' "$destination"
        return 0
    fi

    host=$(printf '%s\n' "$source_url" | sed -nE 's#https?://([^/]+)/.*#\1#p')
    partial="$destination.part"

    if [[ -z ${ytdlp_proxy:-} && ($host == 'video.twimg.com' || $host == 'pbs.twimg.com') ]]; then
        mapfile -t candidates < <(cdn_candidate_ips "$host")
    fi
    candidates+=("")

    for ip in "${candidates[@]}"; do
        curl_args=(
            --fail
            --silent
            --show-error
            --location
            --retry 5
            --retry-delay 1
            --connect-timeout 10
            --max-time 1800
            --user-agent 'Mozilla/5.0'
        )
        [[ -n ${ytdlp_proxy:-} ]] && curl_args+=(--proxy "$ytdlp_proxy")
        [[ -n $ip ]] && curl_args+=(--resolve "$host:443:$ip")

        if [[ -n $ip ]]; then
            printf '使用 CDN 节点 %s\n' "$ip"
        fi

        if [[ -s $partial ]]; then
            if curl "${curl_args[@]}" -C - -o "$partial" "$source_url"; then
                mv -f "$partial" "$destination"
                return 0
            else
                resume_status=$?
            fi

            # Some CDN nodes ignore Range or return 416 for a stale partial file.
            # Retry that node from scratch, while preserving partial data for
            # transient connection failures so the next node can resume it.
            if ((resume_status == 22 || resume_status == 33)); then
                rm -f "$partial"
            else
                continue
            fi
        fi

        if curl "${curl_args[@]}" -o "$partial" "$source_url"; then
            mv -f "$partial" "$destination"
            return 0
        fi
    done

    return 1
}

if [[ $# -lt 1 || $# -gt 2 || "$1" == '-h' || "$1" == '--help' ]]; then
    print_usage
fi

media_url=$1
output_directory=${2:-.}
cookies_from_browser=${COOKIES_FROM_BROWSER:-}
ytdlp_proxy=${YTDLP_PROXY:-}
script_directory=$(CDPATH=; cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

command -v curl >/dev/null 2>&1 || fail '找不到 curl'

mkdir -p "$output_directory" || fail "无法创建输出目录: $output_directory"

if [[ $media_url =~ ^https?://.*\.(jpe?g|png|gif|webp|avif)(\?.*)?$ || $media_url =~ ^https?://pbs\.twimg\.com/media/ ]]; then
    image_ext=$(printf '%s\n' "$media_url" | sed -nE 's#.*\.([[:alnum:]]+)(\?.*)?$#\1#p')
    format_ext=$(printf '%s\n' "$media_url" | sed -nE 's#.*[?&]format=([a-zA-Z0-9]+).*#\1#p')
    if [[ -z $image_ext && -n $format_ext ]]; then
        image_ext=$format_ext
    fi
    [[ -n $image_ext ]] || image_ext='jpg'
    image_ext=${image_ext,,}
    image_path="$output_directory/downloaded_image.$image_ext"
    download_cdn_file "$media_url" "$image_path" \
        || fail '图片直链下载失败'
    printf '下载完成，文件位于: %s\n' "$image_path"
    exit 0
fi

temporary_directory=$(mktemp -d)
trap 'rm -rf "$temporary_directory"' EXIT

run_ytdlp() {
    require_ytdlp

    local -a yt_dlp_options=(
        --no-playlist
        --continue
        --no-overwrites
        --retries 5
        --fragment-retries 5
        --merge-output-format mp4
        --output "$output_directory/%(title)s [%(id)s].%(ext)s"
    )

    if [[ -n $cookies_from_browser ]]; then
        yt_dlp_options+=(--cookies-from-browser "$cookies_from_browser")
    fi

    if [[ -n $ytdlp_proxy ]]; then
        yt_dlp_options+=(--proxy "$ytdlp_proxy")
    fi

    "$ytdlp_command" "${yt_dlp_options[@]}" "$media_url"
}

if ! is_x_url "$media_url"; then
    if run_ytdlp; then
        exit 0
    fi
    fail 'yt-dlp 无法处理这个链接。请检查链接、登录状态、地区限制或网站是否使用 DRM'
fi

page_file="$temporary_directory/page.html"
curl_options=(
    --fail
    --silent
    --show-error
    --location
    --retry 3
    --retry-delay 1
    --connect-timeout 15
    --max-time 60
    --compressed
    --user-agent 'Mozilla/5.0'
)

curl_options+=(--header 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
curl_options+=(--header 'Accept-Language: en-US,en;q=0.8')
curl_options+=(--referer 'https://x.com/')

if [[ -n $ytdlp_proxy ]]; then
    curl_options+=(--proxy "$ytdlp_proxy")
fi

if ! curl "${curl_options[@]}" "$media_url" > "$page_file"; then
    printf 'X 网页直连失败，尝试使用 yt-dlp 兜底...\n' >&2
    if run_ytdlp; then
        exit 0
    fi
    fail '无法访问 X 页面，请检查网络、代理、Cookie 或链接是否有效'
fi

normalized_page=$(sed -E 's#\\/#/#g; s#\\u0026#\&#g; s#&amp;#\&#g' "$page_file")

mapfile -t video_urls < <(
    printf '%s' "$normalized_page" |
        grep -oE 'https://video\.twimg\.com/[^"<> ]+' |
        sed 's/\\$//' |
        grep -E '\.mp4([?&]|$)' |
        awk '!seen[$0]++'
)

tweet_id=$(printf '%s\n' "$media_url" | sed -nE 's#.*/status/([0-9]+).*#\1#p')
[[ -n $tweet_id ]] || tweet_id='media'

if ((${#video_urls[@]} > 0)); then
    media_index=1
    if [[ $media_url =~ /video/([0-9]+) ]]; then
        media_index=${BASH_REMATCH[1]}
    fi

    mapfile -t media_ids < <(
        printf '%s\n' "${video_urls[@]}" |
            sed -nE 's#https://video\.twimg\.com/[^/]+/([0-9]+)/.*#\1#p' |
            awk '!seen[$0]++'
    )

    if ((media_index < 1 || media_index > ${#media_ids[@]})); then
        fail "视频序号 /video/$media_index 不存在；页面只找到 ${#media_ids[@]} 个视频"
    fi

    selected_media_id=${media_ids[media_index - 1]}
    best_url=''
    best_area=0

    while IFS= read -r candidate_url; do
        [[ -n $candidate_url ]] || continue

        resolution=$(printf '%s\n' "$candidate_url" | sed -nE 's#.*/([0-9]+)x([0-9]+)/[^/]+$#\1x\2#p')
        if [[ $resolution =~ ^([0-9]+)x([0-9]+)$ ]]; then
            width=${BASH_REMATCH[1]}
            height=${BASH_REMATCH[2]}
            area=$((width * height))
            if ((area > best_area)); then
                best_area=$area
                best_url=$candidate_url
            fi
        elif [[ -z $best_url ]]; then
            best_url=$candidate_url
        fi
    done < <(
        printf '%s\n' "${video_urls[@]}" |
            awk -v media_id="$selected_media_id" 'index($0, "/" media_id "/") {print}'
    )

    [[ -n $best_url ]] || fail '找不到选定视频的可下载格式'

    video_path="$output_directory/X_${tweet_id}.mp4"
    printf '从 X 页面提取视频 CDN 直链: %s\n' "$best_url"
    if ! download_cdn_file "$best_url" "$video_path"; then
        printf 'X 视频 CDN 下载失败，尝试使用 yt-dlp 兜底...\n' >&2
        if run_ytdlp; then
            exit 0
        fi
        fail 'X 视频 CDN 直链下载失败；可以设置 YTDLP_PROXY、X_CDN_IPS 或 COOKIES_FROM_BROWSER 后重试'
    fi
    printf '下载完成，文件位于: %s\n' "$video_path"
    exit 0
fi

if command -v python3 >/dev/null 2>&1 && [[ -f "$script_directory/download_x_article.py" ]]; then
    if python3 "$script_directory/download_x_article.py" "$page_file" "$output_directory" "$media_url"; then
        exit 0
    fi
fi

mapfile -t image_urls < <(
    printf '%s' "$normalized_page" |
        grep -oE 'https://pbs\.twimg\.com/media/[^" ]+' |
        sed -E 's#([?&])name=[^&"]+#\1name=orig#' |
        awk '!seen[$0]++'
)

if ((${#image_urls[@]} == 0)); then
    if run_ytdlp; then
        exit 0
    fi
    fail '页面中没有找到公开视频或原图直链，可能需要登录 Cookie，或该帖子已删除/受保护'
fi

first_image_path=''
for image_number in "${!image_urls[@]}"; do
    image_index=$((image_number + 1))
    image_url=${image_urls[image_number]}
    image_ext=$(printf '%s\n' "$image_url" | sed -nE 's#.*\.([[:alnum:]]+)(\?.*)?$#\1#p')
    [[ -n $image_ext ]] || image_ext='jpg'
    image_ext=${image_ext,,}
    image_path="$output_directory/X_${tweet_id}_$(printf '%02d' "$image_index").$image_ext"
    if [[ -z $first_image_path ]]; then
        first_image_path=$image_path
    fi

    printf '正在下载 X 图片 %d/%d: %s\n' "$image_index" "${#image_urls[@]}" "$image_url"
    download_cdn_file "$image_url" "$image_path" \
        || fail "图片下载失败: $image_url"
done

printf '下载完成，首个文件位于: %s\n' "$first_image_path"
