"""Compile authored lexical utterance proposals without approving audio or production."""

import argparse
import csv
import fcntl
import re
from pathlib import Path

import dubbing_state as state
import prepare_videos
from media_workflow import caption_projection, native_audio


KIND = 'source-bound-utterance-draft-v1'
TONES = {'explain', 'question', 'contrast', 'caution', 'transition', 'self_deprecating', 'thanks'}
KINDS = {'language', 'unresolved_source', 'handoff_candidate', 'provider_marker'}
require = native_audio.require
load = native_audio.load


def recorded_identity(bound):
    require(isinstance(bound, dict) and {'path', 'bytes', 'sha256'} <= set(bound)
        <= {'path', 'bytes', 'sha256', 'mtime_ns'}, 'Expected an explicit source identity')
    current = state.identity(bound['path'])
    require(all(current[key] == value for key, value in bound.items()), 'Bound source changed')
    return current


def project(path):
    path = Path(path).resolve()
    plan = load(path)
    require(set(plan) == {'kind', 'reading', 'technical_review', 'evidence', 'entries'}
        and plan['kind'] == KIND, 'Unexpected utterance draft fields')
    reading_identity = recorded_identity(plan['reading'])
    review_identity = recorded_identity(plan['technical_review'])
    reading = load(reading_identity['path'])
    review = load(review_identity['path'])
    require(reading['production_admitted'] is False and review['tts_admission'] is False,
        'Only quarantined source reading is supported')
    source_identity = recorded_identity(reading['source'])
    work = Path(source_identity['path']).parent
    captions_path = work / 'en.srt'
    captions = caption_projection.read_srt(captions_path)
    with Path(source_identity['path']).open(newline='') as source:
        originals = [row for row in csv.reader(source, delimiter='\t') if row and row[0].isdigit()]
    rows = reading['rows']
    require(len(rows) == len(originals) == len(captions), 'Incomplete source row inventory')
    tokens, coordinates, row_starts = [], [], {}
    for number, (row, original, caption) in enumerate(zip(rows, originals, captions), 1):
        require(row['source_unit'] == number == int(original[0])
            and row['source_display_window'] == original[1]
            and row['source_english'] == original[2] == caption[2], 'Source row changed')
        row_starts[number] = len(tokens)
        words = row['source_english'].split()
        tokens.extend(words)
        coordinates.extend((number, offset) for offset in range(len(words)))
    require(len(tokens) == reading['source_word_count'], 'Source token count changed')
    require(isinstance(plan['evidence'], list) and len(plan['evidence']) <= 32, 'Bound evidence list required')
    evidence = [recorded_identity(bound) for bound in plan['evidence']]
    authored = plan['entries']
    require(isinstance(authored, list) and 1 <= len(authored) <= 2000, 'Use a bounded authored draft')
    entries, cursor, seen = [], 0, set()
    for item in authored:
        require(isinstance(item, dict) and {'id', 'end_source_unit', 'end_anchor', 'kind', 'zh', 'tone', 'note'}
            <= set(item) <= {'id', 'end_source_unit', 'end_anchor', 'kind', 'zh', 'tone', 'note', 'spoken_candidate'},
            'Unexpected authored entry fields')
        require(isinstance(item['id'], str) and re.fullmatch(r'[A-Za-z0-9-]{1,40}', item['id'])
            and item['id'] not in seen, 'Duplicate or unsafe entry identity')
        seen.add(item['id'])
        unit = item['end_source_unit']
        require(type(unit) is int and 1 <= unit <= len(rows), 'Invalid source end unit')
        require(isinstance(item['end_anchor'], str) and item['end_anchor'].strip(), 'Bind an exact ending anchor')
        words, anchor = rows[unit - 1]['source_english'].split(), item['end_anchor'].split()
        matches = [offset for offset in range(len(words) - len(anchor) + 1)
            if words[offset:offset + len(anchor)] == anchor]
        require(len(matches) == 1, 'Ending anchor must occur exactly once in its source row')
        end = row_starts[unit] + matches[0] + len(anchor)
        require(cursor < end, 'Entries must advance through the unchanged source token stream')
        owned = tokens[cursor:end]
        units = sorted({coordinate[0] for coordinate in coordinates[cursor:end]})
        require(item['kind'] in KINDS and item['tone'] in TONES, 'Unsupported textual kind or tone proposal')
        markers = [token for token in owned if re.fullmatch(r'\[[^\[\]]+\]', token)]
        if item['kind'] == 'provider_marker':
            require(len(owned) == len(markers) == 1 and item['zh'] is None
                and item.get('spoken_candidate') is None, 'Provider markers must be isolated, never voiced')
        else:
            require(not markers and isinstance(item['zh'], str) and item['zh'].strip()
                and not item['zh'].rstrip().endswith(('……', '...')), 'Supply complete prose, not a caption continuation')
        require(isinstance(item['note'], str) and item['note'].strip(), 'Explain editorial intent and limitations')
        holds = [hold['id'] for hold in review['source_text_holds'] if set(hold['units']).intersection(units)]
        findings = [finding['id'] for finding in review['technical_findings']
            if set(finding['units']).intersection(units)]
        candidate = item.get('spoken_candidate', item['zh'])
        require(candidate is None or isinstance(candidate, str) and candidate.strip(), 'Invalid speech-text proposal')
        if holds or item['kind'] != 'language':
            require(item.get('spoken_candidate') is None, 'Do not propose speech for held, unknown-event or handoff text')
            candidate = None
        envelope = [int(min(captions[number - 1][0] for number in units) * 1000),
            int(max(captions[number - 1][1] for number in units) * 1000)]
        entries.append({'id': item['id'], 'kind': item['kind'], 'tokens': [cursor, end - 1],
            'source_start': list(coordinates[cursor]), 'source_end_inclusive': list(coordinates[end - 1]),
            'source_units': units, 'source_text': ' '.join(owned), 'zh': item['zh'],
            'tone_proposal': item['tone'], 'editorial_note': item['note'],
            'spoken_candidate': candidate, 'spoken': None, 'source_hold_ids': holds,
            'technical_finding_ids': findings, 'speaker_id': 'unknown', 'same_speaker_verified': False,
            'source_caption_envelope_ms': envelope, 'envelope_is_not_an_audio_window': True,
            'caption_envelope_exceeds_40s': envelope[1] - envelope[0] > 40000,
            'acoustic_start_ms': None, 'acoustic_end_ms': None, 'tts_allowed': False})
        cursor = end
    require(' '.join(entry['source_text'] for entry in entries).split() == tokens[:cursor],
        'Source words were omitted, duplicated or reordered')
    covered = sorted({unit for entry in entries for unit in entry['source_units']})
    complete = [number for number, start in row_starts.items()
        if start + len(rows[number - 1]['source_english'].split()) <= cursor]
    result = {'kind': 'quarantined-anchored-utterance-reading-v1', 'video_id': reading['video_id'],
        'source_tokens_total': len(tokens), 'source_tokens_covered': cursor,
        'remaining_source_tokens': len(tokens) - cursor, 'covered_source_units': covered,
        'fully_covered_source_units': complete, 'remaining_source_units': len(rows) - len(complete),
        'next_source_coordinate': list(coordinates[cursor]) if cursor < len(tokens) else None,
        'entries': entries, 'all_source_holds_retained': review['source_text_holds'],
        'all_technical_findings_retained': review['technical_findings'],
        'original_partial_markers_retained': reading['partial_text_units'],
        'production_admission': False, 'canonical_translation_installed': False,
        'source_roles_verified': False, 'human_listened': False,
        'pronunciation_or_emotion_approved': False, 'source_english_or_times_modified': False,
        'full_talk_translation_complete': False}
    bindings = {'plan': state.identity(path), 'reading': reading_identity, 'technical_review': review_identity,
        'source_tsv': source_identity, 'source_srt': state.identity(captions_path), 'evidence': evidence,
        'implementations': {name: state.identity(module.__file__) for name, module in (
            ('caption_projection', caption_projection), ('native_audio', native_audio),
            ('dubbing_state', state), ('prepare_videos', prepare_videos))},
        'adapter': state.identity(__file__)}
    return work, result, bindings


