"""局部语义分组/朗读规范 A/B 样片，不替换已绑定的生产配音。"""

import asyncio
import html
import importlib.metadata
import inspect
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from urllib.parse import quote

import dubbing_state as state
import speech_normalization
import speech_review
from prepare_videos import atomic_write, save_json


def dependencies(work):
    narration = state.validate_narration(work)
    return {'files': state.hashes(work, ['compilation.json', 'narration.json', 'translation.tsv', 'spoken_overrides.json',
                                        'voices.json', 'tts_voice.txt', 'speech_review.json', 'meta.json']),
            'waveform': narration['waveform'], 'normalizer_sha256': state.digest(speech_normalization.__file__),
            'planner_sha256': state.digest(__file__)}


def proposed_plan(work):
    from check_videos import translations
    bound = dependencies(work)
    if speech_review.status(work).get('state') in {'stale', 'not_generated'}:
        raise ValueError('请先完成当前音频的 speech-review')
    spoken = state.cues(work / 'spoken.srt')
    original = state.cues(work / 'en.srt')
    translated = translations(work / 'translation.tsv', len(original))
    overrides_path = work / 'spoken_overrides.json'
    overrides = state.load(overrides_path) if overrides_path.exists() else {}
    voices = state.load(work / 'voices.json')
    raw = [overrides.get(str(number), translated[number]) for number in range(1, len(spoken) + 1)]
    import speech_groups
    effective = speech_groups.units(work)
    owners = {number: entry for entry in effective for number in entry['unit_ids']}
    report = state.load(work / 'speech_review.json')
    cases = []

    def add_case(kind, numbers, proposed, **details):
        selected = [entry for entry in effective if set(numbers).intersection(entry['unit_ids'])]
        if any(len(entry['unit_ids']) > 1 for entry in selected):
            numbers = [number for entry in selected for number in entry['unit_ids']]
            proposed = speech_normalization.normalize(''.join(entry['spoken'] for entry in selected))
        if len({voices[number - 1] for number in numbers}) != 1:
            return
        start = spoken[numbers[0] - 1][0]
        last = numbers[-1]
        end = max(spoken[last - 1][1], spoken[last][0] - .05 if last < len(spoken) else spoken[last - 1][1])
        item = {'kind': kind, 'unit_ids': numbers, 'start': start, 'end': end, 'voice': voices[numbers[0] - 1],
                'original_spoken': [entry['spoken'] for entry in selected],
                'source': [original[number - 1][2] for number in numbers], 'proposed_spoken': proposed,
                'speaker_confirmed': False, 'sync_anchors_reviewed': False, **details}
        item['case_id'] = state.signature(item)[:24]
        cases.append(item)

    for issue in report['issues']:
        number = issue['unit_id']
        if issue['kind'] == 'cross-unit-fragment' and number < len(spoken):
            add_case('semantic-group', [number, issue.get('next_unit_id', number + 1)],
                     speech_normalization.normalize(raw[number - 1] + raw[number]), gap_seconds=issue['gap_seconds'])
    probed = set()
    for issue in report['issues']:
        number = issue['unit_id']
        if (issue['kind'] == 'pronunciation-term' and issue['term_id'] not in probed
                and spoken[number - 1][1] - spoken[number - 1][0] <= 40):
            add_case('pronunciation-probe', [number], speech_normalization.normalize(raw[number - 1]),
                     term_id=issue['term_id'], target_reading=issue['reading'], rule_applied=False)
            probed.add(issue['term_id'])
    for number, text in enumerate(raw, 1):
        owner = owners[number]
        if len(owner['unit_ids']) > 1:
            if number != owner['unit_ids'][0]:
                continue
            text = owner['spoken']
        proposed = speech_normalization.normalize(text)
        if proposed != owner['spoken']:
            add_case('normalization', [number], proposed)
    if dependencies(work) != bound:
        raise ValueError('生成优化计划期间输入变化')
    return {'version': 1, 'project_id': work.name.removeprefix('.zh_work_yt_'), 'dependencies': bound,
            'cases': cases, 'production_audio_modified': False, 'human_listening': False}


def plan(work):
    result = proposed_plan(work)
    save_json(work / 'speech_polish_plan.json', result)
    return result


