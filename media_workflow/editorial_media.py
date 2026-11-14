"""Produce isolated editorial video pairs from verified current-audio preflight."""

import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import wave
from contextlib import closing, contextmanager
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import numpy as np

import caption_boundary_master
import caption_layout
import check_videos
import delivery_quality
import dubbing_state as state
import speech_groups
import speech_review
import video_package
from prepare_videos import atomic_write, save_json
from . import editorial_quality
from .speech_variants import require


KIND = 'complete-utterance-editorial-media-v1'
FROZEN_NARRATION = state.validate_narration
FROZEN_BLOCKERS = speech_groups.quality_blockers
FROZEN_PACKAGE = video_package.validate
RATE = 24000


def implementations():
    modules = (state, editorial_quality, caption_layout, caption_boundary_master,
        delivery_quality, speech_groups, speech_review, video_package, check_videos)
    return {module.__name__: state.identity(module.__file__) for module in modules}


def upstream(config_path):
    config_path = Path(config_path).resolve()
    config = state.load(config_path)
    require(set(config) == {'kind', 'preflight_config', 'preflight_manifest', 'terms', 'threads', 'scope'}
        and config['kind'] == KIND and config['scope'] == 'isolated-media-pair-no-installation',
        'Unsupported editorial media configuration or installation claim')
    require(type(config['threads']) is int and 1 <= config['threads'] <= 16, 'Invalid bounded encoder threads')
    for name in ('preflight_config', 'preflight_manifest', 'terms'):
        require(state.matches(config[name]), f'Stale media input: {name}')
    preflight_path = Path(config['preflight_manifest']['path'])
    preflight = state.load(preflight_path)
    require(preflight['config'] == config['preflight_config'], 'Preflight belongs to another configuration')
    editorial_quality.check(config['preflight_config']['path'], preflight_path.parent)
    source_work = Path(preflight['work'])
    record, contract = editorial_quality.derivation(source_work)
    package = state.load(contract['baseline_evidence']['package.json']['archive']['path'])
    donors = {'body': package['body'], 'master': contract['baseline_evidence']['master.mp4']['archive'],
        'intro': package['intro_audio']['waveform'], 'font': package['font']}
    for name, bound in donors.items():
        require(state.matches(bound), f'Historical media donor changed: {name}')
    require(all(donors['master'][key] == package['output'][key] for key in ('bytes', 'mtime_ns', 'sha256')),
        'Archived donor is not the installed baseline master')
    return config, preflight, source_work, record, contract, package, donors


def paths(output):
    output = Path(output).resolve()
    manifest = state.load(output / 'preparation.json')
    work = Path(manifest['work'])
    require(work.parent == output and work.name.startswith('.zh_work_yt_'), 'Invalid derived project location')
    title = state.load(work / 'meta.json')['title']
    master = check_videos.output_path(output, title)
    upload = master.with_name(master.name.replace('_中文配音.mp4', '_中文上传版.mp4'))
    require(upload != master, 'Cannot derive an unambiguous upload filename')
    return output, work, master, upload


def derivation(work):
    work = Path(work).resolve()
    record = state.load(work / 'editorial-media-source.json')
    require(record['kind'] == KIND and record['work'] == str(work)
        and record['adapter'] == state.identity(__file__) and record['implementations'] == implementations(),
        'Wrong/stale media narration runtime')
    for bound in (record['config'], record['preflight_manifest'], record['upstream_derivation'],
            record['upstream_narration'], record['stage_contract'], record['terms'], *record['donors'].values()):
        require(state.matches(bound), 'Media source authority changed')
    upstream_record = state.load(record['upstream_derivation']['path'])
    require(set(record['files']) == set(upstream_record['files']), 'Derived source/review inventory changed')
    for name, bound in record['files'].items():
        require(Path(bound['path']) == work / name and state.matches(bound)
            and all(bound[key] == upstream_record['files'][name][key] for key in ('bytes', 'mtime_ns', 'sha256')),
            'Copied current text/review/plan input differs from preflight')
    require((work / 'download').is_symlink() and (work / 'download').resolve() == Path(record['download_target'])
        and not (work / 'speech_groups.json').exists(), 'Derived download or unsupported grouped route changed')
    for name, bound in record['captions'].items():
        require(Path(bound['path']) == work.parent / 'subtitles' / name and state.matches(bound),
            'Final caption or layout identity changed')
    return record


