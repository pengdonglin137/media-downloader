"""实际v1来源到v10双版本包：保留严格预览v1和原生母版v4/revision2，执行四文件事务。"""

import argparse
import copy
import json
import uuid
from pathlib import Path

import caption_boundary_master_v2 as master_producer
import caption_boundary_upload_v7 as candidate_exporter
import delivery_quality
import dubbing_state as state
import paired_delivery_transaction as files
import paired_delivery_v4 as text_helpers
import paired_delivery_v5 as helpers
import paired_source_v1 as source_validator
import paired_migration_preflight as source_preflight
import upload_delivery
import upload_delivery_v3
import upload_export_v3
import video_package


VERSION = 10
KIND = 'caption-boundary-paired-package-v10'
BASE_FIELDS = helpers.BASE_FIELDS
require = helpers.require
current_reference = helpers.current_reference
read_reference = helpers.read_reference
references = helpers.references
prepared_revision = helpers.prepared_revision


def revision_directory(work, directory):
    directory = files.canonical(directory)
    require(directory.parent == work / 'paired_packages' and len(directory.name) == 32
        and all(character in '0123456789abcdef' for character in directory.name), 'v10包目录不属于当前工程')
    return directory


def module_identities():
    return {'producer': state.identity(__file__), **{name: state.identity(module.__file__) for name, module in {
        'file_transaction': files, 'state': state, 'master_producer': master_producer,
        'legacy_package': video_package, 'quality': delivery_quality, 'text_helpers': text_helpers,
        'package_helpers': helpers, 'source_validator': source_validator, 'upload_candidate': candidate_exporter,
        'safe_band': upload_export_v3, 'legacy_dispatcher': upload_delivery_v3}.items()},
        **{'source_' + name: reference for name, reference in source_validator.module_identities().items()}}


def validate_candidate_headers(work, original, manifest_reference, master, upload, modules):
    require(master['work'] == upload['work'] == str(work)
        and type(master['version']) is int and master['version'] == 4
        and type(upload['version']) is int and upload['version'] == VERSION
        and master['kind'] == 'caption-boundary-master-candidate-v4'
        and type(master['producer_revision']) is int and master['producer_revision'] == 2
        and upload['kind'] == candidate_exporter.KIND
        and master['implementation'] == modules['master_producer']
        and master['reused_helpers'] == state.identity(text_helpers.master_helpers.__file__)
        and master['reused_package_verifier'] == modules['legacy_package']
        and upload['dependencies']['candidate_exporter'] == modules['upload_candidate']
        and type(upload['master_candidate_format_version']) is int and upload['master_candidate_format_version'] == 4
        and type(upload['source_upload_version']) is int and upload['source_upload_version'] == 1
        and master['candidate_only'] is True
        and master['production_applied'] is upload['production_applied'] is False
        and master['original_master'] == original['master'] and master['input_package_manifest'] == manifest_reference
        and master['all_logical_chapters_identical'] is True
        and master['chapter_data_packets_claimed_identical'] is False,
        'v10包必须保留真实v1来源和实际v4母版格式，不能伪造版本或章节证明')



