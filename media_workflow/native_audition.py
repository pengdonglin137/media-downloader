"""Export complete native audio frames for unresolved-source listening, without promotion."""

import argparse
import bisect
import fcntl
import hashlib
import html
import json
import re
import signal
import struct
import subprocess
from pathlib import Path

import dubbing_state as state
import prepare_videos
from . import native_audio


KIND = 'native-source-audition-windows-v1'
ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1024 ** 3
require = native_audio.require


def configuration(path):
    path = Path(path).resolve()
    require(path.stat().st_size <= 512 * 1024, 'Audition plan exceeds512KiB')
    plan = native_audio.load(path)
    require(isinstance(plan, dict) and set(plan) ==
        {'kind', 'observation_plan', 'observation_report', 'windows', 'evidence'}
        and plan['kind'] == KIND, 'Unsupported native audition plan')
    require(isinstance(plan['evidence'], list) and len(plan['evidence']) <= 64, 'Invalid evidence list')
    for identity in [plan['observation_plan'], plan['observation_report'], *plan['evidence']]:
        require(isinstance(identity, dict) and state.matches(identity), 'Changed audition evidence')
    original_path, original_plan = native_audio.read_plan(plan['observation_plan']['path'])
    report_path = Path(plan['observation_report']['path'])
    require(report_path.name == 'report.json', 'Expected an existing native observation report')
    report = native_audio.check(original_path, original_plan, report_path.parent)
    observed = report['observation']
    require(observed['first_pts'] == 0 and observed['discontinuities'] == 0
        and observed['gap_samples'] == observed['overlap_samples'] == 0,
        'Auditions require an observed continuous native timeline starting at zero')
    windows = plan['windows']
    require(isinstance(windows, list) and 0 < len(windows) <= 128, 'Expected1..128 audition windows')
    names = set()
    for window in windows:
        require(isinstance(window, dict) and set(window) ==
            {'id', 'start_sample', 'end_sample', 'label', 'entries'}, 'Invalid audition window fields')
        name = window['id']
        require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name)
            and name not in names, 'Unsafe or duplicate audition ID')
        names.add(name)
        require(type(window['start_sample']) is int and type(window['end_sample']) is int
            and 0 <= window['start_sample'] < window['end_sample'] <= observed['last_frame_end_pts'],
            'Window samples must be explicit native integers within the observed audio')
        require(isinstance(window['label'], str) and 0 < len(window['label']) <= 200, 'Invalid window label')
        require(isinstance(window['entries'], list) and 0 < len(window['entries']) <= 128,
            'Expected contextual text entries')
        for entry in window['entries']:
            require(isinstance(entry, dict) and set(entry) == {'id', 'en', 'zh', 'note'}
                and all(isinstance(value, str) and len(value) <= 20000 for value in entry.values()),
                'Invalid contextual text entry')
    inputs = {'plan': state.identity(path), 'observation_plan': plan['observation_plan'],
        'observation_report': plan['observation_report'], 'source': report['inputs']['source'],
        'ledger': report['artifacts']['decoded.framehash'], 'evidence': plan['evidence'],
        'ffmpeg': report['inputs']['tools']['ffmpeg'], 'adapter': state.identity(__file__),
        'native_adapter': state.identity(native_audio.__file__)}
    return path, plan, report, inputs


def ledger_rows(path):
    rows = []
    with Path(path).open(encoding='ascii') as handle:
        for line in handle:
            if line.strip() and not line.startswith('#'):
                fields = [field.strip() for field in line.split(',')]
                rows.append((*map(int, fields[:5]), fields[5]))
    require(rows, 'Empty verified native ledger')
    return rows


def align_windows(plan, report, rows):
    starts = [row[2] for row in rows]
    ends = [row[2] + row[3] for row in rows]
    channels = report['observation']['channels']
    aligned = []
    for window in plan['windows']:
        first = bisect.bisect_right(starts, window['start_sample']) - 1
        last = bisect.bisect_left(ends, window['end_sample'])
        require(0 <= first <= last < len(rows), 'Cannot enclose the requested window')
        item = {'id': window['id'], 'first_frame_index': first, 'last_frame_index': last,
            'requested_start_sample': window['start_sample'], 'requested_end_sample': window['end_sample'],
            'start_sample': starts[first], 'end_sample': ends[last], 'frames': last - first + 1}
        item['payload_bytes'] = (item['end_sample'] - item['start_sample']) * channels * 4
        require(item['payload_bytes'] + 36 < 2 ** 32, 'Window exceeds WAV32 size')
        aligned.append(item)
    require(sum(item['payload_bytes'] + 44 for item in aligned) <= MAX_BYTES, 'Audition output exceeds1GiB')
    return aligned


