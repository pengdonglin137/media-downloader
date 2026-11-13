"""译配阶段契约、严格质量策略及可审计的缓存身份（仅标准库）。"""

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from prepare_videos import save_json, timestamp

QUALITY = {'version': 1, 'max_rate': 15, 'max_tempo': 1.0}
COMPILER_VERSION = 1
PRONUNCIATION = [(r'\bL1\b', '一级'), (r'\bL2\b', '二级'), (r'\bL3\b', '三级'),
                 (r'\bLLC\b', '末级缓存'), (r'\bDRAM\b', '内存'), (r'\bCPU\b', '处理器'),
                 (r'\bGHz\b', '吉赫兹'), (r'\bMHz\b', '兆赫兹'), (r'\bKiB\b', 'K'),
                 (r'\bKB\b', 'K'), (r'\bMB\b', '兆'), (r'\bGB\b', 'G'),
                 (r'\bBPF\b', 'B P F'), (r'\bIPC\b', 'I P C')]
COMPILE_INPUTS = ['en.srt', 'translation.tsv', 'spoken_overrides.json', 'translation_task.json']
COMPILE_OUTPUTS = ['zh.srt', 'spoken.srt', 'bilingual.srt']
BINDINGS = ['compilation.json', 'narration.json', 'render_state.json']
_DIGEST_CACHE = {}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    path = Path(path).resolve()
    metadata = path.stat()
    cache_key = (str(path), metadata.st_dev, metadata.st_ino, metadata.st_size,
                 metadata.st_mtime_ns, metadata.st_ctime_ns)
    use_cache = metadata.st_size >= 1024 * 1024
    if use_cache and cache_key in _DIGEST_CACHE:
        return _DIGEST_CACHE[cache_key]
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    after = path.stat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != cache_key[1:]:
        raise ValueError(f'计算哈希期间文件变化: {path}')
    result = checksum.hexdigest()
    if use_cache:
        if len(_DIGEST_CACHE) >= 128:
            _DIGEST_CACHE.pop(next(iter(_DIGEST_CACHE)))
        _DIGEST_CACHE[cache_key] = result
    return result


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def fingerprint(path):
    path = Path(path).resolve()
    metadata = path.stat()
    return {'path': str(path), 'bytes': metadata.st_size, 'mtime_ns': metadata.st_mtime_ns}


def identity(path):
    before = fingerprint(path)
    result = {**before, 'sha256': digest(path)}
    if fingerprint(path) != before:
        raise ValueError(f'读取期间文件发生变化: {path}')
    return result


def matches(observation):
    path = Path(observation['path'])
    return path.is_file() and identity(path) == observation


def hashes(work, names):
    return {name: digest(work / name) if (work / name).is_file() else None for name in names}


def source_video(work):
    candidates = [path.resolve() for path in (work / 'download').glob('*.mp4')
                  if not re.search(r'\.f\d+\.mp4$|\.part\.mp4$', path.name)]
    if len(candidates) != 1:
        raise ValueError('工程必须有且仅有一个完整原片 MP4')
    return candidates[0]


def cues(path):
    from check_videos import subtitles
    text = Path(path).read_text(encoding='utf-8-sig').replace('\r\n', '\n').strip()
    parsed = subtitles(text)
    blocks = re.split(r'\n\s*\n', text)
    return [(entry[0], entry[1], '\n'.join(block.splitlines()[2:]).strip())
            for entry, block in zip(parsed, blocks)]


def srt_text(entries):
    return ''.join(f'{index}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n\n'
                   for index, (start, end, text) in enumerate(entries, 1))


def policy(max_rate=15, max_tempo=1.0):
    if isinstance(max_rate, bool) or not isinstance(max_rate, int) or not 0 <= max_rate <= QUALITY['max_rate']:
        raise ValueError('max_rate 必须为 0–15 的整数')
    if max_tempo != QUALITY['max_tempo']:
        raise ValueError('严格质量策略禁止额外 atempo 压缩')
    return {**QUALITY, 'max_rate': max_rate, 'max_tempo': max_tempo}


def check_stats(stats, count=None):
    rows = stats.get('per', [])
    if not rows or stats.get('entries') != len(rows) or (count is not None and len(rows) != count):
        raise ValueError('配音统计条数不符')
    if any(stats.get(key, 1) != 0 for key in ['silent', 'overlap', 'atempo']):
        raise ValueError('配音存在失败留空、重叠或额外压缩')
    for row in rows:
        if len(row) < 4 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in row):
            raise ValueError('配音统计数值无效')
        if not 0 <= row[0] <= QUALITY['max_rate'] or row[1] != 1.0 or row[2] < 0 or row[3] <= 0:
            raise ValueError('配音超出严格质量策略')