def validate_preview_source(work, transition, prior, resolve=current_reference, current_render=None, database=None):
    require(type(transition['from']) is int and transition['from'] == prior.get('upload_version', 1) == 1
        and type(transition['to']) is int and transition['to'] == VERSION,
        '当前生产基线只接受明确实际v1到v10迁移')
    checkpoint = read_reference(transition['preview_checkpoint'], resolve)
    master = read_reference(transition['master_candidate'], resolve)
    preview = read_reference(master['text_preview'], resolve)
    require(checkpoint['passed'] is True and checkpoint['project_id'] == prior['id'] == source_validator.project_id(work)
        and checkpoint['production_applied'] is False and checkpoint['new_upload_candidate_generated'] is False
        and checkpoint['state'] == 'reviewed_display_preview_and_native_master_candidate_verified_production_adaptation_pending'
        and checkpoint['source_upload_version'] == 1 and checkpoint['preview_version'] == 1
        and checkpoint['native_master_format_version'] == 4 and checkpoint['native_master_producer_revision'] == 2
        and checkpoint['master_candidate'] == transition['master_candidate']
        and checkpoint['text_preview'] == master['text_preview'], '需要真实v112严格既有组候选，不能伪造历史断点')
    original = {name: prior[name] for name in ['master', 'upload']}
    original.update(render_state=preview['inputs']['package_render_state'], upload_manifest=preview['inputs']['upload_manifest'])
    files.destinations(work, original)
    for reference in original.values():
        resolve(reference)
    quality, bindings = helpers.normalized_quality(work, original['render_state'], current_render or original['render_state'], database)
    source = source_validator.validate(work, original, prior['manifest'], bindings, resolve)
    base = source['base']
    require(type(master['version']) is int and master['version'] == 4
        and type(master['producer_revision']) is int and master['producer_revision'] == 2
        and master['kind'] == 'caption-boundary-master-candidate-v4'
        and master['implementation'] == state.identity(master_producer.__file__)
        and master['reused_helpers'] == state.identity(text_helpers.master_helpers.__file__)
        and master['reused_package_verifier'] == state.identity(video_package.__file__)
        and master['original_master'] == original['master'] and master['input_package_manifest'] == prior['manifest']
        and master['candidate_only'] is True and master['production_applied'] is False
        and master['all_logical_chapters_identical'] is True and master['chapter_data_packets_claimed_identical'] is False
        and type(preview['version']) is int and preview['version'] == 1,
        '保留实际v1来源、严格既有组预览v1及母版格式v4/revision2')
    references(master, resolve)
    references(preview, resolve)
    source_validator.text_projection(work, preview, master, base, original, prior['manifest'])
    raster_reference = checkpoint['evidence']['text_and_all_cue_raster']
    raster = read_reference(raster_reference, resolve)
    references(raster, resolve)
    candidate_exporter.validate_layout(preview, master, base, raster)
    terminal = read_reference(checkpoint['independent_candidate_terminal'], resolve)
    references(terminal, resolve)
    require(terminal['passed'] is True and terminal['production_applied'] is False
        and terminal['contains_mutable_metadata_raw_inventory'] is False
        and Path(resolve(terminal['actual_exit'])['path']).read_text().strip() == '0',
        '原生候选缺少真实独立终态')
    independent = read_reference(terminal['independent_report'], resolve)
    identities = {reference['path']: reference for reference in independent['raw_hashed_files']}
    require(independent['passed'] is True and independent['production_applied'] is False
        and independent['master_candidate'] == transition['master_candidate']
        and all(identities.get(reference['path']) == reference for reference in
            [transition['master_candidate'], master['output'], master['text_preview'], raster_reference]),
        '独立原始哈希必须覆盖实际母版、最终文字和全部栅格')
    review_reference = checkpoint['evidence']['ass_overlay_review']
    require(identities.get(review_reference['path']) == review_reference, '独立验收未覆盖实际ASS图审')
    review = read_reference(review_reference, resolve)
    references(review, resolve)
    changed = set(preview['proof']['changed_cue_ids'])
    require(review['passed'] is True and review['preview'] == master['text_preview']
        and review['all_four_actually_viewed'] is True and review['actual_encoded_upload_frames'] is False
        and len(review['frames']) == 2 * len(changed)
        and {(frame['cue_id'], frame['kind']) for frame in review['frames']}
            == {(cue, kind) for cue in changed for kind in ['caption-band', 'full-frame']}
        and all(frame['agent_viewed'] is True and frame['actual_encoded_upload_frame'] is False
            and frame['overlay_ass'] == preview['preview_ass'] and frame['image']['bytes'] <= 192 * 1024
            and bool(frame['observation'].strip()) for frame in review['frames']),
        '每条修订字幕都必须具有实际已看过的受限字幕带及全幅ASS图')
    video_package.verify_media(Path(resolve(master['output'])['path']), {**base, 'subtitles': master['subtitles']})
    return {'original': original, 'base': base, 'rendering': source['rendering'], 'quality': quality,
        'master': master, 'preview': preview, 'source_upload_version': 1, 'master_format_version': 4, 'preview_version': 1}


