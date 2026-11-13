"""独立v2到v6生产预检及混版分派，保留现有v1至v5的真实路径。"""

import argparse
import copy
import json
from pathlib import Path

import check_videos
import dubbing_state as state
import mixed_caption_batch as retained_pairs
import mixed_paired_batch_v2 as retained_batch
import paired_batch_v4 as legacy_batch
import paired_delivery_transaction as files
import paired_delivery_v5 as v5_pairs
import paired_delivery_v6 as pairs
import paired_migration_preflight as source_preflight
import paired_status_v5 as v5_status
import paired_status_v6 as new_status


KIND = 'mixed-paired-v6-preserving-v1-v5'
TRANSITION_FIELDS = {'id', 'from', 'to', 'directory', 'installed_runtime_path', 'preview_checkpoint', 'master_candidate'}
CONFIGURATION_FIELDS = {'version', 'kind', 'phase', 'implementation', 'modules', 'retained_configuration',
    'previous_authority', 'source_preflight', 'production_contracts', 'v4_status_inspector',
    'existing_v3_routes', 'existing_v4_runtime', 'existing_v5_runtime', 'existing_v6_runtime',
    'transitions', 'expected_versions', 'expected_summary'}
require = pairs.require


def module_identities():
    return {'batch': state.identity(__file__), **{name: state.identity(module.__file__) for name, module in {
        'retained_batch': retained_batch, 'retained_pairs': retained_pairs, 'legacy_batch': legacy_batch,
        'file_transaction': files, 'state': state, 'base_inspector': check_videos,
        'v5_pairs': v5_pairs, 'v5_status': v5_status, 'v6_status': new_status,
        'source_preflight': source_preflight}.items()},
        **{'v6_' + name: reference for name, reference in pairs.module_identities().items()}}


def version_counts(versions):
    values = list(versions)
    require(all(type(version) is int and version in {1, 2, 3, 4, 5, 6} for version in values), '不支持的实际版本计数')
    return {'formal_pairs': len(values), **{'v' + str(version): values.count(version) for version in range(1, 7)}}


def apply_transitions(projects, transitions):
    require(isinstance(projects, list) and bool(projects) and isinstance(transitions, list), '需要原完整表及明确迁移')
    versions = {project['id']: project.get('upload_version', 1) for project in projects}
    require(len(versions) == len(projects), '原工程重复')
    version_counts(versions.values())
    selected = {}
    for transition in transitions:
        identifier = transition['id']
        require(identifier in versions and identifier not in selected and type(transition['from']) is int
            and type(transition['to']) is int and transition['from'] == versions[identifier] == 2
            and transition['to'] == 6, '必须明确且唯一声明实际v2到v6，不隐式迁移其他版本')
        versions[identifier] = 6
        selected[identifier] = transition
    require(bool(selected), '缺少明确v2到v6迁移')
    return versions, selected


def planned_projection(specification):
    projected = copy.deepcopy(specification)
    projected['phase'] = 'planned'
    projected.pop('planned_configuration', None)
    projected.pop('baseline_preflight', None)
    for transition in projected['transitions']:
        transition.pop('intent', None)
    return projected


def summary_for(previous, counts):
    return {**previous, **{'v' + str(version) + '_uploads': counts['v' + str(version)] for version in range(2, 7)}}


