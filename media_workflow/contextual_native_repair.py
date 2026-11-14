"""Replace seek-dependent preview PCM using a sequential, native-ledger-proven reference."""

import argparse
import fcntl
import json
import shutil
import signal
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import dubbing_state as state
import prepare_videos
from media_workflow import contextual_preview as preview
from media_workflow import insertion_audition as audition
from media_workflow import native_audio


require = audition.require
load = audition.load
ROOT = Path(__file__).resolve().parents[1]


def binding(plan_path):
    plan, bound = preview.inputs(plan_path)
    return plan, {'preview_inputs': bound, 'repair_implementation': state.identity(__file__)}


def destination(output, inputs):
    require(output != ROOT and ROOT not in output.parents
        and not any(parent.name.startswith('.zh_work_') for parent in (output, *output.parents)),
        'Repair output must be outside the repository and canonical work directories')
    require(all(output != path and output not in path.parents for path in inputs), 'Output cannot contain an input')


def context_bytes(plan, raw):
    import hashlib
    rows, start, end = preview.native_reference(plan)
    content = Path(raw).read_bytes()
    offset = 0
    for row in rows:
        payload = content[offset:offset + row[4]]
        require(len(payload) == row[4] and hashlib.sha256(payload).hexdigest() == row[5],
            f'Sequential native frame changed at sample {row[2]}')
        offset += row[4]
    require(offset == len(content), 'Unexpected sequential reference length')
    stride = plan['profile']['channels'] * 4
    return content[(start - rows[0][2]) * stride:(end - rows[0][2]) * stride]


def check_reference(plan_path, output):
    plan, bound = binding(plan_path)
    receipt = load(output / 'reference.json')
    require(receipt.get('kind') == 'sequential-context-native-reference-v1'
        and receipt.get('complete') is True and receipt.get('inputs') == bound, 'Stale or incomplete sequential reference')
    require(set(path.name for path in output.iterdir()) == {'reference.json', 'reference.lock'} | set(receipt['artifacts']),
        'Unexpected reference output files')
    require(all(record['path'] == str(output / name) and state.matches(record)
        for name, record in receipt['artifacts'].items()), 'Sequential reference identity changed')
    context = context_bytes(plan, output / 'native-reference.f32le')
    require(len(context) == receipt['context_samples'] * plan['profile']['channels'] * 4, 'Reference context length changed')
    return plan, bound, context


def extract(plan_path, output):
    plan, bound = binding(plan_path)
    destination(output, [plan_path, Path(plan['source']['path']), Path(plan['native_ledger']['path'])])
    if output.exists():
        with (output / 'reference.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check_reference(plan_path, output)
        return {'reused_without_decode': True, 'reference': state.identity(output / 'reference.json')}
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'reference.lock').open('xb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = {'kind': 'sequential-context-native-reference-v1', 'inputs': bound,
            'complete': False, 'decoding_policy': 'from source start to required context end; no input seek'}
        prepare_videos.save_json(output / 'reference.json', receipt)
        try:
            profile = plan['profile']
            rows, start, end = preview.native_reference(plan)
            command = [bound['preview_inputs']['tools']['ffmpeg']['path'], '-nostdin', '-hide_banner', '-v', 'error',
                '-i', plan['source']['path'], '-map', f'0:{profile["audio_stream"]}', '-vn', '-af',
                f'atrim=start_pts={rows[0][2]}:end_pts={rows[-1][2] + rows[-1][3]}', '-c:a', 'pcm_f32le',
                '-ar', str(profile['sample_rate']), '-ac', str(profile['channels']), '-f', 'f32le', '-']
            native_audio.run_process(command, output, 'sequential-extract', 'native-reference.f32le', lock.fileno())
            context = context_bytes(plan, output / 'native-reference.f32le')
            require(binding(plan_path) == (plan, bound), 'Reference inputs changed')
            receipt['context_samples'] = len(context) // (profile['channels'] * 4)
            receipt['native_reference_frames_checked'] = len(rows)
            receipt['artifacts'] = {path.name: state.identity(path) for path in output.iterdir()
                if path.name not in ('reference.json', 'reference.lock')}
            receipt['complete'] = True
        except BaseException as error:
            receipt['error'] = f'{type(error).__name__}: {error}'
            raise
        finally:
            prepare_videos.save_json(output / 'reference.json', receipt)
    return {'reused_without_decode': False, 'reference': state.identity(output / 'reference.json'),
        'context_samples': receipt['context_samples'], 'native_reference_frames_checked': len(rows)}


def check(plan_path, output):
    plan, bound = binding(plan_path)
    receipt = load(output / 'manifest.json')
    require(receipt.get('kind') == 'native-context-preview-repair-v1' and receipt.get('complete') is True
        and receipt.get('inputs') == bound and all(receipt.get(name) is False for name in audition.UNAPPROVED),
        'Incomplete, stale or overclaimed contextual repair')
    require(set(path.name for path in output.iterdir()) == {'manifest.json', 'repair.lock'} | set(receipt['artifacts']),
        'Unexpected repair output inventory')
    require(all(record['path'] == str(output / name) and state.matches(record)
        for name, record in receipt['artifacts'].items()), 'Repair output identity changed')
    require(all(state.matches(record) for record in receipt['previous_files'].values()), 'Previous failed attempt changed')
    require(state.matches(receipt['reference']), 'Sequential reference receipt changed')
    check_reference(plan_path, Path(receipt['reference']['path']).parent)
    require(preview.comparisons(plan, output) == receipt['cases'], 'Complete repaired context changed')
    return {'manifest': state.identity(output / 'manifest.json'), 'cases': receipt['cases'],
        'reused_without_media_commands': True, **{name: False for name in audition.UNAPPROVED}}