def verify_intent(work, intent, resolve=current_reference, current_render=None, database=None):
    require(type(intent['version']) is int and intent['version'] == VERSION and intent['kind'] == KIND
        and intent['work'] == str(work) and intent['modules'] == module_identities(), 'v10意向工程、版本或实现变化')
    original = intent['original']
    files.destinations(work, original)
    for reference in [*original.values(), *intent['evidence'].values(), intent['base_package_manifest']]:
        resolve(reference)
    checkpoint = read_reference(intent['evidence']['candidate_checkpoint'])
    require(checkpoint['state'] == 'caption_boundary_candidate_pair_verified_not_installed'
        and checkpoint['formal_before'] == original and checkpoint['production_applied'] is False,
        '需要尚未安装且原四文件身份明确的完整双版本候选')
    for reference in [checkpoint['master_candidate'], checkpoint['previous_preview_checkpoint'],
            *checkpoint['evidence'].values()]:
        resolve(reference)
    terminal = read_reference(intent['evidence']['candidate_terminal'])
    references(terminal, resolve)
    require(terminal['checkpoint'] == intent['evidence']['candidate_checkpoint'], '独立终态没有绑定当前候选断点')
    contracts = read_reference(intent['evidence']['file_contracts'])
    require(contracts['passed'] is True and contracts['tests_run'] == 40
        and contracts['implementation'] == intent['modules']['file_transaction'], '文件事务需要原40项契约及精确实现')
    references(contracts)
    master = read_reference(checkpoint['master_candidate'])
    upload = read_reference(checkpoint['evidence']['upload_candidate'])
    validate_candidate_headers(work, original, intent['base_package_manifest'], master, upload, intent['modules'])
    references(master, resolve)
    references(upload, resolve)
    preview = read_reference(master['text_preview'])
    references(preview, resolve)
    previous = read_reference(checkpoint['previous_preview_checkpoint'])
    raster_reference = previous['evidence']['text_and_all_cue_raster']
    raster = read_reference(raster_reference)
    references(raster, resolve)
    dependencies = upload['dependencies']
    require(master['text_preview'] == dependencies['text_preview'] == previous['text_preview']
        and checkpoint['master_candidate'] == dependencies['master_candidate_result'] == previous['master_candidate']
        and master['output'] == dependencies['master_candidate']
        and dependencies['inner_package_manifest'] == intent['base_package_manifest']
        and dependencies['repaired_chinese_captions'] == master['subtitles']['zh']
        and dependencies['repaired_bilingual_captions'] == master['subtitles']['bilingual']
        and dependencies['checkpoint'] == checkpoint['previous_preview_checkpoint']
        and dependencies['raster_receipt'] == raster_reference
        and all(dependencies[name] == original[field] for name, field in [
            ('master', 'master'), ('upload', 'upload'), ('package_render_state', 'render_state'),
            ('upload_manifest', 'upload_manifest')]), '候选必须绑定同一来源、最终字幕和原四文件')
    quality, bindings = helpers.normalized_quality(work, original['render_state'],
        current_render or original['render_state'], database)
    require(bindings == upload['quality']['bindings'] == intent['quality_before']['bindings']
        and upload['quality']['allowed'] is intent['quality_before']['allowed'] is True,
        '除了明确的render_state迁移外，质量绑定不能变化')
    source = source_validator.validate(work, original, intent['base_package_manifest'], bindings, resolve)
    base, rendering = source['base'], source['rendering']
    source_validator.text_projection(work, preview, master, base, original, intent['base_package_manifest'])
    baseline, prior = candidate_exporter.validate_baseline(work, dependencies['baseline_preflight'],
        original, intent['base_package_manifest'])
    selected = baseline['selected'][prior['id']]
    require(selected['preview_checkpoint'] == checkpoint['previous_preview_checkpoint']
        and selected['master_candidate'] == checkpoint['master_candidate']
        and dependencies['production_source_validator'] == intent['modules']['producer'],
        '候选与当前生产来源验证器或声明迁移不符')
    reviewed = validate_preview_source(work, selected, prior, resolve, current_render, database)
    require(reviewed['original'] == original and reviewed['base'] == base
        and reviewed['master'] == master and reviewed['preview'] == preview,
        '成对意向必须重新验证相同严格既有组来源')
    script, layout, content, reused_raster = candidate_exporter.validate_layout(preview, master, base, raster)
    require(script == Path(dependencies['candidate_ass']['path']).read_text()
        and state.signature(layout) == state.signature(upload['layout_report'])
        and content == upload['content_check'] and reused_raster == upload['reused_all_cue_raster_check']
        and upload['profile'] == candidate_exporter.profile(layout['geometry']), 'v10实际上传ASS、字形或安全带无法重建')
    audio = read_reference(intent['evidence']['audio_packets'])
    fidelity, runtime = helpers.fidelity_audio_runtime(checkpoint, master, upload, audio, terminal, base)
    return {'checkpoint': checkpoint, 'master': master, 'upload': upload, 'preview': preview,
        'base': base, 'rendering': rendering, 'quality': quality, 'fidelity': fidelity, 'runtime': runtime}