def expected_narration(work, record, observed_at):
    original = state.load(record['upstream_narration']['path'])
    result = copy.deepcopy(original)
    result.update(origin=KIND, observed_at=observed_at, dependencies=state.narration_dependencies(work))
    result['dependencies']['editorial_media_source'] = state.identity(work / 'editorial-media-source.json')
    result['producer']['kind'] = KIND
    result['producer']['preflight_narration'] = record['upstream_narration']
    return result


def validate_narration(work):
    work = Path(work).resolve()
    record = derivation(work)
    narration = state.load(work / 'narration.json')
    require(isinstance(narration.get('observed_at'), str) and narration['observed_at']
        and narration == expected_narration(work, record, narration['observed_at'])
        and state.matches(narration['waveform']), 'Current media narration/audio changed')
    state.check_stats(state.load(work / 'tts_stats.json'), len(state.cues(work / 'en.srt')))
    return narration


def scoped_blockers(work, report, database):
    classification = state.load(work.parent / 'source-classification.json')
    require(classification['adapter'] == state.identity(__file__) and classification['project'] == str(work)
        and classification['revision'] == report['revision'] and classification['database_decisions_written'] == 0
        and classification['findings'] == editorial_quality.findings(work, report, validate_narration(work)),
        'Current actual-source classification changed')
    exempt = {item['issue']['issue_id'] for item in classification['findings']}
    restricted = copy.deepcopy(report)
    restricted['issues'] = [item for item in report['issues'] if item['issue_id'] not in exempt]
    return FROZEN_BLOCKERS(work, restricted, database)


def validate_package(work, allow_implementation_change=False):
    work = Path(work).resolve()
    record = derivation(work)
    rendering = state.load(work / 'render_state.json')
    require(state.matches(rendering['package_manifest']), 'Stale current package manifest')
    package = state.load(rendering['package_manifest']['path'])
    require(package['kind'] == KIND and package['producer'] == state.identity(__file__)
        and package['implementation_sha256'] == state.digest(__file__)
        and package['media_source'] == state.identity(work / 'editorial-media-source.json')
        and package['candidate_only'] is True and package['canonical_media_modified'] is False
        and all(state.matches(package[name]) for name in ('body', 'output', 'upload_output', 'burned_subtitles')),
        'Package must name its actual shared producer and current outputs')
    baseline = state.load(state.load(record['stage_contract']['path'])['baseline_evidence']['package.json']['archive']['path'])
    for name in ('intro_audio', 'font', 'intro_frames', 'body_offset_seconds', 'body_duration_seconds',
            'duration_seconds', 'chapters', 'audio_tracks'):
        require(package[name] == baseline[name], f'Historical intro/chapter geometry changed: {name}')
    require(package['subtitles'] == {language: record['captions'][f'{language}.srt']
        for language in video_package.LANGUAGES}, 'Package captions differ from actual projection')
    return FROZEN_PACKAGE(work, allow_implementation_change=True)


@contextmanager
def runtime(work):
    work = Path(work).resolve()
    record = derivation(work)
    upstream(record['config']['path'])
    require(state.validate_narration is FROZEN_NARRATION and speech_groups.quality_blockers is FROZEN_BLOCKERS
        and video_package.validate is FROZEN_PACKAGE, 'Do not nest or mix unknown production runtimes')
    validate_narration(work)
    state.validate_narration = lambda selected: validate_narration(work) if Path(selected).resolve() == work else FROZEN_NARRATION(selected)
    speech_groups.quality_blockers = lambda selected, report, database: scoped_blockers(work, report, database) \
        if Path(selected).resolve() == work else FROZEN_BLOCKERS(selected, report, database)
    video_package.validate = lambda selected, allow_implementation_change=False: validate_package(work) \
        if Path(selected).resolve() == work else FROZEN_PACKAGE(selected, allow_implementation_change)
    try:
        yield
    finally:
        state.validate_narration = FROZEN_NARRATION
        speech_groups.quality_blockers = FROZEN_BLOCKERS
        video_package.validate = FROZEN_PACKAGE