def load_configuration(path):
    path = files.canonical(path)
    specification = state.load(path)
    require(isinstance(specification, dict) and set(specification) == CONFIGURATION_FIELDS | (
        {'planned_configuration', 'baseline_preflight'} if specification.get('phase') == 'ready' else set())
        and type(specification['version']) is int and specification['version'] == 1
        and specification['kind'] == KIND and specification['phase'] in {'planned', 'ready'}
        and specification['implementation'] == state.identity(__file__)
        and specification['modules'] == module_identities(), 'v6混版配置、阶段或实现变化')
    ready = specification['phase'] == 'ready'
    require(('planned_configuration' in specification) == ready and ('baseline_preflight' in specification) == ready,
        '就绪配置必须绑定原计划及真实成功基线')
    if ready:
        planned = state.load(files.checked(specification['planned_configuration']))
        require(planned['phase'] == 'planned' and planned == planned_projection(specification), '就绪配置改变已预检计划')
        baseline = state.load(files.checked(specification['baseline_preflight']))
        require(baseline['passed'] is True and baseline['action'] == 'baseline' and baseline['phase'] == 'planned'
            and baseline['configuration'] == specification['planned_configuration']
            and baseline['implementation'] == state.identity(__file__) and baseline['modules'] == module_identities()
            and baseline['complete_scan'] is True, '缺少当前v6实现的全34/原40生产基线')
    for name in ['retained_configuration', 'previous_authority', 'source_preflight', 'production_contracts', 'v4_status_inspector']:
        files.checked(specification[name])
    contracts = state.load(specification['production_contracts']['path'])
    require(contracts['passed'] is True and type(contracts['tests_run']) is int and contracts['tests_run'] > 0
        and contracts['errors'] == contracts['failures'] == contracts['skipped'] == 0
        and contracts['production_modules'] == module_identities(), '当前全部生产模块必须先通过有限契约')
    previous = state.load(specification['previous_authority']['path'])
    previous_is_v5 = previous['implementation'] == state.identity(retained_batch.__file__)
    require(previous_is_v5 or previous['implementation'] == state.identity(__file__), '未知前版权威生产器')
    require(previous['modules'] == (retained_batch.module_identities() if previous_is_v5 else module_identities()),
        '前版权威实现已经变化')
    retained = retained_batch.load_configuration(specification['retained_configuration']['path'])
    original, base = retained['original'], retained['base']
    project_ids = retained['project_ids']
    require(previous['passed'] is True and previous['action'] == 'batch' and previous['complete_scan'] is True
        and previous['both_variants_generated'] == len(previous['projects']) == 34
        and [project['id'] for project in previous['projects']] == project_ids
        and len(previous['videos']) == len(original['all_ids']) == 40 and len(original['blocked']) == 6
        and previous['summary']['source_units'] == previous['summary']['translated_units'] == 6840
        and previous['summary']['quality_allowed'] == 34 and previous['summary']['quality_blocked'] == 6
        and version_counts(project.get('upload_version', 1) for project in previous['projects'])
            == {**previous['current_versions'], 'v6': previous['current_versions'].get('v6', 0)},
        '需要当前真实全34/原40及六门禁权威表')
    if previous_is_v5:
        require(previous['configuration'] == specification['retained_configuration'], '保留的v5配置不是当前权威配置')
    source = state.load(specification['source_preflight']['path'])
    require(source['passed'] is True and source['kind'] == source_preflight.KIND
        and source['implementation'] == state.identity(source_preflight.__file__)
        and source['modules'] == source_preflight.module_identities() and source['formal_authority'] == specification['previous_authority']
        and source['ready_for_encoding'] is False, '源预检必须与原权威一致，且不能冒充生产许可')
    versions, selected = apply_transitions(previous['projects'], specification['transitions'])
    require(version_counts(versions.values()) == specification['expected_versions']
        and summary_for(previous['summary'], specification['expected_versions']) == specification['expected_summary'],
        '明确迁移后的计数或汇总不符')
    routes = retained_batch.route_references(previous['projects'])
    require(specification['existing_v3_routes'] == routes, '必须保留全部既有v3精确证据路由')
    for route in routes.values():
        for reference in route.values():
            files.checked(reference)
    require(specification['v4_status_inspector'] == retained['configuration']['v4_status_inspector'], '既有v4检查器变化')
    for version in [4, 5, 6]:
        expected = {project['id']: previous['installed_runtime_evidence'][project['id']]
            for project in previous['projects'] if project.get('upload_version', 1) == version}
        require(specification['existing_v' + str(version) + '_runtime'] == expected, '既有版本运行证据缺失：v' + str(version))
        for reference in expected.values():
            files.checked(reference)
    source_selected = {project['id']: project for project in source['selected']}
    source_configuration = state.load(files.checked(source['configuration']))
    source_transitions = {transition['id']: transition for transition in source_configuration['transitions']}
    directories, runtimes = set(), set()
    for identifier, transition in selected.items():
        require(set(transition) == TRANSITION_FIELDS | ({'intent'} if ready else set()), '迁移字段或阶段混淆')
        directory = pairs.revision_directory(base.work_for(identifier), transition['directory'])
        runtime = files.canonical(transition['installed_runtime_path'])
        require(directory not in directories and runtime not in runtimes and runtime.is_relative_to(path.parent)
            and runtime.suffix == '.json' and runtime != path, '包目录或正式运行收据重复/越界')
        directories.add(directory)
        runtimes.add(runtime)
        for name in ['preview_checkpoint', 'master_candidate']:
            files.checked(transition[name])
        require(identifier in source_selected
            and transition['master_candidate'] == source_selected[identifier]['master_candidate']
            and transition['preview_checkpoint'] == source_transitions[identifier]['preview_checkpoint'],
            '源预检没有覆盖本次母版候选及预览断点')
        if ready:
            require(files.checked(transition['intent']) == directory / 'intent.json', '就绪意向不属于声明v6目录')
    if ready:
        source_preflight.assert_same_pairs(baseline['projects'], previous['projects'])
        require(baseline['current_versions'] == version_counts(project.get('upload_version', 1)
            for project in previous['projects']), '真实基线的当前版本计数不符')
    dispatch = {**retained['dispatch'], 'configuration': {**retained['dispatch']['configuration'],
        'existing_v3_routes': {identifier: {name: reference['path'] for name, reference in route.items()}
            for identifier, route in routes.items()}}}
    return {'configuration': specification, 'previous': previous, 'retained': retained, 'dispatch': dispatch,
        'original': original, 'base': base, 'project_ids': project_ids, 'selected': selected, 'versions': versions}


