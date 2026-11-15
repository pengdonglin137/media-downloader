"""Preview bounded pitch controls on unchanged complete utterances, without approval."""

import argparse
import asyncio
import fcntl
import hashlib
import importlib.metadata
import inspect
import json
import subprocess
from decimal import Decimal
from pathlib import Path

import dubbing_state as state
from paired_delivery_transaction import durable_json
from . import speech_variants as variants
from .speech_variants import require


KIND = 'unchanged-complete-utterance-pitch-preview-v1'
PITCHES = {'-5Hz', '+5Hz'}
LIMITATIONS = ('production_modified', 'human_listened', 'emotion_or_phoneme_approved',
    'automatic_winner', 'database_written', 'audio_trimmed_or_time_stretched',
    'strict_production_admission_proven')


def engine_observation():
    import edge_tts
    directory = Path(edge_tts.__file__).resolve().parent
    return {'version': importlib.metadata.version('edge-tts'),
        'communicate_signature': str(inspect.signature(edge_tts.Communicate)),
        'sources': [state.identity(path) for path in sorted(directory.rglob('*.py'))]}


def configuration(path):
    profile = state.load(path)
    require(set(profile) == {'kind', 'adapter', 'native_plan', 'engine', 'controls'}
        and profile['kind'] == KIND and profile['adapter'] == state.identity(__file__), 'Unsupported pitch profile')
    require(state.matches(profile['native_plan']) and profile['engine'] == engine_observation(),
        'Pitch plan or installed engine changed')
    plan = variants.validate_plan(profile['native_plan']['path'])
    controls = profile['controls']
    require(isinstance(controls, list) and len(controls) == len(plan['cases']) <= 4, 'Unexpected pitch inventory')
    used = set()
    for case, control in zip(plan['cases'], controls):
        require(isinstance(control, dict) and set(control) == {'key', 'pitch'}
            and control['key'] == case['key'] and control['pitch'] in PITCHES, 'Unsupported pitch control')
        require(case['display'] == case['current_display'] and case['spoken'] == case['current_spoken'],
            'Pitch comparison must not change wording or punctuation')
        require((case['unit_id'], control['pitch']) not in used, 'Duplicate pitch trial for one complete unit')
        used.add((case['unit_id'], control['pitch']))
    return profile, plan


def lexical(text):
    return ''.join(character for character in text if character.isalnum()).casefold()


def boundaries(path, text):
    entries = [json.loads(line) for line in Path(path).read_text().splitlines()]
    words = [entry for entry in entries if entry.get('type') == 'WordBoundary']
    require(words and lexical(''.join(entry['text'] for entry in words)) == lexical(text),
        'Returned word metadata does not cover the unchanged complete text')
    previous = -1
    for entry in words:
        require(type(entry['offset']) is int and type(entry['duration']) is int
            and entry['offset'] >= previous and entry['duration'] > 0, 'Invalid returned word timing')
        previous = entry['offset']
    return {'count': len(words), 'lexical_text': lexical(text),
        'timing_authority': 'returned-engine-metadata-not-ASR-or-human-phonemes'}


async def request_speech(text, voice, pitch, audio, metadata):
    import edge_tts
    await asyncio.wait_for(edge_tts.Communicate(text, voice, rate='+0%', volume='+0%',
        pitch=pitch, boundary='WordBoundary').save(str(audio), str(metadata)), timeout=60)


def contract(case, voice, pitch, engine):
    return {'text': case['spoken'], 'voice': voice, 'pitch': pitch, 'rate': '+0%',
        'volume': '+0%', 'boundary': 'WordBoundary', 'engine': engine}


def inspect_case(case, control, observed, plan, output, engine):
    key = case['key']
    require(observed['key'] == key and observed['unit_id'] == case['unit_id']
        and observed['contract'] == contract(case, plan['voice'], control['pitch'], engine), 'Pitch case pairing changed')
    directory = output / key / f'attempt-{observed["attempt"]:03d}'
    for name, filename in (('audio', 'speech.mp3'), ('metadata', 'boundaries.jsonl'), ('waveform', 'speech.wav')):
        require(Path(observed[name]['path']) == directory / filename and state.matches(observed[name]),
            'Pitch artifact path or identity changed')
    samples, digest = variants.inspect_wave(directory / 'speech.wav')
    available = int((Decimal(case['source_end']) - Decimal(case['source_start'])) * variants.SAMPLE_RATE)
    require(observed['samples'] == samples and observed['pcm_sha256'] == digest
        and observed['source_window_samples'] == available and observed['remaining_samples'] == available - samples
        and observed['fits_original_window'] == (samples <= available), 'Pitch duration or original window changed')
    require(observed['returned_boundaries'] == boundaries(directory / 'boundaries.jsonl', case['spoken']),
        'Returned pitch boundary observations changed')
    decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(directory / 'speech.mp3'),
        '-f', 's16le', '-ac', '1', '-ar', str(variants.SAMPLE_RATE), '-'], capture_output=True, check=True).stdout
    require(len(decoded) == samples * 2 and hashlib.sha256(decoded).hexdigest() == digest,
        'Pitch waveform is not the complete untrimmed MP3 decode')


