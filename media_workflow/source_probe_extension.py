"""Add new lexical observations using a verified diagnostic reference, without decoding again."""

import argparse
import fcntl
import math
import signal
import wave
from pathlib import Path

import dubbing_state as state
import prepare_videos
from media_workflow import native_audio
from media_workflow import source_probes as core


KIND = 'source-utterance-asr-extension-v1'
require = core.require
load = core.load


def parent_binding(path, plan, reference):
    reference = Path(reference).resolve(strict=True)
    require(reference.is_dir(), 'Reference must be an existing successful probe directory')
    previous_inputs = load(reference / 'inputs.json')
    previous_path, previous = core.read_plan(previous_inputs['plan']['path'])
    proof = core.check(previous_path, previous, reference)
    for field in ['work', 'source', 'models', 'cpu_threads', 'language', 'hotwords']:
        require(plan[field] == previous[field], f'Parent observation profile differs: {field}')
    previous_keys = {probe['key'] for probe in previous['probes']}
    previous_windows = {(core.samples(probe['start']), core.samples(probe['end']))
        for probe in previous['probes']}
    previous_units = {number for probe in previous['probes'] for number in probe['source_units']}
    for probe in plan['probes']:
        require(probe['key'] not in previous_keys, 'Do not repeat a successful probe key')
        require((core.samples(probe['start']), core.samples(probe['end'])) not in previous_windows,
            'Do not repeat a successful diagnostic window')
        require(not previous_units.intersection(probe['source_units']),
            'Do not repeat already observed source-unit targets')
    current_inputs = core.binding(path, plan)
    for field in set(current_inputs) - {'plan', 'references'}:
        require(current_inputs[field] == previous_inputs[field], f'Parent inputs differ: {field}')
    entries = sorted(reference.iterdir())
    require(0 < len(entries) <= 256 and all(entry.is_file() and not entry.is_symlink()
        for entry in entries), 'Parent must contain only bounded regular observation files')
    return current_inputs, {'kind': KIND, 'adapter': state.identity(__file__),
        'parent_directory': str(reference), 'parent_plan': state.identity(previous_path),
        'parent_files': {entry.name: state.identity(entry) for entry in entries},
        'parent_verification': proof, 'reference': load(reference / 'reference.json'),
        'reference_decoded_again': False, 'parent_processes_rerun': False}


def selected_commands(path, plan, output, inputs):
    return {name: command for name, command in core.commands(path, plan, output, inputs).items()
        if name.startswith('asr-')}


def verify_observation(result, model_key, probe, window, inputs):
    require(result['model'] == model_key and result['probe'] == probe['key']
        and result['window'] == window and result['model_files'] == inputs['models'][model_key]
        and result['hotwords'] == '' and result['word_times_relative_to_diagnostic_window'] is True
        and result['not_diarization_or_acoustic_boundary_approval'] is True,
        'Observation belongs to different inputs')
    require(isinstance(result['language'], str) and result['language']
        and type(result['language_probability']) in (int, float)
        and math.isfinite(result['language_probability'])
        and 0 <= result['language_probability'] <= 1, 'Invalid language observation')
    require(isinstance(result['words'], list) and isinstance(result['sentences'], list),
        'Malformed observation arrays')
    for sentence in result['sentences']:
        require(isinstance(sentence, list) and len(sentence) == 3
            and all(type(value) in (int, float) and math.isfinite(value) for value in sentence[:2])
            and 0 <= sentence[0] <= sentence[1] and isinstance(sentence[2], str),
            'Malformed or non-finite sentence observation')
    require(result['text'] == ' '.join(sentence[2] for sentence in result['sentences']),
        'Sentence/text mismatch')
    for word in result['words']:
        require(isinstance(word, dict) and set(word) == {'start', 'end', 'word', 'probability'}
            and isinstance(word['word'], str)
            and all(type(word[field]) in (int, float) and math.isfinite(word[field])
                for field in ['start', 'end', 'probability'])
            and 0 <= word['start'] <= word['end'] and 0 <= word['probability'] <= 1,
            'Malformed or non-finite word observation')


