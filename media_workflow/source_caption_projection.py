"""Project reviewed speech phrases and explicit full-track English source corrections."""

import argparse
import copy
import json
from contextlib import contextmanager
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import dubbing_state as state
from prepare_videos import atomic_write, save_json
from . import caption_projection, editorial_sample_grid, editorial_stage, source_revision
from .speech_variants import require


KIND = 'source-revised-word-boundary-projection-v1'
ROOT = Path(__file__).resolve().parents[1]
FROZEN_STAGE_CHECK = editorial_stage.check
FROZEN_CAPTION_CHECK = caption_projection.check
CLAIMS = ('new_audio_or_media', 'canonical_project_modified', 'human_wordlevel_audiovisual_alignment_proven')


def implementations():
    return {'caption_projection': state.identity(caption_projection.__file__),
        'source_revision': state.identity(source_revision.__file__),
        'editorial_stage': state.identity(editorial_stage.__file__),
        'state': state.identity(state.__file__)}


def configuration(path):
    config = state.load(path)
    require(set(config) == {'kind', 'stage_config', 'stage_manifest', 'baseline_package',
        'baseline_subtitles', 'intro_offset_seconds', 'edits'}, 'Unexpected source-projection fields')
    require(config['kind'] == KIND and set(config['baseline_subtitles']) == set(caption_projection.LANGUAGES),
        'Unsupported source-aware caption projection')
    require(isinstance(config['intro_offset_seconds'], str), 'Use the explicit decimal intro offset')
    offset = Decimal(config['intro_offset_seconds'])
    require(offset.is_finite() and offset >= 0 and offset == offset.quantize(Decimal('.001')),
        'Bind the real millisecond intro offset')
    return config


def load_stage(config):
    for name in ('stage_config', 'stage_manifest', 'baseline_package'):
        require(state.matches(config[name]), f'Stale source-projection input: {name}')
    stage = Path(config['stage_manifest']['path']).parent
    require(Path(config['stage_manifest']['path']) == stage / 'manifest.json', 'Wrong source-stage manifest route')
    source_revision.check(config['stage_config']['path'], stage)
    manifest = state.load(stage / 'manifest.json')
    require(manifest['config'] == config['stage_config'], 'Source stage belongs to another configuration')
    contract = state.load(manifest['contract']['path'])
    require(config['baseline_package'] == contract['baseline_evidence']['package.json']['archive'],
        'Projection package differs from exact source-stage baseline')
    package = state.load(config['baseline_package']['path'])
    require(config['baseline_subtitles'] == package['subtitles']
        and all(state.matches(bound) for bound in config['baseline_subtitles'].values())
        and Decimal(config['intro_offset_seconds']) == Decimal(str(package['body_offset_seconds'])),
        'Full subtitle baseline or real intro offset changed')
    require(all(Fraction(Decimal(case['source_start'])) * editorial_stage.RATE == case['start_sample']
        for case in contract['cases']), 'Caption projection requires exact source-start sample alignment')
    proposal = state.load(contract['source_revision']['proposal']['path'])
    return stage, contract, proposal['amendments']


def english_entries(entries, amendments, offset):
    require(isinstance(offset, Decimal) and offset.is_finite() and offset >= 0,
        'Use a finite explicit English-track offset')
    require(isinstance(amendments, list) and amendments, 'English projection requires explicit source corrections')
    result, changes, numbers, owners = list(entries), [], set(), set()
    for amendment in amendments:
        number = amendment['unit_id']
        require(type(number) is int and number > 0 and number not in numbers, 'Duplicate or invalid source amendment')
        numbers.add(number)
        start, end = [Decimal(amendment[name]) + offset for name in ('source_start', 'source_end')]
        require(start.is_finite() and end.is_finite() and 0 <= start < end, 'Invalid English correction window')
        affected = [(index, cue) for index, cue in enumerate(entries) if cue[0] < end and cue[1] > start]
        require(affected and affected[0][1][0] == start and affected[-1][1][1] == end
            and all(start <= cue[0] < cue[1] <= end for _, cue in affected),
            'Full English cues cross or omit the complete revised source window')
        require(' '.join(cue[2] for _, cue in affected).split() == amendment['before'].split(),
            'Full English unit differs from the exact pre-revision source word stream')
        replacement = amendment['replacement']
        require(isinstance(replacement['from'], str) and replacement['from'].strip()
            and isinstance(replacement['to'], str) and replacement['to'].strip()
            and type(replacement['occurrences']) is int and replacement['occurrences'] == 1
            and not any(character in replacement[name] for name in ('from', 'to') for character in '\t\r\n'),
            'Require one exact nonempty English correction span')
        selected = [(index, cue) for index, cue in affected if replacement['from'] in cue[2]]
        require(len(selected) == 1 and selected[0][1][2].count(replacement['from']) == 1,
            'English replacement is ambiguous or crosses an existing cue; review a separate contract')
        index, cue = selected[0]
        require(index not in owners, 'Multiple source amendments claim the same full English cue')
        owners.add(index)
        text = cue[2].replace(replacement['from'], replacement['to'])
        require(text != cue[2], 'Empty English correction')
        result[index] = (*cue[:2], text)
        require(' '.join(result[position][2] for position, _ in affected).split() == amendment['after'].split(),
            'English correction does not reproduce the complete amended source')
        changes.append({'source_unit_id': number, 'full_track_cue_id': index + 1,
            'full_track_start': str(cue[0]), 'full_track_end': str(cue[1]),
            'before': cue[2], 'after': text, 'replacement': replacement,
            'complete_source_envelope_cues': len(affected), 'source_word_stream_after_matches': True})
    return result, changes


