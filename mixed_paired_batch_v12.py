"""实际v2到v15的独立生产基线与混版验证，保留现有v1至v14的真实路由。"""

import argparse
import copy
import json
from pathlib import Path

import check_videos
import dubbing_state as state
import mixed_paired_batch_v3 as retained_batch
import paired_delivery_transaction as files
import mixed_paired_batch_v4 as retained_v7_batch
import paired_delivery_v7 as retained_v7_pairs
import paired_status_v7 as retained_v7_status
import mixed_paired_batch_v5 as retained_v8_batch
import paired_delivery_v8 as retained_v8_pairs
import paired_status_v8 as retained_v8_status
import mixed_paired_batch_v6 as retained_v9_batch
import paired_delivery_v9 as retained_v9_pairs
import paired_status_v9 as retained_v9_status
import mixed_paired_batch_v7 as retained_v10_batch
import paired_delivery_v10 as retained_v10_pairs
import paired_status_v10 as retained_v10_status
import mixed_paired_batch_v8 as retained_v11_batch
import paired_delivery_v11 as retained_v11_pairs
import paired_status_v11 as retained_v11_status
import mixed_paired_batch_v9 as retained_v12_batch
import paired_delivery_v12 as retained_v12_pairs
import paired_status_v12 as retained_v12_status
import mixed_paired_batch_v10 as retained_v13_batch
import paired_delivery_v13 as retained_v13_pairs
import paired_status_v13 as retained_v13_status
import mixed_paired_batch_v11 as retained_v14_batch
import paired_delivery_v14 as retained_v14_pairs
import paired_status_v14 as retained_v14_status
import paired_delivery_v15 as pairs
import paired_status_v15 as new_status


KIND = 'mixed-paired-v15-preserving-v1-v14'
TRANSITION_FIELDS = {'id', 'from', 'to', 'directory', 'installed_runtime_path', 'preview_checkpoint', 'master_candidate', 'preview_registration', 'preview_registration_exit', 'preview_acceptance_exit', 'preview_acceptance_log'}
CONFIGURATION_FIELDS = {'version', 'kind', 'phase', 'implementation', 'modules', 'previous_authority',
    'retained_configuration', 'production_contracts', 'existing_versioned_runtime', 'transitions', 'expected_versions', 'expected_summary'}
require = pairs.require
assert_same_pairs = retained_batch.source_preflight.assert_same_pairs


def module_identities():
    return {'batch': state.identity(__file__), 'retained_batch': state.identity(retained_batch.__file__),
        'v15_status': state.identity(new_status.__file__),
        'retained_v14_batch': state.identity(retained_v14_batch.__file__),
        'retained_v14_status': state.identity(retained_v14_status.__file__),
        **{'retained_v14_' + name: reference for name, reference in retained_v14_pairs.module_identities().items()},
        'retained_v13_batch': state.identity(retained_v13_batch.__file__),
        'retained_v13_status': state.identity(retained_v13_status.__file__),
        **{'retained_v13_' + name: reference for name, reference in retained_v13_pairs.module_identities().items()},
        'retained_v12_batch': state.identity(retained_v12_batch.__file__),
        'retained_v12_status': state.identity(retained_v12_status.__file__),
        **{'retained_v12_' + name: reference for name, reference in retained_v12_pairs.module_identities().items()},
        'retained_v11_batch': state.identity(retained_v11_batch.__file__),
        'retained_v11_status': state.identity(retained_v11_status.__file__),
        **{'retained_v11_' + name: reference for name, reference in retained_v11_pairs.module_identities().items()},
        'retained_v10_batch': state.identity(retained_v10_batch.__file__),
        'retained_v10_status': state.identity(retained_v10_status.__file__),
        **{'retained_v10_' + name: reference for name, reference in retained_v10_pairs.module_identities().items()},
        'retained_v9_batch': state.identity(retained_v9_batch.__file__),
        'retained_v9_status': state.identity(retained_v9_status.__file__),
        **{'retained_v9_' + name: reference for name, reference in retained_v9_pairs.module_identities().items()},
        'retained_v8_batch': state.identity(retained_v8_batch.__file__),
        'retained_v8_status': state.identity(retained_v8_status.__file__),
        **{'retained_v8_' + name: reference for name, reference in retained_v8_pairs.module_identities().items()},
        'retained_v7_batch': state.identity(retained_v7_batch.__file__),
        'retained_v7_status': state.identity(retained_v7_status.__file__),
        **{'retained_v7_' + name: reference for name, reference in retained_v7_pairs.module_identities().items()},
        **{'retained_' + name: reference for name, reference in retained_batch.module_identities().items()},
        **{'v15_' + name: reference for name, reference in pairs.module_identities().items()}}


