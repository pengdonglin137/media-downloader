"""Project explicit source components across minimal adjacent English subtitle cues."""

import argparse
import copy
import json
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import dubbing_state as state
from prepare_videos import atomic_write, save_json
from . import caption_projection, editorial_stage, source_caption_projection, source_revision
from .speech_variants import require


KIND = 'explicit-component-source-caption-projection-v1'
POLICY = 'minimal-adjacent-English-cue-merges-within-unchanged-source-windows-v1'
FROZEN_CHECK = source_caption_projection.check
ROOT = Path(__file__).resolve().parents[1]


def configuration(path):
    profile = state.load(path)
    require(set(profile) == {'kind', 'policy', 'projection_config', 'normalized_proposal', 'component_proposal'},
        'Unexpected component-projection profile fields')
    require(profile['kind'] == KIND and profile['policy'] == POLICY, 'Unsupported explicit component policy')
    for name in ('projection_config', 'normalized_proposal', 'component_proposal'):
        require(state.matches(profile[name]), 'Stale component projection input: ' + name)
    core = source_caption_projection.configuration(profile['projection_config']['path'])
    normalized = state.load(profile['normalized_proposal']['path'])
    original = state.load(profile['component_proposal']['path'])
    stage_manifest = state.load(core['stage_manifest']['path'])
    contract = state.load(stage_manifest['contract']['path'])
    require(contract['source_revision']['proposal'] == profile['normalized_proposal']
        and normalized['normalization_and_scope']['original_proposal'] == profile['component_proposal'],
        'Component proposal is not the staged normalized source authority')
    require(normalized['source_before'] == original['source_before']
        and normalized['unit_id_mapping'] == original['unit_id_mapping']
        and normalized['formal_adoption'] is original['formal_adoption'] is False,
        'Component/source identity or scope changed')
    originals = {entry['unit_id']: entry for entry in original['amendments']}
    require(len(originals) == len(original['amendments']), 'Duplicate component source units')
    amendments = []
    for entry in normalized['amendments']:
        require(entry['unit_id'] in originals, 'Normalized source amendment has no component owner')
        components = originals[entry['unit_id']]
        require(all(entry[name] == components[name] for name in ('before', 'after', 'source_start', 'source_end')),
            'Components changed the complete normalized source amendment')
        revised = entry['before']
        require(components['replacements'], 'Empty explicit component inventory')
        for replacement in components['replacements']:
            require(set(replacement) == {'from', 'to', 'occurrences'}
                and type(replacement['occurrences']) is int and replacement['occurrences'] == 1
                and all(isinstance(replacement[name], str) and replacement[name].strip()
                    and not any(character in replacement[name] for character in '\t\r\n') for name in ('from', 'to'))
                and replacement['from'] != replacement['to']
                and revised.count(replacement['from']) == 1, 'Ambiguous or invalid component replacement')
            revised = revised.replace(replacement['from'], replacement['to'])
        require(revised == entry['after'], 'Components do not reproduce the complete source amendment')
        amendments.append({**entry, 'components': components['replacements']})
    require(len({entry['unit_id'] for entry in amendments}) == len(amendments) > 0,
        'Missing or duplicate normalized source units')
    return profile, core, amendments


