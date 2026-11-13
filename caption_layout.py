"""显式版本化的字幕排版预检，不改旧上传实现、配音或现有成片。"""

import argparse
import math
import re
from pathlib import Path

import dubbing_state as state
import upload_export
import video_package
from prepare_videos import atomic_write, save_json


CLOSING = '，。！？；：、）》」』】〕〉〗〙〛…,.!?;:%)]}”’'
OPENING = '（《「『【〔〈〖〘〚([{“‘'
TOKENS = re.compile(r'[A-Za-z0-9_:.+/\-]+|[^\s]')
QUANTITIES = re.compile(
    r'(?<![A-Za-z0-9_])\d+(?:\.\d+)?\s*'
    r'(?:毫秒|微秒|纳秒|分钟|小时|秒|毫瓦|千瓦|瓦|千赫兹|兆赫兹|吉赫兹|赫兹|'
    r'[kmgtpe]?i?b|[num]?s|[kmgt]?hz|%)'
    r'(?![A-Za-z0-9_])', re.IGNORECASE)


def normalize(text):
    return ' '.join(text.split())


def width(text):
    return sum(.55 if ord(character) < 128 else 1 for character in text)


def load_forms(path):
    entries = state.load(path)
    if not isinstance(entries, list) or not entries:
        raise ValueError('字幕排版需要非空的显式词表')
    forms = set()
    names = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'name', 'spoken_forms'}:
            raise ValueError('词表每项必须包含 name 和 spoken_forms')
        name, aliases = entry['name'], entry['spoken_forms']
        if (not isinstance(name, str) or not normalize(name) or name in names
                or not isinstance(aliases, list) or not aliases
                or any(not isinstance(alias, str) or not normalize(alias) for alias in aliases)):
            raise ValueError('词条必须唯一、非空，且提供非空字面写法')
        names.add(name)
        forms.update(normalize(form) for form in [name, *aliases])
    return sorted(forms, key=lambda form: (-len(form), form))


def protected_spans(text, forms):
    spans = set()
    for form in forms:
        cursor = 0
        while (start := text.find(form, cursor)) >= 0:
            end = start + len(form)
            left_identifier = (start and form[0].isascii() and form[0].isalnum()
                               and text[start - 1].isascii()
                               and (text[start - 1].isalnum() or text[start - 1] == '_'))
            right_identifier = (end < len(text) and form[-1].isascii() and form[-1].isalnum()
                                and text[end].isascii()
                                and (text[end].isalnum() or text[end] == '_'))
            if not left_identifier and not right_identifier:
                spans.add((start, end, form))
            cursor = start + 1
    spans.update((match.start(), match.end(), match.group()) for match in QUANTITIES.finditer(text))
    return sorted(spans)


def split_terms(text, lines, forms):
    if len(lines) == 1:
        return []
    complete = normalize(text)
    boundary = len(lines[0])
    return sorted({form for start, end, form in protected_spans(complete, forms)
                   if start < boundary < end})


def wrap_caption(text, columns, forms):
    if not isinstance(columns, int) or isinstance(columns, bool) or columns < 1:
        raise ValueError('字幕列宽必须为正整数')
    complete = normalize(text)
    if not complete:
        raise ValueError('字幕不能为空')
    if width(complete) <= columns:
        return [complete]
    matches = list(TOKENS.finditer(complete))
    if any(width(match.group()) > columns for match in matches):
        raise ValueError('字幕含超宽代码标识符；需人工分屏，不截字或缩字号')
    spans = protected_spans(complete, forms)
    candidates = []
    for match in matches[:-1]:
        boundary = match.end()
        left, right = complete[:boundary].rstrip(), complete[boundary:].lstrip()
        if (right[0] in CLOSING or left[-1] in OPENING
                or any(start < boundary < end for start, end, _ in spans)):
            continue
        left_width, right_width = width(left), width(right)
        if max(left_width, right_width) <= columns:
            score = abs(left_width - right_width) - (columns * .2 if left[-1] in CLOSING else 0)
            candidates.append((score, boundary, [left, right]))
    if not candidates:
        raise ValueError('保护完整术语后字幕无法在两行显示；需人工分屏，不退回词内断行')
    return min(candidates)[2]


