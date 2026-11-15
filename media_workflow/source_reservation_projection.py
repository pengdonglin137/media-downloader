"""Project actual-source subtitles with explicit ASR anchors, not synthesis timing."""

import argparse
import json
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import dubbing_state as state
from prepare_videos import atomic_write, save_json
from . import caption_projection, editorial_stage, source_reservation_selection
from .speech_variants import require


KIND = 'actual-source-ASR-anchored-caption-projection-v1'
FROZEN_CHECK = caption_projection.check


def anchor_time(anchor, source):
    require(set(anchor) == {'evidence', 'segment_index', 'word_index', 'text', 'edge'}
        and anchor['edge'] in ('start', 'end') and state.matches(anchor['evidence']), 'Invalid/stale source ASR anchor')
    data = state.load(anchor['evidence']['path'])
    require(data['complete'] is True and data['human_listened'] is False and data['source'] == source,
        'ASR evidence does not belong to the complete bound source or claims human listening')
    segment, word = anchor['segment_index'], anchor['word_index']
    require(type(segment) is int and type(word) is int and segment >= 0 and word >= 0,
        'Use explicit nonnegative ASR word coordinates')
    require(segment < len(data['segments']) and word < len(data['segments'][segment]['words']), 'ASR coordinate outside evidence')
    observed = data['segments'][segment]['words'][word]
    require(observed['text'] == anchor['text'] and observed['text'].strip()
        and Decimal(str(observed['start'])) < Decimal(str(observed['end'])), 'ASR anchor word changed or has no duration')
    return Decimal(str(observed[anchor['edge']])).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)


def project_unit(case, selection):
    require(case['kind'] == 'actual-source-retention' and case['spoken'] == '…'
        and case['TTS_preview_or_word_boundaries'] is None, 'Reject synthetic cases or invented TTS timing')
    require(set(selection) == {'unit_id', 'chunks', 'anchors', 'review_note'}
        and selection['unit_id'] == case['unit_id'] and isinstance(selection['review_note'], str)
        and selection['review_note'].strip(), 'Explicit complete-unit source-caption review required')
    chunks, anchors = selection['chunks'], selection['anchors']
    require(isinstance(chunks, list) and chunks and isinstance(anchors, list) and len(anchors) == len(chunks) - 1,
        'Missing source phrase or actual-source boundary ownership')
    for chunk in chunks:
        require(set(chunk) == {'zh', 'en'} and all(isinstance(value, str) and value.strip()
            and '\n' not in value and '\r' not in value for value in chunk.values()), 'Invalid source-caption phrase')
        require(len(chunk['zh']) <= 64 and len(chunk['en']) <= 180, 'Split overcrowded actual-source bilingual phrases')
    require(' '.join(chunk['en'] for chunk in chunks).split() == case['source'].split(),
        'Actual-source bilingual captions must preserve the complete current English word stream')
    notice_only = case['display'].startswith('【原声保留：') and case['display'].endswith('】')
    require(all(chunk['zh'] == case['display'] for chunk in chunks) if notice_only else
        ''.join(chunk['zh'] for chunk in chunks) == case['display'], 'Actual-source captions changed or omitted current Chinese')
    edges = [Decimal(case['source_start'])] + [anchor_time(anchor, case['source_authority']['media'])
        for anchor in anchors] + [Decimal(case['source_end'])]
    require(all(first.is_finite() and first < last for first, last in zip(edges, edges[1:])),
        'Actual-source anchors escape, overlap or collapse the complete source unit')
    return {language: [(first, last, chunk['zh'] if language == 'zh' else chunk['zh'] + '\n' + chunk['en'])
        for first, last, chunk in zip(edges, edges[1:], chunks)] for language in ('zh', 'bilingual')}


