"""仅使用既有已审校语义组及原SRT显示域的字幕切点预览。"""

import argparse
import fcntl
import math
from pathlib import Path

import caption_boundary_repair as original
import caption_boundary_repair_v2 as display_domain
import caption_layout
import dubbing_state as state
import speech_groups
import upload_export
import upload_export_v3
import video_package
from prepare_videos import atomic_write, save_json


VERSION = 3
KIND = 'registered-group-display-boundary-repartition-v3'
require = original.require


def implementations():
    return {module.__name__: state.identity(module.__file__) for module in
            [original, display_domain, caption_layout, state, speech_groups,
             upload_export, upload_export_v3, video_package]}


def contexts_for(work, offset):
    require(type(offset) in {int, float} and math.isfinite(offset) and offset >= 0, '正文偏移无效')
    policy = speech_groups.load_policy(work)
    require(policy is not None, '缺少既有已审校语义组')
    groups = {tuple(group['unit_ids']): group for group in policy['groups']}
    contexts = []
    for unit in speech_groups.units(work):
        group = groups.get(tuple(unit['unit_ids']))
        audio_window = [unit['start'] + offset, unit['end'] + offset]
        serialized, display = display_domain.display_window(*audio_window)
        contexts.append({'unit_ids': unit['unit_ids'],
            'speaker_id': group['speaker_id'] if group is not None else 'unknown',
            'start': display[0], 'end': display[1], 'voice': unit['voice'],
            'source_window_seconds': [unit['start'], unit['end']], 'body_offset_seconds': offset,
            'audio_window_seconds': audio_window, 'display_owner_srt': serialized,
            'role_evidence': {'kind': 'registered-group' if group is not None else 'unknown',
                              'registry': None, 'unit_id': None}})
    return contexts


def repartition(cues, edits, contexts, forms):
    require(isinstance(contexts, list) and bool(contexts), '需要声音及显示域上下文')
    for context in contexts:
        require(isinstance(context, dict) and set(context) == display_domain.CONTEXT_FIELDS,
                '显示域上下文字段无效')
        evidence = context['role_evidence']
        require(isinstance(evidence, dict) and set(evidence) == {'kind', 'registry', 'unit_id'}
                and evidence['kind'] in {'registered-group', 'unknown'}
                and evidence['registry'] is None and evidence['unit_id'] is None
                and (evidence['kind'] == 'registered-group' and display_domain.known_role(context['speaker_id'])
                     or evidence['kind'] == 'unknown' and context['speaker_id'] == 'unknown'),
                '本协议不接受新增单项角色或以音色推断角色')
    repaired, proof = display_domain.repartition(cues, edits, contexts, forms)
    return repaired, {**proof, 'version': VERSION, 'kind': KIND,
        'context_protocol': 'existing-registered-group-original-srt-display-domain-v3',
        'display_domain_algorithm': state.identity(display_domain.__file__),
        'new_singleton_roles_authorized': False}


def inputs_for(work, terms, manifest):
    return original.inputs_for(work, terms, manifest)


def validate_plan(work, terms, plan_path, manifest):
    plan = state.load(plan_path)
    project_id = work.name.removeprefix('.zh_work_yt_').removeprefix('.zh_work_')
    inputs = inputs_for(work, terms, manifest)
    require(isinstance(plan, dict) and set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'}
            and type(plan['version']) is int and plan['version'] == VERSION and plan['kind'] == KIND
            and plan['project_id'] == project_id and plan['inputs'] == inputs
            and all(state.matches(reference) for reference in inputs.values()), '审校计划或绑定输入无效')
    forms = caption_layout.load_forms(terms)
    original_cues = state.cues(inputs['captions']['path'])
    repaired, proof = repartition(original_cues, plan['edits'],
        contexts_for(work, manifest['body_offset_seconds']), forms)
    return inputs, forms, original_cues, repaired, proof


def preview(work, terms, plan_path, output_directory):
    work, terms, plan_path = [Path(path).resolve(strict=True) for path in [work, terms, plan_path]]
    output_directory = Path(output_directory)
    require(output_directory.is_absolute() and not output_directory.exists()
            and output_directory.parent.resolve(strict=True).is_relative_to(work.parent / '.workflow_logs'),
            '需要新的隐藏预览目录，不能覆盖正式文件或历史证据')
    implementation, helpers = state.identity(__file__), implementations()
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
                and state.matches(implementation) and helpers == implementations()
                and video_package.validate(work) == manifest, '预览期间工程或实现发生变化')
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
    reference = state.identity(preview_path)
    record = state.load(preview_path)
    require(record['version'] == VERSION and record['kind'] == KIND and record['work'] == str(work)
            and record['implementation'] == state.identity(__file__)
            and record['reused_implementations'] == implementations()
            and record['preview_only'] is True and record['production_applied'] is False
            and state.matches(record['plan']) and state.matches(record['preview_captions'])
            and state.matches(record['preview_ass']), '预览收据或实现失效')
    manifest = video_package.validate(work)
    inputs, forms, original_cues, repaired, proof = validate_plan(work,
        Path(record['inputs']['terms']['path']), Path(record['plan']['path']), manifest)
    require(inputs == record['inputs'] and state.signature(proof) == state.signature(record['proof'])
            and state.cues(record['preview_captions']['path']) == repaired, '字幕切点不能完整重建')
    video = next(stream for stream in upload_export.probe(Path(inputs['master']['path']))['streams']
                 if stream['codec_type'] == 'video')
    script, layout = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
        manifest['duration_seconds'], forms, Path(record['preview_captions']['path']))
    require(script == Path(record['preview_ass']['path']).read_text() and layout == record['layout']
            and upload_export_v3.verify_script(script, Path(record['preview_captions']['path']),
                                              manifest['duration_seconds']) == record['script_check']
            and state.matches(reference), 'ASS或布局不能完整重建')
    return {'preview': record, 'preview_reference': reference, 'manifest': manifest,
            'original': original_cues, 'repaired': repaired, 'forms': forms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--terms', required=True, type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--preview-directory', required=True, type=Path)
    arguments = parser.parse_args()
    result = preview(arguments.work, arguments.terms, arguments.plan, arguments.preview_directory)
    print(f'显示域预览：{result["proof"]["repaired_boundary_count"]}处；正式双版本未修改')


if __name__ == '__main__':
    main()
