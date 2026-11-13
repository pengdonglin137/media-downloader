#!/usr/bin/env python3
"""查看译配阶段，或验证成品轨道、字幕、时长与抽样解码。"""

import argparse
import fcntl
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from prepare_videos import DEFAULT_OUT, atomic_write, save_json, video_id
import dubbing_state as state

VERIFICATION_VERSION = 3


def fingerprint(path):
    metadata = path.stat()
    return {'path': str(path), 'bytes': metadata.st_size, 'mtime_ns': metadata.st_mtime_ns}


def digest(path):
    return state.digest(path)


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(result.stdout)


def seconds(value):
    match = re.fullmatch(r'(\d+):([0-5]\d):([0-5]\d)[,.](\d{3})', value.strip())
    if not match:
        raise ValueError(f'字幕时间格式无效: {value}')
    hours, minutes, whole, milliseconds = map(int, match.groups())
    return hours * 3600 + minutes * 60 + whole + milliseconds / 1000


def subtitles(text):
    entries = []
    for block in re.split(r'\n\s*\n', text.lstrip('\ufeff').replace('\r\n', '\n').strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3 or '-->' not in lines[1]:
            raise ValueError('字幕含无效或空白条目')
        start, end = [seconds(value) for value in lines[1].split('-->')]
        content = re.sub(r'\s+', ' ', ' '.join(lines[2:])).strip()
        if start < 0 or end <= start or not content:
            raise ValueError('字幕含无效时间或空白文字')
        if entries and start < entries[-1][0]:
            raise ValueError('字幕起点倒退')
        entries.append((start, end, content))
    return entries


def translations(path, unit_count):
    entries = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        number, content = line.split('\t', 1)
        number = int(number)
        content = re.sub(r'\s+', ' ', content).strip()
        if not 1 <= number <= unit_count or number in entries or not content:
            raise ValueError('译文 TSV 含重复、越界或空白条目')
        entries[number] = content
    return entries


def translation_inputs(work):
    return {str(work / name): digest(work / name) if (work / name).exists() else None
            for name in ['translation.tsv', 'spoken_overrides.json', 'translation_task.json']}


def output_path(root, title):
    base = re.sub(r'[\\/:*?"<>|]+', '_', re.sub(r"[^\w\s.,'()&+\-_/:|\u3000-\u303f\uff00-\uffef]", '', title))
    base = re.sub(r'\s+', ' ', base)[:80].strip(' ._')
    return root / f'{base}_中文配音.mp4'


def verify(work, output, record):
    source = state.source_video(work)
    source_identity = fingerprint(source)
    output_identity = fingerprint(output)
    bound_inputs = translation_inputs(work)
    current_files = {str(work / name): digest(work / name)
                     for name in ['en.srt', 'zh.srt', 'spoken.srt', 'tts_stats.json', 'timing_review.json']}
    original = probe(source)
    media = probe(output)
    duration = float(media['format']['duration'])
    source_duration = float(original['format']['duration'])
    streams = media['streams']
    rendering = state.load(work / 'render_state.json') if (work / 'render_state.json').exists() else {}
    package = None
    if rendering.get('package_manifest'):
        import video_package
        package = video_package.validate(work)
        video_package.verify_media(output, package)
        current_files[rendering['package_manifest']['path']] = rendering['package_manifest']['sha256']
        current_files[str(Path(video_package.__file__).resolve())] = state.digest(video_package.__file__)
        current_files[package['plan']['path']] = package['plan']['sha256']
    expected_duration = package['duration_seconds'] if package else source_duration
    subtitle_mode = (rendering.get('options') or {}).get('subs', 'soft')
    has_original_audio = any(stream['codec_type'] == 'audio' for stream in original['streams'])
    counts = {kind: sum(stream['codec_type'] == kind for stream in streams) for kind in ['video', 'audio', 'subtitle']}
    languages = {kind: [stream.get('tags', {}).get('language') for stream in streams if stream['codec_type'] == kind] for kind in ['audio', 'subtitle']}
    subtitle_languages = ['chi', 'eng', 'chi'] if subtitle_mode == 'soft' else []
    audio_languages = ['chi', 'eng'] if has_original_audio else ['chi']
    if counts != {'video': 1, 'audio': len(audio_languages), 'subtitle': len(subtitle_languages)} or languages != {'audio': audio_languages, 'subtitle': subtitle_languages}:
        raise ValueError(f'成品轨道或语言不符: {counts}, {languages}')
    if abs(duration - expected_duration) > (0.15 if package else 1):
        raise ValueError('成品与原片时长不符')
    for stream in streams:
        if stream['codec_type'] in {'video', 'audio'} and abs(float(stream.get('duration', duration)) - expected_duration) > 1.5:
            raise ValueError('成品某音视频轨时长与原片不符')
    for track, language in enumerate(['zh', 'en', 'bilingual'] if subtitle_mode == 'soft' else []):
        expected_path = Path(package['subtitles'][language]['path']) if package else work / f'{language}_display.srt'
        current_files[str(expected_path)] = digest(expected_path)
        expected = subtitles(expected_path.read_text(encoding='utf-8-sig'))
        if any(right[0] < left[1] - 0.001 for left, right in zip(expected, expected[1:])) or expected[-1][1] > duration + 0.1:
            raise ValueError(f'{language} 字幕时间交叠或越界')
        extracted = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-map', f'0:s:{track}', '-f', 'srt', '-'], capture_output=True, text=True, check=True, timeout=60)
        actual = subtitles(extracted.stdout)
        if len(actual) != len(expected) or any(left[2] != right[2] or max(abs(left[0] - right[0]), abs(left[1] - right[1])) > 0.002 for left, right in zip(actual, expected)):
            raise ValueError(f'内嵌 {language} 字幕与当前工作副本不同')
        if language in {'zh', 'en'} and not package:
            external = output.with_name(output.stem.removesuffix('_中文配音') + f'.{language}.srt')
            current_files[str(external)] = digest(external)
            if subtitles(external.read_text(encoding='utf-8-sig')) != expected:
                raise ValueError(f'外挂 {language} 字幕与当前工作副本不同')
    anchors = [min(5, duration / 4), duration / 2, max(0, duration - 15)]
    if package:
        anchors += [max(0, package['body_offset_seconds'] - 1), package['body_offset_seconds'] + 1]
        record['self_contained'] = {'body_offset_seconds': package['body_offset_seconds'],
                                  'chapters': len(package['chapters']), 'manifest': rendering['package_manifest']}
    for anchor in anchors:
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-ss', str(anchor), '-i', str(output), '-t', '5', '-map', '0:v:0', '-map', '0:a:0', '-map', '0:a:1?', '-f', 'null', '-'], capture_output=True, check=True, timeout=120)
    if fingerprint(source) != source_identity or fingerprint(output) != output_identity or translation_inputs(work) != bound_inputs or any(digest(Path(path)) != value for path, value in current_files.items()):
        raise ValueError('验收期间输入或成品发生变化，请等待处理结束再重试')
    record.update(state='verified', verification_version=VERIFICATION_VERSION, output_fingerprint=output_identity, source_fingerprint=source_identity, output_duration=duration, source_duration=source_duration, track_counts=counts, languages=languages, input_sha256=current_files, translation_inputs_sha256=bound_inputs, workflow_bindings=state.hashes(work, state.BINDINGS), decode_anchors_seconds=anchors, decode_seconds_per_anchor=5, full_human_listening=False, verified_at=datetime.now(timezone.utc).isoformat())
    save_json(work / 'delivery_check.json', record)


def inspect(identity, root, perform_verification, allow_legacy=False):
    work = root / f'.zh_work_yt_{identity}'
    record = {'id': identity, 'work': str(work), 'state': 'not_started', 'findings': []}
    try:
        meta_path = work / 'meta.json'
        if not meta_path.exists():
            record['state'] = 'preparing' if work.exists() else 'not_started'
            if (work / 'preparation.json').exists():
                preparation = json.loads((work / 'preparation.json').read_text())
                if preparation.get('stage') == 'failed':
                    record.update(state='needs_review', findings=[preparation.get('error', '素材准备失败')])
            return record
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        record['title'] = meta['title']
        source_path = work / 'en.srt'
        translated_path = work / 'zh.srt'
        if not source_path.exists():
            record['state'] = 'preparing'
            return record
        english = subtitles(source_path.read_text(encoding='utf-8-sig'))
        record['source_units'] = len(english)
        task_path = work / 'translation_task.json'
        record['source_task_bound'] = task_path.exists()
        if task_path.exists():
            task = json.loads(task_path.read_text(encoding='utf-8'))
            if task['id'] != identity or task['units'] != len(english) or task['source_sha256'] != digest(source_path):
                raise ValueError('英文字幕已偏离翻译任务绑定，需重新审校并导出任务')
        record['state'] = 'awaiting_translation'
        translations_path = work / 'translation.tsv'
        translated_units = translations(translations_path, len(english)) if translations_path.exists() else None
        if translated_units is not None:
            record['translated_units'] = len(translated_units)
            if len(translated_units) == len(english):
                record['state'] = 'awaiting_compilation'
        if not translated_path.exists():
            return record
        if not allow_legacy:
            state.validate_compilation(work)
        chinese = subtitles(translated_path.read_text(encoding='utf-8-sig'))
        record['translated_units'] = len(chinese)
        if len(english) != len(chinese) or any(original[:2] != translation[:2] for original, translation in zip(english, chinese)):
            raise ValueError('中英文条数或原片时间对齐不符')
        if translated_units is not None and (len(translated_units) != len(chinese) or any(translated_units[index] != entry[2] for index, entry in enumerate(chinese, 1))):
            raise ValueError('中文字幕与当前译文 TSV 不符，请重新编译后配音')
        record['state'] = 'awaiting_dubbing'
        stats_path = work / 'tts_stats.json'
        if not stats_path.exists():
            return record
        if not allow_legacy:
            narration = state.validate_narration(work)
            record['provenance_origin'] = narration['origin']
            record['historical_generation_proven'] = narration['historical_generation_proven']
        stats = json.loads(stats_path.read_text())
        spoken = subtitles((work / 'spoken.srt').read_text(encoding='utf-8-sig'))
        if len(spoken) != len(english) or any(original[:2] != speech[:2] for original, speech in zip(english, spoken)):
            raise ValueError('朗读稿条数或原片时间对齐不符')
        record['tts_summary'] = {key: value for key, value in stats.items() if key != 'per'}
        count = state.narration_count(work)
        state.check_stats(stats, count)
        if stats.get('entries') != count or len(stats.get('per', [])) != count or any(stats.get(key, 1) for key in ['silent', 'overlap', 'atempo']):
            raise ValueError('配音条数、空音频、交叠或额外时间压缩检查不通过')
        if any(row[0] > 15 or row[1] > 1.001 for row in stats['per']):
            raise ValueError('配音超过 15% 提速门槛或使用额外时间压缩')
        review = work / 'timing_review.json'
        if not review.exists() or json.loads(review.read_text()) != []:
            raise ValueError('配音时窗尚未复核通过')
        output = output_path(root, meta['title'])
        record['output'] = str(output)
        record['state'] = 'awaiting_verification' if output.exists() else 'awaiting_render'
        if output.exists() and not allow_legacy:
            rendering = state.validate_render(work, output)
            if rendering.get('package_manifest'):
                import video_package
                video_package.validate(work)
        if output.exists() and perform_verification:
            verify(work, output, record)
        elif output.exists() and (work / 'delivery_check.json').exists():
            previous = json.loads((work / 'delivery_check.json').read_text())
            inputs = previous.get('input_sha256', {})
            source = previous.get('source_fingerprint', {})
            if previous.get('verification_version') == VERIFICATION_VERSION and previous.get('workflow_bindings') == state.hashes(work, state.BINDINGS) and previous.get('translation_inputs_sha256') == translation_inputs(work) and previous.get('output_fingerprint') == fingerprint(output) and inputs and source and fingerprint(Path(source['path'])) == source and all(Path(path).exists() and digest(Path(path)) == value for path, value in inputs.items()):
                record = previous
        if not allow_legacy:
            from review_videos import review_status
            record['review'] = review_status(work)
            from speech_review import status as speech_status
            record['speech_review'] = speech_status(work)
            try:
                from delivery_quality import evaluate
                record['quality_gate'] = evaluate(work)
            except (ValueError, OSError, KeyError, TypeError) as error:
                record['quality_gate'] = {'state': 'blocked', 'allowed': False, 'error': str(error)}
    except (ValueError, OSError, KeyError, TypeError, AttributeError, StopIteration, IndexError, subprocess.SubprocessError) as error:
        record['state'] = 'needs_review'
        record['findings'].append(str(error))
    return record


def write_index(records, destination):
    labels = {'not_started': '尚未开始', 'preparing': '准备素材', 'awaiting_translation': '翻译中',
              'awaiting_compilation': '待编译译文', 'awaiting_dubbing': '配音中',
              'awaiting_render': '待渲染', 'awaiting_verification': '待验收',
              'verified': '技术验收通过', 'needs_review': '需复核'}

    def link(path, label):
        address = quote(os.path.relpath(path, destination.parent))
        return f'<a href="{html.escape(address, quote=True)}">{html.escape(label)}</a>'

    rows = []
    for record in records:
        links = []
        if record['state'] == 'verified':
            output = Path(record['output'])
            links.append(link(output, '播放视频'))
            for language, label in [('zh', '中文字幕'), ('en', '英文字幕')]:
                subtitle = output.with_name(output.stem.removesuffix('_中文配音') + f'.{language}.srt')
                if subtitle.exists():
                    links.append(link(subtitle, label))
        audit = Path(record['work']) / 'translation_audit.md'
        if audit.exists():
            links.append(link(audit, '审校记录'))
        review_page = Path(record['work']) / 'risk_review.html'
        if review_page.exists():
            links.append(link(review_page, '风险复核与原声对照'))
        speech = record.get('speech_review', {})
        speech_page = Path(record['work']) / 'speech_review.html'
        if speech.get('state') not in {'stale', 'not_generated', None} and speech_page.exists():
            links.append(link(speech_page, f'术语与停顿候选（{speech.get("pause_candidates", 0)} 处停顿）'))
        preview = Path(record['work']) / 'speech_preview.json'
        if preview.exists():
            try:
                from speech_polish import dependencies as preview_dependencies
                observed = json.loads(preview.read_text(encoding='utf-8'))
                if observed['dependencies'] == preview_dependencies(Path(record['work'])) and state.matches(observed['manifest']):
                    links.append(link(Path(observed['directory']) / 'index.html', '局部 A/B（待试听）'))
            except (ValueError, OSError, KeyError, TypeError):
                pass
        title = html.escape(record.get('title', record['id']))
        findings = html.escape('；'.join(record['findings']))
        progress = f'{record.get("translated_units", 0)}/{record.get("source_units", 0)}'
        review = record.get('review', {})
        review_label = f'文字复核 {review.get("text_reviewed", 0)}；抽样听辨 {review.get("listened_units", 0)}；全片试听未验收'
        gate = record.get('quality_gate', {})
        review_label += '；已确认问题门禁：' + ('可交付，候选仍待复核' if gate.get('allowed') else '阻断/证据待更新')
        if record.get('provenance_origin') == 'legacy-adopted':
            review_label += '；旧工程仅建立当前观察基线，历史生成未追认'
        rows.append(f'<tr><td>{title}<br><small>{html.escape(record["id"])}</small></td>'
                    f'<td>{labels.get(record["state"], html.escape(record["state"]))}<br>{findings}<br>{review_label}</td>'
                    f'<td>{progress}</td><td>{" · ".join(links)}</td></tr>')
    completed = sum(record['state'] == 'verified' for record in records)
    updated = datetime.now(timezone.utc).isoformat(timespec='seconds')
    atomic_write(destination, '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">'
                 '<title>中文译配进度与成品</title><style>'
                 'body{font:16px/1.6 system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem}'
                 'table{border-collapse:collapse;width:100%}td,th{padding:.7rem;text-align:left;'
                 'border-bottom:1px solid #ccc;vertical-align:top}small{color:#555}a{color:#0759ad}'
                 '</style><h1>中文译配进度与成品</h1>'
                 f'<p>技术验收通过：{completed}/{len(records)} 部。更新时间（UTC）：{updated}</p>'
                 '<p>成品保留中文配音及英文原声；中文、英文、中英双语字幕可独立切换。'
                 '验收包含轨道、字幕、时长及三处抽样解码，不代表全片人工试听或语言质量最终验收。'
                 '只有通过技术验收的成品显示播放入口；本页不自动刷新，重新运行检查命令可更新。</p>'
                 '<table><thead><tr><th>视频</th><th>状态</th><th>翻译单元</th><th>本地文件</th>'
                 '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></html>\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='*', help='YouTube 链接或 ID；省略则检查输出目录全部 YouTube 工程')
    parser.add_argument('-o', '--out-dir', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--verify', action='store_true', help='实际检查轨道、字幕与三段音视频解码，而非只查看阶段')
    parser.add_argument('--report', type=Path, help='将完整检查清单原子写入 JSON 文件')
    parser.add_argument('--index', type=Path, help='生成可离线打开、含已验证成品入口的 HTML 索引')
    arguments = parser.parse_args()
    try:
        identities = list(dict.fromkeys(video_id(value) for value in arguments.inputs)) if arguments.inputs else [path.name.removeprefix('.zh_work_yt_') for path in sorted(arguments.out_dir.glob('.zh_work_yt_*'))]
    except ValueError as error:
        parser.error(str(error))
    records = []
    for identity in identities:
        work = arguments.out_dir / f'.zh_work_yt_{identity}'
        if work.exists():
            with (work / '.workflow.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_SH)
                records.append(inspect(identity, arguments.out_dir, arguments.verify))
        else:
            records.append(inspect(identity, arguments.out_dir, arguments.verify))
    for record in records:
        print(f'{record["id"]}\t{record["state"]}\t{record.get("translated_units", 0)}/{record.get("source_units", 0)}\t{record.get("title", "")}')
        for finding in record['findings']:
            print('  ' + finding)
    if arguments.report:
        save_json(arguments.report, {'checked_at': datetime.now(timezone.utc).isoformat(), 'videos': records, 'full_human_listening': False})
    if arguments.index:
        write_index(records, arguments.index)
    return int(any(record['state'] == 'needs_review' for record in records))


if __name__ == '__main__':
    sys.exit(main())
