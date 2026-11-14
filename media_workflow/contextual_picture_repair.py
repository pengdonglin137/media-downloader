"""Rebuild failed context pictures as FFV1 while retaining proven native PCM."""

import argparse
import fcntl
import json
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import dubbing_state as state
import prepare_videos
from media_workflow import contextual_preview as preview
from media_workflow import contextual_native_repair as native_repair
from media_workflow import insertion_audition as audition
from media_workflow import native_audio


require = audition.require
load = audition.load


def binding(plan_path):
    plan, bound = native_repair.binding(plan_path)
    return plan, {'native_repair_inputs': bound, 'picture_implementation': state.identity(__file__)}


def check(plan_path, output):
    plan, bound = binding(plan_path)
    receipt = load(output / 'manifest.json')
    require(receipt.get('kind') == 'ffv1-context-picture-repair-v1' and receipt.get('complete') is True
        and receipt.get('inputs') == bound and all(receipt.get(name) is False for name in audition.UNAPPROVED),
        'Incomplete, stale or overclaimed picture repair')
    require(set(path.name for path in output.iterdir()) == {'manifest.json', 'picture.lock'} | set(receipt['artifacts']),
        'Unexpected picture output inventory')
    require(all(record['path'] == str(output / name) and state.matches(record)
        for name, record in receipt['artifacts'].items()), 'Picture repair output changed')
    require(all(state.matches(record) for record in receipt['retained_inputs'].values()), 'Retained input changed')
    require(preview.comparisons(plan, output) == receipt['cases'], 'Complete repaired picture or PCM changed')
    return {'manifest': state.identity(output / 'manifest.json'), 'cases': receipt['cases'],
        'reused_without_media_commands': True, **{name: False for name in audition.UNAPPROVED}}


def repair(plan_path, generation, previous, reference, output):
    plan, bound = binding(plan_path)
    reference_plan, native_bound, context = native_repair.check_reference(plan_path, reference)
    old = load(previous / 'manifest.json')
    require(old.get('kind') == 'native-context-preview-repair-v1' and old.get('complete') is False
        and old.get('inputs') == native_bound and reference_plan == plan
        and old.get('error') == 'ValueError: Complete output frames or exact presentation grid changed',
        'Expected the matching retained picture-comparison failure')
    require(all(item.get('status') == 'complete' and item.get('returncode') == 0
        for item in (load(path) for path in previous.glob('*-process.json'))), 'Unresolved previous media command failure')
    require(old['previous_files']['manifest.json'] == state.identity(generation / 'manifest.json'),
        'The original generation is not the retained input of the native repair')
    native_repair.destination(output, [plan_path, generation, previous, reference, Path(plan['source']['path'])])
    if output.exists():
        with (output / 'picture.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return check(plan_path, output)
    retained = {str(path): state.identity(path) for directory in (generation, previous, reference)
        for path in directory.iterdir() if path.is_file()}
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'picture.lock').open('xb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = {'kind': 'ffv1-context-picture-repair-v1', 'scope': preview.SCOPE, 'inputs': bound,
            'retained_inputs': retained, 'previous_failure_retained': old['error'], 'complete': False,
            'takeover_identity_authority': 'current files; not fabricated historical binding',
            **{name: False for name in audition.UNAPPROVED}}
        prepare_videos.save_json(output / 'manifest.json', receipt)
        try:
            profile = plan['profile']
            base = [native_bound['preview_inputs']['tools']['ffmpeg']['path'], '-nostdin', '-hide_banner', '-v', 'error', '-n']
            audio = ['-c:a', 'pcm_f32le', '-ar', str(profile['sample_rate']), '-ac', str(profile['channels'])]
            def run(name, arguments, stdout=None):
                native_audio.run_process(base + arguments, output, name, stdout or f'{name}.stdout.log', lock.fileno())
            for name in ('native-reference.f32le', 'source-reference.framehash', 'source-context.mov',
                    'source-context.framehash', 'source-context.f32le', 'source-context.audio.framehash'):
                shutil.copy2(previous / name, output / name)
            require((output / 'source-context.f32le').read_bytes() == context, 'Native context PCM was not proven')
            for case in plan['cases']:
                key = case['key']
                for suffix in ('-converted.f32le', '-insert.f32le', '-card.framehash'):
                    shutil.copy2(previous / f'{key}{suffix}', output / f'{key}{suffix}')
                observed = preview.timing(plan, case)
                frame = observed['at_frame']
                graph = (f'[0:v]split[vb][va];[vb]trim=end_frame={frame},setpts=PTS-STARTPTS[v0];'
                    f'[va]trim=start_frame={frame},setpts=PTS-STARTPTS[v2];'
                    '[v0][1:v][v2]concat=n=3:v=1:a=0[v]')
                run(key + '-ffv1-render', ['-i', str(previous / 'source-context.mov'), '-i', str(generation / f'{key}-insert.mov'),
                    '-f', 'f32le', '-ar', str(profile['sample_rate']), '-ac', str(profile['channels']),
                    '-i', str(previous / f'{key}-input.f32le'), '-filter_complex', graph,
                    '-map', '[v]', '-map', '2:a:0', '-c:v', 'ffv1', '-level', '3', '-g', '1', '-threads:v', '4',
                    '-pix_fmt', 'yuv420p', '-enc_time_base:v', profile['time_base'], '-vsync', '0',
                    *audio, '-f', 'nut', str(output / f'{key}.nut')])
                run(key + '-video', ['-i', str(output / f'{key}.nut'), '-map', '0:v:0', '-an', '-c:v', 'rawvideo',
                    '-enc_time_base', profile['time_base'], '-vsync', '0', '-f', 'framehash', '-hash', 'sha256', '-'], key + '.framehash')
                run(key + '-audio', ['-i', str(output / f'{key}.nut'), '-map', '0:a:0', '-vn', *audio,
                    '-f', 'f32le', '-'], key + '.f32le')
                run(key + '-audio-grid', ['-i', str(output / f'{key}.nut'), '-map', '0:a:0', '-vn', *audio,
                    '-f', 'framehash', '-hash', 'sha256', '-'], key + '.audio.framehash')
            receipt['cases'] = preview.comparisons(plan, output)
            require(binding(plan_path) == (plan, bound) and all(state.matches(record) for record in retained.values()),
                'Retained inputs changed during picture repair')
            receipt['artifacts'] = {path.name: state.identity(path) for path in output.iterdir()
                if path.name not in ('manifest.json', 'picture.lock')}
            receipt['complete'] = True
        except BaseException as error:
            receipt['error'] = f'{type(error).__name__}: {error}'
            raise
        finally:
            prepare_videos.save_json(output / 'manifest.json', receipt)
    return check(plan_path, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('repair', 'check'))
    parser.add_argument('plan')
    parser.add_argument('--output', required=True)
    parser.add_argument('--generation')
    parser.add_argument('--previous-output')
    parser.add_argument('--reference')
    options = parser.parse_args()
    handlers = {number: signal.signal(number, native_audio.interrupted) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        plan_path, output = Path(options.plan).resolve(), Path(options.output).resolve()
        if options.action == 'repair':
            require(options.generation and options.previous_output and options.reference, 'Bind all retained input directories')
            result = repair(plan_path, Path(options.generation).resolve(), Path(options.previous_output).resolve(),
                Path(options.reference).resolve(), output)
        else:
            with (output / 'picture.lock').open('rb') as lock:
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
        for number, handler in handlers.items():
            signal.signal(number, handler)


if __name__ == '__main__':
    raise SystemExit(main())
