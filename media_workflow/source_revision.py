"""Stage explicitly reviewed source replacements without modifying canonical projects."""

import argparse
import copy
import fcntl
import json
import re
import shutil
from pathlib import Path

import dubbing_state as state
import video_workflow
from prepare_videos import atomic_write, save_json
from . import caption_projection, editorial_sample_grid, editorial_stage
from .speech_variants import require


KIND = 'preserved-window-source-revision-stage-v1'
PROPOSAL = 'explicit-source-amendment-proposal-only-v1'
ROOT = Path(__file__).resolve().parents[1]
CHANGED = {'en.srt', 'translation_task.json', 'compilation.json', 'bilingual.srt',
    'editorial-source.json', 'narration.json', 'normalization_changes.json'}


def implementations():
    return {**editorial_sample_grid.implementations(),
        'sample_grid': state.identity(editorial_sample_grid.__file__),
        'caption_projection': state.identity(caption_projection.__file__)}


def configuration(path):
    config = state.load(path)
    require(set(config) == {'kind', 'work', 'parent_config', 'parent_manifest', 'proposal',
        'translation_input_before'}, 'Unexpected source-revision configuration fields')
    require(config['kind'] == KIND, 'Unsupported source-revision stage')
    work = Path(config['work'])
    require(work.is_absolute() and work.resolve() == work, 'Use the canonical absolute project path')
    require(Path(config['translation_input_before']['path']) == work / 'translation_input.tsv',
        'Bind the actual previous source-task export')
    return config


def validate_proposal(proposal, contract, plan, proposal_identity):
    require(proposal['kind'] == PROPOSAL and proposal['formal_adoption'] is False
        and proposal['work'] == plan['work'], 'Wrong source proposal or expanded adoption authority')
    require(proposal['source_before'] == contract['baseline_files']['en.srt']['original'],
        'Proposal does not bind the exact archived original English')
    require(proposal_identity in plan['reference_evidence']
        and proposal['author_review'] in plan['reference_evidence'],
        'The complete previews did not bind this source proposal and review')
    count = proposal['unit_count_before']
    require(type(count) is int and count > 0 and type(proposal['unit_count_after_proposed']) is int
        and proposal['unit_count_after_proposed'] == count
        and proposal['unit_id_mapping'] == [[number, number] for number in range(1, count + 1)]
        and all(type(value) is int for pair in proposal['unit_id_mapping'] for value in pair),
        'Source revision must preserve the complete unit inventory and identity mapping')
    cases = {case['unit_id']: case for case in contract['cases']}
    require(len(cases) == len(contract['cases']), 'Duplicate selected source units')
    required = set()
    for case in plan['cases']:
        flag = case.get('requires_explicit_source_amendment_before_adoption', False)
        require(type(flag) is bool, 'Source amendment disclosure must be boolean')
        if flag:
            required.add(case['unit_id'])
    amendments = proposal['amendments']
    require(isinstance(amendments, list) and 0 < len(amendments) <= 12,
        'Expected one to twelve explicit source amendments')
    numbers = set()
    for amendment in amendments:
        require(set(amendment) == {'unit_id', 'source_start', 'source_end', 'before', 'after',
            'replacement', 'basis', 'word_conserving', 'same_unit_identity_and_window', 'human_listened'},
            'Unknown or incomplete source-amendment claims')
        number = amendment['unit_id']
        require(type(number) is int and 1 <= number <= count and number not in numbers
            and number in cases, 'Missing, duplicate, or unselected source amendment')
        numbers.add(number)
        case = cases[number]
        require((amendment['source_start'], amendment['source_end'], amendment['before']) ==
            (case['source_start'], case['source_end'], case['source']),
            'Source amendment changed its original text or window')
        replacement = amendment['replacement']
        require(set(replacement) == {'from', 'to', 'occurrences'}
            and all(isinstance(replacement[name], str) and replacement[name].strip()
                and not any(character in replacement[name] for character in '\t\r\n')
                for name in ('from', 'to')) and type(replacement['occurrences']) is int
            and replacement['occurrences'] == 1, 'Require one exact, nonempty source replacement')
        require(amendment['before'].count(replacement['from']) == 1
            and amendment['after'] == amendment['before'].replace(replacement['from'], replacement['to'])
            and amendment['after'] != amendment['before'], 'Unenumerated, ambiguous or empty source correction')
        require(amendment['same_unit_identity_and_window'] is True
            and amendment['word_conserving'] is False and amendment['human_listened'] is False
            and isinstance(amendment['basis'], str) and amendment['basis'].strip(),
            'Source correction must retain evidence limits, windows and explicit nonconservation')
    require(numbers == required, 'Every disclosed source amendment must be selected, with no extras')
    return amendments


