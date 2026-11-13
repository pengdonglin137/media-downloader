"""将已审校且已在既有朗读中移到后组的专名片段同步到显示字幕。"""

import argparse
import copy
import fcntl
import json
from pathlib import Path

import caption_boundary_context as context
import caption_boundary_repair as original
import caption_boundary_repair_v3 as previous
import caption_layout
import dubbing_state as state
import speech_groups
import upload_export
import upload_export_v3
import video_package
from prepare_videos import atomic_write, save_json


VERSION = 4
KIND = 'reviewed-existing-spoken-adjacent-group-display-transfer-v4'
require = original.require
EDIT_FIELDS = {'left_cue_id', 'right_cue_id', 'expected_left', 'expected_right', 'new_left_text',
    'new_right_text', 'left_synthesis_unit_ids', 'right_synthesis_unit_ids', 'speaker_id', 'transfer_text',
    'agent_context_reviewed', 'source_review_note', 'display_review_note'}


def implementations():
    return {module.__name__: state.identity(module.__file__) for module in [context, original, previous,
        caption_layout, state, speech_groups, upload_export, upload_export_v3, video_package]}


def inputs_for(work, terms, manifest, review_path):
    return {**previous.inputs_for(work, terms, manifest),
        **{name: state.identity(work / filename) for name, filename in {
            'source_translations': 'zh.srt', 'source_spoken': 'spoken.srt', 'source_voices': 'voices.json',
            'spoken_overrides': 'spoken_overrides.json', 'group_review_notes': 'speech_group_review_notes.json'}.items()},
        'agent_source_review': state.identity(review_path)}


def validate_review(work, path, contexts):
    reference, review = state.identity(path), state.load(path)
    require(review.get('version') == 1 and type(review['version']) is int
        and review.get('kind') == 'reviewed-gapped-existing-spoken-transfer'
        and review.get('work') == str(work) and review.get('agent_context_reviewed') is True
        and review.get('human_listened') is False and review.get('voice_used_as_role_evidence') is False,
        '需要本工程明确的源文、画面及既有朗读审校，不能以音色或听辨推断角色')
    for key in ['source_context', 'prepared_source_images']:
        require(state.matches(review[key]), '源文或画面取证身份变化')
    prepared = state.load(review['prepared_source_images']['path'])
    require(review['source_images_reviewed'] and len(review['source_images_reviewed']) == len(prepared['images']),
        '必须逐张审读已声明的来源画面')
    for image, evidence in zip(review['source_images_reviewed'], prepared['images']):
        require(image['image'] == evidence['image'] and image['preparation'] == evidence['preparation']
            and state.matches(image['image']) and state.matches(image['preparation'])
            and image['agent_viewed'] is True and type(image['note']) is str and bool(image['note'].strip())
            and image['image']['bytes'] <= 98304, '缺少实际受限JPEG逐张审读或原身份')
        bitmap = next(stream for stream in upload_export.probe(Path(image['image']['path']))['streams']
            if stream['codec_type'] == 'video')
        require(bitmap['codec_name'] == 'mjpeg' and max(int(bitmap['width']), int(bitmap['height'])) <= 1280,
            '取证JPEG尺寸或格式无效')
        preparation = state.load(image['preparation']['path'])
        require(preparation['source_sha256'] == prepared['source']['sha256'], '取证画面来源不符')
    require(state.matches(prepared['source']) and prepared['source'] == state.identity(state.source_video(work))
        and sum(image['image']['bytes'] for image in review['source_images_reviewed']) <= 393216,
        '来源或累计取证图片载荷超限')
    selected = review['reviewed_synthesis_contexts']
    require(type(selected) is list and len(selected) == 2
        and all(entry in contexts for entry in selected)
        and selected[0]['speaker_id'] == selected[1]['speaker_id'] == review['speaker_id']
        and previous.display_domain.known_role(review['speaker_id']), '只能使用两个真实既有组的明确同讲者')
    originals = speech_groups.original_units(work)
    identifiers = sorted({identifier for entry in selected for identifier in entry['unit_ids']})
    required = list(range(max(1, identifiers[0] - 1), min(len(originals), identifiers[-1] + 1) + 1))
    require(review['reviewed_source_ids'] == required
        and review['reviewed_source_units'] == [originals[identifier - 1] for identifier in required],
        '必须审读完整两组和相邻英文、中文、朗读及原窗，不接受孤立词语')
    for selected_context in selected:
        start, end = selected_context['source_window_seconds']
        require(any(start <= evidence['source_time_seconds'] < end for evidence in prepared['images']),
            '两个实际组都需要独立原片画面')
    require(state.matches(reference), '读取期间源审校发生变化')
    return reference, review


