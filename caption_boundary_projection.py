"""从真实v2角色预览重建字幕，只规范化已登记命中集合的顺序。"""

from pathlib import Path

import caption_boundary_master as master_helpers
import caption_boundary_proof as proof_validation
import caption_boundary_repair_v2 as repair
import caption_layout
import dubbing_state as state
import paired_delivery_v5 as helpers


require = helpers.require


def module_identities():
    return {module.__name__: state.identity(module.__file__) for module in
        [master_helpers, proof_validation, repair, caption_layout, state, helpers]}


def text_projection(work, preview, master, base, original, manifest_reference, resolve=helpers.current_reference):
    inputs = preview['inputs']
    expected = {'master': original['master'], 'upload': original['upload'], 'captions': base['subtitles']['zh'],
        'package_render_state': original['render_state'], 'inner_package_manifest': manifest_reference,
        'terms': inputs['terms'], 'speech_groups': state.identity(work / 'speech_groups.json'),
        'narration': state.identity(work / 'narration.json'), 'source_captions': state.identity(work / 'en.srt'),
        'upload_manifest': original['upload_manifest'], 'role_registry': inputs['role_registry'],
        **{'role_' + name: reference for name, reference in repair.role_inputs_for(work).items()}}
    plan = helpers.read_reference(preview['plan'], resolve)
    require(set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'}
        and type(plan['version']) is int and type(preview['version']) is int
        and plan['version'] == preview['version'] == repair.VERSION == 2
        and plan['kind'] == preview['kind'] == repair.KIND
        and state.signature(inputs) == state.signature(expected) == state.signature(plan['inputs'])
        and plan['project_id'] == repair.project_id(work) and preview['implementation'] == state.identity(repair.__file__)
        and preview['reused_implementations'] == repair.implementations(), '真实v2角色预览输入或实现变化')
    forms = caption_layout.load_forms(inputs['terms']['path'])
    original_cues = state.cues(base['subtitles']['zh']['path'])
    repaired, rebuilt = repair.repartition(original_cues, plan['edits'],
        repair.contexts_for(work, base['body_offset_seconds'], inputs['role_registry']['path']), forms)
    signature = state.signature(proof_validation.canonical_proof(rebuilt))
    require(signature == state.signature(proof_validation.canonical_proof(preview['proof']))
        and repaired == state.cues(preview['preview_captions']['path']) == state.cues(master['subtitles']['zh']['path'])
        and Path(master['subtitles']['zh']['path']).read_text() == state.srt_text(repaired)
        and master['subtitles']['en'] == base['subtitles']['en'] and master['role_registry'] == inputs['role_registry']
        and master['audio_windows_changed'] is False, '严格类型证明、全部文字时间或原声音窗变化')
    require(state.signature(master['proof_validation']) == state.signature({'implementation': state.identity(proof_validation.__file__),
        'legacy_preview_format_version': 2, 'canonical_proof_signature': signature,
        'only_order_normalized': '/reviews/*/resolved_literal_matches',
        'all_members_and_multiplicities_checked': True, 'all_other_fields_compared_exactly': True}),
        '母版必须保留真实v6/revision4的原证明验证来源')
    bilingual = master_helpers.project_bilingual(original_cues, repaired, state.cues(base['subtitles']['bilingual']['path']))
    require(bilingual == state.cues(master['subtitles']['bilingual']['path'])
        and master['english_in_bilingual_preserved'] is True, '双语字幕英文或中文修订不一致')
    return forms
