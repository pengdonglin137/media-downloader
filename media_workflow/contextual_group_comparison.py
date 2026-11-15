"""Package both complete speech alternatives for comparison, without selecting one."""

import argparse
import json
import re
import shutil
from decimal import Decimal
from pathlib import Path

import numpy as np

import dubbing_state as state
from prepare_videos import save_json
from . import caption_projection, editorial_stage, grouped_phrase_refinement


KIND = 'complete-contextual-ab-comparison-v1'
RATE = 24000
FLAGS = ('canonical_adoption', 'human_selected', 'human_listened',
    'pronunciation_or_emotion_approved', 'final_delivery_approved')
require = editorial_stage.require


def configuration(path):
    plan = state.load(path)
    require(plan['kind'] == KIND and all(plan[name] is False for name in FLAGS),
        'Comparison must not select or approve either alternative')
    require(plan['adapter'] == state.identity(__file__), 'Comparison implementation changed')
    require(all(state.matches(row) for row in plan['inputs'].values()), 'Comparison input changed')
    inputs = plan['inputs']
    parent, groups = grouped_phrase_refinement.configuration(inputs['parent_plan']['path'])
    assembly = state.load(inputs['parent_assembly']['path'])
    media = state.load(inputs['parent_media']['path'])
    alternative = state.load(inputs['alternative_plan']['path'])
    manifest = state.load(inputs['alternative_manifest']['path'])
    proof = state.load(inputs['alternative_verification']['path'])
    require(assembly['plan'] == inputs['parent_plan'] and assembly['full'] == inputs['parent_full'],
        'Parent audio is not the complete bound assembly')
    require(media['assembly'] == inputs['parent_assembly'] and media['master'] == inputs['parent_master'],
        'Parent picture or audio package changed')
    require(alternative['parent_plan'] == inputs['parent_plan']
        and alternative['rate'] == '+0%' and alternative['pitch'] == '+0Hz',
        'Alternative source or synthesis controls changed')
    require(manifest['plan'] == inputs['alternative_plan'] and manifest['complete'] is True,
        'Alternative speech is incomplete')
    require(proof['passed'] is True and proof['plan'] == inputs['alternative_plan']
        and proof['manifest'] == inputs['alternative_manifest'], 'Missing independent whole-speech proof')
    require(all(alternative[name] is False for name in
        ('automatic_winner', 'canonical_adoption', 'human_listened', 'pronunciation_approved', 'emotion_approved')),
        'Do not turn ASR diagnostics into a selected winner')
    require(Decimal(plan['intro_seconds']) * RATE == assembly['intro_samples']
        and plan['intro_seconds'] == parent['intro_seconds'], 'Guide offset changed')
    previous = {tuple(case['unit_ids']): case for case in parent['edits']}
    alternatives = {tuple(case['unit_ids']): case for case in alternative['cases']}
    observed = {tuple(case['unit_ids']): case for case in manifest['cases']}
    selected = [tuple(case['unit_ids']) for case in plan['cases']]
    require(0 < len(selected) <= 4 and len(set(selected)) == len(selected)
        and set(selected) == set(alternatives) == set(observed),
        'Include both tracks and all bound alternative groups, without cherry-picking')
    cases = []
    for selection in plan['cases']:
        units = tuple(selection['unit_ids'])
        case, baseline, result = alternatives[units], previous[units], observed[units]
        for name in ('source', 'source_start', 'source_end', 'voice', 'speaker_id', 'source_review_note'):
            require(case[name] == baseline[name], 'Original group, role or window changed')
        require(case['before_spoken'] == baseline['spoken'], 'Wrong baseline spoken text')
        require(result['installed'] is False and result['fits_original_window'] is True
            and result['trimmed_samples'] == 0 and result['time_stretch'] is False,
            'Only complete uninstalled normal-rate speech may be compared')
        require(result['proof']['contract']['text'] == case['spoken']
            and result['proof']['contract']['voice'] == case['voice']
            and result['proof']['contract']['rate'] == 0, 'Whole-group synthesis identity changed')
        require(state.matches(result['waveform']) and state.matches(case['baseline_waveform']),
            'Whole speech waveform changed')
        require(selection['review_note'] and selection['chunks']
            and all(isinstance(text, str) and text and '\n' not in text for text in selection['chunks'])
            and ''.join(selection['chunks']) == case['display'], 'Caption phrases change the alternative text')
        cases.append((case, baseline, result, selection['chunks']))
    return plan, cases


