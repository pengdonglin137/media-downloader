"""Mux complete source media with quarantined textual-review subtitle tracks."""

import argparse
import fcntl
import json
import re
import shutil
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import dubbing_state as state
import prepare_videos
from media_workflow import caption_projection, native_audio, review_text_layout


KIND = 'complete-source-subtitle-review-v1'
ROOT = Path(__file__).resolve().parents[1]
require = native_audio.require
load = native_audio.load
UNAPPROVED = ('production_installed', 'tts_admitted', 'source_roles_verified',
    'acoustic_boundaries_verified', 'human_listened', 'pronunciation_or_emotion_approved',
    'full_film_visual_or_player_approved', 'final_master_upload_pair_complete')
STREAM_FIELDS = ('codec_type', 'codec_name', 'codec_tag_string', 'profile', 'level',
    'width', 'height', 'sample_aspect_ratio', 'sample_rate', 'channels', 'channel_layout',
    'time_base', 'start_pts', 'duration_ts', 'nb_frames', 'extradata_hash')


def binding(plan_path):
    plan = load(plan_path)
    required = {'kind', 'source', 'reading', 'english_subtitles', 'technical_review', 'evidence'}
    require(isinstance(plan, dict) and required <= set(plan) <= required | {'correction_overrides', 'protected_terms'}
        and plan['kind'] == KIND,
        'Expected a complete-source textual-review plan')
    require(isinstance(plan['evidence'], list) and 1 <= len(plan['evidence']) <= 32,
        'Bind the existing source/textual review evidence')
    for record in [plan[name] for name in ('source', 'reading', 'english_subtitles',
            'technical_review')] + plan['evidence']:
        require(isinstance(record, dict) and state.matches(record), 'A bound input changed')
    overrides = plan.get('correction_overrides', {})
    require(isinstance(overrides, dict), 'Correction overrides must be explicitly keyed')
    for correction in overrides.values():
        require(isinstance(correction, dict) and set(correction) == {'text', 'reason', 'evidence'}
            and isinstance(correction['reason'], str) and correction['reason'].strip()
            and state.matches(correction['evidence']), 'Bind and explain each authored correction override')
        single_line(correction['text'])
    review_text_layout.wrap('文字审校', plan.get('protected_terms', []))
    tools = {}
    for name in ('ffmpeg', 'ffprobe'):
        executable = shutil.which(name)
        require(executable is not None, f'Missing {name}')
        tools[name] = state.identity(executable)
    return plan, {'plan': state.identity(plan_path), 'tools': tools, 'implementations': {
        name: state.identity(path) for name, path in [('source_subtitle_review', __file__),
        ('caption_projection', caption_projection.__file__), ('native_audio', native_audio.__file__),
        ('review_text_layout', review_text_layout.__file__),
        ('dubbing_state', state.__file__), ('prepare_videos', prepare_videos.__file__)]}}


def single_line(text):
    require(isinstance(text, str) and text and all(ord(character) >= 32 for character in text)
        and not any(character in text for character in '<\\'), 'Unsafe or empty display text')
    return text


def sweep(records, heading):
    edges = sorted({bound for record in records for bound in record['window_ms']})
    cues = []
    for start, end in zip(edges, edges[1:]):
        active = [record for record in records if record['window_ms'][0] <= start
            and record['window_ms'][1] >= end]
        if not active:
            continue
        text = heading + '\n' + '\n'.join(record['text'] for record in active)
        require(len(text.encode('utf-8')) <= 30000, 'A simultaneous review cue is too large')
        if cues and cues[-1][1] == start and cues[-1][2] == text:
            cues[-1][1] = end
        else:
            cues.append([start, end, text])
    require(cues, 'No complete textual-review cues')
    return [(Decimal(start) / 1000, Decimal(end) / 1000, text) for start, end, text in cues]


