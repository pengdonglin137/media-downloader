"""只读检查已登记专名是否跨越有效配音计划切点，不自动合并或判定发音。"""

import argparse
import bisect
import json
from pathlib import Path

import dubbing_state as state
import speech_groups


PAUSE_MARKS = '，。；：、！？,.!?;:'
DEPENDENCIES = ['compilation.json', 'en.srt', 'zh.srt', 'spoken.srt', 'voices.json',
                'tts_voice.txt', 'speech_groups.json', 'narration.json']


def compact(text):
    return ''.join(character for character in text if not character.isspace())


def validate_terms(terms):
    if not isinstance(terms, list) or not terms:
        raise ValueError('需要显式登记待检查的专名与朗读写法')
    names = set()
    for entry in terms:
        if not isinstance(entry, dict) or set(entry) != {'name', 'spoken_forms'}:
            raise ValueError('每个专名必须包含 name 和 spoken_forms')
        name, forms = entry['name'], entry['spoken_forms']
        if (not isinstance(name, str) or not name.strip() or name in names
                or not isinstance(forms, list) or not forms
                or any(not isinstance(form, str) or not compact(form) for form in forms)):
            raise ValueError('专名及其朗读写法必须非空，专名不能重复')
        names.add(name)
    return terms


def audit_boundaries(entries, terms):
    validate_terms(terms)
    if not entries:
        raise ValueError('缺少配音合成项，不能生成空通过报告')
    candidates, observed = {}, {entry['name']: set() for entry in terms}
    for mode in ['whitespace_only', 'boundary_pause_marks_ignored']:
        parts = [compact(entry['spoken']) for entry in entries]
        if mode == 'boundary_pause_marks_ignored':
            parts = [part.strip(PAUSE_MARKS) for part in parts]
        if any(not part for part in parts):
            raise ValueError('存在空白或仅标点的合成项，不能忽略该切点')
        offsets = [0]
        for part in parts:
            offsets.append(offsets[-1] + len(part))
        text = ''.join(parts)
        for term in terms:
            for form in dict.fromkeys(compact(form) for form in term['spoken_forms']):
                cursor = 0
                while (start := text.find(form, cursor)) >= 0:
                    end = start + len(form)
                    first = bisect.bisect_right(offsets, start) - 1
                    last = bisect.bisect_left(offsets, end) - 1
                    observed[term['name']].add((form, first, last))
                    if first != last:
                        key = (term['name'], form, first, last)
                        if key not in candidates:
                            selected = entries[first:last + 1]
                            candidates[key] = {
                                'term': term['name'], 'spoken_form': form, 'comparison_mode': mode,
                                'synthesis_unit_ids': [entry['unit_ids'] for entry in selected],
                                'spoken': [entry['spoken'] for entry in selected],
                                'voices_differ': len({entry['voice'] for entry in selected}) > 1,
                                'automatic_error_verdict': False,
                                'action': '核对来源、讲者、语义与时窗；不自动跨人合并或删停顿'}
                    cursor = start + 1
    return {'synthesis_entries': len(entries), 'checked_terms': terms,
            'observed_terms': {name: len(matches) for name, matches in observed.items()},
            'cross_synthesis_candidates': list(candidates.values()),
            'complete_entity_recognition': False, 'human_listened': False,
            'scope': '只检查登记写法。忽略空白或块首尾句读仅用于候选比较，不改朗读、字幕或波形；不证明块内发音正确。'}


def inspect_work(work, terms):
    work = Path(work)
    state.validate_compilation(work)
    narration = state.validate_narration(work)
    dependencies = state.hashes(work, DEPENDENCIES)
    result = audit_boundaries(speech_groups.units(work), terms)
    if state.hashes(work, DEPENDENCIES) != dependencies:
        raise ValueError('检查期间工程发生变化，拒绝登记结果')
    return {'created_at': state.now(), 'work': str(work.resolve()),
            'implementation_sha256': state.digest(__file__), 'dependencies': dependencies,
            'narration_origin': narration['origin'],
            'historical_generation_proven': narration.get('historical_generation_proven', False),
            'boundary_basis': '当前有效编译与语义组计划；不是从波形识别出的词边界', **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--terms', required=True, type=Path)
    arguments = parser.parse_args()
    result = inspect_work(arguments.work, state.load(arguments.terms))
    result['terms_file'] = state.identity(arguments.terms)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['cross_synthesis_candidates'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
