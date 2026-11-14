"""Observe a complete native-rate audio decode without producing delivery media."""

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import dubbing_state as state
import prepare_videos


KIND = 'native-audio-observation-v1'
ROOT = Path(__file__).resolve().parents[1]
MAX_LEDGER_BYTES = 256 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f'Non-finite JSON number: {value}')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_object,
        parse_constant=reject_constant)


def read_plan(path):
    path = Path(path).resolve()
    plan = load(path)
    require(isinstance(plan, dict) and set(plan) == {'version', 'source'}
        and type(plan['version']) is int and plan['version'] == 1, 'Unsupported native-audio plan')
    source = plan['source']
    require(isinstance(source, dict) and set(source) == {'path', 'sha256', 'audio_stream'},
        'Source requires only path, sha256 and audio_stream')
    require(isinstance(source['path'], str) and '\x00' not in source['path']
        and Path(source['path']).is_absolute(), 'Source must be an absolute local path')
    source_path = Path(source['path']).resolve()
    require(source_path.is_file(), 'Source is not a regular file')
    require(isinstance(source['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', source['sha256']),
        'Source requires an explicit lowercase SHA-256')
    require(type(source['audio_stream']) is int and 0 <= source['audio_stream'] <= 31,
        'audio_stream must be a zero-based integer from 0 to 31')
    source['path'] = str(source_path)
    return path, plan


def binding(plan_path, plan):
    tools = {}
    for name in ('ffmpeg', 'ffprobe'):
        executable = shutil.which(name)
        require(executable is not None, f'Missing {name}')
        tools[name] = state.identity(executable)
    source = state.identity(plan['source']['path'])
    require(source['sha256'] == plan['source']['sha256'], 'Source SHA-256 does not match the plan')
    return {'plan': state.identity(plan_path), 'source': source, 'tools': tools,
        'implementations': {name: state.identity(filename) for name, filename in (
            ('native_audio', __file__), ('dubbing_state', state.__file__),
            ('prepare_videos', prepare_videos.__file__))}}


def rational(value):
    return {'numerator': value.numerator, 'denominator': value.denominator}


def native_stream(probe):
    streams = probe.get('streams')
    require(isinstance(streams, list) and len(streams) == 1, 'Expected exactly one selected audio stream')
    stream = streams[0]
    require(stream.get('codec_type') == 'audio', 'Selected stream is not audio')
    rate = int(stream['sample_rate'])
    channels = stream['channels']
    require(rate > 0 and type(channels) is int and channels > 0, 'Unknown native rate or channel count')
    time_base = Fraction(stream['time_base'])
    require(time_base > 0, 'Invalid source time base')
    for field in ('start_pts', 'duration_ts'):
        require(field not in stream or type(stream[field]) is int, f'Invalid source {field}')
    require('duration_ts' not in stream or stream['duration_ts'] >= 0, 'Negative declared duration')
    return stream, rate, channels, time_base


def parse_ledger(path, probe):
    stream, rate, channels, source_time_base = native_stream(probe)
    require(0 < Path(path).stat().st_size <= MAX_LEDGER_BYTES, 'Framehash ledger exceeds the finite limit')
    headers = {}
    count = samples = payload_bytes = gaps = overlaps = discontinuities = 0
    first_pts = last_end = minimum_pts = maximum_end = None
    examples = []
    with Path(path).open(encoding='ascii') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                if ':' in line:
                    key, value = line[1:].split(':', 1)
                    key, value = key.strip(), value.strip()
                    require(key not in headers, f'Duplicate ledger header: {key}')
                    headers[key] = value
                continue
            require(headers.get('format') == 'frame checksums' and headers.get('version') == '2'
                and headers.get('hash') == 'SHA256' and headers.get('media_type 0') == 'audio'
                and headers.get('codec_id 0') == 'pcm_f32le'
                and headers.get('sample_rate 0') == str(rate)
                and Fraction(headers.get('tb 0', '0')) == Fraction(1, rate),
                'Ledger does not describe native-rate float32 audio')
            fields = [field.strip() for field in line.split(',')]
            require(len(fields) == 6 and re.fullmatch(r'[0-9a-f]{64}', fields[5]), 'Malformed framehash row')
            stream_id, dts, pts, duration, size = map(int, fields[:5])
            require(stream_id == 0 and dts == pts and duration > 0 and size == duration * channels * 4,
                'Unexpected stream, timestamp, duration or native channel byte count')
            if first_pts is None:
                first_pts = minimum_pts = pts
            if last_end is not None and pts != last_end:
                delta = pts - last_end
                discontinuities += 1
                gaps += max(delta, 0)
                overlaps += max(-delta, 0)
                if len(examples) < 32:
                    examples.append({'frame': count, 'previous_end_pts': last_end, 'pts': pts, 'delta': delta})
            last_end = pts + duration
            minimum_pts = min(minimum_pts, pts)
            maximum_end = last_end if maximum_end is None else max(maximum_end, last_end)
            count += 1
            samples += duration
            payload_bytes += size
    require(count > 0, 'No decoded audio frames observed')
    declared_start = (stream['start_pts'] * source_time_base) if 'start_pts' in stream else None
    declared_duration = (stream['duration_ts'] * source_time_base) if 'duration_ts' in stream else None
    declared_end = declared_start + declared_duration if declared_start is not None and declared_duration is not None else None
    return {'stream_index': stream['index'], 'codec_name': stream.get('codec_name'),
        'sample_rate': rate, 'channels': channels, 'pcm_codec': 'pcm_f32le', 'frames': count,
        'decoded_sample_frames': samples, 'decoded_payload_bytes': payload_bytes,
        'sample_count_duration_seconds': rational(Fraction(samples, rate)),
        'first_pts': first_pts, 'last_frame_end_pts': last_end, 'minimum_pts': minimum_pts,
        'maximum_end_pts': maximum_end, 'decoded_time_base': rational(Fraction(1, rate)),
        'maximum_end_seconds': rational(Fraction(maximum_end, rate)),
        'discontinuities': discontinuities, 'gap_samples': gaps, 'overlap_samples': overlaps,
        'discontinuity_examples': examples, 'discontinuity_examples_truncated': discontinuities > len(examples),
        'declared_start_seconds': rational(declared_start) if declared_start is not None else None,
        'declared_duration_seconds': rational(declared_duration) if declared_duration is not None else None,
        'declared_end_seconds': rational(declared_end) if declared_end is not None else None,
        'decoded_maximum_end_minus_declared_end_seconds': rational(Fraction(maximum_end, rate) - declared_end)
            if declared_end is not None else None,
        'audible_speech_end_verified': False, 'padding_or_silence_classified': False,
        'production_or_human_hearing_approved': False}


class Interrupted(Exception):
    def __init__(self, number):
        self.number = number
        super().__init__(f'Interrupted by signal {number}')


def interrupted(number, frame):
    raise Interrupted(number)


def run_process(command, output, name, stdout_name, lock_descriptor):
    receipt_path = output / f'{name}-process.json'
    receipt = {'command': command, 'started_at': state.now(), 'returncode': None, 'status': 'running'}
    prepare_videos.save_json(receipt_path, receipt)
    process = None
    try:
        with (output / stdout_name).open('xb') as stdout, (output / f'{name}.stderr.log').open('xb') as stderr:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                start_new_session=True, pass_fds=(lock_descriptor,))
            receipt['pid'] = process.pid
            prepare_videos.save_json(receipt_path, receipt)
            process.wait(timeout=1200)
        require(process.returncode == 0, f'{name} exited {process.returncode}')
        receipt['status'] = 'complete'
    except BaseException as error:
        receipt['status'] = 'interrupted' if isinstance(error, Interrupted) else 'failed'
        receipt['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            receipt['returncode'] = process.returncode
        receipt['finished_at'] = state.now()
        prepare_videos.save_json(receipt_path, receipt)


def commands(inputs, plan):
    source = plan['source']
    selector = f"a:{source['audio_stream']}"
    return {'probe': [inputs['tools']['ffprobe']['path'], '-v', 'error', '-select_streams', selector,
        '-show_streams', '-show_program_version', '-of', 'json', source['path']],
        'decode': [inputs['tools']['ffmpeg']['path'], '-nostdin', '-v', 'error', '-xerror', '-copyts',
            '-i', source['path'], '-map', f'0:{selector}', '-vn', '-sn', '-dn', '-c:a', 'pcm_f32le',
            '-f', 'framehash', '-hash', 'sha256', '-']}


def check(plan_path, plan, output):
    report_path = output / 'report.json'
    report = load(report_path)
    inputs = binding(plan_path, plan)
    require(report.get('kind') == KIND and report.get('status') == 'complete'
        and report.get('inputs') == inputs, 'Observation is incomplete or its inputs changed')
    expected_commands = commands(inputs, plan)
    expected_artifacts = {'inputs.json', 'probe.json', 'probe-process.json', 'probe.stderr.log', 'decoded.framehash',
        'decode-process.json', 'decode.stderr.log'}
    require(isinstance(report.get('artifacts'), dict) and set(report['artifacts']) == expected_artifacts,
        'Observation artifact inventory is incomplete')
    for name, observation in report['artifacts'].items():
        require(observation.get('path') == str(output / name) and state.matches(observation),
            f'Changed observation artifact: {name}')
    for name, command in expected_commands.items():
        receipt = load(output / f'{name}-process.json')
        require(receipt.get('command') == command and type(receipt.get('returncode')) is int
            and receipt['returncode'] == 0 and receipt.get('status') == 'complete',
            f'Missing actual successful {name} exit')
    require(report['observation'] == parse_ledger(output / 'decoded.framehash', load(output / 'probe.json')),
        'Native audio observation does not match its ledger')
    return report


def execute(mode, plan_path, output):
    plan_path, plan = read_plan(plan_path)
    output = Path(output).resolve()
    require(output != ROOT and ROOT not in output.parents, 'Observation output must be outside the repository')
    require(all(output != path and output not in path.parents for path in (plan_path, Path(plan['source']['path']))),
        'Output cannot contain the plan or source')
    for parent in (output, *output.parents):
        if parent.name.startswith('.zh_work_'):
            require(parent / '.managed_workflow' in output.parents, 'Work output requires .managed_workflow isolation')
    if mode == 'check':
        require(output.is_dir() and (output / 'observation.lock').is_file(), 'No existing observation to check')
        lock_mode = 'rb'
    else:
        output.mkdir(parents=True, exist_ok=True)
        require(not any(output.iterdir()) or (output / 'observation.lock').is_file(), 'Unknown nonempty output directory')
        lock_mode = 'a+b'
    with (output / 'observation.lock').open(lock_mode) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if mode == 'check' or (output / 'report.json').exists():
            report = check(plan_path, plan, output)
            return {'status': 'verified-existing', 'decode_executed': False, 'report': state.identity(output / 'report.json'),
                'observation': report['observation']}
        require(set(output.iterdir()) == {output / 'observation.lock'},
            'Incomplete attempt retained; use a fresh output directory instead of overwriting evidence')
        inputs = binding(plan_path, plan)
        prepare_videos.save_json(output / 'inputs.json', inputs)
        selected = commands(inputs, plan)
        run_process(selected['probe'], output, 'probe', 'probe.json', lock.fileno())
        native_stream(load(output / 'probe.json'))
        run_process(selected['decode'], output, 'decode', 'decoded.framehash', lock.fileno())
        observation = parse_ledger(output / 'decoded.framehash', load(output / 'probe.json'))
        require(inputs == binding(plan_path, plan), 'Inputs changed during native audio observation')
        artifacts = {name: state.identity(output / name) for name in ('inputs.json', 'probe.json', 'probe-process.json',
            'probe.stderr.log', 'decoded.framehash', 'decode-process.json', 'decode.stderr.log')}
        prepare_videos.save_json(output / 'report.json', {'kind': KIND, 'status': 'complete',
            'recorded_at': state.now(), 'inputs': inputs, 'artifacts': artifacts, 'observation': observation})
        return {'status': 'observed', 'decode_executed': True, 'report': state.identity(output / 'report.json'),
            'observation': observation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('observe', 'check'))
    parser.add_argument('plan')
    parser.add_argument('--output', required=True)
    options = parser.parse_args()
    previous = {number: signal.signal(number, interrupted) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        result = execute(options.mode, options.plan, options.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Interrupted as error:
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