def project_english(entries, amendments, offset):
    require(isinstance(offset, Decimal) and offset.is_finite() and offset >= 0,
        'Require the actual finite decimal intro offset')
    require(isinstance(entries, list) and entries and isinstance(amendments, list) and amendments,
        'Require complete English cues and explicit component amendments')
    previous_end = Decimal('0')
    for cue in entries:
        require(isinstance(cue, (tuple, list)) and len(cue) == 3
            and all(isinstance(value, Decimal) and value.is_finite() for value in cue[:2])
            and previous_end <= cue[0] < cue[1] and isinstance(cue[2], str) and cue[2].strip(),
            'Invalid, empty, overlapping or unordered original English cue')
        previous_end = cue[1]
    groups = {}
    owned = set()
    units = set()
    windows = []
    for amendment in amendments:
        number = amendment['unit_id']
        require(type(number) is int and number > 0 and number not in units, 'Duplicate or invalid source owner')
        units.add(number)
        start, end = [Decimal(amendment[name]) + offset for name in ('source_start', 'source_end')]
        require(start.is_finite() and end.is_finite() and offset <= start < end
            and not any(start < previous_end and end > previous_start for previous_start, previous_end in windows),
            'Invalid or overlapping complete source windows')
        windows.append((start, end))
        affected = [(index, cue) for index, cue in enumerate(entries) if cue[0] < end and cue[1] > start]
        require(affected and affected[0][1][0] == start and affected[-1][1][1] == end
            and all(start <= cue[0] < cue[1] <= end for _, cue in affected)
            and all(previous[1][1] == current[1][0] for previous, current in zip(affected, affected[1:])),
            'English cues cross or omit the unchanged complete source window')
        tokens = []
        token_owners = []
        for index, cue in affected:
            require(cue[2].strip(), 'Empty original English cue')
            words = cue[2].split()
            tokens.extend(words)
            token_owners.extend([index] * len(words))
        require(tokens == amendment['before'].split(), 'English source word stream differs from reviewed before text')
        intervals = []
        component_words = set()
        require(isinstance(amendment['components'], list) and amendment['components'], 'Missing component replacements')
        for replacement in amendment['components']:
            require(set(replacement) == {'from', 'to', 'occurrences'}
                and type(replacement['occurrences']) is int and replacement['occurrences'] == 1
                and all(isinstance(replacement[name], str) and replacement[name].strip()
                    and not any(character in replacement[name] for character in '\t\r\n') for name in ('from', 'to'))
                and replacement['from'] != replacement['to'], 'Invalid explicit component')
            words = replacement['from'].split()
            matches = [index for index in range(len(tokens) - len(words) + 1)
                if tokens[index:index + len(words)] == words]
            require(len(matches) == 1, 'Component must uniquely own complete source words')
            first = matches[0]
            positions = set(range(first, first + len(words)))
            require(not positions.intersection(component_words), 'Explicit components overlap original source words')
            component_words.update(positions)
            intervals.append((token_owners[first], token_owners[first + len(words) - 1], replacement))
        merged = []
        for first, last, replacement in sorted(intervals, key=lambda entry: (entry[0], entry[1])):
            if merged and first <= merged[-1]['last']:
                merged[-1]['last'] = max(merged[-1]['last'], last)
                merged[-1]['components'].append(replacement)
            else:
                merged.append({'first': first, 'last': last, 'components': [replacement]})
        for group in merged:
            indices = set(range(group['first'], group['last'] + 1))
            require(not indices.intersection(owned), 'English component groups overlap another source owner')
            owned.update(indices)
            before = ' '.join(entries[index][2] for index in sorted(indices))
            after = before
            for replacement in group['components']:
                require(after.count(replacement['from']) == 1, 'Component spacing or overlapping replacements are ambiguous')
                after = after.replace(replacement['from'], replacement['to'])
            require(after.strip() and after != before, 'Empty or unchanged English component group')
            groups[group['first']] = {**group, 'source_unit_id': number, 'before': before, 'after': after}
    result, changes, routes = [], [], []
    index = 0
    while index < len(entries):
        if index not in groups:
            require(index not in owned, 'Missing English group boundary')
            result.append(entries[index])
            routes.append({'before_cue_ids': [index + 1], 'after_cue_id': len(result), 'changed': False})
            index += 1
            continue
        group = groups[index]
        last = group['last']
        cue = (entries[index][0], entries[last][1], group['after'])
        result.append(cue)
        before_ids = list(range(index + 1, last + 2))
        changes.append({'source_unit_id': group['source_unit_id'], 'full_track_cue_id': len(result),
            'original_cue_ids': before_ids, 'full_track_start': str(cue[0]), 'full_track_end': str(cue[1]),
            'before': group['before'], 'after': cue[2], 'components': group['components'],
            'same_group_outer_times': True, 'no_source_unit_or_speaker_crossed': True})
        routes.append({'before_cue_ids': before_ids, 'after_cue_id': len(result), 'changed': True})
        index = last + 1
    require([number for route in routes for number in route['before_cue_ids']] == list(range(1, len(entries) + 1)),
        'English cue mapping omits or duplicates old content')
    for amendment in amendments:
        start, end = [Decimal(amendment[name]) + offset for name in ('source_start', 'source_end')]
        current = [cue for cue in result if cue[0] < end and cue[1] > start]
        require(current[0][0] == start and current[-1][1] == end
            and ' '.join(cue[2] for cue in current).split() == amendment['after'].split(),
            'Projected English does not reproduce the complete revised source word stream')
    return result, changes, routes


