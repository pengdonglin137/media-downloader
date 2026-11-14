"""Bind one historical intro plan to an exact transaction archive, without changing frozen code."""

import argparse
import json
from contextlib import contextmanager
from pathlib import Path
from types import FunctionType

import dubbing_state as state
import video_package
from paired_delivery_transaction import canonical, checked, durable_json, project_lock
from . import editorial_install, editorial_media, editorial_quality, editorial_sample_grid, file_transaction
from .speech_variants import require


KIND = 'immutable-historical-intro-plan-runtime-v1'
SCOPE = 'one-derived-project-exact-archived-plan-only'
FROZEN_DERIVATION = editorial_quality.derivation
FROZEN_LOAD = state.load
FROZEN_MATCHES = state.matches


def implementations():
    return {name: state.identity(module.__file__) for name, module in (
        ('state', state), ('package', video_package), ('quality', editorial_quality),
        ('media', editorial_media), ('install', editorial_install),
        ('sample_grid', editorial_sample_grid), ('transaction', file_transaction))}


def authority(path):
    require(state.load is FROZEN_LOAD and state.matches is FROZEN_MATCHES, 'Reject foreign global state I/O hooks')
    path = canonical(path)
    config_identity = state.identity(path)
    config = state.load(path)
    require(set(config) == {'kind', 'scope', 'adapter', 'implementations', 'derivation',
        'archive_preparation', 'archive_terminal'} and config['kind'] == KIND and config['scope'] == SCOPE,
        'Unsupported immutable historical-plan authority')
    require(config['adapter'] == state.identity(__file__) and config['implementations'] == implementations(),
        'Historical-plan runtime implementation changed')
    for name in ('derivation', 'archive_preparation', 'archive_terminal'):
        checked(config[name])
    record = state.load(config['derivation']['path'])
    work = canonical(record['work'])
    require(Path(config['derivation']['path']) == work / 'editorial-derivation.json'
        and record['kind'] == editorial_quality.KIND and record['adapter'] == config['implementations']['quality'],
        'Historical-plan authority belongs to another derived project')
    checked(record['stage_manifest'])
    checked(record['stage_contract'])
    stage = state.load(record['stage_manifest']['path'])
    require(stage['contract'] == record['stage_contract'], 'Historical stage/contract binding differs')
    contract = state.load(record['stage_contract']['path'])
    package_reference = contract['baseline_evidence']['package.json']['archive']
    checked(package_reference)
    original = state.load(package_reference['path'])['plan']
    original_path = canonical(original['path'])
    require(original_path.name == 'package-plan.json' and original_path.parent.name.startswith('.zh_work_yt_'),
        'Expected a canonical original intro plan')
    canonical_work = original_path.parent
    workflow = canonical_work.parent / '.workflow_logs'
    require(work != canonical_work and work.name == canonical_work.name and work.is_relative_to(workflow),
        'Only an isolated derived project may use historical-plan authority')
    preparation = Path(config['archive_preparation']['path'])
    terminal = Path(config['archive_terminal']['path'])
    require(preparation.name == 'prepared.json' and terminal == preparation.parent / 'transaction.json'
        and preparation.is_relative_to(workflow), 'Historical archive is not an explicit project transaction')
    transaction = file_transaction.FileTransaction(preparation.parent)
    require(transaction.journal['state'] in {'committed', 'rolled_back'}
        and transaction.journal['prepared'] == config['archive_preparation']
        and transaction.prepared['work'] == str(canonical_work), 'Historical transaction is incomplete or foreign')
    entry = transaction.prepared['files'].get('package-plan.json')
    require(entry is not None and entry['destination'] == str(original_path) and entry['original'] == original
        and entry['archive'] == file_transaction.rebase(original, preparation.parent / 'archive/package-plan.json'),
        'Archive does not preserve the exact historical plan identity')
    checked(entry['archive'])
    require(not preparation.parent.is_relative_to(work), 'Historical archive cannot be derived-project content')
    require(state.identity(path) == config_identity and all(state.matches(config[name])
        for name in ('derivation', 'archive_preparation', 'archive_terminal')), 'Historical authority changed during validation')
    return {'config': config_identity, 'work': str(work), 'canonical_work': str(canonical_work),
        'derivation': config['derivation'], 'stage_manifest': record['stage_manifest'],
        'original_plan': original, 'archived_plan': entry['archive'],
        'archive_preparation': config['archive_preparation'], 'archive_terminal': config['archive_terminal']}


