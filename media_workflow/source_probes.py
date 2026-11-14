"""Bounded, offline source-ASR observations; never approve translation or speakers."""

import argparse
import fcntl
import importlib.metadata
import importlib.util
import math
import os
import re
import shutil
import signal
import sys
import wave
from fractions import Fraction
from pathlib import Path

import dubbing_state as state
import prepare_videos
from media_workflow import native_audio


KIND = 'source-utterance-asr-probes-v1'
SAMPLE_RATE = 16000
MAX_BATCH_SECONDS = 180
require = native_audio.require
load = native_audio.load


def samples(value):
    require(isinstance(value, str) and re.fullmatch(r'\d+(?:\.\d+)?', value),
        'Use nonnegative decimal strings for reference seconds')
    result = Fraction(value) * SAMPLE_RATE
    require(result.denominator == 1, 'Window does not align to the diagnostic sample grid')
    return int(result)


def safe_key(value):
    return isinstance(value, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value)


def read_plan(path):
    path = Path(path).resolve()
    plan = load(path)
    require(isinstance(plan, dict) and set(plan) == {'kind', 'work', 'source', 'models',
        'probes', 'cpu_threads', 'reference_evidence', 'language', 'hotwords'}, 'Unexpected plan fields')
    require(plan['kind'] == KIND and plan['language'] == 'en' and plan['hotwords'] == '',
        'Only explicit English, no-hotword source observation is supported')
    work = Path(plan['work'])
    require(work.is_absolute() and work.resolve() == work and work.is_dir(), 'Use canonical absolute work')
    require(type(plan['cpu_threads']) is int and 1 <= plan['cpu_threads'] <= 8, 'Use1..8 CPU threads')
    source = plan['source']
    require(isinstance(source, dict) and set(source) == {'path', 'sha256', 'audio_stream'},
        'Bind one explicit source file, digest and zero-based audio stream')
    require(source['path'] == str(state.source_video(work)), 'Source is not the project original')
    require(isinstance(source['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', source['sha256']),
        'Expected source SHA-256')
    require(type(source['audio_stream']) is int and 0 <= source['audio_stream'] <= 31,
        'Invalid zero-based audio stream')
    models = plan['models']
    require(isinstance(models, dict) and 1 <= len(models) <= 3 and all(safe_key(key) for key in models),
        'Use1..3 unique local model keys')
    for directory in models.values():
        require(isinstance(directory, str) and Path(directory).is_absolute()
            and Path(directory).resolve() == Path(directory) and Path(directory).is_dir(),
            'Models must be existing canonical local directories, never downloadable names')
    require(isinstance(plan['reference_evidence'], list) and plan['reference_evidence'],
        'Bind authored source review evidence')
    for bound in plan['reference_evidence']:
        require(isinstance(bound, dict) and state.matches(bound), 'Stale authored reference')
    probes = plan['probes']
    require(isinstance(probes, list) and 1 <= len(probes) <= 24, 'Use1..24 bounded probes')
    maximum_unit = len(state.cues(work / 'en.srt'))
    keys = set()
    total_samples = 0
    for probe in probes:
        require(isinstance(probe, dict) and set(probe) == {'key', 'start', 'end', 'source_units', 'purpose'},
            'Unexpected probe fields')
        require(safe_key(probe['key']) and probe['key'] not in keys, 'Unsafe or duplicate probe key')
        keys.add(probe['key'])
        duration = samples(probe['end']) - samples(probe['start'])
        require(0 < duration <= 120 * SAMPLE_RATE, 'Each complete context must be within120 seconds')
        total_samples += duration
        units = probe['source_units']
        require(isinstance(units, list) and units and len(units) == len(set(units))
            and all(type(number) is int and 1 <= number <= maximum_unit for number in units),
            'Invalid immutable source-unit references')
        require(isinstance(probe['purpose'], str) and probe['purpose'].strip(), 'Describe the uncertainty')
    require(total_samples <= 1800 * SAMPLE_RATE, 'Bound total source context to1800 seconds')
    return path, plan


def model_inventory(directory):
    directory = Path(directory)
    entries = sorted(directory.iterdir())
    require(0 < len(entries) <= 64 and all(entry.is_file() for entry in entries),
        'Model directory must contain at most64 regular file entries')
    names = {entry.name for entry in entries}
    require({'model.bin', 'config.json', 'tokenizer.json'} <= names
        and ('vocabulary.txt' in names or 'vocabulary.json' in names), 'Incomplete local model')
    return {entry.name: state.identity(entry) for entry in entries}


def binding(path, plan):
    source = state.identity(plan['source']['path'])
    require(source['sha256'] == plan['source']['sha256'], 'Source digest changed')
    tools = {}
    for name in ['ffmpeg', 'ffprobe']:
        executable = shutil.which(name)
        require(executable is not None, f'Missing {name}')
        tools[name] = state.identity(executable)
    package = Path(importlib.util.find_spec('faster_whisper').origin).parent
    return {'plan': state.identity(path), 'source': source, 'tools': tools,
        'captions': state.identity(Path(plan['work']) / 'en.srt'),
        'references': plan['reference_evidence'],
        'models': {key: model_inventory(directory) for key, directory in plan['models'].items()},
        'implementations': {name: state.identity(filename) for name, filename in (
            ('source_probes', __file__), ('native_audio', native_audio.__file__),
            ('dubbing_state', state.__file__), ('prepare_videos', prepare_videos.__file__),
            ('asr', Path(__file__).resolve().parent.parent / 'asr.py'))},
        'python': state.identity(sys.executable),
        'recognizer_implementation': {name: state.identity(package / name)
            for name in ['__init__.py', 'transcribe.py', 'audio.py', 'vad.py']},
        'packages': {name: importlib.metadata.version(name) for name in
            ['faster-whisper', 'ctranslate2', 'numpy']}}


def wave_details(path):
    with wave.open(str(path), 'rb') as audio:
        require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype())
            == (1, 2, SAMPLE_RATE, 'NONE'), 'Expected complete mono PCM16 diagnostic reference at16kHz')
        frames = audio.getnframes()
        require(frames > 0, 'Empty reference')
        return frames