def load_stage(config):
    require(set(config) == {'kind', 'stage_config', 'stage_manifest', 'baseline_package',
        'baseline_subtitles', 'intro_offset_seconds', 'edits'} and config['kind'] == KIND,
        'Unsupported actual-source caption configuration')
    for name in ('stage_config', 'stage_manifest', 'baseline_package'):
        require(state.matches(config[name]), 'Actual-source caption upstream identity changed')
    manifest_path = Path(config['stage_manifest']['path'])
    require(manifest_path.name == 'manifest.json', 'Use the exact source selection manifest')
    source_reservation_selection.check(config['stage_config']['path'], manifest_path.parent)
    manifest = state.load(manifest_path)
    require(manifest['config'] == config['stage_config'], 'Actual-source selection configuration differs')
    contract = state.load(manifest['contract']['path'])
    package = state.load(config['baseline_package']['path'])
    require(config['baseline_package'] == contract['baseline_evidence']['package.json']['archive']
        and config['baseline_subtitles'] == package['subtitles']
        and set(config['baseline_subtitles']) == set(caption_projection.LANGUAGES)
        and all(state.matches(bound) for bound in config['baseline_subtitles'].values()), 'Full subtitle baseline changed')
    offset = Decimal(config['intro_offset_seconds'])
    require(isinstance(config['intro_offset_seconds'], str) and offset.is_finite()
        and offset == Decimal(str(package['body_offset_seconds'])) and offset >= 0
        and offset == offset.quantize(Decimal('.001')), 'Use the real unchanged intro offset')
    return contract


