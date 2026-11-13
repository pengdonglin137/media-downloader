"""显式v1到v4双版本迁移，保留最近混版表中的全部v1/v2/v3/v4证据路由。"""

import argparse
import copy
import json
from pathlib import Path

import check_videos
import dubbing_state as state
import mixed_caption_batch as retained_batch
import paired_batch_v4 as legacy_batch
import paired_delivery_transaction as files
import paired_delivery_v4 as pairs


KIND = 'mixed-paired-v4-preserving-v3'
TRANSITION_FIELDS = {'id', 'from', 'to', 'directory', 'installed_runtime_path'}
ROUTE_FIELDS = {'fidelity': 'fidelity_evidence', 'master_runtime': 'unchanged_master_vlc_evidence',
                'runtime': 'vlc_evidence'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def module_identities():
    return {name: state.identity(module.__file__) for name, module in {
        'retained_dispatcher': retained_batch, 'legacy_dispatcher': legacy_batch,
        'pair_validator': pairs, 'file_transaction': files, 'base_inspector': check_videos,
        'state': state}.items()}


def route_references(projects):
    return {project['id']: {key: project[field] for key, field in ROUTE_FIELDS.items()}
            for project in projects if project.get('upload_version', 1) == 3}


def planned_projection(specification):
    projected = copy.deepcopy(specification)
    projected['phase'] = 'planned'
    projected.pop('planned_configuration', None)
    projected.pop('baseline_preflight', None)
    for transition in projected['transitions']:
        transition.pop('intent', None)
    return projected


def load_configuration(path):
    path = files.canonical(path)
    specification = state.load(path)
    require(type(specification['version']) is int and specification['version'] == 1
            and specification['kind'] == KIND and specification['phase'] in {'planned', 'ready'}
            and specification['implementation'] == state.identity(__file__)
            and specification['modules'] == module_identities(), '混版v4配置、阶段或实现身份不符')
    ready = specification['phase'] == 'ready'
    require(('planned_configuration' in specification) == ready
            and ('baseline_preflight' in specification) == ready, '就绪配置必须绑定独立计划和成功基线预检')
    if ready:
        planned = state.load(files.checked(specification['planned_configuration']))
        require(planned['phase'] == 'planned' and planned == planned_projection(specification),
                '就绪配置改变了已预检计划，或没有绑定原计划')
        baseline = state.load(files.checked(specification['baseline_preflight']))
        require(baseline['passed'] and baseline['action'] == 'baseline' and baseline['phase'] == 'planned'
                and baseline['configuration'] == specification['planned_configuration']
                and baseline['implementation'] == state.identity(__file__)
                and baseline['modules'] == module_identities(), '就绪配置需要本实现完成的全34部计划基线预检')
    for key in ['retained_configuration', 'previous_pairs', 'previous_base_status', 'v4_status_inspector']:
        files.checked(specification[key])
    retained = retained_batch.load_configuration(specification['retained_configuration']['path'])
    previous = state.load(specification['previous_pairs']['path'])
    previous_base = state.load(specification['previous_base_status']['path'])
    original = retained['context']['context']
    project_ids = retained['context']['project_ids']
    require(previous['passed'] and previous['action'] == 'batch' and previous_base['passed']
            and previous['configuration'] == specification['retained_configuration']
            and previous['implementation'] == state.identity(retained_batch.__file__)
            and previous['both_variants_generated'] == len(previous['projects']) == 34
            and [entry['id'] for entry in previous['projects']] == project_ids
            and previous['current_versions'] == retained['configuration']['expected_versions']
            and previous_base['complete_scan'] and previous_base['summary'] == previous['summary']
            and previous_base['summary'] == retained['configuration']['expected_summary']
            and len(original['all_ids']) == 40 and len(original['blocked']) == 6,
            '需要最近完整34双版本、原40范围和六门禁的权威混版表')
    if ready:
        require(baseline['current_versions'] == previous['current_versions']
                and baseline['both_variants_generated'] == 34
                and [entry['id'] for entry in baseline['projects']] == project_ids
                and all(all(actual[key] == prior[key] for key in ['master', 'upload', 'manifest'])
                        for actual, prior in zip(baseline['projects'], previous['projects'])),
                '计划基线预检没有覆盖最近34部的全部正式身份')
    versions, selected = legacy_batch.apply_transitions(previous['projects'], specification['transitions'])
    require(legacy_batch.version_counts(versions.values()) == specification['expected_versions'],
            '显式迁移后的版本计数不符')
    expected_summary = copy.deepcopy(previous_base['summary'])
    expected_summary.update({'v' + str(version) + '_uploads': list(versions.values()).count(version)
                             for version in [2, 3, 4]})
    require(expected_summary == specification['expected_summary'], '仅允许声明的版本计数改变')
    routes = route_references(previous['projects'])
    require(specification['existing_v3_routes'] == routes, '必须保留最近表中全部v3的精确证据路由')
    for route in routes.values():
        for observation in route.values():
            files.checked(observation)
    require(specification['v4_status_inspector'] == retained['configuration']['v4_status_inspector']
            and specification['existing_v4_runtime'] == retained['configuration']['existing_v4_runtime'],
            '既有v4验证器或正式路径运行证据变化')
    existing_v4 = {entry['id']: entry for entry in previous['projects'] if entry.get('upload_version', 1) == 4}
    require(set(specification['existing_v4_runtime']) == set(existing_v4), '既有v4运行证据不完整')
    for project_id, observation in specification['existing_v4_runtime'].items():
        runtime = state.load(files.checked(observation))
        require(runtime['passed'] and runtime['transaction'] == existing_v4[project_id]['transaction']
                and runtime['package_manifest'] == existing_v4[project_id]['manifest'], '既有v4运行证据不属于最近正式包')
    base = retained['context']['base']
    directories, runtime_paths = set(), set()
    for project_id, transition in selected.items():
        require(set(transition) == TRANSITION_FIELDS | ({'intent'} if ready else set()), '迁移字段不完整或混淆计划与就绪阶段')
        directory = pairs.revision_directory(base.work_for(project_id), transition['directory'])
        runtime_path = files.canonical(transition['installed_runtime_path'])
        require(directory not in directories and runtime_path not in runtime_paths
                and runtime_path.is_relative_to(path.parent) and runtime_path.suffix == '.json'
                and runtime_path != path, '修订目录或新正式路径运行证据重复/越界')
        directories.add(directory)
        runtime_paths.add(runtime_path)
        if ready:
            require(files.checked(transition['intent']) == directory / 'intent.json', '新v4意向不属于声明修订目录')
    dispatch = {**retained, 'configuration': {**retained['configuration'], 'existing_v3_routes': {
        project_id: {key: observation['path'] for key, observation in route.items()}
        for project_id, route in routes.items()}}}
    return {'configuration': specification, 'previous': previous, 'previous_base': previous_base,
            'retained': retained, 'dispatch': dispatch, 'base': base, 'original': original,
            'project_ids': project_ids, 'selected': selected, 'versions': versions}


def inspect_pairs(context, stage):
    require(stage in {'baseline', 'preflight', 'packages'}, '未知双版本验证阶段')
    specification = context['configuration']
    require(stage == 'baseline' or specification['phase'] == 'ready', '必须完成独立就绪配置后才能验证候选或安装')
    projects = []
    for prior in context['previous']['projects']:
        project_id = prior['id']
        work = context['base'].work_for(project_id)
        with context['base'].read_lock(work):
            transition = context['selected'].get(project_id)
            if stage == 'packages' and transition is not None:
                result = pairs.validate_current(work)
                require(result['id'] == project_id and result['upload_version'] == 4
                        and result['intent'] == transition['intent'], '正式v4不是本次声明意向')
            else:
                result = retained_batch.existing_pair(project_id, prior, context['dispatch'])
                if stage == 'preflight' and transition is not None:
                    verified = pairs.verify_revision(work, Path(transition['directory']))
                    require(verified['intent_reference'] == transition['intent']
                            and all(verified['intent']['original'][key] == prior[key] for key in ['master', 'upload']),
                            '候选意向或原双版本身份不属于已预检正式包')
                    result = {**result, 'prospective_v4': {'intent': transition['intent'],
                        'master': verified['replacements']['master'], 'upload': verified['replacements']['upload']}}
            for key in ['master', 'upload', 'manifest']:
                files.checked(result[key])
            projects.append(result)
    require([entry['id'] for entry in projects] == context['project_ids'], '原34部顺序变化')
    counts = legacy_batch.version_counts(entry.get('upload_version', 1) for entry in projects)
    expected = (specification['expected_versions'] if stage == 'packages' else
                legacy_batch.version_counts(entry.get('upload_version', 1) for entry in context['previous']['projects']))
    require(counts == expected, '当前正式版本计数与验证阶段不符')
    return {'projects': projects, 'both_variants_generated': len(projects), 'current_versions': counts,
            **{'v' + str(version) + '_uploads': counts['v' + str(version)] for version in [2, 3, 4]},
            'expected_after_install': specification['expected_versions'], 'installed_versions_checked': stage == 'packages',
            'candidate_proofs_verified_by_preflight': stage == 'preflight',
            'production_mutated_by_validator': False}


def source_quality(context):
    return legacy_batch.source_quality(context['retained']['context'])


def inspect_base(context, packages):
    sources = source_quality(context)
    package_map = {entry['id']: entry for entry in packages['projects']}
    inspector = legacy_batch.load_module('mixed_paired_v4_status', context['configuration']['v4_status_inspector'])
    records, formats, runtime_evidence = [], [], {}
    for source in sources['videos']:
        project_id = source['id']
        work = context['base'].work_for(project_id)
        with context['base'].read_lock(work):
            package = package_map.get(project_id)
            if package and package['upload_version'] == 4:
                transition = context['selected'].get(project_id)
                runtime = (state.identity(transition['installed_runtime_path']) if transition else
                           context['configuration']['existing_v4_runtime'][project_id])
                record = inspector.inspect_v4(work, package, source, runtime)
                runtime_evidence[project_id] = runtime
            else:
                record = (context['retained']['inspectors']['legacy_refresh'].legacy.inspect_x(context['base'].MEDIA)
                          if project_id.startswith('x_') else check_videos.inspect(project_id, context['base'].MEDIA, False))
            require(context['base'].canonical_id(record['id']) == project_id and record['state'] == 'verified'
                    and record['source_units'] == source['source_units']
                    and record['translated_units'] == source['translated_units']
                    and retained_batch.stable_quality(record['quality_gate']) == retained_batch.stable_quality(source['quality_gate']),
                    '当前基础/源译稿/门禁状态变化：' + project_id)
            for observation in source['source_identities'].values():
                files.checked(observation)
            records.append(record)
            formats.append({'id': project_id, 'master_generated': package is not None,
                'upload_generated': package is not None, 'state': 'both_variants_generated' if package else 'not_packaged',
                **(package or {})})
    summary = {**sources['summary'], 'base_technical_verified': sum(record['state'] == 'verified' for record in records),
        'new_master_generated': len(package_map), 'both_new_variants_generated': len(package_map),
        **{'v' + str(version) + '_uploads': packages['current_versions']['v' + str(version)] for version in [2, 3, 4]}}
    require(summary == context['configuration']['expected_summary'], '全40基础汇总不符')
    return {'complete_scan': True, 'summary': summary, 'videos': records, 'new_format': formats,
            'fresh_source_quality': sources, 'installed_runtime_evidence': runtime_evidence,
            'fresh_full_decode_performed': False, 'legacy_delivery_checks_modified': False}


def run(configuration, action, output):
    context = load_configuration(configuration)
    output = files.canonical(output)
    require(output.is_relative_to(context['base'].ROOT) and output.parent.is_dir() and not output.exists(),
            '需要全新隐藏批量收据路径')
    report = {'version': 1, 'kind': KIND, 'checked_at': state.now(), 'passed': False, 'action': action,
        'phase': context['configuration']['phase'], 'configuration': state.identity(configuration),
        'implementation': state.identity(__file__), 'modules': module_identities(),
        'previous_pairs': context['configuration']['previous_pairs'], 'goal_status': 'active',
        'human_listened': False, 'potplayer_tested': False, 'production_mutated_by_validator': False,
        'fresh_full_decode_performed': False}
    try:
        if action in {'baseline', 'preflight', 'packages', 'batch'}:
            package_result = inspect_pairs(context, 'packages' if action == 'batch' else action)
            report.update(package_result)
            if action == 'batch':
                report.update(inspect_base(context, package_result))
        elif action == 'source-quality':
            report.update(source_quality(context))
        else:
            require(action == 'configuration', '未知动作')
            report.update(project_ids=context['project_ids'], all_ids=context['original']['all_ids'],
                          expected_after_install=context['configuration']['expected_versions'])
        files.checked(report['configuration'])
        load_configuration(configuration)
        report['passed'] = True
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        files.durable_json(output, report)
        raise
    files.durable_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['configuration', 'baseline', 'preflight', 'packages', 'batch', 'source-quality'])
    parser.add_argument('--configuration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    result = run(options.configuration, options.action, options.output)
    print(json.dumps({key: result[key] for key in ['action', 'passed', 'current_versions', 'summary'] if key in result},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
