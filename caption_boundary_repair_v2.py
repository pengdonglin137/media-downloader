"""逐单元角色审校与原SRT显示域量化的跨条修订，不修改声音或正式交付。"""

import argparse
import fcntl
import math
from pathlib import Path

import caption_boundary_repair as original
import caption_layout
import check_videos
import dubbing_state as state
import prepare_review_image
import prepare_videos
import speech_groups
import upload_export
import upload_export_v3
import video_package
from prepare_videos import atomic_write, save_json


VERSION = 2
KIND = 'reviewed-display-boundary-repartition-v2'
ROLE_KIND = 'source-reviewed-singleton-display-roles-v1'
EDIT_FIELDS = original.EDIT_FIELDS
CONTEXT_FIELDS = {'unit_ids', 'speaker_id', 'start', 'end', 'voice', 'source_window_seconds',
                  'body_offset_seconds', 'audio_window_seconds', 'display_owner_srt', 'role_evidence'}
require = original.require


def project_id(work):
    return work.name.removeprefix('.zh_work_yt_').removeprefix('.zh_work_')


def implementations():
    return {module.__name__: state.identity(module.__file__) for module in
            [original, caption_layout, check_videos, state, prepare_review_image, prepare_videos,
             speech_groups, upload_export, upload_export_v3, video_package]}


def role_inputs_for(work):
    names = ['compilation.json', 'en.srt', 'zh.srt', 'spoken.srt', 'voices.json',
             'speech_groups.json', 'narration.json']
    if (work / 'tts_voice.txt').exists():
        names.append('tts_voice.txt')
    return {'source_video': state.identity(state.source_video(work)),
            **{name: state.identity(work / name) for name in names}}


def known_role(value):
    return (isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and not value.lower().startswith(('unknown', 'unverified', 'uncertain', 'auto', '未知', '不明')))


def reviewed_frame(frame, preparation, source, allowed_units):
    require(isinstance(frame, dict) and set(frame) == {'image', 'manifest', 'agent_viewed', 'observation'}
            and frame['agent_viewed'] is True and isinstance(frame['observation'], str)
            and bool(frame['observation'].strip()), '需要逐张实际查看的画面及具体观察')
    require(state.matches(frame['image']) and state.matches(frame['manifest']), '角色取证画面或清单已变化')
    record = next((entry for entry in preparation['frames']
                   if entry['frame'] == frame['image'] and entry['manifest'] == frame['manifest']), None)
    require(record is not None, '角色画面不在绑定的原片提帧收据中')
    image_path = Path(frame['image']['path'])
    with image_path.open('rb') as image:
        magic = image.read(3)
    metadata = state.load(frame['manifest']['path'])
    timestamp = metadata.get('timestamp_seconds')
    dimensions = list(prepare_review_image.probe(image_path))
    require(image_path.suffix.lower() in {'.jpg', '.jpeg'} and magic == b'\xff\xd8\xff'
            and metadata['source'] == source['path'] and metadata['source_sha256'] == source['sha256']
            and metadata['output'] == frame['image']['path'] and metadata['sha256'] == frame['image']['sha256']
            and 0 < frame['image']['bytes'] == metadata['bytes'] == record['bytes'] <= 196608
            and dimensions == metadata['dimensions'] == record['dimensions'] and max(dimensions) <= 1280
            and type(timestamp) in {int, float} and math.isfinite(timestamp)
            and timestamp == record['timestamp_seconds']
            and any(unit['start'] <= timestamp < unit['end'] for unit in allowed_units),
            '画面来源、JPEG安全限制或原片时间域无效')
    return timestamp