class HistoricalState:
    def __init__(self, original, archive):
        self.original = original
        self.archive = archive
        self.matches_count = 0
        self.loads_count = 0

    def __getattr__(self, name):
        return getattr(state, name)

    def matches(self, observation):
        if observation == self.original:
            self.matches_count += 1
            return FROZEN_MATCHES(self.archive)
        return FROZEN_MATCHES(observation)

    def load(self, path):
        if Path(path) == Path(self.original['path']):
            self.loads_count += 1
            checked(self.archive)
            return FROZEN_LOAD(self.archive['path'])
        return FROZEN_LOAD(path)


def historical_derivation(work, binding):
    require(state.load is FROZEN_LOAD and state.matches is FROZEN_MATCHES
        and FROZEN_DERIVATION.__globals__['state'] is state and FROZEN_DERIVATION.__closure__ is None,
        'Reject foreign state hooks or an unsupported frozen checker')
    proxy = HistoricalState(binding['original_plan'], binding['archived_plan'])
    namespace = {**FROZEN_DERIVATION.__globals__, 'state': proxy}
    checker = FunctionType(FROZEN_DERIVATION.__code__, namespace,
        FROZEN_DERIVATION.__name__, FROZEN_DERIVATION.__defaults__)
    checker.__kwdefaults__ = FROZEN_DERIVATION.__kwdefaults__
    result = checker(work)
    require(proxy.matches_count == proxy.loads_count == 1,
        'Frozen historical identity/read guard was not exercised exactly once')
    require(state.identity(Path(work) / 'editorial-derivation.json') == binding['derivation'],
        'Selected derivation changed during historical validation')
    return result


@contextmanager
def runtime(path):
    require(editorial_quality.derivation is FROZEN_DERIVATION, 'Reject nested or foreign historical-plan dispatch')
    path = canonical(path)
    binding = authority(path)
    selected_work = Path(binding['work'])

    def dispatch(work):
        if Path(work).resolve() != selected_work:
            return FROZEN_DERIVATION(work)
        require(authority(path) == binding, 'Historical authority changed while in use')
        return historical_derivation(work, binding)

    dispatch(selected_work)
    editorial_quality.derivation = dispatch
    try:
        yield binding
    finally:
        editorial_quality.derivation = FROZEN_DERIVATION


def validate(path):
    with runtime(path) as binding:
        return {'passed': True, 'kind': KIND, 'adapter': state.identity(__file__), 'binding': binding,
            'global_state_io_unchanged': state.load is FROZEN_LOAD and state.matches is FROZEN_MATCHES,
            'canonical_files_modified': False, 'human_listened': False, 'pronunciation_or_emotion_approved': False}


def profile(binding, install_config, output, stage_config, stage):
    return {'kind': KIND, 'adapter': state.identity(__file__), 'authority': binding['config'],
        'installation_config': state.identity(install_config), 'installation_preparation': state.identity(output / 'preparation.json'),
        'stage_config': state.identity(stage_config), 'stage_manifest': state.identity(stage / 'manifest.json'),
        'canonical_work': binding['canonical_work'], 'historical_archive': binding['archived_plan'],
        'human_listened': False, 'pronunciation_or_emotion_approved': False}