def report(output, reading):
    return {'kind': KIND, 'entries': len(reading['entries']),
        'source_tokens_covered': reading['source_tokens_covered'],
        'remaining_source_tokens': reading['remaining_source_tokens'],
        'fully_covered_source_units': len(reading['fully_covered_source_units']),
        'remaining_source_units': reading['remaining_source_units'],
        'provider_markers': sum(entry['kind'] == 'provider_marker' for entry in reading['entries']),
        'speech_text_proposals': sum(entry['spoken_candidate'] is not None for entry in reading['entries']),
        'caption_envelopes_over_40s': sum(entry['caption_envelope_exceeds_40s'] for entry in reading['entries']),
        'bindings': state.identity(output / 'bindings.json'), 'reading': state.identity(output / 'reading.json'),
        'production_admission': False, 'human_or_source_speaker_approval': False}


def execute(action, path, output=None):
    work, reading, bindings = project(path)
    if action == 'validate':
        return {'valid': True, 'entries': len(reading['entries']),
            'covered_tokens': reading['source_tokens_covered'], 'remaining_tokens': reading['remaining_source_tokens']}
    require(output is not None, 'Build/check requires a separate output directory')
    output = Path(output).resolve()
    require(not output.is_relative_to(work) and not work.is_relative_to(output), 'Never write to the production project')
    with (work / '.workflow.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        require(project(path)[1:] == (reading, bindings), 'Inputs changed before locking')
        if action == 'check':
            require(load(output / 'bindings.json') == bindings and load(output / 'reading.json') == reading,
                'Draft, dependencies or bound inputs changed')
            result = report(output, reading)
            require(load(output / 'verification.json') == result, 'Draft verification changed')
            return result
        require(not output.exists(), 'Never overwrite or silently rebuild an existing draft')
        output.mkdir(parents=True)
        prepare_videos.save_json(output / 'bindings.json', bindings)
        prepare_videos.save_json(output / 'reading.json', reading)
        result = report(output, reading)
        prepare_videos.save_json(output / 'verification.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate', 'build', 'check'])
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    import json
    print(json.dumps(execute(arguments.action, arguments.plan, arguments.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