def projection(case, observed, chunks):
    rows = grouped_phrase_refinement.words(observed['proof'], case['spoken'], observed['samples'])
    plain = caption_projection.plain
    require(plain(case['display'].replace('I/O', '输入输出')) == plain(case['spoken']),
        'Display/spoken expansion must preserve all normalized letters and Chinese words')
    positions, cursor = {}, 0
    for row in rows:
        positions[cursor] = Decimal(row['offset']) / 10000000
        cursor += len(plain(row['text']))
    require(''.join(chunks) == case['display'], 'Caption chunks lose display content')
    cuts, cursor = [], 0
    for text in chunks[:-1]:
        cursor += len(text)
        cuts.append(cursor)
    terms = list(re.finditer(r'[A-Za-z][A-Za-z0-9]*(?:/[A-Za-z0-9]+)*', case['display']))
    require(all(not (term.start() < cut < term.end()) for term in terms for cut in cuts),
        'Do not split a command or slash abbreviation across caption screens')
    merged, current, prefix = [], '', ''
    for index, text in enumerate(chunks):
        current += text
        prefix += text
        cursor = len(plain(prefix.replace('I/O', '输入输出')))
        if index == len(chunks) - 1 or cursor in positions:
            merged.append(current)
            current = ''
    require(not current and ''.join(merged) == case['display'], 'Caption merging loses text')
    edges, cursor = [Decimal(case['source_start'])], 0
    for text in merged[:-1]:
        cursor += len(plain(text.replace('I/O', '输入输出')))
        edges.append(Decimal(case['source_start']) + positions[cursor])
    edges.append(Decimal(case['source_start']) + Decimal(observed['samples']) / RATE)
    require(all(first < stop for first, stop in zip(edges, edges[1:])), 'Collapsed alternative caption')
    require(edges[-1] <= Decimal(case['source_end']), 'Caption exceeds original group window')
    return list(zip(edges, edges[1:], merged))


def location(plan, output):
    output = Path(output).resolve()
    work = Path(state.load(plan['inputs']['parent_plan']['path'])['work']).resolve()
    repo = Path(__file__).resolve().parent.parent
    require(not output.is_relative_to(repo) and not output.is_relative_to(work)
        and not work.is_relative_to(output), 'Keep the comparison outside production and the repository')
    require(all(not Path(row['path']).resolve().is_relative_to(output) for row in plan['inputs'].values()),
        'Output must not contain any immutable comparison input')
    return output


def assemble(plan_path, output):
    plan, cases = configuration(plan_path)
    output = location(plan, output)
    require(not output.exists(), 'Preserve previous comparison outputs')
    output.mkdir(parents=True)
    before = editorial_stage.read_wave(plan['inputs']['parent_full']['path'])
    after = before.copy()
    owned = np.zeros(len(before), dtype=bool)
    baseline_cues = caption_projection.read_srt(plan['inputs']['parent_chinese']['path'])
    revised = list(baseline_cues)
    offset = Decimal(plan['intro_seconds'])
    summaries = []
    for case, baseline, observed, chunks in cases:
        first = int((Decimal(case['source_start']) + offset) * RATE)
        stop = int((Decimal(case['source_end']) + offset) * RATE)
        replacement = editorial_stage.read_wave(observed['waveform']['path'])
        old = editorial_stage.read_wave(case['baseline_waveform']['path'])
        require(0 <= first < stop <= len(before) and not np.any(owned[first:stop])
            and len(replacement) == observed['samples'] <= stop - first,
            'Changed length, overlapping groups or speech outside the complete movie')
        require(np.array_equal(before[first:first + len(old)], old)
            and not np.any(before[first + len(old):stop]), 'Wrong baseline group placement')
        after[first:stop] = 0
        after[first:first + len(replacement)] = replacement
        owned[first:stop] = True
        start_seconds, stop_seconds = Decimal(first) / RATE, Decimal(stop) / RATE
        current = [row for row in revised if row[0] < stop_seconds and row[1] > start_seconds]
        require(current and all(start_seconds <= row[0] < row[1] <= stop_seconds for row in current)
            and ''.join(row[2] for row in current) == baseline['display'],
            'A baseline subtitle crosses the original group or changes its text')
        projected = [(start + offset, end + offset, text)
            for start, end, text in projection(case, observed, chunks)]
        revised = sorted([row for row in revised if row not in current] + projected)
        summaries.append({'unit_ids': case['unit_ids'], 'start_sample': first, 'end_sample': stop,
            'samples': len(replacement), 'remaining_samples': stop - first - len(replacement),
            'new_cues': [[str(start), str(end), text] for start, end, text in projected]})
    require(np.array_equal(before[~owned], after[~owned]), 'Changed PCM outside alternative windows')
    editorial_stage.write_wave(output / 'full-B.wav', after)
    shutil.copyfile(plan['inputs']['parent_chinese']['path'], output / 'zh-A.srt')
    shutil.copyfile(plan['inputs']['parent_english']['path'], output / 'en-source.srt')
    (output / 'zh-B.srt').write_text(caption_projection.srt_text(revised))
    result = {'kind': KIND, 'plan': state.identity(plan_path), 'full_B': state.identity(output / 'full-B.wav'),
        'full_samples': len(after), 'outside_alternative_windows_pcm_exact': True,
        'original_guide_pcm_exact': bool(np.array_equal(before[:int(offset * RATE)], after[:int(offset * RATE)])),
        'cases': summaries, 'caption_B_cues': len(revised), 'caption_A_cues': len(baseline_cues),
        'captions': {name: state.identity(output / name) for name in ('zh-A.srt', 'zh-B.srt', 'en-source.srt')},
        'word_onsets_are_engine_metadata_not_acoustic_alignment': True,
        **{name: False for name in FLAGS}}
    save_json(output / 'assembly.json', result)
    return result


