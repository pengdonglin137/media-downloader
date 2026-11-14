"""Project reviewed whole-unit edits onto observed TTS word boundaries."""

import argparse
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import dubbing_state as state
from prepare_videos import atomic_write, save_json
from . import editorial_stage
from .speech_variants import require


KIND = 'whole-unit-word-boundary-projection-v1'
LANGUAGES = ('zh', 'en', 'bilingual')


def read_srt(path):
    entries = []
    text = Path(path).read_text(encoding='utf-8-sig').replace('\r\n', '\n').strip()
    for number, block in enumerate(re.split(r'\n\s*\n', text), 1):
        lines = block.splitlines()
        require(len(lines) >= 3 and lines[0] == str(number), 'Expected complete sequential SRT cues')
        bounds = []
        for stamp in lines[1].split(' --> '):
            match = re.fullmatch(r'(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})', stamp)
            require(match is not None, 'Expected exact millisecond SRT timestamps')
            hours, minutes, seconds, milliseconds = map(Decimal, match.groups())
            bounds.append(hours * 3600 + minutes * 60 + seconds + milliseconds / 1000)
        require(len(bounds) == 2 and 0 <= bounds[0] < bounds[1], 'Invalid SRT window')
        entries.append((*bounds, '\n'.join(lines[2:])))
    require(entries, 'Empty SRT is not a complete subtitle projection')
    return entries