def expected(config, contract, amendments, baselines):
    legacy_config = copy.deepcopy(config)
    legacy_config['kind'] = caption_projection.KIND
    results, bodies, changes = caption_projection.expected(legacy_config, contract, baselines)
    offset = Decimal(config['intro_offset_seconds'])
    entries, english_changes = english_entries(results['en'], amendments, offset)
    raw = Path(baselines['en']).read_bytes().decode('utf-8')
    edits = [{'unit_id': item['full_track_cue_id'], 'before': item['before'], 'after': item['after'],
        'replacement': item['replacement']} for item in english_changes]
    english_text = source_revision.revise_srt(raw, edits, len(entries))
    results['en'] = entries
    bodies['en'] = [(start - offset, end - offset, text) for start, end, text in entries if start >= offset]
    require(bodies['en'] and not any(start < offset < end for start, end, _ in entries),
        'English intro/body boundary changed')
    return results, bodies, changes, english_changes, english_text


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    stage, contract, amendments = load_stage(config)
    canonical = Path(state.load(config['stage_config']['path'])['work'])
    require(not output.exists() and ROOT not in (output, *output.parents)
        and all(not output.is_relative_to(parent) and not parent.is_relative_to(output)
            for parent in (stage, canonical)), 'Use a fresh external caption projection, not an authoritative project')
    output.mkdir(parents=True)
    manifest = {'kind': KIND, 'complete': False, 'config': state.identity(config_path),
        'adapter': state.identity(__file__), 'implementations': implementations(),
        'stage_manifest': config['stage_manifest'], 'created_at': state.now(),
        **{name: False for name in CLAIMS}, 'english_track_bytes_unchanged': False,
        'source_corrections_explicitly_applied': True}
    save_json(output / 'manifest.json', manifest)
    try:
        archived = {language: editorial_stage.archive(bound, output / 'before' / f'{language}.srt')
            for language, bound in config['baseline_subtitles'].items()}
        results, bodies, changes, english_changes, english_text = expected(config, contract, amendments,
            {language: item['archive']['path'] for language, item in archived.items()})
        for language in caption_projection.LANGUAGES:
            atomic_write(output / f'{language}.srt', english_text if language == 'en'
                else caption_projection.srt_text(results[language]))
            atomic_write(output / f'{language}_display.srt', caption_projection.srt_text(bodies[language]))
        manifest.update(complete=True, before=archived, changes=changes, english_changes=english_changes,
            outputs={path.name: state.identity(path) for path in output.glob('*.srt')})
        save_json(output / 'manifest.json', manifest)
        return check(config_path, output)
    except BaseException as error:
        manifest.update(complete=False, error=f'{type(error).__name__}: {error}')
        save_json(output / 'manifest.json', manifest)
        raise


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = configuration(config_path)
    _, contract, amendments = load_stage(config)
    manifest = state.load(output / 'manifest.json')
    require(manifest['kind'] == KIND and manifest['complete'] is True
        and manifest['config'] == state.identity(config_path) and manifest['adapter'] == state.identity(__file__)
        and manifest['implementations'] == implementations() and manifest['stage_manifest'] == config['stage_manifest'],
        'Stale or incomplete source-aware caption projection')
    require(all(manifest[name] is False for name in CLAIMS) and manifest['english_track_bytes_unchanged'] is False
        and manifest['source_corrections_explicitly_applied'] is True,
        'Source projection cannot hide English edits or expand listening authority')
    require(set(manifest['before']) == set(caption_projection.LANGUAGES), 'Incomplete original subtitle inventory')
    for language, item in manifest['before'].items():
        require(item['original'] == config['baseline_subtitles'][language]
            and Path(item['archive']['path']) == output / 'before' / f'{language}.srt'
            and state.matches(item['archive']) and all(item['archive'][key] == item['original'][key]
                for key in ('bytes', 'mtime_ns', 'sha256')), 'Exact baseline subtitle archive changed')
    results, bodies, changes, english_changes, english_text = expected(config, contract, amendments,
        {language: item['archive']['path'] for language, item in manifest['before'].items()})
    require(manifest['changes'] == changes and manifest['english_changes'] == english_changes,
        'Whole-utterance or English correction inventory changed')
    names = {name for language in caption_projection.LANGUAGES
        for name in (f'{language}.srt', f'{language}_display.srt')}
    require(set(manifest['outputs']) == names and {path.name for path in output.glob('*.srt')} == names,
        'Incomplete or expanded projected subtitle inventory')
    for name, bound in manifest['outputs'].items():
        require(Path(bound['path']) == output / name and state.matches(bound), 'Projected subtitle identity changed')
    for language in caption_projection.LANGUAGES:
        require(caption_projection.read_srt(output / f'{language}.srt') == results[language]
            and caption_projection.read_srt(output / f'{language}_display.srt') == bodies[language],
            'Projected source words, Chinese phrases or timestamps changed')
    require((output / 'en.srt').read_bytes() == english_text.encode('utf-8'),
        'English bytes changed outside explicitly reviewed cue replacements')
    return {'passed': True, 'kind': KIND, 'manifest': state.identity(output / 'manifest.json'),
        'complete_audio_cases': len(changes), 'Chinese_phrases': sum(item['counts']['zh']['after'] for item in changes),
        'English_cues': len(results['en']), 'English_corrected_cue_ids': [item['full_track_cue_id'] for item in english_changes],
        'English_source_units': [item['source_unit_id'] for item in english_changes],
        'unselected_English_cues_and_bytes_preserved': True, 'english_track_bytes_unchanged': False,
        **{name: False for name in CLAIMS}}