def inspect_existing(context, prior):
    work = context['base'].work_for(prior['id'])
    version = prior.get('upload_version', 1)
    result = (pairs.validate_current(work) if version == 6 else v5_pairs.validate_current(work) if version == 5
        else retained_pairs.existing_pair(prior['id'], prior, context['dispatch']))
    source_preflight.assert_same_pairs([result], [prior])
    return result


def inspect_pairs(context, stage):
    require(stage in {'baseline', 'preflight', 'packages'}, '未知v6双版本检查阶段')
    require(stage == 'baseline' or context['configuration']['phase'] == 'ready', '必须先建立独立就绪配置')
    projects = []
    for prior in context['previous']['projects']:
        identifier = prior['id']
        work = context['base'].work_for(identifier)
        with context['base'].read_lock(work):
            transition = context['selected'].get(identifier)
            if stage == 'packages' and transition is not None:
                result = pairs.validate_current(work)
                require(result['upload_version'] == 6 and result['intent'] == transition['intent'], '正式v6不是本次明确意向')
            else:
                result = inspect_existing(context, prior)
                if stage == 'preflight' and transition is not None:
                    verified = pairs.verify_revision(work, Path(transition['directory']))
                    require(verified['intent_reference'] == transition['intent']
                        and verified['intent']['base_package_manifest'] == prior['manifest']
                        and all(verified['intent']['original'][name] == prior[name] for name in ['master', 'upload']),
                        '候选意向不属于已预检原正式双版本')
                    result = {**result, 'prospective_v6': {'intent': transition['intent'],
                        'master': verified['replacements']['master'], 'upload': verified['replacements']['upload']}}
            for name in ['master', 'upload', 'manifest']:
                files.checked(result[name])
            projects.append(result)
    counts = version_counts(project.get('upload_version', 1) for project in projects)
    expected = (context['configuration']['expected_versions'] if stage == 'packages' else
        version_counts(project.get('upload_version', 1) for project in context['previous']['projects']))
    require([project['id'] for project in projects] == context['project_ids'] and counts == expected, '实际34部顺序或版本计数不符')
    return {'projects': projects, 'both_variants_generated': len(projects), 'current_versions': counts,
        **{'v' + str(version) + '_uploads': counts['v' + str(version)] for version in range(2, 7)},
        'expected_after_install': context['configuration']['expected_versions'],
        'installed_versions_checked': stage == 'packages', 'candidate_proofs_verified_by_preflight': stage == 'preflight'}


