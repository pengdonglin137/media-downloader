"""Render isolated, explicitly attributed commentary cards from existing full audio."""

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version

import dubbing_state as state
from media_workflow import commentary_preview
from media_workflow.speech_variants import require
from prepare_videos import save_json


KIND = 'ai-commentary-visual-preview-v2'
HISTORICAL_KIND = 'ai-commentary-visual-preview-v1'
EDITORIAL_KIND = 'ai-commentary-visual-editorial-v1'
WIDTH, HEIGHT, FPS, SAMPLE_RATE = 1280, 720, 25, 24000
STATUS = '隔离审片候选 · 未选胜 · 非已安装正片插段'
FOOTER = '字幕锚点不是已核验交接；未切原片，也未批准声音、发音或情感。'
MANIFEST_FIELDS = {'kind', 'editorial', 'adapter', 'audio_plan', 'audio_manifest', 'audio_check',
    'font', 'font_index', 'tools', 'pillow', 'complete', 'render_started', 'label', 'cases', 'reuse_source',
    *commentary_preview.UNAPPROVED}
CASE_FIELDS = {'key', 'review_case', 'alternatives_not_selected', 'card', 'layout', 'waveform',
    'pcm_sha256', 'timing', 'video_path', 'human_selected', 'complete'}
CLOSING_PUNCTUATION = '，。；：！？、,.;:!?)]}）》】」』”’'


def rational(value):
    value = Fraction(value)
    return {'numerator': value.numerator, 'denominator': value.denominator}


def candidate_timing(anchor, samples):
    require(isinstance(anchor, str) and re.fullmatch(r'\d+(?:\.\d+)?', anchor),
        'Use a finite nonnegative decimal source anchor')
    require(type(samples) is int and 0 < samples <= 40 * SAMPLE_RATE,
        'Expected a complete positive commentary of at most40 seconds')
    source = Fraction(anchor)
    audio = Fraction(samples, SAMPLE_RATE)
    frames = (samples * FPS + SAMPLE_RATE - 1) // SAMPLE_RATE
    video = Fraction(frames, FPS)
    return {'scope': 'one hypothetical insertion only, not a combined candidate film',
        'source_anchor': anchor, 'source_before_end': rational(source),
        'proposed_insert_start': rational(source), 'proposed_insert_end': rational(source + video),
        'proposed_source_resume': rational(source), 'source_after_shift': rational(video),
        'local_audio_end': rational(audio), 'local_video_end': rational(video),
        'video_tail_without_audio': rational(video - audio),
        'samples': samples, 'sample_rate': SAMPLE_RATE, 'video_frames': frames, 'fps': FPS,
        'anchor_authority': commentary_preview.ANCHOR_AUTHORITY,
        'source_cut_or_timeline_installed': False, 'source_speaker_or_boundary_verified': False}


def wrap_text(text, font, max_width):
    require(isinstance(text, str) and text and all(ord(character) >= 32 for character in text),
        'Use nonempty single-line authored display text')
    rows, current = [], ''
    for token in re.findall(r'[A-Za-z0-9_./+-]+|.', text):
        require(font.getlength(token) <= max_width, 'An unbroken display token exceeds the card width')
        if current and font.getlength(current + token) > max_width and token[0] not in CLOSING_PUNCTUATION:
            rows.append(current)
            current = token
        else:
            current += token
    if current:
        rows.append(current)
    if len(rows) > 1 and font.getlength(rows[-1]) < max_width / 4:
        tokens = re.findall(r'[A-Za-z0-9_./+-]+|.', rows[-2] + rows[-1])
        candidates = []
        for boundary in range(1, len(tokens)):
            left, right = ''.join(tokens[:boundary]), ''.join(tokens[boundary:])
            if right[0] not in CLOSING_PUNCTUATION and max(font.getlength(left), font.getlength(right)) <= max_width:
                candidates.append((abs(font.getlength(left) - font.getlength(right)), boundary, left, right))
        if candidates:
            _, _, left, right = min(candidates)
            rows[-2:] = [left, right]
    require(''.join(rows) == text, 'Display wrapping changed the authored text')
    return rows


