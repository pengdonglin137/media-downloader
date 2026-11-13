#!/usr/bin/env python3
"""下载 YouTube 视频并准备可追溯的英文翻译任务，不调用翻译模型。"""

import argparse
import fcntl
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = Path(os.environ.get('ZH_VIDEO_DIR', '/vol_1t/Videos'))


def atomic_write(path, text):
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(text)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def video_id(value):
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
        return value
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower()
    if host in {'youtu.be', 'www.youtu.be'}:
        candidate = parsed.path.strip('/').split('/')[0]
    elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}:
        parts = parsed.path.strip('/').split('/')
        candidate = parse_qs(parsed.query).get('v', [''])[0] if parts[0] == 'watch' else (
            parts[1] if len(parts) == 2 and parts[0] in {'live', 'shorts', 'embed'} else '')
    else:
        candidate = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', candidate):
        raise ValueError(f'不是有效的 YouTube 视频链接或 ID: {value}')
    return candidate


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    return f'{milliseconds // 3600000:02d}:{milliseconds // 60000 % 60:02d}:{milliseconds // 1000 % 60:02d},{milliseconds % 1000:03d}'


def json3_entries(data):
    events = []
    for source_index, event in enumerate(data.get('events', [])):
        text = re.sub(r'\s+', ' ', html.unescape(''.join(segment.get('utf8', '') for segment in event.get('segs', [])))).strip()
        if text:
            events.append({'source_index': source_index, 'start': event['tStartMs'] / 1000,
                           'end': (event['tStartMs'] + event.get('dDurationMs', 0)) / 1000, 'text': text})
    if not events:
        raise ValueError('字幕没有正文')
    adjustments = []
    grouped = []
    for event in events:
        if grouped and event['start'] < grouped[-1]['start']:
            raise ValueError(f'字幕事件起点倒退，需核对源文件: {event["source_index"]}')
        if grouped and event['start'] == grouped[-1]['start']:
            grouped[-1]['text'] += ' ' + event['text']
            grouped[-1]['end'] = max(grouped[-1]['end'], event['end'])
            adjustments.append({'source_index': event['source_index'], 'reason': 'same_start_events_grouped'})
        else:
            grouped.append(dict(event))
    for index, event in enumerate(grouped):
        if index + 1 < len(grouped) and event['end'] > grouped[index + 1]['start']:
            adjustments.append({'source_index': event['source_index'], 'reason': 'display_end_at_next_event', 'original_end': event['end'], 'end': grouped[index + 1]['start']})
            event['end'] = grouped[index + 1]['start']
        if event['start'] < 0 or event['end'] <= event['start']:
            raise ValueError(f'字幕时间无效，需核对源文件: {event["source_index"]}')
    entries = []
    current = None
    for event in grouped:
        if current and (event['start'] - current['end'] > 0.8 or event['end'] - current['start'] > 15):
            entries.append(current)
            current = None
        if current is None:
            current = {'start': event['start'], 'end': event['end'], 'text': event['text']}
        else:
            current['text'] += ' ' + event['text']
            current['end'] = event['end']
        if re.search(r'[.?!]["\x27)\]]*$', current['text']) and current['end'] - current['start'] >= 1.5:
            entries.append(current)
            current = None
    if current:
        entries.append(current)
    if ' '.join(event['text'] for event in events) != ' '.join(entry['text'] for entry in entries):
        raise ValueError('字幕分段损失或重排了原文')
    return entries, events, adjustments


def execute(arguments, log_path, timeout=1200):
    with log_path.open('a', encoding='utf-8') as output:
        output.write('\n' + time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
        output.flush()
        result = subprocess.run(arguments, stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'命令退出码 {result.returncode}，日志: {log_path}')


def load_info(path, identity):
    try:
        info = json.loads(path.read_text(encoding='utf-8'))
        return info if isinstance(info, dict) and info.get('id') == identity and info.get('formats') else None
    except (OSError, ValueError):
        return None


def fetch_info(identity, work, downloader, refresh=False):
    info_path = work / 'source_info.json'
    if not refresh:
        info = load_info(info_path, identity)
        if info:
            return info
    for attempt in range(3):
        with (work / 'metadata.log').open('a', encoding='utf-8') as errors:
            result = subprocess.run([downloader, '--skip-download', '--dump-single-json', '--no-playlist', '--no-warnings', f'https://www.youtube.com/watch?v={identity}'], capture_output=False, stdout=subprocess.PIPE, stderr=errors, text=True, timeout=180)
        try:
            info = json.loads(result.stdout)
        except ValueError:
            info = None
        if result.returncode == 0 and isinstance(info, dict) and info.get('id') == identity and info.get('formats'):
            save_json(info_path, info)
            return info
        if attempt < 2:
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f'无法取得有效元数据，未缓存空结果: {work / "metadata.log"}')


