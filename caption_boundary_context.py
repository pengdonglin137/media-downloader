"""为当前成片中的登记跨条命中附加真实语义组与源文，不自动批准修订。"""

import argparse
import collections
import fcntl
from pathlib import Path

import caption_boundary_repair_v2 as display_domain
import dubbing_state as state
import paired_delivery_transaction as files
import speech_groups


KIND = 'current-display-boundary-source-context-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def module_identities():
    return {name: state.identity(module.__file__) for name, module in {
        'display_domain': display_domain, 'state': state, 'file_identity': files,
        'speech_groups': speech_groups}.items()}


def contexts_for(work, offset):
    require(type(offset) in {int, float} and offset >= 0, '正文偏移无效')
    policy = speech_groups.load_policy(work)
    require(policy is not None, '缺少有效的原语义组策略')
    groups = {tuple(group['unit_ids']): group for group in policy['groups']}
    original = speech_groups.original_units(work)
    contexts = []
    for unit in speech_groups.units(work):
        group = groups.get(tuple(unit['unit_ids']))
        audio_window = [unit['start'] + offset, unit['end'] + offset]
        serialized, display_window = display_domain.display_window(*audio_window)
        contexts.append({'unit_ids': unit['unit_ids'], 'voice': unit['voice'],
            'speaker_id': group['speaker_id'] if group else 'unknown',
            'role_basis': 'validated-registered-group' if group else 'unregistered-singleton',
            'source_window_seconds': [unit['start'], unit['end']],
            'audio_window_seconds': audio_window, 'display_window_seconds': display_window,
            'display_owner_srt': serialized, 'body_offset_seconds': offset,
            'group_source_review_note': group['source_review_note'] if group else None,
            'group_sync_note': group['sync_note'] if group else None,
            'actual_spoken': unit['spoken'], 'actual_group_chinese': unit['zh'],
            'source_units': [original[number - 1] for number in unit['unit_ids']]})
    return contexts, original


def annotate_pair(cues, findings, contexts, source_units, overlapping_cues):
    require(bool(findings), '缺少同一相邻对的命中')
    left_id, right_id = findings[0]['left_cue_id'], findings[0]['right_cue_id']
    require(type(left_id) is int and type(right_id) is int
        and 1 <= left_id < right_id <= len(cues) and right_id == left_id + 1, '相邻对编号无效')
    left, right = cues[left_id - 1], cues[right_id - 1]
    require(all(finding['left_cue_id'] == left_id and finding['right_cue_id'] == right_id
        and finding['left_interval'] == list(left[:2]) and finding['right_interval'] == list(right[:2])
        and finding['left_text'] == left[2] and finding['right_text'] == right[2] for finding in findings),
        '命中不属于同一当前字幕对')
    display_owners = [context for context in contexts
        if context['display_window_seconds'][0] <= left[0] and right[1] <= context['display_window_seconds'][1]]
    audio_owners = [context['unit_ids'] for context in contexts
        if context['audio_window_seconds'][0] <= left[0] and right[1] <= context['audio_window_seconds'][1]]
    intersecting = [context for context in contexts
        if context['display_window_seconds'][0] < right[1] and left[0] < context['display_window_seconds'][1]]
    require(len(display_owners) <= 1, '同一字幕对有歧义的多重显示归属')
    owner = display_owners[0] if display_owners else None
    if left[1] != right[0]:
        category = 'gapped-caption-boundary'
    elif owner:
        category = ('same-registered-synthesis-group' if owner['role_basis'] == 'validated-registered-group'
            and display_domain.known_role(owner['speaker_id']) else 'same-unregistered-synthesis-unit')
    else:
        speakers = {context['speaker_id'] for context in intersecting}
        category = ('cross-synthesis-same-registered-role' if len(intersecting) >= 2 and len(speakers) == 1
            and all(context['role_basis'] == 'validated-registered-group'
                and display_domain.known_role(context['speaker_id']) for context in intersecting)
            else 'cross-synthesis-role-change-or-unknown')
    selected_ids = sorted({number for context in intersecting for number in context['unit_ids']})
    neighbor_ids = (range(max(1, selected_ids[0] - 1), min(len(source_units), selected_ids[-1] + 1) + 1)
        if selected_ids else [])
    return {'left_cue_id': left_id, 'right_cue_id': right_id,
        'left': list(left), 'right': list(right), 'findings': findings, 'category': category,
        'gap_seconds': right[0] - left[1], 'overlapping_review_cue_ids':
            sorted({left_id, right_id} & set(overlapping_cues)),
        'display_owners': display_owners, 'strict_audio_owner_unit_ids': audio_owners,
        'display_quantization_changes_ownership_result': audio_owners != [context['unit_ids'] for context in display_owners],
        'intersecting_synthesis_units': intersecting,
        'source_context_with_neighbors': [source_units[number - 1] for number in neighbor_ids],
        'metadata_classification_only': True, 'agent_source_reviewed': False,
        'approved_for_repartition': False, 'audio_or_pronunciation_reviewed': False}


