"""Create isolated whole-group punctuation and spoken-expansion candidates."""

import argparse
import asyncio
import importlib.metadata
import inspect
import json
import subprocess
from decimal import Decimal
from pathlib import Path

import numpy as np

import dub
import dubbing_state as state
import speech_groups
import speech_polish
from prepare_videos import atomic_write, save_json
from . import caption_projection, editorial_stage


KIND = 'isolated-whole-group-phrase-refinement-v1'
RATE = 24000
UNAPPROVED = ('canonical_adoption', 'human_selected', 'human_listened',
    'pronunciation_or_emotion_approved', 'new_source_role_approval', 'final_delivery_approved')
require = editorial_stage.require


def configuration(path):
    path = Path(path).resolve()
    plan = state.load(path)
    require(plan['kind'] == KIND and plan['rate'] == '+0%' and plan['pitch'] == '+0Hz',
        'Expected the normal-rate isolated phrase contract')
    require(all(plan[name] is False for name in UNAPPROVED), 'Editorial work cannot approve listening or production')
    require(all(state.matches(record) for record in plan['inputs'].values()), 'A current production input changed')
    require(all(plan[name] == plan['inputs'][name] for name in ('source', 'waveform', 'master')),
        'Bind every selected source, waveform and parent master to the current input inventory')
    for name in ('manifest.json', 'intro.wav', 'zh.srt', 'en.srt', 'bilingual.srt', 'chapters.ffmeta'):
        require(state.identity(Path(plan['package']) / name) == plan['inputs'][f'package/{name}'],
            'Candidate packaging must use the bound canonical package')
    work = Path(plan['work'])
    require(work.is_absolute() and state.identity(state.source_video(work)) == plan['source'], 'Wrong original source')
    narration = state.validate_narration(work)
    require(narration['origin'] == 'semantic-grouped' and narration['waveform'] == plan['waveform'],
        'Expected the unchanged real semantic-grouped baseline')
    groups = speech_groups.units(work)
    by_units = {tuple(group['unit_ids']): group for group in groups}
    require(0 < len(plan['edits']) <= len(groups), 'No complete group edits')
    used = set()
    originals = speech_groups.original_units(work)
    for case in plan['edits']:
        units = tuple(case['unit_ids'])
        require(units in by_units and units not in used and len(units) >= 2, 'Edits must reuse unique existing groups')
        used.add(units)
        group = by_units[units]
        require(case['source'] == ' '.join(group['source']) and case['before'] == group['spoken'] == group['zh'],
            'Whole-group source or baseline wording changed')
        require(case['source_start'] == str(group['start']) and case['source_end'] == str(group['end'])
            and group['end'] - group['start'] <= 40, 'Original group window changed')
        boundary = case['remove_comma_after_unit']
        require(boundary in units[:-1], 'Only an inherited intra-group subtitle boundary may lose a comma')
        cut = sum(len(originals[number - 1]['zh']) for number in units if number <= boundary) - 1
        require(group['zh'][cut] == '，' and case['display'] == group['zh'][:cut] + group['zh'][cut + 1:],
            'The display edit must remove exactly the named artificial comma')
        require(case['spoken'] == case['display'].replace('I/O', '输入输出'),
            'Only the explicit I/O spoken expansion is supported')
        require(case['voice'] == group['voice'] and case['speaker_id'] == group['speaker_id']
            and case['source_review_note'] == group['source_review_note'], 'Do not replace inherited role evidence')
        require(case['preserved_facts'] and case['intended_delivery'], 'Record semantic constraints and delivery intent')
    return plan, groups


def plain(text):
    return caption_projection.plain(text)