def version_counts(versions):
    values = list(versions)
    require(all(type(version) is int and 1 <= version <= 15 for version in values), '不支持的实际版本')
    return {'formal_pairs': len(values), **{'v' + str(version): values.count(version) for version in range(1, 16)}}


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
            and transition['to'] == 15, '必须明确且唯一声明实际v2到v15，不能改变其他既有版本')
        versions[identifier] = 15
        selected[identifier] = transition
    require(bool(selected), '缺少明确v2到v15迁移')
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
    return {**previous, **{'v' + str(version) + '_uploads': counts['v' + str(version)] for version in range(2, 16)}}


def load_configuration(path):
    path = files.canonical(path)
    specification = state.load(path)
    ready = specification.get('phase') == 'ready'
    require(isinstance(specification, dict) and set(specification) == CONFIGURATION_FIELDS | (
        {'planned_configuration', 'baseline_preflight'} if ready else set())
        and type(specification['version']) is int and specification['version'] == 1
        and specification['kind'] == KIND and specification['phase'] in {'planned', 'ready'}
        and specification['implementation'] == state.identity(__file__)
        and specification['modules'] == module_identities(), 'v15混版配置、阶段、字段或实现变化')
    for name in ['previous_authority', 'retained_configuration', 'production_contracts']:
        files.checked(specification[name])
    contracts = state.load(specification['production_contracts']['path'])
    require(contracts['passed'] is True and type(contracts['tests_run']) is int and contracts['tests_run'] > 0
        and contracts['errors'] == contracts['failures'] == contracts['skipped'] == 0
        and contracts['production_modules'] == module_identities(), '当前v15生产实现缺少真实有限契约')
    previous = state.load(specification['previous_authority']['path'])
    previous_is_retained = previous['implementation'] == state.identity(retained_v14_batch.__file__)
    require(previous_is_retained or previous['implementation'] == state.identity(__file__), '未知前版权威生产器')
    require(previous['modules'] == (retained_v14_batch.module_identities() if previous_is_retained else module_identities()),
        '前版权威实现变化')
    retained_v14 = retained_v14_batch.load_configuration(specification['retained_configuration']['path'])
    retained = retained_v14['retained']
    if previous_is_retained:
        require(previous['configuration'] == specification['retained_configuration'], '保留配置不是当前权威配置')
    original, base, project_ids = retained['original'], retained['base'], retained['project_ids']
    counts = version_counts(project.get('upload_version', 1) for project in previous['projects'])
    require(previous['passed'] is True and previous['action'] == 'batch' and previous['complete_scan'] is True
        and previous['both_variants_generated'] == len(previous['projects']) == 34
        and [project['id'] for project in previous['projects']] == project_ids
        and len(previous['videos']) == len(original['all_ids']) == 40 and len(original['blocked']) == 6
        and previous['summary']['source_units'] == previous['summary']['translated_units'] == 6840
        and previous['summary']['quality_allowed'] == 34 and previous['summary']['quality_blocked'] == 6
        and counts == {**previous['current_versions'], 'v15': previous['current_versions'].get('v15', 0)},
        '需要真实当前全34/原40及原六声音门禁权威')
    expected_runtime = {project['id']: previous['installed_runtime_evidence'][project['id']]
        for project in previous['projects'] if project.get('upload_version', 1) in {7, 8, 9, 10, 11, 12, 13, 14, 15}}
    require(specification['existing_versioned_runtime'] == expected_runtime, '已有v7、v8或v15正式运行证据缺失')
    for reference in expected_runtime.values():
        files.checked(reference)
    versions, selected = apply_transitions(previous['projects'], specification['transitions'])
    require(version_counts(versions.values()) == specification['expected_versions']
        and summary_for(previous['summary'], specification['expected_versions']) == specification['expected_summary'],
        '声明迁移后的版本或汇总不符')
    directories, runtimes = set(), set()
    for transition in selected.values():
        require(set(transition) == TRANSITION_FIELDS | ({'intent'} if ready else set()), '迁移字段或阶段混淆')
        directory = pairs.revision_directory(base.work_for(transition['id']), transition['directory'])
        runtime = files.canonical(transition['installed_runtime_path'])
        require(directory not in directories and runtime not in runtimes and runtime.is_relative_to(path.parent)
            and runtime.suffix == '.json' and runtime != path, '包目录或运行收据越界/重复')
        directories.add(directory)
        runtimes.add(runtime)
        for name in ['preview_checkpoint', 'master_candidate', 'preview_registration', 'preview_registration_exit', 'preview_acceptance_exit', 'preview_acceptance_log']:
            files.checked(transition[name])
        if ready:
            require(files.checked(transition['intent']) == directory / 'intent.json', '就绪意向不属于声明目录')
    if ready:
        planned = state.load(files.checked(specification['planned_configuration']))
        baseline = state.load(files.checked(specification['baseline_preflight']))
        require(planned['phase'] == 'planned' and planned == planned_projection(specification)
            and baseline['passed'] is True and baseline['action'] == 'baseline' and baseline['phase'] == 'planned'
            and baseline['configuration'] == specification['planned_configuration']
            and baseline['implementation'] == state.identity(__file__) and baseline['modules'] == module_identities()
            and baseline['complete_scan'] is True and baseline['production_baseline_for_encoding'] is True
            and baseline['current_versions'] == counts and set(baseline['candidate_sources']) == set(selected),
            '就绪配置改变原计划或缺少当前生产基线')
        assert_same_pairs(baseline['projects'], previous['projects'])
    return {'configuration': specification, 'previous': previous, 'retained': retained, 'original': original,
        'base': base, 'project_ids': project_ids, 'selected': selected, 'versions': versions}