def same_report(previous, current):
    require(previous['issues'] == current['issues'], 'Unchanged current composite candidates differ from preflight')
    return {'preflight_candidates': len(previous['issues']), 'current_candidates': len(current['issues']),
        'all_current_candidate_records_exact': True, 'historical_scans_preserved': True, 'new_decisions_or_resolutions': 0}


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config, preflight, source_work, upstream_record, contract, baseline, donors = upstream(config_path)
    require(not output.exists(), 'Never overwrite successful or interrupted media preparation')
    canonical = Path(state.load(state.load(upstream_record['config']['path'])['stage_config']['path'])['work'])
    for parent in (source_work.parent, canonical, Path(contract['composite_waveform']['path']).parent):
        require(not output.is_relative_to(parent) and not parent.is_relative_to(output), 'Output overlaps authoritative inputs')
    output.mkdir(parents=True)
    work = output / source_work.name
    work.mkdir()
    receipt = {'kind': KIND, 'adapter': state.identity(__file__), 'config': state.identity(config_path),
        'work': str(work), 'complete': False, 'created_at': state.now(), 'canonical_media_modified': False,
        'human_listened': False, 'pronunciation_or_emotional_approval': False}
    save_json(output / 'preparation.json', receipt)
    try:
        for name in upstream_record['files']:
            shutil.copy2(source_work / name, work / name)
        (work / 'download').symlink_to((source_work / 'download').resolve(), target_is_directory=True)
        subtitles = output / 'subtitles'
        subtitles.mkdir()
        projection = state.load(upstream_record['projection_manifest']['path'])
        for language in video_package.LANGUAGES:
            shutil.copy2(projection['outputs'][f'{language}.srt']['path'], subtitles / f'{language}.srt')
        probe = video_package.probe(donors['master']['path'])
        video = next(track for track in probe['streams'] if track['codec_type'] == 'video')
        require([track['codec_type'] for track in probe['streams']][:6] ==
            ['video', 'audio', 'audio', 'subtitle', 'subtitle', 'subtitle'], 'Unsupported donor stream mapping')
        forms = caption_layout.load_forms(config['terms']['path'])
        ass, layout = caption_layout.subtitles(work, video['width'], video['height'], baseline['duration_seconds'],
            'bottom', forms, subtitles / 'zh.srt')
        require(layout['new_registered_term_split_cues'] == 0 and layout['timings_changed'] is False
            and layout['caption_text_changed'] is False, 'Registered term/quantity layout failed')
        atomic_write(subtitles / 'zh.ass', ass)
        save_json(subtitles / 'layout.json', layout)
        samples = Decimal(str(baseline['body_duration_seconds'])) * RATE
        require(samples == samples.to_integral_value(), 'Body duration must have an exact PCM sample endpoint')
        with wave.open(contract['composite_waveform']['path'], 'rb') as audio:
            require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, RATE)
                and audio.getnframes() >= int(samples), 'Unsupported or incomplete composite WAV')
            audio.setpos(int(samples))
            tail = audio.readframes(audio.getnframes() - int(samples))
        require(not any(tail), 'Output duration would trim nonzero source or synthesis')
        save_json(output / 'container-tail.json', {'body_samples': int(samples), 'extra_zero_samples': len(tail) // 2,
            'waveform': contract['composite_waveform'], 'trimmed_nonzero_samples': 0})
        record = {'kind': KIND, 'adapter': state.identity(__file__), 'implementations': implementations(),
            'work': str(work), 'config': state.identity(config_path), 'preflight_manifest': config['preflight_manifest'],
            'upstream_derivation': preflight['derivation'], 'upstream_narration': preflight['speech_files']['narration.json'],
            'stage_contract': upstream_record['stage_contract'], 'donors': donors, 'terms': config['terms'],
            'download_target': str((work / 'download').resolve()),
            'files': {name: state.identity(work / name) for name in upstream_record['files']},
            'captions': {path.name: state.identity(path) for path in subtitles.iterdir()}}
        save_json(work / 'editorial-media-source.json', record)
        save_json(work / 'narration.json', expected_narration(work, record, state.now()))
        database = output / 'speech-review.sqlite3'
        source_database = source_work.parent / 'speech-review.sqlite3'
        with closing(sqlite3.connect(source_database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            before = editorial_quality.database_snapshot(source)
            require(before == state.load(source_work.parent / 'database-after.json'), 'Preflight history is not current')
            with closing(sqlite3.connect(database)) as destination:
                source.backup(destination)
                require(editorial_quality.database_snapshot(destination) == before, 'Consistent database backup differs')
        save_json(output / 'database-before.json', before)
        with runtime(work):
            report = speech_review.scan(work, database, with_audio=True)
            findings = editorial_quality.findings(work, report, validate_narration(work))
            save_json(output / 'source-classification.json', {'adapter': state.identity(__file__), 'project': str(work),
                'revision': report['revision'], 'findings': findings, 'database_decisions_written': 0,
                'human_listened': False, 'original_pause_role_or_intelligibility_approved': False})
            quality = delivery_quality.evaluate(work, database)
        require(quality['allowed'] and quality['bindings']['inputs']['render_state.json'] is None,
            'Current pre-render quality is blocked or falsely rendered')
        save_json(output / 'pre-render-quality.json', quality)
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            after = editorial_quality.database_snapshot(connection)
        history = editorial_quality.verify_history(before, after, report['project_id'], report['revision'])
        save_json(output / 'database-after.json', after)
        save_json(output / 'database-history.json', history)
        save_json(output / 'candidate-history.json', same_report(state.load(source_work / 'speech_review.json'), report))
        names = ('database-before.json', 'database-after.json', 'database-history.json', 'candidate-history.json',
            'speech-review.sqlite3', 'source-classification.json', 'pre-render-quality.json', 'container-tail.json')
        receipt.update(complete=True, files={name: state.identity(output / name) for name in names},
            speech_files={name: state.identity(work / name) for name in
                ('narration.json', 'speech_review.json', 'speech_review.html', 'speech_database.json')})
        save_json(output / 'preparation.json', receipt)
        return check_preparation(config_path, output)
    except BaseException as error:
        receipt.update(complete=False, error=f'{type(error).__name__}: {error}')
        save_json(output / 'preparation.json', receipt)
        raise


def check_preparation(config_path, output):
    output, work, master, upload = paths(output)
    receipt = state.load(output / 'preparation.json')
    require(receipt['complete'] is True and receipt['kind'] == KIND and receipt['adapter'] == state.identity(__file__)
        and receipt['config'] == state.identity(config_path) and receipt['canonical_media_modified'] is False,
        'Stale/incomplete media preparation')
    for bound in (*receipt['files'].values(), *receipt['speech_files'].values()):
        require(state.matches(bound), 'Prepared quality/history evidence changed')
    with closing(sqlite3.connect((output / 'speech-review.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
        require(editorial_quality.database_snapshot(connection) == state.load(output / 'database-after.json'),
            'Prepared private database rows changed')
    validate_narration(work)
    try:
        FROZEN_NARRATION(work)
    except ValueError:
        pass
    else:
        raise ValueError('Bare frozen validation accepted an unrecognized editorial producer')
    return {'passed': True, 'preparation': state.identity(output / 'preparation.json'), 'work': str(work),
        'master': str(master), 'upload': str(upload), 'canonical_media_modified': False, 'human_listened': False}


def operation(output, name, command, inputs, produced=()):
    directory = output / 'operations'
    directory.mkdir(exist_ok=True)
    receipt_path = directory / f'{name}.json'
    log_path = directory / f'{name}.log'
    contract = {'adapter': state.identity(__file__), 'command': command, 'inputs': inputs,
        'cwd': str(output / 'subtitles'), 'expected_outputs': [str(path) for path in produced]}
    if receipt_path.exists():
        receipt = state.load(receipt_path)
        require(receipt['contract'] == contract and receipt.get('exit_code') == 0
            and all(state.matches(bound) for bound in receipt['outputs']) and state.matches(receipt['log']),
            f'Preserve failed/interrupted/stale operation; do not restart: {name}')
        return receipt
    require(not log_path.exists() and all(not path.exists() for path in produced), f'Unreceipted output/log exists: {name}')
    receipt = {'contract': contract, 'started_at': state.now(), 'pid': os.getpid(), 'terminal': False}
    save_json(receipt_path, receipt)
    with log_path.open('x') as log:
        result = subprocess.run(command, cwd=output / 'subtitles', stdout=log, stderr=subprocess.STDOUT)
    receipt.update(terminal=True, finished_at=state.now(), exit_code=result.returncode, log=state.identity(log_path),
        outputs=[state.identity(path) for path in produced if path.exists()])
    save_json(receipt_path, receipt)
    require(result.returncode == 0 and len(receipt['outputs']) == len(produced), f'Operation failed; preserve evidence: {name}')
    return receipt


def media_command_inputs(work):
    record = derivation(work)
    return {'source': state.identity(work / 'editorial-media-source.json'),
        'narration': state.identity(work / 'narration.json'), 'donors': record['donors'], 'captions': record['captions']}


def encode(config_path, output):
    check_preparation(config_path, output)
    output, work, master, upload = paths(output)
    record = derivation(work)
    config = state.load(config_path)
    baseline = state.load(state.load(record['stage_contract']['path'])['baseline_evidence']['package.json']['archive']['path'])
    donors, captions = record['donors'], record['captions']
    waveform = validate_narration(work)['waveform']['path']
    duration, body_duration = [str(baseline[name]) for name in ('duration_seconds', 'body_duration_seconds')]
    body = output / 'body.mp4'
    common = ['ffmpeg', '-nostdin', '-n', '-v', 'error']
    inputs = media_command_inputs(work)
    operation(output, 'body', [*common, '-i', donors['body']['path'], '-i', waveform,
        '-i', str(work / 'zh_display.srt'), '-i', str(work / 'bilingual_display.srt'),
        '-map', '0:v:0', '-map', '1:a:0', '-map', '0:a:1', '-map', '2:s:0', '-map', '0:s:1', '-map', '3:s:0',
        '-map_metadata', '0', '-map_chapters', '0', '-c:v', 'copy', '-c:a:0', 'aac', '-b:a:0', '128k',
        '-c:a:1', 'copy', '-c:s:0', 'mov_text', '-c:s:1', 'copy', '-c:s:2', 'mov_text',
        '-metadata:s:a:0', 'language=chi', '-metadata:s:a:1', 'language=eng', '-disposition:a:0', 'default',
        '-disposition:a:1', '0', '-metadata:s:s:0', 'language=chi', '-metadata:s:s:1', 'language=eng',
        '-metadata:s:s:2', 'language=chi', '-t', body_duration, '-movflags', '+faststart', str(body)], inputs, (body,))
    offset = baseline['body_offset_seconds']
    filters = f'[2:a:0]apad,atrim=end={offset:.9f},asetpts=PTS-STARTPTS,aresample={RATE},aformat=channel_layouts=mono[head];'
    filters += f'[1:a:0]asetpts=PTS-STARTPTS,aresample={RATE},aformat=channel_layouts=mono[body];'
    filters += '[head][body]concat=n=2:v=0:a=1[chinese]'
    operation(output, 'master', [*common, '-i', donors['master']['path'], '-i', waveform, '-i', donors['intro']['path'],
        '-i', captions['zh.srt']['path'], '-i', captions['bilingual.srt']['path'], '-filter_complex', filters,
        '-map', '0:v:0', '-map', '[chinese]', '-map', '0:a:1', '-map', '3:s:0', '-map', '0:s:1', '-map', '4:s:0',
        '-map_metadata', '0', '-map_chapters', '0', '-c:v', 'copy', '-c:a:0', 'aac', '-b:a:0', '128k',
        '-c:a:1', 'copy', '-c:s:0', 'mov_text', '-c:s:1', 'copy', '-c:s:2', 'mov_text',
        '-metadata:s:a:0', 'language=chi', '-metadata:s:a:0', 'title=中文配音（含AI导读）',
        '-metadata:s:a:1', 'language=eng', '-disposition:a:0', 'default', '-disposition:a:1', '0',
        '-metadata:s:s:0', 'language=chi', '-metadata:s:s:1', 'language=eng', '-metadata:s:s:2', 'language=chi',
        '-disposition:s:0', '0', '-disposition:s:1', '0', '-disposition:s:2', '0',
        '-t', duration, '-movflags', '+faststart', str(master)], inputs, (master,))
    video = next(track for track in video_package.probe(master)['streams'] if track['codec_type'] == 'video')
    timebase = Fraction(video['time_base'])
    require(timebase.numerator == 1 and timebase.denominator <= 1000000, 'Unsupported exact source video timebase')
    operation(output, 'upload', [*common, '-i', str(master), '-map', '0:v:0', '-map', '0:a:0',
        '-map_metadata', '0', '-map_chapters', '0', '-sn', '-vf', 'subtitles=zh.ass', '-c:v', 'libx264',
        '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-threads', str(config['threads']),
        '-filter_threads', '2', '-vsync', '0', '-enc_time_base', '-1', '-video_track_timescale', str(timebase.denominator),
        '-c:a', 'copy', '-metadata:s:a:0', 'language=chi', '-metadata:s:a:0', 'title=中文配音',
        '-disposition:a:0', 'default', '-t', duration, '-progress', str(output / 'upload-progress.log'),
        '-stats_period', '5', '-movflags', '+faststart', str(upload)],
        {**inputs, 'master': state.identity(master)}, (upload,))
    manifest_path = output / 'package.json'
    if not manifest_path.exists():
        manifest = {name: copy.deepcopy(baseline[name]) for name in ('intro_audio', 'font', 'intro_frames',
            'body_offset_seconds', 'body_duration_seconds', 'duration_seconds', 'chapters', 'audio_tracks')}
        manifest.update(kind=KIND, producer=state.identity(__file__), implementation_sha256=state.digest(__file__),
            media_source=state.identity(work / 'editorial-media-source.json'), plan=state.identity(work / 'package-plan.json'),
            dependencies=state.render_dependencies(work, Path(waveform), state.source_video(work)),
            body=state.identity(body), output=state.identity(master), upload_output=state.identity(upload),
            subtitles={language: captions[f'{language}.srt'] for language in video_package.LANGUAGES},
            burned_subtitles=captions['zh.ass'], candidate_only=True, canonical_media_modified=False,
            created_at=state.now(), human_listened=False, pronunciation_or_emotional_approval=False)
        save_json(manifest_path, manifest)
        state.bind_render(work, master, Path(waveform), state.source_video(work),
            {'subs': 'soft', 'sub_pos': 'bottom', 'video_reused': True, 'preserve_video': True}, origin=KIND)
        rendering = state.load(work / 'render_state.json')
        rendering['package_manifest'] = state.identity(manifest_path)
        save_json(work / 'render_state.json', rendering)
    with runtime(work):
        package = validate_package(work)
        quality = delivery_quality.require(work, output / 'speech-review.sqlite3')
        require(quality['bindings']['inputs']['render_state.json'] == state.digest(work / 'render_state.json'),
            'Render-bound quality is missing current media')
    if not (output / 'render-quality.json').exists():
        save_json(output / 'render-quality.json', quality)
    return {'encoded': True, 'master': package['output'], 'upload': package['upload_output'],
        'quality': quality['state'], 'installed': False, 'human_listened': False}


def frame_schedule(path):
    media = video_package.probe(path)
    video = next(track for track in media['streams'] if track['codec_type'] == 'video')
    result = subprocess.run(['ffprobe', '-v', 'error', '-threads', '4', '-select_streams', 'v:0', '-show_frames',
        '-show_entries', 'frame=best_effort_timestamp,pkt_duration', '-of', 'json', str(path)],
        capture_output=True, text=True, check=True)
    require(not result.stderr.strip(), 'Frame schedule decoder emitted errors')
    timebase = Fraction(video['time_base'])
    frames = json.loads(result.stdout)['frames']
    schedule = []
    for frame in frames:
        require('best_effort_timestamp' in frame and int(frame.get('pkt_duration', 0)) > 0,
            'Decoded frame lacks an exact timestamp/duration')
        schedule.append([str(int(frame['best_effort_timestamp']) * timebase), str(int(frame['pkt_duration']) * timebase)])
    require(schedule and len(schedule) == int(video['nb_frames']) and len({item[0] for item in schedule}) == len(schedule),
        'Decoded video frame coverage is incomplete or ambiguous')
    return {'frames': len(schedule), 'exact_rational_schedule_sha256': state.signature(schedule),
        'first': schedule[0], 'last': schedule[-1], 'dimensions': [video['width'], video['height']]}


def signal_diagnostics(expected, actual, label):
    require(len(expected) == len(actual) > 0, f'Incomplete fixed-offset AAC window: {label}')
    reference = expected.astype(np.float64)
    observed = actual.astype(np.float64)
    reference_energy = float(np.dot(reference, reference))
    observed_energy = float(np.dot(observed, observed))
    require(reference_energy > 0 and observed_energy > 0, f'No measurable signal in declared audio window: {label}')
    similarity = float(np.dot(reference, observed) / np.sqrt(reference_energy * observed_energy))
    level = float(np.sqrt(observed_energy / reference_energy))
    require(similarity >= .95 and .85 <= level <= 1.15, f'Fixed-offset AAC signal check failed: {label}')
    return {'label': label, 'samples': len(expected), 'fixed_offset_cosine_similarity': similarity,
        'rms_ratio': level, 'expected_pcm_sha256': hashlib.sha256(expected.tobytes()).hexdigest(),
        'actual_aac_pcm_sha256': hashlib.sha256(actual.tobytes()).hexdigest(), 'human_listened': False,
        'phonetic_speaker_or_emotional_approval': False}


def verify(config_path, output):
    check_preparation(config_path, output)
    output, work, master, upload = paths(output)
    require(not (output / 'verification.json').exists(), 'Reuse completed verification with check, not another full decode')
    record = derivation(work)
    with runtime(work):
        package = validate_package(work)
        structures = {'master': video_package.verify_media(master, package),
            'upload': video_package.verify_media(upload, package, upload=True)}
    preserved = {}
    for name, before, after in (('body', record['donors']['body'], package['body']),
            ('master', record['donors']['master'], package['output'])):
        old_packets = caption_boundary_master.packet_inventory(Path(before['path']), [0, 2, 4])
        new_packets = caption_boundary_master.packet_inventory(Path(after['path']), [0, 2, 4])
        require(old_packets == new_packets, f'Copied video/original-English audio/English subtitle packets changed: {name}')
        old_streams, new_streams = video_package.probe(before['path'])['streams'], video_package.probe(after['path'])['streams']
        for index in (0, 2, 4):
            for field in ('time_base', 'codec_name', 'profile', 'sample_rate', 'channels', 'width', 'height', 'pix_fmt'):
                require(old_streams[index].get(field) == new_streams[index].get(field), f'Copied stream configuration changed: {name}')
        preserved[name] = new_packets
    chinese = [caption_boundary_master.packet_inventory(path, [1]) for path in (master, upload)]
    require(chinese[0] == chinese[1], 'Master/upload Chinese AAC packets or exact time schedule differ')
    schedules = [frame_schedule(path) for path in (master, upload)]
    require(schedules[0] == schedules[1], 'Burned upload did not preserve complete decoded frame presentation schedule')
    decoded = []
    for name, path in (('master', master), ('upload', upload)):
        pcm = output / f'{name}-chinese.s16le'
        operation(output, f'decode-{name}-chinese', ['ffmpeg', '-nostdin', '-n', '-v', 'error', '-i', str(path),
            '-map', '0:a:0', '-ac', '1', '-ar', str(RATE), '-f', 's16le', str(pcm)],
            {'media': state.identity(path)}, (pcm,))
        decoded.append(pcm)
    require(state.digest(decoded[0]) == state.digest(decoded[1]), 'Master/upload decoded Chinese PCM differs')
    actual = np.memmap(decoded[0], dtype='<i2', mode='r')
    narration = validate_narration(work)
    with wave.open(narration['waveform']['path'], 'rb') as audio:
        expected = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')
    offset = Decimal(str(package['body_offset_seconds'])) * RATE
    require(offset == offset.to_integral_value(), 'Nonintegral intro sample offset')
    offset = int(offset)
    contract = state.load(record['stage_contract']['path'])
    diagnostics = []
    for name, windows in (('editorial', contract['cases']), ('source', contract['original_source_ranges'])):
        for index, window in enumerate(windows):
            first = window['start_sample']
            last = window.get('actual_source_end_sample', window['end_sample'])
            diagnostics.append(signal_diagnostics(expected[first:last], actual[offset + first:offset + last],
                f'{name}-{window.get("unit_id", index + 1)}'))
    full_decode = {}
    for name, path in (('master', master), ('upload', upload)):
        full_decode[name] = operation(output, f'full-{name}-decode', ['ffmpeg', '-nostdin', '-v', 'error', '-xerror',
            '-threads', '4', '-i', str(path), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'],
            {'media': state.identity(path)})
        require(full_decode[name]['log']['bytes'] == 0, 'Full decoder emitted errors')
    with runtime(work):
        release = delivery_quality.release(work, output / 'speech-review.sqlite3')
    proof = {'passed': True, 'checked_at': state.now(), 'adapter': state.identity(__file__),
        'preparation': state.identity(output / 'preparation.json'), 'package': state.identity(output / 'package.json'),
        'render': state.identity(work / 'render_state.json'), 'release': state.identity(work / 'release.json'),
        'structures': structures, 'preserved_packets': preserved, 'chinese_aac_packets': chinese[0],
        'master_upload_chinese_pcm_equal': True, 'decoded_pcm': [state.identity(path) for path in decoded],
        'complete_decoded_video_schedule': schedules[0], 'fixed_offset_audio_diagnostics': diagnostics,
        'full_decode': full_decode, 'quality_state': release['quality']['state'],
        'canonical_media_modified': False, 'installed': False, 'human_listened': False,
        'full_visual_or_wordlevel_approval': False, 'pronunciation_or_emotional_approval': False}
    save_json(output / 'verification.json', proof)
    return check(config_path, output)


def check(config_path, output):
    check_preparation(config_path, output)
    output, work, master, upload = paths(output)
    proof = state.load(output / 'verification.json')
    require(proof['passed'] is True and proof['adapter'] == state.identity(__file__)
        and proof['preparation'] == state.identity(output / 'preparation.json')
        and proof['package'] == state.identity(output / 'package.json')
        and proof['render'] == state.identity(work / 'render_state.json')
        and proof['release'] == state.identity(work / 'release.json'), 'Stale current media verification')
    for bound in proof['decoded_pcm']:
        require(state.matches(bound), 'Verified decoded audio changed')
    for operation_proof in proof['full_decode'].values():
        require(operation_proof['exit_code'] == 0 and operation_proof['terminal']
            and state.matches(operation_proof['log']), 'Full decode receipt changed')
    with runtime(work):
        package = validate_package(work)
        state.validate_render(work, master)
        current = delivery_quality.require(work, output / 'speech-review.sqlite3')
    release = state.load(work / 'release.json')
    require(current['bindings'] == release['quality']['bindings'] == state.load(output / 'render-quality.json')['bindings']
        and release['technical_state'] == 'verified' and release['output'] == package['output'],
        'Render/current quality/release disagree')
    return {'passed': True, 'verification': state.identity(output / 'verification.json'),
        'master': package['output'], 'upload': package['upload_output'], 'quality_state': current['state'],
        'canonical_media_modified': False, 'installed': False, 'human_listened': False,
        'pronunciation_or_emotional_approval': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'encode', 'verify', 'check'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    lock_path = args.output.resolve().parent / f'.{args.output.name}.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = globals()[args.action](args.config, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
