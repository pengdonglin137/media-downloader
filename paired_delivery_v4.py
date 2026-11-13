"""跨条字幕双版本生产适配：独立来源、严格归档解析及可恢复安装。"""

import argparse
import copy
import json
import uuid
from pathlib import Path

import caption_boundary_master as master_helpers
import caption_boundary_master_v2 as master_producer
import caption_boundary_repair as repair
import caption_boundary_upload as candidate_exporter
import caption_layout
import delivery_quality
import dubbing_state as state
import paired_delivery_transaction as files
import upload_delivery
import upload_delivery_v3
import upload_export
import upload_export_v3
import video_package


VERSION = 4
KIND = 'caption-boundary-paired-package-v4'
BASE_FIELDS = ['plan', 'body', 'dependencies', 'font', 'intro_audio', 'body_offset_seconds', 'intro_frames',
               'body_duration_seconds', 'duration_seconds', 'chapters', 'audio_tracks']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def current_reference(observation):
    files.checked(observation)
    return observation


def read_reference(observation, resolve=current_reference):
    return state.load(resolve(observation)['path'])


def references(value, resolve=current_reference):
    if isinstance(value, dict):
        if files.IDENTITY_KEYS.issubset(value):
            resolve(value)
        else:
            for child in value.values():
                references(child, resolve)
    elif isinstance(value, list):
        for child in value:
            references(child, resolve)


def module_identities():
    return {'producer': state.identity(__file__), **{name: state.identity(module.__file__) for name, module in {
        'file_transaction': files, 'state': state, 'master_producer': master_producer,
        'legacy_package': video_package, 'quality': delivery_quality, 'text_repair': repair,
        'master_helpers': master_helpers, 'upload_candidate': candidate_exporter,
        'layout': caption_layout, 'legacy_upload': upload_export, 'safe_band': upload_export_v3,
        'legacy_dispatcher': upload_delivery_v3}.items()}}


def normalized_quality(work, original_render, current_render, database=None):
    files.checked(current_render)
    require(current_render['path'] == str(work / 'render_state.json'), '当前质量必须绑定正式render_state')
    quality = delivery_quality.require(work, database)
    bindings = copy.deepcopy(quality['bindings'])
    require(bindings['inputs']['render_state.json'] == current_render['sha256'], '质量快照不是当前正式清单')
    bindings['inputs']['render_state.json'] = original_render['sha256']
    return quality, bindings


def base_package(work, original, manifest_reference, resolve):
    rendering = read_reference(original['render_state'], resolve)
    require(rendering.get('origin') == 'self-contained' and rendering['output'] == original['master']
            and rendering['package_manifest'] == manifest_reference, '仅支持原始自包含母版到v4的显式迁移')
    manifest = read_reference(manifest_reference, resolve)
    references(manifest, resolve)
    narration = state.validate_narration(work)
    dependencies = state.render_dependencies(work, Path(narration['waveform']['path']), state.source_video(work))
    plan = video_package.validate_plan(work, read_reference(manifest['plan'], resolve))
    require(manifest['version'] == 1 and manifest['implementation_sha256'] == state.digest(video_package.__file__)
            and manifest['output'] == original['master'] and manifest['dependencies'] == rendering['dependencies'] == dependencies
            and manifest['chapters'] == video_package.chapter_list(work, plan, manifest['body_offset_seconds'],
                                                                 manifest['duration_seconds'])
            and manifest['full_decode']['exit_code'] == 0, '原包、来源、音频或章节绑定失效')
    return manifest, rendering


def base_upload(work, original, manifest, manifest_reference, bindings, resolve):
    record = read_reference(original['upload_manifest'], resolve)
    require(record['version'] == 1 and record['output'] == original['upload'], '当前迁移只接受v1上传版到v4')
    references(record, resolve)
    dependencies = record['dependencies']
    require(dependencies['quality_bindings'] == bindings
            and dependencies['implementation_sha256'] == state.digest(upload_export.__file__)
            and dependencies['self_contained'] == {'manifest': manifest_reference, 'master': original['master'],
                'audio': 'copy-current-chinese-aac', 'chapters': manifest['chapters']}
            and dependencies['profile'] == {'version': 1, 'crf': 20, 'audio_bitrate': '128k', 'position': 'bottom'}
            and record['verification']['full_decode']['exit_code'] == 0, '原上传版质量、生产来源或解码绑定失效')
    return record


