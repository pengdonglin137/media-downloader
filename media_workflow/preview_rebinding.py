"""Rebind preserved complete previews after a verified disjoint formal adoption."""

import argparse
import copy
import hashlib
import json
import shutil
import wave
from decimal import Decimal
from pathlib import Path
from types import FunctionType

import dubbing_state as state
from prepare_videos import save_json
from . import speech_variants
from .speech_variants import require


KIND = 'verified-disjoint-complete-preview-rebinding-v1'
ROOT = Path(__file__).resolve().parents[1]
RATE = speech_variants.SAMPLE_RATE
FROZEN_VALIDATE = speech_variants.validate_plan
FROZEN_CHECK = speech_variants.check


def selected_cases_unchanged(original, current):
    require(isinstance(original, list) and original and len(original) == len(current),
        'Require the complete original selected-case inventory')
    seen = set()
    for before, after in zip(original, current):
        require(type(before['unit_id']) is int and before['unit_id'] > 0 and before['unit_id'] not in seen,
            'Duplicate or invalid complete source unit')
        seen.add(before['unit_id'])
        require(set(before) == set(after) and all(before[name] == after[name]
            for name in before if name != 'before_waveform'),
            'Rebinding cannot change selected source, translation, candidate, voice, role or time')


class ArchivedPreviewState:
    def __init__(self, aliases):
        self.aliases = aliases
        self.matches_used = set()
        self.loads_used = set()

    def __getattr__(self, name):
        return getattr(state, name)

    def route(self, path):
        selected = self.aliases.get(str(Path(path).resolve()))
        return Path(selected['archive']['path']) if selected else path

    def matches(self, bound):
        selected = self.aliases.get(bound['path'])
        if selected is None:
            return state.matches(bound)
        require(bound == selected['original'], 'Reject another identity at a historical preview alias')
        self.matches_used.add(bound['path'])
        return state.matches(selected['archive'])

    def load(self, path):
        if str(Path(path).resolve()) in self.aliases:
            self.loads_used.add(str(Path(path).resolve()))
        return state.load(self.route(path))

    def cues(self, path):
        return state.cues(self.route(path))

    def translations(self, path, count):
        return speech_variants.translations(self.route(path), count)


def historical_check(plan_path, manifest_path, aliases):
    require(FROZEN_VALIDATE is speech_variants.validate_plan and FROZEN_CHECK is speech_variants.check
        and FROZEN_VALIDATE.__globals__['state'] is state and FROZEN_CHECK.__globals__['state'] is state
        and FROZEN_VALIDATE.__closure__ is None and FROZEN_CHECK.__closure__ is None,
        'Reject foreign preview checkers or global state hooks')
    proxy = ArchivedPreviewState(aliases)
    namespace = {**FROZEN_VALIDATE.__globals__, 'state': proxy, 'translations': proxy.translations}
    validate = FunctionType(FROZEN_VALIDATE.__code__, namespace, FROZEN_VALIDATE.__name__, FROZEN_VALIDATE.__defaults__)
    namespace = {**FROZEN_CHECK.__globals__, 'state': proxy, 'validate_plan': validate}
    checker = FunctionType(FROZEN_CHECK.__code__, namespace, FROZEN_CHECK.__name__, FROZEN_CHECK.__defaults__)
    result = checker(plan_path, Path(manifest_path).parent)
    require(proxy.matches_used == set(aliases), 'Historical native identity guards were not all exercised')
    return {'native_result': result, 'exact_archived_aliases': aliases,
        'native_identity_guards_exercised': sorted(proxy.matches_used),
        'archived_loads': sorted(proxy.loads_used), 'global_state_hooks_installed': False}


