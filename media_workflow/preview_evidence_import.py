"""Add explicit evidence to complete previews without generating or changing speech."""

import argparse
import copy
import fcntl
import json
import shutil
from pathlib import Path

import dubbing_state as state
from prepare_videos import save_json
from . import speech_variants
from .speech_variants import require


KIND = 'complete-preview-evidence-import-v1'
ROOT = Path(__file__).resolve().parents[1]


def configuration(path):
    config = state.load(path)
    require(set(config) == {'kind', 'source_plan', 'source_manifest', 'additional_evidence', 'reason'},
        'Unexpected preview evidence-import fields')
    require(config['kind'] == KIND and isinstance(config['reason'], str) and config['reason'].strip(),
        'An explicit evidence-import reason is required')
    require(isinstance(config['additional_evidence'], list) and 0 < len(config['additional_evidence']) <= 12,
        'Expected one to twelve additional evidence bindings')
    bindings = [config['source_plan'], config['source_manifest'], *config['additional_evidence']]
    require(all(state.matches(bound) for bound in bindings), 'Stale preview or additional evidence')
    paths = [bound['path'] for bound in bindings]
    require(len(set(paths)) == len(paths), 'Duplicate evidence-import paths')
    manifest_path = Path(config['source_manifest']['path'])
    require(manifest_path.name == 'manifest.json', 'Bind a complete source preview manifest')
    speech_variants.check(config['source_plan']['path'], manifest_path.parent)
    source_plan = state.load(config['source_plan']['path'])
    require(not set(paths[2:]).intersection(bound['path'] for bound in source_plan['reference_evidence']),
        'Additional evidence is already bound by the source preview')
    return config


def expected_plan(config, config_path):
    result = copy.deepcopy(state.load(config['source_plan']['path']))
    result['reference_evidence'].extend([*config['additional_evidence'], state.identity(config_path),
        state.identity(__file__)])
    return result


def import_previews(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    plan = state.load(config['source_plan']['path'])
    work = Path(plan['work'])
    upstream = Path(config['source_manifest']['path']).parent
    require(not output.exists() and ROOT not in (output, *output.parents)
        and not output.is_relative_to(work) and not work.is_relative_to(output)
        and not output.is_relative_to(upstream) and not upstream.is_relative_to(output),
        'Use a fresh external import directory, separate from source previews and canonical work')
    with (work / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        output.mkdir(parents=True)
        save_json(output / 'plan.json', expected_plan(config, config_path))
        manifest = copy.deepcopy(state.load(config['source_manifest']['path']))
        manifest['plan'] = state.identity(output / 'plan.json')
        for case in manifest['cases']:
            destination = output / (case['key'] + '.wav')
            shutil.copy2(case['waveform']['path'], destination)
            case['waveform'] = state.identity(destination)
        manifest['evidence_import'] = {'kind': KIND, 'producer': state.identity(__file__),
            'config': state.identity(config_path), 'source_plan': config['source_plan'],
            'source_manifest': config['source_manifest'], 'additional_evidence': config['additional_evidence'],
            'reason': config['reason'], 'imported_at': state.now(), 'new_synthesis_or_waveform_generation': False,
            'candidate_or_selection_changed': False, 'adapter_field_retains_original_synthesis_adapter': True}
        save_json(output / 'manifest.json', manifest)
        return check(config_path, output)


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    source = state.load(config['source_manifest']['path'])
    plan_path, manifest_path = output / 'plan.json', output / 'manifest.json'
    require(state.load(plan_path) == expected_plan(config, config_path),
        'Imported plan changed text, geometry, candidates, voice or existing references')
    manifest = state.load(manifest_path)
    require('evidence_import' in manifest, 'Missing explicit evidence-import provenance')
    provenance = manifest['evidence_import']
    require(set(provenance) == {'kind', 'producer', 'config', 'source_plan', 'source_manifest',
        'additional_evidence', 'reason', 'imported_at', 'new_synthesis_or_waveform_generation',
        'candidate_or_selection_changed', 'adapter_field_retains_original_synthesis_adapter'},
        'Unknown or incomplete import authority')
    require(isinstance(provenance['imported_at'], str) and provenance['imported_at'], 'Missing import time')
    require(provenance == {'kind': KIND, 'producer': state.identity(__file__),
        'config': state.identity(config_path), 'source_plan': config['source_plan'],
        'source_manifest': config['source_manifest'], 'additional_evidence': config['additional_evidence'],
        'reason': config['reason'], 'imported_at': provenance['imported_at'], 'new_synthesis_or_waveform_generation': False,
        'candidate_or_selection_changed': False, 'adapter_field_retains_original_synthesis_adapter': True},
        'Evidence import changed provenance or claimed synthesis, selection or expanded authority')
    expected = copy.deepcopy(source)
    expected['plan'] = state.identity(plan_path)
    for case in expected['cases']:
        original = case['waveform']
        destination = output / (case['key'] + '.wav')
        copied = state.identity(destination)
        require(all(copied[name] == original[name] for name in ('bytes', 'mtime_ns', 'sha256')),
            'Imported complete waveform differs from the original preview')
        case['waveform'] = copied
    expected['evidence_import'] = provenance
    require(manifest == expected, 'Imported manifest changed original observations, audio or review authority')
    speech_variants.validate_plan(plan_path)
    base = speech_variants.check(plan_path, output)
    return {'passed': True, 'kind': KIND, 'producer': state.identity(__file__),
        'config': state.identity(config_path), 'plan': state.identity(plan_path),
        'manifest': state.identity(manifest_path), 'cases': base['cases'],
        'all_original_cases_retained': True, 'additional_evidence': len(config['additional_evidence']),
        'new_synthesis_or_waveform_generation': False, 'formal_adoption': False, 'human_listened': False,
        'pronunciation_or_emotion_approved': False, 'automatic_winner': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['import', 'check'])
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    result = (import_previews if arguments.action == 'import' else check)(arguments.config, arguments.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