def text_projection(work, preview, master, base, original, manifest_reference):
    inputs = preview['inputs']
    expected = {'master': original['master'], 'upload': original['upload'], 'captions': base['subtitles']['zh'],
        'package_render_state': original['render_state'], 'inner_package_manifest': manifest_reference,
        'terms': inputs['terms'], 'speech_groups': state.identity(work / 'speech_groups.json'),
        'narration': state.identity(work / 'narration.json'), 'source_captions': state.identity(work / 'en.srt'),
        'upload_manifest': original['upload_manifest']}
    plan = read_reference(preview['plan'])
    require(set(plan) == {'version', 'kind', 'project_id', 'inputs', 'edits'}
            and plan['version'] == repair.VERSION and plan['kind'] == repair.KIND
            and inputs == expected and plan['inputs'] == inputs
            and plan['project_id'] == work.name.removeprefix('.zh_work_yt_')
            and preview['implementation'] == state.identity(repair.__file__), '文字预览来源或生产实现变化')
    forms = caption_layout.load_forms(inputs['terms']['path'])
    original_cues = state.cues(base['subtitles']['zh']['path'])
    repaired, proof = repair.repartition(original_cues, plan['edits'],
        repair.contexts_for(work, base['body_offset_seconds']), forms)
    require(state.signature(proof) == state.signature(preview['proof'])
            and repaired == state.cues(preview['preview_captions']['path'])
            == state.cues(master['subtitles']['zh']['path'])
            and Path(master['subtitles']['zh']['path']).read_text() == state.srt_text(repaired)
            and master['subtitles']['en'] == base['subtitles']['en'], '文字守恒、原时间或中英字幕投影失效')
    bilingual = master_helpers.project_bilingual(original_cues, repaired, state.cues(base['subtitles']['bilingual']['path']))
    require(bilingual == state.cues(master['subtitles']['bilingual']['path'])
            and master['english_in_bilingual_preserved'] is True, '双语字幕英文或中文修订不一致')
    return forms


def raster_and_layout(work, preview, master, upload, receipt, preview_reference, base, forms):
    raster = candidate_exporter.validate_raster(receipt, preview, preview_reference)
    geometry = upload['profile']['geometry']
    require(upload['profile'] == {'layout': 'caption-boundary-safe-band-v4', 'crf': 20,
        'position': 'added-bottom-band', 'audio': 'copy-candidate-master-chinese-aac',
        'chapters': 'copy-candidate-master', 'geometry': geometry}
        and geometry == upload_export_v3.geometry(geometry['source_width'], geometry['source_height']),
        'v4上传规格或原片几何变化')
    script, layout = upload_export_v3.subtitles(work, geometry['source_width'], geometry['source_height'],
        base['duration_seconds'], forms, Path(master['subtitles']['zh']['path']))
    content = upload_export_v3.verify_script(script, Path(master['subtitles']['zh']['path']), base['duration_seconds'])
    require(script == Path(preview['preview_ass']['path']).read_text()
            == Path(upload['dependencies']['candidate_ass']['path']).read_text()
            and layout == preview['layout'] == upload['layout_report']
            and content == preview['script_check'] == upload['content_check']
            and upload['reused_all_cue_raster_check'] == raster, '字幕ASS、内容、字形或安全带无法重建')