def words(proof, text, samples):
    require(state.matches(proof['metadata']), 'Returned word metadata changed')
    rows = [json.loads(line) for line in Path(proof['metadata']['path']).read_text().splitlines()]
    require(rows and all(row['type'] == 'WordBoundary' for row in rows), 'Missing complete word metadata')
    require(plain(''.join(row['text'] for row in rows)) == plain(text), 'Returned words lose spoken content')
    previous = -1
    for row in rows:
        require(type(row['offset']) is int and row['offset'] > previous and row['duration'] > 0
            and Decimal(row['offset'] + row['duration']) / 10000000 <= Decimal(samples) / RATE,
            'A word boundary exceeds the complete waveform')
        previous = row['offset']
    return rows


def operation(output, name, command):
    directory = output / 'operations'
    directory.mkdir(exist_ok=True)
    record = directory / f'{name}.json'
    require(not record.exists(), 'Preserve a prior command; do not silently replay it')
    save_json(record, {'command': command, 'started_at': state.now(), 'terminal': False})
    with (directory / f'{name}.log').open('xb') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
    receipt = {'command': command, 'terminal': True, 'exit_code': result.returncode,
        'finished_at': state.now(), 'log': state.identity(directory / f'{name}.log')}
    save_json(record, receipt)
    require(result.returncode == 0, f'{name} failed; keep its actual exit and outputs')
    return receipt


def output_location(plan, output):
    work = Path(plan['work']).resolve()
    require(output.is_absolute() and not output.is_relative_to(work) and not work.is_relative_to(output),
        'Use an external candidate directory, not a canonical project or its ancestor')


def validate_observed(case, observed):
    require(case['unit_ids'] == observed['unit_ids'] and state.matches(observed['waveform'])
        and state.matches(observed['proof']['audio']) and state.matches(observed['proof']['metadata']),
        'Whole-group candidate identity changed')
    require(observed['proof']['contract']['text'] == case['spoken']
        and observed['proof']['contract']['rate'] == 0 and observed['trimmed_samples'] == 0
        and observed['time_stretch'] is False, 'Wrong complete speech contract')
    require(observed['start_sample'] == int(Decimal(case['source_start']) * RATE)
        and observed['end_sample'] == int(Decimal(case['source_end']) * RATE), 'Candidate window changed')
    samples = len(editorial_stage.read_wave(observed['waveform']['path']))
    require(samples == observed['samples'] and observed['remaining_samples']
        == observed['end_sample'] - observed['start_sample'] - samples
        and observed['fits_original_window'] == (observed['remaining_samples'] >= 0), 'Changed sample accounting')
    words(observed['proof'], case['spoken'], samples)


