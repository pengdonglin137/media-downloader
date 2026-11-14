"""Produce source-corrected media without claiming the old English stream was copied."""

import argparse
import copy
import fcntl
import json
import wave
from contextlib import contextmanager
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import numpy as np

import caption_boundary_master
import check_videos
import delivery_quality
import dubbing_state as state
import speech_groups
import video_package
from prepare_videos import save_json
from . import editorial_media as media, source_caption_projection as projection
from .speech_variants import require


KIND = 'source-corrected-complete-utterance-media-v1'
SCOPE = 'isolated-source-corrected-media-pair-no-installation'
GEOMETRY = ('intro_audio', 'font', 'intro_frames', 'body_offset_seconds', 'body_duration_seconds',
    'duration_seconds', 'chapters', 'audio_tracks')


def implementations():
    return {'media': state.identity(media.__file__), 'projection': state.identity(projection.__file__),
        **media.implementations()}


def configuration(path):
    config = state.load(path)
    require(set(config) == {'kind', 'scope', 'media_config', 'projection_config', 'projection_manifest'}
        and config['kind'] == KIND and config['scope'] == SCOPE, 'Unsupported source-corrected media scope')
    for name in ('media_config', 'projection_config', 'projection_manifest'):
        require(state.matches(config[name]), f'Stale source-corrected media input: {name}')
    require(Path(config['projection_manifest']['path']).name == 'manifest.json', 'Wrong source-aware projection manifest route')
    inner = state.load(config['media_config']['path'])
    require(inner['kind'] == media.KIND and inner['scope'] == 'isolated-media-pair-no-installation',
        'Shared preparation must remain an isolated media operation')
    quality = state.load(inner['preflight_config']['path'])
    captions = projection.configuration(config['projection_config']['path'])
    require(quality['projection_config'] == config['projection_config']
        and quality['projection_manifest'] == config['projection_manifest']
        and quality['stage_config'] == captions['stage_config'] and quality['stage_manifest'] == captions['stage_manifest'],
        'Media, current-audio quality and source-aware captions have different authorities')
    manifest = state.load(config['projection_manifest']['path'])
    require(manifest['config'] == config['projection_config'] and manifest['complete'] is True
        and manifest['english_track_bytes_unchanged'] is False, 'Require explicit complete English correction authority')
    return config


def projection_context(config):
    return projection.runtime(config['projection_config']['path'], Path(config['projection_manifest']['path']).parent)


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    require(not output.exists(), 'Preserve successful or interrupted source-corrected media preparation')
    with projection_context(config):
        media.prepare(config['media_config']['path'], output)
    marker = {'kind': KIND, 'config': state.identity(config_path), 'adapter': state.identity(__file__),
        'implementations': implementations(), 'preparation': state.identity(output / 'preparation.json'),
        'preparation_adapter': state.identity(media.__file__), 'projection_config': config['projection_config'],
        'projection_manifest': config['projection_manifest'], 'English_stream_copied': False,
        'canonical_media_modified': False, 'installed': False, 'human_listened': False,
        'pronunciation_or_emotional_approval': False}
    save_json(output / 'source-corrected-media.json', marker)
    return check_preparation(config_path, output)