def inspect_existing(context, prior):
    result = (pairs.validate_current(context['base'].work_for(prior['id'])) if prior.get('upload_version', 1) == 15
        else retained_v14_pairs.validate_current(context['base'].work_for(prior['id']))
            if prior.get('upload_version', 1) == 14
        else retained_v13_pairs.validate_current(context['base'].work_for(prior['id']))
            if prior.get('upload_version', 1) == 13
        else retained_v12_pairs.validate_current(context['base'].work_for(prior['id']))
            if prior.get('upload_version', 1) == 12
        else retained_v11_pairs.validate_current(context['base'].work_for(prior['id']))
            if prior.get('upload_version', 1) == 11
        else retained_v10_pairs.validate_current(context['base'].work_for(prior['id']))
            if prior.get('upload_version', 1) == 10
        else retained_v9_pairs.validate_current(context['base'].work_for(prior['id']))
            if prior.get('upload_version', 1) == 9
        else retained_v8_pairs.validate_current(context['base'].work_for(prior['id']))
            if prior.get('upload_version', 1) == 8
        else retained_v7_pairs.validate_current(context['base'].work_for(prior['id']))
            if prior.get('upload_version', 1) == 7 else retained_batch.inspect_existing(context['retained'], prior))
    assert_same_pairs([result], [prior])
    return result


def inspect_pairs(context, stage):
    require(stage in {'baseline', 'preflight', 'packages'}, '未知v15检查阶段')
    require(stage == 'baseline' or context['configuration']['phase'] == 'ready', '必须先有独立就绪配置')
    projects = []
    for prior in context['previous']['projects']:
        identifier = prior['id']
        work = context['base'].work_for(identifier)
        with context['base'].read_lock(work):
            transition = context['selected'].get(identifier)
            if stage == 'packages' and transition is not None:
                result = pairs.validate_current(work)
                require(result['upload_version'] == 15 and result['intent'] == transition['intent'], '正式v15不是本次意向')
            else:
                result = inspect_existing(context, prior)
                if stage == 'preflight' and transition is not None:
                    verified = pairs.verify_revision(work, Path(transition['directory']))
                    require(verified['intent_reference'] == transition['intent']
                        and verified['intent']['base_package_manifest'] == prior['manifest']
                        and all(verified['intent']['original'][name] == prior[name] for name in ['master', 'upload']),
                        '候选意向不属于已预检原正式双版本')
                    result = {**result, 'prospective_v15': {'intent': transition['intent'],
                        'master': verified['replacements']['master'], 'upload': verified['replacements']['upload']}}
            for name in ['master', 'upload', 'manifest']:
                files.checked(result[name])
            projects.append(result)
    counts = version_counts(project.get('upload_version', 1) for project in projects)
    expected = (context['configuration']['expected_versions'] if stage == 'packages' else
        version_counts(project.get('upload_version', 1) for project in context['previous']['projects']))
    require([project['id'] for project in projects] == context['project_ids'] and counts == expected, '实际34部顺序或版本不符')
    return {'projects': projects, 'both_variants_generated': len(projects), 'current_versions': counts,
        **{'v' + str(version) + '_uploads': counts['v' + str(version)] for version in range(2, 16)},
        'expected_after_install': context['configuration']['expected_versions'], 'installed_versions_checked': stage == 'packages',
        'candidate_proofs_verified_by_preflight': stage == 'preflight'}