def package_record(intent_reference, intent, context):
    base, master, upload = context['base'], context['master'], context['upload']
    return {'version': VERSION, 'kind': KIND, 'created_at': intent['created_at'], 'work': intent['work'],
        **{name: copy.deepcopy(base[name]) for name in BASE_FIELDS},
        'producer': intent['modules']['producer'], 'promotion_intent': intent_reference,
        'base_package_manifest': intent['base_package_manifest'],
        'caption_producer': {'master_candidate': context['checkpoint']['master_candidate'],
            'implementation': master['implementation'], 'text_preview': master['text_preview'], 'source_format_version': 4},
        'source_upload_version': 1, 'subtitles': copy.deepcopy(master['subtitles']),
        'output': files.rebase(master['output'], intent['original']['master']['path']),
        'verification': copy.deepcopy(master['media_verification']),
        'decode_basis': {'master_av': 'retained-base-full-decode-and-exact-ordered-av-packet-preservation',
            'base_full_decode': copy.deepcopy(base['full_decode']),
            'master_preserved_packets': copy.deepcopy(master['preserved_encoded_stream_packets']),
            'upload_full_decode': copy.deepcopy(upload['verification']['full_decode'])},
        'new_master_video_or_audio_synthesis': False, 'word_alignment_verified': False,
        'human_sync_approved': False, 'full_human_listening': False, 'release_or_upload_performed': False}


def render_record(intent_reference, intent, context, package_reference):
    rendering = copy.deepcopy(context['rendering'])
    rendering.update(origin='self-contained-caption-boundary-v10', package_version=VERSION,
        observed_at=intent['created_at'], producer=intent['modules']['producer'], promotion_intent=intent_reference,
        package_manifest=package_reference, output=files.rebase(context['master']['output'], intent['original']['master']['path']))
    return rendering


def upload_record(intent_reference, intent, context, package_reference, render_reference):
    candidate = context['upload']
    bindings = copy.deepcopy(intent['quality_before']['bindings'])
    bindings['inputs']['render_state.json'] = render_reference['sha256']
    return {'version': VERSION, 'kind': KIND, 'created_at': intent['created_at'], 'work': intent['work'],
        'producer': intent['modules']['producer'], 'promotion_intent': intent_reference,
        'dependencies': {'package_manifest': package_reference, 'render_state': render_reference,
            'candidate_result': context['checkpoint']['evidence']['upload_candidate'],
            'expected_current_quality_bindings': bindings},
        'output': files.rebase(candidate['output'], intent['original']['upload']['path']),
        'profile': copy.deepcopy(candidate['profile']), 'verification': copy.deepcopy(candidate['verification']),
        'quality_before': copy.deepcopy(intent['quality_before']),
        'human_listened': False, 'release_or_upload_performed': False}


