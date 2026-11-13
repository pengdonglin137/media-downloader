"""审校后的语义朗读分组、增量生产合成与高风险边界门禁。"""

import asyncio
import importlib.metadata
import inspect
import re
import wave
from contextlib import closing
from pathlib import Path

import dubbing_state as state
from prepare_videos import atomic_write, save_json


POLICY = 'speech_groups.json'
FINISHED = re.compile(r'[。！？.!?；;][”’"\')）]*\s*$')


def text_dependencies(work):
    state.validate_compilation(work)
    return {'files': state.hashes(work, ['compilation.json', 'en.srt', 'zh.srt', 'spoken.srt',
                                         'voices.json', 'tts_voice.txt']),
            'source': state.identity(state.source_video(work))}


def original_units(work):
    spoken, chinese, source = [state.cues(work / name) for name in ['spoken.srt', 'zh.srt', 'en.srt']]
    voices = state.load(work / 'voices.json') if (work / 'voices.json').exists() else ['zh-CN-YunxiNeural'] * len(spoken)
    selection = work / 'tts_voice.txt'
    if selection.exists():
        voices = [selection.read_text().strip()] * len(spoken)
    if (not isinstance(voices, list) or len({len(spoken), len(chinese), len(source), len(voices)}) != 1
            or not all(isinstance(voice, str) and voice.strip() for voice in voices)
            or any(speech[:2] != caption[:2] or speech[:2] != english[:2]
                   for speech, caption, english in zip(spoken, chinese, source))):
        raise ValueError('语义分组要求原文、译文、朗读和音色逐单元对应')
    return [{'unit_ids': [number], 'start': speech[0], 'end': speech[1], 'spoken': speech[2],
             'zh': caption[2], 'source': english[2], 'voice': voice}
            for number, (speech, caption, english, voice) in enumerate(zip(spoken, chinese, source, voices), 1)]


def load_policy(work):
    path = work / POLICY
    if not path.exists():
        return None
    data = state.load(path)
    unsigned = {key: value for key, value in data.items() if key != 'revision'}
    if (data.get('version') != 1 or data.get('revision') != state.signature(unsigned)
            or data.get('dependencies') != text_dependencies(work)):
        raise ValueError('语义分组已过期或被改写；请重新导出审校任务')
    used = set()
    units = original_units(work)
    for group in data['groups']:
        derived = derive_group(units, {key: group[key] for key in
                               ['unit_ids', 'spoken', 'zh', 'speaker_id', 'source_review_note', 'sync_note']})
        if group != derived or used.intersection(group['unit_ids']):
            raise ValueError('语义分组来源、音色、时间或重复归属无效')
        used.update(group['unit_ids'])
    return data


def units(work):
    original = original_units(work)
    policy = load_policy(work)
    groups = {group['unit_ids'][0]: group for group in policy['groups']} if policy else {}
    covered = {number for group in groups.values() for number in group['unit_ids'][1:]}
    return [groups.get(item['unit_ids'][0], item) for item in original if item['unit_ids'][0] not in covered]


def derive_group(original, entry):
    numbers = entry['unit_ids']
    if (not isinstance(numbers, list) or not 2 <= len(numbers) <= 8
            or any(type(number) is not int for number in numbers)
            or numbers != list(range(numbers[0], numbers[-1] + 1))
            or numbers[0] < 1 or numbers[-1] > len(original)):
        raise ValueError('朗读组必须由二至八个连续、唯一且有效的来源单元组成')
    selected = [original[number - 1] for number in numbers]
    if len({item['voice'] for item in selected}) != 1:
        raise ValueError('不同音色的单元不得合成同一个朗读组')
    if (selected[-1]['end'] - selected[0]['start'] > 40
            or any(previous['end'] > following['start'] + .01
                   for previous, following in zip(selected, selected[1:]))):
        raise ValueError('朗读组超出四十秒或包含重叠来源，需重新规划')
    for name in ['spoken', 'zh', 'speaker_id', 'source_review_note', 'sync_note']:
        value = entry[name]
        if not isinstance(value, str) or not value.strip() or '\n' in value or '\r' in value or len(value) > 4096:
            raise ValueError('分组必须有完整文本、明确讲者、原文审校和同步说明')
    if entry['speaker_id'].strip().lower() in {'unknown', 'auto', '未知', '不明'}:
        raise ValueError('讲者不明时禁止自动合并')
    return {**entry, 'start': selected[0]['start'], 'end': selected[-1]['end'],
            'voice': selected[0]['voice'], 'source': [item['source'] for item in selected],
            'original_spoken': [item['spoken'] for item in selected]}


