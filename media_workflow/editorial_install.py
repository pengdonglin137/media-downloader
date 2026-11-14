"""Install verified editorial media and current project authority without re-encoding."""

import argparse
import copy
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

import check_videos
import delivery_quality
import dubbing_state as state
import speech_groups
import speech_review
import video_package
from paired_delivery_transaction import canonical, durable_json, project_lock
from . import editorial_media, editorial_quality, file_transaction
from .speech_variants import require


KIND = 'installed-complete-utterance-editorial-media-v1'
GENERATED = ('editorial_install.json', 'narration.json', 'render_state.json', 'upload_export.json',
    'speech_review.json', 'speech_review.html', 'speech_database.json', 'delivery_quality.json',
    'delivery_check.json', 'release.json')
FROZEN_NARRATION = state.validate_narration
FROZEN_PACKAGE = video_package.validate
FROZEN_BLOCKERS = speech_groups.quality_blockers
PACKAGE_FIELDS = ('intro_audio', 'font', 'intro_frames', 'body_offset_seconds', 'body_duration_seconds',
    'duration_seconds', 'chapters', 'audio_tracks', 'body', 'subtitles', 'burned_subtitles')
FROZEN_WRITERS = tuple((module, name, text, getattr(module, name)) for module, name, text in
    ((speech_review, 'save_json', False), (speech_review, 'atomic_write', True),
        (check_videos, 'save_json', False), (delivery_quality, 'save_json', False)))


def configuration(path):
    path = canonical(path)
    config = state.load(path)
    require(set(config) == {'kind', 'work', 'candidate_config', 'candidate_verification', 'scope'}
        and config['kind'] == KIND and config['scope'] == 'explicit-history-preserving-installation',
        'Unsupported installation configuration')
    work = canonical(config['work'])
    require(work.is_dir() and work.name.startswith('.zh_work_yt_'), 'Expected the existing canonical project')
    for name in ('candidate_config', 'candidate_verification'):
        require(state.matches(config[name]), 'Candidate/configuration changed')
    candidate = Path(config['candidate_verification']['path']).parent
    require(candidate != work and candidate.is_relative_to(work.parent / '.workflow_logs'),
        'Candidate must belong to the hidden local workflow area')
    result = editorial_media.check(config['candidate_config']['path'], candidate)
    candidate_work = Path(state.load(candidate / 'preparation.json')['work'])
    require(candidate_work.name == work.name and state.source_video(work) == state.source_video(candidate_work),
        'Candidate belongs to another source project')
    return config, work, candidate, candidate_work, result


def load_source(work, transaction=None):
    work = canonical(work)
    pointer = state.load(work / 'editorial_install.json')
    require(pointer['kind'] == KIND and pointer['work'] == str(work)
        and pointer['runtime'] == state.identity(__file__) and state.matches(pointer['source']),
        'Wrong/stale installed-source pointer')
    source = state.load(pointer['source']['path'])
    require(source['kind'] == KIND and source['work'] == str(work) and source['adapter'] == state.identity(__file__)
        and source['transaction_adapter'] == state.identity(file_transaction.__file__), 'Wrong installed-source runtime')
    for bound in (source['config'], source['candidate_preparation'], source['candidate_package'],
            source['candidate_verification'], source['candidate_narration'], source['candidate_database'],
            source['transaction_prepared'], source['old_database'], source['original_video']):
        require(state.matches(bound), 'Installed-source evidence changed')
    config = state.load(source['config']['path'])
    require(config['work'] == str(work) and config['kind'] == KIND
        and config['candidate_verification'] == source['candidate_verification'], 'Installed configuration differs')
    prepared = state.load(source['transaction_prepared']['path'])
    require(prepared['work'] == str(work), 'Transaction belongs to another canonical project')
    active = transaction if transaction is not None else file_transaction.FileTransaction(Path(source['transaction_prepared']['path']).parent)
    require(active.journal['prepared'] == source['transaction_prepared'], 'Installation transaction differs')
    if transaction is None:
        require(active.journal['state'] == 'committed', 'Uncommitted installation requires explicit recovery, not normal playback authority')
        active.check()
    else:
        require(active.journal['state'] == 'installing', 'Only the current controlled installation may use pending authority')
    candidate = state.load(source['candidate_package']['path'])
    candidate_work = Path(state.load(source['candidate_preparation']['path'])['work'])
    copied = editorial_media.derivation(candidate_work)['files']
    require(set(source['files']) == set(copied), 'Installed source/review/display inventory differs')
    for name, bound in source['files'].items():
        require(bound == file_transaction.rebase(copied[name], work / name)
            and state.matches(bound), 'Installed text/review/display/plan differs from verified candidate')
    for name, key in (('master', 'output'), ('upload', 'upload_output')):
        require(source['media'][name] == file_transaction.rebase(candidate[key], prepared['files'][name]['destination'])
            and state.matches(source['media'][name]), 'Installed media is not the exact verified candidate')
    require(not (work / 'speech_groups.json').exists(), 'Grouped production is not supported by this installation route')
    state.validate_compilation(work)
    return source, pointer['source'], active