def validate_plan(work):
    saved = state.load(work / 'speech_polish_plan.json')
    expected = proposed_plan(work)
    if saved.get('dependencies') != expected['dependencies'] or saved.get('project_id') != expected['project_id']:
        raise ValueError('样片计划已过期；请重新 speech-plan')
    base = {item['case_id']: item for item in expected['cases']}
    identifiers = set()
    for item in saved['cases']:
        identifier = item['case_id']
        text = item['proposed_spoken']
        if (identifier not in base or identifier in identifiers or not isinstance(text, str) or not text.strip()
                or len(text) > 4096 or '\n' in text or '\r' in text
                or item != {**base[identifier], 'proposed_spoken': text}):
            raise ValueError('样片计划只能编辑 proposed_spoken，不得修改来源、音色和时间锚点')
        identifiers.add(identifier)
    if identifiers != set(base):
        raise ValueError('样片计划不得删除来源候选；用 preview-limit 限制生成数量')
    return saved


async def synthesize_sample(text, voice, cache):
    import edge_tts
    engine = importlib.metadata.version('edge-tts')
    contract = {'text': text, 'voice': voice, 'rate': 0, 'engine': engine,
                'boundary': 'WordBoundary', 'implementation_sha256': state.signature(inspect.getsource(synthesize_sample))}
    key = state.signature(contract)
    audio_path, metadata_path, proof_path = [cache / (key + suffix) for suffix in ['.mp3', '.jsonl', '.json']]
    if proof_path.exists():
        proof = state.load(proof_path)
        if proof.get('contract') == contract and state.matches(proof['audio']) and state.matches(proof['metadata']):
            return proof
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sample-', dir=cache) as directory:
        pending_audio, pending_metadata = Path(directory) / 'speech.mp3', Path(directory) / 'boundaries.jsonl'
        await asyncio.wait_for(edge_tts.Communicate(text, voice, rate='+0%', boundary='WordBoundary').save(
            str(pending_audio), str(pending_metadata)), timeout=60)
        if pending_audio.stat().st_size < 200:
            raise ValueError('样片合成未返回有效音频')
        pending_audio.replace(audio_path)
        pending_metadata.replace(metadata_path)
    proof = {'contract': contract, 'audio': state.identity(audio_path), 'metadata': state.identity(metadata_path)}
    save_json(proof_path, proof)
    return proof


def decode_sample(proof):
    import dub
    import numpy as np
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', proof['audio']['path'],
                             '-f', 'f32le', '-ac', '1', '-ar', str(dub.SR), '-'], capture_output=True, check=True)
    samples = np.frombuffer(result.stdout, dtype=np.float32)
    voiced = np.nonzero(np.abs(samples) > .01)[0]
    if not len(voiced):
        raise ValueError('样片只有静音')
    leading = max(0, int(voiced[0]) - 240)
    samples = dub.level(samples[leading:int(voiced[-1]) + 480])
    offset = leading / dub.SR
    boundaries = []
    for line in Path(proof['metadata']['path']).read_text().splitlines():
        entry = json.loads(line)
        begin, finish = entry['offset'] / 10000000 - offset, (entry['offset'] + entry['duration']) / 10000000 - offset
        boundaries.append({'type': entry['type'], 'text': entry['text'], 'raw_start': begin,
                           'raw_end': finish, 'start': min(len(samples) / dub.SR, max(0, begin)),
                           'end': min(len(samples) / dub.SR, max(0, finish)),
                           'clamped': begin < 0 or finish > len(samples) / dub.SR})
    return samples, {'leading_trim_seconds': offset, 'boundaries': boundaries,
                     'word_boundary_observed': any(item['type'] == 'WordBoundary' for item in boundaries),
                     'alignment_human_verified': False}


def write_audio(path, samples):
    import dub
    import numpy as np
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, dub.SR, 0, 'NONE', 'not compressed'))
        audio.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())


