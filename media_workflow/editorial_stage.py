"""Stage source-reviewed whole-unit edits without synthesizing or installing media."""

import argparse
import copy
import fcntl
import json
import os
import re
import shutil
import subprocess
import wave
from decimal import Decimal
from pathlib import Path

import numpy as np

import dub
import dubbing_state as state
import speech_normalization
import video_workflow
from check_videos import translations
from prepare_videos import atomic_write, save_json
from . import speech_variants
from .speech_variants import require


KIND = 'complete-utterance-editorial-stage-v1'
RATE = 24000
INPUTS = ('en.srt', 'translation.tsv', 'spoken_overrides.json', 'translation_task.json',
    'tts_voice.txt', 'voices.json', 'tts_stats.json', 'timing_review.json',
    'zh.srt', 'spoken.srt', 'bilingual.srt', 'compilation.json')
BASELINE = (*INPUTS, 'narration.json', 'render_state.json')
OPTIONAL = ('speech_policy.json',)
FORBIDDEN = ('render_state.json', 'delivery_quality.json', 'release.json',
    'upload_export.json', 'speech_groups.json')


def implementations():
    modules = (state, dub, speech_normalization, video_workflow, speech_variants)
    return {module.__name__: state.identity(module.__file__) for module in modules}


def read_wave(path):
    with wave.open(str(path), 'rb') as audio:
        require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, RATE),
            'Expected mono PCM16 at24kHz')
        count = audio.getnframes()
        payload = audio.readframes(count)
    require(count > 0 and len(payload) == count * 2, 'Incomplete PCM waveform')
    return np.frombuffer(payload, dtype='<i2')


def write_wave(path, samples):
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        audio.writeframes(samples.astype('<i2', copy=False).tobytes())


def replacement(case):
    observed = case['preview']
    for bound in (observed['proof']['audio'], observed['proof']['metadata'], observed['waveform']):
        require(state.matches(bound), 'Complete preview audio/metadata changed')
    proof = observed['proof']['contract']
    require(proof['text'] == case['spoken'] and proof['voice'] == case['voice']
        and proof['rate'] == 0 and proof['boundary'] == 'WordBoundary', 'Wrong complete-preview contract')
    raw = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', observed['proof']['audio']['path'],
        '-f', 'f32le', '-ac', '1', '-ar', str(RATE), '-'], capture_output=True, check=True).stdout
    require(len(raw) % 4 == 0, 'Incomplete full float decode')
    floated = np.frombuffer(raw, dtype='<f4')
    require(len(floated) == observed['samples'] > 0 and np.all(np.isfinite(floated)),
        'Full preview length changed or contains nonfinite PCM')
    require(speech_variants.inspect_wave(observed['waveform']['path']) ==
        (observed['samples'], observed['pcm_sha256']), 'Complete preview waveform changed')
    boundaries = [json.loads(line) for line in Path(observed['proof']['metadata']['path']).read_text().splitlines()]
    require(boundaries and all(item['type'] == 'WordBoundary' for item in boundaries),
        'Expected actual complete word-boundary observations')
    plain = lambda text: re.sub(r'[^\w]', '', text, flags=re.UNICODE).casefold()
    require(plain(''.join(item['text'] for item in boundaries)) == plain(case['spoken']),
        'Observed word-boundary text differs from the whole spoken unit')
    leveled = dub.level(floated)
    require(len(leveled) == len(floated) and np.all(np.isfinite(leveled))
        and np.max(np.abs(leveled)) <= 1, 'Gain-only processing clipped or changed duration')
    require(len(leveled) <= case['end_sample'] - case['start_sample'], 'Complete preview exceeds original window')
    return (leveled * 32767).astype('<i2')


def edit_text(before, cases):
    lines = before['translation.tsv'].read_text().splitlines()
    overrides = state.load(before['spoken_overrides.json'])
    for case in cases:
        number = case['unit_id']
        require(lines[number - 1].split('\t', 1)[0] == str(number), 'Expected ordered translation unit IDs')
        lines[number - 1] = f'{number}\t{case["display"]}'
        overrides[str(number)] = case['spoken']
    return '\n'.join(lines) + '\n', overrides