def repair(plan_path, previous, reference, output):
    plan, bound, context = check_reference(plan_path, reference)
    old = load(previous / 'manifest.json')
    require(old.get('kind') == preview.KIND and old.get('complete') is False
        and old.get('inputs') == bound['preview_inputs']
        and old.get('error', '').startswith('ValueError: Native source frame changed at sample '),
        'Expected the matching retained native-reference failure, not an unrelated or successful attempt')
    require(all(item.get('status') == 'complete' and item.get('returncode') == 0
        for item in (load(path) for path in previous.glob('*-process.json'))),
        'Previous media command failure requires separate recovery')
    destination(output, [plan_path, previous, reference, Path(plan['source']['path'])])
    if output.exists():
        with (output / 'repair.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return check(plan_path, output)
    previous_files = {path.name: state.identity(path) for path in previous.iterdir() if path.is_file()}
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'repair.lock').open('xb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = {'kind': 'native-context-preview-repair-v1', 'scope': preview.SCOPE, 'inputs': bound,
            'previous_files': previous_files, 'previous_failure_retained': old['error'],
            'reference': state.identity(reference / 'reference.json'), 'complete': False,
            'previous_file_binding_authority': 'current takeover identities, not invented historical prebinding',
            **{name: False for name in audition.UNAPPROVED}}
        prepare_videos.save_json(output / 'manifest.json', receipt)
        try:
            profile = plan['profile']
            base = [bound['preview_inputs']['tools']['ffmpeg']['path'], '-nostdin', '-hide_banner', '-v', 'error', '-n']
            audio = ['-c:a', 'pcm_f32le', '-ar', str(profile['sample_rate']), '-ac', str(profile['channels'])]
            def run(name, arguments, stdout=None):
                native_audio.run_process(base + arguments, output, name, stdout or f'{name}.stdout.log', lock.fileno())
            def remux(name, payload):
                with (output / f'{name}-input.f32le').open('xb') as stream:
                    stream.write(payload)
                run(name + '-remux', ['-i', str(previous / f'{name}.mov'), '-f', 'f32le', '-ar',
                    str(profile['sample_rate']), '-ac', str(profile['channels']), '-i', str(output / f'{name}-input.f32le'),
                    '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', *audio, '-video_track_timescale',
                    str(int(1 / Fraction(profile['time_base']))), str(output / f'{name}.mov')])
                run(name + '-video', ['-i', str(output / f'{name}.mov'), '-map', '0:v:0', '-an', '-c:v', 'rawvideo',
                    '-enc_time_base', profile['time_base'], '-vsync', '0', '-f', 'framehash', '-hash', 'sha256', '-'], name + '.framehash')
                run(name + '-audio', ['-i', str(output / f'{name}.mov'), '-map', '0:a:0', '-vn', *audio,
                    '-f', 'f32le', '-'], name + '.f32le')
                run(name + '-audio-grid', ['-i', str(output / f'{name}.mov'), '-map', '0:a:0', '-vn', *audio,
                    '-f', 'framehash', '-hash', 'sha256', '-'], name + '.audio.framehash')
            shutil.copy2(reference / 'native-reference.f32le', output / 'native-reference.f32le')
            shutil.copy2(previous / 'source-reference.framehash', output / 'source-reference.framehash')
            remux('source-context', context)
            for case in plan['cases']:
                key = case['key']
                for suffix in ('-converted.f32le', '-insert.f32le', '-card.framehash'):
                    shutil.copy2(previous / f'{key}{suffix}', output / f'{key}{suffix}')
                insertion = (previous / f'{key}-insert.f32le').read_bytes()
                anchor = preview.timing(plan, case)['at_sample'] * profile['channels'] * 4
                remux(key, context[:anchor] + insertion + context[anchor:])
            receipt['cases'] = preview.comparisons(plan, output)
            require(binding(plan_path) == (plan, bound) and all(state.matches(record) for record in previous_files.values()),
                'Inputs or retained failed attempt changed during repair')
            receipt['artifacts'] = {path.name: state.identity(path) for path in output.iterdir()
                if path.name not in ('manifest.json', 'repair.lock')}
            receipt['complete'] = True
        except BaseException as error:
            receipt['error'] = f'{type(error).__name__}: {error}'
            raise
        finally:
            prepare_videos.save_json(output / 'manifest.json', receipt)
    return check(plan_path, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('extract', 'check-reference', 'repair', 'check'))
    parser.add_argument('plan')
    parser.add_argument('--output', required=True)
    parser.add_argument('--reference')
    parser.add_argument('--previous-output')
    options = parser.parse_args()
    previous_handlers = {number: signal.signal(number, native_audio.interrupted) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        plan_path, output = Path(options.plan).resolve(), Path(options.output).resolve()
        if options.action == 'extract':
            result = extract(plan_path, output)
        elif options.action == 'check-reference':
            check_reference(plan_path, output)
            result = {'reference': state.identity(output / 'reference.json'), 'reused_without_decode': True}
        elif options.action == 'repair':
            require(options.reference and options.previous_output, 'Repair requires reference and previous-output')
            result = repair(plan_path, Path(options.previous_output).resolve(), Path(options.reference).resolve(), output)
        else:
            with (output / 'repair.lock').open('rb') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = check(plan_path, output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except native_audio.Interrupted as error:
        print(str(error), file=sys.stderr)
        return 128 + error.number
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 1
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)


if __name__ == '__main__':
    raise SystemExit(main())