def boundaries(work, ignore_policy=False):
    original = original_units(work)
    policy = None if ignore_policy else load_policy(work)
    covered = {number for group in policy['groups'] for number in group['unit_ids'][:-1]} if policy else set()
    result = []
    for previous, following in zip(original, original[1:]):
        number = previous['unit_ids'][0]
        length = len(re.findall(r'[A-Za-z0-9]+|[\u3400-\u9fff]', following['spoken']))
        if number in covered or FINISHED.search(previous['spoken']) or not 0 < length <= 6:
            continue
        item = {'unit_ids': [number, number + 1], 'start': previous['start'], 'end': following['end'],
                'source': [previous['source'], following['source']],
                'spoken': [previous['spoken'], following['spoken']],
                'voices': [previous['voice'], following['voice']], 'automatic_error_verdict': False}
        result.append({'boundary_id': state.signature(item)[:24], **item})
    return result


def task(work):
    state.validate_compilation(work)
    voices = [item['voice'] for item in original_units(work)]
    voices_path = work / 'voices.json'
    if not voices_path.exists() or state.load(voices_path) != voices:
        save_json(voices_path, voices)
    bound = text_dependencies(work)
    try:
        load_policy(work)
        stale = False
    except ValueError:
        stale = True
    entries = boundaries(work, ignore_policy=stale)
    result = {'version': 1, 'project_id': work.name.removeprefix('.zh_work_yt_'), 'dependencies': bound,
              'base_policy_sha256': state.digest(work / POLICY) if (work / POLICY).exists() else None,
              'supersedes_stale_policy': stale,
              'boundaries': [{**item, 'decision': 'pending', 'note': ''} for item in entries], 'groups': []}
    result['revision'] = state.signature({key: value for key, value in result.items() if key != 'groups'})
    destination = work / 'speech_group_tasks' / (result['revision'] + '.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if state.load(destination) != result:
            raise ValueError('已有分组任务被编辑；请保留原任务，用副本回复')
    else:
        save_json(destination, result)
    return destination, result


def import_task(work, path):
    reply = state.load(path)
    try:
        existing = load_policy(work)
    except ValueError:
        existing = None
    signature = state.signature(reply)
    if existing and existing.get('import_signature') == signature:
        save_json(work / 'speech_boundary_reviews.json', {'dependencies': existing['dependencies'],
                  'boundaries': reply['boundaries'], 'task_revision': reply['revision'],
                  'import_signature': signature, 'agent_audio_listened': False})
        return existing
    _, expected = task(work)
    if (reply.keys() != expected.keys() or any(reply[key] != expected[key]
            for key in expected if key not in {'groups', 'boundaries'})):
        raise ValueError('分组回复已过期或来源依赖被改写')
    if len(reply['boundaries']) != len(expected['boundaries']):
        raise ValueError('不得漏掉待审边界')
    groups = list(existing['groups']) if existing else []
    original = original_units(work)
    for entry in reply['groups']:
        if set(entry) != {'unit_ids', 'spoken', 'zh', 'speaker_id', 'source_review_note', 'sync_note'}:
            raise ValueError('分组回复只接受来源编号、文本、讲者和审校说明，不接受自定时间')
        groups.append(derive_group(original, entry))
    assigned = [number for group in groups for number in group['unit_ids']]
    if len(assigned) != len(set(assigned)):
        raise ValueError('来源单元不能同时属于多个朗读组')
    covered = {number for group in groups for number in group['unit_ids'][:-1]}
    for proposed, base in zip(reply['boundaries'], expected['boundaries']):
        if (proposed != {**base, 'decision': proposed.get('decision'), 'note': proposed.get('note')}
                or proposed.get('decision') not in {'merge', 'keep'}
                or not isinstance(proposed.get('note'), str) or not proposed['note'].strip()):
            raise ValueError('每个边界必须逐项 merge/keep 并说明理由，不得改写锚点')
        if (proposed['decision'] == 'merge') != (base['unit_ids'][0] in covered):
            raise ValueError('合并决定必须有实际朗读组，保留决定不能被合并覆盖')
    baseline = None
    if reply['groups'] and (work / 'narration.json').exists() and not expected['supersedes_stale_policy']:
        try:
            record = state.validate_narration(work)
        except ValueError:
            record = None
        if record:
            baseline = {'narration': record, 'statistics': state.load(work / 'tts_stats.json'), 'units': units(work)}
    reviewed = {'dependencies': expected['dependencies'], 'boundaries': reply['boundaries'],
                'task_revision': expected['revision'], 'import_signature': signature, 'agent_audio_listened': False}
    if text_dependencies(work) != expected['dependencies']:
        raise ValueError('分组审校期间来源发生变化，未发布新策略')
    if not reply['groups'] and not expected['supersedes_stale_policy']:
        save_json(work / 'speech_boundary_reviews.json', reviewed)
        return reviewed
    policy = {'version': 1, 'project_id': expected['project_id'], 'dependencies': expected['dependencies'],
              'groups': sorted(groups, key=lambda group: group['start']), 'baseline': baseline,
              'task_revision': expected['revision'], 'import_signature': signature}
    policy['revision'] = state.signature(policy)
    history = work / 'speech_group_history' / policy['revision']
    history.mkdir(parents=True, exist_ok=True)
    for name in [POLICY, 'speech_boundary_reviews.json', 'narration.json', 'render_state.json', 'tts_stats.json',
                 'timing_review.json', 'delivery_check.json']:
        if (work / name).exists():
            atomic_write(history / name, (work / name).read_text())
    if (text_dependencies(work) != expected['dependencies']
            or (state.digest(work / POLICY) if (work / POLICY).exists() else None) != expected['base_policy_sha256']):
        raise ValueError('分组发布期间输入或旧策略变化，未替换策略')
    if groups:
        save_json(work / POLICY, policy)
    else:
        (work / POLICY).unlink(missing_ok=True)
    save_json(work / 'speech_boundary_reviews.json', reviewed)
    return policy


def preflight(work):
    reviewed_path = work / 'speech_boundary_reviews.json'
    reviewed = state.load(reviewed_path) if reviewed_path.exists() else {}
    accepted = {item['boundary_id'] for item in reviewed.get('boundaries', [])
                if item.get('decision') == 'keep' and isinstance(item.get('note'), str) and item['note'].strip()}
    if reviewed.get('dependencies') != text_dependencies(work):
        accepted = set()
    pending = [item for item in boundaries(work) if item['boundary_id'] not in accepted]
    if pending:
        path, _ = task(work)
        raise ValueError(f'有 {len(pending)} 处短尾断句尚未审校；先 speech-group-plan / speech-group-import：{path}')


def contract(work, engine):
    import speech_polish
    return {'version': 1, 'policy_sha256': state.digest(work / POLICY), 'inputs': units(work), 'quality': state.QUALITY,
            'engine': engine, 'implementation_sha256': state.digest(__file__),
            'synthesizer_sha256': state.signature(inspect.getsource(speech_polish.synthesize_sample))}


def read_audio(path):
    with wave.open(str(path), 'rb') as audio:
        return audio.getparams(), audio.readframes(audio.getnframes())


def write_audio(path, parameters, content):
    with wave.open(str(path), 'wb') as audio:
        audio.setparams(parameters)
        audio.writeframes(content)


def validate_producer(work, waveform, producer):
    if (not producer or producer['contract'] != contract(work, producer['contract']['engine'])
            or producer['waveform'] != state.identity(waveform)
            or producer['statistics_sha256'] != state.digest(work / 'tts_stats.json')):
        raise ValueError('语义分组生产证据与当前输入或声音不符')
    policy = load_policy(work)
    if producer['mode'] == 'full':
        generated = producer['generated']
        entries = producer['contract']['inputs']
        if (not state.matches(producer['input_srt']) or not state.matches(producer['input_voices'])
                or Path(producer['input_srt']['path']).read_text() != state.srt_text(
                    [(item['start'], item['end'], item['spoken']) for item in entries])
                or state.load(producer['input_voices']['path']) != [item['voice'] for item in entries]
                or generated['contract']['srt_sha256'] != producer['input_srt']['sha256']
                or generated['contract']['voices_sha256'] != producer['input_voices']['sha256']
                or generated['contract']['quality'] != state.QUALITY
                or generated['contract']['implementation_sha256'] != state.digest(Path(__file__).with_name('dub.py'))
                or generated['waveform'] != producer['waveform']
                or generated['statistics_sha256'] != producer['statistics_sha256']):
            raise ValueError('完整分组合成的生成证据无效')
        return
    if producer['mode'] != 'incremental' or not policy['baseline']:
        raise ValueError('分组生产模式无效')
    baseline = policy['baseline']
    original_wave = baseline['narration']['waveform']
    current = state.narration_dependencies(work)
    previous = baseline['narration']['dependencies']
    if ({key: value for key, value in current.items() if key != 'semantic_groups'}
            != {key: value for key, value in previous.items() if key != 'semantic_groups'}
            or not state.matches(original_wave)):
        raise ValueError('增量合成的原始声音或编译来源已变化')
    parameters, original = read_audio(original_wave['path'])
    actual_parameters, actual = read_audio(waveform)
    if parameters != actual_parameters or (parameters.nchannels, parameters.sampwidth, parameters.framerate) != (1, 2, 24000):
        raise ValueError('增量合成必须保留 PCM 格式与总帧数')
    cursor = 0
    expected_groups = [item for item in producer['contract']['inputs']
                       if item['unit_ids'] not in [unit['unit_ids'] for unit in baseline['units']]]
    if [item['unit_ids'] for item in producer['patches']] != [item['unit_ids'] for item in expected_groups]:
        raise ValueError('生产修订没有完整覆盖新的语义组')
    for patch, group in zip(producer['patches'], expected_groups):
        start, end = round(group['start'] * parameters.framerate) * 2, round(group['end'] * parameters.framerate) * 2
        proof = patch['synthesis']
        expected_text = {'text': group['spoken'], 'voice': group['voice'], 'rate': 0,
                         'engine': producer['contract']['engine'], 'boundary': 'WordBoundary',
                         'implementation_sha256': producer['contract']['synthesizer_sha256']}
        if (proof['contract'] != expected_text or not all(state.matches(proof[name]) for name in ['audio', 'metadata'])
                or not state.matches(patch['audio'])):
            raise ValueError('语义组音色、文本或合成文件已变化')
        clip_parameters, clip = read_audio(patch['audio']['path'])
        if ((clip_parameters.nchannels, clip_parameters.sampwidth, clip_parameters.framerate) != (1, 2, 24000)
                or len(clip) > end - start or actual[cursor:start] != original[cursor:start]
                or actual[start:end] != clip + bytes(end - start - len(clip))):
            raise ValueError('语义组超窗、被截断或时窗外声音发生变化')
        cursor = end
    if actual[cursor:] != original[cursor:]:
        raise ValueError('语义组之后的声音时间轴或内容发生变化')
    expected_rows = {tuple(unit['unit_ids']): row for unit, row in zip(baseline['units'], baseline['statistics']['per'])}
    for patch, group in zip(producer['patches'], expected_groups):
        clip_parameters, _ = read_audio(patch['audio']['path'])
        frames = round(group['end'] * parameters.framerate) - round(group['start'] * parameters.framerate)
        expected_rows[tuple(group['unit_ids'])] = [0, 1.0, clip_parameters.nframes / frames, group['end'] - group['start']]
    statistics = state.load(work / 'tts_stats.json')
    if (statistics['per'] != [expected_rows[tuple(item['unit_ids'])] for item in producer['contract']['inputs']]
            or statistics.get('source_unit_ids') != [item['unit_ids'] for item in producer['contract']['inputs']]):
        raise ValueError('分组合成统计与实际替换范围不符')


def synthesize(work, workers=6):
    import speech_polish
    preflight(work)
    policy = load_policy(work)
    if not policy:
        raise ValueError('没有已审校的生产语义组')
    try:
        previous = state.validate_narration(work)
        if previous['origin'] == 'semantic-grouped':
            print('语义组、配音及生产证据未变化，复用正式 WAV', flush=True)
            return previous
    except ValueError:
        pass
    engine = importlib.metadata.version('edge-tts')
    bound = contract(work, engine)
    directory = work / 'speech_group_runs' / state.signature(bound)[:20]
    directory.mkdir(parents=True, exist_ok=True)
    entries = bound['inputs']
    atomic_write(directory / 'input.srt', state.srt_text([(item['start'], item['end'], item['spoken']) for item in entries]))
    save_json(directory / 'voices.json', [item['voice'] for item in entries])
    destination = directory / 'narration.wav'
    patches = []
    baseline = policy['baseline']
    if baseline:
        if not state.matches(baseline['narration']['waveform']):
            raise ValueError('增量合成的基线 WAV 已变化')
        parameters, original = read_audio(baseline['narration']['waveform']['path'])
        if (parameters.nchannels, parameters.sampwidth, parameters.framerate) != (1, 2, 24000):
            raise ValueError('当前增量合成只支持生产的 24 kHz 单声道 16-bit PCM')
        previous_rows = {tuple(unit['unit_ids']): row for unit, row in zip(baseline['units'], baseline['statistics']['per'])}
        state.check_stats(baseline['statistics'], len(baseline['units']))
        result = bytearray(original)
        rows = []
        for item in entries:
            if tuple(item['unit_ids']) in previous_rows:
                rows.append(previous_rows[tuple(item['unit_ids'])])
                continue
            if len(item['unit_ids']) < 2:
                raise ValueError('增量分组不能拆开已合成的朗读组，需重新完整合成')
            proof = asyncio.run(speech_polish.synthesize_sample(item['spoken'], item['voice'], work / 'speech_preview_cache'))
            samples, alignment = speech_polish.decode_sample(proof)
            clip = directory / ('group-' + '-'.join(map(str, item['unit_ids'])) + '.wav')
            speech_polish.write_audio(clip, samples)
            _, content = read_audio(clip)
            start, end = round(item['start'] * parameters.framerate) * 2, round(item['end'] * parameters.framerate) * 2
            if len(content) > end - start or end > len(original):
                raise ValueError('完整语义组超出原时窗；不得截尾、压缩或移动后续声音')
            result[start:end] = content + bytes(end - start - len(content))
            rows.append([0, 1.0, len(content) / (end - start), item['end'] - item['start']])
            patches.append({'unit_ids': item['unit_ids'], 'audio': state.identity(clip),
                            'synthesis': proof, 'alignment': alignment})
        write_audio(destination, parameters, result)
        statistics = {'entries': len(entries), 'silent': 0, 'overlap': 0, 'atempo': 0, 'per': rows,
                      'source_unit_ids': [item['unit_ids'] for item in entries], 'quality': state.QUALITY}
        producer = {'mode': 'incremental', 'contract': bound, 'patches': patches,
                    'baseline_origin': baseline['narration']['origin'], 'outside_windows_pcm_identical': True}
    else:
        import translate_to_zh as pipeline
        duration = pipeline.probe_duration(state.source_video(work))
        pipeline.run([str(pipeline.PVT_DIR / '.venv/bin/python'), str(Path(__file__).with_name('dub.py')),
                      str(directory / 'input.srt'), str(destination), str(work / 'tts_cache'),
                      '--voices', str(directory / 'voices.json'), '--total', str(duration), '--workers', str(workers)],
                     logfile=directory / 'tts.log')
        statistics = state.load(destination.with_suffix('.stats.json'))
        producer = {'mode': 'full', 'contract': bound, 'generated': state.load(destination.with_suffix('.provenance.json')),
                    'input_srt': state.identity(directory / 'input.srt'), 'input_voices': state.identity(directory / 'voices.json')}
    state.check_stats(statistics, len(entries))
    import translate_to_zh as pipeline
    pipeline.verify_dub_tail(destination, pipeline.probe_duration(state.source_video(work)))
    if contract(work, engine) != bound:
        raise ValueError('合成过程中语义组输入变化；未更新生产绑定')
    producer.update(waveform=state.identity(destination))
    old = {name: (work / name).read_text() if (work / name).exists() else None
           for name in ['tts_stats.json', 'timing_review.json', 'narration.json']}
    try:
        save_json(work / 'tts_stats.json', statistics)
        save_json(work / 'timing_review.json', [])
        producer['statistics_sha256'] = state.digest(work / 'tts_stats.json')
        validate_producer(work, destination, producer)
        save_json(directory / 'producer.json', producer)
        return state.bind_narration(work, destination, 'semantic-grouped', producer)
    except Exception:
        for name, content in old.items():
            if content is None:
                (work / name).unlink(missing_ok=True)
            else:
                atomic_write(work / name, content)
        raise


def quality_blockers(work, report, database):
    import speech_review
    high_risk = [item for item in report['issues'] if item['kind'] == 'cross-unit-fragment'
                 or (item['kind'] == 'short-tail-after-pause' and item.get('gap_seconds', 0) >= 1.4)]
    with closing(speech_review.connect_database(database, readonly=True)) as connection:
        decisions = {identifier: (decision, listened) for identifier, decision, listened in connection.execute(
            'SELECT issue_id,status,listened FROM decisions WHERE revision=?', (report['revision'],))}
    return [{'kind': 'unreviewed_high_risk_pause', 'issue_id': item['issue_id'], 'unit_id': item['unit_id'],
             'note': '高风险短尾/跨单元停顿必须按当前音频复核；候选尚不等于确诊错误'}
            for item in high_risk if decisions.get(item['issue_id']) not in {('dismissed', 1), ('confirmed_correct', 1)}]


def update_subtitles(work):
    narration = state.validate_narration(work)
    if narration['origin'] != 'semantic-grouped':
        return
    import translate_to_zh as pipeline
    import numpy
    groups = load_policy(work)['groups']
    waveform = Path(narration['waveform']['path'])
    for filename in ['zh_display.srt', 'bilingual_display.srt']:
        current = state.cues(work / filename)
        if any(entry[0] < group['end'] and entry[1] > group['start']
               and (entry[0] < group['start'] - .002 or entry[1] > group['end'] + .002)
               for entry in current for group in groups):
            raise ValueError('原显示字幕跨越语义组外的来源，拒绝丢弃边界外文字')
        kept = [entry for entry in current if not any(entry[0] < group['end'] and entry[1] > group['start'] for group in groups)]
        for group in groups:
            with wave.open(str(waveform)) as audio:
                audio.setpos(round(group['start'] * audio.getframerate()))
                content = audio.readframes(round((group['end'] - group['start']) * audio.getframerate()))
                samples = numpy.frombuffer(content, dtype='<i2')
                nonzero = numpy.flatnonzero(numpy.abs(samples.astype('int32')) > 327)
                if not len(nonzero):
                    raise ValueError('语义组声音为空，不能生成配音字幕')
                duration = (int(nonzero[-1]) + 1) / audio.getframerate()
            count = max(1, (len(group['zh']) + pipeline.ZH_SCREEN - 1) // pipeline.ZH_SCREEN)
            count = min(count, max(1, len(' '.join(group['source']).split())))
            parts = pipeline.split_at(group['zh'], count, True)
            english = pipeline.split_at(' '.join(group['source']), count, False)
            total = sum(len(text) for text in parts)
            cursor = group['start']
            for index, text in enumerate(parts):
                finish = cursor + duration * len(text) / total
                kept.append((cursor, finish, text if filename.startswith('zh') else text + '\n' + english[index]))
                cursor = finish
        kept.sort()
        atomic_write(work / filename, state.srt_text(kept))
    save_json(work / 'speech_group_caption_timing.json', {'narration_sha256': state.digest(work / 'narration.json'),
              'method': 'measured-voiced-duration-proportional', 'word_alignment_verified': False,
              'source_english_subtitles_preserved': True, 'human_sync_approved': False})