def prepare(identity, output, downloader):
    work = output / f'.zh_work_yt_{identity}'
    work.mkdir(parents=True, exist_ok=True)
    state_path = work / 'preparation.json'
    if (work / 'en.srt').exists():
        print(identity, '已有英文字幕，保留原分段；不覆盖翻译或配音', flush=True)
        return
    save_json(state_path, {'id': identity, 'stage': 'preparing'})
    info = fetch_info(identity, work, downloader)
    media = [path for path in (work / 'download').glob('*.mp4') if not re.search(r'\.f\d+\.mp4$|\.part\.mp4$', path.name)]
    if not media:
        arguments = ['--load-info-json', str(work / 'source_info.json'), '--no-playlist', '--continue', '--no-overwrites', '--retries', '5', '--fragment-retries', '5', '--format', 'bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]', '--merge-output-format', 'mp4', '--output', str(work / 'download/%(title).100B [%(id)s].%(ext)s')]
        try:
            execute([downloader, *arguments], work / 'download_agent.log')
        except RuntimeError:
            info = fetch_info(identity, work, downloader, refresh=True)
            execute([downloader, *arguments], work / 'download_agent.log')
    manual = 'en' in info.get('subtitles', {})
    language = 'en' if manual else 'en-orig' if 'en-orig' in info.get('automatic_captions', {}) else 'en'
    directory = work / ('source_subs' if manual else 'source_subs_auto')
    source = directory / f'{identity}.{language}.json3'
    if not source.exists():
        execute([downloader, '--load-info-json', str(work / 'source_info.json'), '--skip-download', '--write-subs' if manual else '--write-auto-subs', '--sub-langs', language, '--sub-format', 'json3', '--output', str(directory / '%(id)s.%(ext)s')], work / 'subtitle_agent.log')
    if not source.exists():
        raise RuntimeError('没有可用英文字幕；请使用 asr.py 本地转写，不生成空翻译任务')
    entries, events, adjustments = json3_entries(json.loads(source.read_text(encoding='utf-8')))
    srt = '\n\n'.join(f'{index}\n{timestamp(entry["start"])} --> {timestamp(entry["end"])}\n{entry["text"]}' for index, entry in enumerate(entries, 1)) + '\n'
    source_text = ' '.join(event['text'] for event in events)
    meta = {key: info.get(key, '') for key in ['title', 'description', 'uploader']}
    meta['en_subs'] = manual
    save_json(work / 'meta.json', meta)
    atomic_write(work / 'translation_input.tsv', '\n'.join(f'{index}\t{timestamp(entry["start"])} --> {timestamp(entry["end"])}\t{entry["text"]}' for index, entry in enumerate(entries, 1)) + '\n')
    save_json(work / 'source_provenance.json', {'provider': 'YouTube uploaded English captions' if manual else 'YouTube automatic English captions', 'source_url': f'https://www.youtube.com/watch?v={identity}', 'source_file': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'text_preserved': True, 'normalized_text_sha256': hashlib.sha256(source_text.encode()).hexdigest(), 'caption_events': len(events), 'units': len(entries), 'timing_basis': 'caption event starts and display ends; no word-level timing inferred', 'timing_adjustments': adjustments, 'first': timestamp(entries[0]['start']), 'last': timestamp(entries[-1]['end'])})
    save_json(work / 'translation_task.json', {'id': identity, 'units': len(entries), 'source_sha256': hashlib.sha256(srt.encode()).hexdigest(), 'response_format': 'translation.tsv: unit_number TAB simplified_chinese', 'source': str(work / 'translation_input.tsv')})
    if manual:
        (work / 'en_from_site').touch()
    atomic_write(work / 'en.srt', srt)
    save_json(state_path, {'id': identity, 'title': info['title'], 'stage': 'awaiting_translation', 'units': len(entries)})
    print(identity, info['title'], f'{len(entries)} 个单元，原文完整保留；等待翻译', flush=True)


def locked_prepare(identity, output, downloader):
    work = output / f'.zh_work_yt_{identity}'
    work.mkdir(parents=True, exist_ok=True)
    with (work / '.prepare.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            prepare(identity, output, downloader)
        except (ValueError, OSError, RuntimeError, KeyError, subprocess.TimeoutExpired) as error:
            save_json(work / 'preparation.json', {'id': identity, 'stage': 'failed', 'error': str(error)})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='+', help='YouTube 链接或视频 ID；可一次传入多个')
    parser.add_argument('-o', '--out-dir', type=Path, default=DEFAULT_OUT)
    arguments = parser.parse_args()
    downloader = next((str(path) for path in [SCRIPT_DIR / 'bin/yt-dlp', SCRIPT_DIR / '.venv/bin/yt-dlp'] if path.is_file()), None) or shutil.which('yt-dlp')
    if not downloader:
        parser.error('找不到 yt-dlp；请先运行 ./setup.sh')
    arguments.out_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    for value in dict.fromkeys(arguments.inputs):
        identity = None
        try:
            identity = video_id(value)
            locked_prepare(identity, arguments.out_dir, downloader)
        except (ValueError, OSError, RuntimeError, KeyError, subprocess.TimeoutExpired) as error:
            failed = True
            print(f'{value}: {error}', file=sys.stderr, flush=True)
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
