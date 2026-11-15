"""Reuse native media production with an explicit actual-source sample-coordinate view."""

import argparse
import copy
import fcntl
import json
from contextlib import contextmanager
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import dubbing_state as state
from prepare_videos import save_json
from . import editorial_media as media, source_reservation_projection as projection
from . import source_reservation_selection as selection
from .speech_variants import require


KIND = 'actual-source-media-sample-view-v1'


def sample_view(contract):
    require(contract['kind'] == selection.KIND and contract['new_TTS_or_ASR'] is False,
        'Only the explicit typed actual-source contract may use this coordinate view')
    result = copy.deepcopy(contract)
    require([case['unit_id'] for case in result['cases']] == contract['added_source_unit_ids'],
        'Actual-source sample-view inventory differs')
    for case in result['cases']:
        require(case['kind'] == 'actual-source-retention' and case['spoken'] == '…'
            and case['TTS_preview_or_word_boundaries'] is None
            and 'start_sample' not in case and 'end_sample' not in case,
            'Do not replace synthetic metadata or conceal existing physical sample fields')
        require(case['source_authority'] == contract['source_authority']
            and case['source_range'] in contract['added_source_ranges']
            and case['unit_id'] in case['source_range']['unit_ids'], 'Foreign actual-source sample owner')
        bounds = [Decimal(case[name]) for name in ('source_start', 'source_end')]
        require(all(value.is_finite() for value in bounds) and bounds[0] < bounds[1], 'Invalid complete source window')
        samples = [Fraction(value) * media.RATE for value in bounds]
        require(all(value.denominator == 1 for value in samples), 'Source-unit edges must map to exact PCM samples')
        first, last = map(int, samples)
        require(case['source_range']['start_sample'] <= first < last <= case['source_range']['end_sample'],
            'Sample-coordinate view escapes its complete actual-source reservation')
        case.update(start_sample=first, end_sample=last)
    return result


class SampleCoordinates:
    def __init__(self, bound):
        require(state.matches(bound), 'Physical source contract changed before diagnostic projection')
        self.bound = copy.deepcopy(bound)
        self.projected = sample_view(state.load(bound['path']))
        self.reads = 0

    def __getattr__(self, name):
        return getattr(state, name)

    def __setattr__(self, name, value):
        if name == 'validate_narration':
            setattr(state, name, value)
        else:
            object.__setattr__(self, name, value)

    def load(self, path):
        if Path(path).resolve() == Path(self.bound['path']).resolve():
            require(state.matches(self.bound), 'Physical source contract changed during diagnostic projection')
            self.reads += 1
            return copy.deepcopy(self.projected)
        return state.load(path)


@contextmanager
def diagnostic_view(bound):
    require(media.state is state, 'Reject nested or foreign media-local coordinate views')
    view = SampleCoordinates(bound)
    media.state = view
    try:
        yield view
    finally:
        media.state = state


def configuration(path):
    path = Path(path).resolve()
    config = state.load(path)
    require(set(config) == {'kind', 'adapter', 'media_config', 'projection_config', 'projection_manifest'}
        and config['kind'] == KIND and config['adapter'] == state.identity(__file__), 'Unsupported actual-source media profile')
    for name in ('media_config', 'projection_config', 'projection_manifest'):
        require(state.matches(config[name]), 'Actual-source media profile input changed')
    captions = state.load(config['projection_config']['path'])
    manifest = state.load(config['projection_manifest']['path'])
    require(captions['kind'] == projection.KIND and manifest['config'] == config['projection_config'],
        'Actual-source captions belong to another profile')
    native = state.load(config['media_config']['path'])
    preflight = state.load(native['preflight_config']['path'])
    require(preflight['projection_config'] == config['projection_config']
        and preflight['projection_manifest'] == config['projection_manifest']
        and preflight['stage_manifest'] == captions['stage_manifest'], 'Media and actual-source preflight differ')
    contract_bound = state.load(captions['stage_manifest']['path'])['contract']
    require(state.matches(contract_bound), 'Actual-source media contract changed')
    sample_view(state.load(contract_bound['path']))
    return config, contract_bound


@contextmanager
def runtime(path):
    config, contract = configuration(path)
    with projection.runtime(config['projection_config']['path'], Path(config['projection_manifest']['path']).parent):
        media.upstream(config['media_config']['path'])
        yield config, contract


