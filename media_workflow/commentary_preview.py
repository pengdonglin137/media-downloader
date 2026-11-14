"""Isolated AI commentary previews, never source-speaker replacements or approval."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import re
import subprocess
from decimal import Decimal
from pathlib import Path

import dubbing_state as state
import speech_polish
from media_workflow.speech_variants import inspect_wave, require
from prepare_videos import save_json


KIND = 'ai-commentary-preview-v1'
EDITORIAL_KIND = 'ai-commentary-editorial-plan-v1'
LABEL = 'AI补充说明（非讲者原话）'
ANCHOR_AUTHORITY = 'caption start only; proposed pause-source insertion, not a validated turn or delivery timeline'
INPUTS = ('en.srt', 'translation_input.tsv', 'translation_task.json', 'preparation.json')
UNAPPROVED = ('production_modified', 'human_listened', 'pronunciation_or_emotion_approved',
    'automatic_winner', 'database_written', 'production_timeline_verified', 'source_speaker_verified')


def bind(editorial_path, destination):
    editorial_path, destination = Path(editorial_path).resolve(), Path(destination).resolve()
    editorial = state.load(editorial_path)
    fields = {'kind', 'work', 'technical_review_path', 'reference_evidence_paths',
        'voice', 'rate', 'label', 'max_seconds', 'cases'}
    require(isinstance(editorial, dict) and set(editorial) == fields
        and editorial['kind'] == EDITORIAL_KIND, 'Unexpected authored commentary plan')
    work = Path(editorial['work']).resolve()
    require(not destination.is_relative_to(work), 'Bound preview plan must remain outside the source project')
    plan = {'kind': KIND, 'work': editorial['work'], 'editorial_plan': state.identity(editorial_path),
        'inputs': {name: state.identity(work / name) for name in INPUTS},
        'original_video': state.identity(state.source_video(work)),
        'technical_review': state.identity(editorial_path.parent / editorial['technical_review_path']),
        'reference_evidence': [state.identity(editorial_path.parent / name)
            for name in editorial['reference_evidence_paths']],
        **{name: editorial[name] for name in ['voice', 'rate', 'label', 'max_seconds', 'cases']}}
    with destination.open('x') as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    validate_plan(destination)
    return {'kind': KIND, 'bound_plan': state.identity(destination), 'cases': len(plan['cases'])}


def validate_plan(path):
    path = Path(path).resolve()
    plan = state.load(path)
    fields = {'kind', 'work', 'editorial_plan', 'inputs', 'original_video', 'technical_review', 'reference_evidence',
        'voice', 'rate', 'label', 'max_seconds', 'cases'}
    require(isinstance(plan, dict) and set(plan) == fields, 'Unexpected commentary plan fields')
    require(plan['kind'] == KIND and plan['label'] == LABEL, 'Explicit AI commentary label required')
    require(plan['rate'] == '+0%' and type(plan['max_seconds']) is int
        and 0 < plan['max_seconds'] <= 40, 'Use normal speed and at most40 seconds per complete commentary')
    require(isinstance(plan['voice'], str) and re.fullmatch(r'zh-CN-[A-Za-z]+Neural', plan['voice']),
        'Select an explicit Chinese neural voice; unsupported styles are not accepted')
    require(state.matches(plan['editorial_plan']), 'Authored commentary plan changed')
    editorial = state.load(plan['editorial_plan']['path'])
    require(editorial['kind'] == EDITORIAL_KIND and all(plan[name] == editorial[name]
        for name in ['work', 'voice', 'rate', 'label', 'max_seconds', 'cases']), 'Authored text or parameters changed')
    editorial_parent = Path(plan['editorial_plan']['path']).parent
    require(Path(plan['technical_review']['path']) == (editorial_parent / editorial['technical_review_path']).resolve()
        and [bound['path'] for bound in plan['reference_evidence']] == [str((editorial_parent / name).resolve())
            for name in editorial['reference_evidence_paths']], 'Authored reference paths changed')
    work = Path(plan['work']).resolve()
    require(Path(plan['work']) == work, 'Use a canonical absolute source project path')
    require(set(plan['inputs']) == set(INPUTS), 'Bind the complete source/input/task/stage set')
    for name, bound in plan['inputs'].items():
        require(Path(bound['path']) == work / name and state.matches(bound), f'Stale source input: {name}')
    require(Path(plan['original_video']['path']) == state.source_video(work)
        and state.matches(plan['original_video']), 'Original video binding changed')
    require(state.matches(plan['technical_review']), 'Technical review binding changed')
    review = state.load(plan['technical_review']['path'])
    require(review['source_sha256'] == plan['inputs']['en.srt']['sha256'], 'Review belongs to different source')
    reviewed = {case['id']: case for case in review['cases']}
    require(len(reviewed) == len(review['cases']), 'Duplicate technical review case IDs')
    require(isinstance(plan['reference_evidence'], list) and plan['reference_evidence'], 'Bind factual evidence')
    for bound in plan['reference_evidence']:
        require(state.matches(bound), 'Referenced factual/source evidence changed')
    require(isinstance(plan['cases'], list) and 0 < len(plan['cases']) <= 16,
        'Expected one to sixteen complete commentary candidates')
    source = state.cues(work / 'en.srt')
    keys = set()
    for case in plan['cases']:
        expected = {'key', 'review_case', 'anchor_unit_id', 'source_anchor', 'source_ids',
            'display', 'spoken', 'reason', 'prosody_intent', 'preserved_facts'}
        require(isinstance(case, dict) and set(case) == expected, 'Unexpected candidate fields')
        key = case['key']
        require(isinstance(key, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', key)
            and key not in keys, 'Candidate keys must be unique safe names')
        keys.add(key)
        require(case['review_case'] in reviewed, 'Unknown technical review case')
        number = case['anchor_unit_id']
        require(type(number) is int and 1 <= number <= len(source), 'Invalid source anchor unit')
        require(isinstance(case['source_anchor'], str) and case['source_anchor'] == str(source[number - 1][0]),
            'Caption anchor differs from immutable source; no inferred speaker turn')
        numbers = case['source_ids']
        require(isinstance(numbers, list) and numbers and all(type(item) is int for item in numbers)
            and len(numbers) == len(set(numbers)) and number in numbers
            and all(1 <= item <= len(source) for item in numbers)
            and set(numbers) <= set(reviewed[case['review_case']]['source_units']),
            'Anchor/source references must belong to the reviewed case')
        require(all(isinstance(case[name], str) and case[name].strip()
            for name in ['display', 'spoken', 'reason', 'prosody_intent']), 'Complete authored commentary required')
        require(case['spoken'].startswith('AI补充说明。') and case['display'].startswith(LABEL + '：'),
            'Both audio and display must explicitly identify AI, not the original speaker')
        require(isinstance(case['preserved_facts'], list) and case['preserved_facts']
            and all(isinstance(fact, str) and fact.strip() for fact in case['preserved_facts']),
            'List explicit semantic constraints; tests do not establish their truth')
    return plan


def boundary_observation(proof, samples):
    entries = []
    for line in Path(proof['metadata']['path']).read_text().splitlines():
        entry = json.loads(line)
        require(entry.get('type') == 'WordBoundary' and isinstance(entry.get('text'), str)
            and entry['text'].strip(), 'Expected actual nonempty WordBoundary records')
        require(type(entry.get('offset')) is int and entry['offset'] >= 0
            and type(entry.get('duration')) is int and entry['duration'] >= 0, 'Invalid boundary ticks')
        entries.append(entry)
    require(entries and all(left['offset'] <= right['offset'] for left, right in zip(entries, entries[1:])),
        'Missing or unsorted actual word boundaries')
    outside = sum(Decimal(entry['offset'] + entry['duration']) / 10000000
        > Decimal(samples) / 24000 for entry in entries)
    return {'word_count': len(entries), 'word_text': ''.join(entry['text'] for entry in entries),
        'last_end_ticks': max(entry['offset'] + entry['duration'] for entry in entries),
        'outside_decoded_duration_count': outside, 'phonemes_or_emotion_verified': False}


def decode_command(proof, destination):
    return ['ffmpeg', '-nostdin', '-v', 'error', '-i', proof['audio']['path'],
        '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', '-n', str(destination)]


async def preview(plan_path, output):
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    plan = validate_plan(plan_path)
    work = Path(plan['work'])
    require(not output.is_relative_to(work) and not work.is_relative_to(output),
        'Use a separate preview directory, never a source project or its parent')
    require(not output.exists(), 'Preserve existing successful/failed previews; do not repeat automatically')
    output.mkdir(parents=True)
    result = {'kind': KIND, 'created_at': state.now(), 'plan': state.identity(plan_path),
        'adapter': state.identity(__file__), 'synthesis_implementation': state.identity(speech_polish.__file__),
        'wave_inspector': state.identity(Path(inspect_wave.__code__.co_filename)), 'complete': False,
        'label': LABEL, 'cases': [], 'network_tts': True, 'audio_trimmed_or_time_stretched': False,
        'anchor_authority': ANCHOR_AUTHORITY,
        **{name: False for name in UNAPPROVED}}
    save_json(output / 'manifest.json', result)
    try:
        for case in plan['cases']:
            observed = {'key': case['key'], 'complete': False, 'source_anchor': case['source_anchor']}
            result['cases'].append(observed)
            save_json(output / 'manifest.json', result)
            proof = await speech_polish.synthesize_sample(case['spoken'], plan['voice'], output / 'cache')
            observed['proof'] = proof
            save_json(output / 'manifest.json', result)
            destination = output / f'{case["key"]}.wav'
            command = decode_command(proof, destination)
            decoded = subprocess.run(command, capture_output=True, text=True)
            observed['decode'] = {'command': command, 'actual_exit': decoded.returncode,
                'stderr': decoded.stderr}
            save_json(output / 'manifest.json', result)
            require(decoded.returncode == 0, 'Full untrimmed commentary decode failed')
            samples, digest = inspect_wave(destination)
            observed.update({'waveform': state.identity(destination), 'samples': samples,
                'pcm_sha256': digest, 'seconds': samples / 24000,
                'within_commentary_limit': samples <= plan['max_seconds'] * 24000,
                'boundaries': boundary_observation(proof, samples),
                'complete': True, 'human_selected': False})
            save_json(output / 'manifest.json', result)
            require(observed['within_commentary_limit'], 'Complete commentary exceeds its independent40second slot')
        require(validate_plan(plan_path) == plan, 'Source/review dependencies changed during preview')
        result['complete'] = True
    except BaseException as error:
        result['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        save_json(output / 'manifest.json', result)
    return result


def check(plan_path, output):
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    plan = validate_plan(plan_path)
    result = state.load(output / 'manifest.json')
    require(result['kind'] == KIND and result['complete'] is True
        and result['plan'] == state.identity(plan_path) and result['adapter'] == state.identity(__file__)
        and result['synthesis_implementation'] == state.identity(speech_polish.__file__)
        and result['wave_inspector'] == state.identity(Path(inspect_wave.__code__.co_filename)),
        'Stale or incomplete commentary preview')
    require(result['label'] == LABEL and result['network_tts'] is True
        and result['anchor_authority'] == ANCHOR_AUTHORITY
        and result['audio_trimmed_or_time_stretched'] is False
        and all(result[name] is False for name in UNAPPROVED), 'Preview authority was expanded')
    require(len(result['cases']) == len(plan['cases']), 'Candidate inventory changed')
    for case, observed in zip(plan['cases'], result['cases']):
        require(observed['key'] == case['key'] and observed['source_anchor'] == case['source_anchor']
            and observed['complete'] is True and observed['human_selected'] is False,
            'Candidate pairing or selection changed')
        proof = observed['proof']
        require(proof['contract']['text'] == case['spoken'] and proof['contract']['voice'] == plan['voice']
            and proof['contract']['rate'] == 0 and proof['contract']['boundary'] == 'WordBoundary',
            'Complete-utterance synthesis contract changed')
        require(all(state.matches(bound) for bound in [proof['audio'], proof['metadata'], observed['waveform']]),
            'Candidate audio or real boundary metadata changed')
        require(Path(observed['waveform']['path']) == output / f'{case["key"]}.wav', 'Unexpected output route')
        require(observed['decode']['command'] == decode_command(proof, observed['waveform']['path'])
            and observed['decode']['actual_exit'] == 0, 'Decode receipt changed')
        samples, digest = inspect_wave(observed['waveform']['path'])
        require(observed['samples'] == samples and observed['pcm_sha256'] == digest
            and observed['seconds'] == samples / 24000
            and observed['within_commentary_limit'] is True and samples <= plan['max_seconds'] * 24000
            and observed['boundaries'] == boundary_observation(proof, samples), 'PCM/length/boundary evidence changed')
        decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', proof['audio']['path'],
            '-ac', '1', '-ar', '24000', '-f', 's16le', '-'], capture_output=True)
        require(decoded.returncode == 0 and len(decoded.stdout) == samples * 2
            and hashlib.sha256(decoded.stdout).hexdigest() == digest,
            'Waveform is not the complete untrimmed MP3 decode')
    return {'kind': KIND, 'plan': state.identity(plan_path), 'manifest': state.identity(output / 'manifest.json'),
        'candidates_checked': len(result['cases']), 'complete_untrimmed_decode_checked': True,
        **{name: False for name in UNAPPROVED}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['bind', 'validate', 'preview', 'check'])
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    if arguments.action == 'bind':
        if arguments.output is None:
            parser.error('--output is required for bind')
        editorial = state.load(arguments.plan)
        with (Path(editorial['work']) / '.workflow.lock').open('r') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            result = bind(arguments.plan, arguments.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    plan = validate_plan(arguments.plan)
    with (Path(plan['work']) / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        if arguments.action == 'validate':
            result = {'kind': KIND, 'valid': True, 'cases': len(validate_plan(arguments.plan)['cases'])}
        else:
            if arguments.output is None:
                parser.error('--output is required for preview/check')
            result = (asyncio.run(preview(arguments.plan, arguments.output)) if arguments.action == 'preview'
                else check(arguments.plan, arguments.output))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
