"""独立中文上传版：唯一中文音轨、最多两行的烧录中文字幕。"""

import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

import delivery_quality
import dubbing_state as state
from prepare_videos import atomic_write, save_json


def wrap_caption(text, columns):
    matches = list(re.finditer(r'[A-Za-z0-9_:.+/\-]+|[^\s]', text))
    pieces = []
    for index, match in enumerate(matches):
        token = match.group()
        if sum(0.55 if ord(character) < 128 else 1 for character in token) > columns:
            raise ValueError('字幕含超宽代码标识符；需人工调整分屏，不能截字')
        space = ' ' if index and text[matches[index - 1].end():match.start()] else ''
        pieces.append(space + token)

    def line_width(line):
        return sum(0.55 if ord(character) < 128 else 1 for character in line)

    complete = ''.join(pieces).strip()
    if not complete:
        raise ValueError('中文字幕无法在两行内完整显示；需调整分屏，不自动删字或缩到不可读')
    if line_width(complete) <= columns:
        return [complete]
    closing = '，。！？；：、）》」』】〕〉〗〙〛…,.!?;:%)]}”’'
    opening = '（《「『【〔〈〖〘〚([{“‘'
    candidates = []
    for boundary in range(1, len(pieces)):
        left = ''.join(pieces[:boundary]).strip()
        right = ''.join(pieces[boundary:]).strip()
        if right[0] in closing or left[-1] in opening:
            continue
        left_width, right_width = line_width(left), line_width(right)
        if max(left_width, right_width) <= columns:
            score = abs(left_width - right_width) - (columns * .2 if left[-1] in closing else 0)
            candidates.append((score, boundary, [left, right]))
    if not candidates:
        raise ValueError('中文字幕无法在两行内完整显示；需调整分屏，不自动删字或缩到不可读')
    return min(candidates)[2]


def ass_time(seconds):
    centiseconds = max(0, round(seconds * 100))
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    whole, fraction = divmod(rest, 100)
    return f'{hours}:{minutes:02d}:{whole:02d}.{fraction:02d}'


