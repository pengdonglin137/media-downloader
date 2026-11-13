"""精确重建既有语义组显示域v3预览，保留原生v6/revision4母版身份。"""

from pathlib import Path

import caption_boundary_master as master_helpers
import caption_boundary_repair_v3 as repair
import caption_layout
import dubbing_state as state
import paired_delivery_v5 as helpers


require = helpers.require


def module_identities():
    return {module.__name__: state.identity(module.__file__) for module in
        [master_helpers, repair, caption_layout, state, helpers]}


def text_projection(work, preview, master, base, original, manifest_reference, resolve=helpers.current_reference):
    inputs = preview['inputs']
    expected = {'master': original['master'], 'upload': original['upload'], 'captions': base['subtitles']['zh'],
        'package_render_state': original['render_state'], 'inner_package_manifest': manifest_reference,
        'terms': inputs['terms'], 'speech_groups': state.identity(work / 'speech_groups.json'),
        'narration': state.identity(work / 'narration.json'), 'source_captions': state.identity(work / 'en.srt'),
        'upload_manifest': original['upload_manifest']}
    plan = helpers.read_reference(preview['plan'], resolve)
    project_id = work.name.removeprefix('.zh_work_yt_').removeprefix('.zh_work_')
    require(set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'}
        and type(plan['version']) is int and type(preview['version']) is int
        and plan['version'] == preview['version'] == repair.VERSION == 3
        and plan['kind'] == preview['kind'] == repair.KIND and preview['work'] == str(work)
        and state.signature(inputs) == state.signature(expected) == state.signature(plan['inputs'])
        and plan['project_id'] == project_id and preview['implementation'] == state.identity(repair.__file__)
        and preview['reused_implementations'] == repair.implementations()
        and preview['preview_only'] is True and preview['production_applied'] is False,
        '真实v3既有语义组预览的输入、类型、来源或实现变化')
    for reference in inputs.values():
        resolve(reference)
    forms = caption_layout.load_forms(inputs['terms']['path'])
    original_cues = state.cues(base['subtitles']['zh']['path'])
    repaired, rebuilt = repair.repartition(original_cues, plan['edits'],
        repair.contexts_for(work, base['body_offset_seconds']), forms)
    require(state.signature(rebuilt) == state.signature(preview['proof'])
        and rebuilt['new_singleton_roles_authorized'] is False
        and repaired == state.cues(preview['preview_captions']['path']) == state.cues(master['subtitles']['zh']['path'])
        and Path(master['subtitles']['zh']['path']).read_text() == state.srt_text(repaired)
        and state.signature(helpers.read_reference(master['text_preview'], resolve)) == state.signature(preview)
        and master['subtitles']['en'] == base['subtitles']['en'] and master['audio_windows_changed'] is False
        and 'role_registry' not in master and 'proof_validation' not in master,
        '完整v3证明、全部文字时间、英文或真实母版字段变化')
    bilingual = master_helpers.project_bilingual(original_cues, repaired,
        state.cues(base['subtitles']['bilingual']['path']))
    require(bilingual == state.cues(master['subtitles']['bilingual']['path'])
        and master['english_in_bilingual_preserved'] is True, '双语英文后缀或中文修订不一致')
    return forms