def inspect_project(project, audit_project):
    identifier = project['id']
    work = files.canonical(audit_project['work'])
    expected_name = '.zh_work_' + (identifier if identifier.startswith('x_') else 'yt_' + identifier)
    require(work.name == expected_name and work.parent == Path('/vol_1t/Videos'), '工程路径不属于明确项目')
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        for name in ['master', 'upload', 'manifest']:
            files.checked(project[name])
        manifest = state.load(project['manifest']['path'])
        require(project['manifest'] == audit_project['package_manifest']
            and project['master'] == audit_project['master'] and project['upload'] == audit_project['upload']
            and manifest['subtitles']['zh'] == audit_project['caption_source']
            and manifest['body_offset_seconds'] == audit_project['body_offset_seconds'], '成片/字幕审计版本不符')
        files.checked(audit_project['caption_source'])
        cues = state.cues(audit_project['caption_source']['path'])
        require(len(cues) == audit_project['cue_count'], '当前字幕总数不符')
        grouped = collections.defaultdict(list)
        for finding in audit_project['findings']:
            grouped[(finding['left_cue_id'], finding['right_cue_id'])].append(finding)
        require(len(grouped) == audit_project['affected_boundaries']
            and sum(map(len, grouped.values())) == audit_project['literal_match_count'], '边界/字面命中未完整枚举')
        if not grouped:
            return {'id': identifier, 'upload_version': project['upload_version'], 'pairs': [],
                'affected_boundaries': 0, 'literal_matches': 0, 'source_context_loaded': False,
                'formal_pair': {key: project[key] for key in ['master', 'upload', 'manifest']}}
        contexts, originals = contexts_for(work, manifest['body_offset_seconds'])
        cue_use = collections.Counter(number for pair in grouped for number in pair)
        overlapping = [number for number, count in cue_use.items() if count > 1]
        pairs = [annotate_pair(cues, findings, contexts, originals, overlapping)
            for pair, findings in sorted(grouped.items())]
        references = display_domain.role_inputs_for(work)
        references['render_state'] = state.identity(work / 'render_state.json')
        references['upload_manifest'] = state.identity(work / 'upload_export.json')
        references['current_captions'] = audit_project['caption_source']
        require(all(state.matches(reference) for reference in references.values()), '上下文输入变化')
        return {'id': identifier, 'work': str(work), 'upload_version': project['upload_version'],
            'pairs': pairs, 'source_context_loaded': True, 'source_inputs': references,
            'source_unit_count': len(originals), 'actual_synthesis_unit_count': len(contexts),
            'affected_boundaries': len(pairs), 'literal_matches': sum(len(pair['findings']) for pair in pairs),
            'formal_pair': {key: project[key] for key in ['master', 'upload', 'manifest']}}


def inspect(inventory_path, audit_path, output):
    inventory_reference, audit_reference = [state.identity(path) for path in [inventory_path, audit_path]]
    inventory, audit = [state.load(reference['path']) for reference in [inventory_reference, audit_reference]]
    require(inventory['passed'] and inventory['action'] == 'batch' and inventory['complete_scan']
        and inventory['installed_versions_checked'] and inventory['both_variants_generated'] == 34
        and inventory['summary']['scope'] == 40 and audit['audit_complete']
        and audit['pair_inventory'] == inventory_reference, '需要同一实际安装版本的完整双版本/字幕审计')
    output = files.canonical(output)
    require(not output.exists() and output.parent.is_relative_to(Path('/vol_1t/Videos/.workflow_logs')),
        '需要全新的隐藏只读审计收据')
    audit_map = {project['id']: project for project in audit['projects']}
    require(len(audit_map) == len(inventory['projects']) == 34
        and [project['id'] for project in audit['projects']] == [project['id'] for project in inventory['projects']],
        '完整34部范围或顺序不同')
    projects = [inspect_project(project, audit_map[project['id']]) for project in inventory['projects']]
    pairs = [pair for project in projects for pair in project['pairs']]
    summary = {'formal_pairs_checked': len(projects), 'affected_projects': sum(bool(project['pairs']) for project in projects),
        'affected_boundaries': len(pairs), 'literal_matches': sum(len(pair['findings']) for pair in pairs),
        'categories': dict(sorted(collections.Counter(pair['category'] for pair in pairs).items())),
        'boundaries_in_overlapping_chains': sum(bool(pair['overlapping_review_cue_ids']) for pair in pairs),
        'display_quantization_sensitive_boundaries': sum(pair['display_quantization_changes_ownership_result'] for pair in pairs),
        'automatically_approved_boundaries': 0}
    require(summary['affected_boundaries'] == audit['summary']['affected_boundaries']
        and summary['literal_matches'] == audit['summary']['literal_matches'], '没有覆盖所有当前审计命中')
    for project in projects:
        for reference in list(project['formal_pair'].values()) + list(project.get('source_inputs', {}).values()):
            files.checked(reference)
    files.checked(inventory_reference)
    files.checked(audit_reference)
    files.durable_json(output, {'checked_at': state.now(), 'passed': True, 'kind': KIND,
        'implementation': state.identity(__file__), 'modules': module_identities(),
        'pair_inventory': inventory_reference, 'boundary_audit': audit_reference,
        'summary': summary, 'projects': projects, 'production_mutated': False,
        'fresh_full_decode_performed': False, 'tts_performed': False,
        'human_listened': False, 'goal_status': 'active',
        'scope': 'All registered current caption boundary hits joined to validated source/synthesis metadata; no semantic, speaker, timing-edit, or playback approval is inferred.'})
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair-inventory', type=Path, required=True)
    parser.add_argument('--boundary-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    print(inspect(options.pair_inventory, options.boundary_audit, options.output), flush=True)