def revise_srt(text, amendments, count):
    require('\r' not in text and '\ufeff' not in text, 'Expected explicit LF UTF-8 source SRT')
    edits = {item['unit_id']: item for item in amendments}
    require(len(edits) == len(amendments), 'Duplicate SRT amendments')
    parts = re.split(r'(\n[ \t]*\n)', text)
    seen = []
    for index in range(0, len(parts), 2):
        block = parts[index]
        if not block.strip():
            continue
        lines = block.split('\n', 2)
        require(len(lines) == 3 and lines[0] == str(len(seen) + 1)
            and ' --> ' in lines[1], 'Expected complete consecutive source SRT blocks')
        number = len(seen) + 1
        seen.append(number)
        if number in edits:
            amendment = edits[number]
            replacement = amendment['replacement']
            require(lines[2].strip() == amendment['before']
                and lines[2].count(replacement['from']) == 1, 'SRT original does not match reviewed replacement')
            payload = lines[2].replace(replacement['from'], replacement['to'])
            require(payload.strip() == amendment['after'], 'SRT replacement changed extra source text')
            parts[index] = '\n'.join([lines[0], lines[1], payload])
    require(len(seen) == count and set(edits) <= set(seen), 'SRT source inventory differs from proposal')
    return ''.join(parts)


def revised_cases(cases, amendments):
    edits = {item['unit_id']: item for item in amendments}
    result = copy.deepcopy(cases)
    for case in result:
        if case['unit_id'] in edits:
            case['original_source_text'] = case['source']
            case['source_amendment'] = edits[case['unit_id']]
            case['source'] = edits[case['unit_id']]['after']
    return result


def source_input(path):
    return ''.join(f'{number}\t{caption_projection.timestamp(start)} --> '
        f'{caption_projection.timestamp(end)}\t{text.replace(chr(10), " ")}\n'
        for number, (start, end, text) in enumerate(caption_projection.read_srt(path), 1))


def load_parent(config):
    for name in ('parent_config', 'parent_manifest', 'proposal'):
        require(state.matches(config[name]), f'Stale source-revision input: {name}')
    parent = Path(config['parent_manifest']['path']).parent
    require(Path(config['parent_manifest']['path']) == parent / 'manifest.json', 'Wrong parent manifest route')
    parent_config = editorial_sample_grid.configuration(config['parent_config']['path'])
    require(parent_config['work'] == config['work'], 'Parent stage belongs to another project')
    editorial_sample_grid.check(config['parent_config']['path'], parent)
    manifest = state.load(parent / 'manifest.json')
    require(manifest['config'] == config['parent_config'], 'Parent configuration differs')
    contract = state.load(manifest['contract']['path'])
    plan = state.load(contract['preview_plan']['path'])
    proposal = state.load(config['proposal']['path'])
    require(all(state.matches(bound) for bound in plan['reference_evidence'])
        and all(state.matches(bound) for bound in [proposal['author_review'], *proposal['evidence']]),
        'Source-review evidence changed')
    amendments = validate_proposal(proposal, contract, plan, config['proposal'])
    return parent, manifest, contract, proposal, amendments


def revision_record(config, snapshots, exported):
    return {'kind': KIND, 'proposal': config['proposal'], 'parent_config': config['parent_config'],
        'parent_manifest': config['parent_manifest'], 'snapshots': snapshots, 'source_input': exported,
        'post_revision_word_conservation_claimed': False, 'formal_adoption': False}


def expected_task(parent_task, source_digest, revision):
    task = copy.deepcopy(parent_task)
    require(type(task['units']) is int and task['units'] > 0, 'Unsupported source-task unit inventory')
    task.update(source_sha256=source_digest, source=revision['source_input']['path'], source_revision=revision)
    return task


def validate_normalization(work):
    require(state.load(work / 'normalization_changes.json') == {
        'contract': state.compilation_contract(work), 'changes': [], 'production_audio_modified': False},
        'Source-only compilation must preserve spoken text and rebind exact normalization bookkeeping')