def edit_statistics(old, cases, sample_counts):
    result = copy.deepcopy(old)
    for case, count in zip(cases, sample_counts):
        number = case['unit_id'] - 1
        available = case['end_sample'] - case['start_sample']
        seconds = float(Decimal(case['source_end']) - Decimal(case['source_start']))
        result['per'][number] = [0, 1.0, count / available, seconds]
        result['duration_seconds'][number] = count / RATE
    result['normal'] = sum(row[0] == 0 for row in result['per'])
    result['rate_up'] = sum(row[0] > 0 for row in result['per'])
    state.check_stats(result, len(result['per']))
    return result


def audio_checks(base, composite, source_pcm, source, spoken, stats, regions, reserved):
    require(len(base) == len(composite), 'Base/composite sample counts differ')
    require(len(source) == len(spoken) == len(stats['per']) == len(stats['duration_seconds']),
        'Incomplete unit/audio inventory')
    state.check_stats(stats, len(source))
    cursor = 0
    for number, (cue, utterance, duration) in enumerate(zip(source, spoken, stats['duration_seconds']), 1):
        start, end = [int(float(bound) * RATE) for bound in cue[:2]]
        require(cursor <= start < end <= len(base), 'Source windows overlap or exceed the complete waveform')
        upper = Decimal(str(duration)) + (Decimal('0.00005') if duration else Decimal('0'))
        require(Decimal('0') <= upper <= Decimal(str(cue[1])) - Decimal(str(cue[0])),
            f'Strict original-window duration overflow: {number}')
        require(not np.any(base[cursor:start]), f'Nonzero base TTS in gap before{number}')
        if number in reserved or not re.search(r'\w', utterance[2]):
            require(not np.any(base[start:end]), f'Source/event reservation synthesized: {number}')
        cursor = end
    require(not np.any(base[cursor:]), 'Unexpected base TTS after the final original window')
    cursor, region_units = 0, []
    for region in regions:
        first, last = region['start_sample'], region['end_sample']
        require(type(first) is int and type(last) is int and cursor <= first < last <= len(base)
            and region['gain_db'] == 0, 'Unsupported, unordered or nonzero-gain source region')
        actual_end = region.get('actual_source_end_sample', last)
        require(type(actual_end) is int and first <= actual_end <= min(last, len(source_pcm))
            and last - actual_end == region.get('container_silence_samples', 0), 'Unbound source EOF padding')
        require(np.array_equal(composite[cursor:first], base[cursor:first]) and not np.any(base[first:last]),
            'Composite changed outside source regions or source reservation contains TTS')
        require(np.array_equal(composite[first:actual_end], source_pcm[first:actual_end])
            and not np.any(composite[actual_end:last]), 'Actual original voice or explicit EOF silence changed')
        require(isinstance(region['unit_ids'], list) and region['unit_ids'], 'Missing source-region unit inventory')
        region_units.extend(region['unit_ids'])
        cursor = last
    require(np.array_equal(composite[cursor:], base[cursor:]) and sorted(region_units) == sorted(reserved)
        and len(set(reserved)) == len(reserved) and all(type(number) is int and 1 <= number <= len(source)
        for number in reserved), 'Original voice inventory or non-source composite tail changed')
    return {'strict_original_windows': len(source), 'original_regions': len(regions),
        'reserved_units': len(reserved), 'outside_source_regions_equals_base': True,
        'actual_source_pcm_and_declared_eof_silence_exact': True}