def projection(plan):
    english = caption_projection.read_srt(plan['english_subtitles']['path'])
    require(len(english) <= 2000, 'Too many source caption blocks for this bounded adapter')
    reading = load(plan['reading']['path'])
    require(reading.get('kind') == 'cumulative-textual-review-proposal-not-production-v1'
        and reading.get('production_admission') is False
        and reading.get('remaining_source_units') == 0
        and reading.get('source_units') == [1, len(english)], 'Expected the complete quarantined reading')
    entries = reading['entries']
    require(isinstance(entries, list) and 1 <= len(entries) <= 4000, 'Invalid textual entry inventory')
    source_words = ' '.join(text for start, end, text in english).split()
    require(' '.join(entry['source_text'] for entry in entries).split() == source_words,
        'Reading lost or reordered source words or provider markers')
    rows, notes, coverage, seen, token_cursor = [], [], set(), set(), 1
    for entry in entries:
        entry_id = entry['id']
        require(isinstance(entry_id, str) and re.fullmatch(r'[A-Za-z0-9-]{1,40}', entry_id)
            and entry_id not in seen, 'Duplicate or unsafe textual entry ID')
        seen.add(entry_id)
        require(entry.get('speaker_id') == 'unknown' and entry.get('same_speaker_verified') is False
            and entry.get('tts_allowed') is False and entry.get('acoustic_end_ms') is None
            and entry.get('envelope_is_not_an_audio_window') is True,
            'Do not promote textual candidates to speaker, acoustic or TTS authority')
        units = entry['source_units']
        require(isinstance(units, list) and units and all(type(unit) is int
            and 1 <= unit <= len(english) for unit in units), 'Invalid source-unit ownership')
        coverage.update(units)
        window = [int(min(english[unit - 1][0] for unit in units) * 1000),
            int(max(english[unit - 1][1] for unit in units) * 1000)]
        require(entry['source_caption_envelope_ms'] == window, 'Caption envelope does not match original SRT')
        token_end = token_cursor + len(entry['source_text'].split()) - 1
        require(entry['tokens'] == [token_cursor, token_end], 'Source-token ownership changed')
        token_cursor = token_end + 1
        if entry.get('kind') == 'provider_marker':
            require(entry['zh'] is None and entry['spoken'] is None, 'Provider markers cannot be narration')
            text = f'〔{entry_id} 来源标记；不代表换人或静音〕'
        else:
            text = f'〔{entry_id}{" 文字待核" if entry["spoken"] is None else ""}〕{single_line(entry["zh"])}'
        rows.append({'id': entry_id, 'window_ms': window, 'text': text})
        if entry['spoken'] is None:
            gate = entry.get('gate', '供应商标记；不代表声学换人、静音或TTS批准。') if entry.get('kind') == 'provider_marker' else entry['gate']
            notes.append({'window_ms': window, 'text': f'〔{entry_id} 待核〕{single_line(gate)}'})
    require(coverage == set(range(1, len(english) + 1)), 'Some source caption blocks are not represented')
    technical = load(plan['technical_review']['path'])
    require(technical.get('production_blocked') is True and technical.get('canonical_translation_installed') is False,
        'Technical holds must remain in force')
    fact_ids = []
    for case in technical['cases']:
        require(re.fullmatch(r'F[0-9]{2}', case['id']) and case['id'] not in fact_ids,
            'Duplicate or unsafe factual-review ID')
        fact_ids.append(case['id'])
        correction = single_line(plan.get('correction_overrides', {}).get(case['id'], {}).get(
            'text', case['ai_correction_candidate']))
        for unit in case['source_units']:
            require(type(unit) is int and 1 <= unit <= len(english), 'Invalid factual source-unit reference')
            start, end, text = english[unit - 1]
            note = {'window_ms': [int(start * 1000), int(end * 1000)],
                'text': f'〔AI事实审校 {case["id"]}／非讲者原话〕{correction}'}
            notes.append(note)
            if case.get('severity') == 'critical':
                rows.append(note)
    require(set(plan.get('correction_overrides', {})) <= set(fact_ids), 'Unknown factual correction override')
    chinese = sweep(rows, '【文字审校候选｜原声未译配｜时间仅旧字幕外包｜技术事实未放行】')
    review_notes = sweep(notes, '【AI审校说明／非讲者原话｜不构成角色、听辨或生产批准】')
    protected = plan.get('protected_terms', [])
    chinese = [(start, end, '\n'.join(review_text_layout.wrap(paragraph, protected) for paragraph in text.split('\n')))
        for start, end, text in chinese]
    review_notes = [(start, end, '\n'.join(review_text_layout.wrap(paragraph, protected) for paragraph in text.split('\n')))
        for start, end, text in review_notes]
    return {'zh': chinese, 'en': english, 'notes': review_notes}, {
        'source_caption_blocks': len(english), 'entries': len(entries), 'source_tokens': len(source_words),
        'provider_markers': sum(entry.get('kind') == 'provider_marker' for entry in entries),
        'textual_holds': sum(entry.get('kind') != 'provider_marker' and entry['spoken'] is None for entry in entries),
        'factual_review_ids': fact_ids, 'source_audio_between_or_outside_captions_not_removed': True,
        'critical_corrections_visible_on_default_Chinese_track': True,
        'explicit_review_line_width_cells': 100, 'protected_terms': protected,
        'display_windows_are_caption_envelopes_not_acoustic_boundaries': True,
        'overlap_policy': 'Display every simultaneous candidate; no inferred speaker merge or duration allocation',
        'chinese_cues': len(chinese), 'notes_cues': len(review_notes)}


