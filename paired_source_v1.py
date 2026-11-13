"""原生v1上传来源及严格既有语义组预览：支持YouTube和X，不伪装角色登记。"""

from pathlib import Path

import caption_boundary_master as master_helpers
import caption_boundary_repair as repair
import caption_layout
import dubbing_state as state
import paired_delivery_transaction as files
import paired_delivery_v5 as helpers
import video_package


require = helpers.require


def project_id(work):
    name = Path(work).name
    require(name.startswith('.zh_work_'), '需要真实隐藏视频工程')
    return name.removeprefix('.zh_work_yt_').removeprefix('.zh_work_')


def module_identities():
    return {'source_validator': state.identity(__file__), **{name: state.identity(module.__file__)
        for name, module in {'state': state, 'files': files, 'package_helpers': helpers,
            'text_repair': repair, 'layout': caption_layout, 'master_helpers': master_helpers,
            'package_verifier': video_package}.items()}}


def validate(work, original, manifest_reference, bindings, resolve=helpers.current_reference):
    work = files.canonical(work)
    files.destinations(work, original)
    for reference in original.values():
        resolve(reference)
    base, rendering = helpers.base_package(work, original, manifest_reference, resolve)
    upload = helpers.base_upload(work, original, base, manifest_reference, bindings, resolve)
    require(type(upload['version']) is int and upload['version'] == 1, '只接受真实整数v1上传来源')
    return {'base': base, 'rendering': rendering, 'upload': upload, 'source_upload_version': 1}


def text_projection(work, preview, master, base, original, manifest_reference):
    inputs = preview['inputs']
    expected = {'master': original['master'], 'upload': original['upload'], 'captions': base['subtitles']['zh'],
        'package_render_state': original['render_state'], 'inner_package_manifest': manifest_reference,
        'terms': inputs['terms'], 'speech_groups': state.identity(work / 'speech_groups.json'),
        'narration': state.identity(work / 'narration.json'), 'source_captions': state.identity(work / 'en.srt'),
        'upload_manifest': original['upload_manifest']}
    plan = helpers.read_reference(preview['plan'])
    require(set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'}
        and type(plan['version']) is int and plan['version'] == preview['version'] == repair.VERSION
        and plan['kind'] == preview['kind'] == repair.KIND and inputs == expected and plan['inputs'] == inputs
        and plan['project_id'] == project_id(work) and preview['implementation'] == state.identity(repair.__file__),
        '需要真实v1严格既有组预览；不新增角色输入或改变工程身份')
    forms = caption_layout.load_forms(inputs['terms']['path'])
    original_cues = state.cues(base['subtitles']['zh']['path'])
    repaired, proof = repair.repartition(original_cues, plan['edits'],
        repair.contexts_for(work, base['body_offset_seconds']), forms)
    require(state.signature(proof) == state.signature(preview['proof'])
        and repaired == state.cues(preview['preview_captions']['path']) == state.cues(master['subtitles']['zh']['path'])
        and Path(master['subtitles']['zh']['path']).read_text() == state.srt_text(repaired)
        and master['subtitles']['en'] == base['subtitles']['en'], '文字、时间或严格原声音窗变化')
    bilingual = master_helpers.project_bilingual(original_cues, repaired, state.cues(base['subtitles']['bilingual']['path']))
    require(bilingual == state.cues(master['subtitles']['bilingual']['path'])
        and master['english_in_bilingual_preserved'] is True, '双语英文或中文投影变化')
    return forms