def cut_windows(plan, reference, output):
    frames = wave_details(reference)
    for probe in plan['probes']:
        require(samples(probe['end']) <= frames, 'Source context exceeds decoded reference; never pad')
    result = {}
    with wave.open(str(reference), 'rb') as audio:
        for probe in plan['probes']:
            first, last = samples(probe['start']), samples(probe['end'])
            audio.setpos(first)
            data = audio.readframes(last - first)
            require(len(data) == (last - first) * 2, 'Incomplete context; never trim or pad')
            path = output / (probe['key'] + '.wav')
            require(not path.exists(), 'Context already exists')
            with wave.open(str(path), 'wb') as target:
                target.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
                target.writeframes(data)
            result[probe['key']] = {'audio': state.identity(path), 'start_sample': first,
                'end_sample': last, 'samples': last - first}
    return result


def batches(probes):
    result, current, count = [], [], 0
    for probe in probes:
        duration = samples(probe['end']) - samples(probe['start'])
        if current and count + duration > MAX_BATCH_SECONDS * SAMPLE_RATE:
            result.append(current)
            current, count = [], 0
        current.append(probe['key'])
        count += duration
    if current:
        result.append(current)
    return result


def commands(path, plan, output, inputs):
    source = plan['source']
    result = {
        'source-probe': [inputs['tools']['ffprobe']['path'], '-v', 'error', '-select_streams',
            f'a:{source["audio_stream"]}', '-show_streams', '-of', 'json', source['path']],
        'reference-decode': [inputs['tools']['ffmpeg']['path'], '-nostdin', '-v', 'error', '-n',
            '-i', source['path'], '-map', f'0:a:{source["audio_stream"]}', '-vn', '-ac', '1',
            '-ar', str(SAMPLE_RATE), '-c:a', 'pcm_s16le', str(output / 'reference-mono16k.wav')]}
    for model_key in plan['models']:
        for number, group in enumerate(batches(plan['probes'])):
            result[f'asr-{model_key}-{number}'] = [sys.executable, '-m', 'media_workflow.source_probes',
                'worker', str(path), '--output', str(output), '--model-key', model_key,
                '--batch-number', str(number)]
    return result


