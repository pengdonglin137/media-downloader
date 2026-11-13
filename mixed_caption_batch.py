"""显式推进v3字幕上传版，同时保留既有v4双版本与原40部范围。"""

import argparse
import copy
from pathlib import Path

import check_videos
import delivery_quality
import dubbing_state as state
import paired_batch_v4 as previous_batch
import paired_delivery_transaction as files
import paired_delivery_v4 as pairs
import upload_delivery_v3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def module_identities():
    return {name: state.identity(module.__file__) for name, module in {
        'dispatcher': previous_batch, 'pair_validator': pairs, 'v3_delivery': upload_delivery_v3,
        'base_inspector': check_videos, 'quality': delivery_quality, 'state': state}.items()}


def transition_versions(projects, transitions):
    versions = {project['id']: project.get('upload_version', 1) for project in projects}
    require(len(versions) == len(projects) and all(type(version) is int and version in {1, 2, 3, 4}
            for version in versions.values()), '前版工程重复或版本未知')
    selected = {}
    require(bool(transitions), '需要显式字幕迁移')
    for transition in transitions:
        project_id = transition['id']
        require(project_id in versions and project_id not in selected
                and type(transition['from']) is int and type(transition['to']) is int
                and transition['from'] == versions[project_id] and transition['from'] in {1, 2}
                and transition['to'] == 3, '只支持明确v1/v2到v3字幕上传迁移，既有v4不可迁移')
        versions[project_id] = 3
        selected[project_id] = transition
    return versions, selected


def stable_quality(quality):
    return {key: value for key, value in quality.items() if key != 'checked_at'}


def load_configuration(path):
    path = files.canonical(path)
    specification = state.load(path)
    require(specification['version'] == 1 and specification['kind'] == 'mixed-caption-v3-with-existing-v4'
            and specification['implementation'] == state.identity(__file__)
            and specification['modules'] == module_identities(), '混版配置版本或实现身份变化')
    for key in ['previous_configuration', 'previous_pairs', 'previous_base_status', 'v4_status_inspector']:
        files.checked(specification[key])
    context = previous_batch.load_configuration(specification['previous_configuration']['path'])
    previous = state.load(specification['previous_pairs']['path'])
    base_status = state.load(specification['previous_base_status']['path'])
    require(previous['passed'] and base_status['passed'] and previous['both_variants_generated'] == 34
            and [entry['id'] for entry in previous['projects']] == context['project_ids']
            and base_status['complete_scan'] and base_status['summary']['scope'] == 40
            and base_status['summary']['base_technical_verified'] == 40, '需要原34双版本和全40基础权威表')
    versions, selected = transition_versions(previous['projects'], specification['transitions'])
    require(previous_batch.version_counts(versions.values()) == specification['expected_versions'], '预期版本计数不符')
    expected_summary = copy.deepcopy(base_status['summary'])
    expected_summary.update({'v' + str(version) + '_uploads': list(versions.values()).count(version)
                             for version in [2, 3, 4]})
    require(expected_summary == specification['expected_summary'], '批量摘要只允许显式版本迁移')
    v4_ids = {entry['id'] for entry in previous['projects'] if entry.get('upload_version', 1) == 4}
    require(set(specification['existing_v4_runtime']) == v4_ids, '所有既有v4须有正式路径运行证据')
    for observation in specification['existing_v4_runtime'].values():
        files.checked(observation)
    v3_ids = {entry['id'] for entry in previous['projects'] if entry.get('upload_version', 1) == 3}
    require(set(specification['existing_v3_routes']) == v3_ids, '所有既有v3须有明确证据路由')
    for project_id, route in specification['existing_v3_routes'].items():
        prior = next(entry for entry in previous['projects'] if entry['id'] == project_id)
        require(set(route) == {'fidelity', 'master_runtime', 'runtime'}
                and route['fidelity'] == prior['fidelity_evidence']['path']
                and route['master_runtime'] == prior['unchanged_master_vlc_evidence']['path']
                and route['runtime'] == prior['vlc_evidence']['path'], '既有v3路由不属于前版正式表')
    for transition in selected.values():
        directory = Path(transition['directory'])
        require(directory.is_absolute() and directory.is_relative_to(context['base'].ROOT)
                and not directory.is_symlink() and (not directory.exists() or directory.resolve() == directory), '候选目录须为真实隐藏路径')
        route = transition['evidence']
        require(set(route) == {'fidelity', 'master_runtime', 'runtime'}
                and Path(route['runtime']) == directory / 'canonical-upload-vlc-result.json', '目标v3运行证据路径不符')
        prior = next(entry for entry in previous['projects'] if entry['id'] == transition['id'])
        require(route['fidelity'] == prior['fidelity_evidence']['path']
                and route['master_runtime'] == prior['vlc_evidence']['path'], '目标未沿用原母版帧/运行证据')
    inspectors = {key: previous_batch.load_module('mixed_caption_' + key, reference)
                  for key, reference in context['context']['specification']['modules'].items()}
    return {'configuration': specification, 'previous': previous, 'previous_base': base_status,
        'context': context, 'selected': selected, 'versions': versions, 'inspectors': inspectors}


