"""审校过的 AI 导读、内嵌章节与自包含母版；工程证据不作为交付附件。"""

import argparse
import asyncio
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave
from fractions import Fraction
from pathlib import Path

import dubbing_state as state
from prepare_videos import atomic_write, save_json

SOURCE_FILES = ['en.srt', 'translation.tsv', 'translation_audit.md']
LANGUAGES = ['zh', 'en', 'bilingual']


def run(command, directory=None):
    return subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True)


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                           '-show_chapters', '-of', 'json', str(path)]).stdout)


def validate_plan(work, plan):
    units = state.cues(work / 'en.srt')
    if (plan.get('version') != 1 or plan.get('id') != work.name.removeprefix('.zh_work_yt_')
            or plan.get('source_sha256') != state.hashes(work, SOURCE_FILES)
            or plan.get('reviewed_source_units') != list(range(1, len(units) + 1))):
        raise ValueError('导读计划未绑定完整当前审校来源')
    scenes, chapters = plan.get('intro'), plan.get('chapters')
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 6 or not isinstance(chapters, list) or not chapters:
        raise ValueError('导读场景或语义章节缺失')
    for scene in scenes:
        references = scene.get('source_unit_ids', [])
        if (not references or any(type(number) is not int or not 1 <= number <= len(units) for number in references)
                or not scene.get('review_note', '').strip()):
            raise ValueError('导读事实缺少源单元或具体审校依据')
        for value in [plan.get('title', ''), scene.get('heading', ''), *scene.get('screen', [])]:
            if not isinstance(value, str) or not value.strip() or len(value) > 32 or any(character in value for character in '{}\\\n\r'):
                raise ValueError('导读画面文本为空、过长或含不安全排版字符')
        if not 1 <= len(scene.get('screen', [])) <= 4:
            raise ValueError('每个导读场景需要一至四行简洁重点')
        if not scene.get('captions'):
            raise ValueError('导读缺少中英文字幕')
        for caption in scene['captions']:
            if (not isinstance(caption.get('zh'), str) or not caption['zh'].strip() or len(caption['zh']) > 28
                    or not isinstance(caption.get('en'), str) or not caption['en'].strip() or len(caption['en']) > 150
                    or any(character in caption['zh'] + caption['en'] for character in '{}\\\n\r')):
                raise ValueError('导读字幕缺失、过长或含不安全字符')
    previous = -1
    for chapter in chapters:
        number, title = chapter.get('unit_id'), chapter.get('title')
        if (type(number) is not int or not 1 <= number <= len(units) or number <= previous
                or not isinstance(title, str) or not title.strip() or len(title) > 70
                or any(character in title for character in '\n\r\x00') or not chapter.get('review_note', '').strip()):
            raise ValueError('章节源编号、顺序、标题或审校依据无效')
        previous = number
    if chapters[0]['unit_id'] != 1:
        raise ValueError('第一正文章节必须从完整正文开始，不裁掉开场')
    if plan.get('voice') not in {'zh-CN-YunxiNeural', 'zh-CN-XiaoxiaoNeural', 'zh-CN-YunyangNeural'}:
        raise ValueError('导读须显式选择已支持的中文音色')
    return plan


async def synthesize(plan_path, directory):
    import dub
    import numpy as np
    plan = state.load(plan_path)
    cache = directory / 'tts-cache'
    cache.mkdir(exist_ok=True)
    semaphore = asyncio.Semaphore(2)
    samples = [np.zeros(round(.4 * dub.SR), np.float32)]
    cursor, scenes = .4, []
    for scene in plan['intro']:
        text = ''.join(caption['zh'] for caption in scene['captions'])
        audio = await dub.synth(text, plan['voice'], 0, cache, semaphore)
        if audio is None:
            raise ValueError('导读合成失败，保留原母版')
        decoded = dub.level(dub.decode(audio))
        if not len(decoded):
            raise ValueError('导读返回空音频')
        duration = len(decoded) / dub.SR
        scenes.append({'start': cursor, 'end': cursor + duration, 'audio': state.identity(audio),
                       'text': text, 'rate': 0, 'tempo': 1.0})
        samples.extend([decoded, np.zeros(round(.4 * dub.SR), np.float32)])
        cursor += duration + .4
    samples.append(np.zeros(round(.4 * dub.SR), np.float32))
    waveform = directory / 'intro.wav'
    with wave.open(str(waveform), 'wb') as output:
        output.setparams((1, 2, dub.SR, 0, 'NONE', 'not compressed'))
        output.writeframes(np.clip(np.concatenate(samples) * 32767, -32768, 32767).astype('<i2').tobytes())
    save_json(directory / 'intro_audio.json', {'waveform': state.identity(waveform), 'scenes': scenes,
              'duration': cursor + .4, 'plan_sha256': state.digest(plan_path),
              'voice': plan['voice'], 'max_rate': 0, 'extra_atempo': False, 'human_listened': False})


