"""Complete an explicit late failed source stage without rewriting its successful PCM."""

import argparse
import fcntl
import json
import shutil
from pathlib import Path

import dubbing_state as state
from prepare_videos import save_json
from . import editorial_stage, source_reservation_stage
from .speech_variants import require


KIND = 'complete-unit-source-reservation-late-recovery-v1'


def check(config_path, output):
    output = Path(output).resolve()
    manifest = state.load(output / 'manifest.json')
    recovery = manifest['completion']
    require(recovery['kind'] == KIND and recovery['adapter'] == state.identity(__file__)
        and recovery['stage_adapter'] == state.identity(source_reservation_stage.__file__)
        and recovery['audio_or_source_contract_rewritten'] is False and recovery['old_failure_reclassified'] is False,
        'Unsupported late-stage completion or expanded recovery authority')
    require(all(state.matches(bound) for bound in recovery['failed_files'])
        and state.matches(recovery['failed_manifest']) and state.matches(recovery['failed_exit']),
        'Historical failed stage or original successful PCM changed')
    failed = state.load(recovery['failed_manifest']['path'])
    require(failed['complete'] is False and failed['adapter'] == recovery['stage_adapter']
        and Path(recovery['failed_exit']['path']).read_text().strip() == '1'
        and failed['error'] == 'ValueError: 工程必须有且仅有一个完整原片 MP4',
        'Only the evidenced missing-source-link failure may use this recovery')
    config = source_reservation_stage.configuration(config_path)
    work = output / Path(config['work']).name
    original = Path(recovery['failed_manifest']['path']).parent / work.name
    contract = state.load(work / 'editorial-source.json')
    require((work / 'editorial-source.json').read_bytes() == (original / 'editorial-source.json').read_bytes(),
        'Late recovery must not alter the original produced source contract')
    require((work / 'download').is_symlink()
        and (work / 'download').resolve() == (Path(config['work']) / 'download').resolve()
        and state.identity(state.source_video(work)) == config['source']['media'], 'Recovered source link changed')
    copied = {name for name in (*editorial_stage.INPUTS, *editorial_stage.OPTIONAL) if name in config['baseline_files']}
    for name in copied:
        require((work / name).read_bytes() == (original / name).read_bytes()
            and (work / name).stat().st_mtime_ns == (original / name).stat().st_mtime_ns,
            'Recovery changed already compiled source/text/statistics')
    require({Path(bound['path']) for bound in recovery['failed_files']} == {
        path.resolve() for path in Path(recovery['failed_manifest']['path']).parent.rglob('*')
        if path.is_file() and not path.is_symlink()}, 'Historical failed-file inventory is incomplete')
    require(set(manifest['files']) == copied | {'narration.json', 'editorial-source.json'}
        and not any((work / name).exists() for name in editorial_stage.FORBIDDEN), 'Recovered stage acquired production output')
    native = source_reservation_stage.check(config_path, output)
    require(contract['composite_waveform']['path'].startswith(str(original.parent) + '/')
        and contract['base_waveform']['path'].startswith(str(original.parent) + '/'),
        'Recovery did not reuse the already produced PCM')
    return {'passed': True, 'kind': KIND, 'native_source_contract_check': native,
        'completion': recovery, 'manifest': state.identity(output / 'manifest.json'),
        'new_TTS_ASR_or_PCM_rewrite': False, 'old_failure_exit': 1,
        'canonical_media_modified': False, 'rendered_or_installed': False,
        'human_listened': False, 'source_issue_resolved': False}


def recover(config_path, failed_output, failed_exit, output):
    config_path, failed_output, failed_exit, output = (Path(path).resolve()
        for path in (config_path, failed_output, failed_exit, output))
    config = source_reservation_stage.configuration(config_path)
    canonical = Path(config['work'])
    require(not output.exists() and all(not output.is_relative_to(parent) and not parent.is_relative_to(output)
        for parent in (canonical, failed_output)), 'Preserve the failed stage and canonical project')
    failed_manifest = failed_output / 'manifest.json'
    failed = state.load(failed_manifest)
    require(failed['complete'] is False and failed['kind'] == source_reservation_stage.KIND
        and failed['config'] == state.identity(config_path) and failed['adapter'] == state.identity(source_reservation_stage.__file__)
        and failed['implementations'] == source_reservation_stage.implementations()
        and failed['error'] == 'ValueError: 工程必须有且仅有一个完整原片 MP4'
        and failed_exit.read_text().strip() == '1', 'Not the supported evidenced late-stage failure')
    original = failed_output / canonical.name
    require(not (original / 'download').exists() and not (original / 'narration.json').exists(),
        'Recovery must not overwrite a source link or completed narration')
    with (canonical / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(all(state.matches(bound) for bound in config['baseline_files'].values()),
            'Canonical inputs changed after the failed stage')
        files = [state.identity(path) for path in sorted(failed_output.rglob('*'))
            if path.is_file() and not path.is_symlink()]
        output.mkdir()
        work = output / canonical.name
        work.mkdir()
        copied = {name for name in (*editorial_stage.INPUTS, *editorial_stage.OPTIONAL) if name in config['baseline_files']}
        for name in sorted(copied | {'editorial-source.json'}):
            shutil.copy2(original / name, work / name)
        (work / 'download').symlink_to(canonical / 'download', target_is_directory=True)
        contract = state.load(work / 'editorial-source.json')
        save_json(work / 'narration.json', source_reservation_stage.narration(work, contract, state.now()))
        recovery = {'kind': KIND, 'adapter': state.identity(__file__), 'stage_adapter': failed['adapter'],
            'failed_manifest': state.identity(failed_manifest), 'failed_exit': state.identity(failed_exit),
            'failed_files': files, 'audio_or_source_contract_rewritten': False,
            'old_failure_reclassified': False, 'source_link_added_only_to_new_project': True,
            'compiler_diagnostic_preserved_at_original': state.identity(original / 'normalization_changes.json')}
        manifest = {**failed, 'complete': True, 'work': str(work),
            'contract': state.identity(work / 'editorial-source.json'),
            'files': {path.name: state.identity(path) for path in work.iterdir() if path.is_file()},
            'audit': contract['audit'], 'completion': recovery}
        manifest.pop('error')
        save_json(output / 'manifest.json', manifest)
        try:
            result = check(config_path, output)
        except BaseException as error:
            save_json(output / 'recovery-error.json', {'error': f'{type(error).__name__}: {error}',
                'old_failure_preserved': True, 'canonical_project_modified': False})
            raise
        save_json(output / 'recovery-verification.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['recover', 'check'])
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--failed-output', type=Path)
    parser.add_argument('--failed-exit', type=Path)
    arguments = parser.parse_args()
    if arguments.action == 'recover':
        parser.error('--failed-output and --failed-exit are required') if (
            arguments.failed_output is None or arguments.failed_exit is None) else None
        result = recover(arguments.config, arguments.failed_output, arguments.failed_exit, arguments.output)
    else:
        result = check(arguments.config, arguments.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
