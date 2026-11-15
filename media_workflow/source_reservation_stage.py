"""Stage explicit complete-unit source-audio reservations without synthesizing them."""

import argparse
import copy
import fcntl
import json
from decimal import Decimal
from pathlib import Path

import numpy as np

import dubbing_state as state
import video_workflow
from prepare_videos import atomic_write, save_json
from . import editorial_stage
from .speech_variants import require


KIND = 'complete-unit-actual-source-reservation-stage-v1'
RATE = editorial_stage.RATE
FIELDS = {'kind', 'work', 'baseline_files', 'authority', 'source', 'evidence', 'regions'}


def implementations():
    return {module.__name__: state.identity(module.__file__)
        for module in (state, video_workflow, editorial_stage)}


def configuration(path):
    config = state.load(path)
    require(set(config) == FIELDS and config['kind'] == KIND, 'Unsupported actual-source staging contract')
    work = Path(config['work'])
    require(work.is_absolute() and work.resolve() == work, 'Use a canonical absolute source project')
    require(set(editorial_stage.BASELINE) <= set(config['baseline_files'])
        <= set((*editorial_stage.BASELINE, *editorial_stage.OPTIONAL)), 'Incomplete baseline input inventory')
    require(set(config['authority']) == {'runtime', 'receipt', 'exit'}, 'Bind current baseline quality authority')
    require(set(config['source']) == {'media', 'audio_stream', 'sample_rate', 'pcm', 'decode_receipt'}
        and config['source']['audio_stream'] == '0:a:0' and config['source']['sample_rate'] == RATE,
        'Bind the original stream and decoded mono24kHz PCM explicitly')
    require(isinstance(config['evidence'], list) and 1 <= len(config['evidence']) <= 32,
        'Bind the source uncertainty and retention-policy evidence')
    require(isinstance(config['regions'], list) and 1 <= len(config['regions']) <= 12,
        'Expected one to twelve complete source regions')
    for region in config['regions']:
        require(set(region) == {'units', 'issue', 'reason', 'gain_db', 'human_listened', 'issue_resolved'}
            and type(region['gain_db']) in (int, float) and region['gain_db'] == 0
            and region['human_listened'] is False and region['issue_resolved'] is False,
            'Source retention must not impersonate a resolved source or listening decision')
        require(isinstance(region['issue'], str) and region['issue'].strip()
            and isinstance(region['reason'], str) and region['reason'].strip(), 'Explain the actual uncertainty')
        require(isinstance(region['units'], list) and region['units'], 'Missing complete-unit reservation inventory')
        for unit in region['units']:
            require(set(unit) == {'unit_id', 'display'} and type(unit['unit_id']) is int
                and isinstance(unit['display'], str) and unit['display'].startswith('【原声保留：')
                and '】' in unit['display'] and all(character not in unit['display'] for character in '\t\r\n'),
                'Use explicit nonspoken source-retention subtitles, not a confident guessed translation')
    return config


def selections(config, source, producer):
    previous, selected, regions = -1, set(), []
    old = set(producer['original_source_unit_ids'])
    for specification in config['regions']:
        numbers = [unit['unit_id'] for unit in specification['units']]
        require(all(1 <= number <= len(source) for number in numbers)
            and numbers == list(range(numbers[0], numbers[-1] + 1))
            and not selected.intersection(numbers) and not old.intersection(numbers),
            'Reservations must contain new unique consecutive complete source units')
        start, stop = Decimal(str(source[numbers[0] - 1][0])), Decimal(str(source[numbers[-1] - 1][1]))
        require(0 <= start < stop and stop - start <= 40, 'Keep complete source regions within40seconds')
        cursor = start
        for number in numbers:
            first, last = (Decimal(str(value)) for value in source[number - 1][:2])
            require(first == cursor and first < last, 'Do not bridge unreviewed source gaps')
            cursor = last
        scaled_start, scaled_stop = start * RATE, stop * RATE
        first, last = int(scaled_start), int(scaled_stop)
        require(scaled_start == first and scaled_stop == last
            and first == int(float(start) * RATE) and last == int(float(stop) * RATE),
            'A new source reservation must match exact and legacy sample ownership')
        require(previous <= first and all(last <= item['start_sample'] or first >= item['end_sample']
            for item in producer['original_source_ranges']), 'Source reservations overlap or are unordered')
        regions.append({'unit_ids': numbers, 'source_start': float(start), 'source_end': float(stop),
            'start_sample': first, 'end_sample': last, 'gain_db': 0.0})
        selected.update(numbers)
        previous = last
    return regions, sorted(selected)


