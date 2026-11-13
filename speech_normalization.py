"""有版本的技术朗读规范；显示字幕不变，代码标识符不展开。"""

import re
from pathlib import Path


STYLE = 'technical-v2'
ALIASES = {'CPU': 'C P U', 'GPU': 'G P U', 'BPF': 'B P F', 'IPC': 'I P C',
           'TLB': 'T L B', 'SIMD': 'S I M D', 'AVX': 'A V X', 'LLVM': 'L L V M',
           'LLC': '末级缓存', 'DRAM': '动态随机存取存储器'}
SIZES = {'B': '字节', 'b': '比特'}
for prefix, decimal, binary in [('k', '千', None), ('K', '千', '千'), ('M', '兆', '兆'),
                                ('G', '吉', '吉'), ('T', '太', '太'), ('P', '拍', '拍')]:
    for suffix, reading in [('B', '字节'), ('b', '比特')]:
        SIZES[prefix + suffix] = decimal + reading
        if binary:
            SIZES[prefix + 'i' + suffix] = '二进制' + binary + reading
FREQUENCIES = {'Hz': '赫兹', 'kHz': '千赫兹', 'KHz': '千赫兹', 'MHz': '兆赫兹',
               'GHz': '吉赫兹', 'THz': '太赫兹'}
UNITS = {**SIZES, **FREQUENCIES}
UNIT_PATTERN = re.compile(r'(?<![A-Za-z0-9_.])(?P<number>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*'
                          r'(?P<unit>' + '|'.join(re.escape(unit) for unit in sorted(UNITS, key=len, reverse=True)) +
                          r')(?P<rate>/(?:ms|s))?(?![A-Za-z0-9_])')
PROTECTED = re.compile(r'`[^`\n]*`|https?://[^\s，。；]+|'
                       r'(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.)[A-Za-z_][A-Za-z0-9_]*)+|'
                       r'(?<![A-Za-z0-9_])/[A-Za-z_][^\s，。；]*')


def normalize_plain(text):
    def unit_reading(match):
        value = match['number'] + ' ' + UNITS[match['unit']]
        if match['rate']:
            return ('每毫秒 ' if match['rate'] == '/ms' else '每秒 ') + value
        return value

    text = UNIT_PATTERN.sub(unit_reading, text)
    text = re.sub(r'(?<![A-Za-z0-9_])L([123])(?=\s*(?:缓存|cache\b))',
                  lambda match: {'1': '一级', '2': '二级', '3': '三级'}[match[1]], text)
    for token, reading in ALIASES.items():
        text = re.sub(r'(?<![A-Za-z0-9_])' + re.escape(token) + r'(?![A-Za-z0-9_])', reading, text)
    return text


def normalize(text):
    parts, offset = [], 0
    for match in PROTECTED.finditer(text):
        parts.extend([normalize_plain(text[offset:match.start()]), match[0]])
        offset = match.end()
    parts.append(normalize_plain(text[offset:]))
    return ''.join(parts)


def configuration(work):
    import dubbing_state as state
    path = Path(work) / 'speech_policy.json'
    if not path.exists():
        return {'version': 1, 'normalization': 'legacy'}
    settings = state.load(path)
    if settings not in [{'version': 1, 'normalization': 'legacy'}, {'version': 2, 'normalization': STYLE}]:
        raise ValueError('speech_policy.json 无效；仅支持 legacy 或 technical-v2')
    return settings


def configure(work, style):
    from prepare_videos import save_json
    if style not in {'legacy', STYLE}:
        raise ValueError('未知朗读规范')
    settings = {'version': 1 if style == 'legacy' else 2, 'normalization': style}
    if configuration(work) != settings:
        save_json(Path(work) / 'speech_policy.json', settings)
    return settings
