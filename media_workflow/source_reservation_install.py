"""Bind one verified actual-source candidate to the immutable historical installer."""

import argparse
import json
from contextlib import contextmanager
from pathlib import Path

import dubbing_state as state
from paired_delivery_transaction import durable_json
from . import editorial_history as history, editorial_install, editorial_media, editorial_sample_grid
from . import source_reservation_media, source_reservation_selection
from .speech_variants import require


KIND = 'actual-source-history-preserving-installation-runtime-v1'
SCOPE = 'one-verified-actual-source-candidate-exact-history-only'
FROZEN_MEDIA_CHECK = editorial_media.check
FROZEN_GRID_RUNTIME = editorial_sample_grid.runtime


def implementations():
    return {name: state.identity(module.__file__) for name, module in (
        ('state', state), ('history', history), ('install', editorial_install), ('media', editorial_media),
        ('grid', editorial_sample_grid), ('source_media', source_reservation_media),
        ('source_selection', source_reservation_selection))}


def configuration(path):
    path = Path(path).resolve()
    config = state.load(path)
    require(set(config) == {'kind', 'scope', 'adapter', 'implementations', 'authority', 'media_profile',
        'installation_config', 'actual_source_verification', 'preinstall_verification'}
        and config['kind'] == KIND and config['scope'] == SCOPE, 'Unsupported actual-source installation scope')
    require(config['adapter'] == state.identity(__file__) and config['implementations'] == implementations(),
        'Actual-source installation implementation changed')
    for name in ('authority', 'media_profile', 'installation_config', 'actual_source_verification', 'preinstall_verification'):
        require(state.matches(config[name]), 'Actual-source installation authority changed')
    binding = history.authority(config['authority']['path'])
    media_profile, _ = source_reservation_media.configuration(config['media_profile']['path'])
    installation = state.load(config['installation_config']['path'])
    require(installation['kind'] == editorial_install.KIND
        and installation['scope'] == 'explicit-history-preserving-installation'
        and installation['work'] == binding['canonical_work']
        and installation['candidate_config'] == media_profile['media_config'], 'Installation selects another project or producer')
    candidate = Path(config['actual_source_verification']['path']).parent.resolve()
    actual = state.load(config['actual_source_verification']['path'])
    preinstall = state.load(config['preinstall_verification']['path'])
    require(Path(config['actual_source_verification']['path']) == candidate / 'actual-source-verification.json'
        and installation['candidate_verification'] == actual['native_verification']
        and Path(installation['candidate_verification']['path']) == candidate / 'verification.json'
        and actual['profile'] == config['media_profile'] and preinstall['passed'] is True
        and preinstall['actual_source_verification'] == config['actual_source_verification']
        and preinstall['formal_adoption'] is False, 'Require the exact verified candidate and bounded preinstall review')
    captions = state.load(media_profile['projection_config']['path'])
    require(captions['stage_manifest'] == binding['stage_manifest'], 'Historical authority selects another source stage')
    return config, binding, candidate, captions, media_profile


@contextmanager
def runtime(path):
    path = Path(path).resolve()
    config, binding, candidate, captions, media_profile = configuration(path)
    require(editorial_media.check is FROZEN_MEDIA_CHECK and editorial_sample_grid.runtime is FROZEN_GRID_RUNTIME,
        'Reject nested or foreign actual-source installation dispatchers')
    selected_config = Path(media_profile['media_config']['path']).resolve()
    stage_config = Path(captions['stage_config']['path']).resolve()
    stage = Path(captions['stage_manifest']['path']).parent.resolve()

    def media_dispatch(selected, output):
        if Path(selected).resolve() != selected_config or Path(output).resolve() != candidate:
            return FROZEN_MEDIA_CHECK(selected, output)
        require(editorial_media.check is media_dispatch, 'Actual-source media dispatcher changed')
        editorial_media.check = FROZEN_MEDIA_CHECK
        try:
            return source_reservation_media.check(config['media_profile']['path'], candidate)
        finally:
            editorial_media.check = media_dispatch

    @contextmanager
    def checked_source_stage():
        require(configuration(path)[1] == binding, 'Actual-source historical authority changed')
        source_reservation_selection.check(stage_config, stage)
        yield
        source_reservation_selection.check(stage_config, stage)

    def stage_dispatch(selected, output):
        if Path(selected).resolve() == stage_config and Path(output).resolve() == stage:
            return checked_source_stage()
        return FROZEN_GRID_RUNTIME(selected, output)

    editorial_media.check, editorial_sample_grid.runtime = media_dispatch, stage_dispatch
    try:
        yield config, binding, captions
    finally:
        editorial_media.check, editorial_sample_grid.runtime = FROZEN_MEDIA_CHECK, FROZEN_GRID_RUNTIME


def install(path, operation, output):
    require(operation in {'prepare', 'apply', 'check'}, 'Unsupported actual-source installation operation')
    path, output = Path(path).resolve(), Path(output).resolve()
    profile_path = output / 'actual-source-installation-runtime.json'
    with runtime(path) as (config, binding, captions):
        profile = {'kind': KIND, 'adapter': state.identity(__file__), 'configuration': state.identity(path),
            'authority': config['authority'], 'media_profile': config['media_profile'],
            'installation_config': config['installation_config'],
            'actual_source_verification': config['actual_source_verification'],
            'preinstall_verification': config['preinstall_verification']}
        if operation != 'prepare':
            require(state.load(profile_path) == profile, 'Actual-source installation profile changed')
        result = history.install(config['authority']['path'], operation, config['installation_config']['path'], output,
            captions['stage_config']['path'], Path(captions['stage_manifest']['path']).parent)
        if operation == 'prepare':
            durable_json(profile_path, profile)
    require(editorial_media.check is FROZEN_MEDIA_CHECK and editorial_sample_grid.runtime is FROZEN_GRID_RUNTIME,
        'Actual-source installation dispatchers were not restored')
    return {'kind': KIND, 'adapter': state.identity(__file__), 'configuration': state.identity(path),
        'operation': operation, 'historical_result': result, 'historical_authority': binding['config'],
        'runtime': state.identity(profile_path), 'new_encoding_TTS_ASR_or_human_approval': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'apply', 'check'))
    parser.add_argument('configuration', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(install(arguments.configuration, arguments.operation, arguments.output), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