def fidelity_audio_runtime(checkpoint, master, upload, audio, terminal):
    evidence = checkpoint['evidence']
    fidelity = read_reference(evidence['fidelity'])
    references(fidelity)
    geometry = upload['profile']['geometry']
    require(fidelity['passed'] and fidelity['candidate_result'] == evidence['upload_candidate']
            and fidelity['master'] == master['output'] and fidelity['candidate'] == upload['output']
            and fidelity['compared_candidate_region'] == [0, 0, geometry['source_width'], geometry['source_height']]
            and fidelity['predeclared_thresholds'] == {'mean_frame_ssim': .99, 'minimum_frame_ssim': .95}
            and fidelity['mean_frame_ssim'] >= .99 and fidelity['minimum_frame_ssim'] >= .95
            and fidelity['video_frames_compared'] == master['preserved_encoded_stream_packets']['0']['packets']
            and fidelity['maximum_pts_error_seconds'] <= .0001
            and fidelity['maximum_frame_duration_error_seconds'] <= .0001
            and fidelity['source_pixels_bitwise_identical_claimed'] is False, '完整原画区域画质证据失效')
    references(audio)
    require(audio['passed'] and audio['candidate_result'] == evidence['upload_candidate']
            and audio['master'] == master['output'] and audio['upload'] == upload['output']
            and audio['time_base'] == '1/24000'
            and audio['master_packets'] == audio['upload_packets'] == {'1': master['preserved_encoded_stream_packets']['1']}
            and upload['chinese_aac_identical'] and upload['master_chinese_aac'] == upload['candidate_chinese_aac'],
            '中文AAC载荷或时间线保留证据失效')
    require(terminal['passed'] and terminal['candidate_pair_verified'] and not terminal['production_applied'],
            '缺少候选双版本独立终态')
    runtime = read_reference(evidence['runtime'])
    references(runtime)
    require(runtime['passed'] and runtime['candidate_result'] == evidence['upload_candidate']
            and runtime['whole_film_continuous_playback'] is False, '有限VLC候选双版本验证失效')
    for key, candidate in [('master', master), ('upload', upload)]:
        actual = runtime[key]
        require(actual['passed'] and actual['media'] == candidate['output'] and actual['chapter_seeks']
                and len(actual['chapters']) == master['media_verification']['chapters']
                and actual['rendered_snapshots'], '候选VLC章节/轨道/渲染证据不完整：' + key)
    visual = read_reference(evidence['actual_visual_review'])
    references(visual)
    require(visual['candidate_result'] == evidence['upload_candidate'] and visual['upload'] == upload['output']
            and visual['agent_visual_review_performed'] and visual['caption_text_readable_and_unclipped']
            and visual['source_content_and_controls_not_overlaid_by_captions']
            and visual['actual_encoded_video_frames_not_source_overlays'] and len(visual['actual_images_viewed']) >= 6,
            '实际编码帧图审证据不完整')
    for image in visual['actual_images_viewed']:
        receipt = read_reference(image['receipt'])
        require(receipt['source_sha256'] == upload['output']['sha256']
                and receipt['sha256'] == image['image']['sha256']
                and max(receipt['dimensions']) <= 1280 and image['image']['bytes'] <= 192 * 1024,
                '图审图片不是当前候选的受限JPEG')
    decode = upload['verification']['full_decode']
    require(decode['exit_code'] == 0 and decode['log']['bytes'] == 0, '候选上传全片解码没有通过')
    return fidelity, runtime