def install(path, operation, install_config, output, stage_config, stage):
    path, install_config, output, stage_config, stage = map(canonical,
        (path, install_config, output, stage_config, stage))
    require(operation in {'prepare', 'apply', 'check'}, 'Unsupported historical installation operation')
    binding = authority(path)
    work = Path(binding['canonical_work'])
    require(state.load(install_config)['work'] == str(work) and output.is_relative_to(work.parent / '.workflow_logs'),
        'Installation escapes the explicit historical project')
    require(binding['stage_manifest'] == state.identity(stage / 'manifest.json')
        and state.load(stage / 'manifest.json')['config'] == state.identity(stage_config), 'Wrong explicit stage route')
    candidate = Path(state.load(install_config)['candidate_verification']['path']).parent
    candidate_work = Path(state.load(candidate / 'preparation.json')['work'])
    require(state.load(candidate_work / 'editorial-media-source.json')['upstream_derivation'] == binding['derivation'],
        'Installation candidate belongs to another historical derivation')
    profile_path = output / 'historical-runtime.json'
    application_path = output / 'historical-application.json'
    failure_path = output / f'historical-{operation}-failure.json'
    with project_lock(work), runtime(path), editorial_sample_grid.runtime(stage_config, stage):
        if operation == 'prepare':
            require(not output.exists(), 'Use a new transaction; never retry a failed installation')
        else:
            require(state.load(profile_path) == profile(binding, install_config, output, stage_config, stage)
                and not failure_path.exists(), 'Installation historical profile changed or already failed')
            if operation == 'apply':
                require(not application_path.exists(), 'Do not reapply a completed historical installation')
            else:
                application = state.load(application_path)
                require(application['kind'] == KIND and application['adapter'] == state.identity(__file__)
                    and application['runtime'] == state.identity(profile_path)
                    and application['authority'] == binding['config'], 'Missing historical installation proof')
                checked(application['native_verification'])
                checked(application['transaction'])
        try:
            result = getattr(editorial_install, operation)(install_config, output)
            if operation == 'prepare':
                durable_json(profile_path, profile(binding, install_config, output, stage_config, stage))
            elif operation == 'apply':
                durable_json(application_path, {'kind': KIND, 'adapter': state.identity(__file__),
                    'runtime': state.identity(profile_path), 'authority': binding['config'],
                    'native_verification': state.identity(output / 'installation-verification.json'),
                    'transaction': state.identity(output / 'file-transaction/transaction.json'), 'created_at': state.now(),
                    'new_encoding_tts_asr_or_human_approval': False})
        except BaseException as error:
            if operation != 'check' and output.exists() and not failure_path.exists():
                durable_json(failure_path, {'kind': KIND, 'adapter': state.identity(__file__),
                    'authority': binding['config'], 'operation': operation, 'failed_at': state.now(),
                    'error': f'{type(error).__name__}: {error}', 'historical_failed_attempt_preserved': True})
            raise
    require(state.load is FROZEN_LOAD and state.matches is FROZEN_MATCHES, 'Global state I/O changed during installation')
    return {'kind': KIND, 'adapter': state.identity(__file__), 'authority': binding['config'],
        'operation': operation, 'native_result': result, 'runtime': state.identity(profile_path),
        'historical_plan_bound_to_exact_archive': True, 'global_state_io_unchanged': True,
        'human_listened': False, 'pronunciation_or_emotion_approved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'install'))
    parser.add_argument('authority', type=Path)
    parser.add_argument('--operation', choices=('prepare', 'apply', 'check'))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--stage-config', type=Path)
    parser.add_argument('--stage', type=Path)
    args = parser.parse_args()
    options = (args.operation, args.config, args.output, args.stage_config, args.stage)
    if args.action == 'validate':
        require(all(value is None for value in options), 'Unexpected installation arguments')
        result = validate(args.authority)
    else:
        require(all(value is not None for value in options), 'Explicit installation, operation and stage required')
        result = install(args.authority, *options)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