def timestamp(value):
    total = int((value * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    hours, remainder = divmod(total, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}'


def srt_text(entries):
    return ''.join(f'{number}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n\n'
        for number, (start, end, text) in enumerate(entries, 1))


def plain(text):
    return re.sub(r'[^\w]', '', text, flags=re.UNICODE).casefold()


def project_unit(case, selection):
    chunks = selection['chunks']
    require(isinstance(chunks, list) and 1 <= len(chunks) <= 16, 'Expected one to sixteen reviewed caption phrases')
    require(all(set(chunk) == {'zh', 'spoken', 'en'} and all(isinstance(text, str) and text.strip()
        and '\n' not in text and '\r' not in text for text in chunk.values()) for chunk in chunks),
        'Every phrase needs explicit complete Chinese/spoken/source-English text')
    require(''.join(chunk['zh'] for chunk in chunks) == case['display']
        and ''.join(chunk['spoken'] for chunk in chunks) == case['spoken'], 'Caption phrases lose or change current Chinese/spoken text')
    require(' '.join(chunk['en'] for chunk in chunks).split() == case['source'].split(),
        'Source-English phrase projection must preserve the entire source word stream')
    require(isinstance(selection['review_note'], str) and selection['review_note'].strip(), 'Phrase/source review reason required')
    bound = case['preview']['proof']['metadata']
    require(state.matches(bound), 'Observed word-boundary metadata changed')
    words = [json.loads(line) for line in Path(bound['path']).read_text().splitlines()]
    positions, cursor, previous = {}, 0, Decimal('-1')
    full_duration = Decimal(case['preview']['samples']) / editorial_stage.RATE
    for word in words:
        require(word['type'] == 'WordBoundary' and plain(word['text']), 'Unsupported/empty observed word boundary')
        offset = Decimal(str(word['offset'])) / 10000000
        duration = Decimal(str(word['duration'])) / 10000000
        require(previous < offset and duration > 0 and offset + duration <= full_duration,
            'Word-boundary order or duration exceeds the complete audio')
        positions[cursor] = offset
        cursor += len(plain(word['text']))
        previous = offset
    require(plain(''.join(word['text'] for word in words)) == plain(case['spoken']),
        'Observed words do not cover the complete current spoken unit')
    start, end = Decimal(case['source_start']), Decimal(case['source_end'])
    edges, cursor = [start], 0
    for chunk in chunks[:-1]:
        cursor += len(plain(chunk['spoken']))
        require(cursor in positions, 'A caption boundary splits an observed synthesis word')
        edge = (start + positions[cursor]).quantize(Decimal('.001'), rounding=ROUND_HALF_UP)
        require(edges[-1] < edge < end, 'Projected phrase boundary escapes or collapses the original unit')
        edges.append(edge)
    edges.append(end)
    return {language: [(first, last, chunk['zh'] if language == 'zh' else chunk['zh'] + '\n' + chunk['en'])
        for first, last, chunk in zip(edges, edges[1:], chunks)] for language in ('zh', 'bilingual')}


def expected(config, contract, baselines):
    require(config['kind'] == KIND and set(config['baseline_subtitles']) == set(LANGUAGES), 'Unsupported projection configuration')
    require(isinstance(config['edits'], list) and len(config['edits']) == len(contract['cases']),
        'Projection must account for every actual editorial waveform edit')
    selected = {selection['unit_id']: selection for selection in config['edits']}
    require(len(selected) == len(config['edits']) and set(selected) == {case['unit_id'] for case in contract['cases']},
        'Duplicate, missing or unrelated projection unit')
    offset = Decimal(config['intro_offset_seconds'])
    require(offset >= 0 and offset == offset.quantize(Decimal('.001')), 'Bind the actual millisecond intro offset')
    results = {language: read_srt(path) for language, path in baselines.items()}
    changes = []
    for case in contract['cases']:
        selection = selected[case['unit_id']]
        require(selection['key'] == case['key'], 'Projection selected a different whole-utterance variant')
        unit = project_unit(case, selection)
        start, end = Decimal(case['source_start']) + offset, Decimal(case['source_end']) + offset
        counts = {}
        for language in ('zh', 'bilingual'):
            current = results[language]
            affected = [cue for cue in current if cue[0] < end and cue[1] > start]
            require(affected and affected[0][0] == start and affected[-1][1] == end
                and all(start <= cue[0] < cue[1] <= end for cue in affected),
                'Existing subtitle cues cross the selected source-unit boundary')
            outside = [cue for cue in current if not (cue[0] < end and cue[1] > start)]
            replacement = [(first + offset, last + offset, text) for first, last, text in unit[language]]
            results[language] = sorted(outside + replacement, key=lambda cue: (cue[0], cue[1]))
            counts[language] = {'before': len(affected), 'after': len(replacement)}
        changes.append({'unit_id': case['unit_id'], 'counts': counts,
            'body_edges': [str(cue[0]) for cue in unit['zh']] + [str(unit['zh'][-1][1])],
            'actual_word_boundaries': case['preview']['proof']['metadata'],
            'human_wordlevel_audiovisual_alignment_proven': False})
    bodies = {}
    for language, entries in results.items():
        require(not any(first < offset < last for first, last, _ in entries), 'A subtitle cue crosses intro/body boundary')
        bodies[language] = [(first - offset, last - offset, text) for first, last, text in entries if first >= offset]
        require(bodies[language], 'Missing full body subtitle projection')
    return results, bodies, changes


def prepare(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config = state.load(config_path)
    require(not output.exists(), 'Preserve successful or interrupted caption projections')
    for name in ('stage_config', 'stage_manifest', 'baseline_package'):
        require(state.matches(config[name]), f'Stale projection input: {name}')
    stage = Path(config['stage_manifest']['path']).parent
    editorial_stage.check(config['stage_config']['path'], stage)
    contract = state.load(state.load(stage / 'manifest.json')['contract']['path'])
    canonical = Path(state.load(config['stage_config']['path'])['work'])
    require(all(not output.is_relative_to(parent) and not parent.is_relative_to(output) for parent in (stage, canonical)),
        'Projection output must be separate from both authoritative projects')
    require(config['baseline_package'] == contract['baseline_evidence']['package.json']['archive'],
        'Baseline package differs from the verified editorial source archive')
    package = state.load(config['baseline_package']['path'])
    require(config['baseline_subtitles'] == package['subtitles']
        and Decimal(config['intro_offset_seconds']) == Decimal(str(package['body_offset_seconds'])),
        'Baseline subtitle route or actual intro offset changed')
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'kind': KIND, 'complete': False, 'config': state.identity(config_path), 'adapter': state.identity(__file__),
        'stage_manifest': config['stage_manifest'], 'created_at': state.now(), 'new_audio_or_media': False,
        'canonical_project_modified': False, 'human_wordlevel_audiovisual_alignment_proven': False}
    save_json(output / 'manifest.json', manifest)
    try:
        archived = {language: editorial_stage.archive(bound, output / 'before' / f'{language}.srt')
            for language, bound in config['baseline_subtitles'].items()}
        results, bodies, changes = expected(config, contract, {language: item['archive']['path'] for language, item in archived.items()})
        for language in LANGUAGES:
            if language == 'en':
                atomic_write(output / 'en.srt', Path(archived['en']['archive']['path']).read_text())
            else:
                atomic_write(output / f'{language}.srt', srt_text(results[language]))
            atomic_write(output / f'{language}_display.srt', srt_text(bodies[language]))
        manifest.update({'complete': True, 'before': archived, 'changes': changes,
            'outputs': {name: state.identity(output / name) for name in
                [*[f'{language}.srt' for language in LANGUAGES], *[f'{language}_display.srt' for language in LANGUAGES]]}})
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
    require(manifest['kind'] == KIND and manifest['complete'] is True and manifest['config'] == state.identity(config_path)
        and manifest['adapter'] == state.identity(__file__) and manifest['stage_manifest'] == config['stage_manifest']
        and state.matches(config['stage_manifest']) and state.matches(config['baseline_package']), 'Stale/incomplete projection authority')
    stage = Path(config['stage_manifest']['path']).parent
    editorial_stage.check(config['stage_config']['path'], stage)
    contract = state.load(state.load(stage / 'manifest.json')['contract']['path'])
    require(config['baseline_package'] == contract['baseline_evidence']['package.json']['archive'], 'Projection baseline package changed')
    package = state.load(config['baseline_package']['path'])
    require(config['baseline_subtitles'] == package['subtitles']
        and Decimal(config['intro_offset_seconds']) == Decimal(str(package['body_offset_seconds'])), 'Subtitle/intro baseline changed')
    for language, item in manifest['before'].items():
        require(item['original'] == config['baseline_subtitles'][language] and state.matches(item['archive'])
            and all(item['original'][key] == item['archive'][key] for key in ('bytes', 'mtime_ns', 'sha256')),
            'Baseline subtitle archive changed')
    results, bodies, changes = expected(config, contract, {language: item['archive']['path'] for language, item in manifest['before'].items()})
    require(manifest['changes'] == changes and all(manifest[name] is False for name in
        ('new_audio_or_media', 'canonical_project_modified', 'human_wordlevel_audiovisual_alignment_proven')),
        'Projection claims or observed word-boundary evidence changed')
    require(set(manifest['outputs']) == {name for language in LANGUAGES for name in
        (f'{language}.srt', f'{language}_display.srt')}, 'Incomplete projected subtitle inventory')
    for name, bound in manifest['outputs'].items():
        require(Path(bound['path']) == output / name and state.matches(bound), 'Projected subtitle identity changed')
    for language in LANGUAGES:
        require(read_srt(output / f'{language}.srt') == results[language]
            and read_srt(output / f'{language}_display.srt') == bodies[language], 'Caption content/time differs from exact projection')
    require((output / 'en.srt').read_bytes() == Path(manifest['before']['en']['archive']['path']).read_bytes(),
        'Unchanged full English subtitle bytes differ')
    return {'passed': True, 'manifest': state.identity(output / 'manifest.json'), 'edits': changes,
        'all_other_caption_text_and_times_preserved': True, 'english_track_bytes_unchanged': True,
        'new_audio_or_media': False, 'human_wordlevel_audiovisual_alignment_proven': False}


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