def verify_intent(work, intent, resolve=current_reference, current_render=None, database=None):
    require(intent['version'] == VERSION and intent['kind'] == KIND and intent['work'] == str(work)
            and intent['modules'] == module_identities(), 'v4意向工程、版本或生产实现变化')
    original = intent['original']
    files.destinations(work, original)
    for observation in [*original.values(), *intent['evidence'].values(), intent['base_package_manifest']]:
        resolve(observation)
    checkpoint = read_reference(intent['evidence']['candidate_checkpoint'])
    require(checkpoint['state'] == 'caption_boundary_candidate_pair_verified_installation_unimplemented'
            and checkpoint['formal_before'] == original and checkpoint['production_applied'] is False,
            '只接受尚未安装的已审双版本候选断点')
    for observation in [checkpoint['master_candidate'], *checkpoint['evidence'].values(), checkpoint['previous_preview_checkpoint']]:
        resolve(observation)
    terminal = read_reference(intent['evidence']['candidate_terminal'])
    require(terminal['checkpoint'] == intent['evidence']['candidate_checkpoint'], '独立终态没有绑定当前候选断点')
    file_contracts = read_reference(intent['evidence']['file_contracts'])
    require(file_contracts['passed'] and file_contracts['tests_run'] == 40
            and file_contracts['implementation'] == intent['modules']['file_transaction'], '文件事务不是已测实现')
    references(file_contracts)
    master = read_reference(checkpoint['master_candidate'])
    upload = read_reference(checkpoint['evidence']['upload_candidate'])
    require(master['work'] == upload['work'] == str(work) and master['version'] == upload['version'] == VERSION
            and master['kind'] == 'caption-boundary-master-candidate-v4'
            and upload['kind'] == 'caption-boundary-upload-candidate-v4'
            and master['implementation'] == intent['modules']['master_producer']
            and master['reused_helpers'] == intent['modules']['master_helpers']
            and master['reused_package_verifier'] == intent['modules']['legacy_package']
            and upload['dependencies']['candidate_exporter'] == intent['modules']['upload_candidate']
            and master['candidate_only'] is True and master['production_applied'] is upload['production_applied'] is False
            and master['original_master'] == original['master']
            and master['input_package_manifest'] == intent['base_package_manifest']
            and master['all_logical_chapters_identical'] and not master['chapter_data_packets_claimed_identical'],
            'v4候选来源、版本、章节或生产边界无效')
    references(master, resolve)
    references(upload, resolve)
    preview = read_reference(master['text_preview'])
    references(preview, resolve)
    previous = read_reference(checkpoint['previous_preview_checkpoint'])
    raster = read_reference(previous['evidence']['independent_text_and_raster'])
    references(raster, resolve)
    require(master['text_preview'] == upload['dependencies']['text_preview'] == previous['evidence']['text_preview']
            and checkpoint['master_candidate'] == upload['dependencies']['master_candidate_result']
            and master['output'] == upload['dependencies']['master_candidate']
            and upload['dependencies']['inner_package_manifest'] == intent['base_package_manifest']
            and upload['dependencies']['repaired_chinese_captions'] == master['subtitles']['zh']
            and upload['dependencies']['repaired_bilingual_captions'] == master['subtitles']['bilingual']
            and upload['dependencies']['checkpoint'] == checkpoint['previous_preview_checkpoint']
            and upload['dependencies']['master'] == original['master']
            and upload['dependencies']['upload'] == original['upload']
            and upload['dependencies']['package_render_state'] == original['render_state']
            and upload['dependencies']['upload_manifest'] == original['upload_manifest'],
            '候选双版本未使用同一字幕与母版')
    base, rendering = base_package(work, original, intent['base_package_manifest'], resolve)
    quality, bindings = normalized_quality(work, original['render_state'], current_render or original['render_state'], database)
    require(bindings == upload['quality']['bindings'] == intent['quality_before']['bindings']
            and upload['quality']['allowed'] is intent['quality_before']['allowed'] is True,
            '除明确render_state迁移外的质量输入发生变化')
    base_upload(work, original, base, intent['base_package_manifest'], bindings, resolve)
    forms = text_projection(work, preview, master, base, original, intent['base_package_manifest'])
    raster_and_layout(work, preview, master, upload, raster, master['text_preview'], base, forms)
    audio = read_reference(intent['evidence']['audio_packets'])
    fidelity, runtime = fidelity_audio_runtime(checkpoint, master, upload, audio, terminal)
    return {'checkpoint': checkpoint, 'master': master, 'upload': upload, 'preview': preview, 'base': base,
            'rendering': rendering, 'quality': quality, 'fidelity': fidelity, 'runtime': runtime}


def package_record(intent_reference, intent, context):
    base, master, upload = context['base'], context['master'], context['upload']
    return {'version': VERSION, 'kind': KIND, 'created_at': intent['created_at'], 'work': intent['work'],
        **{key: copy.deepcopy(base[key]) for key in BASE_FIELDS},
        'producer': intent['modules']['producer'], 'promotion_intent': intent_reference,
        'base_package_manifest': intent['base_package_manifest'],
        'caption_producer': {'master_candidate': context['checkpoint']['master_candidate'],
            'implementation': master['implementation'], 'text_preview': master['text_preview']},
        'subtitles': copy.deepcopy(master['subtitles']),
        'output': files.rebase(master['output'], intent['original']['master']['path']),
        'verification': copy.deepcopy(master['media_verification']),
        'decode_basis': {'master_av': 'retained-base-full-decode-and-exact-ordered-av-packet-preservation',
            'base_full_decode': copy.deepcopy(base['full_decode']),
            'master_preserved_packets': copy.deepcopy(master['preserved_encoded_stream_packets']),
            'upload_full_decode': copy.deepcopy(upload['verification']['full_decode'])},
        'new_video_or_audio_synthesis': False, 'word_alignment_verified': False, 'human_sync_approved': False,
        'full_human_listening': False, 'release_or_upload_performed': False}