def ass_cards(plan, audio, width, height, duration):
    import upload_export
    header = ('[Script Info]\nScriptType: v4.00+\n'
              f'PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n'
              '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
              'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, '
              'Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
              f'Style: Default,Noto Sans CJK SC,{round(height * .047)},&H00FFFFFF,&H00FFFFFF,&H00221810,'
              '&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,40,40,40,1\n\n'
              '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    rows = []

    def line(start, end, text, vertical, size, colour='FFFFFF'):
        rows.append(f'Dialogue: 0,{upload_export.ass_time(start)},{upload_export.ass_time(end)},Default,,0,0,0,,'
                    + f'{{\\an5\\pos({width // 2},{round(height * vertical)})\\fs{round(height * size)}\\c&H{colour}&}}'
                    + text + '\n')

    line(0, duration, 'AI 导读 · 非讲者原话', .13, .032, 'F3D276')
    line(0, duration, plan['title'], .29, .059)
    line(0, duration, '章节可跳过导读 · 原声音轨在正文开始后为讲者原声', .87, .025, 'B5A89B')
    for index, (scene, timing) in enumerate(zip(plan['intro'], audio['scenes'])):
        start = 0 if index == 0 else timing['start'] - .2
        end = audio['scenes'][index + 1]['start'] - .2 if index + 1 < len(plan['intro']) else duration
        line(start, end, scene['heading'], .43, .039, 'F3D276')
        for row, text in enumerate(scene['screen']):
            line(start, end, text, .55 + row * .075, .043)
    return header + ''.join(rows)


def write_subtitles(work, directory, plan, audio, offset):
    prefixes = {language: [] for language in LANGUAGES}
    for scene, timing in zip(plan['intro'], audio['scenes']):
        total = sum(len(caption['zh']) for caption in scene['captions'])
        cursor = timing['start']
        for index, caption in enumerate(scene['captions']):
            finish = cursor + (timing['end'] - timing['start']) * len(caption['zh']) / total
            if index == len(scene['captions']) - 1:
                finish = timing['end']
            prefixes['zh'].append((cursor, finish, caption['zh']))
            prefixes['en'].append((cursor, finish, caption['en']))
            prefixes['bilingual'].append((cursor, finish, caption['zh'] + '\n' + caption['en']))
            cursor = finish
    for language in LANGUAGES:
        body = [(start + offset, end + offset, text)
                for start, end, text in state.cues(work / f'{language}_display.srt')]
        atomic_write(directory / f'{language}.srt', state.srt_text(prefixes[language] + body))


def chapter_list(work, plan, offset, duration):
    source = state.cues(work / 'en.srt')
    chapters = [{'start': 0.0, 'title': 'AI 导读｜非讲者原话'}]
    for index, entry in enumerate(plan['chapters']):
        chapters.append({'start': offset if index == 0 else offset + source[entry['unit_id'] - 1][0],
                         'title': entry['title'], 'source_unit_id': entry['unit_id']})
    for index, chapter in enumerate(chapters):
        chapter['end'] = chapters[index + 1]['start'] if index + 1 < len(chapters) else duration
        if not 0 <= chapter['start'] < chapter['end'] <= duration:
            raise ValueError('映射后的章节越界或交叠')
    return chapters


def write_metadata(directory, chapters):
    lines = [';FFMETADATA1', 'title=AI导读与中文译配', 'comment=AI导读不是讲者原话；正文保持来源时间顺序。']
    for chapter in chapters:
        title = re.sub(r'([\\=;#])', r'\\\1', chapter['title'])
        lines.extend(['[CHAPTER]', 'TIMEBASE=1/1000', f'START={round(chapter["start"] * 1000)}',
                      f'END={round(chapter["end"] * 1000)}', 'title=' + title])
    atomic_write(directory / 'chapters.ffmeta', '\n'.join(lines) + '\n')


def validate(work, allow_implementation_change=False):
    rendering = state.load(work / 'render_state.json')
    reference = rendering.get('package_manifest')
    if not reference or not state.matches(reference):
        raise ValueError('缺少有效自包含成片清单')
    manifest = state.load(reference['path'])
    plan = validate_plan(work, state.load(manifest['plan']['path']))
    if (not state.matches(manifest['plan']) or not state.matches(manifest['body'])
            or manifest['dependencies'] != state.render_dependencies(
                work, Path(state.validate_narration(work)['waveform']['path']), state.source_video(work))
            or (not allow_implementation_change and manifest['implementation_sha256'] != state.digest(__file__))
            or manifest['output'] != rendering['output'] or not state.matches(manifest['output'])
            or not state.matches(manifest['intro_audio']['waveform']) or not state.matches(manifest['font'])):
        raise ValueError('导读、章节、正文或自包含成片绑定失效')
    expected = chapter_list(work, plan, manifest['body_offset_seconds'], manifest['duration_seconds'])
    if manifest['chapters'] != expected:
        raise ValueError('章节偏离当前来源与片头时间映射')
    for name, identity in manifest['subtitles'].items():
        if not state.matches(identity):
            raise ValueError(f'映射后的 {name} 字幕身份失效')
    return manifest


def verify_media(path, manifest, upload=False):
    import check_videos
    media = probe(path)
    duration = float(media['format']['duration'])
    tracks = media['streams']
    expected = {'video': 1, 'audio': 1 if upload else manifest['audio_tracks'], 'subtitle': 0 if upload else 3}
    actual = {kind: sum(track['codec_type'] == kind for track in tracks) for kind in expected}
    if actual != expected or abs(duration - manifest['duration_seconds']) > .15:
        raise ValueError(f'自包含成片轨道或总时长不符: {actual}')
    if any(track['codec_name'] != 'h264' or track.get('pix_fmt') != 'yuv420p'
           for track in tracks if track['codec_type'] == 'video'):
        raise ValueError('自包含成片视频编码不符合当前兼容规格')
    audio = [track for track in tracks if track['codec_type'] == 'audio']
    if ([track.get('tags', {}).get('language') for track in audio] != (['chi'] if upload else ['chi', 'eng'][:len(audio)])
            or any(track['codec_name'] != 'aac' for track in audio)
            or audio[0].get('disposition', {}).get('default') != 1):
        raise ValueError('音轨编码、默认项或语言不正确')
    actual_chapters = media.get('chapters', [])
    if len(actual_chapters) != len(manifest['chapters']):
        raise ValueError('文件内未包含全部语义章节')
    for actual_chapter, chapter in zip(actual_chapters, manifest['chapters']):
        if (actual_chapter.get('tags', {}).get('title') != chapter['title']
                or abs(float(actual_chapter['start_time']) - chapter['start']) > .002
                or abs(float(actual_chapter['end_time']) - chapter['end']) > .002):
            raise ValueError('内嵌章节标题或时间不符')
    if not upload:
        subtitles = [track for track in tracks if track['codec_type'] == 'subtitle']
        if [track.get('tags', {}).get('language') for track in subtitles] != ['chi', 'eng', 'chi']:
            raise ValueError('内嵌字幕语言不符')
        for index, language in enumerate(LANGUAGES):
            expected_cues = check_videos.subtitles(Path(manifest['subtitles'][language]['path']).read_text())
            extracted = run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', f'0:s:{index}', '-f', 'srt', '-'])
            actual_cues = check_videos.subtitles(extracted.stdout)
            if len(actual_cues) != len(expected_cues) or any(
                    actual_cue[2] != expected_cue[2] or max(abs(actual_cue[0] - expected_cue[0]),
                    abs(actual_cue[1] - expected_cue[1])) > .002
                    for actual_cue, expected_cue in zip(actual_cues, expected_cues)):
                raise ValueError(f'内嵌 {language} 字幕与映射副本不符')
    return {'tracks': actual, 'chapters': len(actual_chapters), 'duration_seconds': duration,
            'subtitle_content_and_timing_checked': not upload, 'human_listened': False}


def full_decode(path, directory, name):
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
                             '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'], capture_output=True, text=True)
    atomic_write(directory / name, result.stdout + result.stderr)
    if result.returncode or result.stderr.strip():
        raise ValueError('全片解码未通过；原成片保留，参见解码日志')
    return {'exit_code': result.returncode, 'log': state.identity(directory / name)}