def validate_roles(work, registry, frame_preparation):
    require(isinstance(registry, dict) and set(registry) ==
            {'version', 'kind', 'project_id', 'inputs', 'frame_preparation', 'reviewed_units'}
            and type(registry['version']) is int and registry['version'] == 1
            and registry['kind'] == ROLE_KIND and registry['project_id'] == project_id(work)
            and registry['inputs'] == role_inputs_for(work)
            and all(state.matches(reference) for reference in registry['inputs'].values()),
            '逐单元角色审校登记与真实工程输入不匹配')
    require(state.matches(registry['frame_preparation'])
            and frame_preparation == state.load(registry['frame_preparation']['path'])
            and frame_preparation['source'] == registry['inputs']['source_video']
            and state.matches(frame_preparation['implementation'])
            and frame_preparation['image_implementation'] == state.identity(prepare_review_image.__file__),
            '原片提帧收据或实现身份失效')
    synthesis = speech_groups.units(work)
    units_by_ids = {tuple(unit['unit_ids']): unit for unit in synthesis}
    policy = speech_groups.load_policy(work)
    require(policy is not None, '需要原有语义分组策略')
    groups = {tuple(group['unit_ids']): group for group in policy['groups']}
    source_units = speech_groups.original_units(work)
    entries = registry['reviewed_units']
    require(isinstance(entries, list) and bool(entries), '需要非空逐单元角色审校')
    reviewed = {}
    entry_fields = {'unit_id', 'actual_synthesis_unit', 'speaker_id', 'anchor', 'source_context', 'frames',
                    'role_note', 'agent_context_reviewed', 'human_listened', 'voice_used_as_role_evidence'}
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == entry_fields, '逐单元角色审校字段缺失或未知')
        identifier = entry['unit_id']
        require(type(identifier) is int and 1 <= identifier <= len(source_units) and identifier not in reviewed
                and (identifier,) in units_by_ids and (identifier,) not in groups
                and entry['actual_synthesis_unit'] == units_by_ids[(identifier,)],
                '只允许真实单独合成项，不覆盖已登记组或重复审校')
        require(known_role(entry['speaker_id']) and entry['agent_context_reviewed'] is True
                and entry['human_listened'] is False and entry['voice_used_as_role_evidence'] is False
                and isinstance(entry['role_note'], str) and bool(entry['role_note'].strip()),
                '需要明确源文/画面角色审读；同音色和人耳声明不能代替证据')
        anchor = entry['anchor']
        require(isinstance(anchor, dict) and set(anchor) == {'unit_ids', 'speaker_id', 'actual_synthesis_unit'}
                and isinstance(anchor['unit_ids'], list) and bool(anchor['unit_ids'])
                and all(type(number) is int for number in anchor['unit_ids']), '角色锚点结构无效')
        anchor_unit = groups.get(tuple(anchor['unit_ids']))
        require(anchor_unit is not None and anchor['actual_synthesis_unit'] == anchor_unit
                and anchor['speaker_id'] == anchor_unit['speaker_id'] == entry['speaker_id'],
                '角色锚点必须是同一明确讲者的真实登记组，不以邻组自动指派')
        context = entry['source_context']
        require(isinstance(context, list) and bool(context)
                and all(isinstance(unit, dict) and isinstance(unit.get('unit_ids'), list)
                        and len(unit['unit_ids']) == 1 and type(unit['unit_ids'][0]) is int
                        and 1 <= unit['unit_ids'][0] <= len(source_units)
                        and unit == source_units[unit['unit_ids'][0] - 1] for unit in context),
                '角色审读必须绑定实际原文、译文、朗读和原片时间')
        identifiers = [unit['unit_ids'][0] for unit in context]
        required = set(anchor['unit_ids']) | {identifier, max(1, identifier - 1),
                                             min(len(source_units), identifier + 1)}
        require(identifiers == sorted(set(identifiers)) and required.issubset(identifiers),
                '需要目标项及前后文、明确角色锚点，不接受孤立词语审校')
        require(isinstance(entry['frames'], list) and len(entry['frames']) >= 2, '需要目标项与角色锚点的取证画面')
        timestamps = [reviewed_frame(frame, frame_preparation, registry['inputs']['source_video'], context)
                      for frame in entry['frames']]
        target = entry['actual_synthesis_unit']
        require(len(timestamps) == len(set(timestamps))
                and any(target['start'] <= timestamp < target['end'] for timestamp in timestamps)
                and any(anchor_unit['start'] <= timestamp < anchor_unit['end'] for timestamp in timestamps),
                '缺少目标合成窗或已登记角色锚点的不同原片画面')
        reviewed[identifier] = entry
    require(list(reviewed) == sorted(reviewed), '角色登记按实际单元编号排序')
    return reviewed


def load_roles(work, path):
    reference = state.identity(path)
    registry = state.load(path)
    preparation = state.load(registry['frame_preparation']['path'])
    reviewed = validate_roles(work, registry, preparation)
    require(state.matches(reference), '读取期间逐单元角色审校发生变化')
    return reference, reviewed