def card_image(case, card, font_config):
    image = Image.new('RGB', (WIDTH, HEIGHT), '#111d2e')
    draw = ImageDraw.Draw(image)
    boxes = []

    def font(size):
        return ImageFont.truetype(font_config['path'], size, index=font_config['index'])

    def line(name, text, position, size, colour):
        selected = font(size)
        box = draw.textbbox(position, text, font=selected)
        require(0 <= box[0] < box[2] <= WIDTH and 0 <= box[1] < box[3] <= HEIGHT,
            f'Card text is outside the actual canvas: {name}')
        draw.text(position, text, font=selected, fill=colour)
        boxes.append({'name': name, 'text': text, 'box': list(box), 'font_size': size})

    line('attribution', commentary_preview.LABEL, (64, 32), 28, '#f5cc7a')
    line('status', STATUS, (64, 80), 20, '#a8bacb')
    require(font(44).getlength(card['heading']) <= 1152, 'Heading does not fit without truncation')
    line('heading', card['heading'], (64, 136), 44, '#ffffff')
    draw.line((64, 213, 1216, 213), fill='#365066', width=2)
    body = case['display'][len(commentary_preview.LABEL + '：'):]
    rows = wrap_text(body, font(34), 1152)
    require(len(rows) <= 6, 'Full commentary display requires more than six rows')
    for row, text in enumerate(rows):
        line(f'body-{row + 1}', text, (64, 236 + row * 50), 34, '#e1eaf2')
    cursor = 64
    for number, term in enumerate(card['terms'], 1):
        width = int(font(24).getlength(term)) + 32
        require(cursor + width <= 1216, 'Technical-term chips exceed the card width')
        draw.rounded_rectangle((cursor, 560, cursor + width, 605), radius=9, fill='#203b4a')
        line(f'term-{number}', term, (cursor + 16, 565), 24, '#83ded5')
        cursor += width + 14
    line('anchor', f'拟插入的原片字幕锚点 {case["source_anchor"]} 秒 · {case["review_case"]} · {case["key"]}',
        (64, 625), 18, '#a8bacb')
    line('limits', FOOTER, (64, 666), 18, '#a8bacb')
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue(), {'body': body, 'body_rows': rows, 'text_boxes': boxes,
        'width': WIDTH, 'height': HEIGHT, 'pillow': pillow_version}


def load_inputs(editorial_path):
    editorial_path = Path(editorial_path).resolve()
    editorial = state.load(editorial_path)
    require(isinstance(editorial, dict) and set(editorial) ==
        {'kind', 'bound_audio_plan', 'audio_previews', 'font', 'cards'}
        and editorial['kind'] == EDITORIAL_KIND, 'Unexpected visual editorial fields')
    for name in ['bound_audio_plan', 'audio_previews']:
        require(isinstance(editorial[name], str) and Path(editorial[name]).is_absolute()
            and str(Path(editorial[name]).resolve()) == editorial[name], 'Use canonical absolute input paths')
    audio_check = commentary_preview.check(editorial['bound_audio_plan'], editorial['audio_previews'])
    plan = state.load(editorial['bound_audio_plan'])
    manifest = state.load(Path(editorial['audio_previews']) / 'manifest.json')
    font_config = editorial['font']
    require(isinstance(font_config, dict) and set(font_config) == {'path', 'index'}
        and isinstance(font_config['path'], str) and Path(font_config['path']).is_absolute()
        and str(Path(font_config['path']).resolve()) == font_config['path']
        and type(font_config['index']) is int and 0 <= font_config['index'] <= 32,
        'Bind one explicit font collection face')
    selected = ImageFont.truetype(font_config['path'], 34, index=font_config['index'])
    require('CJK SC' in selected.getname()[0], 'Select the Simplified Chinese CJK face explicitly')
    require(isinstance(editorial['cards'], list) and len(editorial['cards']) == len(plan['cases']),
        'Render all existing candidates without silently selecting a winner')
    for card, case in zip(editorial['cards'], plan['cases']):
        require(isinstance(card, dict) and set(card) == {'key', 'heading', 'terms'}
            and card['key'] == case['key'], 'Preserve the complete candidate inventory and order')
        require(isinstance(card['heading'], str) and 0 < len(card['heading']) <= 40
            and all(ord(character) >= 32 for character in card['heading']), 'Invalid full card heading')
        terms = card['terms']
        require(isinstance(terms, list) and 0 < len(terms) <= 5
            and all(isinstance(term, str) and 0 < len(term) <= 24 and term in case['display'] for term in terms)
            and len(terms) == len(set(terms)), 'Use distinct technical terms already present in the complete display')
        card_image(case, card, font_config)
    return editorial, plan, manifest, audio_check