def repartition(cues, edits, contexts, forms, review):
    original.validate_cues(cues)
    cues = [tuple(cue) for cue in cues]
    require(type(edits) is list and len(edits) == 1, '当前契约只接受一个逐项审读的相邻组显示边界')
    edit = edits[0]
    require(type(edit) is dict and set(edit) == EDIT_FIELDS, '修订字段缺失或未知')
    left_id, right_id = edit['left_cue_id'], edit['right_cue_id']
    require(type(left_id) is int and type(right_id) is int
        and 1 <= left_id < right_id <= len(cues) and right_id == left_id + 1, '必须为真实相邻字幕编号')
    left, right = cues[left_id - 1], cues[right_id - 1]
    require(edit['expected_left'] == list(left) and edit['expected_right'] == list(right)
        and left[1] < right[0], '原字幕必须精确匹配，且保留既有非零显示空隙')
    require(edit['agent_context_reviewed'] is True and review['agent_context_reviewed'] is True
        and previous.display_domain.known_role(edit['speaker_id'])
        and review['human_listened'] is False and review['voice_used_as_role_evidence'] is False
        and all(type(edit[key]) is str and bool(edit[key].strip())
            for key in ['speaker_id', 'source_review_note', 'display_review_note', 'transfer_text']), '缺少逐项审校')
    transfer = edit['transfer_text']
    require(left[2].endswith(transfer) and transfer == transfer.strip()
        and edit['new_left_text'] == left[2][:-len(transfer)]
        and edit['new_right_text'] == transfer + right[2], '只允许将前条既有完整后缀移到后条起首')
    for key in ['new_left_text', 'new_right_text']:
        text = edit[key]
        require(type(text) is str and bool(text.strip()) and text == text.strip()
            and not any(character in text for character in '{}\\\r\n'), '显示文字必须为安全非空单行')
    owners = []
    for cue, identifier_key in [(left, 'left_synthesis_unit_ids'), (right, 'right_synthesis_unit_ids')]:
        identifiers = edit[identifier_key]
        require(type(identifiers) is list and bool(identifiers)
            and all(type(identifier) is int and identifier > 0 for identifier in identifiers)
            and identifiers == sorted(set(identifiers)), '必须保留真实合成单元编号，不能伪造合组')
        matches = [entry for entry in contexts if entry['display_window_seconds'][0] <= cue[0]
            and cue[1] <= entry['display_window_seconds'][1]]
        require(len(matches) == 1 and matches[0]['unit_ids'] == identifiers
            and matches[0]['role_basis'] == 'validated-registered-group'
            and matches[0]['speaker_id'] == edit['speaker_id'] == review['speaker_id'], '两个显示条各归真实、已登记同讲者组')
        owners.append(matches[0])
    left_owner, right_owner = owners
    require(owners == review['reviewed_synthesis_contexts']
        and contexts.index(right_owner) == contexts.index(left_owner) + 1
        and left_owner['unit_ids'][-1] + 1 == right_owner['unit_ids'][0]
        and left_owner['source_window_seconds'][1] == right_owner['source_window_seconds'][0]
        and left_owner['display_window_seconds'][1] == right_owner['display_window_seconds'][0] == right[0],
        '只能跨两个相接真实组的末显示条和首显示条，不改变或借用声音窗口')
    owned_cues = []
    for owner in owners:
        selected = [(index, cue) for index, cue in enumerate(cues, 1)
            if owner['display_window_seconds'][0] <= cue[0] and cue[1] <= owner['display_window_seconds'][1]]
        require(''.join(cue[2] for index, cue in selected) == owner['actual_group_chinese'], '组内原显示必须精确重建既有中文')
        owned_cues.append(selected)
    require(owned_cues[0][-1][0] == left_id and owned_cues[1][0][0] == right_id, '不允许跳过组内其他显示条转移文字')
    left_display = left_owner['actual_group_chinese'][:-len(transfer)]
    require(left_owner['actual_group_chinese'].endswith(transfer)
        and left_display.endswith('，') and left_owner['actual_spoken'] == left_display[:-1] + '。'
        and transfer not in left_owner['actual_spoken']
        and right_owner['actual_spoken'] == transfer + right_owner['actual_group_chinese']
        and right_owner['actual_spoken'].startswith(edit['new_right_text']),
        '必须精确对应已经审校且已经合成的整组朗读迁移，仅保留前组原逗号与朗读句号的既有差异')
    before = set(original.boundary_matches(cues, forms))
    addressed = {entry for entry in before if entry[:2] == (left_id, right_id)}
    require(bool(addressed), '指定边界没有已登记拆词')
    repaired = copy.deepcopy(cues)
    repaired[left_id - 1] = (*left[:2], edit['new_left_text'])
    repaired[right_id - 1] = (*right[:2], edit['new_right_text'])
    after = set(original.boundary_matches(repaired, forms))
    require(after == before - addressed and ''.join(cue[2] for cue in repaired) == ''.join(cue[2] for cue in cues)
        and [cue[:2] for cue in repaired] == [cue[:2] for cue in cues], '全字幕字符、时间及其他登记边界必须精确保留')
    metrics = [{'cue_id': identifier, 'before_width_units_per_second': caption_layout.width(before_cue[2]) / (before_cue[1] - before_cue[0]),
        'after_width_units_per_second': caption_layout.width(after_cue[2]) / (after_cue[1] - after_cue[0])}
        for identifier, before_cue, after_cue in [(left_id, left, repaired[left_id - 1]), (right_id, right, repaired[right_id - 1])]]
    return repaired, {'version': VERSION, 'kind': KIND, 'changed_cue_ids': [left_id, right_id],
        'caption_count': len(cues), 'before_literal_matches': sorted(before), 'after_literal_matches': sorted(after),
        'removed_literal_matches': sorted(addressed), 'display_gap_seconds_unchanged': right[0] - left[1],
        'all_times_unchanged': True, 'all_characters_and_order_unchanged': True, 'display_metrics': metrics,
        'distinct_original_synthesis_contexts': owners, 'new_synthesis_groups_authorized': False,
        'new_singleton_roles_authorized': False, 'existing_audio_or_source_windows_changed': False,
        'word_alignment_verified': False, 'human_listened': False}