def check(path, plan, reference, output, inputs=None, extension=None):
    if inputs is None or extension is None:
        inputs, extension = parent_binding(path, plan, reference)
    require(core.binding(path, plan) == inputs and state.matches(extension['adapter'])
        and state.matches(extension['parent_plan']), 'Inputs changed during extension observation')
    require(inputs == load(output / 'inputs.json'), 'Extension inputs changed')
    require(extension == load(output / 'extension.json'), 'Parent proof or extension adapter changed')
    borrowed = load(output / 'reference.json')
    require(borrowed == extension['reference'] and state.matches(borrowed['audio'])
        and core.wave_details(Path(borrowed['audio']['path'])) == borrowed['samples'],
        'Borrowed diagnostic reference changed')
    require(not any((output / name).exists() for name in ['reference-mono16k.wav',
        'source-probe.json', 'source-probe-process.json', 'reference-decode-process.json']),
        'Extension must not create or fabricate source/decode observations')
    windows = load(output / 'windows.json')
    require(set(windows) == {probe['key'] for probe in plan['probes']}, 'Incomplete context set')
    with wave.open(borrowed['audio']['path'], 'rb') as full:
        for probe in plan['probes']:
            window = windows[probe['key']]
            first, last = core.samples(probe['start']), core.samples(probe['end'])
            require(0 <= first < last <= borrowed['samples'], 'Context exceeds borrowed reference')
            require((window['start_sample'], window['end_sample'], window['samples'])
                == (first, last, last - first) and state.matches(window['audio']),
                'Context identity or sample grid changed')
            require(Path(window['audio']['path']) == output / f'{probe["key"]}.wav',
                'Context must belong to this extension')
            full.setpos(first)
            with wave.open(window['audio']['path'], 'rb') as context:
                require(core.wave_details(Path(window['audio']['path'])) == last - first
                    and context.readframes(last - first) == full.readframes(last - first),
                    'Context is not the complete declared borrowed-reference slice')
    commands = selected_commands(path, plan, output, inputs)
    for name, command in commands.items():
        receipt = load(output / f'{name}-process.json')
        require(receipt['status'] == 'complete' and type(receipt['returncode']) is int
            and receipt['returncode'] == 0 and receipt['command'] == command
            and type(receipt.get('pid')) is int and receipt['pid'] > 0
            and receipt.get('started_at') and receipt.get('finished_at'),
            f'Unproven new worker process: {name}')
    observations = {}
    for model_key in plan['models']:
        for probe in plan['probes']:
            filename = output / f'{model_key}-{probe["key"]}.json'
            verify_observation(load(filename), model_key, probe, windows[probe['key']], inputs)
            observations[filename.name] = state.identity(filename)
    require(observations == load(output / 'observations.json'), 'Observation content changed')
    require(all(state.matches(bound) for bound in extension['parent_files'].values()),
        'Parent evidence changed during extension verification')
    return {'kind': KIND, 'reference_samples': borrowed['samples'],
        'borrowed_reference': borrowed['audio'], 'probes': len(windows),
        'context_seconds': sum(window['samples'] for window in windows.values()) / core.SAMPLE_RATE,
        'models': len(plan['models']), 'observations': len(observations),
        'new_processes_verified': len(commands),
        'parent_processes_verified_without_rerunning': extension['parent_verification']['processes_verified'],
        'reference_decoded_again': False, 'speaker_or_translation_approved': False,
        'human_listened': False, 'native_signal_or_production_alignment_claimed': False}


def execute(action, path, reference, output=None):
    path, plan = core.read_plan(path)
    work = Path(plan['work'])
    reference = Path(reference).resolve(strict=True)
    if output is not None:
        output = Path(output).resolve()
        require(not output.is_relative_to(work) and not work.is_relative_to(output)
            and not output.is_relative_to(reference) and not reference.is_relative_to(output),
            'Use separate production, parent and extension directories')
    with (work / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        inputs, extension = parent_binding(path, plan, reference)
        if action == 'validate':
            return {'valid': True, 'probes': len(plan['probes']),
                'batches': len(core.batches(plan['probes'])),
                'reference': extension['reference']['audio'], 'reference_decoded_again': False}
        require(output is not None, 'Execution or check requires an output directory')
        if action == 'check':
            return check(path, plan, reference, output, inputs, extension)
        require(not output.exists(), 'Output exists; never repeat or overwrite prior observations')
        output.mkdir(parents=True)
        prepare_videos.save_json(output / 'inputs.json', inputs)
        prepare_videos.save_json(output / 'extension.json', extension)
        try:
            borrowed = extension['reference']
            prepare_videos.save_json(output / 'reference.json', borrowed)
            windows = core.cut_windows(plan, Path(borrowed['audio']['path']), output)
            prepare_videos.save_json(output / 'windows.json', windows)
            for name, command in selected_commands(path, plan, output, inputs).items():
                native_audio.run_process(command, output, name, f'{name}.stdout.log', lock.fileno())
            observations = {filename.name: state.identity(filename) for model_key in plan['models']
                for probe in plan['probes'] for filename in [output / f'{model_key}-{probe["key"]}.json']}
            prepare_videos.save_json(output / 'observations.json', observations)
            result = check(path, plan, reference, output, inputs, extension)
            prepare_videos.save_json(output / 'verification.json', result)
            return result
        except BaseException as error:
            prepare_videos.save_json(output / 'failure.json', {'error': f'{type(error).__name__}: {error}',
                'finished_at': state.now(), 'partial_outputs_retained': True})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate', 'run', 'check'])
    parser.add_argument('plan', type=Path)
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    if arguments.action != 'validate' and arguments.output is None:
        parser.error('--output is required for run/check')
    signal.signal(signal.SIGTERM, native_audio.interrupted)
    signal.signal(signal.SIGINT, native_audio.interrupted)
    result = execute(arguments.action, arguments.plan, arguments.reference, arguments.output)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