def verify_revision(work, directory, prepared=None, installed=False, database=None):
    directory = revision_directory(work, directory)
    intent_reference = state.identity(directory / 'intent.json')
    intent = read_reference(intent_reference)
    resolve = current_reference
    render_reference = intent['original']['render_state']
    if prepared is not None:
        require(prepared['work'] == str(work) and prepared['evidence']['intent'] == intent_reference
            and prepared['original'] == intent['original'], '四文件事务与v10生产意向不符')
        staged_directory = Path(prepared['staged']['master']['path']).parent.parent
        resolve = files.ArchiveRouter(work, staged_directory, prepared['original'], prepared['archived']).resolve
        files.verify_set(prepared, 'desired' if installed else 'original')
        if installed:
            render_reference = prepared['desired']['render_state']
    context = verify_intent(work, intent, resolve, render_reference, database)
    package_reference = state.identity(directory / 'manifest.json')
    package = read_reference(package_reference)
    require(package == package_record(intent_reference, intent, context), 'v10包元数据不是确定性生产结果')
    new_render_reference = state.identity(directory / 'render_state.json')
    new_render = read_reference(new_render_reference)
    require(new_render == render_record(intent_reference, intent, context, package_reference), 'v10母版清单不符')
    formal_render = files.rebase(new_render_reference, work / 'render_state.json')
    upload_reference = state.identity(directory / 'upload_export.json')
    new_upload = read_reference(upload_reference)
    require(new_upload == upload_record(intent_reference, intent, context, package_reference, formal_render), 'v10上传清单不符')
    replacements = {'master': context['master']['output'], 'upload': context['upload']['output'],
        'render_state': new_render_reference, 'upload_manifest': upload_reference}
    if prepared is not None:
        require(prepared['sources'] == replacements, '事务必须暂存本次v10的全部四个替换文件')
    if installed:
        require(state.load(work / 'render_state.json') == new_render
            and state.load(work / 'upload_export.json') == new_upload
            and new_upload['dependencies']['expected_current_quality_bindings'] == context['quality']['bindings'],
            '实际正式清单或当前质量输入不是本v10版本')
    master_path = package['output']['path'] if installed else context['master']['output']['path']
    upload_path = new_upload['output']['path'] if installed else context['upload']['output']['path']
    video_package.verify_media(Path(master_path), package)
    video_package.verify_media(Path(upload_path), package, upload=True)
    video = next(stream for stream in video_package.probe(Path(upload_path))['streams'] if stream['codec_type'] == 'video')
    geometry = new_upload['profile']['geometry']
    require([video['width'], video['height']] == [geometry['output_width'], geometry['output_height']], '实际上传不是已验证安全带')
    return {'intent': intent, 'intent_reference': intent_reference, 'package': package,
        'package_reference': package_reference, 'rendering': new_render, 'upload': new_upload,
        'replacements': replacements, 'context': context}


def draft_intent(work, *, checkpoint, audio_packets, terminal, file_contracts, database=None):
    work = files.canonical(work)
    upload_delivery.pending_transactions(work)
    files.pending_transactions(work)
    base = video_package.validate(work)
    old_upload = upload_delivery_v3.validate_current(work, database=database)
    require(type(old_upload['version']) is int and old_upload['version'] == 1, 'v10适配只接受明确的实际v1上传来源')
    return {'version': VERSION, 'kind': KIND, 'created_at': state.now(), 'work': str(work),
        'modules': module_identities(), 'original': {'master': base['output'], 'upload': old_upload['output'],
            'render_state': state.identity(work / 'render_state.json'),
            'upload_manifest': state.identity(work / 'upload_export.json')},
        'base_package_manifest': state.load(work / 'render_state.json')['package_manifest'],
        'quality_before': delivery_quality.require(work, database),
        'evidence': {name: state.identity(path) for name, path in {'candidate_checkpoint': checkpoint,
            'audio_packets': audio_packets, 'candidate_terminal': terminal, 'file_contracts': file_contracts}.items()}}