def package(plan_path, output):
    plan, cases = configuration(plan_path)
    output = location(plan, output)
    assembly = state.load(output / 'assembly.json')
    require(assembly['plan'] == state.identity(plan_path) and state.matches(assembly['full_B'])
        and all(state.matches(row) for row in assembly['captions'].values()), 'Comparison assembly changed')
    target = output / 'comparison.mp4'
    require(not target.exists(), 'Preserve previous comparison media')
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-n', '-copyts',
        '-i', plan['inputs']['parent_master']['path'], '-i', assembly['full_B']['path']]
    for name in ('zh-A.srt', 'zh-B.srt', 'en-source.srt'):
        command += ['-i', str(output / name)]
    command += ['-map', '0:v:0', '-map', '0:a:0', '-map', '1:a:0', '-map', '0:a:1',
        '-map', '2:0', '-map', '3:0', '-map', '4:0', '-map_metadata', '0', '-map_chapters', '0',
        '-c', 'copy', '-c:a:1', 'aac', '-b:a:1', '128k', '-c:s', 'mov_text',
        '-video_track_timescale', plan['video_timescale'], '-avoid_negative_ts', 'disabled',
        '-metadata', 'title=INTERNAL A-B COMPARISON - neither Chinese version selected',
        '-metadata', 'comment=Switch Chinese A with subtitle A or Chinese B with subtitle B manually; source English follows the original, not TTS. No listening or emotion approval.',
        '-metadata:s:a:0', 'language=zho', '-metadata:s:a:0', 'handler_name=A previous Chinese - unapproved',
        '-metadata:s:a:1', 'language=zho', '-metadata:s:a:1', 'handler_name=B three full-group alternatives - unapproved',
        '-metadata:s:a:2', 'language=eng', '-metadata:s:a:2', 'handler_name=Inherited complete original backup',
        '-metadata:s:s:0', 'language=zho', '-metadata:s:s:0', 'handler_name=A previous Chinese timing',
        '-metadata:s:s:1', 'language=zho', '-metadata:s:s:1', 'handler_name=B alternative word-onset timing - not acoustic alignment',
        '-metadata:s:s:2', 'language=eng', '-metadata:s:s:2', 'handler_name=Inherited source English - not TTS timing',
        '-disposition:a:0', 'default', '-disposition:a:1', '0', '-disposition:a:2', '0',
        '-disposition:s:0', 'default', '-disposition:s:1', '0', '-disposition:s:2', '0', str(target)]
    grouped_phrase_refinement.operation(output, 'comparison', command)
    result = {'kind': KIND, 'assembly': state.identity(output / 'assembly.json'),
        'media': state.identity(target), 'new_tts_or_asr_requests': 0,
        'default_track_is_inherited_A_not_selection': True, **{name: False for name in FLAGS}}
    save_json(output / 'media.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('assemble', 'package'))
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    result = {'assemble': assemble, 'package': package}[arguments.action](arguments.plan, arguments.output)
    print(json.dumps(result, ensure_ascii=False))
