"""独立重建真实preview4的双既有组显示转移，不调用预览变换器。"""

import json
from pathlib import Path

import caption_boundary_context as context
import caption_boundary_repair as original
import caption_boundary_repair_v2 as roles
import caption_boundary_repair_v3 as previous
import caption_boundary_repair_v4 as repair
import caption_layout
import dubbing_state as state
import prepare_review_image
import speech_groups
import upload_export
import upload_export_v3
import video_package


PREVIEW_VERSION = 4
require = original.require
EDIT_FIELDS = {'left_cue_id', 'right_cue_id', 'expected_left', 'expected_right', 'new_left_text',
    'new_right_text', 'left_synthesis_unit_ids', 'right_synthesis_unit_ids', 'speaker_id', 'transfer_text',
    'agent_context_reviewed', 'source_review_note', 'display_review_note'}
PREVIEW_FIELDS = {'version', 'kind', 'checked_at', 'work', 'implementation', 'reused_implementations', 'plan',
    'inputs', 'preview_captions', 'preview_ass', 'proof', 'layout', 'script_check', 'preview_only',
    'production_applied', 'embedded_master_subtitles_updated', 'upload_video_updated', 'tts_performed',
    'video_encoding_performed', 'word_alignment_verified', 'human_listened', 'goal_status'}


def implementations():
    return {module.__name__: state.identity(module.__file__) for module in [context, original, roles, previous,
        repair, caption_layout, state, prepare_review_image, speech_groups, upload_export, upload_export_v3, video_package]}


def strict_equal(left, right):
    return json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False, allow_nan=False)


