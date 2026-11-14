"""Derive an editorial project and scan current audio without losing review history."""

import argparse
import copy
import hashlib
import json
import re
import shutil
import sqlite3
import wave
from contextlib import closing, contextmanager
from pathlib import Path

import delivery_quality
import dubbing_state as state
import speech_groups
import speech_review
import video_package
from prepare_videos import save_json
from . import caption_projection, editorial_stage
from .speech_variants import require


KIND = 'derived-complete-utterance-editorial-quality-v1'
TABLES = {'terms', 'scans', 'projects', 'occurrences', 'issues', 'decisions', 'repair_resolutions'}
FROZEN_NARRATION = state.validate_narration
FROZEN_BLOCKERS = speech_groups.quality_blockers


def review_name(name):
    return name in {'meta.json', 'translation_audit.md', 'review_manifest.json', 'quality_resolutions.json'} or bool(
        re.fullmatch(r'review_feedback_[A-Za-z0-9_-]+\.json', name))


def database_snapshot(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    require(tables == TABLES, 'Unsupported review database schema; no unknown history tables may be omitted')
    result = {}
    for name in sorted(tables):
        rows = [list(row) for row in connection.execute(f'SELECT * FROM "{name}"')]
        result[name] = sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False, allow_nan=False))
    return {'tables': result, 'schema': [list(row) for row in connection.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')],
        'application_id': connection.execute('PRAGMA application_id').fetchone()[0],
        'user_version': connection.execute('PRAGMA user_version').fetchone()[0]}


def verify_history(before, after, project_id, revision):
    require(all(before[key] == after[key] for key in ('schema', 'application_id', 'user_version')),
        'Review database schema or application identity changed')
    old, current = before['tables'], after['tables']
    require(set(old) == set(current) == TABLES, 'Incomplete review history inventory')
    for name in TABLES - {'projects'}:
        previous_rows = {state.signature(row) for row in old[name]}
        current_rows = {state.signature(row) for row in current[name]}
        require(previous_rows <= current_rows, f'Existing database rows changed or vanished: {name}')
        if name not in {'scans', 'occurrences', 'issues'}:
            require(old[name] == current[name], f'Unexpected catalog/decision/resolution writes: {name}')
    expected_projects = [row for row in old['projects'] if row[0] != project_id] + [[project_id, revision]]
    require(sorted(expected_projects) == sorted(current['projects']), 'Unrelated database project pointers changed')
    revisions = {row[0] for row in old['scans']}
    require(revision not in revisions and len(current['scans']) == len(old['scans']) + 1,
        'Expected one new current-path scan, not a replaced or fabricated old scan')
    project_revisions = {row[0] for row in old['scans'] if row[1] == project_id}
    require(not any(row[0] in project_revisions and row[2] == 'needs_fix' for row in old['decisions']),
        'Historical confirmed project problems require an explicit supported recheck, not a path change')
    return {'before_rows': {name: len(rows) for name, rows in old.items()},
        'after_rows': {name: len(rows) for name, rows in current.items()},
        'all_old_rows_preserved': True, 'new_decisions_or_resolutions': 0}


def derivation(work):
    work = Path(work).resolve()
    record = state.load(work / 'editorial-derivation.json')
    require(record['kind'] == KIND and record['work'] == str(work)
        and record['adapter'] == state.identity(__file__)
        and record['caption_adapter'] == state.identity(caption_projection.__file__), 'Wrong/stale derived-source runtime')
    for bound in (record['config'], record['stage_manifest'], record['stage_contract'], record['projection_manifest']):
        require(state.matches(bound), 'Derived source authority changed')
    require(not (work / 'speech_groups.json').exists(), 'Grouped production is not supported by this editorial route')
    for name, bound in record['files'].items():
        require(Path(bound['path']) == work / name and state.matches(bound), 'Derived source/review/display file changed')
    stage = state.load(record['stage_manifest']['path'])
    stage_contract = state.load(record['stage_contract']['path'])
    require(stage['contract'] == record['stage_contract'], 'Upstream editorial contract differs')
    config = state.load(record['config']['path'])
    require(config['kind'] == KIND and config['stage_manifest'] == record['stage_manifest']
        and config['projection_manifest'] == record['projection_manifest']
        and config['projection_config'] == record['projection_config'], 'Derived source configuration differs')
    expected_copies = {name for name in (*editorial_stage.INPUTS, *editorial_stage.OPTIONAL) if name in stage['files']}
    require(set(record['source_copies']) == expected_copies and len(record['source_copies']) == len(expected_copies)
        and set(record['files']) == expected_copies | set(config['review_files']) |
        {f'{language}_display.srt' for language in caption_projection.LANGUAGES} | {'package-plan.json'},
        'Derived source/review/display inventory is incomplete or expanded')
    for name in record['source_copies']:
        require(name in stage['files'] and all(record['files'][name][key] == stage['files'][name][key]
            for key in ('bytes', 'sha256', 'mtime_ns')), 'Derived core text differs from verified staged text')
    projection = state.load(record['projection_manifest']['path'])
    for name, bound in {**config['review_files'], **{f'{language}_display.srt': projection['outputs'][f'{language}_display.srt']
            for language in caption_projection.LANGUAGES}}.items():
        require(all(record['files'][name][key] == bound[key] for key in ('bytes', 'sha256', 'mtime_ns')),
            'Derived review or display copy differs from its explicit source')
    require(record['files']['package-plan.json'] == state.identity(work / 'package-plan.json'), 'Package plan route changed')
    plan = state.load(work / 'package-plan.json')
    baseline = state.load(stage_contract['baseline_evidence']['package.json']['archive']['path'])
    require(state.matches(baseline['plan']), 'Historical intro plan changed')
    rebound = copy.deepcopy(state.load(baseline['plan']['path']))
    rebound['source_sha256'] = state.hashes(work, video_package.SOURCE_FILES)
    require(plan == rebound, 'Intro/chapters may not be regenerated or silently edited while rebinding source hashes')
    video_package.validate_plan(work, plan)
    return record, stage_contract


def expected_narration(work, record, contract, observed_at):
    dependencies = state.narration_dependencies(work)
    dependencies['editorial_derivation'] = state.identity(work / 'editorial-derivation.json')
    producer = {'kind': KIND, 'stage_narration': state.load(record['stage_manifest']['path'])['files']['narration.json'],
        'stage_contract': record['stage_contract'], 'base_tts_waveform': contract['base_waveform'],
        'original_source_pcm': contract['baseline_waveforms']['source']['archive'],
        'original_source_ranges': contract['original_source_ranges'],
        'original_source_unit_ids': contract['original_source_unit_ids'],
        'source_eof_gap_samples': contract['source_eof_gap_samples']}
    return {'origin': KIND, 'observed_at': observed_at, 'dependencies': dependencies,
        'waveform': contract['composite_waveform'], 'statistics': state.hashes(work, ['tts_stats.json', 'timing_review.json']),
        'producer': producer, 'historical_generation_proven': False, 'full_human_listening': False,
        'pronunciation_or_emotional_approval': False}


def validate_narration(work):
    work = Path(work).resolve()
    record, contract = derivation(work)
    observed = state.load(work / 'narration.json')
    require(isinstance(observed['observed_at'], str) and observed['observed_at']
        and observed == expected_narration(work, record, contract, observed['observed_at'])
        and state.matches(observed['waveform']), 'Derived narration provenance or audio changed')
    state.check_stats(state.load(work / 'tts_stats.json'), len(state.cues(work / 'en.srt')))
    return observed


def read_samples(path, first, last):
    with wave.open(str(path), 'rb') as audio:
        require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
            and 0 <= first < last <= audio.getnframes(), 'Invalid original-source classification PCM range')
        audio.setpos(first)
        payload = audio.readframes(last - first)
    require(len(payload) == (last - first) * 2, 'Incomplete original-source classification PCM')
    return payload


