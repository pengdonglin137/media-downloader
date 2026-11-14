"""Complete-utterance speech previews, separate from production and listening approval."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import re
import subprocess
import wave
from decimal import Decimal
from pathlib import Path

import dubbing_state as state
import speech_polish
from check_videos import translations
from prepare_videos import save_json


KIND = 'complete-utterance-speech-variants-v1'
SAMPLE_RATE = 24000
INPUTS = ('en.srt', 'translation.tsv', 'spoken_overrides.json', 'tts_voice.txt',
    'voices.json', 'narration.json', 'render_state.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_plan(path):
    path = Path(path).resolve()
    plan = state.load(path)
    require(plan.get('kind') == KIND and plan.get('rate') == '+0%', 'Unsupported preview kind or rate')
    work = Path(plan['work']).resolve()
    require(Path(plan['work']) == work, 'Use an explicit canonical absolute work path')
    bindings = plan['inputs']
    require(set(bindings) == set(INPUTS), 'Bind every current source/text/voice/narration/render input')
    for name, bound in bindings.items():
        require(Path(bound['path']) == work / name and state.matches(bound), f'Stale preview input: {name}')
    rendering = state.load(work / 'render_state.json')
    require(plan['master'] == rendering['output'] and state.matches(plan['master']), 'Current master binding changed')
    require(plan['voice'] == (work / 'tts_voice.txt').read_text().strip(), 'Current production voice changed')
    for bound in plan['reference_evidence']:
        require(state.matches(bound), 'Referenced source/audio evidence changed')
    source = state.cues(work / 'en.srt')
    display = translations(work / 'translation.tsv', len(source))
    overrides = state.load(work / 'spoken_overrides.json')
    voices = state.load(work / 'voices.json')
    require(len(voices) == len(source), 'Voice/source unit mapping changed')
    require(isinstance(plan['cases'], list) and 0 < len(plan['cases']) <= 12, 'Expected one to twelve complete cases')
    keys = set()
    for case in plan['cases']:
        key, number = case['key'], case['unit_id']
        require(isinstance(key, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', key)
            and key not in keys, 'Preview keys must be unique safe names')
        keys.add(key)
        require(type(number) is int and 1 <= number <= len(source), 'Invalid source unit')
        start, end, english = source[number - 1]
        current_spoken = overrides.get(str(number), display[number])
        require(case['source'] == english and case['current_display'] == display[number]
            and case['current_spoken'] == current_spoken, 'Current complete source/translation pairing changed')
        require(case['source_start'] == str(start) and case['source_end'] == str(end), 'Original source window changed')
        require(Decimal('0') < Decimal(str(end)) - Decimal(str(start)) <= Decimal('40'),
            'Preview must keep a complete at-most40second source unit')
        require(voices[number - 1] == plan['voice'], 'Preview voice differs from this source unit')
        require(current_spoken.strip() not in {'', '…', '...', '♪'}
            and not current_spoken.lstrip().startswith('【原声保留'), 'Do not synthesize source-audio or event reservations')
        review = case['speaker_review']
        require(review['same_speaker'] is True and isinstance(review['evidence'], str) and review['evidence'].strip(),
            'An explicit source-speaker review is required, not inferred from punctuation')
        require(all(isinstance(case[name], str) and case[name].strip()
            for name in ['display', 'spoken', 'reason']), 'Candidate text and editorial reason must be explicit')
        require(isinstance(case['preserved_facts'], list) and case['preserved_facts'], 'List the retained semantic constraints')
        require(state.matches(case['before_waveform']), 'Captured before-waveform identity changed')
        with wave.open(case['before_waveform']['path'], 'rb') as audio:
            require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, SAMPLE_RATE),
                'Before waveform must be mono PCM16 at24kHz')
            available = int((Decimal(str(end)) - Decimal(str(start))) * SAMPLE_RATE)
            require(audio.getnframes() == available, 'Before waveform must cover the exact complete source-window length')
    return plan


def inspect_wave(path):
    with wave.open(str(path), 'rb') as audio:
        require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, SAMPLE_RATE),
            'Unexpected preview PCM format')
        samples = audio.getnframes()
        pcm = audio.readframes(samples)
    require(samples > 0 and len(pcm) == samples * 2, 'Incomplete preview PCM')
    return samples, hashlib.sha256(pcm).hexdigest()


async def preview(plan_path, output):
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    plan = validate_plan(plan_path)
    work = Path(plan['work'])
    require(not output.is_relative_to(work) and not work.is_relative_to(output),
        'Preview output must be separate from the formal project')
    require(not output.exists(), 'Preserve existing previews; inspect them rather than silently repeating synthesis')
    output.mkdir(parents=True)
    result = {'kind': KIND, 'created_at': state.now(), 'plan': state.identity(plan_path),
        'adapter': state.identity(__file__), 'synthesis_implementation': state.identity(speech_polish.__file__),
        'complete': False, 'cases': [], 'production_audio_or_text_modified': False,
        'human_listened': False, 'pronunciation_or_emotion_approved': False,
        'automatic_winner_or_database_decision': False, 'database_written': False,
        'extra_atempo_or_audio_trimming': False, 'strict_production_admission_proven': False,
        'before_clip_provenance': 'Caller-supplied identity and reference evidence; not independently recaptured here.'}
    try:
        for case in plan['cases']:
            proof = await speech_polish.synthesize_sample(case['spoken'], plan['voice'], output / 'cache')
            destination = output / f'{case["key"]}.wav'
            command = ['ffmpeg', '-nostdin', '-v', 'error', '-i', proof['audio']['path'],
                '-ac', '1', '-ar', str(SAMPLE_RATE), '-c:a', 'pcm_s16le', '-n', str(destination)]
            decoded = subprocess.run(command, capture_output=True, text=True)
            require(decoded.returncode == 0, f'Complete untrimmed decode failed: {decoded.stderr}')
            samples, digest = inspect_wave(destination)
            available = int((Decimal(case['source_end']) - Decimal(case['source_start'])) * SAMPLE_RATE)
            result['cases'].append({'key': case['key'], 'unit_id': case['unit_id'], 'proof': proof,
                'waveform': state.identity(destination), 'samples': samples, 'pcm_sha256': digest,
                'seconds': samples / SAMPLE_RATE, 'source_window_samples': available,
                'remaining_samples': available - samples, 'fits_original_window': samples <= available,
                'zero_margin': samples == available, 'audio_trimmed': False,
                'human_selected': False, 'automatic_winner': None})
            save_json(output / 'manifest.json', result)
        require(validate_plan(plan_path) == plan, 'Source dependencies changed during preview synthesis')
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
    require(result['kind'] == KIND and result['complete'] is True and result['plan'] == state.identity(plan_path)
        and result['adapter'] == state.identity(__file__)
        and result['synthesis_implementation'] == state.identity(speech_polish.__file__), 'Stale or incomplete preview')
    require(all(result[name] is False for name in ['production_audio_or_text_modified', 'human_listened',
        'pronunciation_or_emotion_approved', 'automatic_winner_or_database_decision', 'database_written',
        'extra_atempo_or_audio_trimming', 'strict_production_admission_proven']), 'Preview authority was expanded')
    require(len(result['cases']) == len(plan['cases']), 'Preview case inventory changed')
    for case, observed in zip(plan['cases'], result['cases']):
        require((case['key'], case['unit_id']) == (observed['key'], observed['unit_id']), 'Preview pairing changed')
        proof = observed['proof']
        require(proof['contract']['text'] == case['spoken'] and proof['contract']['voice'] == plan['voice']
            and proof['contract']['rate'] == 0 and proof['contract']['boundary'] == 'WordBoundary',
            'Preview synthesis contract changed')
        for bound in [proof['audio'], proof['metadata'], observed['waveform']]:
            require(state.matches(bound), 'Preview audio or real boundary metadata changed')
        require(Path(observed['waveform']['path']) == output / f'{case["key"]}.wav', 'Preview waveform route changed')
        samples, digest = inspect_wave(observed['waveform']['path'])
        available = int((Decimal(case['source_end']) - Decimal(case['source_start'])) * SAMPLE_RATE)
        require(observed['samples'] == samples and observed['pcm_sha256'] == digest
            and observed['seconds'] == samples / SAMPLE_RATE and observed['source_window_samples'] == available
            and observed['remaining_samples'] == available - samples
            and observed['fits_original_window'] == (samples <= available)
            and observed['zero_margin'] == (samples == available)
            and observed['audio_trimmed'] is False and observed['human_selected'] is False
            and observed['automatic_winner'] is None, 'Preview duration/fit or review authority changed')
        decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', proof['audio']['path'],
            '-f', 's16le', '-ac', '1', '-ar', str(SAMPLE_RATE), '-'], capture_output=True, check=True).stdout
        require(len(decoded) == samples * 2 and hashlib.sha256(decoded).hexdigest() == digest,
            'Preview is not the complete untrimmed MP3 decode')
        boundaries = [json.loads(line) for line in Path(proof['metadata']['path']).read_text().splitlines()]
        require(any(item['type'] == 'WordBoundary' for item in boundaries), 'No real word boundary observations')
    return {'passed': True, 'cases': len(result['cases']),
        'normal_rate_fits': sum(item['fits_original_window'] for item in result['cases']),
        'plan': result['plan'], 'manifest': state.identity(output / 'manifest.json'),
        'adapter': state.identity(__file__), 'human_listened': False, 'strict_production_admission_proven': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preview', 'check'])
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    plan = validate_plan(arguments.plan)
    with (Path(plan['work']) / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        result = (asyncio.run(preview(arguments.plan, arguments.output)) if arguments.action == 'preview'
            else check(arguments.plan, arguments.output))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
