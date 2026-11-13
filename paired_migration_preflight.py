"""实际复验现有v1至v5全批和v2来源候选；不提供编码、安装或版本伪装。"""

import argparse
import copy
import json
from pathlib import Path

import caption_boundary_master_v2 as master_producer
import caption_layout
import delivery_quality
import dubbing_state as state
import mixed_paired_batch_v2 as retained
import paired_delivery_transaction as files
import paired_delivery_v4 as text_helpers
import paired_delivery_v5 as helpers
import paired_source_v2 as source_v2
import upload_export_v3
import video_package


KIND = 'v2-source-migration-preflight-preserving-v1-v5'
TRANSITION_FIELDS = {'id', 'from', 'to', 'preview_checkpoint', 'master_candidate'}
CONFIGURATION_FIELDS = {'version', 'kind', 'phase', 'implementation', 'modules',
    'retained_configuration', 'previous_authority', 'transitions',
    'expected_original_versions', 'prospective_versions_not_installed'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def module_identities():
    return {'preflight': state.identity(__file__), **source_v2.module_identities(),
        **{'retained_' + name: reference for name, reference in retained.module_identities().items()},
        **{name: state.identity(module.__file__) for name, module in {
            'retained_batch': retained, 'master_producer': master_producer,
            'text_helpers': text_helpers,
            'safe_band': upload_export_v3, 'quality': delivery_quality}.items()}}


def version_counts(versions, prospective=False):
    values = list(versions)
    allowed = {1, 2, 3, 4, 5, 6} if prospective else {1, 2, 3, 4, 5}
    require(all(type(version) is int and version in allowed for version in values), '非法现有或计划版本')
    return {'formal_pairs': len(values), **{'v' + str(version): values.count(version)
        for version in sorted(allowed)}}


def apply_transitions(projects, transitions):
    require(isinstance(projects, list) and bool(projects) and isinstance(transitions, list), '需要完整原表和明确计划')
    versions = {project['id']: project.get('upload_version', 1) for project in projects}
    require(len(versions) == len(projects), '原表工程重复')
    version_counts(versions.values())
    selected = {}
    for transition in transitions:
        require(isinstance(transition, dict) and set(transition) == TRANSITION_FIELDS, '计划字段不完整或包含未实现安装字段')
        identifier = transition['id']
        require(identifier in versions and identifier not in selected
            and type(transition['from']) is int and transition['from'] == versions[identifier] == 2
            and type(transition['to']) is int and transition['to'] == 6,
            '只预检明确且唯一的v2来源，不改动其他既有版本')
        versions[identifier] = 6
        selected[identifier] = transition
    require(bool(selected), '缺少明确的v2来源候选')
    return versions, selected


def assert_same_pairs(actual, expected):
    require(isinstance(actual, list) and len(actual) == len(expected), '原34部数量变化')
    for current, prior in zip(actual, expected):
        normalized = []
        for record in [current, prior]:
            value = copy.deepcopy(record)
            if 'quality' in value:
                value['quality'].pop('checked_at', None)
            normalized.append(value)
        require(normalized[0] == normalized[1], '正式双版本或其真实验证路由变化：' + prior['id'])


def validate_registered_raster(receipt, preview, preview_reference, script, cues, forms):
    raster = receipt['all_cue_raster_bounds']
    require(receipt['passed'] is True and receipt['preview_proof'] == preview_reference
        and receipt['all_original_timestamps_identical'] is True
        and receipt['exact_full_character_stream_identical'] is True
        and receipt['all_ass_text_checked'] is True
        and receipt['all_intra_cue_registered_terms_checked'] is True
        and receipt['bilingual_english_all_' + str(len(cues)) + '_suffixes_identical'] is True
        and raster['passed'] is True and raster['all_cues_inside_added_band'] is True
        and receipt['checked_cues'] == raster['cues_rasterized'] == preview['proof']['cue_count'] == len(cues),
        '完整已登记词、文字、原时间或逐字幕字形证据无效')
    rows = [line.split(',', 9) for line in script.splitlines() if line.startswith('Dialogue: ')]
    require(len(rows) == len(cues), '实际ASS条数与完整预览不符')
    for number, (row, cue) in enumerate(zip(rows, cues), 1):
        lines = row[9].split(r'\N')
        require(''.join(''.join(lines).split()) == ''.join(cue[2].split())
            and not caption_layout.split_terms(cue[2], lines, forms), '实际ASS文字或登记词内折行变化：' + str(number))
    return raster


def load_configuration(path):
    path = files.canonical(path)
    specification = state.load(path)
    require(set(specification) == CONFIGURATION_FIELDS
        and type(specification['version']) is int and specification['version'] == 1
        and specification['kind'] == KIND and specification['phase'] == 'source-preflight-only'
        and specification['implementation'] == state.identity(__file__)
        and specification['modules'] == module_identities(), '预检阶段、配置或实现身份变化')
    for name in ['retained_configuration', 'previous_authority']:
        files.checked(specification[name])
    previous = state.load(specification['previous_authority']['path'])
    context = retained.load_configuration(specification['retained_configuration']['path'])
    require(context['configuration']['phase'] == 'ready' and previous['passed'] is True
        and previous['action'] == 'batch' and previous['complete_scan'] is True
        and previous['implementation'] == state.identity(retained.__file__)
        and previous['modules'] == retained.module_identities()
        and previous['configuration'] == specification['retained_configuration']
        and previous['both_variants_generated'] == len(previous['projects']) == 34
        and len(previous['videos']) == 40 and len(context['original']['all_ids']) == 40
        and [project['id'] for project in previous['projects']] == context['project_ids']
        and previous['summary'] == context['configuration']['expected_summary']
        and previous['current_versions'] == context['configuration']['expected_versions']
            == version_counts(project.get('upload_version', 1) for project in previous['projects'])
        and previous['summary']['source_units'] == previous['summary']['translated_units'] == 6840
        and previous['summary']['quality_allowed'] == 34 and previous['summary']['quality_blocked'] == 6,
        '需要当前真实v1至v5全34部、原40部和六门禁的完整权威表')
    versions, selected = apply_transitions(previous['projects'], specification['transitions'])
    require(specification['expected_original_versions'] == previous['current_versions']
        and specification['prospective_versions_not_installed'] == version_counts(versions.values(), prospective=True),
        '现有计数或明确标注未安装的计划计数不符')
    for transition in selected.values():
        files.checked(transition['preview_checkpoint'])
        files.checked(transition['master_candidate'])
    return {'specification': specification, 'retained': context, 'previous': previous, 'selected': selected}


def validate_candidate(work, transition, prior):
    checkpoint = helpers.read_reference(transition['preview_checkpoint'])
    require(checkpoint['passed'] is True and checkpoint['production_applied'] is False
        and checkpoint['new_upload_generated'] is False
        and checkpoint['project_id'] == prior['id']
        and checkpoint['evidence']['master_candidate'] == transition['master_candidate']
        and checkpoint['candidate_master'] == helpers.read_reference(transition['master_candidate'])['output'],
        '只接受当前工程尚未安装且没有新上传版的已审母版候选')
    master = helpers.read_reference(transition['master_candidate'])
    helpers.references(master)
    original = {name: state.identity(work / filename) for name, filename in [
        ('render_state', 'render_state.json'), ('upload_manifest', 'upload_export.json')]}
    original.update(master=prior['master'], upload=prior['upload'])
    quality = delivery_quality.require(work)
    source = source_v2.validate(work, original, prior['manifest'], quality['bindings'])
    base = source['base']
    require(type(master['version']) is int and master['version'] == 4
        and master['kind'] == 'caption-boundary-master-candidate-v4' and master['work'] == str(work)
        and master['implementation'] == state.identity(master_producer.__file__)
        and master['reused_helpers'] == state.identity(text_helpers.master_helpers.__file__)
        and master['reused_package_verifier'] == state.identity(video_package.__file__)
        and master['candidate_only'] is True and master['production_applied'] is False
        and master['original_master'] == original['master'] and master['input_package_manifest'] == prior['manifest']
        and master['all_logical_chapters_identical'] is True
        and master['chapter_data_packets_claimed_identical'] is False,
        '必须复用原始已验证v4格式母版，不重封装或伪装成新版本')
    preview = helpers.read_reference(master['text_preview'])
    helpers.references(preview)
    require(master['text_preview'] == checkpoint['evidence']['final_preview'], '没有使用最终审校文字预览')
    forms = text_helpers.text_projection(work, preview, master, base, original, prior['manifest'])
    raster = helpers.read_reference(checkpoint['evidence']['final_raster'])
    helpers.references(raster)
    geometry = preview['layout']['geometry']
    script, layout = upload_export_v3.subtitles(work, geometry['source_width'], geometry['source_height'],
        base['duration_seconds'], forms, Path(master['subtitles']['zh']['path']))
    content = upload_export_v3.verify_script(script, Path(master['subtitles']['zh']['path']), base['duration_seconds'])
    require(script == Path(preview['preview_ass']['path']).read_text()
        and layout == preview['layout'] and content == preview['script_check'], '最终候选字幕带无法精确重建')
    validate_registered_raster(raster, preview, master['text_preview'], script,
        state.cues(master['subtitles']['zh']['path']), forms)
    expected = {**base, 'subtitles': master['subtitles']}
    video_package.verify_media(Path(master['output']['path']), expected)
    files.checked(checkpoint['evidence']['independent_validation'])
    independent = state.load(checkpoint['evidence']['independent_validation']['path'])
    identities = {reference['path']: reference for reference in independent['raw_hashed_files']}
    require(independent['passed'] is True and independent['production_applied'] is False
        and all(identities.get(reference['path']) == reference for reference in [
            transition['master_candidate'], master['output'], master['text_preview'],
            checkpoint['evidence']['final_raster']]), '原候选独立验收没有绑定实际母版或最终文字/字形')
    return {'id': prior['id'], 'source_upload_version': 2, 'planned_pair_version_not_installed': 6,
        'original': original, 'original_manifest': prior['manifest'],
        'source_v2_validation': {key: source[key] for key in ['candidate_result', 'prior_upload_archives',
            'chinese_aac_identical', 'ass_exactly_reconstructed', 'original_vlc_evidence_revalidated']},
        'master_candidate': transition['master_candidate'], 'master_output': master['output'],
        'master_candidate_format_version_retained': 4, 'text_preview': master['text_preview'],
        'raster': checkpoint['evidence']['final_raster'], 'cues': preview['proof']['cue_count'],
        'candidate_source_projection_reconstructed': True, 'new_upload_generated': False,
        'paired_installation_protocol_verified': False, 'production_applied': False}


def run(configuration, output):
    configuration, output = files.canonical(configuration), files.canonical(output)
    context = load_configuration(configuration)
    require(output.parent == configuration.parent and output != configuration and not output.exists(),
        '预检收据必须使用配置同目录的全新路径')
    delegated = output.with_name(output.stem + '-retained-batch.json')
    require(delegated != configuration and not delegated.exists(), '不能覆盖原全批收据')
    report = {'version': 1, 'kind': KIND, 'checked_at': state.now(), 'passed': False,
        'phase': 'source-preflight-only', 'configuration': state.identity(configuration),
        'implementation': state.identity(__file__), 'modules': module_identities(),
        'formal_authority': context['specification']['previous_authority'], 'goal_status': 'active',
        'production_applied': False, 'new_upload_generated': False, 'fresh_full_decode_performed': False,
        'human_listened': False, 'potplayer_tested': False, 'ready_for_encoding': False,
        'paired_installation_protocol_verified': False}
    try:
        actual = retained.run(configuration=context['specification']['retained_configuration']['path'],
            action='batch', output=delegated)
        assert_same_pairs(actual['projects'], context['previous']['projects'])
        require(actual['summary'] == context['previous']['summary']
            and actual['current_versions'] == context['previous']['current_versions'], '当前全批汇总变化')
        selected = []
        for prior in context['previous']['projects']:
            if prior['id'] not in context['selected']:
                continue
            work = context['retained']['base'].work_for(prior['id'])
            with context['retained']['base'].read_lock(work):
                selected.append(validate_candidate(work, context['selected'][prior['id']], prior))
        files.checked(report['configuration'])
        load_configuration(configuration)
        report.update(passed=True, retained_actual_batch=state.identity(delegated), selected=selected,
            current_versions=actual['current_versions'], summary=actual['summary'],
            prospective_versions_not_installed=context['specification']['prospective_versions_not_installed'],
            all_34_real_routes_revalidated=True, original_40_scope_revalidated=True,
            state='source-v2-and-existing-v1-v5-baseline-verified-production-adapters-pending')
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        files.durable_json(output, report)
        raise
    files.durable_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configuration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    result = run(options.configuration, options.output)
    print(json.dumps({key: result[key] for key in ['passed', 'state', 'current_versions', 'summary',
        'ready_for_encoding']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