def configuration(path):
    config = state.load(path)
    require(config['kind'] == KIND, 'Unsupported editorial staging contract')
    work = Path(config['work'])
    require(work.is_absolute() and work.resolve() == work, 'Expected canonical absolute work path')
    require(not (work / 'speech_groups.json').exists(), 'Grouped production requires a separate supported adapter')
    require(set(BASELINE) <= set(config['baseline_files']) <= set(BASELINE + OPTIONAL),
        'Bind every required baseline input; no arbitrary copy paths')
    require(set(config['authority']) == {'runtime', 'receipt', 'exit'}, 'Bind the explicit successful baseline check')
    require(isinstance(config['edits'], list) and 0 < len(config['edits']) <= 12, 'Expected one to twelve explicit edits')
    keys = []
    for edit in config['edits']:
        require(edit['source_fidelity_selected'] is True and isinstance(edit['reason'], str) and edit['reason'].strip()
            and edit['human_listened'] is False and edit['pronunciation_or_emotion_approved'] is False,
            'Editorial selection is not an audio/listening approval')
        keys.append(edit['key'])
    require(len(set(keys)) == len(keys), 'Duplicate editorial selections')
    return config


def archive(bound, destination):
    require(state.matches(bound), 'Baseline changed before its exact archive')
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(bound['path'], destination)
    except OSError:
        shutil.copy2(bound['path'], destination)
    observed = state.identity(destination)
    require(all(observed[key] == bound[key] for key in ('bytes', 'mtime_ns', 'sha256')),
        'Archive differs from bound baseline')
    return {'original': bound, 'archive': observed}


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    work = Path(config['work'])
    require(not output.exists() and not output.is_relative_to(work) and not work.is_relative_to(output),
        'Preserve existing stages and keep staging separate from the formal project')
    with (work / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, bound in config['baseline_files'].items():
            require(Path(bound['path']) == work / name and state.matches(bound), f'Stale baseline input: {name}')
        require(all((work / name).exists() == (name in config['baseline_files']) for name in OPTIONAL),
            'Optional compilation policy was omitted')
        for bound in [config['preview_plan'], config['preview_manifest'], *config['authority'].values()]:
            require(state.matches(bound), 'Stale preview/baseline check authority')
        require(Path(config['authority']['exit']['path']).read_text().strip() == '0', 'Baseline check did not exit0')
        authority = state.load(config['authority']['receipt']['path'])
        baseline = state.load(work / 'narration.json')
        require(authority['quality']['allowed'] is True and authority['quality']['bindings']['waveform'] == baseline['waveform']
            and authority['master'] == state.load(work / 'render_state.json')['output'], 'Baseline check is not current')
        require(all(config['baseline_files'][name]['sha256'] == digest for name, digest in
            authority['quality']['bindings']['inputs'].items()) and authority['quality']['bindings']['source'] ==
            state.identity(state.source_video(work)), 'Baseline quality receipt binds different source/text inputs')
        state.validate_compilation(work)
        speech_variants.check(config['preview_plan']['path'], Path(config['preview_manifest']['path']).parent)
        plan = state.load(config['preview_plan']['path'])
        preview = state.load(config['preview_manifest']['path'])
        require(plan['work'] == str(work), 'Preview belongs to another project')
        cases, numbers = [], set()
        for edit in config['edits']:
            selected = [case for case in plan['cases'] if case['key'] == edit['key']]
            require(len(selected) == 1, 'Editorial selection is missing from reviewed previews')
            case = copy.deepcopy(selected[0])
            require(case['unit_id'] not in numbers, 'Cannot select two variants of the same unit')
            numbers.add(case['unit_id'])
            case['preview'] = next(item for item in preview['cases'] if item['key'] == case['key'])
            case['voice'] = plan['voice']
            case['editorial_selection'] = edit
            case['start_sample'], case['end_sample'] = [int(float(case[name]) * RATE)
                for name in ('source_start', 'source_end')]
            require(case['end_sample'] - case['start_sample'] <= case['preview']['source_window_samples'],
                'Legacy absolute sample mapping exceeds the exact Decimal source window')
            require(all(character not in case[name] for name in ('display', 'spoken') for character in '\t\r\n'),
                'An edit must preserve one complete TSV/spoken unit')
            cases.append(case)
        cases.sort(key=lambda case: case['start_sample'])
        producer = baseline['producer']
        regions, reserved = producer['original_source_ranges'], producer['original_source_unit_ids']
        require(not numbers.intersection(reserved) and all(not (case['start_sample'] < region['end_sample']
            and case['end_sample'] > region['start_sample']) for case in cases for region in regions),
            'Editorial synthesis overlaps actual original voice')
        output.mkdir(parents=True, exist_ok=False)
        manifest = {'kind': KIND, 'complete': False, 'config': state.identity(config_path),
            'adapter': state.identity(__file__), 'implementations': implementations(), 'created_at': state.now(),
            'scope': 'staged-text-and-composite-only', 'canonical_media_modified': False,
            'rendered_or_installed': False, 'human_listened': False, 'pronunciation_or_emotion_approved': False}
        save_json(output / 'manifest.json', manifest)
        try:
            files = {name: archive(bound, output / 'baseline' / name)
                for name, bound in config['baseline_files'].items()}
            waves = {name: archive(bound, output / 'baseline' / f'{name}.wav') for name, bound in {
                'base': producer['base_tts_waveform'], 'composite': baseline['waveform'],
                'source': producer['original_source_pcm']}.items()}
            evidence = {name: archive(bound, output / 'baseline' / name) for name, bound in {
                'master.mp4': authority['master'], 'upload.mp4': authority['upload'],
                'package.json': authority['package'], 'check.log': config['authority']['receipt'],
                'check.exit': config['authority']['exit'], 'runtime.py': config['authority']['runtime']}.items()}
            shadow = output / work.name
            shadow.mkdir()
            for name in (*INPUTS, *OPTIONAL):
                if name in files:
                    shutil.copy2(files[name]['archive']['path'], shadow / name)
            (shadow / 'download').symlink_to(work / 'download', target_is_directory=True)
            before = {name: Path(item['archive']['path']) for name, item in files.items()}
            text, overrides = edit_text(before, cases)
            atomic_write(shadow / 'translation.tsv', text)
            save_json(shadow / 'spoken_overrides.json', overrides)
            video_workflow.compile_translation(shadow)
            source = state.cues(shadow / 'en.srt')
            base = read_wave(waves['base']['archive']['path']).copy()
            composite = read_wave(waves['composite']['archive']['path']).copy()
            source_pcm = read_wave(waves['source']['archive']['path'])
            replacements = [replacement(case) for case in cases]
            for case, samples in zip(cases, replacements):
                first, last = case['start_sample'], case['end_sample']
                for waveform in (base, composite):
                    waveform[first:last] = 0
                    waveform[first:first + len(samples)] = samples
            stats = edit_statistics(state.load(before['tts_stats.json']), cases, [len(item) for item in replacements])
            save_json(shadow / 'tts_stats.json', stats)
            audit = audio_checks(base, composite, source_pcm, source, state.cues(shadow / 'spoken.srt'),
                stats, regions, reserved)
            write_wave(output / 'base.wav', base)
            write_wave(output / 'composite.wav', composite)
            contract = {'kind': KIND, 'adapter': manifest['adapter'], 'implementations': manifest['implementations'],
                'baseline_files': files, 'baseline_waveforms': waves, 'baseline_evidence': evidence,
                'preview_plan': config['preview_plan'], 'preview_manifest': config['preview_manifest'],
                'cases': cases, 'original_source_ranges': regions, 'original_source_unit_ids': reserved,
                'base_waveform': state.identity(output / 'base.wav'), 'composite_waveform': state.identity(output / 'composite.wav'),
                'source_eof_gap_samples': producer['source_eof_gap_samples'], 'audit': audit}
            save_json(shadow / 'editorial-source.json', contract)
            dependencies = state.narration_dependencies(shadow)
            dependencies['editorial_stage_contract'] = state.identity(shadow / 'editorial-source.json')
            save_json(shadow / 'narration.json', {'origin': KIND, 'observed_at': state.now(),
                'dependencies': dependencies, 'waveform': contract['composite_waveform'],
                'statistics': state.hashes(shadow, ['tts_stats.json', 'timing_review.json']),
                'producer': {'kind': KIND, 'contract': dependencies['editorial_stage_contract']},
                'historical_generation_proven': False, 'human_listened': False, 'pronunciation_or_emotion_approved': False})
            require(all(state.matches(bound) for bound in config['baseline_files'].values()), 'Live baseline changed during staging')
            require(all(state.matches(item['original']) for item in evidence.values()), 'Live media/check authority changed')
            manifest.update({'complete': True, 'work': str(shadow), 'contract': state.identity(shadow / 'editorial-source.json'),
                'files': {path.name: state.identity(path) for path in shadow.iterdir() if path.is_file()}, 'audit': audit})
            save_json(output / 'manifest.json', manifest)
            del base, composite, source_pcm, replacements
            return check(config_path, output)
        except BaseException as error:
            manifest.update({'complete': False, 'error': f'{type(error).__name__}: {error}'})
            save_json(output / 'manifest.json', manifest)
            raise


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = state.load(config_path)
    manifest = state.load(output / 'manifest.json')
    require(config['kind'] == manifest['kind'] == KIND and manifest['complete'] is True
        and manifest['config'] == state.identity(config_path) and manifest['adapter'] == state.identity(__file__)
        and manifest['implementations'] == implementations(), 'Incomplete/stale stage, config or implementation')
    require(manifest['scope'] == 'staged-text-and-composite-only' and all(manifest[name] is False for name in
        ('canonical_media_modified', 'rendered_or_installed', 'human_listened', 'pronunciation_or_emotion_approved')),
        'Staging is not installation or audio approval')
    work = output / Path(config['work']).name
    require(manifest['work'] == str(work) and not any((work / name).exists() for name in FORBIDDEN),
        'Staged text/audio must not impersonate current render, quality, release or grouped production')
    require(manifest['contract'] == state.identity(work / 'editorial-source.json'), 'Editorial contract changed')
    require(set(manifest['files']) == {path.name for path in work.iterdir() if path.is_file()}, 'Stage file inventory changed')
    for name, bound in manifest['files'].items():
        require(Path(bound['path']) == work / name and state.matches(bound), 'Staged file changed')
    contract = state.load(work / 'editorial-source.json')
    require(contract['kind'] == KIND and contract['adapter'] == manifest['adapter']
        and contract['implementations'] == manifest['implementations'], 'Wrong explicit narration origin')
    for group in ('baseline_files', 'baseline_waveforms', 'baseline_evidence'):
        for item in contract[group].values():
            require(state.matches(item['archive']) and all(item['archive'][key] == item['original'][key]
                for key in ('bytes', 'sha256', 'mtime_ns')), 'Baseline archive changed')
    require({name: item['original'] for name, item in contract['baseline_files'].items()} == config['baseline_files'],
        'Archived input inventory differs from the explicit baseline')
    before = {name: Path(item['archive']['path']) for name, item in contract['baseline_files'].items()}
    baseline = state.load(before['narration.json'])
    require(contract['original_source_ranges'] == baseline['producer']['original_source_ranges']
        and contract['original_source_unit_ids'] == baseline['producer']['original_source_unit_ids']
        and contract['source_eof_gap_samples'] == baseline['producer']['source_eof_gap_samples'], 'Original-source policy changed')
    require(all(contract['baseline_waveforms'][name]['original'] == bound for name, bound in {
        'base': baseline['producer']['base_tts_waveform'], 'composite': baseline['waveform'],
        'source': baseline['producer']['original_source_pcm']}.items()), 'Archived waveform provenance changed')
    for bound in (contract['preview_plan'], contract['preview_manifest'], contract['base_waveform'], contract['composite_waveform']):
        require(state.matches(bound), 'Preview or staged complete waveform changed')
    plan = state.load(contract['preview_plan']['path'])
    preview = state.load(contract['preview_manifest']['path'])
    require(plan['work'] == config['work'] and preview['plan'] == contract['preview_plan'] and preview['complete'] is True,
        'Whole-unit preview provenance changed')
    require(contract['preview_plan'] == config['preview_plan'] and contract['preview_manifest'] == config['preview_manifest']
        and plan['master'] == contract['baseline_evidence']['master.mp4']['original'], 'Preview/baseline master identity changed')
    for name, bound in plan['inputs'].items():
        require(contract['baseline_files'][name]['original'] == bound, 'Preview did not bind the archived baseline')
    cases = contract['cases']
    require(len(cases) == len(config['edits']) and {case['key'] for case in cases} == {edit['key'] for edit in config['edits']},
        'Editorial selection inventory changed')
    for case in cases:
        candidate = next(item for item in plan['cases'] if item['key'] == case['key'])
        require(all(case[key] == value for key, value in candidate.items()) and case['voice'] == plan['voice']
            and case['preview'] == next(item for item in preview['cases'] if item['key'] == case['key'])
            and case['editorial_selection'] == next(item for item in config['edits'] if item['key'] == case['key']),
            'Whole source-reviewed preview selection changed')
    text, overrides = edit_text(before, cases)
    require((work / 'translation.tsv').read_text() == text and state.load(work / 'spoken_overrides.json') == overrides,
        'Translation changes escaped the selected complete units')
    for name in (*INPUTS, *OPTIONAL):
        if name in before and name not in ('translation.tsv', 'spoken_overrides.json', 'tts_stats.json',
                'compilation.json', *state.COMPILE_OUTPUTS):
            require((work / name).read_bytes() == before[name].read_bytes(), f'Unselected input changed: {name}')
    state.validate_compilation(work)
    for name in state.COMPILE_OUTPUTS:
        require(state.cues(work / name) == state.expected_compilation(work)[name], 'Compiled text is not source-bound')
        for number, (old, new) in enumerate(zip(state.cues(before[name]), state.cues(work / name)), 1):
            require(old[:2] == new[:2] and (number in {case['unit_id'] for case in cases} or old == new),
                'Compiled source times or unselected text changed')
    replacements = [replacement(case) for case in cases]
    stats = state.load(work / 'tts_stats.json')
    require(stats == edit_statistics(state.load(before['tts_stats.json']), cases, [len(item) for item in replacements]),
        'Statistics differ from the complete gain-only replacements')
    current = {}
    for label in ('base', 'composite'):
        expected = read_wave(contract['baseline_waveforms'][label]['archive']['path']).copy()
        for case, samples in zip(cases, replacements):
            first, last = case['start_sample'], case['end_sample']
            expected[first:last] = 0
            expected[first:first + len(samples)] = samples
        current[label] = read_wave(contract[f'{label}_waveform']['path'])
        require(np.array_equal(current[label], expected), 'Complete edited PCM or outside-edit PCM differs')
    audit = audio_checks(current['base'], current['composite'], read_wave(contract['baseline_waveforms']['source']['archive']['path']),
        state.cues(work / 'en.srt'), state.cues(work / 'spoken.srt'), stats,
        contract['original_source_ranges'], contract['original_source_unit_ids'])
    narration = state.load(work / 'narration.json')
    dependencies = state.narration_dependencies(work)
    dependencies['editorial_stage_contract'] = state.identity(work / 'editorial-source.json')
    require(narration['origin'] == KIND and narration['dependencies'] == dependencies
        and narration['waveform'] == contract['composite_waveform']
        and narration['statistics'] == state.hashes(work, ['tts_stats.json', 'timing_review.json'])
        and narration['producer'] == {'kind': KIND, 'contract': dependencies['editorial_stage_contract']}
        and all(narration[name] is False for name in ('historical_generation_proven', 'human_listened',
            'pronunciation_or_emotion_approved')), 'Explicit narration provenance or approval changed')
    require(audit == contract['audit'] == manifest['audit'], 'Strict complete-audio audit changed')
    try:
        state.validate_narration(work)
    except ValueError:
        pass
    else:
        raise ValueError('Bare frozen narration validator unexpectedly accepted the special-origin audio')
    return {'passed': True, 'kind': KIND, 'work': str(work), 'edits': [case['unit_id'] for case in cases],
        **audit, 'manifest': state.identity(output / 'manifest.json'), 'canonical_media_modified': False,
        'rendered_or_installed': False, 'historical_generation_proven': False,
        'human_listened': False, 'pronunciation_or_emotion_approved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.config, args.output) if args.action == 'prepare' else check(args.config, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