def worker(path, output, model_key, batch_number):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    path, plan = read_plan(path)
    inputs = load(output / 'inputs.json')
    require(state.matches(inputs['plan']) and all(state.matches(bound)
        for bound in inputs['implementations'].values()), 'Worker plan or implementation changed')
    require(all(state.matches(bound) for bound in inputs['recognizer_implementation'].values()),
        'Recognizer implementation changed')
    require(model_key in plan['models'] and model_inventory(plan['models'][model_key])
        == inputs['models'][model_key], 'Worker model changed')
    windows = load(output / 'windows.json')
    group = batches(plan['probes'])[batch_number]
    import asr
    import numpy
    from faster_whisper import WhisperModel
    model = WhisperModel(plan['models'][model_key], device='cpu', compute_type='int8',
        cpu_threads=plan['cpu_threads'], num_workers=1, local_files_only=True)
    for key in group:
        destination = output / f'{model_key}-{key}.json'
        require(not destination.exists(), 'Never overwrite a successful or partial observation')
        observation = windows[key]
        require(state.matches(observation['audio']), 'Context audio changed')
        with wave.open(observation['audio']['path'], 'rb') as audio:
            data = audio.readframes(audio.getnframes())
        waveform = numpy.frombuffer(data, dtype='<i2').astype(numpy.float32) / 32768.0
        words, sentences, info = asr.transcribe(model, waveform, '')
        entries = [{'start': float(word.start), 'end': float(word.end),
            'word': word.word, 'probability': float(word.probability)} for word in words]
        require(all(all(math.isfinite(entry[name]) for name in ['start', 'end', 'probability'])
            for entry in entries), 'Non-finite ASR output')
        prepare_videos.save_json(destination, {'model': model_key, 'probe': key,
            'window': observation, 'language': info.language,
            'language_probability': float(info.language_probability), 'words': entries,
            'sentences': sentences, 'text': ' '.join(text for first, last, text in sentences),
            'word_times_relative_to_diagnostic_window': True,
            'not_diarization_or_acoustic_boundary_approval': True,
            'hotwords': '', 'model_files': inputs['models'][model_key]})
        print(f'{model_key}/{key}: {len(words)} words', flush=True)


def check(path, plan, output):
    inputs = load(output / 'inputs.json')
    require(binding(path, plan) == inputs, 'Source, plan, model, dependency version or implementation changed')
    reference = load(output / 'reference.json')
    require(state.matches(reference['audio']) and reference['source'] == inputs['source']
        and reference['audio_stream'] == plan['source']['audio_stream'], 'Diagnostic reference changed')
    require(wave_details(Path(reference['audio']['path'])) == reference['samples'], 'Reference length changed')
    windows = load(output / 'windows.json')
    require(set(windows) == {probe['key'] for probe in plan['probes']}, 'Incomplete context set')
    with wave.open(reference['audio']['path'], 'rb') as full:
        for probe in plan['probes']:
            key = probe['key']
            item = windows[key]
            first, last = samples(probe['start']), samples(probe['end'])
            require((item['start_sample'], item['end_sample'], item['samples']) ==
                (first, last, last - first) and state.matches(item['audio']), 'Context identity/grid changed')
            full.setpos(first)
            with wave.open(item['audio']['path'], 'rb') as context:
                require(wave_details(Path(item['audio']['path'])) == last - first
                    and context.readframes(last - first) == full.readframes(last - first),
                    'Context is not the complete declared diagnostic-reference slice')
    expected_commands = commands(path, plan, output, inputs)
    for name, command in expected_commands.items():
        receipt = load(output / f'{name}-process.json')
        require(receipt['status'] == 'complete' and receipt['returncode'] == 0
            and receipt['command'] == command, f'Unproven process: {name}')
    observations = {}
    for model_key in plan['models']:
        for probe in plan['probes']:
            key = probe['key']
            filename = output / f'{model_key}-{key}.json'
            result = load(filename)
            require(result['model'] == model_key and result['probe'] == key
                and result['window'] == windows[key] and result['model_files'] == inputs['models'][model_key]
                and result['hotwords'] == '' and result['not_diarization_or_acoustic_boundary_approval'] is True,
                'Observation belongs to different inputs')
            require(isinstance(result['words'], list) and isinstance(result['sentences'], list)
                and result['text'] == ' '.join(item[2] for item in result['sentences']), 'Malformed ASR observation')
            require(all(isinstance(entry, dict) and set(entry) == {'start', 'end', 'word', 'probability'}
                and isinstance(entry['word'], str) and all(type(entry[field]) in (int, float)
                    and math.isfinite(entry[field]) for field in ['start', 'end', 'probability'])
                for entry in result['words']), 'Malformed or non-finite word observation')
            observations[filename.name] = state.identity(filename)
    require(observations == load(output / 'observations.json'), 'Observation content changed')
    return {'kind': KIND, 'reference_samples': reference['samples'],
        'probes': len(windows), 'models': len(plan['models']), 'observations': len(observations),
        'processes_verified': len(expected_commands), 'speaker_or_translation_approved': False,
        'human_listened': False, 'native_signal_or_production_alignment_claimed': False}