def configuration(path):
    config = state.load(path)
    require(set(config) == {'kind', 'source_plan', 'source_manifest', 'archive_preparation', 'archive_terminal',
        'current_package', 'current_media_verification', 'current_installation_verification',
        'additional_evidence', 'reason'} and config['kind'] == KIND,
        'Unsupported or incomplete preserved-preview rebinding contract')
    require(isinstance(config['reason'], str) and config['reason'].strip()
        and isinstance(config['additional_evidence'], list) and 1 <= len(config['additional_evidence']) <= 12,
        'Require explicit current source authority and a rebinding reason')
    bindings = [config[name] for name in ('source_plan', 'source_manifest', 'archive_preparation', 'archive_terminal',
        'current_package', 'current_media_verification', 'current_installation_verification')]
    bindings += config['additional_evidence']
    require(all(state.matches(bound) for bound in bindings)
        and len({bound['path'] for bound in bindings}) == len(bindings), 'Stale or duplicate rebinding input')
    plan = state.load(config['source_plan']['path'])
    observed = state.load(config['source_manifest']['path'])
    require(observed['plan'] == config['source_plan'] and observed['complete'] is True,
        'Historical complete preview belongs to another plan')
    work = Path(plan['work']).resolve()
    require(str(work) == plan['work'] and all(Path(bound['path']) == work / name for name, bound in plan['inputs'].items()),
        'Historical inputs do not belong to the canonical project')
    prepared = state.load(config['archive_preparation']['path'])
    terminal = state.load(config['archive_terminal']['path'])
    installed = state.load(config['current_installation_verification']['path'])
    require(terminal['state'] == 'committed' and installed['passed'] is True
        and installed['transaction'] == config['archive_terminal']
        and installed['transaction_state'] == 'committed'
        and all(state.matches(bound) for bound in installed['canonical_files_after']),
        'Require the exact current successful transactional adoption')
    directory = Path(config['archive_preparation']['path']).parent
    require(Path(config['archive_preparation']['path']) == directory / 'prepared.json'
        and Path(config['archive_terminal']['path']) == directory / 'transaction.json'
        and terminal['verification']['path'] == str(directory.parent / 'installation-verification.json')
        and state.matches(terminal['verification']), 'Wrong or stale transaction archive route')
    targets = {entry['destination']: entry for entry in prepared['files'].values()}
    require(len(targets) == len(prepared['files']), 'Ambiguous historical archive targets')
    aliases = {}
    for bound in [*plan['inputs'].values(), plan['master']]:
        if state.matches(bound):
            continue
        require(bound['path'] in targets and targets[bound['path']]['original'] == bound,
            'Stale historical preview input has no exact transaction archive')
        entry = targets[bound['path']]
        require(state.matches(entry['archive']) and all(entry['archive'][name] == bound[name]
            for name in ('bytes', 'mtime_ns', 'sha256')), 'Historical archive differs from its exact original identity')
        aliases[bound['path']] = {'original': bound, 'archive': entry['archive']}
    require(aliases and all(state.matches(plan['inputs'][name]) for name in ('tts_voice.txt', 'voices.json')),
        'Rebinding requires actual stale inputs and unchanged production voice mapping')
    package = state.load(config['current_package']['path'])
    media = state.load(config['current_media_verification']['path'])
    require(media['passed'] is True and installed['candidate_verification'] == config['current_media_verification']
        and package['upstream_package'] == media['package']
        and package['output'] == installed['master'] and package['upload_output'] == installed['upload']
        and all(state.matches(package[name]) for name in ('output', 'upload_output'))
        and state.load(work / 'render_state.json')['output'] == package['output']
        and media['master_upload_chinese_pcm_equal'] is True,
        'Current media/installed master/decoded Chinese authority differs')
    pcm = [bound for bound in media['decoded_pcm'] if Path(bound['path']).name == 'master-chinese.s16le']
    require(len(pcm) == 1 and state.matches(pcm[0]) and pcm[0]['bytes'] % 2 == 0,
        'Require the actual complete current-master mono24k PCM reference')
    offset = Decimal(str(package['body_offset_seconds'])) * RATE
    require(offset.is_finite() and offset >= 0 and offset == offset.to_integral_value(), 'Use the real exact intro sample offset')
    return config, plan, observed, aliases, pcm[0], int(offset)


def current_plan(config_path, config, original, output):
    plan = copy.deepcopy(original)
    work = Path(plan['work'])
    plan['inputs'] = {name: state.identity(work / name) for name in speech_variants.INPUTS}
    plan['master'] = state.load(work / 'render_state.json')['output']
    plan['reference_evidence'].extend([*config['additional_evidence'], state.identity(config_path), state.identity(__file__)])
    plan['current_binding_rebase'] = {'kind': KIND, 'configuration': state.identity(config_path),
        'historical_source_plan': config['source_plan'], 'historical_source_manifest': config['source_manifest'],
        'new_synthesis_or_candidate_edit': False, 'current_before_clips_refreshed': True}
    for case in plan['cases']:
        case['before_waveform'] = state.identity(output / 'before' / (case['key'] + '.wav'))
    selected_cases_unchanged(original['cases'], plan['cases'])
    return plan


def before_payload(case, pcm, offset):
    first, last = [Decimal(case[name]) * RATE for name in ('source_start', 'source_end')]
    require(first.is_finite() and last.is_finite() and 0 <= first < last
        and all(value == value.to_integral_value() for value in (first, last))
        and last - first <= 40 * RATE and offset + last <= pcm['bytes'] // 2,
        'Invalid, nonintegral or out-of-reference complete source window')
    with Path(pcm['path']).open('rb') as stream:
        stream.seek((offset + int(first)) * 2)
        payload = stream.read(int(last - first) * 2)
    require(len(payload) == int(last - first) * 2, 'Truncated current complete before clip')
    return payload