def expected(config, contract, baselines):
    selections = {entry['unit_id']: entry for entry in config['edits']}
    require(len(selections) == len(config['edits']) == len(contract['cases'])
        and set(selections) == {case['unit_id'] for case in contract['cases']}, 'Incomplete actual-source caption inventory')
    offset = Decimal(config['intro_offset_seconds'])
    results = {language: caption_projection.read_srt(path) for language, path in baselines.items()}
    changes = []
    for case in contract['cases']:
        unit = project_unit(case, selections[case['unit_id']])
        start, end = Decimal(case['source_start']) + offset, Decimal(case['source_end']) + offset
        counts = {}
        english = [cue for cue in results['en'] if cue[0] < end and cue[1] > start]
        require(english and english[0][0] == start and english[-1][1] == end
            and all(start <= first < last <= end for first, last, _ in english)
            and ' '.join(text for _, _, text in english).split() == case['source'].split(),
            'Original full English cues cross or omit the selected source word stream')
        for language in ('zh', 'bilingual'):
            current = results[language]
            affected = [cue for cue in current if cue[0] < end and cue[1] > start]
            require(affected and affected[0][0] == start and affected[-1][1] == end
                and all(start <= first < last <= end for first, last, _ in affected), 'Baseline captions cross the source boundary')
            outside = [cue for cue in current if not (cue[0] < end and cue[1] > start)]
            replacements = [(first + offset, last + offset, text) for first, last, text in unit[language]]
            results[language] = sorted(outside + replacements, key=lambda cue: (cue[0], cue[1]))
            counts[language] = {'before': len(affected), 'after': len(replacements)}
        changes.append({'unit_id': case['unit_id'], 'counts': counts,
            'body_edges': [str(cue[0]) for cue in unit['zh']] + [str(unit['zh'][-1][1])],
            'ASR_anchors': selections[case['unit_id']]['anchors'], 'TTS_word_boundaries_used': False,
            'human_wordlevel_audiovisual_alignment_proven': False})
    bodies = {}
    for language, entries in results.items():
        require(not any(first < offset < last for first, last, _ in entries), 'Subtitle crosses the unchanged intro boundary')
        require(all(before[1] <= after[0] for before, after in zip(entries, entries[1:])), 'Overlapping actual-source subtitles')
        bodies[language] = [(first - offset, last - offset, text) for first, last, text in entries if first >= offset]
        require(bodies[language], 'Missing complete body captions')
    return results, bodies, changes


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = state.load(config_path)
    contract = load_stage(config)
    canonical = Path(state.load(config['stage_config']['path'])['work'])
    stage = Path(config['stage_manifest']['path']).parent
    require(not output.exists() and all(not output.is_relative_to(parent) and not parent.is_relative_to(output)
        for parent in (canonical, stage)), 'Preserve source projects and existing subtitle attempts')
    results, bodies, changes = expected(config, contract, {name: bound['path'] for name, bound in config['baseline_subtitles'].items()})
    output.mkdir(parents=True)
    before = {}
    for language, bound in config['baseline_subtitles'].items():
        before[language] = editorial_stage.archive(bound, output / 'before' / f'{language}.srt')
        for name, entries in ((f'{language}.srt', results[language]), (f'{language}_display.srt', bodies[language])):
            atomic_write(output / name, caption_projection.srt_text(entries))
    (output / 'en.srt').write_bytes(Path(before['en']['archive']['path']).read_bytes())
    save_json(output / 'manifest.json', {'kind': KIND, 'complete': True, 'config': state.identity(config_path),
        'adapter': state.identity(__file__), 'stage_manifest': config['stage_manifest'], 'before': before,
        'changes': changes, 'outputs': {path.name: state.identity(path) for path in output.glob('*.srt')},
        'new_audio_or_media': False, 'canonical_project_modified': False, 'TTS_word_boundaries_used': False,
        'human_wordlevel_audiovisual_alignment_proven': False})
    return check(config_path, output)


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = state.load(config_path)
    contract = load_stage(config)
    manifest = state.load(output / 'manifest.json')
    require(manifest['kind'] == KIND and manifest['complete'] is True and manifest['config'] == state.identity(config_path)
        and manifest['adapter'] == state.identity(__file__) and manifest['stage_manifest'] == config['stage_manifest']
        and set(manifest['before']) == set(caption_projection.LANGUAGES)
        and all(manifest[name] is False for name in ('new_audio_or_media', 'canonical_project_modified',
            'TTS_word_boundaries_used', 'human_wordlevel_audiovisual_alignment_proven')), 'Stale or expanded source-caption authority')
    for language, entry in manifest['before'].items():
        require(entry['original'] == config['baseline_subtitles'][language]
            and Path(entry['archive']['path']) == output / 'before' / f'{language}.srt'
            and state.matches(entry['archive']) and all(entry['archive'][key] == entry['original'][key]
                for key in ('bytes', 'mtime_ns', 'sha256')), 'Exact original subtitle archive changed')
    results, bodies, changes = expected(config, contract, {name: entry['archive']['path'] for name, entry in manifest['before'].items()})
    names = {name for language in caption_projection.LANGUAGES for name in (f'{language}.srt', f'{language}_display.srt')}
    require(set(manifest['outputs']) == names == {path.name for path in output.glob('*.srt')}
        and manifest['changes'] == changes, 'Incomplete source-caption output or anchor inventory')
    for name, bound in manifest['outputs'].items():
        require(Path(bound['path']) == output / name and state.matches(bound), 'Actual-source projected subtitle changed')
    for language in caption_projection.LANGUAGES:
        require(caption_projection.read_srt(output / f'{language}.srt') == results[language]
            and caption_projection.read_srt(output / f'{language}_display.srt') == bodies[language], 'Actual-source caption content/time changed')
    require((output / 'en.srt').read_bytes() == Path(manifest['before']['en']['archive']['path']).read_bytes(),
        'Full English subtitle bytes must remain unchanged')
    return {'passed': True, 'kind': KIND, 'manifest': state.identity(output / 'manifest.json'), 'edits': changes,
        'English_track_bytes_unchanged': True, 'all_exterior_cues_preserved': True,
        'new_audio_or_media': False, 'human_wordlevel_audiovisual_alignment_proven': False}


@contextmanager
def runtime(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    check(config_path, output)
    require(caption_projection.check is FROZEN_CHECK, 'Reject nested or foreign actual-source caption dispatch')

    def dispatch(selected_config, selected_output):
        if Path(selected_config).resolve() == config_path and Path(selected_output).resolve() == output:
            return check(config_path, output)
        return FROZEN_CHECK(selected_config, selected_output)

    caption_projection.check = dispatch
    try:
        yield
    finally:
        caption_projection.check = FROZEN_CHECK


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    result = (prepare if arguments.action == 'prepare' else check)(arguments.config, arguments.output)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