def classify_one(issue, spoken, producer, waveform):
    number = issue['unit_id']
    require(issue['kind'] == 'short-tail-after-pause' and issue.get('gap_seconds', 0) >= 1.4
        and number in producer['original_source_unit_ids'], 'Only an actual-source synthetic-tail rule can be inapplicable')
    start, end, text = spoken[number - 1]
    regions = [region for region in producer['original_source_ranges'] if number in region['unit_ids']]
    require(text == issue['text'] == '…' and len(regions) == 1, 'Classification is not an explicit original-voice reservation')
    region = regions[0]
    require(region['gain_db'] == 0 and region['source_start'] <= start < end <= region['source_end']
        and start <= issue['gap_start'] < issue['gap_end'] <= end
        and issue['gap_end'] + issue['tail_seconds'] <= end + .0005, 'Detected source gap/tail escapes the bound utterance')
    first, last = int(start * 24000), int(end * 24000)
    stop = min(last, region.get('actual_source_end_sample', region['end_sample']))
    require(first < stop <= last and region['end_sample'] - region.get('actual_source_end_sample', region['end_sample'])
        == region.get('container_silence_samples', 0), 'Invalid explicit source EOF evidence')
    source = read_samples(producer['original_source_pcm']['path'], first, stop)
    actual = read_samples(waveform['path'], first, last)
    base = read_samples(producer['base_tts_waveform']['path'], first, last)
    require(not any(base) and actual[:len(source)] == source and not any(actual[len(source):]),
        'Classified region contains synthesis or differs from actual source/declared EOF silence')
    return {'issue': copy.deepcopy(issue), 'source_range': region, 'source_bounds': [start, end],
        'absolute_samples': [first, last], 'actual_source_stop_sample': stop,
        'source_pcm_sha256': hashlib.sha256(source).hexdigest(),
        'composite_pcm_sha256': hashlib.sha256(actual).hexdigest(), 'base_tts_zero': True,
        'classification': 'actual-original-audio-pause-not-a-synthetic-sentence-tail',
        'human_listened': False, 'original_pause_role_or_intelligibility_approved': False}