def validate_plan(work, terms, plan_path, manifest):
    plan = state.load(plan_path)
    require(type(plan) is dict and set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'}
        and type(plan['version']) is int and plan['version'] == VERSION and plan['kind'] == KIND
        and plan['project_id'] == work.name.removeprefix('.zh_work_yt_'), '不能将新双组显示契约冒充旧版单组合约')
    contexts, source_units = context.contexts_for(work, manifest['body_offset_seconds'])
    contexts = json.loads(json.dumps(contexts, ensure_ascii=False))
    review_path = Path(plan['inputs']['agent_source_review']['path'])
    review_reference, review = validate_review(work, review_path, contexts)
    inputs = inputs_for(work, terms, manifest, review_path)
    require(plan['inputs'] == inputs and all(state.matches(reference) for reference in inputs.values()), '计划输入或实际朗读源已变化')
    forms, cues = caption_layout.load_forms(terms), state.cues(inputs['captions']['path'])
    repaired, proof = repartition(cues, plan['edits'], contexts, forms, review)
    proof['agent_source_review'] = review_reference
    return inputs, forms, cues, repaired, proof


def preview(work, terms, plan_path, output_directory):
    work, terms, plan_path = [Path(path).resolve(strict=True) for path in [work, terms, plan_path]]
    output_directory = Path(output_directory)
    require(output_directory.is_absolute() and not output_directory.exists()
        and output_directory.parent.resolve(strict=True).is_relative_to(work.parent / '.workflow_logs'), '只能创建新的隐藏候选目录')
    implementation, helpers = state.identity(__file__), implementations()
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        manifest, plan_reference = video_package.validate(work), state.identity(plan_path)
        inputs, forms, original_cues, repaired, proof = validate_plan(work, terms, plan_path, manifest)
        video = next(stream for stream in upload_export.probe(Path(inputs['master']['path']))['streams'] if stream['codec_type'] == 'video')
        output_directory.mkdir()
        captions = output_directory / 'repartitioned-zh.srt'
        atomic_write(captions, state.srt_text(repaired))
        require(state.cues(captions) == repaired, '候选序列化不得改变时间或字符')
        script, layout = upload_export_v3.subtitles(work, int(video['width']), int(video['height']), manifest['duration_seconds'], forms, captions)
        script_check = upload_export_v3.verify_script(script, captions, manifest['duration_seconds'])
        ass = output_directory / 'repartitioned-preview.ass'
        atomic_write(ass, script)
        require(all(state.matches(reference) for reference in inputs.values()) and state.matches(plan_reference)
            and state.matches(implementation) and helpers == implementations() and video_package.validate(work) == manifest,
            '候选生成期间源、正式媒体或实现发生变化')
        result = {'version': VERSION, 'kind': KIND, 'checked_at': state.now(), 'work': str(work),
            'implementation': implementation, 'reused_implementations': helpers, 'plan': plan_reference, 'inputs': inputs,
            'preview_captions': state.identity(captions), 'preview_ass': state.identity(ass), 'proof': proof,
            'layout': layout, 'script_check': script_check, 'preview_only': True, 'production_applied': False,
            'embedded_master_subtitles_updated': False, 'upload_video_updated': False,
            'tts_performed': False, 'video_encoding_performed': False, 'word_alignment_verified': False,
            'human_listened': False, 'goal_status': 'active'}
        save_json(output_directory / 'preview-proof.json', result)
        return result