def reconstruct(cues, edits, contexts, forms, review, review_reference):
    original.validate_cues(cues)
    require(type(edits) is list and len(edits) == 1 and type(edits[0]) is dict
        and set(edits[0]) == EDIT_FIELDS, '只重建一个完整、明确字段的真实相邻组修订')
    require(type(contexts) is list and bool(contexts) and type(forms) is list and bool(forms), '缺少真实上下文或词表')
    edit = edits[0]
    left_id, right_id = edit['left_cue_id'], edit['right_cue_id']
    require(type(left_id) is int and type(right_id) is int
        and 1 <= left_id < right_id <= len(cues) and right_id == left_id + 1, '相邻显示条编号无效')
    original_cues = [tuple(cue) for cue in cues]
    left, right = original_cues[left_id - 1], original_cues[right_id - 1]
    require(strict_equal(edit['expected_left'], left) and strict_equal(edit['expected_right'], right)
        and left[1] < right[0], '原显示条必须精确匹配并保留真实空隙')
    require(edit['agent_context_reviewed'] is True and review['agent_context_reviewed'] is True
        and review['human_listened'] is False and review['voice_used_as_role_evidence'] is False
        and roles.known_role(edit['speaker_id']) and edit['speaker_id'] == review['speaker_id']
        and all(type(edit[key]) is str and bool(edit[key].strip())
            for key in ['source_review_note', 'display_review_note', 'transfer_text']), '缺少明确角色或上下文审读')
    transfer = edit['transfer_text']
    require(transfer == transfer.strip() and left[2].endswith(transfer)
        and edit['new_left_text'] == left[2][:-len(transfer)]
        and edit['new_right_text'] == transfer + right[2], '只能逐字符移动既有后缀，不允许改写或丢字')
    for key in ['new_left_text', 'new_right_text']:
        text = edit[key]
        require(type(text) is str and bool(text.strip()) and text == text.strip()
            and not any(character in text for character in '{}\\\r\n'), '新显示文字不安全或为空')
    owners, owner_indices = [], []
    for cue, key in [(left, 'left_synthesis_unit_ids'), (right, 'right_synthesis_unit_ids')]:
        identifiers = edit[key]
        require(type(identifiers) is list and bool(identifiers)
            and all(type(identifier) is int and identifier > 0 for identifier in identifiers)
            and identifiers == sorted(set(identifiers)), '真实组编号不允许布尔值、重复或伪造合组')
        matches = [(index, owner) for index, owner in enumerate(contexts)
            if owner['display_window_seconds'][0] <= cue[0] and cue[1] <= owner['display_window_seconds'][1]]
        require(len(matches) == 1, '每条显示必须独立属于唯一原合成组')
        index, owner = matches[0]
        require(owner['unit_ids'] == identifiers and owner['role_basis'] == 'validated-registered-group'
            and owner['speaker_id'] == edit['speaker_id'], '组来源或原登记角色不符')
        owners.append(owner)
        owner_indices.append(index)
    left_owner, right_owner = owners
    require(strict_equal(owners, review['reviewed_synthesis_contexts']) and owner_indices[1] == owner_indices[0] + 1
        and left_owner['unit_ids'][-1] + 1 == right_owner['unit_ids'][0]
        and left_owner['source_window_seconds'][1] == right_owner['source_window_seconds'][0]
        and left_owner['display_window_seconds'][1] == right_owner['display_window_seconds'][0] == right[0],
        '两组必须真实相邻、原窗相接，不能移动显示起点或声音窗口')
    selected_ids = []
    for owner in owners:
        selected = [(identifier, cue) for identifier, cue in enumerate(original_cues, 1)
            if owner['display_window_seconds'][0] <= cue[0] and cue[1] <= owner['display_window_seconds'][1]]
        require(bool(selected) and ''.join(cue[2] for identifier, cue in selected) == owner['actual_group_chinese'],
            '整组原字幕必须逐字符等于已登记中文')
        selected_ids.append([identifier for identifier, cue in selected])
    require(selected_ids[0][-1] == left_id and selected_ids[1][0] == right_id, '只能转移前组末条到后组首条')
    first_display = left_owner['actual_group_chinese']
    require(first_display.endswith(transfer), '前组必须确有该后缀')
    retained = first_display[:-len(transfer)]
    require(retained.endswith('，') and left_owner['actual_spoken'] == retained[:-1] + '。'
        and transfer not in left_owner['actual_spoken']
        and right_owner['actual_spoken'] == transfer + right_owner['actual_group_chinese']
        and right_owner['actual_spoken'].startswith(edit['new_right_text']),
        '必须精确对应已经存在的整组朗读迁移，不能新创迁移或泛化标点豁免')
    before = set(original.boundary_matches(original_cues, forms))
    removed = {match for match in before if match[:2] == (left_id, right_id)}
    require(bool(removed), '该边界没有真实已登记拆词')
    repaired = list(original_cues)
    repaired[left_id - 1] = (left[0], left[1], edit['new_left_text'])
    repaired[right_id - 1] = (right[0], right[1], edit['new_right_text'])
    after = set(original.boundary_matches(repaired, forms))
    require(after == before - removed
        and ''.join(cue[2] for cue in repaired) == ''.join(cue[2] for cue in original_cues)
        and [cue[:2] for cue in repaired] == [cue[:2] for cue in original_cues], '所有字符、原时间及其他登记边界必须保持')
    metrics = []
    for identifier in [left_id, right_id]:
        old, new = original_cues[identifier - 1], repaired[identifier - 1]
        duration = old[1] - old[0]
        metrics.append({'cue_id': identifier, 'before_width_units_per_second': caption_layout.width(old[2]) / duration,
            'after_width_units_per_second': caption_layout.width(new[2]) / duration})
    proof = {'version': PREVIEW_VERSION, 'kind': repair.KIND, 'changed_cue_ids': [left_id, right_id],
        'caption_count': len(cues), 'before_literal_matches': sorted(before), 'after_literal_matches': sorted(after),
        'removed_literal_matches': sorted(removed), 'display_gap_seconds_unchanged': right[0] - left[1],
        'all_times_unchanged': True, 'all_characters_and_order_unchanged': True, 'display_metrics': metrics,
        'distinct_original_synthesis_contexts': owners, 'new_synthesis_groups_authorized': False,
        'new_singleton_roles_authorized': False, 'existing_audio_or_source_windows_changed': False,
        'word_alignment_verified': False, 'human_listened': False, 'agent_source_review': review_reference}
    return repaired, proof