def render_record(intent_reference, intent, context, package_reference):
    rendering = copy.deepcopy(context['rendering'])
    rendering.update(origin='self-contained-caption-boundary-v4', package_version=VERSION,
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


def revision_directory(work, directory):
    directory = files.canonical(directory)
    require(directory.parent == work / 'paired_packages' and len(directory.name) == 32
            and all(character in '0123456789abcdef' for character in directory.name), 'v4包目录不属于当前工程')
    return directory


def verify_revision(work, directory, prepared=None, installed=False, database=None):
    directory = revision_directory(work, directory)
    intent_reference = state.identity(directory / 'intent.json')
    intent = read_reference(intent_reference)
    resolve = current_reference
    render_reference = intent['original']['render_state']
    if prepared is not None:
        require(prepared['work'] == str(work) and prepared['evidence']['intent'] == intent_reference
                and prepared['original'] == intent['original'], '文件事务与生产意向不符')
        staged_directory = Path(prepared['staged']['master']['path']).parent.parent
        resolve = files.ArchiveRouter(work, staged_directory, prepared['original'], prepared['archived']).resolve
        if installed:
            files.verify_set(prepared, 'desired')
            render_reference = prepared['desired']['render_state']
        else:
            files.verify_set(prepared, 'original')
    context = verify_intent(work, intent, resolve, render_reference, database)
    package_reference = state.identity(directory / 'manifest.json')
    package = read_reference(package_reference)
    require(package == package_record(intent_reference, intent, context), 'v4包不是声明生产器的确定性元数据')
    new_render_reference = state.identity(directory / 'render_state.json')
    new_render = read_reference(new_render_reference)
    require(new_render == render_record(intent_reference, intent, context, package_reference), 'v4母版清单来源或输出不符')
    formal_render = files.rebase(new_render_reference, work / 'render_state.json')
    new_upload_reference = state.identity(directory / 'upload_export.json')
    new_upload = read_reference(new_upload_reference)
    require(new_upload == upload_record(intent_reference, intent, context, package_reference, formal_render),
            'v4上传清单来源、质量或输出不符')
    replacements = {'master': context['master']['output'], 'upload': context['upload']['output'],
                    'render_state': new_render_reference, 'upload_manifest': new_upload_reference}
    if prepared is not None:
        require(prepared['sources'] == replacements, '文件事务未暂存本次生产器的全部四个替换文件')
    if installed:
        require(state.load(work / 'render_state.json') == new_render
                and state.load(work / 'upload_export.json') == new_upload
                and new_upload['dependencies']['expected_current_quality_bindings'] == context['quality']['bindings'],
                '当前正式清单或实际质量输入不是已安装版本')
    master_path = package['output']['path'] if installed else context['master']['output']['path']
    upload_path = new_upload['output']['path'] if installed else context['upload']['output']['path']
    video_package.verify_media(Path(master_path), package)
    video_package.verify_media(Path(upload_path), package, upload=True)
    video = next(stream for stream in video_package.probe(Path(upload_path))['streams'] if stream['codec_type'] == 'video')
    geometry = new_upload['profile']['geometry']
    require([video['width'], video['height']] == [geometry['output_width'], geometry['output_height']],
            '实际上传画幅不是已验证安全带')
    return {'intent': intent, 'intent_reference': intent_reference, 'package': package,
        'package_reference': package_reference, 'rendering': new_render, 'upload': new_upload,
        'replacements': replacements, 'context': context}


def draft_intent(work, *, checkpoint, audio_packets, terminal, file_contracts, database=None):
    work = files.canonical(work)
    upload_delivery.pending_transactions(work)
    files.pending_transactions(work)
    base = video_package.validate(work)
    old_upload = upload_delivery_v3.validate_current(work, database=database)
    require(old_upload['version'] == 1, '当前适配器只支持v1上传到v4的显式迁移')
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
    require(not directory.exists(), '不能覆盖既有v4生产包')
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
        return {'state': 'revision_verified', 'directory': str(directory), 'intent': intent_reference,
                'production_applied': False}


def prepared_revision(prepared):
    intent_reference = prepared['evidence']['intent']
    files.checked(intent_reference)
    return Path(prepared['work']), Path(intent_reference['path']).parent


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
    record = upload_delivery_v3.validate_current(work, database=database)
    require(record['version'] == 1, '恢复的旧版上传不是本适配器声明的v1')
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
    return files.recover(files.canonical(work), files.canonical(transaction), verify_current=validators['verify_current'],
                         verify_original=validators['verify_original'])


def current_transaction(work):
    rendering = state.load(work / 'render_state.json')
    require(rendering.get('package_version') == VERSION, '正式母版不是显式v4包')
    package_reference = rendering['package_manifest']
    files.checked(package_reference)
    directory = revision_directory(work, Path(package_reference['path']).parent)
    require(Path(package_reference['path']).name == 'manifest.json', 'v4包清单路径不正确')
    intent_reference = state.identity(directory / 'intent.json')
    found = []
    for path in (work / 'paired_revisions').glob('*/transaction.json'):
        journal = state.load(files.transaction_path(work, path))
        if journal.get('state') != 'committed':
            continue
        prepared, _ = files.load_transaction(work, path)
        if prepared['evidence'].get('intent') == intent_reference and all(
                state.matches(observation) for observation in prepared['desired'].values()):
            found.append((path, prepared))
    require(len(found) == 1, '需要唯一已提交且与当前四文件完全一致的v4事务')
    files.pending_transactions(work, found[0][0])
    return found[0]


def validate_current(work, database=None):
    work = files.canonical(work)
    path, prepared = current_transaction(work)
    verified = verify_installed(prepared, database)
    package, context = verified['package'], verified['context']
    return {'id': work.name.removeprefix('.zh_work_yt_'), 'upload_version': VERSION,
        'manifest': verified['package_reference'], 'master': package['output'], 'upload': verified['upload']['output'],
        'intro_seconds': package['body_offset_seconds'], 'duration_seconds': package['duration_seconds'],
        'chapters': len(package['chapters']), 'transaction': state.identity(path), 'intent': verified['intent_reference'],
        'quality': context['quality'], 'current_full_decode_evidence_valid': True,
        'master_decode_basis': package['decode_basis']['master_av'],
        'candidate_vlc_evidence_reused_by_exact_content': context['checkpoint']['evidence']['runtime'],
        'canonical_path_vlc_tested_by_this_validator': False,
        'source_region_fidelity': context['checkpoint']['evidence']['fidelity'],
        'current_chinese_aac_identical': True,
        'current_master_chinese_aac': context['upload']['master_chinese_aac'],
        'current_upload_chinese_aac': context['upload']['candidate_chinese_aac'],
        'word_alignment_verified': False, 'human_listened': False, 'human_gui_checked': False, 'potplayer_tested': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'create', 'prepare', 'plan', 'promote', 'verify', 'rollback', 'recover'])
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--transaction', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--audio-packets', type=Path)
    parser.add_argument('--terminal', type=Path)
    parser.add_argument('--file-contracts', type=Path)
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    require(not options.apply or options.action in {'promote', 'rollback'}, '--apply只能用于显式晋升或回滚')
    if options.action in {'preflight', 'create'}:
        require(all([options.checkpoint, options.audio_packets, options.terminal, options.file_contracts]), '缺少候选或契约证据')
        inputs = {'checkpoint': options.checkpoint, 'audio_packets': options.audio_packets,
                  'terminal': options.terminal, 'file_contracts': options.file_contracts}
        if options.action == 'preflight':
            with files.project_lock(files.canonical(options.work)):
                intent = draft_intent(options.work, **inputs)
                context = verify_intent(options.work, intent)
            result = {'state': 'production_candidates_verified_read_only', 'work': str(options.work),
                'master': context['master']['output'], 'upload': context['upload']['output'],
                'cues': context['preview']['proof']['cue_count'], 'production_applied': False}
        else:
            directory = options.directory or options.work / 'paired_packages' / uuid.uuid4().hex
            result = create(options.work, directory, **inputs)
    elif options.action == 'prepare':
        require(options.directory is not None, '需要已验证的生产包目录')
        result = prepare(options.work, options.directory)
    elif options.action == 'verify':
        result = validate_current(options.work)
    else:
        require(options.transaction is not None, '需要具体事务日志')
        if options.action in {'plan', 'promote'}:
            result = promote(options.work, options.transaction, apply=options.apply)
        elif options.action == 'rollback':
            result = rollback(options.work, options.transaction, apply=options.apply)
        else:
            result = recover(options.work, options.transaction)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