def prepare(path, output):
    output = Path(output).resolve()
    with runtime(path) as (config, contract):
        result = media.prepare(config['media_config']['path'], output)
    save_json(output / 'actual-source-media.json', {'kind': KIND, 'adapter': state.identity(__file__),
        'profile': state.identity(path), 'preparation': state.identity(output / 'preparation.json'),
        'physical_source_contract': contract, 'source_contract_modified': False,
        'TTS_metadata_invented': False, 'canonical_media_modified': False, 'human_listened': False})
    return result


def encode(path, output):
    output = Path(output).resolve()
    prepared = state.load(output / 'actual-source-media.json')
    require(prepared['profile'] == state.identity(path) and prepared['adapter'] == state.identity(__file__)
        and prepared['preparation'] == state.identity(output / 'preparation.json'), 'Actual-source media preparation changed')
    with runtime(path) as (config, _):
        return media.encode(config['media_config']['path'], output)


def verify(path, output):
    output = Path(output).resolve()
    require(not (output / 'actual-source-verification.json').exists(), 'Reuse successful actual-source verification')
    with runtime(path) as (config, bound):
        with diagnostic_view(bound) as view:
            media.verify(config['media_config']['path'], output)
        require(view.reads > 0 and state.matches(bound), 'Native verification did not use the actual-source coordinate view')
    save_json(output / 'actual-source-verification.json', {'kind': KIND, 'adapter': state.identity(__file__),
        'profile': state.identity(path), 'native_verification': state.identity(output / 'verification.json'),
        'preparation': state.identity(output / 'actual-source-media.json'), 'physical_source_contract': bound,
        'projected_contract_signature': state.signature(view.projected), 'actual_native_contract_reads': view.reads,
        'sample_windows': [{'unit_id': case['unit_id'], 'start_sample': case['start_sample'],
            'end_sample': case['end_sample']} for case in view.projected['cases']],
        'source_contract_modified': False, 'global_state_IO_patched': False, 'TTS_metadata_invented': False,
        'canonical_media_modified': False, 'human_listened': False, 'source_issue_resolved': False})
    return check(path, output)


def check(path, output):
    output = Path(output).resolve()
    with runtime(path) as (config, bound):
        native = media.check(config['media_config']['path'], output)
    proof = state.load(output / 'actual-source-verification.json')
    prepared = state.load(output / 'actual-source-media.json')
    projected = sample_view(state.load(bound['path']))
    require(proof['kind'] == KIND and proof['adapter'] == state.identity(__file__)
        and proof['profile'] == state.identity(path) and proof['native_verification'] == native['verification']
        and proof['preparation'] == state.identity(output / 'actual-source-media.json')
        and proof['physical_source_contract'] == bound and proof['projected_contract_signature'] == state.signature(projected)
        and type(proof['actual_native_contract_reads']) is int and proof['actual_native_contract_reads'] > 0,
        'Stale actual-source diagnostic authority')
    require(prepared['profile'] == proof['profile'] and prepared['adapter'] == proof['adapter']
        and prepared['preparation'] == state.identity(output / 'preparation.json')
        and prepared['physical_source_contract'] == bound, 'Actual-source preparation belongs to another contract')
    require(proof['sample_windows'] == [{'unit_id': case['unit_id'], 'start_sample': case['start_sample'],
        'end_sample': case['end_sample']} for case in projected['cases']], 'Actual-source diagnostic sample coordinates changed')
    require(all(proof[name] is False for name in ('source_contract_modified', 'global_state_IO_patched',
        'TTS_metadata_invented', 'canonical_media_modified', 'human_listened', 'source_issue_resolved')),
        'Actual-source diagnostic authority was expanded')
    diagnostics = state.load(native['verification']['path'])['fixed_offset_audio_diagnostics']
    expected = [f'editorial-{case["unit_id"]}' for case in projected['cases']] + [
        f'source-{index + 1}' for index in range(len(projected['original_source_ranges']))]
    require([entry['label'] for entry in diagnostics] == expected, 'Native diagnostic coverage omitted an actual-source region')
    return {**native, 'actual_source_verification': state.identity(output / 'actual-source-verification.json'),
        'typed_actual_source_windows': proof['sample_windows'], 'source_contract_modified': False,
        'TTS_metadata_invented': False, 'source_issue_resolved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'encode', 'verify', 'check'))
    parser.add_argument('profile', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    with (arguments.output.resolve().parent / f'.{arguments.output.name}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = globals()[arguments.action](arguments.profile, arguments.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
