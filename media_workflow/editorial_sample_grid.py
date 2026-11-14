"""Stage complete utterances inside exact source windows and legacy sample ownership."""

import argparse
import copy
import fcntl
import json
import math
import shutil
from contextlib import contextmanager
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import numpy as np

import dubbing_state as state
import video_workflow
from check_videos import translations
from prepare_videos import atomic_write, save_json
from . import editorial_stage as legacy
from . import speech_variants
from .speech_variants import require


KIND = 'exact-window-legacy-owned-editorial-stage-v1'
POLICY = 'legacy-owned-span-exact-contained-synthesis-v1'
RATE = legacy.RATE
ROOT = Path(__file__).resolve().parents[1]
FROZEN_CHECK = legacy.check


def implementations():
    return {**legacy.implementations(), 'legacy_editorial_stage': state.identity(legacy.__file__)}


def sample_mapping(start, end, count, window_samples):
    require(isinstance(start, str) and isinstance(end, str), 'Use explicit decimal source bounds')
    first_time, last_time = Decimal(start), Decimal(end)
    require(first_time.is_finite() and last_time.is_finite()
        and 0 <= first_time < last_time and last_time - first_time <= 40, 'Invalid complete source window')
    require(type(count) is int and count > 0 and type(window_samples) is int and window_samples > 0,
        'Expected positive integer complete-audio and window sample counts')
    first_exact, last_exact = Fraction(first_time) * RATE, Fraction(last_time) * RATE
    duration = last_exact - first_exact
    require(window_samples == duration.numerator // duration.denominator,
        'Preview duration window differs from exact complete source bounds')
    require(math.isfinite(float(start) * RATE) and math.isfinite(float(end) * RATE),
        'Nonfinite legacy sample mapping')
    first_legacy, last_legacy = int(float(start) * RATE), int(float(end) * RATE)
    first_exact_sample = -(-first_exact.numerator // first_exact.denominator)
    last_exact_sample = last_exact.numerator // last_exact.denominator
    first, last = max(first_legacy, first_exact_sample), min(last_legacy, last_exact_sample)
    require(0 <= first_legacy <= first < last <= last_legacy,
        'Exact source and legacy ownership have no complete-sample intersection')
    require(count <= window_samples and count <= last - first,
        'Complete audio does not fit both exact source bounds and legacy ownership')
    require(first >= first_exact and first + count <= last_exact,
        'Complete synthesis escapes the exact source window')
    return {'policy': POLICY, 'source_start': start, 'source_end': end,
        'legacy_start_sample': first_legacy, 'legacy_end_sample': last_legacy,
        'exact_start_scaled': [first_exact.numerator, first_exact.denominator],
        'exact_end_scaled': [last_exact.numerator, last_exact.denominator],
        'exact_contained_start_sample': first_exact_sample, 'exact_contained_end_sample': last_exact_sample,
        'start_sample': first, 'end_sample': last, 'complete_samples': count,
        'preview_window_samples': window_samples, 'remaining_contained_samples': last - first - count}


def configuration(path):
    config = state.load(path)
    required = {'kind', 'mapping_policy', 'work', 'preview_plan', 'preview_manifest',
        'baseline_files', 'authority', 'edits'}
    require(required <= set(config) <= required | {'review_scope'}, 'Unexpected sample-grid configuration fields')
    require(config['kind'] == KIND and config['mapping_policy'] == POLICY, 'Unsupported explicit sample-grid contract')
    work = Path(config['work'])
    require(work.is_absolute() and work.resolve() == work and not (work / 'speech_groups.json').exists(),
        'Expected canonical, ungrouped baseline project')
    require(set(legacy.BASELINE) <= set(config['baseline_files']) <= set(legacy.BASELINE + legacy.OPTIONAL),
        'Bind every required baseline input and actual optional policy')
    require(set(config['authority']) == {'runtime', 'receipt', 'exit'}, 'Bind successful current baseline authority')
    require(isinstance(config['edits'], list) and 0 < len(config['edits']) <= 12,
        'Expected one to twelve complete-unit selections')
    keys = []
    for edit in config['edits']:
        require(set(edit) == {'key', 'source_fidelity_selected', 'reason', 'human_listened',
            'pronunciation_or_emotion_approved'} and edit['source_fidelity_selected'] is True
            and isinstance(edit['reason'], str) and edit['reason'].strip()
            and edit['human_listened'] is False and edit['pronunciation_or_emotion_approved'] is False,
            'Editorial selection must not impersonate listening approval')
        keys.append(edit['key'])
    require(len(set(keys)) == len(keys), 'Duplicate editorial selections')
    return config


def selected_cases(config, plan, preview, producer):
    require(plan['kind'] == speech_variants.KIND and plan['rate'] == '+0%' and plan['work'] == config['work']
        and preview['complete'] is True and preview['plan'] == config['preview_plan'], 'Wrong complete preview provenance')
    cases, numbers = [], set()
    for edit in config['edits']:
        selected = [case for case in plan['cases'] if case['key'] == edit['key']]
        observed = [case for case in preview['cases'] if case['key'] == edit['key']]
        require(len(selected) == len(observed) == 1, 'Missing or ambiguous complete preview selection')
        case = copy.deepcopy(selected[0])
        require(type(case['unit_id']) is int and case['unit_id'] > 0
            and case['unit_id'] not in numbers and observed[0]['unit_id'] == case['unit_id'],
            'Duplicate source-unit or mismatched preview')
        numbers.add(case['unit_id'])
        require(all(character not in case[name] for name in ('display', 'spoken') for character in '\t\r\n'),
            'Keep one complete TSV and spoken unit')
        case.update({'preview': observed[0], 'voice': plan['voice'], 'editorial_selection': edit})
        case['sample_mapping'] = sample_mapping(case['source_start'], case['source_end'],
            observed[0]['samples'], observed[0]['source_window_samples'])
        case['start_sample'], case['end_sample'] = [case['sample_mapping'][name]
            for name in ('start_sample', 'end_sample')]
        cases.append(case)
    cases.sort(key=lambda case: case['sample_mapping']['legacy_start_sample'])
    cursor = 0
    for case in cases:
        mapping = case['sample_mapping']
        require(cursor <= mapping['legacy_start_sample'] < mapping['legacy_end_sample'],
            'Selected legacy ownership spans overlap')
        require(case['unit_id'] not in producer['original_source_unit_ids']
            and all(not (mapping['legacy_start_sample'] < region['end_sample']
                and mapping['legacy_end_sample'] > region['start_sample'])
                for region in producer['original_source_ranges']), 'Editing would erase actual original speech')
        cursor = mapping['legacy_end_sample']
    return cases


def replace_owned(waveform, cases, replacements):
    require(waveform.ndim == 1 and waveform.dtype == np.dtype('<i2'), 'Expected mono PCM16 baseline samples')
    result = waveform.copy()
    require(len(cases) == len(replacements), 'Incomplete complete-audio replacement inventory')
    for case, samples in zip(cases, replacements):
        mapping = case['sample_mapping']
        require(samples.ndim == 1 and samples.dtype == np.dtype('<i2') and len(samples) == mapping['complete_samples']
            and case['start_sample'] == mapping['start_sample'] and case['end_sample'] == mapping['end_sample']
            and 0 <= mapping['legacy_start_sample'] <= case['start_sample']
            and case['start_sample'] + len(samples) <= case['end_sample']
            <= mapping['legacy_end_sample'] <= len(result), 'Replacement escapes proven sample ownership')
        result[mapping['legacy_start_sample']:mapping['legacy_end_sample']] = 0
        result[case['start_sample']:case['start_sample'] + len(samples)] = samples
    return result


def narration(work, contract, observed_at):
    dependencies = state.narration_dependencies(work)
    dependencies['editorial_stage_contract'] = state.identity(work / 'editorial-source.json')
    return {'origin': KIND, 'observed_at': observed_at, 'dependencies': dependencies,
        'waveform': contract['composite_waveform'],
        'statistics': state.hashes(work, ['tts_stats.json', 'timing_review.json']),
        'producer': {'kind': KIND, 'contract': dependencies['editorial_stage_contract']},
        'historical_generation_proven': False, 'human_listened': False, 'pronunciation_or_emotion_approved': False}


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    work = Path(config['work'])
    require(not output.exists() and ROOT not in (output, *output.parents)
        and not output.is_relative_to(work) and not work.is_relative_to(output),
        'Use a new external stage, separate from canonical work and repository')
    with (work / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(all(Path(bound['path']) == work / name and state.matches(bound)
            for name, bound in config['baseline_files'].items()), 'Current baseline input changed')
        require(all((work / name).exists() == (name in config['baseline_files']) for name in legacy.OPTIONAL),
            'Optional compilation policy omitted')
        require(all(state.matches(bound) for bound in
            (config['preview_plan'], config['preview_manifest'], *config['authority'].values())),
            'Preview or baseline-check authority changed')
        require(Path(config['authority']['exit']['path']).read_text().strip() == '0', 'Baseline check did not exit0')
        authority = state.load(config['authority']['receipt']['path'])
        baseline = state.load(work / 'narration.json')
        require(authority['quality']['allowed'] is True
            and authority['quality']['bindings']['waveform'] == baseline['waveform']
            and authority['master'] == state.load(work / 'render_state.json')['output']
            and all(config['baseline_files'][name]['sha256'] == digest
                for name, digest in authority['quality']['bindings']['inputs'].items())
            and authority['quality']['bindings']['source'] == state.identity(state.source_video(work)),
            'Baseline check does not bind current source, text, audio and master')
        state.validate_compilation(work)
        speech_variants.check(config['preview_plan']['path'], Path(config['preview_manifest']['path']).parent)
        plan, preview = [state.load(config[name]['path']) for name in ('preview_plan', 'preview_manifest')]
        producer = baseline['producer']
        cases = selected_cases(config, plan, preview, producer)
        output.mkdir(parents=True, exist_ok=False)
        manifest = {'kind': KIND, 'mapping_policy': POLICY, 'complete': False,
            'config': state.identity(config_path), 'adapter': state.identity(__file__),
            'implementations': implementations(), 'created_at': state.now(),
            'scope': 'staged-text-and-composite-only', 'canonical_media_modified': False,
            'rendered_or_installed': False, 'human_listened': False, 'pronunciation_or_emotion_approved': False}
        save_json(output / 'manifest.json', manifest)
        try:
            files = {name: legacy.archive(bound, output / 'baseline' / name)
                for name, bound in config['baseline_files'].items()}
            waves = {name: legacy.archive(bound, output / 'baseline' / f'{name}.wav') for name, bound in {
                'base': producer['base_tts_waveform'], 'composite': baseline['waveform'],
                'source': producer['original_source_pcm']}.items()}
            evidence = {name: legacy.archive(bound, output / 'baseline' / name) for name, bound in {
                'master.mp4': authority['master'], 'upload.mp4': authority['upload'],
                'package.json': authority['package'], 'check.log': config['authority']['receipt'],
                'check.exit': config['authority']['exit'], 'runtime.py': config['authority']['runtime']}.items()}
            shadow = output / work.name
            shadow.mkdir()
            for name in (*legacy.INPUTS, *legacy.OPTIONAL):
                if name in files:
                    shutil.copy2(files[name]['archive']['path'], shadow / name)
            (shadow / 'download').symlink_to(work / 'download', target_is_directory=True)
            before = {name: Path(item['archive']['path']) for name, item in files.items()}
            text, overrides = legacy.edit_text(before, cases)
            atomic_write(shadow / 'translation.tsv', text)
            save_json(shadow / 'spoken_overrides.json', overrides)
            video_workflow.compile_translation(shadow)
            replacements = [legacy.replacement(case) for case in cases]
            stats = legacy.edit_statistics(state.load(before['tts_stats.json']), cases,
                [len(samples) for samples in replacements])
            save_json(shadow / 'tts_stats.json', stats)
            waveforms = {name: replace_owned(legacy.read_wave(item['archive']['path']), cases, replacements)
                for name, item in waves.items() if name != 'source'}
            audit = legacy.audio_checks(waveforms['base'], waveforms['composite'],
                legacy.read_wave(waves['source']['archive']['path']), state.cues(shadow / 'en.srt'),
                state.cues(shadow / 'spoken.srt'), stats,
                producer['original_source_ranges'], producer['original_source_unit_ids'])
            audit.update({'selected_exact_contained_windows': len(cases), 'legacy_ownership_preserved': True,
                'mapping_policy': POLICY})
            for name, samples in waveforms.items():
                legacy.write_wave(output / f'{name}.wav', samples)
            contract = {'kind': KIND, 'mapping_policy': POLICY, 'adapter': manifest['adapter'],
                'implementations': manifest['implementations'], 'baseline_files': files,
                'baseline_waveforms': waves, 'baseline_evidence': evidence,
                'preview_plan': config['preview_plan'], 'preview_manifest': config['preview_manifest'],
                'cases': cases, 'original_source_ranges': producer['original_source_ranges'],
                'original_source_unit_ids': producer['original_source_unit_ids'],
                'source_eof_gap_samples': producer['source_eof_gap_samples'],
                'base_waveform': state.identity(output / 'base.wav'),
                'composite_waveform': state.identity(output / 'composite.wav'), 'audit': audit}
            save_json(shadow / 'editorial-source.json', contract)
            save_json(shadow / 'narration.json', narration(shadow, contract, state.now()))
            require(all(state.matches(bound) for bound in config['baseline_files'].values())
                and all(state.matches(item['original']) for item in evidence.values()), 'Live baseline changed during staging')
            manifest.update({'complete': True, 'work': str(shadow), 'contract': state.identity(shadow / 'editorial-source.json'),
                'files': {path.name: state.identity(path) for path in shadow.iterdir() if path.is_file()}, 'audit': audit})
            save_json(output / 'manifest.json', manifest)
            del waveforms, replacements
            return check(config_path, output)
        except BaseException as error:
            manifest.update({'complete': False, 'error': f'{type(error).__name__}: {error}'})
            save_json(output / 'manifest.json', manifest)
            raise


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    manifest = state.load(output / 'manifest.json')
    require(manifest['kind'] == KIND and manifest['mapping_policy'] == POLICY and manifest['complete'] is True
        and manifest['config'] == state.identity(config_path) and manifest['adapter'] == state.identity(__file__)
        and manifest['implementations'] == implementations(), 'Stale or incomplete explicit sample-grid stage')
    require(manifest['scope'] == 'staged-text-and-composite-only' and all(manifest[name] is False for name in
        ('canonical_media_modified', 'rendered_or_installed', 'human_listened', 'pronunciation_or_emotion_approved')),
        'Stage cannot impersonate production or hearing approval')
    work = output / Path(config['work']).name
    require(manifest['work'] == str(work) and not any((work / name).exists() for name in legacy.FORBIDDEN)
        and (work / 'download').resolve() == (Path(config['work']) / 'download').resolve(), 'Stage layout changed')
    require(manifest['contract'] == state.identity(work / 'editorial-source.json')
        and set(manifest['files']) == {path.name for path in work.iterdir() if path.is_file()}
        and all(Path(bound['path']) == work / name and state.matches(bound)
            for name, bound in manifest['files'].items()), 'Staged file inventory changed')
    contract = state.load(work / 'editorial-source.json')
    require(contract['kind'] == KIND and contract['mapping_policy'] == POLICY
        and contract['adapter'] == manifest['adapter'] and contract['implementations'] == manifest['implementations'],
        'Wrong sample-grid contract producer')
    for group in ('baseline_files', 'baseline_waveforms', 'baseline_evidence'):
        for item in contract[group].values():
            require(state.matches(item['archive']) and all(item['archive'][key] == item['original'][key]
                for key in ('bytes', 'mtime_ns', 'sha256')), 'Exact archived baseline changed')
    require({name: item['original'] for name, item in contract['baseline_files'].items()} == config['baseline_files'],
        'Archived baseline input inventory changed')
    before = {name: Path(item['archive']['path']) for name, item in contract['baseline_files'].items()}
    baseline = state.load(before['narration.json'])
    producer = baseline['producer']
    require(all(contract[name] == producer[name]
        for name in ('original_source_ranges', 'original_source_unit_ids', 'source_eof_gap_samples')),
        'Actual original-speech policy changed')
    expected_waves = {'base': producer['base_tts_waveform'], 'composite': baseline['waveform'],
        'source': producer['original_source_pcm']}
    require(set(contract['baseline_waveforms']) == set(expected_waves)
        and all(contract['baseline_waveforms'][name]['original'] == bound for name, bound in expected_waves.items()),
        'Archived waveform provenance changed')
    authority = state.load(contract['baseline_evidence']['check.log']['archive']['path'])
    expected_evidence = {'master.mp4': authority['master'], 'upload.mp4': authority['upload'],
        'package.json': authority['package'], 'check.log': config['authority']['receipt'],
        'check.exit': config['authority']['exit'], 'runtime.py': config['authority']['runtime']}
    require(set(contract['baseline_evidence']) == set(expected_evidence)
        and all(contract['baseline_evidence'][name]['original'] == bound for name, bound in expected_evidence.items())
        and Path(contract['baseline_evidence']['check.exit']['archive']['path']).read_text().strip() == '0'
        and authority['quality']['allowed'] is True and authority['quality']['bindings']['waveform'] == baseline['waveform']
        and all(config['baseline_files'][name]['sha256'] == digest
            for name, digest in authority['quality']['bindings']['inputs'].items())
        and authority['quality']['bindings']['source'] == baseline['dependencies']['source']
        and authority['master'] == state.load(before['render_state.json'])['output'], 'Archived baseline authority changed')
    require(contract['preview_plan'] == config['preview_plan'] and contract['preview_manifest'] == config['preview_manifest']
        and all(state.matches(contract[name]) for name in ('preview_plan', 'preview_manifest', 'base_waveform', 'composite_waveform')),
        'Preview or complete staged waveform changed')
    plan, preview = [state.load(contract[name]['path']) for name in ('preview_plan', 'preview_manifest')]
    require(plan['master'] == authority['master'] and all(contract['baseline_files'][name]['original'] == bound
        for name, bound in plan['inputs'].items()), 'Preview did not bind the actual archived baseline')
    require(preview['adapter'] == state.identity(speech_variants.__file__)
        and state.matches(preview['synthesis_implementation']), 'Complete preview implementation changed')
    cases = selected_cases(config, plan, preview, producer)
    require(contract['cases'] == cases, 'Selected source unit, complete preview or sample mapping changed')
    source = state.cues(before['en.srt'])
    display = translations(before['translation.tsv'], len(source))
    overrides = state.load(before['spoken_overrides.json'])
    voices = state.load(before['voices.json'])
    require(plan['voice'] == before['tts_voice.txt'].read_text().strip() and len(voices) == len(source),
        'Archived voice inventory changed')
    for case in cases:
        number = case['unit_id']
        require(number <= len(source) and case['source'] == source[number - 1][2]
            and (case['source_start'], case['source_end']) == tuple(str(bound) for bound in source[number - 1][:2])
            and case['current_display'] == display[number]
            and case['current_spoken'] == overrides.get(str(number), display[number])
            and case['voice'] == voices[number - 1], 'Whole source, previous translation or voice pairing changed')
    text, overrides = legacy.edit_text(before, cases)
    require((work / 'translation.tsv').read_text() == text and state.load(work / 'spoken_overrides.json') == overrides,
        'Text changes escaped selected complete source units')
    for name in (*legacy.INPUTS, *legacy.OPTIONAL):
        if name in before and name not in ('translation.tsv', 'spoken_overrides.json', 'tts_stats.json',
                'compilation.json', *state.COMPILE_OUTPUTS):
            require((work / name).read_bytes() == before[name].read_bytes(), f'Unselected input changed: {name}')
    state.validate_compilation(work)
    selected = {case['unit_id'] for case in cases}
    for name in state.COMPILE_OUTPUTS:
        current, previous = state.cues(work / name), state.cues(before[name])
        require(current == state.expected_compilation(work)[name] and len(current) == len(previous)
            and all(old[:2] == new[:2] and (number in selected or old == new)
                for number, (old, new) in enumerate(zip(previous, current), 1)), 'Compiled source times or unselected text changed')
    replacements = [legacy.replacement(case) for case in cases]
    stats = state.load(work / 'tts_stats.json')
    require(stats == legacy.edit_statistics(state.load(before['tts_stats.json']), cases,
        [len(samples) for samples in replacements]), 'Complete gain-only replacement statistics changed')
    waveforms = {}
    for name in ('base', 'composite'):
        expected = replace_owned(legacy.read_wave(contract['baseline_waveforms'][name]['archive']['path']), cases, replacements)
        waveforms[name] = legacy.read_wave(contract[f'{name}_waveform']['path'])
        require(np.array_equal(expected, waveforms[name]), 'Edited PCM or legacy-owned-span exterior changed')
    audit = legacy.audio_checks(waveforms['base'], waveforms['composite'],
        legacy.read_wave(contract['baseline_waveforms']['source']['archive']['path']),
        state.cues(work / 'en.srt'), state.cues(work / 'spoken.srt'), stats,
        contract['original_source_ranges'], contract['original_source_unit_ids'])
    audit.update({'selected_exact_contained_windows': len(cases), 'legacy_ownership_preserved': True, 'mapping_policy': POLICY})
    require(audit == contract['audit'] == manifest['audit'], 'Strict complete-audio audit changed')
    observed = state.load(work / 'narration.json')
    require(isinstance(observed['observed_at'], str) and observed['observed_at']
        and observed == narration(work, contract, observed['observed_at']), 'Explicit sample-grid narration provenance changed')
    try:
        state.validate_narration(work)
    except ValueError:
        pass
    else:
        raise ValueError('Bare frozen validator accepted the new sample-grid origin')
    return {'passed': True, 'kind': KIND, 'work': str(work), 'edits': [case['unit_id'] for case in cases],
        **audit, 'manifest': state.identity(output / 'manifest.json'), 'canonical_media_modified': False,
        'rendered_or_installed': False, 'human_listened': False, 'pronunciation_or_emotion_approved': False}


@contextmanager
def runtime(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    require(legacy.check is FROZEN_CHECK, 'Reject nested or foreign stage-check dispatch')
    check(config_path, output)
    contract = state.load(state.load(output / 'manifest.json')['contract']['path'])
    require(all(Fraction(Decimal(case['source_start'])) * RATE == case['start_sample']
        for case in contract['cases']), 'Existing caption projection requires exact source-start sample alignment')

    def dispatch(selected_config, selected_output):
        if Path(selected_config).resolve() == config_path and Path(selected_output).resolve() == output:
            return check(config_path, output)
        return FROZEN_CHECK(selected_config, selected_output)

    legacy.check = dispatch
    try:
        yield
    finally:
        legacy.check = FROZEN_CHECK


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check', 'project', 'quality'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--operation', choices=('prepare', 'check'))
    parser.add_argument('--stage-config', type=Path)
    parser.add_argument('--stage', type=Path)
    args = parser.parse_args()
    if args.action in ('prepare', 'check'):
        require(args.operation is None and args.stage_config is None and args.stage is None, 'Unexpected bridge arguments')
        result = (prepare if args.action == 'prepare' else check)(args.config, args.output)
    else:
        require(args.operation is not None and args.stage_config is not None and args.stage is not None,
            'Explicit stage and operation required for projection/quality dispatch')
        from . import caption_projection, editorial_quality
        module = caption_projection if args.action == 'project' else editorial_quality
        selected = state.load(args.config)
        require(selected['stage_config'] == state.identity(args.stage_config)
            and selected['stage_manifest'] == state.identity(args.stage / 'manifest.json'), 'Bridge scope differs from selected stage')
        with runtime(args.stage_config, args.stage):
            result = (module.prepare if args.operation == 'prepare' else module.check)(args.config, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