def display_window(start, end):
    require(all(type(value) in {int, float} and math.isfinite(value) for value in [start, end])
            and 0 <= start < end, '原始声音窗口必须为非负、有限且有序的时间')
    serialized = state.srt_text([(start, end, 'display-owner')])
    parsed = check_videos.subtitles(serialized)
    require(len(parsed) == 1, '显示量化必须使用原SRT序列化与解析')
    return serialized, parsed[0][:2]


def contexts_for(work, offset, registry_path):
    require(type(offset) in {int, float} and math.isfinite(offset) and offset >= 0, '正文偏移无效')
    reference, reviewed = load_roles(work, registry_path)
    policy = speech_groups.load_policy(work)
    groups = {tuple(group['unit_ids']): group for group in policy['groups']}
    result = []
    for unit in speech_groups.units(work):
        identifiers = tuple(unit['unit_ids'])
        if identifiers in groups:
            speaker = groups[identifiers]['speaker_id']
            evidence = {'kind': 'registered-group', 'registry': None, 'unit_id': None}
        elif len(identifiers) == 1 and identifiers[0] in reviewed:
            speaker = reviewed[identifiers[0]]['speaker_id']
            evidence = {'kind': ROLE_KIND, 'registry': reference, 'unit_id': identifiers[0]}
        else:
            speaker = 'unknown'
            evidence = {'kind': 'unknown', 'registry': None, 'unit_id': None}
        audio_window = [unit['start'] + offset, unit['end'] + offset]
        serialized, display = display_window(*audio_window)
        result.append({'unit_ids': unit['unit_ids'], 'speaker_id': speaker, 'start': display[0], 'end': display[1],
            'voice': unit['voice'], 'source_window_seconds': [unit['start'], unit['end']],
            'body_offset_seconds': offset, 'audio_window_seconds': audio_window,
            'display_owner_srt': serialized, 'role_evidence': evidence})
    return result


def repartition(cues, edits, contexts, forms):
    require(isinstance(contexts, list) and bool(contexts), '需要实际角色及声音/显示分域上下文')
    for context in contexts:
        require(isinstance(context, dict) and set(context) == CONTEXT_FIELDS, '上下文必须显式保留声音与显示域')
        source_window, offset = context['source_window_seconds'], context['body_offset_seconds']
        require(isinstance(source_window, list) and len(source_window) == 2
                and all(type(value) in {int, float} and math.isfinite(value) for value in source_window)
                and 0 <= source_window[0] < source_window[1]
                and type(offset) in {int, float} and math.isfinite(offset) and offset >= 0,
                '上下文原片窗口或偏移无效')
        expected_audio = [value + offset for value in source_window]
        serialized, display = display_window(*expected_audio)
        require(context['audio_window_seconds'] == expected_audio and context['display_owner_srt'] == serialized
                and (context['start'], context['end']) == display,
                '显示域只能由原SRT量化派生，不得扩展音频窗或添加任意容差')
    repaired, proof = original.repartition(cues, edits, contexts, forms)
    return repaired, {**proof, 'version': VERSION, 'kind': KIND,
        'repartition_algorithm': state.identity(original.__file__),
        'context_protocol': 'source-reviewed-role-and-original-srt-display-domain-v2',
        'audio_windows_changed': False, 'timestamps_quantized_again': False,
        'word_alignment_verified': False, 'human_listened': False}


def inputs_for(work, terms, manifest, registry_path):
    return {**original.inputs_for(work, terms, manifest), 'role_registry': state.identity(registry_path),
            **{'role_' + name: reference for name, reference in role_inputs_for(work).items()}}


def validate_plan(work, terms, plan_path, manifest):
    plan = state.load(plan_path)
    require(isinstance(plan, dict) and set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'}
            and type(plan['version']) is int and plan['version'] == VERSION and plan['kind'] == KIND
            and plan['project_id'] == project_id(work), '逐项修订计划版本、工程或字段无效')
    registry_path = Path(plan['inputs']['role_registry']['path'])
    inputs = inputs_for(work, terms, manifest, registry_path)
    require(plan['inputs'] == inputs and all(state.matches(reference) for reference in inputs.values()),
            '逐项修订计划绑定输入已变化')
    forms = caption_layout.load_forms(terms)
    original_cues = state.cues(inputs['captions']['path'])
    contexts = contexts_for(work, manifest['body_offset_seconds'], registry_path)
    repaired, proof = repartition(original_cues, plan['edits'], contexts, forms)
    return inputs, forms, original_cues, repaired, proof