def expected(core, contract, amendments, baselines):
    legacy = copy.deepcopy(core)
    legacy['kind'] = caption_projection.KIND
    results, bodies, changes = caption_projection.expected(legacy, contract, baselines)
    offset = Decimal(core['intro_offset_seconds'])
    english, english_changes, routes = project_english(results['en'], amendments, offset)
    results['en'] = english
    bodies['en'] = [(start - offset, end - offset, text) for start, end, text in english if start >= offset]
    require(bodies['en'] and not any(start < offset < end for start, end, _ in english),
        'English intro/body boundary changed')
    return results, bodies, changes, english_changes, routes


def implementations():
    return {**source_caption_projection.implementations(),
        'native_source_projection': state.identity(source_caption_projection.__file__)}


def prepare(profile_path, output):
    profile_path, output = Path(profile_path).resolve(), Path(output).resolve()
    profile, core, amendments = configuration(profile_path)
    stage, contract, normalized = source_caption_projection.load_stage(core)
    require([entry['unit_id'] for entry in normalized] == [entry['unit_id'] for entry in amendments],
        'Component source inventory differs from native source stage')
    canonical = Path(state.load(core['stage_config']['path'])['work'])
    require(not output.exists() and ROOT not in (output, *output.parents)
        and all(not output.is_relative_to(parent) and not parent.is_relative_to(output) for parent in (stage, canonical)),
        'Use a fresh external component projection directory')
    output.mkdir(parents=True)
    manifest = {'kind': KIND, 'policy': POLICY, 'complete': False, 'config': profile['projection_config'],
        'component_profile': state.identity(profile_path), 'adapter': state.identity(__file__),
        'implementations': implementations(), 'stage_manifest': core['stage_manifest'], 'created_at': state.now(),
        'new_audio_or_media': False, 'canonical_project_modified': False,
        'human_wordlevel_audiovisual_alignment_proven': False, 'english_track_bytes_unchanged': False,
        'source_corrections_explicitly_applied': True, 'unselected_English_raw_bytes_claimed_preserved': False}
    save_json(output / 'manifest.json', manifest)
    try:
        archived = {language: editorial_stage.archive(bound, output / 'before' / f'{language}.srt')
            for language, bound in core['baseline_subtitles'].items()}
        results, bodies, changes, english_changes, routes = expected(core, contract, amendments,
            {language: entry['archive']['path'] for language, entry in archived.items()})
        for language in caption_projection.LANGUAGES:
            atomic_write(output / f'{language}.srt', caption_projection.srt_text(results[language]))
            atomic_write(output / f'{language}_display.srt', caption_projection.srt_text(bodies[language]))
        manifest.update(complete=True, before=archived, changes=changes, english_changes=english_changes,
            english_cue_routes=routes, outputs={path.name: state.identity(path) for path in output.glob('*.srt')})
        save_json(output / 'manifest.json', manifest)
        return check(profile_path, output)
    except BaseException as error:
        manifest.update(complete=False, error=f'{type(error).__name__}: {error}')
        save_json(output / 'manifest.json', manifest)
        raise


