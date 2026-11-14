"""Build lossless PCM insertion auditions without installing a production timeline."""

import argparse
import fcntl
import hashlib
import json
import re
import sys
import wave
from pathlib import Path

import dubbing_state as state
import prepare_videos


KIND = 'pcm-insertion-audition-v1'
SCOPE = 'isolated-audition-no-production-installation'
ROOT = Path(__file__).resolve().parents[1]
IDENTITY_FIELDS = {'path', 'bytes', 'mtime_ns', 'sha256'}
UNAPPROVED = ('human_listened', 'pronunciation_or_emotion_approved', 'source_handoff_approved',
    'source_speakers_verified', 'production_installed', 'automatic_winner', 'database_written')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f'Nonfinite JSON constant: {value}')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object,
        parse_constant=invalid_constant)


def bound_file(value):
    require(isinstance(value, dict) and set(value) == IDENTITY_FIELDS, 'Bind the complete file identity')
    require(isinstance(value['path'], str) and Path(value['path']).is_absolute()
        and str(Path(value['path']).resolve()) == value['path'], 'Use canonical absolute file paths')
    require(type(value['bytes']) is int and value['bytes'] >= 0 and type(value['mtime_ns']) is int
        and isinstance(value['sha256'], str) and re.fullmatch('[0-9a-f]{64}', value['sha256']),
        'Invalid file identity values')
    require(state.matches(value), f'Stale input: {value["path"]}')


def wave_format(path):
    with wave.open(str(path), 'rb') as audio:
        channels, width, rate, samples = audio.getparams()[:4]
        require(audio.getcomptype() == 'NONE' and channels in (1, 2) and width == 2
            and 8000 <= rate <= 192000 and 0 < samples <= rate * 8 * 3600,
            'Expected complete PCM16 mono/stereo audio of at most eight hours')
    return {'channels': channels, 'sample_width': width, 'sample_rate': rate, 'samples': samples}