def wave_header(payload_bytes, sample_rate, channels):
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', payload_bytes + 36, b'WAVE', b'fmt ',
        16, 3, channels, sample_rate, sample_rate * channels * 4, channels * 4, 32, b'data', payload_bytes)


def decode_command(plan, inputs, rows, aligned):
    last_index = max(item['last_frame_index'] for item in aligned)
    stop = rows[last_index][2] + rows[last_index][3]
    original_plan = native_audio.load(plan['observation_plan']['path'])
    return [inputs['ffmpeg']['path'], '-nostdin', '-v', 'error', '-xerror', '-copyts',
        '-i', inputs['source']['path'], '-map', f'0:a:{original_plan["source"]["audio_stream"]}',
        '-vn', '-sn', '-dn', '-af', f'atrim=end_sample={stop}', '-c:a', 'pcm_f32le', '-f', 'f32le', '-']


def read_exact(handle, count):
    pieces = []
    remaining = count
    while remaining:
        payload = handle.read(remaining)
        require(payload, 'Decoder ended before the complete verified frame')
        pieces.append(payload)
        remaining -= len(payload)
    return b''.join(pieces)


def verify_wave(path, window, rows, sample_rate, channels):
    require(Path(path).stat().st_size == window['payload_bytes'] + 44, 'Incomplete or expanded native WAV')
    with Path(path).open('rb') as handle:
        require(handle.read(44) == wave_header(window['payload_bytes'], sample_rate, channels), 'Native WAV header changed')
        for row in rows[window['first_frame_index']:window['last_frame_index'] + 1]:
            require(hashlib.sha256(read_exact(handle, row[4])).hexdigest() == row[5],
                f'Exported native audio frame changed at sample{row[2]}')
        require(not handle.read(1), 'Unexpected extra native audio')


def page(plan, aligned, sample_rate):
    sections = []
    for window, actual in zip(plan['windows'], aligned):
        entries = ''.join('<p><strong>' + html.escape(entry['id']) + '</strong> ' +
            html.escape(entry['note']) + '</p><p lang="en">' + html.escape(entry['en']) +
            '</p><p>' + html.escape(entry['zh']) + '</p>' for entry in window['entries'])
        sections.append('<section><h2>' + html.escape(window['label']) + '</h2><p>原片原生样本 [' +
            str(actual['start_sample']) + ', ' + str(actual['end_sample']) + ') / ' + str(sample_rate) +
            ' Hz；包含完整前后文，不是已批准的交接窗。</p><audio controls preload="none" src="' +
            window['id'] + '.wav"></audio><p><a href="' + window['id'] +
            '.wav">打开完整原生浮点WAV</a>（浏览器支持及人工听辨尚待验证）</p>' + entries + '</section>')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>原声待核完整语境</title>' +
        '<style>body{max-width:960px;margin:2rem auto;padding:1rem;font:18px/1.7 sans-serif}' +
        'section{border-top:1px solid #bbb;padding:1rem 0}audio{width:100%}p{white-space:pre-wrap}</style>' +
        '<h1>原声待核完整语境</h1><p>中文仅为待核阅读候选。没有选择说话人、批准声学词尾、' +
        '缩小交接窗、静音、删除或重合成原发言；没有写入人耳决定。WAV保留原采样率、' +
        '声道和完整float32字节，不调增益、不重采样。</p>' + ''.join(sections) + '</html>\n')


def check(plan_path, output):
    _, plan, report, inputs = configuration(plan_path)
    output = Path(output).resolve()
    receipt = native_audio.load(output / 'manifest.json')
    require(receipt.get('kind') == KIND and receipt.get('complete') is True and receipt.get('inputs') == inputs,
        'Incomplete or stale audition; preserve it and use a fresh directory')
    rows = ledger_rows(inputs['ledger']['path'])
    aligned = align_windows(plan, report, rows)
    require(receipt['windows'] == aligned, 'Audition sample mapping changed')
    require(receipt['command'] == decode_command(plan, inputs, rows, aligned)
        and receipt['verified_decoded_native_frames'] == max(item['last_frame_index'] for item in aligned) + 1,
        'Audition decoder command or verified prefix changed')
    require(receipt['human_listened'] is False and receipt['speaker_or_acoustic_end_approved'] is False
        and receipt['production_installed_or_pronunciation_emotion_approved'] is False,
        'An audition cannot promote source, voice or production approval')
    require(receipt['process']['terminal'] is True and type(receipt['process']['returncode']) is int
        and receipt['process']['returncode'] == 0, 'Missing actual successful decoder exit')
    names = {'manifest.json', 'audition.lock', 'decode.stderr.log', 'index.html'} | {
        window['id'] + '.wav' for window in aligned}
    require({path.name for path in output.iterdir()} == names, 'Unexpected audition artifacts')
    require(set(receipt['artifacts']) == names - {'manifest.json', 'audition.lock'}, 'Incomplete artifact inventory')
    for name, identity in receipt['artifacts'].items():
        require(identity['path'] == str(output / name) and state.matches(identity), 'Audition artifact changed')
    require((output / 'decode.stderr.log').stat().st_size == 0, 'Native extraction emitted diagnostics')
    observation = report['observation']
    for window in aligned:
        verify_wave(output / (window['id'] + '.wav'), window, rows,
            observation['sample_rate'], observation['channels'])
    require((output / 'index.html').read_text() == page(plan, aligned, observation['sample_rate']), 'Audition page changed')
    return {'passed': True, 'manifest': state.identity(output / 'manifest.json'), 'windows': len(aligned),
        'exported_native_frames_checked': sum(item['frames'] for item in aligned),
        'reused_without_decode': True, 'human_listened': False, 'speaker_or_acoustic_end_approved': False,
        'production_installed_or_pronunciation_emotion_approved': False}


