"""Expose typed actual-source selections without inventing synthesis metadata."""

import argparse
import copy
import json
import shutil
from pathlib import Path

import dubbing_state as state
from prepare_videos import save_json
from . import editorial_stage, source_reservation_resume
from .speech_variants import require


KIND = 'typed-complete-unit-actual-source-selection-v1'


def upstream(config_path, source_manifest):
    config_path, source_manifest = Path(config_path).resolve(), Path(source_manifest).resolve()
    require(source_manifest.name == 'manifest.json', 'Select an explicit recovered source manifest')
    source_reservation_resume.check(config_path, source_manifest.parent)
    manifest = state.load(source_manifest)
    require(manifest['config'] == state.identity(config_path), 'Source selection configuration changed')
    return manifest, state.load(manifest['contract']['path'])


def selections(contract, work):
    source = state.cues(work / 'en.srt')
    display = state.cues(work / 'zh.srt')
    spoken = state.cues(work / 'spoken.srt')
    selected = contract['added_source_unit_ids']
    require(selected == sorted(set(selected)) and selected, 'Incomplete actual-source selection inventory')
    result = []
    for number in selected:
        start, end, text = source[number - 1]
        require(spoken[number - 1] == (start, end, '…') and display[number - 1][:2] == (start, end),
            'An actual-source selection must have a literal nonspoken placeholder')
        regions = [region for region in contract['added_source_ranges'] if number in region['unit_ids']]
        require(len(regions) == 1, 'Each selected source unit must have one complete PCM owner')
        result.append({'kind': 'actual-source-retention', 'unit_id': number,
            'source_start': str(start), 'source_end': str(end), 'source': text,
            'display': display[number - 1][2], 'spoken': '…', 'source_range': copy.deepcopy(regions[0]),
            'source_authority': copy.deepcopy(contract['source_authority']),
            'TTS_preview_or_word_boundaries': None, 'human_listened': False, 'source_issue_resolved': False})
    return result


def expected_contract(original, source_manifest, work):
    require('cases' not in original and 'selection_derivation' not in original,
        'Do not reclassify an existing synthesis selection contract')
    return {**copy.deepcopy(original), 'kind': KIND, 'adapter': state.identity(__file__),
        'cases': selections(original, work), 'selection_derivation': {
            'kind': KIND, 'adapter': state.identity(__file__),
            'upstream_manifest': state.identity(source_manifest),
            'upstream_contract': state.load(source_manifest)['contract'],
            'upstream_writer': original['adapter'], 'new_audio_or_synthesis': False,
            'original_failed_exit_reclassified': False}}


def narration(work, contract, observed_at):
    dependencies = state.narration_dependencies(work)
    dependencies['actual_source_selection_contract'] = state.identity(work / 'editorial-source.json')
    return {'origin': KIND, 'observed_at': observed_at, 'dependencies': dependencies,
        'waveform': contract['composite_waveform'],
        'statistics': state.hashes(work, ['tts_stats.json', 'timing_review.json']),
        'producer': {'kind': KIND, 'contract': dependencies['actual_source_selection_contract']},
        'historical_generation_proven': False, 'human_listened': False,
        'pronunciation_or_emotion_approved': False, 'source_issue_resolved': False}


def prepare(config_path, source_manifest, output):
    config_path, source_manifest, output = (Path(path).resolve() for path in (config_path, source_manifest, output))
    source, contract = upstream(config_path, source_manifest)
    source_work = Path(source['work'])
    canonical = Path(state.load(config_path)['work'])
    require(not output.exists() and all(not output.is_relative_to(parent) and not parent.is_relative_to(output)
        for parent in (canonical, source_manifest.parent)), 'Preserve the source stage and canonical project')
    output.mkdir(parents=True)
    manifest = {'kind': KIND, 'config': state.identity(config_path), 'adapter': state.identity(__file__),
        'upstream_manifest': state.identity(source_manifest), 'complete': False,
        'new_audio_or_synthesis': False, 'canonical_project_modified': False,
        'rendered_or_installed': False, 'human_listened': False, 'source_issue_resolved': False}
    save_json(output / 'manifest.json', manifest)
    try:
        work = output / source_work.name
        work.mkdir()
        for name, bound in source['files'].items():
            if name not in ('narration.json', 'editorial-source.json'):
                shutil.copy2(bound['path'], work / name)
        (work / 'download').symlink_to(source_work / 'download', target_is_directory=True)
        save_json(work / 'editorial-source.json', expected_contract(contract, source_manifest, source_work))
        derived = state.load(work / 'editorial-source.json')
        save_json(work / 'narration.json', narration(work, derived, state.now()))
        manifest.update({'complete': True, 'work': str(work), 'contract': state.identity(work / 'editorial-source.json'),
            'files': {path.name: state.identity(path) for path in work.iterdir() if path.is_file()}})
        save_json(output / 'manifest.json', manifest)
        return check(config_path, output)
    except BaseException as error:
        manifest.update({'complete': False, 'error': f'{type(error).__name__}: {error}'})
        save_json(output / 'manifest.json', manifest)
        raise


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    manifest = state.load(output / 'manifest.json')
    require(manifest['kind'] == KIND and manifest['complete'] is True
        and manifest['config'] == state.identity(config_path) and manifest['adapter'] == state.identity(__file__)
        and state.matches(manifest['upstream_manifest']), 'Stale or incomplete actual-source selection bridge')
    require(all(manifest[name] is False for name in ('new_audio_or_synthesis', 'canonical_project_modified',
        'rendered_or_installed', 'human_listened', 'source_issue_resolved')), 'Selection authority was expanded')
    source_manifest = Path(manifest['upstream_manifest']['path'])
    source, original = upstream(config_path, source_manifest)
    work = output / Path(source['work']).name
    require(manifest['work'] == str(work) and manifest['contract'] == state.identity(work / 'editorial-source.json')
        and set(manifest['files']) == set(source['files']), 'Selection project or complete file inventory changed')
    for name, bound in manifest['files'].items():
        require(Path(bound['path']) == work / name and state.matches(bound), 'Derived source selection file changed')
        if name not in ('narration.json', 'editorial-source.json'):
            require(all(bound[key] == source['files'][name][key] for key in ('bytes', 'sha256', 'mtime_ns')),
                'A selection bridge may not change compiled source, display, speech or statistics')
    contract = state.load(manifest['contract']['path'])
    require(contract == expected_contract(original, source_manifest, Path(source['work'])),
        'Actual-source selections differ from exact original provenance or invent synthesis')
    require((work / 'download').is_symlink()
        and (work / 'download').resolve() == (Path(source['work']) / 'download').resolve()
        and not any((work / name).exists() for name in editorial_stage.FORBIDDEN), 'Invalid source link or production output')
    observed = state.load(work / 'narration.json')
    require(observed == narration(work, contract, observed['observed_at'])
        and state.matches(contract['base_waveform']) and state.matches(contract['composite_waveform']),
        'Actual-source narration or reused complete PCM changed')
    return {'passed': True, 'kind': KIND, 'manifest': state.identity(output / 'manifest.json'),
        'typed_actual_source_units': contract['added_source_unit_ids'], 'synthetic_selection_count': 0,
        'new_audio_or_synthesis': False, 'original_failed_exit_reclassified': False,
        'canonical_project_modified': False, 'rendered_or_installed': False, 'human_listened': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--source-manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.action == 'prepare':
        if arguments.source_manifest is None:
            parser.error('--source-manifest is required for prepare')
        result = prepare(arguments.config, arguments.source_manifest, arguments.output)
    else:
        result = check(arguments.config, arguments.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
