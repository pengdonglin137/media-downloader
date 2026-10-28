#!/usr/bin/env python3

import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path


def decode_value_strings(page):
    marker = 'value:"'
    position = 0
    while True:
        start = page.find(marker, position)
        if start < 0:
            return
        start += len(marker)
        end = start
        escaped = False
        while end < len(page):
            char = page[end]
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                break
            end += 1
        if end >= len(page):
            return
        raw_value = page[start:end]
        try:
            yield json.loads('"' + raw_value + '"')
        except json.JSONDecodeError:
            pass
        position = end + 1


def find_article(value):
    if isinstance(value, dict):
        if isinstance(value.get('content_state'), dict) and isinstance(
            value['content_state'].get('blocks'), list
        ):
            return value
        for child in value.values():
            result = find_article(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = find_article(child)
            if result:
                return result
    return None


def find_first(value, key):
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            result = find_first(child, key)
            if result is not None:
                return result
    elif isinstance(value, list):
        for child in value:
            result = find_first(child, key)
            if result is not None:
                return result
    return None


def metadata_value(page, name):
    pattern = rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)'
    match = re.search(pattern, page, re.IGNORECASE)
    return html.unescape(match.group(1)) if match else ''


def format_block(block):
    text = block.get('text', '').strip()
    block_type = block.get('type', '')
    if not text:
        return ''
    if block_type == 'header-one':
        return f'# {text}'
    if block_type == 'header-two':
        return f'## {text}'
    if block_type == 'header-three':
        return f'### {text}'
    if block_type in {'unordered-list-item', 'unordered-list'}:
        return f'- {text}'
    if block_type in {'ordered-list-item', 'ordered-list'}:
        return f'1. {text}'
    if block_type == 'blockquote':
        return f'> {text}'
    if block_type == 'code-block':
        return f'```\n{text}\n```'
    return text


def download_cover(article, output_directory, tweet_id):
    cover = article.get('cover_media_results') or {}
    media_info = ((cover.get('result') or {}).get('media_info') or {})
    image_url = media_info.get('original_img_url')
    if not image_url:
        return '', ''

    image_url = str(image_url)
    extension = Path(image_url.split('?', 1)[0]).suffix.lower() or '.jpg'
    if not re.fullmatch(r'\.[a-z0-9]{1,5}', extension):
        extension = '.jpg'
    image_path = output_directory / f'X_{tweet_id}_article_cover{extension}'
    if not image_path.exists():
        request = urllib.request.Request(
            image_url,
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://x.com/'},
        )
        proxy = os.environ.get('YTDLP_PROXY')
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
            if proxy
            else urllib.request.ProxyHandler()
        )
        try:
            with opener.open(request, timeout=60) as response:
                image_path.write_bytes(response.read())
        except Exception as error:
            print(f'提示: 文章封面下载失败，将保留远程地址: {error}', file=sys.stderr)
            return image_url, ''
    return image_url, image_path.name


def main():
    if len(sys.argv) != 4:
        return 2

    page_path = Path(sys.argv[1])
    output_directory = Path(sys.argv[2])
    source_url = sys.argv[3]
    page = page_path.read_text(encoding='utf-8', errors='replace')

    article = None
    for encoded_value in decode_value_strings(page):
        try:
            payload = json.loads(encoded_value)
        except json.JSONDecodeError:
            continue
        article = find_article(payload)
        if article:
            break
    if not article:
        return 3

    tweet_id_match = re.search(r'/status/(\d+)', source_url)
    tweet_id = tweet_id_match.group(1) if tweet_id_match else 'article'
    title = article.get('title') or metadata_value(page, 'og:title') or f'X Article {tweet_id}'
    creator = metadata_value(page, 'twitter:creator') or metadata_value(page, 'article:author')
    published = article.get('metadata', {}).get('first_published_at_secs')
    if published:
        published_text = dt.datetime.fromtimestamp(published, dt.timezone.utc).isoformat()
    else:
        published_text = metadata_value(page, 'article:published_time')

    image_url, image_name = download_cover(article, output_directory, tweet_id)
    lines = [
        f'# {title}',
        '',
        f'- 作者：{creator or "未知"}',
        f'- 发布时间：{published_text or "未知"}',
        f'- 原文：{source_url}',
        '',
    ]
    if image_name:
        lines.extend([f'![文章封面]({image_name})', ''])
    elif image_url:
        lines.extend([f'![文章封面]({image_url})', ''])

    for block in article.get('content_state', {}).get('blocks', []):
        rendered = format_block(block)
        if rendered:
            lines.extend([rendered, ''])

    article_path = output_directory / f'X_{tweet_id}.md'
    article_path.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    print(f'文章下载完成，文件位于: {article_path}')
    if image_name:
        print(f'文章封面位于: {output_directory / image_name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