def packet_summary(probe):
    require(isinstance(probe.get('streams'), list) and len(probe['streams']) >= 2,
        'Missing complete source media streams')
    require([stream['codec_type'] for stream in probe['streams'][:2]] == ['video', 'audio'],
        'Expected one explicitly ordered source video and audio stream')
    summaries = {}
    for stream in probe['streams'][:2]:
        stream_index = stream['index']
        packets = [packet for packet in probe['packets'] if packet['stream_index'] == stream_index]
        require(packets and all(all(field in packet for field in ('pts', 'dts', 'duration',
            'size', 'flags', 'data_hash')) and packet['data_hash'].startswith('SHA256:') for packet in packets),
            'Missing actual packet bytes or precise scheduling fields')
        require('time_base' in stream and 'extradata_hash' in stream, 'Missing encoded stream identity')
        clock = Fraction(stream['time_base'])
        summaries[str(stream_index)] = {'stream': {name: stream[name] for name in STREAM_FIELDS if name in stream},
            'packets': len(packets), 'ordered_payload_and_exact_ticks': state.signature(packets),
            'first_pts_seconds': str(min(packet['pts'] for packet in packets) * clock),
            'last_pts_end_seconds': str(max(packet['pts'] + packet['duration'] for packet in packets) * clock)}
    return summaries


def check(plan_path, output):
    plan, inputs = binding(plan_path)
    manifest = load(output / 'manifest.json')
    require(manifest.get('kind') == KIND and manifest.get('complete') is True
        and manifest.get('inputs') == inputs and all(manifest.get(name) is False for name in UNAPPROVED),
        'Review is incomplete, stale or overclaimed')
    require(set(path.name for path in output.iterdir()) == {'review.lock', 'manifest.json'} | set(manifest['artifacts']),
        'Unexpected review output inventory')
    require(all(record['path'] == str(output / name) and state.matches(record)
        for name, record in manifest['artifacts'].items()), 'Review output changed')
    for name in ('source-probe', 'mux', 'review-probe', 'extract-zh', 'extract-en', 'extract-notes'):
        process = load(output / f'{name}-process.json')
        require(process.get('status') == 'complete' and type(process.get('returncode')) is int
            and process['returncode'] == 0, 'Missing actual successful media subprocess exit')
    tracks, coverage = projection(plan)
    require(coverage == manifest['coverage'], 'Candidate coverage changed')
    for name, expected in tracks.items():
        require(caption_projection.read_srt(output / f'decoded-{name}.srt') == expected,
            'Embedded textual-review content or display windows changed')
    before = packet_summary(load(output / 'source-probe.json'))
    after_probe = load(output / 'review-probe.json')
    after = packet_summary(after_probe)
    require(before == after == manifest['source_packet_preservation'], 'Source bytes or exact schedule changed')
    require([stream['codec_type'] for stream in after_probe['streams']] ==
        ['video', 'audio', 'subtitle', 'subtitle', 'subtitle'], 'Unexpected review stream inventory')
    return {'manifest': state.identity(output / 'manifest.json'), 'coverage': coverage,
        'source_packet_preservation': before, 'no_media_commands_repeated': True,
        **{name: False for name in UNAPPROVED}}