def source_quality(context):
    return retained_batch.source_quality(context['retained'])


def inspect_base(context, packages):
    sources = source_quality(context)
    package_map = {project['id']: project for project in packages['projects']}
    inspector = legacy_batch.load_module('mixed_v6_retained_v4_status', context['configuration']['v4_status_inspector'])
    records, formats, runtime_evidence = [], [], {}
    for source in sources['videos']:
        identifier = source['id']
        work = context['base'].work_for(identifier)
        with context['base'].read_lock(work):
            package = package_map.get(identifier)
            version = package.get('upload_version', 1) if package else 0
            if version in {4, 5, 6}:
                transition = context['selected'].get(identifier)
                runtime = (state.identity(transition['installed_runtime_path']) if transition and version == 6
                    else context['configuration']['existing_v' + str(version) + '_runtime'][identifier])
                method = {4: inspector.inspect_v4, 5: v5_status.inspect_v5, 6: new_status.inspect_v6}[version]
                record = method(work, package, source, runtime)
                runtime_evidence[identifier] = runtime
            else:
                record = (context['retained']['retained']['inspectors']['legacy_refresh'].legacy.inspect_x(context['base'].MEDIA)
                    if identifier.startswith('x_') else check_videos.inspect(identifier, context['base'].MEDIA, False))
            require(context['base'].canonical_id(record['id']) == identifier and record['state'] == 'verified'
                and record['source_units'] == source['source_units'] and record['translated_units'] == source['translated_units']
                and retained_pairs.stable_quality(record['quality_gate']) == retained_pairs.stable_quality(source['quality_gate']),
                '当前基础状态、来源或门禁变化：' + identifier)
            for reference in source['source_identities'].values():
                files.checked(reference)
            records.append(record)
            formats.append({'id': identifier, 'master_generated': package is not None, 'upload_generated': package is not None,
                'state': 'both_variants_generated' if package else 'not_packaged', **(package or {})})
    summary = {**sources['summary'], 'base_technical_verified': sum(record['state'] == 'verified' for record in records),
        'new_master_generated': len(package_map), 'both_new_variants_generated': len(package_map),
        **{'v' + str(version) + '_uploads': packages['current_versions']['v' + str(version)] for version in range(2, 7)}}
    require(summary == summary_for(context['previous']['summary'], packages['current_versions']), '原40基础汇总不符')
    return {'complete_scan': True, 'summary': summary, 'videos': records, 'new_format': formats,
        'fresh_source_quality': sources, 'installed_runtime_evidence': runtime_evidence,
        'fresh_full_decode_performed': False, 'legacy_delivery_checks_modified': False}


def run(configuration, action, output):
    context = load_configuration(configuration)
    output = files.canonical(output)
    require(output.is_relative_to(context['base'].ROOT) and output.parent.is_dir() and not output.exists(), '需要全新隐藏收据路径')
    report = {'version': 1, 'kind': KIND, 'checked_at': state.now(), 'passed': False, 'action': action,
        'phase': context['configuration']['phase'], 'configuration': state.identity(configuration),
        'implementation': state.identity(__file__), 'modules': module_identities(),
        'previous_authority': context['configuration']['previous_authority'], 'goal_status': 'active',
        'human_listened': False, 'potplayer_tested': False, 'production_mutated_by_validator': False,
        'fresh_full_decode_performed': False, 'production_baseline_for_encoding': False}
    try:
        if action in {'baseline', 'preflight', 'packages', 'batch'}:
            packages = inspect_pairs(context, 'packages' if action == 'batch' else action)
            report.update(packages)
            if action in {'baseline', 'batch'}:
                report.update(inspect_base(context, packages))
            report['production_baseline_for_encoding'] = action == 'baseline' and report['phase'] == 'planned'
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
    print(json.dumps({name: result[name] for name in ['action', 'passed', 'current_versions', 'summary',
        'production_baseline_for_encoding'] if name in result}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