def inspect_manifest(profile_path, output, complete):
    profile, plan = configuration(profile_path)
    manifest = state.load(output / 'manifest.json')
    require(manifest['kind'] == KIND and manifest['profile'] == state.identity(profile_path)
        and manifest['adapter'] == state.identity(__file__) and manifest['engine'] == profile['engine'],
        'Pitch manifest belongs to another profile or implementation')
    require(all(manifest[name] is False for name in LIMITATIONS), 'Pitch preview authority was expanded')
    require(type(manifest['complete']) is bool and manifest['complete'] is complete, 'Unexpected pitch terminal state')
    require(len(manifest['cases']) <= len(plan['cases']), 'Unexpected pitch case count')
    for case, control, observed in zip(plan['cases'], profile['controls'], manifest['cases']):
        inspect_case(case, control, observed, plan, output, profile['engine'])
    if complete:
        require(len(manifest['cases']) == len(plan['cases']) and manifest['attempts'][-1]['status'] == 'complete',
            'Pitch inventory or successful terminal attempt is missing')
    return profile, plan, manifest


async def render(profile_path, output, resume=False):
    profile_path, output = Path(profile_path).resolve(), Path(output).resolve()
    profile, plan = configuration(profile_path)
    work = Path(plan['work'])
    require(not output.is_relative_to(work) and not work.is_relative_to(output), 'Keep pitch previews outside production')
    if resume:
        require(output.is_dir(), 'No partial pitch output to resume')
        _, _, manifest = inspect_manifest(profile_path, output, False)
        require(manifest['attempts'][-1]['status'] == 'failed', 'Do not resume a running or successful attempt')
    else:
        require(not output.exists(), 'Preserve prior pitch output; use check or explicit resume')
        output.mkdir(parents=True)
        manifest = {'kind': KIND, 'profile': state.identity(profile_path), 'adapter': state.identity(__file__),
            'engine': profile['engine'], 'complete': False, 'cases': [], 'attempts': [],
            **{name: False for name in LIMITATIONS}}
    attempt = len(manifest['attempts']) + 1
    manifest['attempts'].append({'attempt': attempt, 'operation': 'resume' if resume else 'preview',
        'started_at': state.now(), 'status': 'running'})
    durable_json(output / 'manifest.json', manifest)
    try:
        for case, control in list(zip(plan['cases'], profile['controls']))[len(manifest['cases']):]:
            directory = output / case['key'] / f'attempt-{attempt:03d}'
            directory.mkdir(parents=True)
            audio, metadata, waveform = [directory / filename for filename in ('speech.mp3', 'boundaries.jsonl', 'speech.wav')]
            await request_speech(case['spoken'], plan['voice'], control['pitch'], audio, metadata)
            require(audio.stat().st_size >= 200, 'Pitch request returned no usable MP3')
            word_observations = boundaries(metadata, case['spoken'])
            decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(audio),
                '-ac', '1', '-ar', str(variants.SAMPLE_RATE), '-c:a', 'pcm_s16le', '-n', str(waveform)],
                capture_output=True, text=True)
            require(decoded.returncode == 0, 'Complete pitch decode failed: ' + decoded.stderr)
            samples, digest = variants.inspect_wave(waveform)
            available = int((Decimal(case['source_end']) - Decimal(case['source_start'])) * variants.SAMPLE_RATE)
            observed = {'key': case['key'], 'unit_id': case['unit_id'], 'attempt': attempt,
                'contract': contract(case, plan['voice'], control['pitch'], profile['engine']),
                'audio': state.identity(audio), 'metadata': state.identity(metadata), 'waveform': state.identity(waveform),
                'samples': samples, 'pcm_sha256': digest, 'source_window_samples': available,
                'remaining_samples': available - samples, 'fits_original_window': samples <= available,
                'returned_boundaries': word_observations}
            inspect_case(case, control, observed, plan, output, profile['engine'])
            manifest['cases'].append(observed)
            durable_json(output / 'manifest.json', manifest)
        require(configuration(profile_path)[0] == profile, 'Pitch inputs changed during synthesis')
        manifest['complete'] = True
        manifest['attempts'][-1].update(status='complete', ended_at=state.now())
        durable_json(output / 'manifest.json', manifest)
    except BaseException as error:
        manifest['complete'] = False
        manifest['attempts'][-1].update(status='failed', ended_at=state.now(),
            exception_type=type(error).__name__, error=str(error))
        durable_json(output / 'manifest.json', manifest)
        raise
    return check(profile_path, output)


def check(profile_path, output):
    output = Path(output).resolve()
    _, _, manifest = inspect_manifest(profile_path, output, True)
    return {'passed': True, 'cases': len(manifest['cases']),
        'normal_rate_fits': sum(case['fits_original_window'] for case in manifest['cases']),
        'manifest': state.identity(output / 'manifest.json'), 'human_listened': False,
        'emotion_or_phoneme_approved': False, 'production_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('preview', 'resume', 'check'))
    parser.add_argument('profile', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    _, plan = configuration(arguments.profile)
    with (Path(plan['work']) / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        result = (check(arguments.profile, arguments.output) if arguments.operation == 'check'
            else asyncio.run(render(arguments.profile, arguments.output, arguments.operation == 'resume')))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