def check_preparation(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    marker = state.load(output / 'source-corrected-media.json')
    require(marker['kind'] == KIND and marker['config'] == state.identity(config_path)
        and marker['adapter'] == state.identity(__file__) and marker['implementations'] == implementations()
        and marker['preparation'] == state.identity(output / 'preparation.json')
        and marker['preparation_adapter'] == state.identity(media.__file__)
        and marker['projection_config'] == config['projection_config']
        and marker['projection_manifest'] == config['projection_manifest']
        and all(marker[name] is False for name in ('English_stream_copied', 'canonical_media_modified',
            'installed', 'human_listened', 'pronunciation_or_emotional_approval')),
        'Wrong, stale or overclaiming source-corrected media preparation')
    with projection_context(config):
        result = media.check_preparation(config['media_config']['path'], output)
    return {**result, 'source_corrections': config['projection_manifest'], 'English_stream_copied': False}


def validate_package(work, allow_implementation_change=False):
    work = Path(work).resolve()
    marker = state.load(work.parent / 'source-corrected-media.json')
    config = configuration(marker['config']['path'])
    require(marker['adapter'] == state.identity(__file__) and marker['implementations'] == implementations()
        and marker['config'] == state.identity(marker['config']['path']), 'Stale source-corrected producer')
    record = media.derivation(work)
    rendering = state.load(work / 'render_state.json')
    require(state.matches(rendering['package_manifest']), 'Stale source-corrected package manifest')
    package = state.load(rendering['package_manifest']['path'])
    require(package['kind'] == KIND and package['producer'] == state.identity(__file__)
        and package['implementation_sha256'] == state.digest(__file__)
        and package['source_corrected_media'] == state.identity(work.parent / 'source-corrected-media.json')
        and package['projection_manifest'] == config['projection_manifest']
        and package['media_source'] == state.identity(work / 'editorial-media-source.json')
        and package['English_stream_copied'] is False and package['candidate_only'] is True
        and package['canonical_media_modified'] is False and package['human_listened'] is False
        and package['pronunciation_or_emotional_approval'] is False
        and all(state.matches(package[name]) for name in ('body', 'output', 'upload_output', 'burned_subtitles')),
        'Package must bind the actual source-corrected producer and outputs')
    baseline = state.load(state.load(record['stage_contract']['path'])['baseline_evidence']['package.json']['archive']['path'])
    require(all(package[name] == baseline[name] for name in GEOMETRY), 'Historical intro, chapters or geometry changed')
    require(package['subtitles'] == {language: record['captions'][f'{language}.srt']
        for language in video_package.LANGUAGES}, 'Source-corrected package captions differ from projection')
    return media.FROZEN_PACKAGE(work, allow_implementation_change=True)


@contextmanager
def runtime(work):
    work = Path(work).resolve()
    marker = state.load(work.parent / 'source-corrected-media.json')
    check_preparation(marker['config']['path'], work.parent)
    config = configuration(marker['config']['path'])
    require(state.validate_narration is media.FROZEN_NARRATION
        and speech_groups.quality_blockers is media.FROZEN_BLOCKERS and video_package.validate is media.FROZEN_PACKAGE,
        'Reject nested or foreign source-corrected production runtimes')
    with projection_context(config):
        media.upstream(config['media_config']['path'])
        media.validate_narration(work)
        state.validate_narration = lambda selected: media.validate_narration(work) \
            if Path(selected).resolve() == work else media.FROZEN_NARRATION(selected)
        speech_groups.quality_blockers = lambda selected, report, database: media.scoped_blockers(work, report, database) \
            if Path(selected).resolve() == work else media.FROZEN_BLOCKERS(selected, report, database)
        video_package.validate = lambda selected, allow_implementation_change=False: validate_package(work) \
            if Path(selected).resolve() == work else media.FROZEN_PACKAGE(selected, allow_implementation_change)
        try:
            yield
        finally:
            state.validate_narration = media.FROZEN_NARRATION
            speech_groups.quality_blockers = media.FROZEN_BLOCKERS
            video_package.validate = media.FROZEN_PACKAGE


def soft_commands(record, work, output, master, baseline, waveform):
    donors, captions = record['donors'], record['captions']
    common = ['ffmpeg', '-nostdin', '-n', '-v', 'error']
    mappings = ['-map_metadata', '0', '-map_chapters', '0', '-c:v', 'copy', '-c:a:0', 'aac', '-b:a:0', '128k',
        '-c:a:1', 'copy', '-c:s:0', 'mov_text', '-c:s:1', 'mov_text', '-c:s:2', 'mov_text',
        '-metadata:s:a:0', 'language=chi', '-metadata:s:a:1', 'language=eng', '-disposition:a:0', 'default',
        '-disposition:a:1', '0', '-metadata:s:s:0', 'language=chi', '-metadata:s:s:1', 'language=eng',
        '-metadata:s:s:2', 'language=chi']
    body = [*common, '-i', donors['body']['path'], '-i', waveform, '-i', str(work / 'zh_display.srt'),
        '-i', str(work / 'bilingual_display.srt'), '-i', str(work / 'en_display.srt'),
        '-map', '0:v:0', '-map', '1:a:0', '-map', '0:a:1', '-map', '2:s:0', '-map', '4:s:0', '-map', '3:s:0',
        *mappings, '-t', str(baseline['body_duration_seconds']), '-movflags', '+faststart', str(output / 'body.mp4')]
    offset = baseline['body_offset_seconds']
    filters = f'[2:a:0]apad,atrim=end={offset:.9f},asetpts=PTS-STARTPTS,aresample={media.RATE},aformat=channel_layouts=mono[head];'
    filters += f'[1:a:0]asetpts=PTS-STARTPTS,aresample={media.RATE},aformat=channel_layouts=mono[body];'
    filters += '[head][body]concat=n=2:v=0:a=1[chinese]'
    full = [*common, '-i', donors['master']['path'], '-i', waveform, '-i', donors['intro']['path'],
        '-i', captions['zh.srt']['path'], '-i', captions['bilingual.srt']['path'], '-i', captions['en.srt']['path'],
        '-filter_complex', filters, '-map', '0:v:0', '-map', '[chinese]', '-map', '0:a:1',
        '-map', '3:s:0', '-map', '5:s:0', '-map', '4:s:0', *mappings,
        '-metadata:s:a:0', 'title=中文配音（含AI导读）', '-disposition:s:0', '0', '-disposition:s:1', '0',
        '-disposition:s:2', '0', '-t', str(baseline['duration_seconds']), '-movflags', '+faststart', str(master)]
    return {'body': body, 'master': full}


def upload_command(master, upload, duration, threads, time_base, progress):
    timebase = Fraction(time_base)
    require(timebase.numerator == 1 and timebase.denominator <= 1000000, 'Unsupported exact source video timebase')
    require(type(threads) is int and 1 <= threads <= 16, 'Invalid bounded encoder threads')
    return ['ffmpeg', '-nostdin', '-n', '-v', 'error', '-i', str(master), '-map', '0:v:0', '-map', '0:a:0',
        '-map_metadata', '0', '-map_chapters', '0', '-sn', '-vf', 'subtitles=zh.ass', '-c:v', 'libx264',
        '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-threads', str(threads), '-filter_threads', '2',
        '-vsync', '0', '-enc_time_base', '-1', '-video_track_timescale', str(timebase.denominator), '-c:a', 'copy',
        '-metadata:s:a:0', 'language=chi', '-metadata:s:a:0', 'title=中文配音', '-disposition:a:0', 'default',
        '-t', str(duration), '-progress', str(progress), '-stats_period', '5', '-movflags', '+faststart', str(upload)]


def encode(config_path, output):
    check_preparation(config_path, output)
    output, work, master, upload = media.paths(output)
    record = media.derivation(work)
    config = configuration(config_path)
    inner = state.load(config['media_config']['path'])
    baseline = state.load(state.load(record['stage_contract']['path'])['baseline_evidence']['package.json']['archive']['path'])
    waveform = media.validate_narration(work)['waveform']['path']
    inputs = {**media.media_command_inputs(work), 'source_corrected_producer': state.identity(__file__),
        'source_corrected_media': state.identity(output / 'source-corrected-media.json')}
    commands = soft_commands(record, work, output, master, baseline, waveform)
    for name, destination in (('body', output / 'body.mp4'), ('master', master)):
        media.operation(output, name, commands[name], inputs, (destination,))
    video = next(track for track in video_package.probe(master)['streams'] if track['codec_type'] == 'video')
    media.operation(output, 'upload', upload_command(master, upload, baseline['duration_seconds'], inner['threads'],
        video['time_base'], output / 'upload-progress.log'), {**inputs, 'master': state.identity(master)}, (upload,))
    manifest_path = output / 'package.json'
    if not manifest_path.exists():
        package = {name: copy.deepcopy(baseline[name]) for name in GEOMETRY}
        package.update(kind=KIND, producer=state.identity(__file__), implementation_sha256=state.digest(__file__),
            preparation_producer=state.identity(media.__file__), source_corrected_media=state.identity(output / 'source-corrected-media.json'),
            projection_manifest=config['projection_manifest'], English_stream_copied=False,
            media_source=state.identity(work / 'editorial-media-source.json'), plan=state.identity(work / 'package-plan.json'),
            dependencies=state.render_dependencies(work, Path(waveform), state.source_video(work)),
            body=state.identity(output / 'body.mp4'), output=state.identity(master), upload_output=state.identity(upload),
            subtitles={language: record['captions'][f'{language}.srt'] for language in video_package.LANGUAGES},
            burned_subtitles=record['captions']['zh.ass'], candidate_only=True, canonical_media_modified=False,
            created_at=state.now(), human_listened=False, pronunciation_or_emotional_approval=False)
        save_json(manifest_path, package)
        state.bind_render(work, master, Path(waveform), state.source_video(work),
            {'subs': 'soft', 'sub_pos': 'bottom', 'video_reused': True, 'preserve_video': True}, origin=KIND)
        rendering = state.load(work / 'render_state.json')
        rendering['package_manifest'] = state.identity(manifest_path)
        save_json(work / 'render_state.json', rendering)
    with runtime(work):
        package = validate_package(work)
        quality = delivery_quality.require(work, output / 'speech-review.sqlite3')
    require(quality['bindings']['inputs']['render_state.json'] == state.digest(work / 'render_state.json'),
        'Current source-corrected quality lacks actual render binding')
    if not (output / 'render-quality.json').exists():
        save_json(output / 'render-quality.json', quality)
    return {'encoded': True, 'master': package['output'], 'upload': package['upload_output'],
        'English_stream_copied': False, 'quality_state': quality['state'], 'installed': False, 'human_listened': False}


def compare_english(expected, actual):
    require(len(actual) == len(expected) and all(current[2] == wanted[2]
        and max(abs(current[0] - wanted[0]), abs(current[1] - wanted[1])) <= .002
        for current, wanted in zip(actual, expected)), 'Actual English soft subtitle content or timestamps differ from explicit source projection')


def verify_english(output, name, path, expected):
    extracted = output / f'{name}-English.srt'
    receipt = media.operation(output, f'extract-{name}-English', ['ffmpeg', '-nostdin', '-n', '-v', 'error',
        '-i', str(path), '-map', '0:s:1', '-f', 'srt', str(extracted)], {'media': state.identity(path), 'expected': expected}, (extracted,))
    before = check_videos.subtitles(Path(expected['path']).read_text())
    actual = check_videos.subtitles(extracted.read_text())
    compare_english(before, actual)
    return {'expected': expected, 'extracted': state.identity(extracted), 'cue_count': len(actual),
        'all_source_words_and_times_match': True, 'maximum_container_rounding_seconds': .002,
        'old_English_packet_identity_claimed': False, 'operation': receipt}


def verify(config_path, output):
    check_preparation(config_path, output)
    output, work, master, upload = media.paths(output)
    require(not (output / 'verification.json').exists(), 'Check successful verification, never repeat complete decodes')
    record = media.derivation(work)
    with runtime(work):
        package = validate_package(work)
        structures = {'master': video_package.verify_media(master, package),
            'upload': video_package.verify_media(upload, package, upload=True)}
    preserved = {}
    for name, before, after in (('body', record['donors']['body'], package['body']),
            ('master', record['donors']['master'], package['output'])):
        previous = caption_boundary_master.packet_inventory(Path(before['path']), [0, 2])
        current = caption_boundary_master.packet_inventory(Path(after['path']), [0, 2])
        require(previous == current, f'Copied video or original-English audio packets changed: {name}')
        old_streams, new_streams = video_package.probe(before['path'])['streams'], video_package.probe(after['path'])['streams']
        for index in (0, 2):
            for field in ('time_base', 'codec_name', 'profile', 'sample_rate', 'channels', 'width', 'height', 'pix_fmt'):
                require(old_streams[index].get(field) == new_streams[index].get(field), 'Copied stream configuration changed')
        preserved[name] = current
    english = {'body': verify_english(output, 'body', Path(package['body']['path']), record['files']['en_display.srt']),
        'master': verify_english(output, 'master', master, package['subtitles']['en'])}
    chinese = [caption_boundary_master.packet_inventory(path, [1]) for path in (master, upload)]
    require(chinese[0] == chinese[1], 'Master/upload Chinese AAC packets or schedules differ')
    schedules = [media.frame_schedule(path) for path in (master, upload)]
    require(schedules[0] == schedules[1], 'Burned upload changed decoded video presentation schedule')
    decoded = []
    for name, path in (('master', master), ('upload', upload)):
        pcm = output / f'{name}-chinese.s16le'
        media.operation(output, f'decode-{name}-chinese', ['ffmpeg', '-nostdin', '-n', '-v', 'error', '-i', str(path),
            '-map', '0:a:0', '-ac', '1', '-ar', str(media.RATE), '-f', 's16le', str(pcm)], {'media': state.identity(path)}, (pcm,))
        decoded.append(pcm)
    require(state.digest(decoded[0]) == state.digest(decoded[1]), 'Master/upload decoded Chinese PCM differs')
    actual = np.memmap(decoded[0], dtype='<i2', mode='r')
    narration = media.validate_narration(work)
    with wave.open(narration['waveform']['path'], 'rb') as audio:
        expected = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')
    offset = Decimal(str(package['body_offset_seconds'])) * media.RATE
    require(offset == offset.to_integral_value(), 'Nonintegral real intro sample offset')
    offset = int(offset)
    contract = state.load(record['stage_contract']['path'])
    diagnostics = []
    for name, windows in (('editorial', contract['cases']), ('source', contract['original_source_ranges'])):
        for number, window in enumerate(windows):
            first, last = window['start_sample'], window.get('actual_source_end_sample', window['end_sample'])
            diagnostics.append(media.signal_diagnostics(expected[first:last], actual[offset + first:offset + last],
                f'{name}-{window.get("unit_id", number + 1)}'))
    full_decode = {}
    for name, path in (('master', master), ('upload', upload)):
        full_decode[name] = media.operation(output, f'full-{name}-decode', ['ffmpeg', '-nostdin', '-v', 'error', '-xerror',
            '-threads', '4', '-i', str(path), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'], {'media': state.identity(path)})
        require(full_decode[name]['log']['bytes'] == 0, 'Full decoder emitted errors')
    with runtime(work):
        release = delivery_quality.release(work, output / 'speech-review.sqlite3')
    proof = {'passed': True, 'adapter': state.identity(__file__), 'checked_at': state.now(),
        'preparation': state.identity(output / 'preparation.json'), 'source_corrected_media': state.identity(output / 'source-corrected-media.json'),
        'package': state.identity(output / 'package.json'), 'render': state.identity(work / 'render_state.json'),
        'release': state.identity(work / 'release.json'), 'structures': structures, 'preserved_video_original_audio_packets': preserved,
        'English_subtitles': english, 'English_stream_copied': False, 'chinese_aac_packets': chinese[0],
        'master_upload_chinese_pcm_equal': True, 'decoded_pcm': [state.identity(path) for path in decoded],
        'complete_decoded_video_schedule': schedules[0], 'fixed_offset_audio_diagnostics': diagnostics,
        'full_decode': full_decode, 'quality_state': release['quality']['state'], 'canonical_media_modified': False,
        'installed': False, 'human_listened': False, 'pronunciation_or_emotional_approval': False,
        'full_visual_or_wordlevel_approval': False}
    save_json(output / 'verification.json', proof)
    return check(config_path, output)


def check(config_path, output):
    check_preparation(config_path, output)
    output, work, master, upload = media.paths(output)
    proof = state.load(output / 'verification.json')
    require(proof['passed'] is True and proof['adapter'] == state.identity(__file__)
        and all(proof[name] == state.identity(path) for name, path in (('preparation', output / 'preparation.json'),
            ('source_corrected_media', output / 'source-corrected-media.json'), ('package', output / 'package.json'),
            ('render', work / 'render_state.json'), ('release', work / 'release.json')))
        and all(proof[name] is False for name in ('English_stream_copied', 'canonical_media_modified', 'installed',
            'human_listened', 'pronunciation_or_emotional_approval', 'full_visual_or_wordlevel_approval')),
        'Stale or overclaiming source-corrected media verification')
    for bound in proof['decoded_pcm']:
        require(state.matches(bound), 'Verified decoded Chinese audio changed')
    record = media.derivation(work)
    require(set(proof['English_subtitles']) == {'body', 'master'} and set(proof['full_decode']) == {'master', 'upload'}
        and len(proof['decoded_pcm']) == 2, 'Incomplete actual English or full-media verification inventory')
    expected_subtitles = {'body': record['files']['en_display.srt'], 'master': record['captions']['en.srt']}
    for name, item in proof['English_subtitles'].items():
        require(item['expected'] == expected_subtitles[name] and state.matches(item['expected']) and state.matches(item['extracted'])
            and Path(item['extracted']['path']) == output / f'{name}-English.srt'
            and item['maximum_container_rounding_seconds'] == .002
            and item['all_source_words_and_times_match'] is True and item['old_English_packet_identity_claimed'] is False,
            'Actual extracted English subtitle proof changed')
        original_cues = check_videos.subtitles(Path(item['expected']['path']).read_text())
        extracted_cues = check_videos.subtitles(Path(item['extracted']['path']).read_text())
        require(item['cue_count'] == len(original_cues), 'Actual English subtitle count changed')
        compare_english(original_cues, extracted_cues)
    for receipt in [*proof['full_decode'].values(), *[item['operation'] for item in proof['English_subtitles'].values()]]:
        require(receipt['terminal'] is True and receipt['exit_code'] == 0 and state.matches(receipt['log'])
            and all(state.matches(bound) for bound in receipt['outputs']), 'Actual media operation receipt changed')
    with runtime(work):
        package = validate_package(work)
        state.validate_render(work, master)
        quality = delivery_quality.require(work, output / 'speech-review.sqlite3')
    release = state.load(work / 'release.json')
    require(quality['bindings'] == release['quality']['bindings'] == state.load(output / 'render-quality.json')['bindings']
        and release['technical_state'] == 'verified' and release['output'] == package['output'], 'Render-bound quality/release disagree')
    return {'passed': True, 'verification': state.identity(output / 'verification.json'), 'master': package['output'],
        'upload': package['upload_output'], 'English_stream_copied': False, 'quality_state': quality['state'],
        'canonical_media_modified': False, 'installed': False, 'human_listened': False,
        'pronunciation_or_emotional_approval': False}


def run(config_path, output):
    output = Path(output).resolve()
    if not (output / 'preparation.json').exists():
        prepare(config_path, output)
    else:
        check_preparation(config_path, output)
    if (output / 'verification.json').exists():
        return check(config_path, output)
    encode(config_path, output)
    return verify(config_path, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'encode', 'verify', 'check', 'run'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    lock_path = arguments.output.resolve().parent / f'.{arguments.output.name}.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = globals()[arguments.action](arguments.config, arguments.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
