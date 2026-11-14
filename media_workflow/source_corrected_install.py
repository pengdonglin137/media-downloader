"""Compose exact source-revision checks with the immutable historical installation route."""

import argparse
import json
from contextlib import contextmanager
from pathlib import Path

import dubbing_state as state
from paired_delivery_transaction import durable_json
from . import editorial_history as history, editorial_install, editorial_media, editorial_sample_grid
from . import source_corrected_media, source_revision
from .speech_variants import require


KIND = 'source-corrected-historical-installation-runtime-v1'
SCOPE = 'one-verified-source-corrected-candidate-exact-history-only'
FROZEN_MEDIA_CHECK = editorial_media.check
FROZEN_GRID_RUNTIME = editorial_sample_grid.runtime


def implementations():
    return {name: state.identity(module.__file__) for name, module in (
        ('state', state), ('history', history), ('install', editorial_install), ('media', editorial_media),
        ('grid', editorial_sample_grid), ('source_corrected_media', source_corrected_media), ('source_revision', source_revision))}


def configuration(path):
    path = Path(path).resolve()
    config = state.load(path)
    require(set(config) == {'kind', 'scope', 'adapter', 'implementations', 'authority', 'media_config', 'installation_config'}
        and config['kind'] == KIND and config['scope'] == SCOPE, 'Unsupported source-corrected installation scope')
    require(config['adapter'] == state.identity(__file__) and config['implementations'] == implementations(),
        'Source-corrected installation implementation changed')
    for name in ('authority', 'media_config', 'installation_config'):
        require(state.matches(config[name]), f'Stale source-corrected installation input: {name}')
    binding = history.authority(config['authority']['path'])
    media_config = source_corrected_media.configuration(config['media_config']['path'])
    installation = state.load(config['installation_config']['path'])
    require(installation['kind'] == editorial_install.KIND
        and installation['scope'] == 'explicit-history-preserving-installation'
        and installation['work'] == binding['canonical_work']
        and installation['candidate_config'] == config['media_config'], 'Installation belongs to another candidate or canonical project')
    candidate = Path(installation['candidate_verification']['path']).parent.resolve()
    require(Path(installation['candidate_verification']['path']) == candidate / 'verification.json'
        and state.matches(installation['candidate_verification']), 'Require the actual complete source-corrected candidate verification')
    projection_config = state.load(media_config['projection_config']['path'])
    require(projection_config['stage_manifest'] == binding['stage_manifest'], 'Historical authority names another revised-source stage')
    require(state.load(candidate / 'source-corrected-media.json')['config'] == config['media_config'],
        'Candidate does not bind the explicit source-corrected encoder configuration')
    return config, binding, candidate, projection_config


@contextmanager
def runtime(path):
    path = Path(path).resolve()
    config, binding, candidate, captions = configuration(path)
    require(editorial_media.check is FROZEN_MEDIA_CHECK and editorial_sample_grid.runtime is FROZEN_GRID_RUNTIME,
        'Reject nested or foreign source-corrected installation dispatchers')
    selected_config = Path(config['media_config']['path']).resolve()
    stage_config = Path(captions['stage_config']['path']).resolve()
    stage = Path(captions['stage_manifest']['path']).parent.resolve()

    def media_dispatch(selected, output):
        if Path(selected).resolve() == selected_config and Path(output).resolve() == candidate:
            return source_corrected_media.check(selected_config, candidate)
        return FROZEN_MEDIA_CHECK(selected, output)

    @contextmanager
    def checked_source_stage():
        require(configuration(path)[1] == binding, 'Source-corrected installation authority changed')
        source_revision.check(stage_config, stage)
        yield
        source_revision.check(stage_config, stage)

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
    require(operation in {'prepare', 'apply', 'check'}, 'Unsupported explicit installation operation')
    path, output = Path(path).resolve(), Path(output).resolve()
    profile_path = output / 'source-corrected-installation-runtime.json'
    with runtime(path) as (config, binding, captions):
        profile = {'kind': KIND, 'adapter': state.identity(__file__), 'configuration': state.identity(path),
            'authority': config['authority'], 'media_config': config['media_config'],
            'installation_config': config['installation_config']}
        if operation != 'prepare':
            require(state.load(profile_path) == profile, 'Source-corrected installation profile changed')
        result = history.install(config['authority']['path'], operation, config['installation_config']['path'], output,
            captions['stage_config']['path'], Path(captions['stage_manifest']['path']).parent)
        if operation == 'prepare':
            durable_json(profile_path, profile)
    require(editorial_media.check is FROZEN_MEDIA_CHECK and editorial_sample_grid.runtime is FROZEN_GRID_RUNTIME,
        'Source-corrected installation dispatchers were not restored')
    return {'kind': KIND, 'adapter': state.identity(__file__), 'configuration': state.identity(path),
        'operation': operation, 'historical_result': result, 'historical_authority': binding['config'],
        'runtime': state.identity(profile_path),
        'new_encoding_TTS_ASR_or_human_approval': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'apply', 'check'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(install(arguments.config, arguments.operation, arguments.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
