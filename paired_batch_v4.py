"""保持原40项范围的显式v4双版本分派；复用未变更的v1/v2/v3检查器。"""

import argparse
import importlib.util
import json
from pathlib import Path

import delivery_quality
import dubbing_state as state
import paired_delivery_transaction as files
import paired_delivery_v4 as pairs


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_module(name, reference):
    files.checked(reference)
    specification = importlib.util.spec_from_file_location(name, reference['path'])
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def apply_transitions(projects, transitions):
    versions = {project['id']: project.get('upload_version', 1) for project in projects}
    require(len(versions) == len(projects) and all(type(version) is int and version in {1, 2, 3, 4}
            for version in versions.values()), '前版双版本集合或版本无效')
    selected = {}
    for transition in transitions:
        project_id = transition['id']
        require(project_id in versions and project_id not in selected and transition['from'] == versions[project_id] == 1
                and transition['to'] == 4 and type(transition['from']) is int and type(transition['to']) is int,
                '必须显式且唯一地声明受支持的v1到v4迁移')
        versions[project_id] = 4
        selected[project_id] = transition
    require(bool(selected), '没有显式v4迁移')
    return versions, selected


def version_counts(versions):
    values = list(versions)
    return {'formal_pairs': len(values), **{'v' + str(version): values.count(version) for version in [1, 2, 3, 4]}}


def load_configuration(path):
    path = files.canonical(path)
    specification = state.load(path)
    require(specification['version'] == 4 and specification['kind'] == 'explicit-paired-batch-v4'
            and specification['implementation'] == state.identity(__file__)
            and specification['pair_validator'] == state.identity(pairs.__file__), '新批次版本或实现身份变化')
    for key in ['base_configuration', 'base_dispatcher', 'legacy_baseline', 'previous_pairs']:
        files.checked(specification[key])
    previous = state.load(specification['previous_pairs']['path'])
    baseline = state.load(specification['legacy_baseline']['path'])
    require(previous['passed'] and previous['both_variants_generated'] == len(previous['projects']) == 34,
            '需要完整原34部双版本权威表')
    base = load_module('retained_configured_batch_v87', specification['base_dispatcher'])
    context = base.load_configuration(Path(specification['base_configuration']['path']))
    project_ids = [project['id'] for project in previous['projects']]
    baseline_versions = {project['id']: project.get('upload_version', 1) for project in baseline['projects']}
    require(project_ids == context['project_ids'] == [project['id'] for project in baseline['projects']]
            and baseline['passed'] and baseline['configuration'] == specification['base_configuration']
            and baseline_versions == context['target_versions']
            and all(project.get('upload_version', 1) in {baseline_versions[project['id']], 4}
                    for project in previous['projects']),
            '原双版本顺序、范围或版本路由发生变化')
    versions, selected = apply_transitions(previous['projects'], specification['transitions'])
    require(version_counts(versions.values()) == specification['expected_after_install']
            and len(context['all_ids']) == 40 and len(context['blocked']) == 6,
            '新版本计数或原40部/六门禁范围不符')
    for project_id, transition in selected.items():
        work = base.work_for(project_id)
        directory = pairs.revision_directory(work, transition['directory'])
        files.checked(transition['intent'])
        require(transition['intent']['path'] == str(directory / 'intent.json'), 'v4迁移意向不属于声明生产包')
    return {'configuration': specification, 'previous': previous, 'base': base, 'context': context,
            'target_versions': versions, 'selected': selected, 'project_ids': project_ids}