def check_task(work):
    task = load(work / 'translation_task.json')
    if (task['id'] != work.name.removeprefix('.zh_work_yt_') or task['units'] != len(cues(work / 'en.srt'))
            or task['source_sha256'] != digest(work / 'en.srt')):
        raise ValueError('英文字幕偏离翻译任务绑定，请重新审校并导出任务')


def compilation_contract(work):
    check_task(work)
    import speech_normalization
    settings = speech_normalization.configuration(work)
    if settings['version'] == 2:
        return {'version': 2, 'inputs': hashes(work, [*COMPILE_INPUTS, 'speech_policy.json']),
                'normalization': settings['normalization'],
                'normalizer_sha256': digest(speech_normalization.__file__)}
    return {'version': COMPILER_VERSION, 'inputs': hashes(work, COMPILE_INPUTS),
            'pronunciation': [list(item) for item in PRONUNCIATION]}


def expected_compilation(work):
    from check_videos import translations
    import speech_normalization
    settings = speech_normalization.configuration(work)
    original = cues(work / 'en.srt')
    translated = translations(work / 'translation.tsv', len(original))
    if len(translated) != len(original):
        raise ValueError('译文尚未完整，不能编译')
    overrides = load(work / 'spoken_overrides.json') if (work / 'spoken_overrides.json').exists() else {}
    if not isinstance(overrides, dict) or any(not str(number).isdigit() or str(int(number)) != number
                                             or not 1 <= int(number) <= len(original) for number in overrides):
        raise ValueError('朗读覆盖编号无效')
    chinese, spoken, bilingual = [], [], []
    for number, (start, end, english) in enumerate(original, 1):
        text = translated[number]
        speech = overrides.get(str(number), text)
        if not isinstance(speech, str) or not speech.strip() or '\n' in speech or '\r' in speech:
            raise ValueError(f'朗读覆盖内容无效: {number}')
        if settings['version'] == 2:
            speech = speech_normalization.normalize(speech)
        else:
            for pattern, replacement in PRONUNCIATION:
                speech = re.sub(pattern, replacement, speech)
        chinese.append((start, end, text))
        spoken.append((start, end, speech))
        bilingual.append((start, end, f'{text}\n{english}'))
    return dict(zip(COMPILE_OUTPUTS, [chinese, spoken, bilingual]))


def bind_compilation(work, origin='compiled'):
    expected = expected_compilation(work)
    for name, entries in expected.items():
        if cues(work / name) != entries:
            raise ValueError(f'{name} 与译文/朗读覆盖不符，请重新编译')
    record = {'origin': origin, 'observed_at': now(), 'contract': compilation_contract(work),
              'outputs': hashes(work, COMPILE_OUTPUTS)}
    save_json(work / 'compilation.json', record)
    return record


def validate_compilation(work):
    path = work / 'compilation.json'
    if not path.exists():
        raise ValueError('缺少编译依赖绑定：旧工程请执行 adopt，新工程请执行 compile')
    record = load(path)
    if record['contract'] != compilation_contract(work) or record['outputs'] != hashes(work, COMPILE_OUTPUTS):
        raise ValueError('译文或朗读覆盖已变化，必须重新 compile → tts → render')
    return record


def narration_dependencies(work):
    validate_compilation(work)
    result = {'compilation': signature(load(work / 'compilation.json')),
            'voices': hashes(work, ['voices.json', 'tts_voice.txt']), 'policy': QUALITY,
            'cache_implementation_sha256': digest(Path(__file__).with_name('dub.py')),
            'source': identity(source_video(work))}
    if (work / 'speech_groups.json').exists():
        import speech_groups
        speech_groups.load_policy(work)
        result['semantic_groups'] = {'policy_sha256': digest(work / 'speech_groups.json'),
                                     'implementation_sha256': digest(speech_groups.__file__)}
    return result


def narration_count(work):
    if (work / 'speech_groups.json').exists():
        import speech_groups
        return len(speech_groups.units(work))
    return len(cues(work / 'spoken.srt'))