def tools():
    result = {}
    for name in ['ffmpeg', 'ffprobe']:
        path = shutil.which(name)
        require(path is not None, f'Missing existing local tool: {name}')
        result[name] = state.identity(Path(path).resolve())
    return result


def historical_source(manifest):
    source = manifest['reuse_source']
    if source is None:
        return None
    require(isinstance(source, dict) and set(source) == {'manifest', 'adapter_archive'}
        and state.matches(source['manifest']) and state.matches(source['adapter_archive']),
        'Historical manifest or explicitly archived renderer source changed')
    previous = state.load(source['manifest']['path'])
    require(previous['kind'] == HISTORICAL_KIND and previous['complete'] is True
        and previous['render_started'] is True and previous['label'] == commentary_preview.LABEL
        and all(previous[name] is False for name in commentary_preview.UNAPPROVED),
        'Reuse requires complete unapproved historical visual candidates')
    require(all(previous['adapter'][name] == source['adapter_archive'][name] for name in ['bytes', 'sha256'])
        and all(previous[name] == manifest[name] for name in ['audio_plan', 'audio_manifest', 'audio_check',
            'font', 'font_index', 'tools', 'pillow']), 'Historical source, full audio or rendering environment differs')
    return previous


def verify_reuse(current, previous):
    require(previous is not None and 'encode' not in current, 'Reused bytes are not a new encoder invocation')
    matched = [case for case in previous['cases'] if case['key'] == current['key']]
    require(len(matched) == 1, 'Historical candidate must have one exact key')
    original = matched[0]
    require(current['reuse'] == {'key': original['key'], 'video': original['video']}
        and original['complete'] is True and current['complete'] is True and original['human_selected'] is False
        and all(current[name] == original[name] for name in ['review_case', 'alternatives_not_selected',
            'layout', 'waveform', 'pcm_sha256', 'timing']) and state.matches(original['video'])
        and state.matches(original['card']) and state.matches(current['video'])
        and current['video']['path'] == current['video_path']
        and Path(original['card']['path']).read_bytes() == Path(current['card']['path']).read_bytes()
        and all(current['video'][name] == original['video'][name] for name in ['bytes', 'sha256']),
        'Reused candidate pixels, complete audio, timing or video bytes differ')