def findings(work, report, narration):
    spoken = state.cues(work / 'spoken.srt')
    reserved = narration['producer']['original_source_unit_ids']
    return [classify_one(issue, spoken, narration['producer'], narration['waveform']) for issue in report['issues']
        if issue['kind'] == 'short-tail-after-pause' and issue.get('gap_seconds', 0) >= 1.4 and issue['unit_id'] in reserved]


def scoped_blockers(work, report, database):
    proof = state.load(work.parent / 'source-classification.json')
    narration = validate_narration(work)
    require(proof['adapter'] == state.identity(__file__) and proof['project'] == str(work)
        and proof['revision'] == report['revision'] and proof['findings'] == findings(work, report, narration)
        and proof['database_decisions_written'] == 0, 'Current original-source classification is stale')
    exempt = {item['issue']['issue_id'] for item in proof['findings']}
    restricted = copy.deepcopy(report)
    restricted['issues'] = [item for item in report['issues'] if item['issue_id'] not in exempt]
    return FROZEN_BLOCKERS(work, restricted, database)


@contextmanager
def narration_context(work):
    work = Path(work).resolve()
    record, _ = derivation(work)
    caption_projection.check(record['projection_config']['path'], Path(record['projection_manifest']['path']).parent)
    require(state.validate_narration is FROZEN_NARRATION and speech_groups.quality_blockers is FROZEN_BLOCKERS,
        'Do not nest or mix unknown narration/quality runtime patches')
    validate_narration(work)
    state.validate_narration = lambda selected: validate_narration(work) if Path(selected).resolve() == work else FROZEN_NARRATION(selected)
    speech_groups.quality_blockers = lambda selected, report, database: scoped_blockers(work, report, database) \
        if Path(selected).resolve() == work else FROZEN_BLOCKERS(selected, report, database)
    try:
        yield
    finally:
        state.validate_narration = FROZEN_NARRATION
        speech_groups.quality_blockers = FROZEN_BLOCKERS


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = state.load(config_path)
    require(config['kind'] == KIND and not output.exists(), 'Preserve successful or interrupted derived-quality work')
    for name in ('stage_config', 'stage_manifest', 'projection_config', 'projection_manifest', 'database', 'baseline_speech_report'):
        require(state.matches(config[name]), f'Stale quality preparation input: {name}')
    canonical = Path(state.load(config['stage_config']['path'])['work'])
    stage = Path(config['stage_manifest']['path']).parent
    require(all(not output.is_relative_to(parent) and not parent.is_relative_to(output) for parent in (canonical, stage)),
        'Derived quality output must not overlap an authoritative project')
    projection = state.load(config['projection_manifest']['path'])
    projection_config = state.load(config['projection_config']['path'])
    require(projection['config'] == config['projection_config'] and projection_config['stage_manifest'] == config['stage_manifest']
        and projection_config['stage_config'] == config['stage_config'], 'Projection belongs to another editorial source')
    caption_projection.check(config['projection_config']['path'], Path(config['projection_manifest']['path']).parent)
    source_manifest = state.load(config['stage_manifest']['path'])
    source_work = Path(source_manifest['work'])
    contract = state.load(source_manifest['contract']['path'])
    require(Path(config['baseline_speech_report']['path']) == canonical / 'speech_review.json'
        and Path(config['database']['path']) == speech_review.database_path(canonical), 'Baseline review/report route changed')
    required_reviews = {path.name for path in canonical.iterdir() if path.is_file() and review_name(path.name)}
    require(set(config['review_files']) == required_reviews and {'meta.json', 'translation_audit.md'} <= required_reviews,
        'Historical editorial review files may not be omitted')
    for name, bound in config['review_files'].items():
        require(Path(bound['path']) == canonical / name and state.matches(bound), 'Baseline review input changed')
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'kind': KIND, 'complete': False, 'config': state.identity(config_path), 'adapter': state.identity(__file__),
        'created_at': state.now(), 'scope': 'pre-render-current-audio-quality', 'canonical_project_or_database_modified': False,
        'rendered_or_installed': False, 'human_listened': False, 'pronunciation_or_emotional_approval': False}
    save_json(output / 'manifest.json', manifest)
    try:
        work = output / canonical.name
        work.mkdir()
        copies = [name for name in (*editorial_stage.INPUTS, *editorial_stage.OPTIONAL) if (source_work / name).is_file()]
        for name in copies:
            shutil.copy2(source_work / name, work / name)
        for name, bound in config['review_files'].items():
            shutil.copy2(bound['path'], work / name)
        for language in caption_projection.LANGUAGES:
            shutil.copy2(projection['outputs'][f'{language}_display.srt']['path'], work / f'{language}_display.srt')
        (work / 'download').symlink_to(canonical / 'download', target_is_directory=True)
        package = state.load(contract['baseline_evidence']['package.json']['archive']['path'])
        require(state.matches(package['plan']), 'Old intro generation plan changed')
        plan = copy.deepcopy(state.load(package['plan']['path']))
        plan['source_sha256'] = state.hashes(work, video_package.SOURCE_FILES)
        video_package.validate_plan(work, plan)
        save_json(work / 'package-plan.json', plan)
        record = {'kind': KIND, 'work': str(work), 'adapter': state.identity(__file__),
            'caption_adapter': state.identity(caption_projection.__file__), 'config': state.identity(config_path),
            'stage_manifest': config['stage_manifest'], 'stage_contract': source_manifest['contract'],
            'projection_manifest': config['projection_manifest'], 'projection_config': config['projection_config'],
            'source_copies': copies, 'files': {path.name: state.identity(path) for path in work.iterdir() if path.is_file()}}
        save_json(work / 'editorial-derivation.json', record)
        save_json(work / 'narration.json', expected_narration(work, record, contract, state.now()))
        database = output / 'speech-review.sqlite3'
        with closing(sqlite3.connect(Path(config['database']['path']).resolve().as_uri() + '?mode=ro', uri=True)) as source:
            before = database_snapshot(source)
            with closing(sqlite3.connect(database)) as destination:
                source.backup(destination)
                require(database_snapshot(destination) == before, 'Consistent review database backup differs')
        save_json(output / 'database-before.json', before)
        with narration_context(work):
            report = speech_review.scan(work, database, with_audio=True)
            source_findings = findings(work, report, validate_narration(work))
            save_json(output / 'source-classification.json', {'adapter': state.identity(__file__), 'project': str(work),
                'revision': report['revision'], 'findings': source_findings, 'database_decisions_written': 0,
                'full_human_listening': False, 'original_pause_role_or_intelligibility_approved': False})
            quality = delivery_quality.evaluate(work, database)
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            after = database_snapshot(connection)
        history = verify_history(before, after, report['project_id'], report['revision'])
        save_json(output / 'database-after.json', after)
        save_json(output / 'database-history.json', history)
        previous = state.load(config['baseline_speech_report']['path'])
        selected = {case['unit_id'] for case in contract['cases']}
        outside = [issue for issue in previous['issues'] if issue['unit_id'] not in selected]
        current = {issue['issue_id']: issue for issue in report['issues']}
        require(all(current.get(issue['issue_id']) == issue for issue in outside), 'Unedited-unit current candidates disappeared or changed')
        save_json(output / 'candidate-history.json', {'previous_current_candidates': len(previous['issues']),
            'current_candidates': len(report['issues']), 'unedited_current_candidates_preserved': len(outside),
            'edited_previous_candidates_retained_in_old_database_scans': len(previous['issues']) - len(outside),
            'classified_source_only_synthetic_tail_candidates': len(source_findings), 'new_review_decisions': 0})
        save_json(output / 'quality-evaluation.json', quality)
        require(state.matches(config['database']) and all(state.matches(bound) for bound in config['review_files'].values()),
            'Canonical database or review inputs changed during preparation')
        require(quality['allowed'], 'Current audio quality still has non-exempt blockers; preserve and inspect this attempt')
        names = ['database-before.json', 'database-after.json', 'database-history.json', 'candidate-history.json',
            'quality-evaluation.json', 'source-classification.json', 'speech-review.sqlite3']
        manifest.update({'complete': True, 'work': str(work), 'derivation': state.identity(work / 'editorial-derivation.json'),
            'files': {name: state.identity(output / name) for name in names},
            'speech_files': {name: state.identity(work / name) for name in
                ('narration.json', 'speech_review.json', 'speech_review.html', 'speech_database.json')}})
        save_json(output / 'manifest.json', manifest)
        return check(config_path, output)
    except BaseException as error:
        manifest.update({'complete': False, 'error': f'{type(error).__name__}: {error}'})
        save_json(output / 'manifest.json', manifest)
        raise


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = state.load(config_path)
    manifest = state.load(output / 'manifest.json')
    work = output / Path(state.load(config['stage_config']['path'])['work']).name
    require(manifest['kind'] == config['kind'] == KIND and manifest['complete'] is True
        and manifest['config'] == state.identity(config_path) and manifest['adapter'] == state.identity(__file__)
        and manifest['work'] == str(work) and manifest['derivation'] == state.identity(work / 'editorial-derivation.json'),
        'Stale/incomplete derived-quality authority')
    require(manifest['scope'] == 'pre-render-current-audio-quality' and all(manifest[name] is False for name in
        ('canonical_project_or_database_modified', 'rendered_or_installed', 'human_listened', 'pronunciation_or_emotional_approval'))
        and not any((work / name).exists() for name in ('render_state.json', 'upload_export.json', 'release.json')),
        'Pre-render audio quality cannot impersonate a rendered or installed package')
    for bound in (*manifest['files'].values(), *manifest['speech_files'].values()):
        require(state.matches(bound), 'Derived quality/database artifact changed')
    database = output / 'speech-review.sqlite3'
    before, after = state.load(output / 'database-before.json'), state.load(output / 'database-after.json')
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        require(database_snapshot(connection) == after, 'Current private database differs from preserved row history')
    report = state.load(work / 'speech_review.json')
    history = verify_history(before, after, report['project_id'], report['revision'])
    require(history == state.load(output / 'database-history.json'), 'Database preservation proof changed')
    with narration_context(work):
        quality = delivery_quality.evaluate(work, database)
    saved = state.load(output / 'quality-evaluation.json')
    require(quality['allowed'] is True and {key: value for key, value in quality.items() if key != 'checked_at'} ==
        {key: value for key, value in saved.items() if key != 'checked_at'}, 'Current audio quality binding or candidates changed')
    try:
        FROZEN_NARRATION(work)
    except ValueError:
        pass
    else:
        raise ValueError('Bare frozen validation unexpectedly accepted the explicit derived narration')
    return {'passed': True, 'manifest': state.identity(output / 'manifest.json'), 'quality_state': quality['state'],
        'current_candidates': len(report['issues']), 'candidate_history': state.load(output / 'candidate-history.json'),
        'database_history': history, 'current_audio_scan_proven': True, 'render_bindings_not_yet_proven': True,
        'canonical_project_or_database_modified': False, 'human_listened': False,
        'pronunciation_or_emotional_approval': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.config, args.output) if args.action == 'prepare' else check(args.config, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
