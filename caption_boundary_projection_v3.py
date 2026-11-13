"""独立重建真实preview4及双原组证明，允许四文件归档路由而不重新标记来源。"""

import json
from pathlib import Path

import caption_boundary_context as context
import caption_boundary_master as master_helpers
import caption_boundary_proof_v2 as proof
import caption_boundary_repair_v4 as repair
import caption_layout
import dubbing_state as state
import paired_delivery_v5 as helpers
import upload_export
import upload_export_v3


require = helpers.require


def module_identities():
    return {**proof.implementations(), **{module.__name__: state.identity(module.__file__) for module in
        [context, master_helpers, proof, repair, caption_layout, state, helpers, upload_export, upload_export_v3]}}


def text_projection(work, preview, master, base, original, manifest_reference, resolve=helpers.current_reference):
    inputs = preview['inputs']
    expected = {'master': original['master'], 'upload': original['upload'], 'captions': base['subtitles']['zh'],
        'package_render_state': original['render_state'], 'inner_package_manifest': manifest_reference,
        'terms': inputs['terms'], 'upload_manifest': original['upload_manifest'],
        **{name: state.identity(work / filename) for name, filename in {
            'speech_groups': 'speech_groups.json', 'narration': 'narration.json', 'source_captions': 'en.srt',
            'source_translations': 'zh.srt', 'source_spoken': 'spoken.srt', 'source_voices': 'voices.json',
            'spoken_overrides': 'spoken_overrides.json', 'group_review_notes': 'speech_group_review_notes.json'}.items()},
        'agent_source_review': inputs['agent_source_review']}
    plan = helpers.read_reference(preview['plan'], resolve)
    require(type(preview) is dict and set(preview) == proof.PREVIEW_FIELDS
        and type(plan) is dict and set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'}
        and type(plan['version']) is int and type(preview['version']) is int and plan['version'] == preview['version'] == 4
        and plan['kind'] == preview['kind'] == repair.KIND and preview['work'] == str(work)
        and plan['project_id'] == work.name.removeprefix('.zh_work_yt_')
        and proof.strict_equal(inputs, expected) and proof.strict_equal(inputs, plan['inputs'])
        and preview['implementation'] == state.identity(repair.__file__)
        and preview['reused_implementations'] == repair.implementations() and preview['preview_only'] is True
        and all(preview[name] is False for name in ['production_applied', 'embedded_master_subtitles_updated',
            'upload_video_updated', 'tts_performed', 'video_encoding_performed', 'word_alignment_verified', 'human_listened'])
        and preview['goal_status'] == 'active', '只接受真实preview4及完整原输入，不伪装旧预览或已安装状态')
    for reference in inputs.values():
        resolve(reference)
    contexts, source_units = context.contexts_for(work, base['body_offset_seconds'])
    contexts = json.loads(json.dumps(contexts, ensure_ascii=False, allow_nan=False))
    review_reference, review = repair.validate_review(work, Path(resolve(inputs['agent_source_review'])['path']), contexts)
    require(review_reference == inputs['agent_source_review'] and bool(source_units), '原来源审校身份必须保留')
    forms = caption_layout.load_forms(resolve(inputs['terms'])['path'])
    original_cues = state.cues(resolve(base['subtitles']['zh'])['path'])
    repaired, reconstructed = proof.reconstruct(original_cues, plan['edits'], contexts, forms, review, review_reference)
    require(proof.strict_equal(reconstructed, preview['proof'])
        and repaired == state.cues(resolve(preview['preview_captions'])['path'])
            == state.cues(resolve(master['subtitles']['zh'])['path'])
        and Path(resolve(master['subtitles']['zh'])['path']).read_text() == state.srt_text(repaired)
        and proof.strict_equal(helpers.read_reference(master['text_preview'], resolve), preview)
        and master['subtitles']['en'] == base['subtitles']['en'] and master['audio_windows_changed'] is False,
        '完整独立双组证明、全部中文字符/时间或原英文变化')
    expected_validation = {'implementation': state.identity(proof.__file__), 'reused_implementations': proof.implementations(),
        'admitted_preview_format_version': 4, 'canonical_proof_signature': state.signature(reconstructed),
        'all_proof_fields_strictly_compared': True, 'proof_set_order_normalized': False,
        'separate_original_synthesis_groups': 2, 'preview_transform_or_plan_validator_called': False,
        'frozen_source_review_and_input_helpers_reused': True}
    require(proof.strict_equal(master['proof_validation'], expected_validation)
        and proof.strict_equal(master['distinct_original_synthesis_contexts'], reconstructed['distinct_original_synthesis_contexts']),
        '母版必须保留真实独立证明及两个分开的原声音窗')
    bilingual = master_helpers.project_bilingual(original_cues, repaired,
        state.cues(resolve(base['subtitles']['bilingual'])['path']))
    require(bilingual == state.cues(resolve(master['subtitles']['bilingual'])['path'])
        and master['english_in_bilingual_preserved'] is True, '全部双语英文后缀及原时间必须保持')
    video = next(stream for stream in upload_export.probe(Path(resolve(original['master'])['path']))['streams']
        if stream['codec_type'] == 'video')
    script, layout = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
        base['duration_seconds'], forms, Path(resolve(master['subtitles']['zh'])['path']))
    require(script == Path(resolve(preview['preview_ass'])['path']).read_text()
        and proof.strict_equal(layout, preview['layout'])
        and proof.strict_equal(upload_export_v3.verify_script(script, Path(resolve(master['subtitles']['zh'])['path']),
            base['duration_seconds']), preview['script_check']), '完整ASS、安全带和全部显示必须独立重建')
    return forms