def check(profile_path, output):
    profile_path, output = Path(profile_path).resolve(), Path(output).resolve()
    profile, core, amendments = configuration(profile_path)
    _, contract, _ = source_caption_projection.load_stage(core)
    manifest = state.load(output / 'manifest.json')
    require(manifest['kind'] == KIND and manifest['policy'] == POLICY and manifest['complete'] is True
        and manifest['component_profile'] == state.identity(profile_path) and manifest['config'] == profile['projection_config']
        and manifest['adapter'] == state.identity(__file__) and manifest['implementations'] == implementations()
        and manifest['stage_manifest'] == core['stage_manifest'], 'Stale or incomplete explicit component projection')
    require(all(manifest[name] is False for name in ('new_audio_or_media', 'canonical_project_modified',
        'human_wordlevel_audiovisual_alignment_proven', 'english_track_bytes_unchanged',
        'unselected_English_raw_bytes_claimed_preserved')) and manifest['source_corrections_explicitly_applied'] is True,
        'Component projection authority was expanded')
    require(set(manifest['before']) == set(caption_projection.LANGUAGES), 'Incomplete exact original subtitle archives')
    for language, entry in manifest['before'].items():
        require(entry['original'] == core['baseline_subtitles'][language]
            and Path(entry['archive']['path']) == output / 'before' / f'{language}.srt'
            and state.matches(entry['archive'])
            and all(entry['archive'][name] == entry['original'][name] for name in ('bytes', 'mtime_ns', 'sha256')),
            'Original subtitle archive changed')
    results, bodies, changes, english_changes, routes = expected(core, contract, amendments,
        {language: entry['archive']['path'] for language, entry in manifest['before'].items()})
    require(manifest['changes'] == changes and manifest['english_changes'] == english_changes
        and manifest['english_cue_routes'] == routes, 'Component/phrase/cue ownership changed')
    names = {name for language in caption_projection.LANGUAGES for name in (f'{language}.srt', f'{language}_display.srt')}
    require(set(manifest['outputs']) == names == {path.name for path in output.glob('*.srt')},
        'Incomplete component subtitle inventory')
    for name, bound in manifest['outputs'].items():
        require(Path(bound['path']) == output / name and state.matches(bound), 'Component subtitle identity changed')
    for language in caption_projection.LANGUAGES:
        require((output / f'{language}.srt').read_text() == caption_projection.srt_text(results[language])
            and (output / f'{language}_display.srt').read_text() == caption_projection.srt_text(bodies[language]),
            'Projected source words, Chinese phrases or timestamps changed')
    return {'passed': True, 'kind': KIND, 'policy': POLICY, 'manifest': state.identity(output / 'manifest.json'),
        'complete_audio_cases': len(changes), 'Chinese_phrases': sum(entry['counts']['zh']['after'] for entry in changes),
        'English_cues': len(results['en']), 'English_changed_groups': len(english_changes),
        'English_component_replacements': sum(len(entry['components']) for entry in english_changes),
        'English_original_cue_coverage_complete': True, 'unselected_English_text_and_times_preserved': True,
        'English_raw_bytes_or_exact_wordlevel_timing_claimed_preserved': False,
        'new_audio_or_media': False, 'canonical_project_modified': False,
        'human_wordlevel_audiovisual_alignment_proven': False}


@contextmanager
def runtime(profile_path, output):
    profile_path, output = Path(profile_path).resolve(), Path(output).resolve()
    profile, _, _ = configuration(profile_path)
    check(profile_path, output)
    selected_config = Path(profile['projection_config']['path']).resolve()
    require(source_caption_projection.check is FROZEN_CHECK, 'Reject nested or foreign component projection dispatch')

    def dispatch(config_path, selected_output):
        if Path(config_path).resolve() == selected_config and Path(selected_output).resolve() == output:
            return check(profile_path, output)
        return FROZEN_CHECK(config_path, selected_output)

    source_caption_projection.check = dispatch
    try:
        yield
    finally:
        source_caption_projection.check = FROZEN_CHECK


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check'))
    parser.add_argument('profile', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    result = (prepare if arguments.action == 'prepare' else check)(arguments.profile, arguments.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