def prepare(editorial_path, output, reuse_manifest=None, reuse_adapter=None):
    editorial_path, output = Path(editorial_path).resolve(), Path(output).resolve()
    editorial, plan, audio, audio_check = load_inputs(editorial_path)
    for protected in [Path(plan['work']), Path(editorial['audio_previews']), editorial_path.parent]:
        require(not output.is_relative_to(protected) and not protected.is_relative_to(output),
            'Use an independent output directory, not an input tree or its parent')
    require(not output.exists(), 'Preserve existing complete or partial visual previews')
    require((reuse_manifest is None) == (reuse_adapter is None), 'Bind both the historical manifest and renderer archive')
    commands = tools()
    output.mkdir(parents=True)
    manifest = {'kind': KIND, 'editorial': state.identity(editorial_path), 'adapter': state.identity(__file__),
        'audio_plan': state.identity(editorial['bound_audio_plan']),
        'audio_manifest': state.identity(Path(editorial['audio_previews']) / 'manifest.json'),
        'audio_check': audio_check, 'font': state.identity(editorial['font']['path']),
        'font_index': editorial['font']['index'], 'tools': commands, 'pillow': pillow_version,
        'complete': False, 'render_started': False, 'label': commentary_preview.LABEL,
        'reuse_source': {'manifest': state.identity(reuse_manifest), 'adapter_archive': state.identity(reuse_adapter)}
            if reuse_manifest is not None else None,
        'cases': [], **{name: False for name in commentary_preview.UNAPPROVED}}
    previous = historical_source(manifest)
    families = {}
    for case in plan['cases']:
        families.setdefault(case['review_case'], []).append(case['key'])
    for case, card, observed in zip(plan['cases'], editorial['cards'], audio['cases']):
        timing = candidate_timing(case['source_anchor'], observed['samples'])
        image, layout = card_image(case, card, editorial['font'])
        path = output / f'{case["key"]}.png'
        path.write_bytes(image)
        current = {'key': case['key'], 'review_case': case['review_case'],
            'alternatives_not_selected': families[case['review_case']], 'card': state.identity(path),
            'layout': layout, 'waveform': observed['waveform'], 'pcm_sha256': observed['pcm_sha256'],
            'timing': timing, 'video_path': str(output / f'{case["key"]}.mkv'),
            'human_selected': False, 'complete': False}
        if previous is not None:
            matched = [item for item in previous['cases'] if item['key'] == case['key']]
            require(len(matched) == 1, 'Historical inventory missing a candidate')
            original = matched[0]
            require(state.matches(original['card']) and state.matches(original['video']), 'Historical output changed')
            if Path(original['card']['path']).read_bytes() == image and original['layout'] == layout:
                shutil.copy2(original['video']['path'], current['video_path'])
                current.update({'reuse': {'key': case['key'], 'video': original['video']},
                    'video': state.identity(current['video_path']), 'complete': True})
                verify_reuse(current, previous)
                current['inspection'] = inspect_video(current, commands)
        manifest['cases'].append(current)
    save_json(output / 'manifest.json', manifest)
    return {'kind': KIND, 'manifest': state.identity(output / 'manifest.json'), 'prepared': len(manifest['cases']),
        'explicitly_reused': sum('reuse' in current for current in manifest['cases'])}


def validate_prepared(output):
    output = Path(output).resolve()
    manifest = state.load(output / 'manifest.json')
    require(isinstance(manifest, dict) and set(manifest) == MANIFEST_FIELDS
        and manifest['kind'] == KIND and state.matches(manifest['adapter'])
        and manifest['adapter'] == state.identity(__file__) and state.matches(manifest['editorial'])
        and manifest['pillow'] == pillow_version, 'Visual implementation or authored input changed')
    editorial, plan, audio, audio_check = load_inputs(manifest['editorial']['path'])
    require(manifest['audio_plan'] == state.identity(editorial['bound_audio_plan'])
        and manifest['audio_manifest'] == state.identity(Path(editorial['audio_previews']) / 'manifest.json')
        and manifest['audio_check'] == audio_check and manifest['font'] == state.identity(editorial['font']['path'])
        and manifest['font_index'] == editorial['font']['index'] and manifest['tools'] == tools(),
        'Bound audio, font or encoder tools changed')
    require(manifest['label'] == commentary_preview.LABEL
        and all(manifest[name] is False for name in commentary_preview.UNAPPROVED), 'Visual preview authority expanded')
    require(len(manifest['cases']) == len(plan['cases']), 'Visual candidate inventory changed')
    previous = historical_source(manifest)
    for case, card, observed, current in zip(plan['cases'], editorial['cards'], audio['cases'], manifest['cases']):
        require(isinstance(current, dict) and CASE_FIELDS <= current.keys()
            and current.keys() <= CASE_FIELDS | {'encode', 'inspection', 'video', 'error', 'reuse'}
            and type(current['complete']) is bool, 'Unexpected visual candidate fields')
        image, layout = card_image(case, card, editorial['font'])
        alternatives = [item['key'] for item in plan['cases'] if item['review_case'] == case['review_case']]
        require(current['key'] == case['key'] and current['review_case'] == case['review_case']
            and current['alternatives_not_selected'] == alternatives and current['human_selected'] is False
            and current['timing'] == candidate_timing(case['source_anchor'], observed['samples'])
            and current['waveform'] == observed['waveform'] and current['pcm_sha256'] == observed['pcm_sha256'],
            'Candidate pairing, complete audio or hypothetical timing changed')
        require(Path(current['card']['path']) == output / f'{case["key"]}.png'
            and state.matches(current['card']) and Path(current['card']['path']).read_bytes() == image
            and current['layout'] == layout and current['video_path'] == str(output / f'{case["key"]}.mkv'),
            'Card pixels, full display, layout or output route changed')
        if 'reuse' in current:
            verify_reuse(current, previous)
    return manifest