def bind_narration(work, wav, origin, producer=None):
    dependencies = narration_dependencies(work)
    stats = load(work / 'tts_stats.json')
    check_stats(stats, narration_count(work))
    if load(work / 'timing_review.json') != []:
        raise ValueError('配音时窗仍有未解决问题')
    voices = load(work / 'voices.json')
    if not isinstance(voices, list) or len(voices) != len(cues(work / 'spoken.srt')) or not all(isinstance(voice, str) and voice for voice in voices):
        raise ValueError('音色列表无效')
    if origin not in {'synthesized', 'legacy-adopted', 'semantic-grouped'}:
        raise ValueError('配音来源类型无效')
    if (work / 'speech_groups.json').exists() and origin != 'semantic-grouped':
        raise ValueError('已审校语义组必须通过分组生产路径合成，不能追认旧逐条声音')
    if origin == 'semantic-grouped':
        import speech_groups
        speech_groups.validate_producer(work, wav, producer)
    if origin == 'synthesized':
        if (not producer or producer['waveform'] != identity(wav)
                or producer['statistics_sha256'] != digest(work / 'tts_stats.json')
                or producer['contract']['srt_sha256'] != digest(work / 'spoken.srt')
                or producer['contract']['voices_sha256'] != digest(work / 'voices.json')
                or producer['contract']['quality'] != QUALITY
                or producer['contract']['implementation_sha256'] != digest(Path(__file__).with_name('dub.py'))):
            raise ValueError('配音生成证据与当前输入/音频不符')
    record = {'origin': origin, 'observed_at': now(), 'dependencies': dependencies,
              'waveform': identity(wav), 'statistics': hashes(work, ['tts_stats.json', 'timing_review.json']),
              'producer': producer, 'historical_generation_proven': origin == 'synthesized' or
              (origin == 'semantic-grouped' and (producer['mode'] == 'full' or
               speech_groups.load_policy(work)['baseline']['narration']['historical_generation_proven']))}
    save_json(work / 'narration.json', record)
    return record


def validate_narration(work):
    if not (work / 'narration.json').exists():
        raise ValueError('缺少配音生成绑定：旧工程请显式 adopt；否则重新 tts')
    record = load(work / 'narration.json')
    if (record['dependencies'] != narration_dependencies(work)
            or record['statistics'] != hashes(work, ['tts_stats.json', 'timing_review.json'])
            or not matches(record['waveform'])):
        raise ValueError('配音依赖或 WAV 已变化，禁止复用旧配音，请重新 tts')
    if record['origin'] == 'semantic-grouped':
        import speech_groups
        speech_groups.validate_producer(work, Path(record['waveform']['path']), record['producer'])
    elif (work / 'speech_groups.json').exists():
        raise ValueError('当前声音未应用已审校的生产语义组')
    if record['origin'] == 'synthesized':
        producer = record['producer']
        if (producer['waveform'] != record['waveform']
                or producer['statistics_sha256'] != digest(work / 'tts_stats.json')
                or producer['contract']['srt_sha256'] != digest(work / 'spoken.srt')
                or producer['contract']['voices_sha256'] != digest(work / 'voices.json')
                or producer['contract']['quality'] != QUALITY
                or producer['contract']['implementation_sha256'] != digest(Path(__file__).with_name('dub.py'))):
            raise ValueError('合成实现已变化，请重新 tts 确认语音缓存')
    check_stats(load(work / 'tts_stats.json'), narration_count(work))
    return record


def render_dependencies(work, wav, video):
    return {'source': identity(video), 'waveform': identity(wav),
            'subtitles': hashes(work, [f'{language}_display.srt' for language in ['zh', 'en', 'bilingual']]),
            'narration': hashes(work, ['narration.json'])}


def bind_render(work, output, wav, video, options, origin='rendered'):
    record = {'origin': origin, 'observed_at': now(), 'dependencies': render_dependencies(work, wav, video),
              'output': identity(output), 'options': options}
    save_json(work / 'render_state.json', record)
    return record


def validate_render(work, output):
    narration = validate_narration(work)
    if not (work / 'render_state.json').exists():
        raise ValueError('缺少成品依赖绑定，请执行 render 或显式 adopt')
    record = load(work / 'render_state.json')
    if (record['dependencies'] != render_dependencies(work, Path(narration['waveform']['path']), source_video(work))
            or str(output.resolve()) != record['output']['path'] or not matches(record['output'])):
        raise ValueError('当前字幕/配音/原片与成品绑定不符，请重新 render')
    return record


def synthesis_contract(srt, voices, total, quality, engine_version):
    return {'version': 1, 'srt_sha256': digest(srt), 'voices_sha256': digest(voices),
            'duration': round(total, 3), 'quality': quality, 'engine': engine_version,
            'implementation_sha256': digest(Path(__file__).with_name('dub.py'))}