def build(plan_path, output):
    plan, inputs = binding(plan_path)
    tracks, coverage = projection(plan)
    require(not output.exists() and output.is_absolute() and ROOT not in output.parents
        and all(output != Path(record['path']) and output not in Path(record['path']).parents
            for record in [plan[name] for name in ('source', 'reading', 'english_subtitles', 'technical_review')]),
        'Use an absent external output directory that contains no inputs')
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'review.lock').open('xb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = {'kind': KIND, 'complete': False, 'inputs': inputs, 'coverage': coverage,
            'scope': 'full-original-media-with-quarantined-text-not-final-Chinese-delivery',
            **{name: False for name in UNAPPROVED}}
        prepare_videos.save_json(output / 'manifest.json', manifest)
        try:
            for name, cues in tracks.items():
                prepare_videos.atomic_write(output / f'{name}.srt', caption_projection.srt_text(cues))
            probe_args = [inputs['tools']['ffprobe']['path'], '-v', 'error', '-show_streams',
                '-show_packets', '-show_data_hash', 'sha256', '-show_entries',
                'stream=index,' + ','.join(STREAM_FIELDS) + ':packet=stream_index,pts,dts,duration,size,flags,data_hash',
                '-of', 'json']
            native_audio.run_process(probe_args + [plan['source']['path']], output, 'source-probe', 'source-probe.json', lock.fileno())
            before = load(output / 'source-probe.json')
            require(len(before['streams']) == 2 and before['streams'][0]['codec_name'] == 'h264'
                and before['streams'][1]['codec_name'] == 'aac', 'Expected the explicit H.264/AAC MP4 source profile')
            video_clock = Fraction(before['streams'][0]['time_base'])
            require(video_clock.numerator == 1, 'Unsupported source video time base')
            source_end = max(Fraction(record['last_pts_end_seconds']) for record in packet_summary(before).values())
            require(all(Fraction(cue[1]) <= source_end for cues in tracks.values() for cue in cues),
                'Review captions would extend beyond the complete source media')
            base = [inputs['tools']['ffmpeg']['path'], '-nostdin', '-hide_banner', '-v', 'error', '-n']
            command = base + ['-copyts', '-i', plan['source']['path']]
            for name in tracks:
                command += ['-i', str(output / f'{name}.srt')]
            command += ['-map', '0:v:0', '-map', '0:a:0', '-map', '1:0', '-map', '2:0', '-map', '3:0',
                '-map_metadata', '-1', '-map_chapters', '-1', '-c', 'copy', '-c:s', 'mov_text',
                '-video_track_timescale', str(video_clock.denominator), '-avoid_negative_ts', 'disabled',
                '-metadata', 'title=INTERNAL TEXT REVIEW - original audio, not Chinese dubbing',
                '-metadata', 'comment=Quarantined captions only; roles, acoustic boundaries and factual holds unapproved',
                '-metadata:s:a:0', 'language=eng', '-metadata:s:a:0', 'handler_name=Complete unchanged source audio']
            for index, (language, title) in enumerate([('zho', 'Chinese textual candidates - NOT final timing'),
                    ('eng', 'Original automatic English captions'), ('zho', 'AI review notes - NOT speaker words')]):
                command += [f'-metadata:s:s:{index}', f'language={language}', f'-metadata:s:s:{index}',
                    f'handler_name={title}', f'-disposition:s:{index}', 'default' if index == 0 else '0']
            command.append(str(output / 'source-text-review.mp4'))
            native_audio.run_process(command, output, 'mux', 'mux.stdout.log', lock.fileno())
            native_audio.run_process(probe_args + [str(output / 'source-text-review.mp4')], output,
                'review-probe', 'review-probe.json', lock.fileno())
            require(packet_summary(before) == packet_summary(load(output / 'review-probe.json')),
                'Remux changed source payload, extradata or exact PTS/DTS/duration')
            for index, name in enumerate(tracks):
                native_audio.run_process(base + ['-i', str(output / 'source-text-review.mp4'), '-map',
                    f'0:s:{index}', '-c:s', 'srt', str(output / f'decoded-{name}.srt')], output,
                    f'extract-{name}', f'extract-{name}.stdout.log', lock.fileno())
                require(caption_projection.read_srt(output / f'decoded-{name}.srt') == tracks[name],
                    'Embedded subtitle content or exact display times did not round-trip')
            manifest['source_packet_preservation'] = packet_summary(before)
            manifest['complete'] = True
        except BaseException as error:
            manifest['error'] = f'{type(error).__name__}: {error}'
            raise
        finally:
            manifest['artifacts'] = {path.name: state.identity(path) for path in output.iterdir()
                if path.name not in {'review.lock', 'manifest.json'} and path.is_file()}
            prepare_videos.save_json(output / 'manifest.json', manifest)
    return check(plan_path, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('build', 'check'))
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    operation = build if arguments.mode == 'build' else check
    print(json.dumps(operation(arguments.plan.resolve(), arguments.output.resolve()), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