def install(work, directory, manifest, rendering, output):
    pending = directory / 'pending.mp4'
    if state.digest(output) != manifest['output']['sha256']:
        if not state.matches(rendering['output']) or not state.matches(manifest['output']):
            raise ValueError('待安装文件或旧成片变化，拒绝覆盖')
        pending.replace(output)
    if (manifest['dependencies'] != state.render_dependencies(
            work, Path(state.validate_narration(work)['waveform']['path']), state.source_video(work))
            or manifest['implementation_sha256'] != state.digest(__file__)):
        raise ValueError('安装恢复时输入或实现变化，保留事务与归档以便复核')
    for external, identity in manifest['archived_sidecars'].items():
        if not state.matches(identity) or (Path(external).exists() and state.digest(external) != identity['sha256']):
            raise ValueError('旁置字幕与归档不同，拒绝删除')
    manifest['output'] = state.identity(output)
    manifest_path = directory / 'manifest.json'
    save_json(manifest_path, manifest)
    waveform = Path(state.validate_narration(work)['waveform']['path'])
    bound = state.bind_render(work, output, waveform, state.source_video(work), rendering['options'], 'self-contained')
    bound['package_manifest'] = state.identity(manifest_path)
    save_json(work / 'render_state.json', bound)
    for external in manifest['archived_sidecars']:
        Path(external).unlink(missing_ok=True)
    return validate(work)