def expected_narration(work, source, source_identity, observed_at):
    result = copy.deepcopy(state.load(source['candidate_narration']['path']))
    result.update(origin=KIND, observed_at=observed_at, dependencies=state.narration_dependencies(work))
    result['dependencies']['editorial_install_source'] = source_identity
    result['producer'].update(kind=KIND, installed_source=source_identity,
        candidate_narration=source['candidate_narration'])
    return result


def validate_narration(work, transaction=None):
    work = canonical(work)
    source, source_identity, _ = load_source(work, transaction)
    actual = state.load(work / 'narration.json')
    require(isinstance(actual.get('observed_at'), str) and actual['observed_at']
        and actual == expected_narration(work, source, source_identity, actual['observed_at'])
        and state.matches(actual['waveform']), 'Installed narration or current WAV changed')
    state.check_stats(state.load(work / 'tts_stats.json'), len(state.cues(work / 'en.srt')))
    return actual


def expected_package(work, source, source_identity, created_at):
    candidate = state.load(source['candidate_package']['path'])
    result = {name: copy.deepcopy(candidate[name]) for name in PACKAGE_FIELDS}
    waveform = state.load(source['candidate_narration']['path'])['waveform']
    result.update(kind=KIND, producer=state.identity(__file__), implementation_sha256=state.digest(__file__),
        media_producer=candidate['producer'], upstream_package=source['candidate_package'],
        installed_source=source_identity, transaction_prepared=source['transaction_prepared'],
        candidate_only=False, requires_committed_transaction=True, created_at=created_at,
        dependencies=state.render_dependencies(work, Path(waveform['path']), state.source_video(work)),
        plan=state.identity(work / 'package-plan.json'), output=source['media']['master'],
        upload_output=source['media']['upload'], human_listened=False, pronunciation_or_emotional_approval=False)
    return result


def validate_package(work, transaction=None):
    work = canonical(work)
    source, source_identity, _ = load_source(work, transaction)
    rendering = state.load(work / 'render_state.json')
    require(state.matches(rendering['package_manifest']), 'Installed package manifest changed')
    package = state.load(rendering['package_manifest']['path'])
    require(isinstance(package.get('created_at'), str) and package['created_at']
        and package == expected_package(work, source, source_identity, package['created_at']),
        'Installed package differs from the verified encoder, source, media or chapters')
    return FROZEN_PACKAGE(work, allow_implementation_change=True)


def scoped_blockers(work, report, database, transaction=None):
    source, source_identity, _ = load_source(work, transaction)
    output = Path(source_identity['path']).parent
    classification = state.load(output / 'source-classification.json')
    require(classification['adapter'] == state.identity(__file__) and classification['project'] == str(work)
        and classification['revision'] == report['revision'] and classification['database_decisions_written'] == 0
        and classification['findings'] == editorial_quality.findings(work, report, validate_narration(work, transaction)),
        'Installed actual-source classification is stale')
    exempt = {finding['issue']['issue_id'] for finding in classification['findings']}
    restricted = copy.deepcopy(report)
    restricted['issues'] = [issue for issue in report['issues'] if issue['issue_id'] not in exempt]
    return FROZEN_BLOCKERS(work, restricted, database)


@contextmanager
def runtime(work, transaction=None):
    work = canonical(work)
    source, _, _ = load_source(work, transaction)
    configuration(source['config']['path'])
    require(state.validate_narration is FROZEN_NARRATION and video_package.validate is FROZEN_PACKAGE
        and speech_groups.quality_blockers is FROZEN_BLOCKERS, 'Do not nest or mix installation runtimes')
    validate_narration(work, transaction)
    state.validate_narration = lambda selected: validate_narration(work, transaction) \
        if Path(selected).resolve() == work else FROZEN_NARRATION(selected)
    video_package.validate = lambda selected, allow_implementation_change=False: validate_package(work, transaction) \
        if Path(selected).resolve() == work else FROZEN_PACKAGE(selected, allow_implementation_change)
    speech_groups.quality_blockers = lambda selected, report, database: scoped_blockers(work, report, database, transaction) \
        if Path(selected).resolve() == work else FROZEN_BLOCKERS(selected, report, database)
    try:
        yield
    finally:
        state.validate_narration = FROZEN_NARRATION
        video_package.validate = FROZEN_PACKAGE
        speech_groups.quality_blockers = FROZEN_BLOCKERS