def subtitles(work, width, height, duration, position, caption_path=None):
    font_size = max(18, round(height * .032))
    margin = max(12, round(min(width, height) * .035))
    columns = min(28, math.floor((width - 2 * margin) / font_size))
    if columns < 8:
        raise ValueError('画面过窄，无法保证中文字幕可读性')
    header = ('[Script Info]\nScriptType: v4.00+\n'
              f'PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n'
              '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
              'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, '
              'MarginL, MarginR, MarginV, Encoding\n'
              f'Style: Default,Noto Sans CJK SC,{font_size},&H00FFFFFF,&H00FFFFFF,&H00181818,&H00000000,'
              f'0,0,0,0,100,100,0,0,1,{max(1, round(height / 720))},0,{8 if position == "top" else 2},'
              f'{margin},{margin},{margin},1\n\n[Events]\n'
              'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    rows = []
    for start, end, text in state.cues(caption_path or work / 'zh_display.srt'):
        if start >= duration:
            continue
        if any(character in text for character in '{}\\'):
            raise ValueError('上传字幕含 ASS 特殊字符；请人工核对代码字幕，暂不冒险隐藏或改写内容')
        rendered = r'\N'.join(wrap_caption(text, columns))
        rows.append(f'Dialogue: 0,{ass_time(start)},{ass_time(min(duration, end))},Default,,0,0,0,,{rendered}\n')
    if not rows:
        raise ValueError('缺少可烧录的中文字幕')
    return header + ''.join(rows)


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-show_chapters', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def verify(path, duration):
    media = probe(path)
    streams = media['streams']
    audio = [item for item in streams if item['codec_type'] == 'audio']
    if (sum(item['codec_type'] == 'video' for item in streams) != 1 or len(audio) != 1
            or any(item['codec_type'] not in {'video', 'audio', 'data'} for item in streams)
            or audio[0].get('tags', {}).get('language') not in {'chi', 'zho'}
            or abs(float(media['format']['duration']) - duration) > .25):
        raise ValueError('中文上传版轨道、语言或完整时长验收失败')
    for instant in sorted({0, max(0, duration / 2 - .5), max(0, duration - 1)}):
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(instant), '-i', str(path),
                        '-t', '0.5', '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                       capture_output=True, check=True)
    return {'video': 1, 'audio': 1, 'subtitle_tracks': 0, 'audio_language': audio[0]['tags']['language'],
            'duration': float(media['format']['duration']), 'burned_text_human_checked': False}


def export(work, database=None, position='bottom', crf=20):
    import check_videos
    quality = delivery_quality.require(work, database)
    master = check_videos.output_path(work.parent, state.load(work / 'meta.json')['title'])
    rendering = state.validate_render(work, master)
    package = None
    caption_path = None
    if rendering.get('package_manifest'):
        import video_package
        package = video_package.validate(work)
        caption_path = Path(package['subtitles']['zh']['path'])
    source = state.source_video(work)
    waveform = Path(state.validate_narration(work)['waveform']['path'])
    if package:
        source = master
    media = probe(source)
    video = next(item for item in media['streams'] if item['codec_type'] == 'video')
    duration = float(media['format']['duration'])
    subtitle_text = subtitles(work, int(video['width']), int(video['height']), duration, position, caption_path)
    font_result = subprocess.run(['fc-match', '-f', '%{family}\n%{file}', 'Noto Sans CJK SC'], capture_output=True, text=True, check=True).stdout.splitlines()
    if len(font_result) != 2 or 'Noto Sans CJK SC' not in font_result[0] or not Path(font_result[1]).is_file():
        raise ValueError('缺少可核对的中文字幕字体')
    font = font_result[1]
    dependencies = {'quality_bindings': quality['bindings'], 'subtitles': state.signature(subtitle_text),
                    'implementation_sha256': state.digest(__file__), 'font': state.identity(Path(font)),
                    'encoder': subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, check=True).stdout.splitlines()[0],
                    'profile': {'version': 1, 'crf': crf, 'audio_bitrate': '128k', 'position': position}}
    if package:
        dependencies['self_contained'] = {'manifest': rendering['package_manifest'], 'master': state.identity(master),
                                         'audio': 'copy-current-chinese-aac', 'chapters': package['chapters']}
    destination = master.with_name(master.stem.removesuffix('_中文配音') + '_中文上传版.mp4')
    manifest = work / 'upload_export.json'
    if manifest.exists():
        previous = state.load(manifest)
        if previous['dependencies'] == dependencies and state.matches(previous['output']):
            return previous
    descriptor, temporary = tempfile.mkstemp(prefix='.zh-upload-', suffix='.mp4', dir=work.parent)
    os.close(descriptor)
    pending = Path(temporary)
    try:
        with tempfile.TemporaryDirectory(prefix='upload-', dir=work) as directory:
            subtitle_path = Path(directory) / 'zh.ass'
            atomic_write(subtitle_path, subtitle_text)
            command = ['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', str(source)]
            if not package:
                command += ['-i', str(waveform)]
            command += ['-map', '0:v:0', '-map', '0:a:0' if package else '1:a:0',
                       '-map_metadata', '0' if package else '-1', '-map_chapters', '0' if package else '-1', '-sn',
                       '-vf', 'subtitles=zh.ass', '-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf),
                       '-pix_fmt', 'yuv420p']
            command += ['-c:a', 'copy'] if package else ['-c:a', 'aac', '-b:a', '128k', '-af', 'apad']
            command += [
                       '-metadata:s:a:0', 'language=chi', '-metadata:s:a:0', 'title=中文配音',
                       '-disposition:a:0', 'default', '-t', str(duration), '-movflags', '+faststart', str(pending)]
            subprocess.run(command, cwd=directory, capture_output=True, check=True)
        verification = verify(pending, duration)
        if package:
            verification['self_contained'] = video_package.verify_media(pending, package, upload=True)
            evidence = work / 'upload_evidence' / state.signature(dependencies)[:20]
            evidence.mkdir(parents=True, exist_ok=True)
            verification['full_decode'] = video_package.full_decode(pending, evidence, 'full-decode.log')
        final = delivery_quality.require(work, database)
        state.validate_render(work, master)
        if (final['bindings'] != quality['bindings'] or state.signature(subtitles(work, int(video['width']), int(video['height']), duration, position, caption_path))
                != dependencies['subtitles'] or not state.matches(dependencies['font'])):
            raise ValueError('上传版编码期间输入发生变化；未替换旧版本')
        if package:
            video_package.validate(work)
        pending.replace(destination)
        result = {'version': 1, 'created_at': state.now(), 'dependencies': dependencies,
                  'output': state.identity(destination), 'verification': verification, 'quality': final}
        save_json(manifest, result)
        return result
    finally:
        pending.unlink(missing_ok=True)