def existing_pair(project_id, prior, context):
    base = context['context']['base']
    original = context['context']['context']
    version = prior.get('upload_version', 1)
    work = base.work_for(project_id)
    actual_version = state.load(work / 'upload_export.json')['version']
    require(actual_version == version, '未选中工程实际版本变化：' + project_id)
    if version == 4:
        result = pairs.validate_current(work)
    elif version == 3:
        result = context['inspectors']['v3_inspector'].inspect_v3(project_id, context['configuration']['existing_v3_routes'][project_id])
    else:
        result = base.inspect_pair(project_id, original, context['inspectors'])
    for key in ['master', 'upload', 'manifest']:
        require(result[key] == prior[key], '未选中工程身份变化：' + project_id + '/' + key)
    result.setdefault('upload_version', version)
    return result


def inspect_pairs(context, installed):
    base = context['context']['base']
    projects = []
    for prior in context['previous']['projects']:
        project_id = prior['id']
        work = base.work_for(project_id)
        with base.read_lock(work):
            transition = context['selected'].get(project_id)
            if installed and transition is not None:
                result = context['inspectors']['v3_inspector'].inspect_v3(project_id, transition['evidence'])
                current = upload_delivery_v3.validate_current(work)
                require(result['upload_version'] == 3 and result['master'] == prior['master']
                        and result['manifest'] == prior['manifest']
                        and current['dependencies']['candidate_result']['path'] == str(Path(transition['directory']) / 'candidate-result.json'),
                        '目标必须来自声明v3候选，母版/字幕包保持')
            else:
                result = existing_pair(project_id, prior, context)
            for key in ['master', 'upload', 'manifest']:
                files.checked(result[key])
            projects.append(result)
    require([entry['id'] for entry in projects] == context['context']['project_ids'], '原34部顺序变化')
    counts = previous_batch.version_counts(entry.get('upload_version', 1) for entry in projects)
    expected = (context['configuration']['expected_versions'] if installed else previous_batch.version_counts(
        entry.get('upload_version', 1) for entry in context['previous']['projects']))
    require(counts == expected, '实际版本数量不符合显式阶段')
    return {'projects': projects, 'both_variants_generated': len(projects), 'current_versions': counts,
            'v2_uploads': counts['v2'], 'v3_uploads': counts['v3'], 'v4_uploads': counts['v4'],
            'installed_versions_checked': installed, 'candidate_proofs_verified_by_preflight': False}


def source_quality(context):
    return previous_batch.source_quality(context['context'])


def inspect_base(context, packages):
    base, original = context['context']['base'], context['context']['context']
    sources = source_quality(context)
    package_map = {entry['id']: entry for entry in packages['projects']}
    v4_status = previous_batch.load_module('retained_v4_base_status', context['configuration']['v4_status_inspector'])
    records, formats = [], []
    for source in sources['videos']:
        project_id = source['id']
        work = base.work_for(project_id)
        with base.read_lock(work):
            package = package_map.get(project_id)
            if package and package['upload_version'] == 4:
                record = v4_status.inspect_v4(work, package, source, context['configuration']['existing_v4_runtime'][project_id])
            else:
                record = (context['inspectors']['legacy_refresh'].legacy.inspect_x(base.MEDIA) if project_id.startswith('x_')
                          else check_videos.inspect(project_id, base.MEDIA, False))
            require(base.canonical_id(record['id']) == project_id and record['state'] == 'verified'
                    and record['source_units'] == source['source_units']
                    and record['translated_units'] == source['translated_units']
                    and stable_quality(record['quality_gate']) == stable_quality(source['quality_gate']),
                    '当前基础/源译稿/质量状态不符：' + project_id)
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
        'fresh_source_quality': sources, 'fresh_full_decode_performed': False,
        'legacy_delivery_checks_modified': False}


def run(configuration, action, output):
    context = load_configuration(configuration)
    output = files.canonical(output)
    require(output.is_relative_to(context['context']['base'].ROOT) and output.parent.is_dir() and not output.exists(),
            '需要全新隐藏批量收据路径')
    report = {'version': 1, 'kind': 'mixed-caption-v3-with-existing-v4', 'checked_at': state.now(), 'passed': False,
        'action': action, 'configuration': state.identity(configuration), 'implementation': state.identity(__file__),
        'modules': module_identities(), 'previous_pairs': context['configuration']['previous_pairs'],
        'goal_status': 'active', 'human_listened': False, 'potplayer_tested': False,
        'production_mutated_by_validator': False, 'fresh_full_decode_performed': False}
    try:
        if action in {'preflight', 'packages', 'batch'}:
            package_result = inspect_pairs(context, installed=action != 'preflight')
            report.update(package_result)
            if action == 'batch':
                report.update(inspect_base(context, package_result))
        elif action == 'source-quality':
            report.update(source_quality(context))
        else:
            require(action == 'configuration', '未知动作')
            report.update(project_ids=context['context']['project_ids'], all_ids=context['context']['context']['all_ids'],
                          expected_versions=context['configuration']['expected_versions'])
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
    parser.add_argument('action', choices=['configuration', 'preflight', 'packages', 'batch', 'source-quality'])
    parser.add_argument('--configuration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    result = run(options.configuration, options.action, options.output)
    print({key: result[key] for key in ['action', 'passed', 'current_versions', 'summary'] if key in result}, flush=True)


if __name__ == '__main__':
    main()
