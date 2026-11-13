"""显式审校的跨字幕条文字重分配预览，不修改声音、时间或正式成片。"""

import argparse
import copy
import fcntl
import math
from pathlib import Path

import caption_layout
import dubbing_state as state
import speech_groups
import upload_export
import upload_export_v3
import video_package
from prepare_videos import atomic_write, save_json


VERSION = 1
KIND = 'reviewed-display-boundary-repartition-v1'
EDIT_FIELDS = {'left_cue_id', 'right_cue_id', 'expected_left', 'expected_right', 'new_left_text',
               'new_right_text', 'synthesis_unit_ids', 'speaker_id', 'agent_context_reviewed',
               'source_review_note', 'display_review_note'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def boundary_matches(cues, forms):
    matches = set()
    registered = set(forms)
    for cue_id, (left, right) in enumerate(zip(cues, cues[1:]), 1):
        left_text, right_text = [caption_layout.normalize(cue[2]) for cue in [left, right]]
        for separator in ['', ' ']:
            combined = left_text + separator + right_text
            for start, end, term in caption_layout.protected_spans(combined, forms):
                if term in registered and start < len(left_text) and end > len(left_text) + len(separator):
                    matches.add((cue_id, cue_id + 1, term, combined[start:len(left_text)],
                                 combined[len(left_text) + len(separator):end]))
    return sorted(matches)


def validate_cues(cues):
    require(isinstance(cues, list) and bool(cues), '需要非空字幕清单')
    previous_end = 0
    for cue in cues:
        require(isinstance(cue, (list, tuple)) and len(cue) == 3, '字幕结构无效')
        start, end, text = cue
        require(all(type(value) in {int, float} and math.isfinite(value) for value in [start, end])
                and previous_end <= start < end and isinstance(text, str) and bool(text.strip())
                and not any(character in text for character in '{}\\'), '字幕时间、文字或ASS字符无效')
        previous_end = end


def repartition(cues, edits, contexts, forms):
    validate_cues(cues)
    cues = [tuple(cue) for cue in cues]
    require(isinstance(edits, list) and bool(edits), '需要明确、非空的逐项审校修订')
    require(isinstance(contexts, list) and bool(contexts), '缺少实际朗读与角色上下文')
    require(isinstance(forms, list) and bool(forms)
            and all(isinstance(form, str) and bool(form.strip()) for form in forms), '缺少登记术语')
    result = copy.deepcopy(cues)
    before = set(boundary_matches(cues, forms))
    used = set()
    addressed = set()
    reviews = []
    for edit in edits:
        require(isinstance(edit, dict) and set(edit) == EDIT_FIELDS, '修订字段缺失或未知')
        left_id, right_id = edit['left_cue_id'], edit['right_cue_id']
        require(type(left_id) is int and type(right_id) is int and 1 <= left_id < right_id <= len(cues)
                and right_id == left_id + 1 and not used.intersection([left_id, right_id]),
                '只接受不重叠的相邻字幕对；复杂连锁修订需另行审校')
        left, right = cues[left_id - 1], cues[right_id - 1]
        require(edit['expected_left'] == list(left) and edit['expected_right'] == list(right), '原字幕文字或时间已变化')
        require(left[1] == right[0], '不跨字幕空隙重分配文字')
        require(edit['agent_context_reviewed'] is True and all(isinstance(edit[key], str) and bool(edit[key].strip())
                for key in ['speaker_id', 'source_review_note', 'display_review_note']), '缺少逐项角色或语义审读')
        require(not edit['speaker_id'].lower().startswith(('unknown', 'unverified', 'uncertain', '未知')),
                '角色不明不得自动重分配')
        for key in ['new_left_text', 'new_right_text']:
            text = edit[key]
            require(isinstance(text, str) and bool(text.strip()) and text == text.strip()
                    and not any(character in text for character in '{}\\\r\n'), '新字幕必须为非空安全单行文字')
        require(left[2] + right[2] == edit['new_left_text'] + edit['new_right_text'],
                '相邻字幕组合的全部字符和顺序必须完全一致，包括空格和标点')
        require(left[2] != edit['new_left_text'] and right[2] != edit['new_right_text'], '禁止无效修订')
        identifiers = edit['synthesis_unit_ids']
        require(isinstance(identifiers, list) and bool(identifiers)
                and all(type(number) is int and number > 0 for number in identifiers)
                and identifiers == sorted(set(identifiers)), '实际朗读单元编号无效')
        owners = [context for context in contexts if context['start'] <= left[0]
                  and right[1] <= context['end']]
        require(len(owners) == 1 and owners[0]['unit_ids'] == identifiers
                and owners[0]['speaker_id'] == edit['speaker_id'], '必须属于同一已审校讲者的实际合成窗口')
        old_matches = [entry for entry in before if entry[:2] == (left_id, right_id)]
        require(bool(old_matches), '指定边界没有登记术语拆分，不能顺带改写')
        result[left_id - 1] = (left[0], left[1], edit['new_left_text'])
        result[right_id - 1] = (right[0], right[1], edit['new_right_text'])
        used.update([left_id, right_id])
        addressed.add((left_id, right_id))
        reviews.append({'left_cue_id': left_id, 'right_cue_id': right_id, 'resolved_literal_matches': old_matches,
            'synthesis_context': owners[0], 'source_review_note': edit['source_review_note'],
            'display_review_note': edit['display_review_note'], 'agent_context_reviewed': True})
    after = set(boundary_matches(result, forms))
    require(after == {entry for entry in before if entry[:2] not in addressed},
            '目标拆词未消除，出现新拆词，或未授权边界的术语匹配发生变化')
    require(len(result) == len(cues) and [cue[:2] for cue in result] == [cue[:2] for cue in cues]
            and ''.join(cue[2] for cue in result) == ''.join(cue[2] for cue in cues), '完整时间线或文字序列不守恒')
    return result, {'version': VERSION, 'kind': KIND, 'cue_count': len(cues), 'changed_cue_ids': sorted(used),
        'repaired_boundary_count': len(addressed), 'before_literal_matches': sorted(before),
        'after_literal_matches': sorted(after), 'reviews': reviews, 'all_timestamps_identical': True,
        'exact_combined_characters_identical': True, 'untargeted_cues_identical': all(
            result[index] == cue for index, cue in enumerate(cues) if index + 1 not in used),
        'word_alignment_verified': False, 'human_sync_approved': False, 'complete_entity_recognition': False,
        'production_applied': False, 'narration_changed': False, 'human_listened': False}


def contexts_for(work, offset):
    policy = speech_groups.load_policy(work)
    require(policy is not None, '实际角色上下文需要有效、已审校的语义组策略')
    speakers = {tuple(group['unit_ids']): group['speaker_id'] for group in policy['groups']}
    return [{'unit_ids': unit['unit_ids'], 'speaker_id': speakers.get(tuple(unit['unit_ids']), 'unknown'),
        'start': unit['start'] + offset, 'end': unit['end'] + offset, 'voice': unit['voice']}
        for unit in speech_groups.units(work)]


def inputs_for(work, terms, manifest):
    rendering = state.load(work / 'render_state.json')
    upload = state.load(work / 'upload_export.json')
    return {'master': manifest['output'], 'upload': upload['output'], 'captions': manifest['subtitles']['zh'],
        'package_render_state': state.identity(work / 'render_state.json'),
        'inner_package_manifest': rendering['package_manifest'], 'terms': state.identity(terms),
        'speech_groups': state.identity(work / 'speech_groups.json'), 'narration': state.identity(work / 'narration.json'),
        'source_captions': state.identity(work / 'en.srt'), 'upload_manifest': state.identity(work / 'upload_export.json')}


def preview(work, terms, plan_path, output_directory):
    work, terms, plan_path = [Path(path).resolve(strict=True) for path in [work, terms, plan_path]]
    output_directory = Path(output_directory)
    require(output_directory.is_absolute() and not output_directory.exists()
            and output_directory.parent.resolve(strict=True).is_relative_to(work.parent / '.workflow_logs'),
            '只能在隐藏工作流目录创建全新的文字预览，不覆盖已有输出')
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        manifest = video_package.validate(work)
        inputs = inputs_for(work, terms, manifest)
        require(all(state.matches(value) for value in inputs.values()), '当前工程输入或媒体身份无效')
        plan_reference = state.identity(plan_path)
        plan = state.load(plan_path)
        require(set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'} and type(plan['version']) is int
                and plan['version'] == VERSION and plan['kind'] == KIND and plan['inputs'] == inputs
                and plan['project_id'] == work.name.removeprefix('.zh_work_yt_').removeprefix('.zh_work_'),
                '审校计划版本、工程或绑定输入不匹配')
        forms = caption_layout.load_forms(terms)
        original = state.cues(inputs['captions']['path'])
        repaired, proof = repartition(original, plan['edits'], contexts_for(work, manifest['body_offset_seconds']), forms)
        video = next(stream for stream in upload_export.probe(Path(inputs['master']['path']))['streams']
                     if stream['codec_type'] == 'video')
        output_directory.mkdir()
        captions = output_directory / 'repartitioned-zh.srt'
        atomic_write(captions, state.srt_text(repaired))
        require(state.cues(captions) == repaired, 'SRT序列化未保留修订时间或文字')
        script, layout = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
            manifest['duration_seconds'], forms, captions)
        script_check = upload_export_v3.verify_script(script, captions, manifest['duration_seconds'])
        ass = output_directory / 'repartitioned-preview.ass'
        atomic_write(ass, script)
        require(all(state.matches(value) for value in inputs.values()) and state.matches(plan_reference)
                and video_package.validate(work) == manifest, '预览期间工程输入或媒体发生变化')
        result = {'version': VERSION, 'kind': KIND, 'checked_at': state.now(), 'work': str(work),
            'implementation': state.identity(__file__), 'plan': plan_reference, 'inputs': inputs,
            'preview_captions': state.identity(captions), 'preview_ass': state.identity(ass),
            'proof': proof, 'layout': layout, 'script_check': script_check,
            'reused_layout_implementations': [state.identity(module.__file__) for module in
                [caption_layout, upload_export, upload_export_v3]], 'preview_only': True,
            'embedded_master_subtitles_updated': False, 'upload_video_updated': False,
            'production_applied': False, 'tts_performed': False, 'video_encoding_performed': False,
            'word_alignment_verified': False, 'human_listened': False, 'goal_status': 'active'}
        save_json(output_directory / 'preview-proof.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--terms', required=True, type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--preview-directory', required=True, type=Path)
    arguments = parser.parse_args()
    result = preview(arguments.work, arguments.terms, arguments.plan, arguments.preview_directory)
    print(f'文字预览：{result["proof"]["repaired_boundary_count"]}个跨条边界，正式双版本尚未修改')


if __name__ == '__main__':
    main()