def execute(action, path, output):
    path, plan = read_plan(path)
    output = Path(output).resolve()
    work = Path(plan['work'])
    require(not output.is_relative_to(work) and not work.is_relative_to(output),
        'Use a separate diagnostic output, not the production project')
    with (work / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        if action == 'check':
            return check(path, plan, output)
        require(not output.exists(), 'Output exists; do not repeat or overwrite prior observations')
        inputs = binding(path, plan)
        output.mkdir(parents=True)
        prepare_videos.save_json(output / 'inputs.json', inputs)
        try:
            source = plan['source']
            selected_commands = commands(path, plan, output, inputs)
            native_audio.run_process(selected_commands['source-probe'],
                output, 'source-probe', 'source-probe.json', lock.fileno())
            probe = load(output / 'source-probe.json')
            require(len(probe.get('streams', [])) == 1 and probe['streams'][0]['codec_type'] == 'audio',
                'Selected audio stream is unavailable')
            require(Fraction(probe['streams'][0].get('start_time', 'unknown')) == 0,
                'Nonzero/unknown source origin requires an explicit timeline adapter')
            reference_path = output / 'reference-mono16k.wav'
            native_audio.run_process(selected_commands['reference-decode'],
                output, 'reference-decode', 'reference-decode.stdout.log', lock.fileno())
            prepare_videos.save_json(output / 'reference.json', {'audio': state.identity(reference_path),
                'samples': wave_details(reference_path), 'sample_rate': SAMPLE_RATE,
                'source': inputs['source'], 'audio_stream': source['audio_stream'],
                'not_native_rate_or_channels_or_gain_preserving': True})
            windows = cut_windows(plan, reference_path, output)
            prepare_videos.save_json(output / 'windows.json', windows)
            for model_key in plan['models']:
                for number, group in enumerate(batches(plan['probes'])):
                    native_audio.run_process(selected_commands[f'asr-{model_key}-{number}'],
                        output, f'asr-{model_key}-{number}',
                        f'asr-{model_key}-{number}.stdout.log', lock.fileno())
            observations = {filename.name: state.identity(filename) for model_key in plan['models']
                for probe in plan['probes'] for filename in [output / f'{model_key}-{probe["key"]}.json']}
            prepare_videos.save_json(output / 'observations.json', observations)
            result = check(path, plan, output)
            prepare_videos.save_json(output / 'verification.json', result)
            return result
        except BaseException as error:
            prepare_videos.save_json(output / 'failure.json', {'error': f'{type(error).__name__}: {error}',
                'finished_at': state.now(), 'partial_outputs_retained': True})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate', 'run', 'check', 'worker'])
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--model-key')
    parser.add_argument('--batch-number', type=int)
    arguments = parser.parse_args()
    if arguments.action == 'validate':
        path, plan = read_plan(arguments.plan)
        result = {'valid': True, 'probes': len(plan['probes']), 'batches': len(batches(plan['probes']))}
    else:
        if arguments.output is None:
            parser.error('--output is required')
        if arguments.action == 'worker':
            if arguments.model_key is None or arguments.batch_number is None or arguments.batch_number < 0:
                parser.error('worker requires model key and nonnegative batch number')
            worker(arguments.plan, arguments.output.resolve(), arguments.model_key, arguments.batch_number)
            return
        signal.signal(signal.SIGTERM, native_audio.interrupted)
        signal.signal(signal.SIGINT, native_audio.interrupted)
        result = execute(arguments.action, arguments.plan, arguments.output)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