@contextmanager
def generated_writes(work, transaction):
    work = canonical(work)
    require(all(getattr(module, name) is original for module, name, _, original in FROZEN_WRITERS),
        'Do not nest or mix unknown generated-file writers')
    def writer(original, text):
        def write(path, value):
            selected = Path(path).resolve()
            if selected.parent == work:
                require(selected.name in GENERATED, 'Native writer attempted an undeclared canonical mutation')
                return transaction.write(selected.name, value, text=text)
            return original(path, value)
        return write
    for module, name, text, original in FROZEN_WRITERS:
        setattr(module, name, writer(original, text))
    try:
        yield
    finally:
        for module, name, _, original in FROZEN_WRITERS:
            setattr(module, name, original)


def prepare(config_path, output):
    config_path, output = canonical(config_path), canonical(output)
    config, work, candidate, candidate_work, checked = configuration(config_path)
    require(not output.exists() and output.is_relative_to(work.parent / '.workflow_logs')
        and not output.is_relative_to(candidate) and not candidate.is_relative_to(output), 'Use a fresh isolated installation directory')
    package = state.load(candidate / 'package.json')
    candidate_source = editorial_media.derivation(candidate_work)
    stage = state.load(candidate_source['stage_contract']['path'])
    require(all(state.matches(item['original']) for item in stage['baseline_files'].values()),
        'Canonical semantic/narration baseline changed; do not overwrite newer work')
    preflight_source = state.load(candidate_source['upstream_derivation']['path'])
    preflight_config = state.load(preflight_source['config']['path'])
    require(state.matches(preflight_config['database']) and state.matches(preflight_config['baseline_speech_report'])
        and speech_review.database_path(work) == Path(preflight_config['database']['path'])
        and all(state.matches(bound) for bound in preflight_config['review_files'].values()),
        'Canonical review/database history changed; incorporate it before installation')
    for name in ('master', 'upload'):
        expected = stage['baseline_evidence'][name + '.mp4']['original']
        require(state.matches(expected), 'Canonical baseline changed; do not install over another revision')
    expected_master = check_videos.output_path(work.parent, state.load(work / 'meta.json')['title'])
    require(str(expected_master) == stage['baseline_evidence']['master.mp4']['original']['path'], 'Canonical title/output route differs')
    reviews = {path.name for path in work.iterdir() if path.is_file() and editorial_quality.review_name(path.name)}
    require(reviews == {name for name in candidate_source['files'] if editorial_quality.review_name(name)},
        'New canonical review history must be incorporated before installation')
    files = {name: {'destination': str(work / name), 'original': file_transaction.observed(work / name), 'source': bound}
        for name, bound in candidate_source['files'].items()}
    for name, field in (('master', 'output'), ('upload', 'upload_output')):
        original = stage['baseline_evidence'][name + '.mp4']['original']
        files[name] = {'destination': original['path'], 'original': original, 'source': package[field]}
    for name in GENERATED:
        require(name not in files, 'Generated output overlaps a copied source')
        files[name] = {'destination': str(work / name), 'original': file_transaction.observed(work / name), 'source': None}
    output.mkdir(parents=True)
    transaction = file_transaction.FileTransaction.prepare(output / 'file-transaction', work, files,
        {'config': state.identity(config_path), 'candidate_preparation': state.identity(candidate / 'preparation.json'),
            'candidate_package': state.identity(candidate / 'package.json'), 'candidate_verification': config['candidate_verification']})
    old_database = speech_review.database_path(work)
    source = {'kind': KIND, 'adapter': state.identity(__file__), 'transaction_adapter': state.identity(file_transaction.__file__),
        'work': str(work), 'config': state.identity(config_path), 'candidate_preparation': state.identity(candidate / 'preparation.json'),
        'candidate_package': state.identity(candidate / 'package.json'), 'candidate_verification': config['candidate_verification'],
        'candidate_narration': state.identity(candidate_work / 'narration.json'),
        'candidate_database': state.identity(candidate / 'speech-review.sqlite3'), 'old_database': state.identity(old_database),
        'transaction_prepared': state.identity(transaction.directory / 'prepared.json'),
        'original_video': state.identity(state.source_video(work)),
        'files': {name: file_transaction.rebase(bound, work / name) for name, bound in candidate_source['files'].items()},
        'media': {name: file_transaction.rebase(package[field], files[name]['destination'])
            for name, field in (('master', 'output'), ('upload', 'upload_output'))}}
    durable_json(output / 'installed-source.json', source)
    with closing(sqlite3.connect(Path(source['candidate_database']['path']).as_uri() + '?mode=ro', uri=True)) as database:
        before = editorial_quality.database_snapshot(database)
        with closing(sqlite3.connect(output / 'speech-review.sqlite3')) as destination:
            database.backup(destination)
            require(editorial_quality.database_snapshot(destination) == before, 'Latest candidate database backup differs')
    durable_json(output / 'database-before.json', before)
    receipt = {'kind': KIND, 'complete': True, 'created_at': state.now(), 'adapter': state.identity(__file__),
        'config': state.identity(config_path), 'source': state.identity(output / 'installed-source.json'),
        'database_before': state.identity(output / 'database-before.json'),
        'initial_database': state.identity(output / 'speech-review.sqlite3'), 'candidate_checked': checked,
        'canonical_files_modified': False, 'installed': False, 'human_listened': False}
    durable_json(output / 'preparation.json', receipt)
    return {'prepared': True, 'receipt': state.identity(output / 'preparation.json'),
        'transaction_entries': len(files), 'canonical_files_modified': False, 'installed': False}