def extract(plan_path, output):
    path, plan, report, inputs = configuration(plan_path)
    output = Path(output).resolve()
    require(ROOT not in (output, *output.parents)
        and not any(parent.name.startswith('.zh_work_') for parent in (output, *output.parents)),
        'Auditions must remain outside repository and canonical work directories')
    require(all(output != Path(identity['path']) and output not in Path(identity['path']).parents
        for identity in [inputs['plan'], inputs['source'], inputs['ledger'], *plan['evidence']]),
        'Output cannot contain an input')
    if output.exists():
        with (output / 'audition.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return check(path, output)
    rows = ledger_rows(inputs['ledger']['path'])
    aligned = align_windows(plan, report, rows)
    last_index = max(item['last_frame_index'] for item in aligned)
    command = decode_command(plan, inputs, rows, aligned)
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'audition.lock').open('xb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = {'kind': KIND, 'inputs': inputs, 'windows': aligned, 'complete': False,
            'command': command, 'decoding_policy': 'one sequential extraction, no input seeking',
            'requested_windows_are_not_acoustic_turn_boundaries': True, 'human_listened': False,
            'speaker_or_acoustic_end_approved': False,
            'production_installed_or_pronunciation_emotion_approved': False}
        prepare_videos.save_json(output / 'manifest.json', receipt)
        process = None
        handles = []
        previous_handler = signal.signal(signal.SIGTERM, native_audio.interrupted)
        try:
            observation = report['observation']
            for window in aligned:
                handle = (output / (window['id'] + '.wav')).open('xb')
                handles.append(handle)
                handle.write(wave_header(window['payload_bytes'], observation['sample_rate'], observation['channels']))
            with (output / 'decode.stderr.log').open('xb') as log:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, pass_fds=(lock.fileno(),))
                receipt['process'] = {'pid': process.pid, 'terminal': False}
                prepare_videos.save_json(output / 'manifest.json', receipt)
                for index, row in enumerate(rows[:last_index + 1]):
                    payload = read_exact(process.stdout, row[4])
                    require(hashlib.sha256(payload).hexdigest() == row[5],
                        f'Sequential native frame differs at sample{row[2]}')
                    for window, handle in zip(aligned, handles):
                        if window['first_frame_index'] <= index <= window['last_frame_index']:
                            handle.write(payload)
                require(not process.stdout.read(1), 'Decoder produced more than the bounded complete frame prefix')
                require(process.wait() == 0, 'Native audition extraction failed')
            require((output / 'decode.stderr.log').stat().st_size == 0, 'Native extraction emitted diagnostics')
            for handle in handles:
                handle.close()
            require(configuration(path)[3] == inputs, 'Audition inputs changed during extraction')
            for window in aligned:
                verify_wave(output / (window['id'] + '.wav'), window, rows,
                    observation['sample_rate'], observation['channels'])
            (output / 'index.html').write_text(page(plan, aligned, observation['sample_rate']))
            receipt['verified_decoded_native_frames'] = last_index + 1
            receipt['artifacts'] = {item.name: state.identity(item) for item in output.iterdir()
                if item.name not in ('manifest.json', 'audition.lock')}
            receipt['complete'] = True
        except BaseException as error:
            receipt['error'] = f'{type(error).__name__}: {error}'
            raise
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                if process.stdout is not None:
                    process.stdout.close()
                receipt['process'] = {'pid': process.pid, 'terminal': True, 'returncode': process.returncode}
            for handle in handles:
                handle.close()
            signal.signal(signal.SIGTERM, previous_handler)
            prepare_videos.save_json(output / 'manifest.json', receipt)
        result = check(path, output)
        result['reused_without_decode'] = False
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('extract', 'check'))
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(globals()[args.action](args.plan, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