def rebind(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config, original, observed, aliases, pcm, offset = configuration(config_path)
    require(not output.exists() and ROOT not in (output, *output.parents)
        and all(not output.is_relative_to(parent) and not parent.is_relative_to(output)
            for parent in (Path(original['work']), Path(config['source_manifest']['path']).parent)),
        'Use a fresh external rebinding directory outside original and canonical projects')
    history = historical_check(config['source_plan']['path'], config['source_manifest']['path'], aliases)
    output.mkdir(parents=True)
    (output / 'before').mkdir()
    for case in original['cases']:
        with wave.open(str(output / 'before' / (case['key'] + '.wav')), 'wb') as audio:
            audio.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            audio.writeframes(before_payload(case, pcm, offset))
    save_json(output / 'plan.json', current_plan(config_path, config, original, output))
    result = copy.deepcopy(observed)
    result['plan'] = state.identity(output / 'plan.json')
    for case in result['cases']:
        destination = output / (case['key'] + '.wav')
        shutil.copy2(case['waveform']['path'], destination)
        case['waveform'] = state.identity(destination)
    result['preview_rebinding'] = {'kind': KIND, 'producer': state.identity(__file__),
        'config': state.identity(config_path), 'historical_check': history, 'current_decoded_pcm': pcm,
        'intro_sample_offset': offset, 'rebound_at': state.now(), 'new_synthesis_or_candidate_edit': False,
        'adapter_retains_actual_original_synthesis_identity': True, 'current_before_clips_refreshed': True}
    save_json(output / 'manifest.json', result)
    return check(config_path, output)


def check(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    config, original, observed, aliases, pcm, offset = configuration(config_path)
    require(state.load(output / 'plan.json') == current_plan(config_path, config, original, output), 'Rebound plan changed')
    result = state.load(output / 'manifest.json')
    provenance = result['preview_rebinding']
    history = historical_check(config['source_plan']['path'], config['source_manifest']['path'], aliases)
    require(isinstance(provenance['rebound_at'], str) and provenance['rebound_at'], 'Missing actual rebinding time')
    require(provenance == {'kind': KIND, 'producer': state.identity(__file__), 'config': state.identity(config_path),
        'historical_check': history, 'current_decoded_pcm': pcm, 'intro_sample_offset': offset,
        'rebound_at': provenance['rebound_at'], 'new_synthesis_or_candidate_edit': False,
        'adapter_retains_actual_original_synthesis_identity': True, 'current_before_clips_refreshed': True},
        'Rebinding provenance or authority changed')
    expected = copy.deepcopy(observed)
    expected['plan'] = state.identity(output / 'plan.json')
    for case in expected['cases']:
        copied = state.identity(output / (case['key'] + '.wav'))
        require(all(copied[name] == case['waveform'][name] for name in ('bytes', 'mtime_ns', 'sha256')),
            'Rebound candidate is not an exact complete preserved waveform')
        case['waveform'] = copied
    expected['preview_rebinding'] = provenance
    require(result == expected, 'Historical observations, proof, text, complete audio or authority changed')
    comparisons = []
    for case in original['cases']:
        path = output / 'before' / (case['key'] + '.wav')
        with wave.open(str(path), 'rb') as audio:
            require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, RATE),
                'Wrong current before-clip format')
            payload = audio.readframes(audio.getnframes())
        require(payload == before_payload(case, pcm, offset), 'Before clip differs from the exact current AAC decode window')
        with wave.open(case['before_waveform']['path'], 'rb') as old_audio:
            old_payload = old_audio.readframes(old_audio.getnframes())
        comparisons.append({'unit_id': case['unit_id'], 'current_before_clip': state.identity(path),
            'PCM_sha256': hashlib.sha256(payload).hexdigest(), 'old_before_PCM_equal_observed': payload == old_payload})
    native = speech_variants.check(output / 'plan.json', output)
    return {'passed': True, 'kind': KIND, 'config': state.identity(config_path),
        'plan': state.identity(output / 'plan.json'), 'manifest': state.identity(output / 'manifest.json'),
        'cases': native['cases'], 'normal_rate_fits': native['normal_rate_fits'],
        'historical_native_check': history, 'current_native_check': native, 'current_before_clips': comparisons,
        'new_TTS_ASR_or_media_encoding': False, 'canonical_modified': False, 'formal_adoption': False,
        'human_listened_or_pronunciation_emotion_approved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('rebind', 'check'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps((rebind if arguments.action == 'rebind' else check)(arguments.config, arguments.output),
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