def layout_cues(caption_path, duration, columns, forms):
    rows, changes = [], []
    legacy_split_count = 0
    for cue_id, (start, end, text) in enumerate(state.cues(caption_path), 1):
        if start >= duration:
            continue
        if any(character in text for character in '{}\\'):
            raise ValueError(f'字幕 {cue_id} 含 ASS 特殊字符，需人工核对')
        try:
            lines = wrap_caption(text, columns, forms)
            old_lines = upload_export.wrap_caption(text, columns)
        except ValueError as error:
            raise ValueError(f'字幕 {cue_id} ({start:.3f}–{end:.3f}): {error}') from error
        if ''.join(lines).replace(' ', '') != normalize(text).replace(' ', ''):
            raise ValueError(f'字幕 {cue_id} 排版丢字')
        current_splits = split_terms(text, lines, forms)
        if current_splits:
            raise ValueError(f'字幕 {cue_id} 仍有词内折行: {current_splits}')
        old_splits = split_terms(text, old_lines, forms)
        legacy_split_count += bool(old_splits)
        rendered = r'\N'.join(lines)
        rows.append(f'Dialogue: 0,{upload_export.ass_time(start)},'
                    f'{upload_export.ass_time(min(duration, end))},Default,,0,0,0,,{rendered}\n')
        if lines != old_lines:
            changes.append({'cue_id': cue_id, 'start': start, 'end': end, 'text': text,
                            'legacy_lines': old_lines, 'lines': lines, 'protected_legacy_splits': old_splits})
    if not rows:
        raise ValueError('没有可烧录字幕')
    return rows, {'cues': len(rows), 'changed_cues': len(changes), 'changes': changes,
                  'legacy_registered_term_split_cues': legacy_split_count,
                  'new_registered_term_split_cues': 0, 'timings_changed': False,
                  'caption_text_changed': False, 'complete_entity_recognition': False}


def subtitles(work, width_pixels, height_pixels, duration, position, forms, caption_path=None):
    if position not in {'top', 'bottom'}:
        raise ValueError('字幕位置必须为 top 或 bottom')
    font_size = max(18, round(height_pixels * .032))
    margin = max(12, round(min(width_pixels, height_pixels) * .035))
    columns = min(28, math.floor((width_pixels - 2 * margin) / font_size))
    if columns < 8:
        raise ValueError('画面过窄，无法保证中文字幕可读性')
    caption_path = caption_path or work / 'zh_display.srt'
    legacy = upload_export.subtitles(work, width_pixels, height_pixels, duration, position, caption_path)
    header = legacy.split('Dialogue: ', 1)[0]
    rows, report = layout_cues(caption_path, duration, columns, forms)
    legacy_rows = [line for line in legacy.splitlines() if line.startswith('Dialogue: ')]
    if (len(legacy_rows) != len(rows)
            or any(old.split(',', 9)[:9] != new.split(',', 9)[:9]
                   for old, new in zip(legacy_rows, rows))):
        raise ValueError('字幕排版改变了显示时间、样式或数量')
    return header + ''.join(rows), {'columns': columns, 'font_size': font_size, **report}


def preflight(work, terms, ass_path, report_path):
    work, terms, ass_path, report_path = (Path(path).resolve() for path in
                                          (work, terms, ass_path, report_path))
    if ass_path == report_path:
        raise ValueError('字幕和报告必须使用不同路径')
    for output in (ass_path, report_path):
        if output.exists() or not output.parent.is_dir() or output == terms:
            raise ValueError('预检需要两个不同、尚不存在的工程内部输出路径')
        if not (output.is_relative_to(work) or output.is_relative_to(work.parent / '.workflow_logs')):
            raise ValueError('字幕排版预检只允许写入隐藏工程或工作流证据目录')
    manifest = video_package.validate(work)
    before = {'implementation': state.identity(__file__), 'legacy': state.identity(upload_export.__file__),
              'terms': state.identity(terms), 'master': manifest['output'],
              'captions': manifest['subtitles']['zh'],
              'upload_manifest': state.identity(work / 'upload_export.json')}
    upload = state.load(work / 'upload_export.json')
    if not state.matches(upload['output']):
        raise ValueError('现有上传版身份已变化')
    before['upload'] = upload['output']
    stream = upload_export.probe(Path(manifest['output']['path']))
    video = next(item for item in stream['streams'] if item['codec_type'] == 'video')
    forms = load_forms(terms)
    script, report = subtitles(work, int(video['width']), int(video['height']),
                              float(stream['format']['duration']), 'bottom', forms,
                              Path(manifest['subtitles']['zh']['path']))
    if not all(state.matches(identity) for identity in before.values()) or video_package.validate(work) != manifest:
        raise ValueError('排版预检期间输入或成片发生变化')
    atomic_write(ass_path, script)
    result = {'version': 2, 'checked_at': state.now(), 'inputs': before,
              'work': str(work), 'layout': 'registered-terms-v2', 'protected_forms': len(forms),
              'ass': state.identity(ass_path), 'report': report, 'preflight_passed': True,
              'production_applied': False, 'narration_changed': False, 'quality_gates_changed': False,
              'human_listened': False, 'goal_status': 'active'}
    save_json(report_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--terms', required=True, type=Path)
    parser.add_argument('--ass', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    options = parser.parse_args()
    result = preflight(options.work, options.terms, options.ass, options.report)
    print(f'预检 {result["report"]["cues"]} 条，改善 {result["report"]["changed_cues"]} 条；尚未替换成片')


if __name__ == '__main__':
    main()