@contextmanager
def runtime(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    check(config_path, output)
    config = configuration(config_path)
    stage_config = Path(config['stage_config']['path']).resolve()
    stage = Path(config['stage_manifest']['path']).parent.resolve()
    require(FROZEN_STAGE_CHECK is editorial_sample_grid.FROZEN_CHECK
        and editorial_stage.check is FROZEN_STAGE_CHECK and caption_projection.check is FROZEN_CAPTION_CHECK,
        'Reject nested or foreign source-projection dispatchers')

    def stage_dispatch(selected_config, selected_output):
        if Path(selected_config).resolve() == stage_config and Path(selected_output).resolve() == stage:
            return source_revision.check(stage_config, stage)
        return FROZEN_STAGE_CHECK(selected_config, selected_output)

    def caption_dispatch(selected_config, selected_output):
        if Path(selected_config).resolve() == config_path and Path(selected_output).resolve() == output:
            return check(config_path, output)
        return FROZEN_CAPTION_CHECK(selected_config, selected_output)

    editorial_stage.check, caption_projection.check = stage_dispatch, caption_dispatch
    try:
        yield
    finally:
        editorial_stage.check, caption_projection.check = FROZEN_STAGE_CHECK, FROZEN_CAPTION_CHECK


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check', 'quality'])
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--operation', choices=['prepare', 'check'], default='check')
    parser.add_argument('--projection-config', type=Path)
    parser.add_argument('--projection', type=Path)
    arguments = parser.parse_args()
    if arguments.action == 'quality':
        from . import editorial_quality
        require(arguments.projection_config is not None and arguments.projection is not None,
            'Quality requires the explicit verified source-aware projection')
        projection_config = configuration(arguments.projection_config)
        quality_config = state.load(arguments.config)
        require(quality_config['projection_config'] == state.identity(arguments.projection_config)
            and quality_config['projection_manifest'] == state.identity(arguments.projection / 'manifest.json')
            and quality_config['stage_config'] == projection_config['stage_config']
            and quality_config['stage_manifest'] == projection_config['stage_manifest'],
            'Quality scope differs from the selected source-aware projection')
        with runtime(arguments.projection_config, arguments.projection):
            result = (editorial_quality.prepare if arguments.operation == 'prepare' else editorial_quality.check)(
                arguments.config, arguments.output)
    else:
        result = (prepare if arguments.action == 'prepare' else check)(arguments.config, arguments.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