def encode_command(current, commands):
    frames = current['timing']['video_frames']
    duration = f'{frames // FPS}.{frames % FPS * (1000 // FPS):03d}'
    return [commands['ffmpeg']['path'], '-nostdin', '-v', 'warning', '-loop', '1', '-framerate', str(FPS),
        '-t', duration, '-i', current['card']['path'], '-i', current['waveform']['path'],
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264rgb', '-preset', 'veryfast', '-crf', '0',
        '-threads', '2', '-pix_fmt', 'rgb24', '-c:a', 'flac', '-compression_level', '5',
        '-metadata', 'title=AI补充说明（非讲者原话）·隔离候选', '-n', current['video_path']]


def inspect_video(current, commands):
    probe = subprocess.run([commands['ffprobe']['path'], '-v', 'error',
        '-show_streams', '-show_format', '-of', 'json', current['video_path']], capture_output=True, timeout=120)
    require(probe.returncode == 0, f'Video probe/decode failed: {probe.stderr.decode(errors="replace")}')
    information = json.loads(probe.stdout)
    require(len(information['streams']) == 2, 'Expected exactly one video and one commentary audio stream')
    video, audio = information['streams']
    require(video['codec_type'] == 'video' and video['codec_name'] == 'h264'
        and video['width'] == WIDTH and video['height'] == HEIGHT and video['pix_fmt'] == 'gbrp'
        and video['avg_frame_rate'] == f'{FPS}/1', 'Unexpected lossless RGB card video')
    require(audio['codec_type'] == 'audio' and audio['codec_name'] == 'flac'
        and audio['sample_rate'] == str(SAMPLE_RATE) and audio['channels'] == 1, 'Unexpected lossless commentary audio')
    decoded = subprocess.run([commands['ffmpeg']['path'], '-nostdin', '-v', 'error', '-i', current['video_path'],
        '-map', '0:a:0', '-c:a', 'pcm_s16le', '-f', 's16le', '-'], capture_output=True, timeout=120)
    require(decoded.returncode == 0 and len(decoded.stdout) == current['timing']['samples'] * 2
        and hashlib.sha256(decoded.stdout).hexdigest() == current['pcm_sha256'],
        'Encoded preview audio is not the complete unchanged candidate PCM')
    frames = subprocess.run([commands['ffmpeg']['path'], '-nostdin', '-v', 'error', '-threads', '2',
        '-i', current['video_path'], '-map', '0:v:0', '-pix_fmt', 'rgb24', '-vsync', '0',
        '-f', 'framehash', '-hash', 'sha256', '-'], capture_output=True, timeout=120)
    require(frames.returncode == 0,
        f'Complete visual frame decode failed: {frames.stderr.decode(errors="replace")}')
    rows = [row for row in frames.stdout.decode().splitlines() if row and not row.startswith('#')]
    require(len(rows) == current['timing']['video_frames'], 'Complete video frame count changed')
    with Image.open(current['card']['path']) as image:
        pixel_digest = hashlib.sha256(image.convert('RGB').tobytes()).hexdigest()
    for number, row in enumerate(rows):
        stream, decode_time, presentation_time, duration, size, digest = [field.strip() for field in row.split(',')]
        require(int(stream) == 0 and int(decode_time) == int(presentation_time) == number
            and int(duration) == 1 and int(size) == WIDTH * HEIGHT * 3 and digest == pixel_digest,
            'A decoded frame differs from the complete attributed card or frame schedule')
    return {'video_frames': len(rows), 'audio_samples': len(decoded.stdout) // 2,
        'audio_pcm_sha256': hashlib.sha256(decoded.stdout).hexdigest(),
        'all_decoded_rgb_frames_equal_card': True, 'card_rgb_sha256': pixel_digest,
        'framehash_actual_exit': frames.returncode,
        'framehash_stdout_sha256': hashlib.sha256(frames.stdout).hexdigest(),
        'framehash_stderr': frames.stderr.decode(errors='replace'),
        'probe_actual_exit': probe.returncode, 'audio_decode_actual_exit': decoded.returncode,
        'probe_stderr': probe.stderr.decode(errors='replace'), 'decode_stderr': decoded.stderr.decode(errors='replace')}