def write_page(work, directory, result):
    rows = []
    for item in result['samples']:
        def local(path):
            return html.escape(quote(os.path.relpath(path, directory)), quote=True)
        rows.append('<article><h2>' + html.escape(item['kind']) + ' · 单元 ' + html.escape(str(item['unit_ids'])) + '</h2>'
                    '<p>原朗读：' + html.escape(' / '.join(item['original_spoken'])) + '</p>'
                    '<audio controls preload="metadata" src="' + local(item['before']['path']) + '"></audio>'
                    '<p>候选：' + html.escape(item['proposed_spoken']) + '</p>'
                    '<audio controls preload="metadata" src="' + local(item['after']['path']) + '"></audio>'
                    '<p>音色：' + html.escape(item['voice']) + '；时窗：' + str(round(item['end'] - item['start'], 2))
                    + ' 秒；候选有声长度：' + str(item['duration']) + ' 秒；放入时窗：' + ('是' if item['fits'] else '否，不能交付')
                    + '；词边界观测：' + ('有，未人工验证' if item['alignment']['word_boundary_observed'] else '无') + '</p>'
                    + '<button data-start="' + str(item['start']) + '" data-end="' + str(item['end']) + '">对照原片</button>'
                    + '<p>原文：' + html.escape(' / '.join(item['source'])) + '</p></article>')
    source = html.escape(quote(os.path.relpath(state.source_video(work), directory)), quote=True)
    atomic_write(directory / 'index.html', '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1"><title>中文译配局部 A/B</title>'
                 '<style>body{font:16px/1.6 system-ui;max-width:1000px;margin:2rem auto;padding:1rem}'
                 'audio{width:100%}video{max-width:100%;max-height:320px}article{border-bottom:1px solid #ccc;padding:1rem 0}</style>'
                 '<h1>中文译配局部 A/B</h1><p>' + html.escape(result.get('error', '局部样片；尚未登记人工试听')) + '</p>'
                 '<p>候选仅供试听，不替换生产配音；播放不会登记验收。'
                 '合并组的讲者、画面锚点与自然度仍需确认；缓存行样例只是当前引擎读法探测，没有强制音素修复。</p>'
                 '<video id="source" controls preload="metadata" src="' + source + '"></video>' + ''.join(rows) +
                 '<script>const players=[...document.querySelectorAll("audio,video")];'
                 'players.forEach(player=>player.addEventListener("play",()=>players.forEach(other=>{if(other!==player)other.pause();})));'
                 'const source=document.getElementById("source");let ending=Infinity;'
                 'document.querySelectorAll("button[data-start]").forEach(button=>button.onclick=()=>{'
                 'ending=Number(button.dataset.end);source.currentTime=Number(button.dataset.start);source.play().catch(()=>{});});'
                 'source.addEventListener("timeupdate",()=>{if(source.currentTime>=ending)source.pause();});</script></html>')


async def preview(work, limit=6):
    import dub
    planned = validate_plan(work)
    groups = [[item for item in planned['cases'] if item['kind'] == kind and item['end'] - item['start'] <= 40]
              for kind in ['semantic-group', 'normalization', 'pronunciation-probe']]
    selected = []
    seen = set()
    while len(selected) < limit and any(groups):
        for group in groups:
            if group and len(selected) < limit:
                item = group.pop(0)
                key = (tuple(item['unit_ids']), item['voice'], item['proposed_spoken'])
                if key not in seen:
                    selected.append(item)
                    seen.add(key)
    if not selected:
        raise ValueError('没有 40 秒内的可用样片候选')
    engine = importlib.metadata.version('edge-tts')
    preview_id = state.signature({'plan': planned, 'selected': selected, 'engine': engine})[:20]
    directory = work / 'speech_previews' / preview_id
    directory.mkdir(parents=True, exist_ok=True)
    result = {'version': 1, 'dependencies': planned['dependencies'], 'plan_sha256': state.digest(work / 'speech_polish_plan.json'),
              'engine': engine, 'samples': [], 'production_audio_modified': False, 'human_listening': False}
    for item in selected:
        try:
            proof = await synthesize_sample(item['proposed_spoken'], item['voice'], work / 'speech_preview_cache')
        except Exception as error:
            result['error'] = str(error)
            result['complete'] = False
            save_json(directory / 'manifest.json', result)
            write_page(work, directory, result)
            raise ValueError('局部样片合成失败，已保留结果且未改生产配音：' + str(error)) from error
        samples, alignment = decode_sample(proof)
        after = directory / (item['case_id'] + '-after.wav')
        before = directory / (item['case_id'] + '-before.wav')
        write_audio(after, samples)
        with wave.open(planned['dependencies']['waveform']['path']) as original:
            begin = int(item['start'] * original.getframerate())
            original.setpos(begin)
            frames = original.readframes(max(0, int(item['end'] * original.getframerate()) - begin))
            with wave.open(str(before), 'wb') as audio:
                audio.setparams(original.getparams())
                audio.writeframes(frames)
        result['samples'].append({**item, 'before': state.identity(before), 'after': state.identity(after),
                                  'duration': round(len(samples) / dub.SR, 3), 'fits': len(samples) / dub.SR <= item['end'] - item['start'],
                                  'alignment': alignment, 'synthesis': proof, 'acceptance': 'awaiting_listening'})
        if dependencies(work) != planned['dependencies']:
            raise ValueError('样片生成期间输入变化；未修改生产配音')
        save_json(directory / 'manifest.json', result)
        write_page(work, directory, result)
    result['complete'] = True
    save_json(directory / 'manifest.json', result)
    save_json(work / 'speech_preview.json', {'directory': str(directory), 'manifest': state.identity(directory / 'manifest.json'),
                                          'dependencies': planned['dependencies'], 'human_listening': False})
    return directory