def source_quality(context):
    return retained_batch.source_quality(context['retained'])


def inspect_base(context, packages):
    sources = source_quality(context)
    package_map = {project['id']: project for project in packages['projects']}
    legacy = context['retained']
    inspector = retained_batch.legacy_batch.load_module('v7_retained_v4_status', legacy['configuration']['v4_status_inspector'])
    records, formats, runtime_evidence = [], [], {}
    for source in sources['videos']:
        identifier = source['id']
        work = context['base'].work_for(identifier)
        with context['base'].read_lock(work):
            package = package_map.get(identifier)
            version = package.get('upload_version', 1) if package else 0
            if version in {4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}:
                transition = context['selected'].get(identifier)
                runtime = (state.identity(transition['installed_runtime_path']) if transition and version == 15
                    else context['previous']['installed_runtime_evidence'][identifier])
                method = {4: inspector.inspect_v4, 5: retained_batch.v5_status.inspect_v5,
                    6: retained_batch.new_status.inspect_v6, 7: retained_v7_status.inspect_v7, 8: retained_v8_status.inspect_v8, 9: retained_v9_status.inspect_v9, 10: retained_v10_status.inspect_v10, 11: retained_v11_status.inspect_v11, 12: retained_v12_status.inspect_v12, 13: retained_v13_status.inspect_v13, 14: retained_v14_status.inspect_v14, 15: new_status.inspect_v15}[version]
                record = method(work, package, source, runtime)
                runtime_evidence[identifier] = runtime
            else:
                record = (legacy['retained']['retained']['inspectors']['legacy_refresh'].legacy.inspect_x(context['base'].MEDIA)
                    if identifier.startswith('x_') else check_videos.inspect(identifier, context['base'].MEDIA, False))
            require(context['base'].canonical_id(record['id']) == identifier and record['state'] == 'verified'
                and record['source_units'] == source['source_units'] and record['translated_units'] == source['translated_units']
                and retained_batch.retained_pairs.stable_quality(record['quality_gate'])
                    == retained_batch.retained_pairs.stable_quality(source['quality_gate']), '当前基础状态或门禁变化: ' + identifier)
            for reference in source['source_identities'].values():
                files.checked(reference)
            records.append(record)
            formats.append({'id': identifier, 'master_generated': package is not None, 'upload_generated': package is not None,
                'state': 'both_variants_generated' if package else 'not_packaged', **(package or {})})
    summary = {**sources['summary'], 'base_technical_verified': sum(record['state'] == 'verified' for record in records),
        'new_master_generated': len(package_map), 'both_new_variants_generated': len(package_map),
        **{'v' + str(version) + '_uploads': packages['current_versions']['v' + str(version)] for version in range(2, 16)}}
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
            if action == 'baseline':
                report['candidate_sources'] = {}
                for identifier, transition in context['selected'].items():
                    prior = next(project for project in context['previous']['projects'] if project['id'] == identifier)
                    source = pairs.validate_preview_source(context['base'].work_for(identifier), transition, prior)
                    report['candidate_sources'][identifier] = {name: source[name] for name in
                        ['original', 'source_upload_version', 'master_format_version', 'preview_version']}
                report['production_baseline_for_encoding'] = report['phase'] == 'planned'
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