def render(output):
    output = Path(output).resolve()
    manifest = validate_prepared(output)
    require(manifest['render_started'] is False and manifest['complete'] is False
        and all((current['complete'] is True and 'reuse' in current) or
            (current['complete'] is False and 'reuse' not in current and not Path(current['video_path']).exists())
            for current in manifest['cases']), 'Do not restart successful or partially executed renders')
    manifest['render_started'] = True
    save_json(output / 'manifest.json', manifest)
    for current in manifest['cases']:
        if 'reuse' in current:
            continue
        command = encode_command(current, manifest['tools'])
        current['encode'] = {'command': command, 'actual_exit': None}
        save_json(output / 'manifest.json', manifest)
        try:
            result = subprocess.run(command, capture_output=True, timeout=180)
            current['encode'].update({'actual_exit': result.returncode,
                'stdout': result.stdout.decode(errors='replace'), 'stderr': result.stderr.decode(errors='replace')})
            save_json(output / 'manifest.json', manifest)
            require(result.returncode == 0, f'Card encode failed: {current["key"]}')
            current['inspection'] = inspect_video(current, manifest['tools'])
            current['video'] = state.identity(current['video_path'])
            current['complete'] = True
        except Exception as error:
            current['error'] = str(error)
            save_json(output / 'manifest.json', manifest)
            raise
        save_json(output / 'manifest.json', manifest)
    manifest['complete'] = True
    save_json(output / 'manifest.json', manifest)
    return {'kind': KIND, 'manifest': state.identity(output / 'manifest.json'), 'candidates': len(manifest['cases']),
        'new_encoder_invocations': sum('encode' in current for current in manifest['cases']),
        'explicitly_reused_without_encoding': sum('reuse' in current for current in manifest['cases'])}


def check(output):
    manifest = validate_prepared(output)
    require(manifest['complete'] is True and manifest['render_started'] is True,
        'Expected a completed visual preview, not a prepared or partial plan')
    for current in manifest['cases']:
        require(current['complete'] is True and state.matches(current['video'])
            and current['video']['path'] == current['video_path']
            and ('reuse' in current or (current['encode']['command'] == encode_command(current, manifest['tools'])
                and current['encode']['actual_exit'] == 0))
            and current['inspection'] == inspect_video(current, manifest['tools']),
            'Incomplete, changed or unverified visual candidate')
    return {'kind': KIND, 'manifest': state.identity(Path(output) / 'manifest.json'),
        'candidates_checked': len(manifest['cases']), 'complete_audio_pcm_preserved': True,
        'explicitly_reused_without_encoding': sum('reuse' in current for current in manifest['cases']),
        'complete_video_frames_decoded': True, **{name: False for name in commentary_preview.UNAPPROVED}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('editorial')
    prepare_parser.add_argument('--output', required=True)
    prepare_parser.add_argument('--reuse-manifest')
    prepare_parser.add_argument('--reuse-adapter')
    for name in ['render', 'check']:
        command = commands.add_parser(name)
        command.add_argument('output')
    arguments = parser.parse_args()
    result = prepare(arguments.editorial, arguments.output, arguments.reuse_manifest, arguments.reuse_adapter) if arguments.command == 'prepare' else (
        render(arguments.output) if arguments.command == 'render' else check(arguments.output))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