def prepared(config_path, output):
    config_path, output = canonical(config_path), canonical(output)
    receipt = state.load(output / 'preparation.json')
    require(receipt['kind'] == KIND and receipt['complete'] is True and receipt['adapter'] == state.identity(__file__)
        and receipt['config'] == state.identity(config_path) and state.matches(receipt['source'])
        and state.matches(receipt['database_before']), 'Stale/incomplete installation preparation')
    source = state.load(receipt['source']['path'])
    require(source['config'] == receipt['config'] and source['adapter'] == receipt['adapter'], 'Wrong prepared source')
    return receipt, source, file_transaction.FileTransaction(output / 'file-transaction')


def apply(config_path, output):
    output = canonical(output)
    config, work, candidate, candidate_work, _ = configuration(config_path)
    receipt, source, transaction = prepared(config_path, output)
    require(transaction.journal['state'] == 'prepared' and state.matches(receipt['initial_database']),
        'Do not restart a live, failed, completed or changed installation')
    try:
        transaction.install()
        pointer = {'kind': KIND, 'work': str(work), 'runtime': state.identity(__file__), 'source': receipt['source']}
        transaction.write('editorial_install.json', pointer)
        transaction.write('narration.json', expected_narration(work, source, receipt['source'], state.now()))
        manifest = expected_package(work, source, receipt['source'], state.now())
        durable_json(output / 'package.json', manifest)
        waveform = Path(state.load(work / 'narration.json')['waveform']['path'])
        rendering = {'origin': KIND, 'observed_at': state.now(),
            'dependencies': state.render_dependencies(work, waveform, state.source_video(work)),
            'output': source['media']['master'], 'options': {'subs': 'soft', 'sub_pos': 'bottom',
                'video_reused': True, 'preserve_video': True}, 'package_manifest': state.identity(output / 'package.json')}
        transaction.write('render_state.json', rendering)
        transaction.write('upload_export.json', {'kind': KIND, 'producer': state.identity(__file__),
            'media_producer': manifest['media_producer'], 'dependencies': {'package': rendering['package_manifest'],
                'master': source['media']['master'], 'captions': manifest['burned_subtitles']},
            'output': source['media']['upload'], 'original_candidate_verification': source['candidate_verification'],
            'copied_without_reencoding': True, 'human_listened': False, 'burned_text_full_human_checked': False})
        database = output / 'speech-review.sqlite3'
        with runtime(work, transaction), generated_writes(work, transaction):
            report = speech_review.scan(work, database, with_audio=True)
            durable_json(output / 'source-classification.json', {'adapter': state.identity(__file__), 'project': str(work),
                'revision': report['revision'], 'database_decisions_written': 0,
                'findings': editorial_quality.findings(work, report, validate_narration(work, transaction)),
                'human_listened': False, 'original_pause_role_or_intelligibility_approved': False})
            editorial_media.same_report(state.load(candidate_work / 'speech_review.json'), report)
            quality = delivery_quality.require(work, database)
            transaction.write('delivery_quality.json', quality)
            release = delivery_quality.release(work, database)
            package = validate_package(work, transaction)
            state.validate_render(work, Path(package['output']['path']))
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
            after = editorial_quality.database_snapshot(connection)
        history = editorial_quality.verify_history(state.load(output / 'database-before.json'), after,
            report['project_id'], report['revision'])
        durable_json(output / 'database-after.json', after)
        durable_json(output / 'database-history.json', history)
        require(quality['bindings']['inputs']['render_state.json'] == state.digest(work / 'render_state.json')
            and release['technical_state'] == 'verified' and release['quality']['bindings'] == quality['bindings'],
            'Current canonical render/quality/release bindings differ')
        for name in GENERATED:
            require(transaction.journal['versions'][name], 'Native output was not captured by the transaction')
        proof = {'passed': True, 'checked_at': state.now(), 'adapter': state.identity(__file__),
            'preparation': state.identity(output / 'preparation.json'), 'source': receipt['source'],
            'package': state.identity(output / 'package.json'), 'candidate_verification': source['candidate_verification'],
            'database_after': state.identity(output / 'database-after.json'), 'database_history': state.identity(output / 'database-history.json'),
            'source_classification': state.identity(output / 'source-classification.json'),
            'canonical_files': {key: bound for key, bound in transaction.current().items()},
            'current_candidates': len(report['issues']), 'quality_state': quality['state'],
            'original_source_rule_only_classifications': len(state.load(output / 'source-classification.json')['findings']),
            'formal_media_exact_candidate_bytes_and_mtime': True,
            'candidate_decode_and_packet_schedule_proofs_transferred_by_exact_file_identity': True,
            'new_full_av_decode_or_encoding_or_tts_or_asr': False,
            'human_listened': False, 'pronunciation_or_emotional_approval': False}
        durable_json(output / 'installation-verification.json', proof)
        transaction.commit(state.identity(output / 'installation-verification.json'))
        return check(config_path, output)
    except BaseException as error:
        failure = {'failed_at': state.now(), 'error': f'{type(error).__name__}: {error}',
            'historical_failed_attempt_preserved': True, 'human_listened': False}
        try:
            if transaction.journal['state'] != 'committed':
                failure['rollback'] = transaction.rollback()
            else:
                failure['committed_postcheck_failure_no_implicit_rollback'] = True
        except BaseException as recovery_error:
            failure['recovery_error'] = f'{type(recovery_error).__name__}: {recovery_error}'
        durable_json(output / 'failed-attempt.json', failure)
        raise


