"""Render source-picture/native-audio insertion previews without installing a film."""

import argparse
import fcntl
import hashlib
import json
import math
import re
import shutil
import signal
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import dubbing_state as state
import prepare_videos
from media_workflow import insertion_audition as audition
from media_workflow import native_audio


KIND = 'av-context-insertion-preview-v1'
SCOPE = 'isolated-audition-no-production-installation'
ROOT = Path(__file__).resolve().parents[1]
require = audition.require
load = audition.load


def inputs(path):
    plan = load(path)
    require(isinstance(plan, dict) and set(plan) == {'kind', 'scope', 'source', 'profile',
        'context_frames', 'at_frame', 'cases', 'native_ledger', 'evidence'}
        and plan['kind'] == KIND and plan['scope'] == SCOPE, 'Unexpected contextual preview plan')
    profile = plan['profile']
    require(isinstance(profile, dict) and set(profile) == {'fps', 'width', 'height', 'time_base',
        'sample_rate', 'channels', 'video_stream', 'audio_stream'}, 'Unexpected source profile')
    for name, minimum, maximum in [('fps', 1, 60), ('width', 16, 3840), ('height', 16, 2160),
            ('sample_rate', 8000, 192000), ('channels', 1, 2), ('video_stream', 0, 100), ('audio_stream', 0, 100)]:
        require(type(profile[name]) is int and minimum <= profile[name] <= maximum, f'Invalid {name}')
    require(profile['width'] % 2 == profile['height'] % 2 == 0
        and profile['video_stream'] != profile['audio_stream'], 'Invalid geometry or stream mapping')
    require(isinstance(profile['time_base'], str)
        and re.fullmatch(r'1/[1-9][0-9]{0,8}', profile['time_base']), 'Expected explicit video time base')
    clock = Fraction(profile['time_base'])
    require(profile['sample_rate'] % profile['fps'] == 0
        and (1 / clock).denominator == 1 and int(1 / clock) % profile['fps'] == 0,
        'This adapter requires common integer source frame/sample boundaries')
    bounds = plan['context_frames']
    require(isinstance(bounds, list) and len(bounds) == 2 and all(type(value) is int for value in bounds)
        and 0 <= bounds[0] < bounds[1] and bounds[1] - bounds[0] <= profile['fps'] * 600,
        'Expected a positive context of at most ten minutes')
    require(type(plan['at_frame']) is int and bounds[0] < plan['at_frame'] < bounds[1],
        'Insertion must be on an interior source frame')
    require(isinstance(plan['cases'], list) and 1 <= len(plan['cases']) <= 4, 'Expected one to four alternatives')
    require(isinstance(plan['evidence'], list) and 1 <= len(plan['evidence']) <= 32, 'Bind anchor evidence')
    for record in [plan['source'], plan['native_ledger'], *plan['evidence']]:
        audition.bound_file(record)
    keys = set()
    for case in plan['cases']:
        require(isinstance(case, dict) and set(case) == {'key', 'waveform', 'card', 'caption_reference_seconds'},
            'Unexpected contextual preview case')
        require(isinstance(case['key'], str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', case['key'])
            and case['key'] not in keys, 'Invalid or duplicate alternative key')
        keys.add(case['key'])
        require(isinstance(case['caption_reference_seconds'], str)
            and re.fullmatch(r'\d+(?:\.\d+)?', case['caption_reference_seconds']), 'Keep the distinct caption reference')
        audition.bound_file(case['card'])
        audition.bound_file(case['waveform'])
        audio = audition.wave_format(case['waveform']['path'])
        require(audio['samples'] <= audio['sample_rate'] * 40, 'Complete commentary exceeds forty seconds')
        require(audio['channels'] in (1, profile['channels']), 'Unsupported explicit channel conversion')
    tools = {name: state.identity(Path(shutil.which(name) or '').resolve()) for name in ('ffmpeg', 'ffprobe')}
    return plan, {'plan': state.identity(path), 'tools': tools, 'implementations': {
        name: state.identity(filename) for name, filename in [('contextual_preview', __file__),
        ('insertion_audition', audition.__file__), ('native_audio', native_audio.__file__),
        ('dubbing_state', state.__file__), ('prepare_videos', prepare_videos.__file__)]}}


def ledger(path):
    rows = []
    for line in Path(path).read_text().splitlines():
        if line and not line.startswith('#'):
            fields = [value.strip() for value in line.split(',')]
            require(len(fields) == 6 and re.fullmatch('[0-9a-f]{64}', fields[5]), 'Malformed frame ledger')
            rows.append((*[int(value) for value in fields[:5]], fields[5]))
    require(rows, 'Empty decoded-frame ledger')
    return rows


def timing(plan, case):
    profile = plan['profile']
    audio = audition.wave_format(case['waveform']['path'])
    frames = math.ceil(Fraction(audio['samples'] * profile['fps'], audio['sample_rate']))
    samples_per_frame = profile['sample_rate'] // profile['fps']
    return {'insert_frames': frames, 'insert_samples': frames * samples_per_frame,
        'source_frames': plan['context_frames'][1] - plan['context_frames'][0],
        'source_samples': (plan['context_frames'][1] - plan['context_frames'][0]) * samples_per_frame,
        'at_frame': plan['at_frame'] - plan['context_frames'][0],
        'at_sample': (plan['at_frame'] - plan['context_frames'][0]) * samples_per_frame,
        'original_waveform_samples': audio['samples'], 'original_waveform_rate': audio['sample_rate'],
        'caption_reference_seconds': case['caption_reference_seconds'],
        'source_anchor_seconds': str(Fraction(plan['at_frame'], profile['fps']))}


def review_card(text, key, font_path, font_index, at_frame, fps, caption_reference, destination):
    from PIL import Image, ImageDraw, ImageFont
    from media_workflow.commentary_visual import wrap_text
    require(isinstance(text, str) and text and '\n' not in text, 'Expected a complete single-line correction')
    require(type(at_frame) is int and at_frame >= 0 and type(fps) is int and fps > 0,
        'Expected an exact frame anchor for the review card')
    require(not Path(destination).exists(), 'Review card already exists')
    image = Image.new('RGB', (1920, 1080), '#111d2e')
    draw = ImageDraw.Draw(image)
    heading = ImageFont.truetype(str(font_path), 48, index=font_index)
    body = ImageFont.truetype(str(font_path), 56, index=font_index)
    small = ImageFont.truetype(str(font_path), 36, index=font_index)
    draw.text((80, 60), '隔离声画交接试听 · AI补充说明（非讲者原话）', font=heading, fill='#ffd778')
    draw.text((80, 140), '未选胜 · 未安装正片 · 未批准发音、情感或原声交接', font=small, fill='#d4deeb')
    rows = wrap_text(text, body, 1760)
    require(len(rows) <= 7, 'Complete correction exceeds the card body')
    for index, row in enumerate(rows):
        draw.text((80, 260 + index * 82), row, font=body, fill='#ffffff')
    anchor = Fraction(at_frame, fps)
    seconds = f'{float(anchor):.6f}'
    draw.text((80, 880), f'本预览暂停点：原片第 {at_frame} 帧 / {seconds} 秒（精确值 {anchor} 秒）',
        font=small, fill='#a7d7ff')
    draw.text((80, 940), f'原字幕参考：{caption_reference} 秒；不是上述交接点。候选：{key}', font=small, fill='#d4deeb')
    draw.text((80, 1000), '只在本预览暂停后继续原画；原始媒体文件未改，未据此批准角色或听辨。', font=small, fill='#d4deeb')
    image.save(destination)
    return {'card': state.identity(destination), 'full_text': text, 'body_rows': rows,
        'source_frame': at_frame, 'source_seconds': str(anchor), 'caption_reference_seconds': caption_reference}


def native_reference(plan):
    profile = plan['profile']
    start, end = [frame * (profile['sample_rate'] // profile['fps']) for frame in plan['context_frames']]
    text = Path(plan['native_ledger']['path']).read_text()
    require(f'#tb 0: 1/{profile["sample_rate"]}\n' in text and '#codec_id 0: pcm_f32le\n' in text,
        'Native reference is not the declared float32 sample clock')
    rows = [row for row in ledger(plan['native_ledger']['path']) if row[2] < end and row[2] + row[3] > start]
    require(rows and rows[0][2] <= start and rows[-1][2] + rows[-1][3] >= end, 'Native reference lacks context')
    position = rows[0][2]
    for stream, dts, pts, duration, size, checksum in rows:
        require(stream == 0 and dts == pts == position and duration > 0
            and size == duration * profile['channels'] * 4, 'Native reference is not contiguous float32 PCM')
        position += duration
    return rows, start, end


def validate_probe(plan, probe):
    profile = plan['profile']
    streams = {item['index']: item for item in probe['streams']}
    video, audio = streams[profile['video_stream']], streams[profile['audio_stream']]
    require(video['codec_type'] == 'video' and audio['codec_type'] == 'audio'
        and video['width'] == profile['width'] and video['height'] == profile['height']
        and video['pix_fmt'] == 'yuv420p' and video['time_base'] == profile['time_base']
        and Fraction(video['r_frame_rate']) == Fraction(video['avg_frame_rate']) == profile['fps']
        and int(video['start_pts']) == 0 and int(audio['start_pts']) == 0
        and audio['time_base'] == f'1/{profile["sample_rate"]}'
        and int(audio['sample_rate']) == profile['sample_rate'] and audio['channels'] == profile['channels'],
        'Actual streams do not match the explicitly supported source profile')
    require(plan['context_frames'][1] / profile['fps'] <= Fraction(video['duration_ts']) * Fraction(video['time_base']),
        'Context exceeds source video')


def comparisons(plan, output):
    profile = plan['profile']
    rows, start, end = native_reference(plan)
    original = (output / 'native-reference.f32le').read_bytes()
    position = 0
    for row in rows:
        payload = original[position:position + row[4]]
        require(len(payload) == row[4] and hashlib.sha256(payload).hexdigest() == row[5],
            f'Native source frame changed at sample {row[2]}')
        position += row[4]
    require(position == len(original), 'Unexpected native reference samples')
    stride = profile['channels'] * 4
    context = original[(start - rows[0][2]) * stride:(end - rows[0][2]) * stride]
    require((output / 'source-context.f32le').read_bytes() == context, 'Source context PCM changed')
    reference = ledger(output / 'source-reference.framehash')
    require(ledger(output / 'source-context.framehash') == reference, 'Source picture or presentation grid changed')
    ticks = int(1 / Fraction(profile['time_base'])) // profile['fps']
    require(len(reference) == plan['context_frames'][1] - plan['context_frames'][0]
        and all(row[:4] == (0, index * ticks, index * ticks, ticks) for index, row in enumerate(reference)),
        'Actual source context is not the complete consecutive frame grid')
    def audio_grid(name, payload):
        text = (output / f'{name}.audio.framehash').read_text()
        require(f'#tb 0: 1/{profile["sample_rate"]}\n' in text, 'Unexpected decoded audio clock')
        position = 0
        for stream, dts, pts, duration, size, checksum in ledger(output / f'{name}.audio.framehash'):
            require(stream == 0 and dts == pts == position and duration > 0 and size == duration * stride
                and hashlib.sha256(payload[position * stride:(position + duration) * stride]).hexdigest() == checksum,
                'Audio timestamp discontinuity or decoded sample change')
            position += duration
        require(position * stride == len(payload), 'Audio ledger does not cover the complete PCM')
    audio_grid('source-context', context)
    results = []
    for case in plan['cases']:
        key = case['key']
        observed = timing(plan, case)
        converted = (output / f'{key}-converted.f32le').read_bytes()
        require(len(converted) % stride == 0 and 0 < len(converted) <= observed['insert_samples'] * stride,
            'Complete converted commentary does not fit the explicit insertion duration')
        padding = observed['insert_samples'] * stride - len(converted)
        require(padding < profile['sample_rate'] // profile['fps'] * stride + stride,
            'Unexpected commentary padding exceeds one source frame')
        insertion = converted + bytes(padding)
        require((output / f'{key}-insert.f32le').read_bytes() == insertion, 'Complete insertion or explicit silence changed')
        audio = (output / f'{key}.f32le').read_bytes()
        anchor = observed['at_sample'] * stride
        require(audio == context[:anchor] + insertion + context[anchor:], 'Source/AI/source PCM ordering changed')
        audio_grid(key, audio)
        card = ledger(output / f'{key}-card.framehash')
        require(len(card) == 1, 'Expected one converted card frame')
        actual = ledger(output / f'{key}.framehash')
        expected = reference[:observed['at_frame']] + card * observed['insert_frames'] + reference[observed['at_frame']:]
        require(len(actual) == len(expected) and all(row[:4] == (0, index * ticks, index * ticks, ticks)
            and row[4:] == expected[index][4:] for index, row in enumerate(actual)),
            'Complete output frames or exact presentation grid changed')
        results.append({'key': key, **observed, 'converted_commentary_samples': len(converted) // stride,
            'explicit_tail_silence_samples': padding // stride, 'native_source_frames_checked': len(rows),
            'original_video_frames_exact': len(reference), 'all_original_context_PCM_exact': True,
            'whole_commentary_conversion_retained': True, 'selected': False})
    return results


def render(plan, binding, output, descriptor):
    profile = plan['profile']
    base = [binding['tools']['ffmpeg']['path'], '-nostdin', '-hide_banner', '-v', 'error', '-n', '-threads', '4']
    seek = str(max(0, plan['context_frames'][0] // profile['fps'] - 3))
    source = ['-ss', seek, '-copyts', '-i', plan['source']['path']]
    ticks = int(1 / Fraction(profile['time_base'])) // profile['fps']
    start_pts, end_pts = [frame * ticks for frame in plan['context_frames']]
    rows, start, end = native_reference(plan)
    video_filter = f'trim=start_pts={start_pts}:end_pts={end_pts},setpts=PTS-STARTPTS'
    audio_filter = f'atrim=start_pts={start}:end_pts={end},asetpts=PTS-STARTPTS'
    video_options = ['-c:v', 'libx264', '-preset', 'ultrafast', '-qp', '0', '-threads:v', '4',
        '-pix_fmt', 'yuv420p', '-vsync', '0', '-video_track_timescale', str(int(1 / Fraction(profile['time_base'])))]
    audio_options = ['-c:a', 'pcm_f32le', '-ar', str(profile['sample_rate']), '-ac', str(profile['channels'])]
    def run(name, arguments, stdout=None):
        native_audio.run_process(base + arguments, output, name, stdout or f'{name}.stdout.log', descriptor)
    def decoded(name, path):
        run(name + '-video-decode', ['-i', str(path), '-map', '0:v:0', '-an', '-c:v', 'rawvideo',
            '-enc_time_base', profile['time_base'], '-vsync', '0', '-f', 'framehash', '-hash', 'sha256', '-'], name + '.framehash')
        run(name + '-audio-decode', ['-i', str(path), '-map', '0:a:0', '-vn', *audio_options,
            '-f', 'f32le', '-'], name + '.f32le')
        run(name + '-audio-grid', ['-i', str(path), '-map', '0:a:0', '-vn', *audio_options,
            '-f', 'framehash', '-hash', 'sha256', '-'], name + '.audio.framehash')
    native_audio.run_process([binding['tools']['ffprobe']['path'], '-v', 'error', '-show_streams',
        '-of', 'json', plan['source']['path']], output, 'source-probe', 'source-probe.json', descriptor)
    validate_probe(plan, load(output / 'source-probe.json'))
    run('native-reference', [*source, '-map', f'0:{profile["audio_stream"]}', '-vn', '-af',
        f'atrim=start_pts={rows[0][2]}:end_pts={rows[-1][2] + rows[-1][3]}', *audio_options,
        '-f', 'f32le', '-'], 'native-reference.f32le')
    run('source-reference', [*source, '-map', f'0:{profile["video_stream"]}', '-an', '-vf', video_filter,
        '-c:v', 'rawvideo', '-enc_time_base', profile['time_base'], '-vsync', '0', '-f', 'framehash',
        '-hash', 'sha256', '-'], 'source-reference.framehash')
    run('source-context-render', [*source, '-filter_complex',
        f'[0:{profile["video_stream"]}]{video_filter}[v];[0:{profile["audio_stream"]}]{audio_filter}[a]',
        '-map', '[v]', '-map', '[a]', *video_options, *audio_options, str(output / 'source-context.mov')])
    decoded('source-context', output / 'source-context.mov')
    for case in plan['cases']:
        key = case['key']
        observed = timing(plan, case)
        waveform = audition.wave_format(case['waveform']['path'])
        conversion = f'aresample={profile["sample_rate"]}:osf=flt'
        if waveform['channels'] == 1 and profile['channels'] == 2:
            conversion += ',pan=stereo|c0=c0|c1=c0'
        card_filter = f'pad={profile["width"]}:{profile["height"]}:(ow-iw)/2:(oh-ih)/2,format=yuv420p'
        run(key + '-conversion', ['-i', case['waveform']['path'], '-af', conversion,
            *audio_options, '-f', 'f32le', '-'], key + '-converted.f32le')
        run(key + '-card-reference', ['-i', case['card']['path'], '-vf', card_filter, '-frames:v', '1',
            '-c:v', 'rawvideo', '-f', 'framehash', '-hash', 'sha256', '-'], key + '-card.framehash')
        run(key + '-insert-render', ['-loop', '1', '-framerate', str(profile['fps']), '-i', case['card']['path'],
            '-i', case['waveform']['path'], '-map', '0:v:0', '-map', '1:a:0', '-vf',
            card_filter + f',trim=end_frame={observed["insert_frames"]},setpts=PTS-STARTPTS',
            '-af', conversion + f',apad=whole_len={observed["insert_samples"]}',
            *video_options, *audio_options, str(output / f'{key}-insert.mov')])
        run(key + '-insert-decode', ['-i', str(output / f'{key}-insert.mov'), '-map', '0:a:0', '-vn',
            *audio_options, '-f', 'f32le', '-'], key + '-insert.f32le')
        frame, sample = observed['at_frame'], observed['at_sample']
        graph = (f'[0:v]split[vb][va];[vb]trim=end_frame={frame},setpts=PTS-STARTPTS[v0];'
            f'[va]trim=start_frame={frame},setpts=PTS-STARTPTS[v2];'
            f'[0:a]asplit[ab][aa];[ab]atrim=end_sample={sample},asetpts=PTS-STARTPTS[a0];'
            f'[aa]atrim=start_sample={sample},asetpts=PTS-STARTPTS[a2];'
            '[v0][1:v][v2]concat=n=3:v=1:a=0[v];[a0][1:a][a2]concat=n=3:v=0:a=1[a]')
        run(key + '-context-render', ['-i', str(output / 'source-context.mov'), '-i', str(output / f'{key}-insert.mov'),
            '-filter_complex', graph, '-map', '[v]', '-map', '[a]', *video_options, *audio_options,
            str(output / f'{key}.mov')])
        decoded(key, output / f'{key}.mov')
    return comparisons(plan, output)


def execute(action, plan_path, output):
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    plan, binding = inputs(plan_path)
    require(output != ROOT and ROOT not in output.parents, 'Output must be outside the repository')
    bound = [plan_path, Path(plan['source']['path']), Path(plan['native_ledger']['path']),
        *[Path(record['path']) for record in plan['evidence']],
        *[Path(case[name]['path']) for case in plan['cases'] for name in ('card', 'waveform')]]
    require(all(output != path and output not in path.parents for path in bound), 'Output cannot contain an input')
    require(not any(parent.name.startswith('.zh_work_') for parent in (output, *output.parents)),
        'Contextual preview must not enter a canonical work directory')
    existing = output.exists()
    require(action == 'render' or existing, 'No existing contextual preview to check')
    if not existing:
        output.mkdir(parents=True, exist_ok=False)
    require(not existing or (output / 'preview.lock').is_file(), 'Unknown output directory')
    with (output / 'preview.lock').open('rb' if existing else 'xb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if existing:
            manifest = load(output / 'manifest.json')
            require(manifest.get('kind') == KIND and manifest.get('scope') == SCOPE
                and manifest.get('complete') is True and manifest.get('inputs') == binding
                and all(manifest.get(name) is False for name in audition.UNAPPROVED), 'Stale or incomplete contextual preview')
            require(set(path.name for path in output.iterdir()) == {'manifest.json', 'preview.lock'} | set(manifest['artifacts']),
                'Unexpected output inventory')
            require(all(record['path'] == str(output / name) and state.matches(record)
                for name, record in manifest['artifacts'].items()), 'Output identity changed or moved')
            require(comparisons(plan, output) == manifest['cases'], 'Contextual preview evidence changed')
        else:
            manifest = {'kind': KIND, 'scope': SCOPE, 'created_at': state.now(), 'inputs': binding,
                'complete': False, **{name: False for name in audition.UNAPPROVED}}
            prepare_videos.save_json(output / 'manifest.json', manifest)
            try:
                manifest['cases'] = render(plan, binding, output, lock.fileno())
                require(inputs(plan_path) == (plan, binding), 'Inputs changed during rendering')
                manifest['artifacts'] = {path.name: state.identity(path) for path in output.iterdir()
                    if path.name not in ('manifest.json', 'preview.lock')}
                manifest['complete'] = True
            except BaseException as error:
                manifest['error'] = f'{type(error).__name__}: {error}'
                raise
            finally:
                prepare_videos.save_json(output / 'manifest.json', manifest)
        require(inputs(plan_path) == (plan, binding), 'Inputs changed during final verification')
    return {'manifest': state.identity(output / 'manifest.json'), 'cases': manifest['cases'],
        'reused_without_media_commands': existing, **{name: False for name in audition.UNAPPROVED}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('render', 'check'))
    parser.add_argument('plan')
    parser.add_argument('--output', required=True)
    options = parser.parse_args()
    previous = {number: signal.signal(number, native_audio.interrupted) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        print(json.dumps(execute(options.action, options.plan, options.output), ensure_ascii=False, indent=2))
        return 0
    except native_audio.Interrupted as error:
        print(str(error), file=sys.stderr)
        return 128 + error.number
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 1
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == '__main__':
    raise SystemExit(main())