def preview(work, terms, plan_path, output_directory):
    work, terms, plan_path = [Path(path).resolve(strict=True) for path in [work, terms, plan_path]]
    output_directory = Path(output_directory)
    require(output_directory.is_absolute() and not output_directory.exists()
            and output_directory.parent.resolve(strict=True).is_relative_to(work.parent / '.workflow_logs'),
            '只能在隐藏工作流目录创建全新预览')
    implementation = state.identity(__file__)
    helpers = implementations()
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        manifest = video_package.validate(work)
        plan_reference = state.identity(plan_path)
        inputs, forms, original_cues, repaired, proof = validate_plan(work, terms, plan_path, manifest)
        video = next(stream for stream in upload_export.probe(Path(inputs['master']['path']))['streams']
                     if stream['codec_type'] == 'video')
        output_directory.mkdir()
        captions = output_directory / 'repartitioned-zh.srt'
        atomic_write(captions, state.srt_text(repaired))
        require(state.cues(captions) == repaired, '不得再次改变原字幕时间或文字')
        script, layout = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
            manifest['duration_seconds'], forms, captions)
        script_check = upload_export_v3.verify_script(script, captions, manifest['duration_seconds'])
        ass = output_directory / 'repartitioned-preview.ass'
        atomic_write(ass, script)
        require(all(state.matches(reference) for reference in inputs.values()) and state.matches(plan_reference)
                and state.matches(implementation) and implementations() == helpers
                and video_package.validate(work) == manifest, '预览期间工程输入或实现发生变化')
        result = {'version': VERSION, 'kind': KIND, 'checked_at': state.now(), 'work': str(work),
            'implementation': implementation, 'reused_implementations': helpers, 'plan': plan_reference,
            'inputs': inputs, 'preview_captions': state.identity(captions), 'preview_ass': state.identity(ass),
            'proof': proof, 'layout': layout, 'script_check': script_check, 'preview_only': True,
            'embedded_master_subtitles_updated': False, 'upload_video_updated': False,
            'production_applied': False, 'tts_performed': False, 'video_encoding_performed': False,
            'word_alignment_verified': False, 'human_listened': False, 'goal_status': 'active'}
        save_json(output_directory / 'preview-proof.json', result)
        return result


def verify_preview(work, preview_path):
    work, preview_path = [Path(path).resolve(strict=True) for path in [work, preview_path]]
    preview_reference = state.identity(preview_path)
    record = state.load(preview_path)
    require(record['version'] == VERSION and record['kind'] == KIND and record['work'] == str(work)
            and record['implementation'] == state.identity(__file__)
            and record['reused_implementations'] == implementations()
            and record['preview_only'] is True and record['production_applied'] is False
            and state.matches(record['plan']) and state.matches(record['preview_captions'])
            and state.matches(record['preview_ass']), '文字预览版本、实现或输出身份失效')
    manifest = video_package.validate(work)
    inputs, forms, original_cues, repaired, proof = validate_plan(work, Path(record['inputs']['terms']['path']),
                                                               Path(record['plan']['path']), manifest)
    require(inputs == record['inputs'] and state.signature(proof) == state.signature(record['proof'])
            and state.cues(record['preview_captions']['path']) == repaired
            and Path(record['preview_captions']['path']).read_text() == state.srt_text(repaired),
            '不能从真实登记重建同一角色、显示域和逐项修订')
    video = next(stream for stream in upload_export.probe(Path(inputs['master']['path']))['streams']
                 if stream['codec_type'] == 'video')
    script, layout = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
        manifest['duration_seconds'], forms, Path(record['preview_captions']['path']))
    require(script == Path(record['preview_ass']['path']).read_text() and layout == record['layout']
            and upload_export_v3.verify_script(script, Path(record['preview_captions']['path']),
                                              manifest['duration_seconds']) == record['script_check']
            and state.matches(preview_reference), '完整ASS或显示布局不能确定性重建')
    return {'preview': record, 'preview_reference': preview_reference, 'manifest': manifest,
            'original': original_cues, 'repaired': repaired, 'forms': forms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--terms', required=True, type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--preview-directory', required=True, type=Path)
    arguments = parser.parse_args()
    result = preview(arguments.work, arguments.terms, arguments.plan, arguments.preview_directory)
    print(f'v2文字预览：{result["proof"]["repaired_boundary_count"]}个跨条边界；声音与正式双版本不变')


if __name__ == '__main__':
    main()