def verify_preview(work, preview_path):
    work, preview_path = [Path(path).resolve(strict=True) for path in [work, preview_path]]
    reference, record = state.identity(preview_path), state.load(preview_path)
    require(type(record['version']) is int and record['version'] == VERSION and record['kind'] == KIND
        and record['work'] == str(work) and record['implementation'] == state.identity(__file__)
        and record['reused_implementations'] == implementations() and record['preview_only'] is True
        and record['production_applied'] is False and all(state.matches(record[key])
            for key in ['plan', 'preview_captions', 'preview_ass']), '预览身份、版本或明确候选状态变化')
    manifest = video_package.validate(work)
    inputs, forms, original_cues, repaired, proof = validate_plan(work, Path(record['inputs']['terms']['path']), Path(record['plan']['path']), manifest)
    require(inputs == record['inputs'] and state.signature(proof) == state.signature(record['proof'])
        and state.cues(record['preview_captions']['path']) == repaired, '全部字幕及双组来源证明必须精确重建')
    video = next(stream for stream in upload_export.probe(Path(inputs['master']['path']))['streams'] if stream['codec_type'] == 'video')
    script, layout = upload_export_v3.subtitles(work, int(video['width']), int(video['height']), manifest['duration_seconds'], forms, Path(record['preview_captions']['path']))
    require(script == Path(record['preview_ass']['path']).read_text() and layout == record['layout']
        and upload_export_v3.verify_script(script, Path(record['preview_captions']['path']), manifest['duration_seconds']) == record['script_check']
        and state.matches(reference), 'ASS、布局或时间无法完整重建')
    return {'preview': record, 'preview_reference': reference, 'manifest': manifest, 'original': original_cues, 'repaired': repaired, 'forms': forms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['work', 'terms', 'plan', 'preview-directory']:
        parser.add_argument('--' + name, required=True, type=Path)
    arguments = parser.parse_args()
    result = preview(arguments.work, arguments.terms, arguments.plan, arguments.preview_directory)
    print(state.signature(result))


if __name__ == '__main__':
    main()