def check(config_path, output):
    output = canonical(output)
    _, work, candidate, _, _ = configuration(config_path)
    receipt, source, transaction = prepared(config_path, output)
    transaction_result = transaction.check()
    with runtime(work):
        package = validate_package(work)
        state.validate_render(work, Path(package['output']['path']))
        quality = delivery_quality.require(work, output / 'speech-review.sqlite3')
    release = state.load(work / 'release.json')
    require(release['output'] == package['output'] and release['quality']['bindings'] == quality['bindings']
        and release['technical_state'] == 'verified', 'Current installed release is stale')
    upload = state.load(work / 'upload_export.json')
    require(upload['kind'] == KIND and upload['output'] == package['upload_output']
        and upload['dependencies']['package'] == state.identity(output / 'package.json')
        and upload['dependencies']['master'] == package['output']
        and upload['dependencies']['captions'] == package['burned_subtitles']
        and upload['copied_without_reencoding'] is True, 'Current upload is stale')
    proof = state.load(output / 'installation-verification.json')
    require(proof['canonical_files'] == transaction.current() and proof['package'] == state.identity(output / 'package.json'),
        'Installed current files differ from actual post-installation verification')
    with closing(sqlite3.connect((output / 'speech-review.sqlite3').as_uri() + '?mode=ro', uri=True)) as database:
        require(editorial_quality.database_snapshot(database) == state.load(output / 'database-after.json'),
            'Installed production database changed; review history requires a separate current verification')
    try:
        FROZEN_NARRATION(work)
    except ValueError:
        pass
    else:
        raise ValueError('Bare frozen narration validation accepted the special installed source')
    return {'passed': True, 'installed': True, 'transaction': transaction_result, 'master': package['output'],
        'upload': package['upload_output'], 'quality_state': quality['state'], 'current_candidates': proof['current_candidates'],
        'verification': state.identity(output / 'installation-verification.json'), 'human_listened': False,
        'pronunciation_or_emotional_approval': False}


def recover(config_path, output):
    receipt, source, transaction = prepared(config_path, output)
    require(source['config'] == state.identity(config_path), 'Recovery belongs to another explicit request')
    return transaction.rollback()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'apply', 'check', 'recover'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = state.load(args.config)
    work = canonical(config['work'])
    with project_lock(work):
        result = globals()[args.action](args.config.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