def verify_preview(work, preview_path):
    work, preview_path = [Path(path).resolve(strict=True) for path in [work, preview_path]]
    reference, record = state.identity(preview_path), state.load(preview_path)
    require(type(record) is dict and set(record) == PREVIEW_FIELDS
        and type(record['version']) is int and record['version'] == PREVIEW_VERSION == repair.VERSION
        and record['kind'] == repair.KIND and record['work'] == str(work)
        and record['implementation'] == state.identity(repair.__file__)
        and record['reused_implementations'] == repair.implementations() and record['preview_only'] is True
        and all(record[key] is False for key in ['production_applied', 'embedded_master_subtitles_updated',
            'upload_video_updated', 'tts_performed', 'video_encoding_performed', 'word_alignment_verified', 'human_listened'])
        and record['goal_status'] == 'active' and all(state.matches(record[key]) for key in ['plan', 'preview_captions', 'preview_ass']),
        '只接受真实preview4及其冻结实现、完整身份和未应用状态')
    manifest, plan = video_package.validate(work), state.load(record['plan']['path'])
    require(type(plan) is dict and set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'}
        and type(plan['version']) is int and plan['version'] == PREVIEW_VERSION and plan['kind'] == repair.KIND
        and plan['project_id'] == work.name.removeprefix('.zh_work_yt_'), '真实v4计划版本或字段不符')
    review_path = Path(plan['inputs']['agent_source_review']['path'])
    inputs = repair.inputs_for(work, Path(record['inputs']['terms']['path']), manifest, review_path)
    require(strict_equal(inputs, plan['inputs']) and strict_equal(inputs, record['inputs'])
        and all(state.matches(value) for value in inputs.values()), '所有原始输入必须当前有效且精确一致')
    contexts, source_units = context.contexts_for(work, manifest['body_offset_seconds'])
    contexts = json.loads(json.dumps(contexts, ensure_ascii=False, allow_nan=False))
    review_reference, review = repair.validate_review(work, review_path, contexts)
    forms = caption_layout.load_forms(Path(inputs['terms']['path']))
    original_cues = state.cues(inputs['captions']['path'])
    repaired, proof = reconstruct(original_cues, plan['edits'], contexts, forms, review, review_reference)
    require(strict_equal(proof, record['proof']) and state.cues(record['preview_captions']['path']) == repaired
        and Path(record['preview_captions']['path']).read_text() == state.srt_text(repaired),
        '完整v4证明、全部字幕字符/空格/时间或两真实组无法独立重建')
    video = next(stream for stream in upload_export.probe(Path(inputs['master']['path']))['streams'] if stream['codec_type'] == 'video')
    script, layout = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
        manifest['duration_seconds'], forms, Path(record['preview_captions']['path']))
    require(script == Path(record['preview_ass']['path']).read_text() and strict_equal(layout, record['layout'])
        and strict_equal(upload_export_v3.verify_script(script, Path(record['preview_captions']['path']),
            manifest['duration_seconds']), record['script_check']) and state.matches(reference), '完整ASS及布局无法重建')
    return {'preview': record, 'preview_reference': reference, 'manifest': manifest, 'original': original_cues,
        'repaired': repaired, 'forms': forms, 'proof_validation': {'implementation': state.identity(__file__),
            'reused_implementations': implementations(), 'admitted_preview_format_version': PREVIEW_VERSION,
            'canonical_proof_signature': state.signature(proof), 'all_proof_fields_strictly_compared': True,
            'proof_set_order_normalized': False, 'separate_original_synthesis_groups': 2,
            'preview_transform_or_plan_validator_called': False,
            'frozen_source_review_and_input_helpers_reused': True}}