def create(work, directory, *, checkpoint, audio_packets, terminal, file_contracts, database=None):
    work = files.canonical(work)
    directory = revision_directory(work, directory)
    require(not directory.exists(), '不能覆盖既有v10生产包')
    with files.project_lock(work):
        intent = draft_intent(work, checkpoint=checkpoint, audio_packets=audio_packets, terminal=terminal,
            file_contracts=file_contracts, database=database)
        context = verify_intent(work, intent, database=database)
        directory.parent.mkdir(exist_ok=True)
        directory.mkdir()
        files.sync_directory(work)
        files.sync_directory(directory.parent)
        files.durable_json(directory / 'intent.json', intent)
        intent_reference = state.identity(directory / 'intent.json')
        files.durable_json(directory / 'manifest.json', package_record(intent_reference, intent, context))
        package_reference = state.identity(directory / 'manifest.json')
        files.durable_json(directory / 'render_state.json', render_record(intent_reference, intent, context, package_reference))
        render_reference = files.rebase(state.identity(directory / 'render_state.json'), work / 'render_state.json')
        files.durable_json(directory / 'upload_export.json', upload_record(intent_reference, intent, context,
            package_reference, render_reference))
        verify_revision(work, directory, database=database)
    return {'state': 'revision_verified', 'directory': str(directory), 'intent': intent_reference, 'production_applied': False}


def verify_prepared(prepared, database=None):
    work, directory = prepared_revision(prepared)
    upload_delivery.pending_transactions(work)
    return verify_revision(work, directory, prepared, database=database)


def verify_installed(prepared, database=None):
    work, directory = prepared_revision(prepared)
    upload_delivery.pending_transactions(work)
    return verify_revision(work, directory, prepared, installed=True, database=database)


def verify_original(prepared, database=None):
    work = Path(prepared['work'])
    files.verify_set(prepared, 'original')
    video_package.validate(work)
    require(upload_delivery_v3.validate_current(work, database=database)['version'] == 1,
        '恢复后的实际上传必须为原v1，不是其他来源伪装')
    files.verify_set(prepared, 'original')


def callbacks(database=None):
    return {'verify_prepared': lambda prepared: verify_prepared(prepared, database),
        'verify_current': lambda prepared: verify_installed(prepared, database),
        'verify_original': lambda prepared: verify_original(prepared, database)}


def prepare(work, directory, database=None):
    work = files.canonical(work)
    upload_delivery.pending_transactions(work)
    result = verify_revision(work, directory, database=database)
    prepared = files.prepare(work, result['intent']['original'], result['replacements'],
        evidence={'intent': result['intent_reference']})
    plan = files.promote(work, Path(prepared['transaction']), **callbacks(database))
    return {**prepared, 'production_preflight': plan}


def promote(work, transaction, apply=False, database=None):
    return files.promote(files.canonical(work), files.canonical(transaction), apply=apply, **callbacks(database))


def rollback(work, transaction, apply=False, database=None):
    return files.rollback(files.canonical(work), files.canonical(transaction), apply=apply,
        verify_original=callbacks(database)['verify_original'])


def recover(work, transaction, database=None):
    validators = callbacks(database)
    return files.recover(files.canonical(work), files.canonical(transaction),
        verify_current=validators['verify_current'], verify_original=validators['verify_original'])