def inspect_pairs(context, installed):
    base, original = context['base'], context['context']
    inspectors = {key: base.load_module('retained_v4_' + key, reference)
                  for key, reference in original['specification']['modules'].items()}
    previous = {project['id']: project for project in context['previous']['projects']}
    result = []
    for project_id in context['project_ids']:
        work = base.work_for(project_id)
        with base.read_lock(work):
            actual_version = state.load(work / 'upload_export.json')['version']
            transition = context['selected'].get(project_id)
            if transition is not None:
                if installed:
                    require(actual_version == transition['to'], '目标v4工程没有真实安装')
                    row = pairs.validate_current(work)
                    require(row['intent'] == transition['intent'], '正式v4工程不是声明迁移意向')
                else:
                    require(actual_version == transition['from'], '预演应保留旧正式版本')
                    verified = pairs.verify_revision(work, Path(transition['directory']))
                    require(verified['intent_reference'] == transition['intent'], '预演未使用当前意向')
                    row = {'id': project_id, 'upload_version': actual_version,
                        'master': previous[project_id]['master'], 'upload': previous[project_id]['upload'],
                        'manifest': previous[project_id]['manifest'],
                        'prospective_v4': {'master': verified['replacements']['master'],
                            'upload': verified['replacements']['upload'], 'intent': transition['intent']},
                        'production_applied': False}
                    for key in ['master', 'upload', 'manifest']:
                        files.checked(row[key])
            else:
                require(actual_version == previous[project_id].get('upload_version', 1), '未选中工程被改变版本：' + project_id)
                row = pairs.validate_current(work) if actual_version == 4 else base.inspect_pair(project_id, original, inspectors)
                for key in ['master', 'upload', 'manifest']:
                    require(row[key] == previous[project_id][key], '未选中工程身份变化：' + project_id)
            result.append(row)
    require([row['id'] for row in result] == context['project_ids'], '批量检查改变原34部顺序')
    counts = version_counts(row.get('upload_version', 1) for row in result)
    expected = context['configuration']['expected_after_install'] if installed else version_counts(
        row.get('upload_version', 1) for row in context['previous']['projects'])
    require(counts == expected, '实际正式版本计数与声明不符')
    return {'projects': result, 'current_versions': counts, 'both_variants_generated': len(result),
        'v2_uploads': counts['v2'], 'v3_uploads': counts['v3'], 'v4_uploads': counts['v4'],
        'expected_after_install': context['configuration']['expected_after_install'],
        'production_applied': False, 'installed_versions_checked': installed,
        'scope': 'Exact prior 34 pair order; selected explicit v4 path and 33 unchanged versioned inspectors.'}


def source_quality(context):
    base, original = context['base'], context['context']
    records = []
    for project_id in original['all_ids']:
        work = base.work_for(project_id)
        with base.read_lock(work):
            identities = {name: state.identity(work / name) for name in ['en.srt', 'zh.srt', 'translation.tsv',
                                                                        'compilation.json', 'narration.json']}
            quality = delivery_quality.evaluate(work)
            records.append({'id': project_id, 'source_units': len(state.cues(work / 'en.srt')),
                'translated_units': len(state.cues(work / 'zh.srt')), 'source_identities': identities,
                'quality_gate': quality})
            require(all(state.matches(observation) for observation in identities.values()), '源/译稿扫描期间输入变化')
    blocked = {record['id'] for record in records if not record['quality_gate']['allowed']}
    summary = {'scope': len(records), 'source_units': sum(record['source_units'] for record in records),
        'translated_units': sum(record['translated_units'] for record in records),
        'quality_allowed': len(records) - len(blocked), 'quality_blocked': len(blocked)}
    expected = original['specification']['expected_summary']
    require(blocked == original['blocked'] and summary == {key: expected[key] for key in summary},
            '原40部源译稿数量或六个原质量门禁发生变化')
    return {'complete_scan': True, 'summary': summary, 'videos': records,
        'scope': 'Fresh exact 39 YouTube + 1 X source/translation counts and current quality gates; not a new full technical decode.'}


def run(configuration, action, output):
    context = load_configuration(configuration)
    output = files.canonical(output)
    allowed_root = context['base'].ROOT
    require(output.is_relative_to(allowed_root) and output.parent.is_dir() and not output.exists(), '需要全新隐藏批次收据路径')
    report = {'version': 4, 'checked_at': state.now(), 'passed': False, 'action': action,
        'implementation': state.identity(__file__), 'pair_validator': state.identity(pairs.__file__),
        'configuration': state.identity(configuration), 'base_dispatcher': context['configuration']['base_dispatcher'],
        'inspector_implementations': context['context']['specification']['modules'],
        'previous_pairs': context['configuration']['previous_pairs'], 'goal_status': 'active',
        'legacy_baseline': context['configuration']['legacy_baseline'],
        'human_listened': False, 'potplayer_tested': False}
    try:
        if action == 'source-quality':
            report.update(source_quality(context))
        elif action in {'preflight', 'packages'}:
            report.update(inspect_pairs(context, installed=action == 'packages'))
        else:
            report.update(project_ids=context['project_ids'], all_ids=context['context']['all_ids'],
                          expected_after_install=context['configuration']['expected_after_install'])
        require(state.matches(report['configuration']), '批次配置在检查期间变化')
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
    parser.add_argument('action', choices=['configuration', 'preflight', 'packages', 'source-quality'])
    parser.add_argument('--configuration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    report = run(options.configuration, options.action, options.output)
    print(json.dumps({key: report[key] for key in ['action', 'passed', 'current_versions', 'summary'] if key in report},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