def inputs(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = load(plan_path)
    require(isinstance(plan, dict) and set(plan) == {'kind', 'scope', 'source', 'cases', 'evidence'}
        and plan['kind'] == KIND and plan['scope'] == SCOPE, 'Unexpected insertion audition plan')
    bound_file(plan['source'])
    source_format = wave_format(plan['source']['path'])
    require(isinstance(plan['evidence'], list) and 0 < len(plan['evidence']) <= 32, 'Bind explicit anchor evidence')
    for evidence in plan['evidence']:
        bound_file(evidence)
    require(len({item['path'] for item in plan['evidence']}) == len(plan['evidence']), 'Duplicate anchor evidence')
    require(isinstance(plan['cases'], list) and 0 < len(plan['cases']) <= 16, 'Expected one to sixteen alternatives')
    keys = set()
    for case in plan['cases']:
        require(isinstance(case, dict) and set(case) == {'key', 'at_sample', 'insert'}, 'Unexpected audition case')
        key = case['key']
        require(isinstance(key, str) and re.fullmatch('[a-z0-9][a-z0-9-]{0,79}', key)
            and key not in keys, 'Invalid or duplicate audition key')
        keys.add(key)
        require(type(case['at_sample']) is int and 0 < case['at_sample'] < source_format['samples'],
            'Insertion requires an integer sample anchor inside the complete source')
        bound_file(case['insert'])
        insert_format = wave_format(case['insert']['path'])
        require(all(insert_format[name] == source_format[name] for name in ('channels', 'sample_width', 'sample_rate')),
            'No implicit channel conversion or resampling is permitted')
        require(insert_format['samples'] <= source_format['sample_rate'] * 40,
            'Each complete insertion must be at most forty seconds')
        require(source_format['samples'] + insert_format['samples'] <= source_format['sample_rate'] * 8 * 3600,
            'Complete audition exceeds eight hours')
        require((source_format['samples'] + insert_format['samples']) * source_format['channels'] * 2 < 2**32 - 36,
            'Audition exceeds the RIFF size limit')
    return plan, {'plan': state.identity(plan_path), 'source_format': source_format,
        'implementations': {name: state.identity(path) for name, path in (
            ('insertion_audition', __file__), ('dubbing_state', state.__file__),
            ('prepare_videos', prepare_videos.__file__))}}


def copy_frames(source, target, count):
    while count:
        size = min(count, 65536)
        payload = source.readframes(size)
        require(len(payload) == size * source.getnchannels() * source.getsampwidth(), 'Incomplete source PCM')
        target.writeframesraw(payload)
        count -= size


def compare_frames(actual, expected, count, checksum):
    while count:
        size = min(count, 65536)
        actual_payload, expected_payload = actual.readframes(size), expected.readframes(size)
        require(len(expected_payload) == size * expected.getnchannels() * expected.getsampwidth()
            and actual_payload == expected_payload, 'Audition changed, deleted, padded or reordered PCM samples')
        checksum.update(actual_payload)
        count -= size


def inspect_case(plan, case, destination):
    source_format = wave_format(plan['source']['path'])
    insert_format = wave_format(case['insert']['path'])
    actual_format = wave_format(destination)
    expected_format = dict(source_format, samples=source_format['samples'] + insert_format['samples'])
    require(actual_format == expected_format, 'Audition format or complete sample count changed')
    checksum = hashlib.sha256()
    with wave.open(str(destination), 'rb') as actual, wave.open(plan['source']['path'], 'rb') as source, \
            wave.open(case['insert']['path'], 'rb') as insertion:
        compare_frames(actual, source, case['at_sample'], checksum)
        compare_frames(actual, insertion, insert_format['samples'], checksum)
        compare_frames(actual, source, source_format['samples'] - case['at_sample'], checksum)
        require(not actual.readframes(1), 'Unexpected trailing audition PCM')
    return {'key': case['key'], 'waveform': state.identity(destination), 'source_samples': source_format['samples'],
        'insert_samples': insert_format['samples'], 'output_samples': actual_format['samples'],
        'at_sample': case['at_sample'], 'sample_rate': source_format['sample_rate'],
        'pcm_sha256': checksum.hexdigest(), 'entire_source_before_and_after_exact': True,
        'complete_insert_exact': True, 'selected': False}


def check(plan_path, output):
    output = Path(output).resolve()
    plan, binding = inputs(plan_path)
    manifest = load(output / 'manifest.json')
    require(manifest.get('kind') == KIND and manifest.get('scope') == SCOPE and manifest.get('complete') is True
        and manifest.get('inputs') == binding and all(manifest.get(name) is False for name in UNAPPROVED),
        'Incomplete, stale or overclaimed audition manifest')
    require(len(manifest['cases']) == len(plan['cases']), 'Alternative inventory changed')
    for case, observed in zip(plan['cases'], manifest['cases']):
        require(observed == inspect_case(plan, case, output / f'{case["key"]}.wav'), 'Audition evidence changed')
    require({path.name for path in output.iterdir()} == {'manifest.json', 'audition.lock'}
        | {f'{case["key"]}.wav' for case in plan['cases']}, 'Unknown audition output files')
    require(inputs(plan_path) == (plan, binding), 'Inputs changed during audition verification')
    return {'kind': KIND, 'scope': SCOPE, 'manifest': state.identity(output / 'manifest.json'),
        'alternatives_checked': len(plan['cases']), 'entire_original_context_and_insertions_exact': True,
        **{name: False for name in UNAPPROVED}}


def render(plan_path, output, plan, binding):
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'audition.lock').open('xb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = {'kind': KIND, 'scope': SCOPE, 'created_at': state.now(), 'inputs': binding,
            'complete': False, 'cases': [], **{name: False for name in UNAPPROVED}}
        prepare_videos.save_json(output / 'manifest.json', manifest)
        try:
            for case in plan['cases']:
                destination = output / f'{case["key"]}.wav'
                with destination.open('xb') as handle, wave.open(handle, 'wb') as actual, \
                        wave.open(plan['source']['path'], 'rb') as source, wave.open(case['insert']['path'], 'rb') as insertion:
                    actual.setparams(source.getparams())
                    copy_frames(source, actual, case['at_sample'])
                    copy_frames(insertion, actual, insertion.getnframes())
                    copy_frames(source, actual, source.getnframes() - case['at_sample'])
                manifest['cases'].append(inspect_case(plan, case, destination))
                prepare_videos.save_json(output / 'manifest.json', manifest)
            require(inputs(plan_path) == (plan, binding), 'Inputs changed during audition generation')
            manifest['complete'] = True
        except BaseException as error:
            manifest['error'] = f'{type(error).__name__}: {error}'
            raise
        finally:
            prepare_videos.save_json(output / 'manifest.json', manifest)
        return check(plan_path, output)


def execute(action, plan_path, output):
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    plan, binding = inputs(plan_path)
    require(output != ROOT and ROOT not in output.parents, 'Audition output must stay outside the repository')
    bound_paths = [plan_path, Path(plan['source']['path']), *[Path(case['insert']['path']) for case in plan['cases']],
        *[Path(evidence['path']) for evidence in plan['evidence']]]
    require(all(output != path and output not in path.parents for path in bound_paths), 'Output cannot contain an input')
    for parent in (output, *output.parents):
        if parent.name.startswith('.zh_work_'):
            require(parent / '.managed_workflow' in output.parents, 'Audition output must be isolated from production')
    if action == 'render' and not output.exists():
        return render(plan_path, output, plan, binding)
    require(output.is_dir() and (output / 'audition.lock').is_file(), 'No existing audition to check')
    with (output / 'audition.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return check(plan_path, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('render', 'check'))
    parser.add_argument('plan')
    parser.add_argument('--output', required=True)
    arguments = parser.parse_args()
    try:
        print(json.dumps(execute(arguments.action, arguments.plan, arguments.output), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, wave.Error) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