def recover_install(work):
    rendering = state.load(work / 'render_state.json')
    output = Path(rendering['output']['path'])
    if rendering.get('package_manifest') and state.matches(rendering['output']):
        return
    if not output.exists() or state.matches(rendering['output']):
        return
    matches = []
    for path in (work / 'self_contained').glob('*/install.json'):
        transaction = state.load(path)
        if transaction['output'] == str(output) and state.digest(output) == transaction['manifest']['output']['sha256']:
            matches.append((path.parent, transaction))
    if len(matches) != 1:
        raise ValueError('成片偏离绑定，且没有唯一可恢复安装事务')
    directory, transaction = matches[0]
    install(work, directory, transaction['manifest'], transaction['previous_render'], output)


def build(work, feedback=None, database=None):
    import check_videos
    import delivery_quality
    recover_install(work)
    quality = delivery_quality.require(work, database)
    output = check_videos.output_path(work.parent, state.load(work / 'meta.json')['title'])
    rendering = state.validate_render(work, output)
    if rendering.get('package_manifest'):
        manifest = validate(work)
        if feedback and state.digest(feedback) != manifest['plan']['sha256']:
            raise ValueError('新导读计划需先 package-restore，保留旧正文及版本历史后再制作')
        verify_media(output, manifest)
        return manifest
    if (rendering.get('options') or {}).get('subs') != 'soft':
        raise ValueError('当前自包含母版接入需要可切换字幕的干净基础成片')
    if feedback is None:
        raise ValueError('package 需要 --feedback 指定当前AI审校的导读/章节计划')
    plan = validate_plan(work, state.load(feedback))
    dependencies = copy.deepcopy(rendering['dependencies'])
    directory = work / 'self_contained' / state.signature({'plan': plan, 'body': rendering['output']['sha256']})[:20]
    directory.mkdir(parents=True, exist_ok=True)
    plan_path = directory / 'plan.json'
    save_json(plan_path, plan)
    body = directory / 'body.mp4'
    if not body.exists():
        shutil.copy2(output, body)
    if state.digest(body) != rendering['output']['sha256']:
        raise ValueError('旧正文母版归档校验失败')
    save_json(directory / 'body_render.json', rendering)
    originals = {}
    for language in ['zh', 'en']:
        external = output.with_name(output.stem.removesuffix('_中文配音') + f'.{language}.srt')
        if external.exists():
            saved = directory / external.name
            shutil.copy2(external, saved)
            originals[str(external)] = state.identity(saved)
    media = probe(body)
    video = next(track for track in media['streams'] if track['codec_type'] == 'video')
    if video['codec_name'] != 'h264' or video.get('pix_fmt') != 'yuv420p':
        raise ValueError('正文不是当前支持的H.264/yuv420p；需单独核对转码策略，不无声降质')
    frame_rate = Fraction(video['r_frame_rate'])
    interpreter = Path(os.environ.get('PVT_DIR', '/vol_1t/pyvideotrans')) / '.venv/bin/python'
    run([str(interpreter), str(Path(__file__).resolve()), '--synthesize', str(plan_path), '--directory', str(directory)])
    audio = state.load(directory / 'intro_audio.json')
    frames = math.ceil(audio['duration'] * float(frame_rate))
    offset = frames / float(frame_rate)
    body_duration = float(media['format']['duration'])
    duration = offset + body_duration
    if offset > 45:
        raise ValueError('导读超过45秒，需审视文案，不自动提速或截尾')
    width, height = int(video['width']), int(video['height'])
    atomic_write(directory / 'intro.ass', ass_cards(plan, audio, width, height, offset))
    font = run(['fc-match', '-f', '%{family}\n%{file}', 'Noto Sans CJK SC']).stdout.splitlines()
    if len(font) != 2 or 'Noto Sans CJK SC' not in font[0] or not Path(font[1]).is_file():
        raise ValueError('缺少可核对的中文字体')
    timescale = str(Fraction(video['time_base']).denominator)
    run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
         f'color=c=0x101a26:s={width}x{height}:r={frame_rate}', '-vf', 'ass=intro.ass',
         '-frames:v', str(frames), '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
         '-pix_fmt', 'yuv420p', '-video_track_timescale', timescale, 'intro-video.mp4'], directory)
    run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', 'body.mp4', '-map', '0:v:0',
         '-c:v', 'copy', '-an', '-sn', '-dn', '-map_metadata', '-1', '-map_chapters', '-1', 'body-video.mp4'], directory)
    atomic_write(directory / 'video.ffconcat', f"ffconcat version 1.0\nfile 'intro-video.mp4'\nduration {offset:.9f}\nfile 'body-video.mp4'\n")
    run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', 'video.ffconcat',
         '-map', '0:v:0', '-c:v', 'copy', '-an', '-map_metadata', '-1', '-map_chapters', '-1', 'video.mp4'], directory)
    write_subtitles(work, directory, plan, audio, offset)
    chapters = chapter_list(work, plan, offset, duration)
    write_metadata(directory, chapters)
    waveform = state.validate_narration(work)['waveform']
    audio_tracks = len([track for track in media['streams'] if track['codec_type'] == 'audio'])
    if audio_tracks not in [1, 2]:
        raise ValueError('基础母版音轨数不受支持')
    command = ['ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', 'video.mp4', '-i', 'body.mp4',
               '-i', 'intro.wav', '-i', waveform['path']]
    for language in LANGUAGES:
        command += ['-i', f'{language}.srt']
    command += ['-f', 'ffmetadata', '-i', 'chapters.ffmeta']
    filters = []
    if audio_tracks == 2:
        filters.append('[2:a]asplit=2[prefix0][prefix1]')
    for index in range(audio_tracks):
        prefix = f'prefix{index}' if audio_tracks == 2 else '2:a'
        sample_rate, layout = (24000, 'mono') if index == 0 else (44100, 'stereo')
        source = '3:a:0' if index == 0 else '1:a:1'
        filters.extend([f'[{prefix}]apad,atrim=end={offset:.9f},asetpts=PTS-STARTPTS,'
                        f'aresample={sample_rate},aformat=channel_layouts={layout}[head{index}]',
                        f'[{source}]asetpts=PTS-STARTPTS,aresample={sample_rate},'
                        f'aformat=channel_layouts={layout}[body{index}]',
                        f'[head{index}][body{index}]concat=n=2:v=0:a=1[out{index}]'])
    command += ['-filter_complex', ';'.join(filters), '-map', '0:v:0']
    for index in range(audio_tracks):
        command += ['-map', f'[out{index}]']
    for index, language in enumerate(LANGUAGES):
        command += ['-map', f'{index + 4}:0']
    command += ['-map_metadata', '7', '-map_chapters', '7', '-c:v', 'copy', '-c:a', 'aac', '-c:s', 'mov_text']
    for index in range(audio_tracks):
        command += [f'-b:a:{index}', '128k' if index == 0 else '192k', f'-metadata:s:a:{index}',
                    'language=' + ('chi' if index == 0 else 'eng'), f'-metadata:s:a:{index}',
                    'title=' + ('中文配音（含AI导读）' if index == 0 else 'English原声（片头为中文AI导读）'),
                    f'-disposition:a:{index}', 'default' if index == 0 else '0']
    for index, language in enumerate(['chi', 'eng', 'chi']):
        command += [f'-metadata:s:s:{index}', 'language=' + language, f'-disposition:s:{index}', '0']
    command += ['-t', f'{duration:.9f}', '-movflags', '+faststart', 'pending.mp4']
    run(command, directory)
    pending = directory / 'pending.mp4'
    manifest = {'version': 1, 'created_at': state.now(), 'plan': state.identity(plan_path),
                'body': state.identity(body), 'dependencies': dependencies,
                'implementation_sha256': state.digest(__file__), 'font': state.identity(Path(font[1])),
                'intro_audio': audio, 'body_offset_seconds': offset, 'intro_frames': frames,
                'body_duration_seconds': body_duration, 'duration_seconds': duration,
                'chapters': chapters, 'audio_tracks': audio_tracks,
                'subtitles': {language: state.identity(directory / f'{language}.srt') for language in LANGUAGES},
                'full_human_listening': False, 'release_or_upload_performed': False}
    manifest['verification'] = verify_media(pending, manifest)
    manifest['full_decode'] = full_decode(pending, directory, 'full-decode.log')
    if (delivery_quality.require(work, database)['bindings'] != quality['bindings']
            or not state.matches(rendering['output']) or validate_plan(work, state.load(plan_path)) != plan):
        raise ValueError('制作期间基础输入变化，拒绝替换')
    for external, saved in originals.items():
        if not Path(external).is_file() or state.digest(external) != saved['sha256']:
            raise ValueError('制作期间外挂字幕变化，拒绝替换或隐藏')
    manifest['output'] = state.identity(pending)
    manifest['archived_sidecars'] = originals
    save_json(directory / 'install.json', {'manifest': manifest, 'output': str(output),
                                         'previous_render': rendering})
    return install(work, directory, manifest, rendering, output)


def restore(work):
    manifest = validate(work, allow_implementation_change=True)
    output = Path(manifest['output']['path'])
    directory = Path(manifest['body']['path']).parent
    archived = directory / 'packaged-master.mp4'
    for external, identity in manifest['archived_sidecars'].items():
        if not state.matches(identity) or Path(external).exists():
            raise ValueError('恢复旧字幕时发现来源变化或目标已存在')
    shutil.copy2(output, archived)
    if state.digest(archived) != manifest['output']['sha256']:
        raise ValueError('导读版归档失败')
    temporary = directory / 'restore.mp4'
    shutil.copy2(manifest['body']['path'], temporary)
    temporary.replace(output)
    rendering = state.load(directory / 'body_render.json')
    rendering['output'] = state.identity(output)
    save_json(work / 'render_state.json', rendering)
    for external, identity in manifest['archived_sidecars'].items():
        shutil.copy2(identity['path'], external)
    return rendering


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--synthesize', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(synthesize(arguments.synthesize, arguments.directory))