def current_transaction(work):
    rendering = state.load(work / 'render_state.json')
    require(rendering.get('package_version') == VERSION, '正式母版不是显式v10包')
    package_reference = rendering['package_manifest']
    files.checked(package_reference)
    directory = revision_directory(work, Path(package_reference['path']).parent)
    require(Path(package_reference['path']).name == 'manifest.json', 'v10包清单路径不正确')
    intent_reference = state.identity(directory / 'intent.json')
    found = []
    for path in (work / 'paired_revisions').glob('*/transaction.json'):
        journal = state.load(files.transaction_path(work, path))
        if journal.get('state') != 'committed':
            continue
        prepared, journal = files.load_transaction(work, path)
        if prepared['evidence'].get('intent') == intent_reference and all(
                state.matches(reference) for reference in prepared['desired'].values()):
            found.append((path, prepared))
    require(len(found) == 1, '需要唯一已提交且与当前四文件一致的v10事务')
    files.pending_transactions(work, found[0][0])
    return found[0]


def validate_current(work, database=None):
    work = files.canonical(work)
    path, prepared = current_transaction(work)
    verified = verify_installed(prepared, database)
    package, context = verified['package'], verified['context']
    return {'id': work.name.removeprefix('.zh_work_yt_').removeprefix('.zh_work_'), 'upload_version': VERSION,
        'manifest': verified['package_reference'], 'master': package['output'], 'upload': verified['upload']['output'],
        'intro_seconds': package['body_offset_seconds'], 'duration_seconds': package['duration_seconds'],
        'chapters': len(package['chapters']), 'transaction': state.identity(path), 'intent': verified['intent_reference'],
        'quality': context['quality'], 'current_full_decode_evidence_valid': True,
        'master_decode_basis': package['decode_basis']['master_av'],
        'candidate_vlc_evidence_reused_by_exact_content': context['checkpoint']['evidence']['runtime'],
        'canonical_path_vlc_tested_by_this_validator': False,
        'source_region_fidelity': context['checkpoint']['evidence']['fidelity'],
        'current_chinese_aac_identical': True, 'current_master_chinese_aac': context['upload']['master_chinese_aac'],
        'current_upload_chinese_aac': context['upload']['candidate_chinese_aac'],
        'word_alignment_verified': False, 'human_listened': False, 'human_gui_checked': False, 'potplayer_tested': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'create', 'prepare', 'plan', 'promote', 'verify', 'rollback', 'recover'])
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--transaction', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--audio-packets', type=Path)
    parser.add_argument('--terminal', type=Path)
    parser.add_argument('--file-contracts', type=Path)
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    require(not options.apply or options.action in {'promote', 'rollback'}, '--apply只适用于明确的晋升或回滚')
    if options.action in {'preflight', 'create'}:
        require(all([options.checkpoint, options.audio_packets, options.terminal, options.file_contracts]), '缺少候选或契约证据')
        inputs = {'checkpoint': options.checkpoint, 'audio_packets': options.audio_packets,
            'terminal': options.terminal, 'file_contracts': options.file_contracts}
        if options.action == 'preflight':
            with files.project_lock(files.canonical(options.work)):
                intent = draft_intent(options.work, **inputs)
                context = verify_intent(options.work, intent)
            result = {'state': 'production_candidates_verified_read_only', 'work': str(options.work),
                'master': context['master']['output'], 'upload': context['upload']['output'], 'production_applied': False}
        else:
            result = create(options.work, options.directory or options.work / 'paired_packages' / uuid.uuid4().hex, **inputs)
    elif options.action == 'prepare':
        require(options.directory is not None, '需要已验证的包目录')
        result = prepare(options.work, options.directory)
    elif options.action == 'verify':
        result = validate_current(options.work)
    else:
        require(options.transaction is not None, '需要明确事务日志')
        if options.action in {'plan', 'promote'}:
            result = promote(options.work, options.transaction, apply=options.apply)
        elif options.action == 'rollback':
            result = rollback(options.work, options.transaction, apply=options.apply)
        else:
            result = recover(options.work, options.transaction)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