def expected_contract(parent, manifest, revision, amendments):
    contract = copy.deepcopy(parent)
    contract.update(kind=KIND, adapter=manifest['adapter'], implementations=manifest['implementations'],
        cases=revised_cases(parent['cases'], amendments), source_revision=revision)
    return contract


def narration(work, contract, observed_at):
    dependencies = state.narration_dependencies(work)
    dependencies['editorial_stage_contract'] = state.identity(work / 'editorial-source.json')
    return {'origin': KIND, 'observed_at': observed_at, 'dependencies': dependencies,
        'waveform': contract['composite_waveform'],
        'statistics': state.hashes(work, ['tts_stats.json', 'timing_review.json']),
        'producer': {'kind': KIND, 'contract': dependencies['editorial_stage_contract']},
        'historical_generation_proven': False, 'human_listened': False, 'pronunciation_or_emotion_approved': False}


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    canonical = Path(config['work'])
    require(not output.exists() and ROOT not in (output, *output.parents)
        and not output.is_relative_to(canonical) and not canonical.is_relative_to(output),
        'Use a fresh external source stage, separate from the repository and canonical project')
    with (canonical / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parent, parent_manifest, parent_contract, proposal, amendments = load_parent(config)
        require(not output.is_relative_to(parent) and not parent.is_relative_to(output),
            'Source stage must not overlap its immutable parent')
        require(state.matches(config['translation_input_before'])
            and state.matches(proposal['source_before']), 'Live source or old task export changed before staging')
        output.mkdir(parents=True)
        manifest = {'kind': KIND, 'complete': False, 'config': state.identity(config_path),
            'adapter': state.identity(__file__), 'implementations': implementations(), 'created_at': state.now(),
            'scope': 'source-and-compiled-text-stage-only', 'canonical_source_or_media_modified': False,
            'formal_adoption': False, 'downstream_source_projection_supported': False,
            'human_listened': False, 'pronunciation_or_emotion_approved': False}
        save_json(output / 'manifest.json', manifest)
        try:
            snapshots = {name: editorial_stage.archive(bound, output / 'before' / name) for name, bound in {
                'raw-caption.json3': proposal['raw_caption_json3_untouched'],
                'source-provenance.json': proposal['source_provenance_before'],
                'translation-input.tsv': config['translation_input_before']}.items()}
            work = output / canonical.name
            work.mkdir()
            parent_work = Path(parent_manifest['work'])
            for name, bound in parent_manifest['files'].items():
                shutil.copy2(bound['path'], work / name)
            (work / 'download').symlink_to(parent_work / 'download', target_is_directory=True)
            original = (parent_work / 'en.srt').read_text()
            atomic_write(work / 'en.srt', revise_srt(original, amendments, proposal['unit_count_before']))
            atomic_write(output / 'source-input.tsv', source_input(work / 'en.srt'))
            revision = revision_record(config, snapshots, state.identity(output / 'source-input.tsv'))
            save_json(work / 'translation_task.json', expected_task(state.load(parent_work / 'translation_task.json'),
                state.digest(work / 'en.srt'), revision))
            video_workflow.compile_translation(work)
            contract = expected_contract(parent_contract, manifest, revision, amendments)
            save_json(work / 'editorial-source.json', contract)
            save_json(work / 'narration.json', narration(work, contract, state.now()))
            manifest.update(complete=True, work=str(work), contract=state.identity(work / 'editorial-source.json'),
                files={path.name: state.identity(path) for path in work.iterdir() if path.is_file()},
                source_revision=revision, audit=parent_contract['audit'])
            save_json(output / 'manifest.json', manifest)
            return check(config_path, output)
        except BaseException as error:
            manifest.update(complete=False, error=f'{type(error).__name__}: {error}')
            save_json(output / 'manifest.json', manifest)
            raise


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    _, parent_manifest, parent_contract, proposal, amendments = load_parent(config)
    manifest = state.load(output / 'manifest.json')
    require(manifest['kind'] == KIND and manifest['complete'] is True and manifest['scope'] == 'source-and-compiled-text-stage-only'
        and manifest['config'] == state.identity(config_path) and manifest['adapter'] == state.identity(__file__)
        and manifest['implementations'] == implementations(), 'Stale or incomplete source-revision stage')
    require(all(manifest[name] is False for name in ('canonical_source_or_media_modified', 'formal_adoption',
        'downstream_source_projection_supported', 'human_listened', 'pronunciation_or_emotion_approved')),
        'Source staging cannot impersonate downstream support or hearing approval')
    work, parent_work = output / Path(config['work']).name, Path(parent_manifest['work'])
    require(manifest['work'] == str(work) and (work / 'download').resolve() == (parent_work / 'download').resolve()
        and not any((work / name).exists() for name in editorial_stage.FORBIDDEN), 'Source stage layout changed')
    require(set(manifest['files']) == set(parent_manifest['files'])
        == {path.name for path in work.iterdir() if path.is_file()}
        and all(Path(bound['path']) == work / name and state.matches(bound)
            for name, bound in manifest['files'].items()), 'Source-stage file inventory changed')
    revision = manifest['source_revision']
    expected_snapshots = {'raw-caption.json3': proposal['raw_caption_json3_untouched'],
        'source-provenance.json': proposal['source_provenance_before'],
        'translation-input.tsv': config['translation_input_before']}
    require(set(revision['snapshots']) == set(expected_snapshots), 'Incomplete original source snapshots')
    for name, bound in expected_snapshots.items():
        snapshot = revision['snapshots'][name]
        require(snapshot['original'] == bound and Path(snapshot['archive']['path']) == output / 'before' / name
            and state.matches(snapshot['archive']) and all(snapshot['archive'][key] == bound[key]
                for key in ('bytes', 'mtime_ns', 'sha256')), 'Exact original source snapshot changed')
    require(revision['source_input'] == state.identity(output / 'source-input.tsv')
        and revision == revision_record(config, revision['snapshots'], revision['source_input']), 'Revision provenance changed')
    expected = revise_srt((parent_work / 'en.srt').read_text(), amendments, proposal['unit_count_before'])
    require((work / 'en.srt').read_text() == expected
        and (output / 'source-input.tsv').read_text() == source_input(work / 'en.srt'), 'Source edits or task export changed')
    before, after = state.cues(parent_work / 'en.srt'), state.cues(work / 'en.srt')
    changed = {item['unit_id']: item for item in amendments}
    require(len(before) == len(after) == proposal['unit_count_before']
        and all(previous[:2] == current[:2] and current[2] == changed.get(number, {}).get('after', previous[2])
            for number, (previous, current) in enumerate(zip(before, after), 1)), 'Source times or unselected text changed')
    task = expected_task(state.load(parent_work / 'translation_task.json'), state.digest(work / 'en.srt'), revision)
    require(task['units'] == len(after) and state.load(work / 'translation_task.json') == task, 'Revised source task changed')
    for name in manifest['files'].keys() - CHANGED:
        require((work / name).read_bytes() == (parent_work / name).read_bytes(), f'Unselected staged input changed: {name}')
    state.validate_compilation(work)
    validate_normalization(work)
    expected_compilation = state.expected_compilation(work)
    require(all(state.cues(work / name) == expected_compilation[name] for name in state.COMPILE_OUTPUTS),
        'Compiled Chinese/spoken/bilingual text differs from revised source')
    contract = state.load(work / 'editorial-source.json')
    require(manifest['contract'] == state.identity(work / 'editorial-source.json')
        and contract == expected_contract(parent_contract, manifest, revision, amendments)
        and manifest['audit'] == parent_contract['audit'], 'Source revision changed audio ownership or parent provenance')
    observed = state.load(work / 'narration.json')
    require(isinstance(observed['observed_at'], str) and observed['observed_at']
        and observed == narration(work, contract, observed['observed_at']), 'Source-aware narration binding changed')
    return {'passed': True, 'kind': KIND, 'source_amendments': sorted(changed),
        'complete_audio_cases': [case['unit_id'] for case in contract['cases']], 'units': len(after),
        'source_windows_and_unselected_text_preserved': True, 'parent_complete_audio_unchanged': True,
        'manifest': state.identity(output / 'manifest.json'), 'formal_adoption': False,
        'downstream_source_projection_supported': False, 'human_listened': False, 'pronunciation_or_emotion_approved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check'])
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    result = (prepare if arguments.action == 'prepare' else check)(arguments.config, arguments.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