async def synthesize(plan_path, output, resume=False):
    plan, groups = configuration(plan_path)
    output_location(plan, output)
    if resume:
        manifest = state.load(output / 'manifest.json')
        require(manifest['plan'] == state.identity(plan_path) and manifest['implementation'] == state.identity(__file__)
            and manifest['engine'] == importlib.metadata.version('edge-tts')
            and not manifest['synthesis_complete'] and all(manifest[name] is False for name in UNAPPROVED),
            'Only an unchanged interrupted candidate may resume')
        require(len(manifest['cases']) <= len(plan['edits']), 'Too many partial cases')
        for case, observed in zip(plan['edits'], manifest['cases']):
            validate_observed(case, observed)
        previous = output / f'interrupted-{len(list(output.glob("interrupted-*.json"))) + 1}.json'
        require(not previous.exists(), 'Preserve interrupted attempt evidence')
        save_json(previous, manifest)
        manifest.pop('error', None)
    else:
        require(not output.exists(), 'Use an absent external candidate directory')
        output.mkdir(parents=True)
        manifest = {'kind': KIND, 'plan': state.identity(plan_path), 'implementation': state.identity(__file__),
            'engine': importlib.metadata.version('edge-tts'), 'cases': [], 'synthesis_complete': False,
            'scope': 'isolated-editorial-candidate-not-canonical-production', **{name: False for name in UNAPPROVED}}
    import edge_tts
    require(inspect.signature(edge_tts.Communicate).parameters['pitch'].default == '+0Hz',
        'The shared normal-rate requester no longer has the recorded default pitch')
    try:
        for case in plan['edits'][len(manifest['cases']):]:
            key = '-'.join(map(str, case['unit_ids']))
            proof = await speech_polish.synthesize_sample(case['spoken'], case['voice'], output / 'speech-cache')
            raw = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', proof['audio']['path'],
                '-f', 'f32le', '-ac', '1', '-ar', str(RATE), '-'], capture_output=True, check=True).stdout
            require(len(raw) % 4 == 0 and raw, 'Incomplete full speech decode')
            floated = np.frombuffer(raw, dtype='<f4')
            require(np.all(np.isfinite(floated)), 'Nonfinite speech decode')
            metadata = words(proof, case['spoken'], len(floated))
            leveled = dub.level(floated)
            require(len(leveled) == len(floated) and np.max(np.abs(leveled)) <= 1, 'Gain changed length or clipped speech')
            waveform = output / f'group-{key}.wav'
            editorial_stage.write_wave(waveform, (leveled * 32767).astype('<i2'))
            first = int(Decimal(case['source_start']) * RATE)
            stop = int(Decimal(case['source_end']) * RATE)
            manifest['cases'].append({'unit_ids': case['unit_ids'], 'proof': proof,
                'waveform': state.identity(waveform), 'samples': len(floated), 'word_count': len(metadata),
                'start_sample': first, 'end_sample': stop, 'remaining_samples': stop - first - len(floated),
                'fits_original_window': len(floated) <= stop - first, 'rate': '+0%',
                'pitch': 'shared-requester-default-verified-+0Hz', 'trimmed_samples': 0, 'time_stretch': False})
            save_json(output / 'manifest.json', manifest)
        manifest['synthesis_complete'] = True
    except BaseException as error:
        manifest['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        save_json(output / 'manifest.json', manifest)
    return manifest


def candidate(plan_path, output):
    plan, groups = configuration(plan_path)
    output_location(plan, output)
    manifest = state.load(output / 'manifest.json')
    require(manifest['plan'] == state.identity(plan_path) and manifest['implementation'] == state.identity(__file__)
        and manifest['synthesis_complete'] and len(manifest['cases']) == len(plan['edits'])
        and all(manifest[name] is False for name in UNAPPROVED), 'Stale, incomplete or overclaimed candidate')
    for case, observed in zip(plan['edits'], manifest['cases']):
        validate_observed(case, observed)
    return plan, groups, manifest


def refined_chunks(chunks, cut):
    require(chunks and all(chunks), 'Missing baseline caption phrases')
    cursor = 0
    result = []
    for text in chunks:
        if cursor <= cut < cursor + len(text):
            local = cut - cursor
            require(text[local] == '，', 'Caption comma does not match its source-unit boundary')
            result.append(text[:local] + text[local + 1:])
        else:
            result.append(text)
        cursor += len(text)
    return result


def project(case, observed, chunks):
    metadata = words(observed['proof'], case['spoken'], observed['samples'])
    positions, cursor = {}, 0
    for row in metadata:
        positions[cursor] = Decimal(row['offset']) / 10000000
        cursor += len(plain(row['text']))
    require(''.join(chunks) == case['display'], 'Projection loses edited display text')
    merged, current, prefix = [], '', ''
    for index, chunk in enumerate(chunks):
        current += chunk
        prefix += chunk
        cursor = len(plain(prefix.replace('I/O', '输入输出')))
        if index == len(chunks) - 1 or cursor in positions:
            merged.append(current)
            current = ''
    require(not current and ''.join(merged) == case['display'], 'Caption merging lost a phrase')
    edges, cursor = [Decimal(case['source_start'])], 0
    for text in merged[:-1]:
        cursor += len(plain(text.replace('I/O', '输入输出')))
        edges.append(Decimal(case['source_start']) + positions[cursor])
    edges.append(Decimal(case['source_start']) + Decimal(observed['samples']) / RATE)
    require(all(first < stop for first, stop in zip(edges, edges[1:])), 'Projected caption window collapsed')
    return [(first, stop, text) for first, stop, text in zip(edges, edges[1:], merged)]


def assemble(plan_path, output):
    plan, groups, manifest = candidate(plan_path, output)
    require(all(case['fits_original_window'] for case in manifest['cases']), 'Whole speech exceeds a strict source window')
    require(not (output / 'assembly.json').exists(), 'Do not redo a successful assembly')
    before = editorial_stage.read_wave(plan['waveform']['path'])
    after = before.copy()
    owned = np.zeros(len(before), dtype=bool)
    for observed in manifest['cases']:
        first, stop = observed['start_sample'], observed['end_sample']
        replacement = editorial_stage.read_wave(observed['waveform']['path'])
        require(0 <= first < stop <= len(before) and not np.any(owned[first:stop])
            and len(replacement) == observed['samples'], 'Overlapping/out-of-body/changed full-group speech')
        after[first:stop] = 0
        after[first:first + len(replacement)] = replacement
        owned[first:stop] = True
    require(np.array_equal(before[~owned], after[~owned]), 'Changed speech outside edited groups')
    editorial_stage.write_wave(output / 'body.wav', after)
    chinese = caption_projection.read_srt(Path(plan['package']) / 'zh.srt')
    bilingual = caption_projection.read_srt(Path(plan['package']) / 'bilingual.srt')
    english = caption_projection.read_srt(Path(plan['package']) / 'en.srt')
    originals = speech_groups.original_units(Path(plan['work']))
    offset = Decimal(plan['intro_seconds'])
    projection = []
    for case, observed in zip(plan['edits'], manifest['cases']):
        start, stop = Decimal(case['source_start']) + offset, Decimal(case['source_end']) + offset
        current = [row for row in chinese if row[0] < stop and row[1] > start]
        require(current and all(start <= row[0] < row[1] <= stop for row in current), 'A baseline cue crosses a group')
        chunks = [row[2] for row in current]
        require(''.join(chunks) == case['before'], 'Baseline group display changed')
        cut = sum(len(originals[number - 1]['zh']) for number in case['unit_ids']
            if number <= case['remove_comma_after_unit']) - 1
        revised = refined_chunks(chunks, cut)
        new = [(first + offset, last + offset, text) for first, last, text in project(case, observed, revised)]
        chinese = sorted([row for row in chinese if not (row[0] < stop and row[1] > start)] + new)
        selected = [index for index, row in enumerate(bilingual) if row[0] < stop and row[1] > start]
        require(len(selected) == len(revised), 'Inherited bilingual/source phrase count changed')
        for index, text in zip(selected, revised):
            first, last, previous = bilingual[index]
            old_chinese, old_english = previous.split('\n', 1)
            require(old_chinese == chunks[selected.index(index)], 'Bilingual Chinese baseline changed')
            bilingual[index] = (first, last, text + '\n' + old_english)
        projection.append({'unit_ids': case['unit_ids'], 'old_cues': len(chunks), 'new_cues': len(new),
            'new_last_cue_end_is_full_wav_end_not_guessed_last_phoneme': True})
    for name, rows in (('zh', chinese), ('en', english), ('bilingual', bilingual)):
        atomic_write(output / f'{name}.srt', caption_projection.srt_text(rows))
    guide = editorial_stage.read_wave(Path(plan['package']) / 'intro.wav')
    intro_samples = int(offset * RATE)
    body_samples = plan['master_chinese_duration_samples'] - intro_samples
    require(len(guide) <= intro_samples and body_samples <= len(after) and not np.any(after[body_samples:]),
        'Packaging would trim real guide/body speech')
    full = np.concatenate((guide, np.zeros(intro_samples - len(guide), dtype='<i2'), after[:body_samples]))
    editorial_stage.write_wave(output / 'full.wav', full)
    result = {'kind': KIND, 'plan': state.identity(plan_path), 'manifest': state.identity(output / 'manifest.json'),
        'body': state.identity(output / 'body.wav'), 'full': state.identity(output / 'full.wav'),
        'changed_groups': len(manifest['cases']), 'reused_groups': len(groups) - len(manifest['cases']),
        'outside_edited_groups_pcm_exact': True, 'intro_samples': intro_samples,
        'guide_samples': len(guide), 'guide_zero_padding': intro_samples - len(guide),
        'body_samples': body_samples, 'removed_body_eof_samples_all_zero': len(after) - body_samples,
        'projection': projection, 'english_track_unchanged': True,
        'bilingual_English_text_and_timing_unchanged': True,
        'new_chinese_word_onsets_are_engine_metadata_not_human_alignment': True,
        **{name: False for name in UNAPPROVED}}
    save_json(output / 'assembly.json', result)
    return result


def package(plan_path, output):
    plan, groups, manifest = candidate(plan_path, output)
    assembly = state.load(output / 'assembly.json')
    require(state.matches(assembly['body']) and state.matches(assembly['full']), 'Assembled audio changed')
    master, upload = output / 'master.mp4', output / 'upload.mp4'
    require(not master.exists() and not upload.exists(), 'Keep previous candidate media')
    base = ['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-n']
    command = base + ['-copyts', '-i', plan['master']['path'], '-i', assembly['full']['path']]
    for name in ('zh', 'en', 'bilingual'):
        command += ['-i', str(output / f'{name}.srt')]
    command += ['-map', '0:v:0', '-map', '1:a:0', '-map', '0:a:1', '-map', '2:0', '-map', '3:0', '-map', '4:0',
        '-map_metadata', '0', '-map_chapters', '0', '-c', 'copy', '-c:a:0', 'aac', '-b:a:0', '128k',
        '-c:s', 'mov_text', '-video_track_timescale', plan['video_timescale'], '-avoid_negative_ts', 'disabled',
        '-metadata', 'title=INTERNAL EDITORIAL CANDIDATE - unapproved Chinese delivery',
        '-metadata:s:a:0', 'language=zho', '-metadata:s:s:0', 'language=zho',
        '-metadata:s:s:1', 'language=eng', '-metadata:s:s:2', 'language=zho',
        '-metadata:s:s:2', 'handler_name=Inherited bilingual timing - unapproved alignment',
        '-disposition:s:0', 'default', '-disposition:s:1', '0', '-disposition:s:2', '0', str(master)]
    operation(output, 'master', command)
    filters = f"subtitles=filename='{master}':si=0:force_style='FontName=Noto Sans CJK SC,FontSize=18,Outline=1,MarginV=24'"
    operation(output, 'upload', base + ['-copyts', '-i', str(master), '-map', '0:v:0', '-map', '0:a:0',
        '-map_metadata', '0', '-map_chapters', '0', '-vf', filters, '-c:v', 'libx264', '-preset', 'fast',
        '-crf', '18', '-threads', '4', '-pix_fmt', 'yuv420p', '-vsync', '0', '-c:a', 'copy',
        '-video_track_timescale', plan['video_timescale'], '-avoid_negative_ts', 'disabled', str(upload)])
    result = {'kind': KIND, 'assembly': state.identity(output / 'assembly.json'),
        'master': state.identity(master), 'upload': state.identity(upload),
        'guide_video_and_parent_original_backup_copied_not_resynthesized': True,
        **{name: False for name in UNAPPROVED}}
    save_json(output / 'media.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('synthesize', 'assemble', 'package'))
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true', help='Resume only an identity-bound interrupted synthesis')
    arguments = parser.parse_args()
    plan_path, output = arguments.plan.resolve(), arguments.output.resolve()
    require(not arguments.resume or arguments.action == 'synthesize', 'Only synthesis supports explicit resume')
    if arguments.action == 'synthesize':
        result = asyncio.run(synthesize(plan_path, output, arguments.resume))
    else:
        result = globals()[arguments.action](plan_path, output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