def source_authority(config, producer):
    source = config['source']
    require(source['pcm'] == producer['original_source_pcm'] and all(state.matches(bound)
        for bound in (source['media'], source['pcm'], source['decode_receipt'])), 'Original audio authority changed')
    receipt = state.load(source['decode_receipt']['path'])
    require(receipt['status'] == 0 and receipt['source'] == source['media'] and state.matches(receipt['pcm'])
        and all(receipt['pcm'][key] == source['pcm'][key] for key in ('bytes', 'sha256')),
        'Original decode does not bind this source and preserved PCM payload')
    command = receipt['command']
    require(command == ['ffmpeg', '-nostdin', '-v', 'error', '-i', source['media']['path'],
        '-map', source['audio_stream'], '-vn', '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s16le',
        receipt['pcm']['path']], 'Do not accept an unbound stream, gain or resampling recipe')


def expected_text(before, config):
    lines = before['translation.tsv'].read_text().splitlines()
    overrides = state.load(before['spoken_overrides.json'])
    for region in config['regions']:
        for unit in region['units']:
            number = unit['unit_id']
            require(lines[number - 1].split('\t', 1)[0] == str(number), 'Unordered baseline translation IDs')
            lines[number - 1] = f'{number}\t{unit["display"]}'
            overrides[str(number)] = '…'
    return '\n'.join(lines) + '\n', overrides


def expected_stats(before, selected):
    stats = copy.deepcopy(before)
    for number in selected:
        stats['per'][number - 1] = [0, 1.0, 0.0, stats['per'][number - 1][3]]
        stats['duration_seconds'][number - 1] = 0.0
    stats['normal'] = sum(row[0] == 0 for row in stats['per'])
    stats['rate_up'] = sum(row[0] > 0 for row in stats['per'])
    stats['rate_ge_25'] = sum(row[0] >= 25 for row in stats['per'])
    state.check_stats(stats, len(stats['per']))
    return stats


def compose(base, composite, source, regions):
    require(len(base) == len(composite), 'Baseline TTS/composite lengths differ')
    for region in regions:
        first, last = region['start_sample'], region['end_sample']
        require(last <= len(base) and last <= len(source), 'New reservations cannot invent source EOF silence')
        base[first:last] = 0
        composite[first:last] = source[first:last]


def narration(work, contract, observed_at):
    dependencies = state.narration_dependencies(work)
    dependencies['actual_source_stage_contract'] = state.identity(work / 'editorial-source.json')
    return {'origin': KIND, 'observed_at': observed_at, 'dependencies': dependencies,
        'waveform': contract['composite_waveform'],
        'statistics': state.hashes(work, ['tts_stats.json', 'timing_review.json']),
        'producer': {'kind': KIND, 'contract': dependencies['actual_source_stage_contract']},
        'historical_generation_proven': False, 'human_listened': False,
        'pronunciation_or_emotion_approved': False, 'source_issue_resolved': False}


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    work = Path(config['work'])
    require(not output.exists() and not output.is_relative_to(work) and not work.is_relative_to(output),
        'Preserve successful/interrupted stages and never stage over the canonical source')
    with (work / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not (work / 'speech_groups.json').exists(), 'Grouped source staging is not supported')
        for name, bound in config['baseline_files'].items():
            require(Path(bound['path']) == work / name and state.matches(bound), f'Stale baseline input: {name}')
        require(all((work / name).exists() == (name in config['baseline_files']) for name in editorial_stage.OPTIONAL),
            'Optional source policy was omitted')
        for bound in [*config['authority'].values(), *config['evidence']]:
            require(state.matches(bound), 'Current baseline authority or source evidence changed')
        require(Path(config['authority']['exit']['path']).read_text().strip() == '0', 'Baseline quality did not exit0')
        authority = state.load(config['authority']['receipt']['path'])
        baseline = state.load(work / 'narration.json')
        require(authority['passed'] and authority['quality']['allowed'] and not authority['quality']['blockers']
            and authority['quality']['bindings']['waveform'] == baseline['waveform']
            and authority['master'] == state.load(work / 'render_state.json')['output']
            and authority['quality']['bindings']['source'] == config['source']['media'], 'Wrong current baseline authority')
        require(all(config['baseline_files'][name]['sha256'] == digest
            for name, digest in authority['quality']['bindings']['inputs'].items()), 'Baseline quality input digests differ')
        state.validate_compilation(work)
        producer = baseline['producer']
        source_authority(config, producer)
        regions, selected = selections(config, state.cues(work / 'en.srt'), producer)
        output.mkdir()
        manifest = {'kind': KIND, 'complete': False, 'config': state.identity(config_path),
            'adapter': state.identity(__file__), 'implementations': implementations(),
            'canonical_media_modified': False, 'rendered_or_installed': False,
            'human_listened': False, 'source_issue_resolved': False}
        save_json(output / 'manifest.json', manifest)
        try:
            files = {name: editorial_stage.archive(bound, output / 'baseline' / name)
                for name, bound in config['baseline_files'].items()}
            waves = {name: editorial_stage.archive(bound, output / 'baseline' / f'{name}.wav') for name, bound in {
                'base': producer['base_tts_waveform'], 'composite': baseline['waveform'],
                'source': producer['original_source_pcm']}.items()}
            evidence = {name: editorial_stage.archive(bound, output / 'baseline' / name) for name, bound in {
                'master.mp4': authority['master'], 'upload.mp4': authority['upload'], 'package.json': authority['package'],
                'check.log': config['authority']['receipt'], 'check.exit': config['authority']['exit'],
                'runtime.py': config['authority']['runtime']}.items()}
            shadow = output / work.name
            shadow.mkdir()
            before = {name: Path(item['archive']['path']) for name, item in files.items()}
            for name in (*editorial_stage.INPUTS, *editorial_stage.OPTIONAL):
                if name in before:
                    editorial_stage.archive(files[name]['archive'], shadow / name)
            text, overrides = expected_text(before, config)
            atomic_write(shadow / 'translation.tsv', text)
            save_json(shadow / 'spoken_overrides.json', overrides)
            video_workflow.compile_translation(shadow)
            stats = expected_stats(state.load(before['tts_stats.json']), selected)
            save_json(shadow / 'tts_stats.json', stats)
            base = editorial_stage.read_wave(waves['base']['archive']['path']).copy()
            composite = editorial_stage.read_wave(waves['composite']['archive']['path']).copy()
            source = editorial_stage.read_wave(waves['source']['archive']['path'])
            compose(base, composite, source, regions)
            combined = sorted(copy.deepcopy(producer['original_source_ranges']) + regions, key=lambda item: item['start_sample'])
            reserved = sorted(producer['original_source_unit_ids'] + selected)
            audit = editorial_stage.audio_checks(base, composite, source, state.cues(shadow / 'en.srt'),
                state.cues(shadow / 'spoken.srt'), stats, combined, reserved)
            editorial_stage.write_wave(output / 'base.wav', base)
            editorial_stage.write_wave(output / 'composite.wav', composite)
            contract = {'kind': KIND, 'adapter': manifest['adapter'], 'implementations': manifest['implementations'],
                'baseline_files': files, 'baseline_waveforms': waves, 'baseline_evidence': evidence,
                'source_authority': config['source'], 'evidence': config['evidence'],
                'added_source_ranges': regions, 'added_source_unit_ids': selected,
                'original_source_ranges': combined, 'original_source_unit_ids': reserved,
                'base_waveform': state.identity(output / 'base.wav'), 'composite_waveform': state.identity(output / 'composite.wav'),
                'source_eof_gap_samples': producer['source_eof_gap_samples'], 'audit': audit,
                'new_TTS_or_ASR': False, 'source_issue_resolved': False}
            save_json(shadow / 'editorial-source.json', contract)
            save_json(shadow / 'narration.json', narration(shadow, contract, state.now()))
            require(all(state.matches(bound) for bound in config['baseline_files'].values()), 'Live source changed during staging')
            require(all(state.matches(item['original']) for item in evidence.values()), 'Live media authority changed during staging')
            manifest.update(complete=True, work=str(shadow), contract=state.identity(shadow / 'editorial-source.json'),
                files={path.name: state.identity(path) for path in shadow.iterdir() if path.is_file()}, audit=audit)
            save_json(output / 'manifest.json', manifest)
            del base, composite, source
            return check(config_path, output)
        except BaseException as error:
            manifest.update(complete=False, error=f'{type(error).__name__}: {error}')
            save_json(output / 'manifest.json', manifest)
            raise


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    manifest = state.load(output / 'manifest.json')
    require(manifest['complete'] is True and manifest['kind'] == KIND and manifest['config'] == state.identity(config_path)
        and manifest['adapter'] == state.identity(__file__) and manifest['implementations'] == implementations(),
        'Stale or incomplete actual-source stage')
    require(all(manifest[name] is False for name in ('canonical_media_modified', 'rendered_or_installed',
        'human_listened', 'source_issue_resolved')), 'Actual-source staging authority was expanded')
    work = output / Path(config['work']).name
    require(manifest['work'] == str(work) and manifest['contract'] == state.identity(work / 'editorial-source.json'),
        'Actual-source project or contract route changed')
    contract = state.load(manifest['contract']['path'])
    expected_files = {name for name in (*editorial_stage.INPUTS, *editorial_stage.OPTIONAL)
        if name in config['baseline_files']} | {'narration.json', 'editorial-source.json'}
    require(set(manifest['files']) == expected_files and all(Path(bound['path']) == work / name
        for name, bound in manifest['files'].items()), 'Incomplete or foreign staged-file inventory')
    require(contract['kind'] == KIND and contract['adapter'] == manifest['adapter']
        and contract['implementations'] == manifest['implementations'] and contract['source_authority'] == config['source']
        and contract['evidence'] == config['evidence'] and contract['new_TTS_or_ASR'] is False
        and contract['source_issue_resolved'] is False, 'Actual-source authority or claims changed')
    for bound in [*manifest['files'].values(), *config['evidence'], contract['base_waveform'], contract['composite_waveform']]:
        require(state.matches(bound), 'Staged audio, source evidence or files changed')
    require(set(contract['baseline_files']) == set(config['baseline_files']), 'Baseline input inventory changed')
    before = {}
    for name, item in contract['baseline_files'].items():
        require(item['original'] == config['baseline_files'][name] and state.matches(item['archive'])
            and all(item['original'][key] == item['archive'][key] for key in ('bytes', 'mtime_ns', 'sha256')),
            'Baseline archive differs from its explicit input')
        before[name] = Path(item['archive']['path'])
    producer = state.load(before['narration.json'])['producer']
    evidence_originals = {'master.mp4': state.load(config['authority']['receipt']['path'])['master'],
        'upload.mp4': state.load(config['authority']['receipt']['path'])['upload'],
        'package.json': state.load(config['authority']['receipt']['path'])['package'],
        'check.log': config['authority']['receipt'], 'check.exit': config['authority']['exit'],
        'runtime.py': config['authority']['runtime']}
    require(set(contract['baseline_evidence']) == set(evidence_originals), 'Incomplete baseline media authority')
    for name, bound in evidence_originals.items():
        item = contract['baseline_evidence'][name]
        require(item['original'] == bound and state.matches(item['archive'])
            and all(item['original'][key] == item['archive'][key] for key in ('bytes', 'mtime_ns', 'sha256')),
            'Baseline media or current-quality archive changed')
    source_authority(config, producer)
    regions, selected = selections(config, state.cues(before['en.srt']), producer)
    combined = sorted(copy.deepcopy(producer['original_source_ranges']) + regions, key=lambda item: item['start_sample'])
    reserved = sorted(producer['original_source_unit_ids'] + selected)
    require(contract['added_source_ranges'] == regions and contract['added_source_unit_ids'] == selected
        and contract['original_source_ranges'] == combined and contract['original_source_unit_ids'] == reserved
        and contract['source_eof_gap_samples'] == producer['source_eof_gap_samples'], 'Actual-source inventory changed')
    for name, bound in {'base': producer['base_tts_waveform'], 'composite': state.load(before['narration.json'])['waveform'],
            'source': producer['original_source_pcm']}.items():
        item = contract['baseline_waveforms'][name]
        require(item['original'] == bound and state.matches(item['archive'])
            and all(item['original'][key] == item['archive'][key] for key in ('bytes', 'mtime_ns', 'sha256')),
            'Baseline waveform archive changed')
    text, overrides = expected_text(before, config)
    require((work / 'translation.tsv').read_text() == text and state.load(work / 'spoken_overrides.json') == overrides,
        'Subtitle/placeholder edits escaped the selected complete source units')
    for name in (*editorial_stage.INPUTS, *editorial_stage.OPTIONAL):
        if name in before and name not in ('translation.tsv', 'spoken_overrides.json', 'tts_stats.json',
                'compilation.json', *state.COMPILE_OUTPUTS):
            require((work / name).read_bytes() == before[name].read_bytes(), f'Unselected source input changed: {name}')
    state.validate_compilation(work)
    stats = state.load(work / 'tts_stats.json')
    require(stats == expected_stats(state.load(before['tts_stats.json']), selected), 'Non-TTS source statistics changed')
    base = editorial_stage.read_wave(contract['baseline_waveforms']['base']['archive']['path']).copy()
    composite = editorial_stage.read_wave(contract['baseline_waveforms']['composite']['archive']['path']).copy()
    source = editorial_stage.read_wave(contract['baseline_waveforms']['source']['archive']['path'])
    compose(base, composite, source, regions)
    require(np.array_equal(base, editorial_stage.read_wave(contract['base_waveform']['path']))
        and np.array_equal(composite, editorial_stage.read_wave(contract['composite_waveform']['path'])),
        'Source PCM differs or the new reservation changed exterior audio')
    audit = editorial_stage.audio_checks(base, composite, source, state.cues(work / 'en.srt'),
        state.cues(work / 'spoken.srt'), stats, combined, reserved)
    require(audit == contract['audit'] == manifest['audit'], 'Strict actual-source audio audit changed')
    observed = state.load(work / 'narration.json')
    require(observed == narration(work, contract, observed['observed_at']), 'Actual-source narration provenance changed')
    try:
        state.validate_narration(work)
    except ValueError:
        pass
    else:
        raise ValueError('Bare frozen narration validator accepted the new actual-source origin')
    return {'passed': True, 'manifest': state.identity(output / 'manifest.json'), 'work': str(work),
        'added_source_unit_ids': selected, 'added_source_regions': regions, **audit,
        'canonical_media_modified': False, 'rendered_or_installed': False,
        'new_TTS_or_ASR': False, 'human_listened': False, 'source_issue_resolved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check'])
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    result = globals()[arguments.action](arguments.config, arguments.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
