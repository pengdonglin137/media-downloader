"""安全字幕带的显式版本分派和可回滚安装；不修改旧版交付契约。"""

import copy
import fcntl
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

import caption_layout
import delivery_quality
import dubbing_state as state
import upload_delivery as legacy
import upload_export
import upload_export_v2
import upload_export_v3
import video_package
from prepare_videos import save_json


def require(condition, message):
    if not condition:
        raise ValueError(message)


@contextmanager
def project_lock(work):
    with (work / '.workflow.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('工程正被其他流程占用，未修改交付') from error
        yield


def candidate(work, directory, previous=None, database=None):
    work, _, _ = legacy.paths(work)
    require(not Path(directory).is_symlink(), '候选目录不能是符号链接')
    directory = Path(directory).resolve(strict=True)
    require(directory.is_relative_to(work) or directory.is_relative_to(work.parent / '.workflow_logs'),
            '候选必须位于隐藏工程或工作流证据目录')
    result_path = directory / 'candidate-result.json'
    result = state.load(result_path)
    require(result['version'] == 3 and result['profile']['layout'] == upload_export_v3.LAYOUT
            and result['production_applied'] is False and result['current_delivery_unchanged'] is True
            and result['output']['path'] == str(directory / 'candidate.mp4')
            and not (directory / 'candidate.mp4').is_symlink() and state.matches(result['output']), 'v3候选身份无效')
    dependencies = result['dependencies']
    modules = {'candidate_exporter': upload_export_v3.__file__, 'caption_layout': caption_layout.__file__,
               'legacy_exporter': upload_export.__file__, 'v2_audio_helper': upload_export_v2.__file__,
               'package_implementation': video_package.__file__, 'quality_implementation': delivery_quality.__file__}
    require(all(dependencies[key] == state.identity(path) for key, path in modules.items()), '候选实现不是当前显式版本')
    for key, observation in dependencies.items():
        if previous is not None and key in {'upload', 'upload_manifest'}:
            require(legacy.equivalent(observation, previous[key]), '原上传归档与候选绑定不符：' + key)
        else:
            require(state.matches(observation), '候选输入变化：' + key)
    package = video_package.validate(work)
    require(dependencies['master'] == package['output'] and dependencies['captions'] == package['subtitles']['zh'],
            '候选不是当前母版及中文字幕')
    media = upload_export.probe(Path(package['output']['path']))
    video = next(entry for entry in media['streams'] if entry['codec_type'] == 'video')
    script, report = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
                                              float(media['format']['duration']),
                                              caption_layout.load_forms(dependencies['terms']['path']),
                                              Path(package['subtitles']['zh']['path']))
    content = upload_export_v3.verify_script(script, Path(package['subtitles']['zh']['path']),
                                             float(media['format']['duration']))
    prepared = state.load(dependencies['preflight']['path'])
    require(script == Path(dependencies['ass']['path']).read_text() and report == result['layout_report']
            and content == result['content_check'] and prepared['version'] == 3
            and prepared['work'] == str(work) and prepared['report'] == report
            and prepared['content_check'] == content and prepared['ass'] == dependencies['ass'], '实际ASS、内容或预检不可重建')
    geometry = report['geometry']
    require(result['profile']['geometry'] == result['verification']['geometry'] == geometry
            and result['profile']['position'] == 'added-bottom-band', '候选安全带配置不符')
    proof_names = {'raster': 'independent-safe-band-preflight.json', 'fidelity': 'full-source-region-fidelity.json',
                   'visual': 'actual-image-review-v3.json', 'runtime': 'candidate-vlc-result.json'}
    proofs = {key: state.identity(directory / name) for key, name in proof_names.items()}
    raster = state.load(proofs['raster']['path'])
    require(raster['passed'] and raster['preflight'] == dependencies['preflight']
            and raster['cues_independently_checked'] == raster['actual_libass_bounding_boxes_checked'] == content['cues']
            and raster['overlapping_intervals'] == 0 and raster['geometry'] == geometry
            and raster['all_rendered_cue_bounds_inside_added_band'] is True
            and raster['minimum_rendered_y'] >= geometry['source_height'] + geometry['outline'] + 4
            and raster['maximum_rendered_y'] < geometry['output_height'] - geometry['outline'] - 4
            and state.matches(raster['implementation']), '全字幕字形边界证明失效')
    require(prepared['raster_check']['passed'] and prepared['raster_check']['cues_rasterized'] == content['cues']
            and all(prepared['raster_check'][key] == dependencies['raster_' + suffix]
                    for key, suffix in [('ass', 'ass'), ('metadata', 'metadata'), ('log', 'log')]), '原始栅格输入绑定不符')
    fidelity = state.load(proofs['fidelity']['path'])
    require(fidelity['passed'] and fidelity['master'] == package['output'] and fidelity['candidate'] == result['output']
            and fidelity['compared_candidate_region'] == [0, 0, geometry['source_width'], geometry['source_height']]
            and fidelity['predeclared_thresholds'] == {'mean_frame_ssim': .99, 'minimum_frame_ssim': .95}
            and fidelity['mean_frame_ssim'] >= .99 and fidelity['minimum_frame_ssim'] >= .95
            and fidelity['maximum_pts_error_seconds'] <= .0001
            and fidelity['maximum_frame_duration_error_seconds'] <= .0001
            and fidelity['video_frames_compared'] == int(video['nb_frames'])
            and fidelity['source_pixels_bitwise_identical_claimed'] is False,
            '新候选全帧、PTS或原片区域画质证明不完整')
    require(all(state.matches(fidelity[key]) for key in
                ['master', 'candidate', 'master_packet_inventory', 'candidate_packet_inventory', 'statistics', 'log', 'implementation']),
            '画质原始证据变化')
    notes = state.load(proofs['visual']['path'])
    require(notes['candidate'] == result['output'] and notes['candidate_visual_sample_review_passed'] is True
            and notes['all_cue_raster_proof'] == proofs['raster'] and notes['images'], '图审未绑定当前候选')
    cue_ids = [image['cue_id'] for image in notes['images']]
    require(len(cue_ids) == len(set(cue_ids)) and {1, content['cues']}.issubset(cue_ids)
            and {entry['cue_id'] for entry in report['changes']}.issubset(cue_ids), '片头、片尾或变化字幕图审缺失')
    for image in notes['images']:
        require(image['actually_viewed_by_current_agent'] is True
                and image['caption_text_readable_and_unclipped'] is True
                and image['source_content_and_controls_not_overlaid_by_captions'] is True
                and state.matches(image['image']) and state.matches(image['receipt']), '图审图片或实际观察失效')
        receipt = state.load(image['receipt']['path'])
        require(receipt['source_sha256'] == result['output']['sha256'] and receipt['sha256'] == image['image']['sha256']
                and receipt['dimensions'] == image['dimensions'] and max(receipt['dimensions']) <= 1280
                and image['image']['bytes'] <= 192 * 1024, '图审不是当前受限JPEG')
    runtime = state.load(proofs['runtime']['path'])
    require(runtime['passed'] and runtime['candidate']['passed'] and runtime['candidate']['media'] == result['output']
            and len(runtime['candidate']['chapters']) == len(package['chapters'])
            and runtime['candidate']['chapter_seeks'] and runtime['candidate']['rendered_snapshots']
            and all(state.matches(image) for image in runtime['candidate']['rendered_snapshots']), '候选VLC证据失效')
    decode = result['verification']['full_decode']
    require(decode['exit_code'] == 0 and state.matches(decode['log']), '完整解码证据失效')
    require(delivery_quality.require(work, database)['bindings'] == result['quality']['bindings'], '当前质量输入变化')
    video_package.verify_media(Path(result['output']['path']), package, upload=True)
    actual_video = next(entry for entry in upload_export.probe(Path(result['output']['path']))['streams']
                        if entry['codec_type'] == 'video')
    require([actual_video['width'], actual_video['height']] == [geometry['output_width'], geometry['output_height']]
            and actual_video.get('sample_aspect_ratio') == video.get('sample_aspect_ratio'), '候选实际画幅不符')
    require(upload_export_v2.audio_digest(Path(result['output']['path'])) == result['candidate_chinese_aac']
            == result['master_chinese_aac'] == upload_export_v2.audio_digest(Path(package['output']['path'])), '中文AAC不一致')
    return result, package, state.identity(result_path), proofs


def validate_current(work, record=None, output_override=None, database=None):
    work, output, manifest_path = legacy.paths(work)
    record = state.load(manifest_path) if record is None else record
    if record['version'] in {1, 2}:
        return legacy.validate_current(work, record, output_override, database)
    require(record['version'] == 3, '不支持的上传交付版本')
    actual = record['output'] if output_override is None else output_override
    require(record['output']['path'] == str(output) and legacy.equivalent(record['output'], actual), '当前上传文件身份失效')
    dependencies = record['dependencies']
    require(dependencies['implementation'] == state.identity(__file__)
            and dependencies['legacy_dispatcher'] == state.identity(legacy.__file__)
            and state.matches(dependencies['prepared']) and state.matches(dependencies['candidate_result']), 'v3事务绑定失效')
    prepared_path = Path(dependencies['prepared']['path'])
    require(prepared_path.name == 'prepared.json' and prepared_path.parent.parent == work / 'upload_revisions'
            and not prepared_path.is_symlink(), '事务不属于当前工程')
    prepared = state.load(prepared_path)
    require(prepared['version'] == 3 and prepared['work'] == str(work)
            and prepared['implementation'] == dependencies['implementation'], '事务归属或实现不一致')
    reviewed, package, reference, proofs = candidate(work, Path(dependencies['candidate_result']['path']).parent,
                                                     prepared['archived'], database)
    require(reference == prepared['candidate_result'] == dependencies['candidate_result']
            and proofs == prepared['proofs'] == dependencies['proofs']
            and legacy.equivalent(reviewed['output'], actual)
            and record['verification'] == reviewed['verification'] and record['profile'] == reviewed['profile']
            and dependencies['quality_bindings'] == reviewed['quality']['bindings'], '当前v3上传不是已审验候选')
    video_package.verify_media(Path(actual['path']), package, upload=True)
    require(upload_export_v2.audio_digest(Path(actual['path'])) == reviewed['master_chinese_aac'], '当前正式中文AAC不一致')
    return record


def install(pending, destination):
    Path(pending).replace(destination)


def restore_files(prepared, transaction_directory):
    work, output, manifest_path = legacy.paths(Path(prepared['work']))
    require(all(state.matches(identity) for identity in prepared['archived'].values()), '回滚归档不完整')
    installed_path = transaction_directory / 'installed-manifest.json'
    installed = state.load(installed_path)
    require(state.digest(output) in {prepared['original']['upload']['sha256'], installed['output']['sha256']}
            and state.digest(manifest_path) in {prepared['original']['upload_manifest']['sha256'], state.digest(installed_path)},
            '存在事务外的文件修改，拒绝覆盖')
    pending_video = output.with_name(f'.upload-v3-rollback-{uuid.uuid4().hex}.mp4')
    pending_manifest = transaction_directory / f'rollback-{uuid.uuid4().hex}.json'
    try:
        os.link(prepared['archived']['upload']['path'], pending_video)
        shutil.copy2(prepared['archived']['upload_manifest']['path'], pending_manifest)
        install(pending_video, output)
        install(pending_manifest, manifest_path)
        require(all(state.matches(identity) for identity in prepared['original'].values()), '回滚后的原文件身份不一致')
    finally:
        pending_video.unlink(missing_ok=True)
        pending_manifest.unlink(missing_ok=True)


def promote(work, directory, apply=False, database=None):
    work, output, manifest_path = legacy.paths(work)
    with project_lock(work):
        legacy.pending_transactions(work)
        current = validate_current(work, database=database)
        reference = state.identity(Path(directory).resolve(strict=True) / 'candidate-result.json')
        if current['version'] == 3 and current['dependencies']['candidate_result'] == reference:
            return {'state': 'already_current', 'output': current['output'], 'applied': False}
        reviewed, _, reference, proofs = candidate(work, directory, database=database)
        if not apply:
            return {'state': 'planned', 'work': str(work), 'candidate_result': reference, 'proofs': proofs,
                    'previous_output': current['output'], 'candidate_output': reviewed['output'], 'applied': False}
        require(Path(reviewed['output']['path']).stat().st_dev == output.parent.stat().st_dev, '需要同文件系统硬链接事务')
        transaction_directory = work / 'upload_revisions' / uuid.uuid4().hex
        transaction_directory.mkdir(parents=True)
        archived_video = transaction_directory / 'previous.mp4'
        archived_manifest = transaction_directory / 'previous-manifest.json'
        os.link(output, archived_video)
        shutil.copy2(manifest_path, archived_manifest)
        prepared = {'version': 3, 'created_at': state.now(), 'work': str(work), 'implementation': state.identity(__file__),
                    'original': {'upload': current['output'], 'upload_manifest': state.identity(manifest_path)},
                    'archived': {'upload': state.identity(archived_video), 'upload_manifest': state.identity(archived_manifest)},
                    'candidate_result': reference, 'candidate_output': reviewed['output'], 'proofs': proofs}
        prepared_path = transaction_directory / 'prepared.json'
        save_json(prepared_path, prepared)
        updated = {'version': 3, 'created_at': state.now(),
                   'dependencies': {'implementation': state.identity(__file__), 'legacy_dispatcher': state.identity(legacy.__file__),
                                    'prepared': state.identity(prepared_path), 'candidate_result': reference, 'proofs': proofs,
                                    'quality_bindings': reviewed['quality']['bindings']},
                   'output': {**reviewed['output'], 'path': str(output)}, 'profile': reviewed['profile'],
                   'verification': copy.deepcopy(reviewed['verification']), 'quality': reviewed['quality'],
                   'human_listened': False, 'production_applied': True}
        installed_path = transaction_directory / 'installed-manifest.json'
        save_json(installed_path, updated)
        journal_path = transaction_directory / 'transaction.json'
        journal = {'version': 3, 'created_at': state.now(), 'state': 'prepared', 'prepared': state.identity(prepared_path),
                   'installed_manifest': state.identity(installed_path)}
        save_json(journal_path, journal)
        pending_video = output.with_name(f'.upload-v3-{transaction_directory.name}.mp4')
        pending_manifest = transaction_directory / 'pending-manifest.json'
        touched = False
        try:
            os.link(reviewed['output']['path'], pending_video)
            shutil.copy2(installed_path, pending_manifest)
            candidate(work, directory, database=database)
            require(all(state.matches(identity) for identity in prepared['original'].values()), '安装前原交付变化')
            touched = True
            install(pending_video, output)
            journal['state'] = 'video_installed'
            save_json(journal_path, journal)
            install(pending_manifest, manifest_path)
            journal['state'] = 'manifest_installed'
            save_json(journal_path, journal)
            validate_current(work, database=database)
            journal.update(state='committed', finished_at=state.now(), output=state.identity(output))
            save_json(journal_path, journal)
            return {'state': 'committed', 'output': updated['output'], 'applied': True, 'transaction': str(journal_path)}
        except BaseException as error:
            if touched:
                try:
                    restore_files(prepared, transaction_directory)
                except BaseException as recovery_error:
                    journal.update(state='recovery_required', error=f'{type(error).__name__}: {error}',
                                   recovery_error=f'{type(recovery_error).__name__}: {recovery_error}')
                    save_json(journal_path, journal)
                    raise
            journal.update(state='automatic_rollback' if touched else 'failed_before_install',
                           error=f'{type(error).__name__}: {error}', finished_at=state.now())
            save_json(journal_path, journal)
            raise
        finally:
            pending_video.unlink(missing_ok=True)
            pending_manifest.unlink(missing_ok=True)


def rollback(work, transaction, apply=False, database=None):
    work, output, manifest_path = legacy.paths(work)
    transaction = Path(transaction).resolve(strict=True)
    require(transaction.name == 'transaction.json' and transaction.parent.parent == work / 'upload_revisions', '回滚事务归属不符')
    with project_lock(work):
        legacy.pending_transactions(work, transaction)
        journal = state.load(transaction)
        require(journal['version'] == 3 and state.matches(journal['prepared'])
                and state.matches(journal['installed_manifest']), 'v3事务证据变化')
        prepared = state.load(journal['prepared']['path'])
        require(prepared['version'] == 3 and prepared['work'] == str(work)
                and prepared['implementation'] == state.identity(__file__), 'v3事务实现或工程不符')
        require(all(legacy.equivalent(prepared['original'][key], observation)
                    for key, observation in prepared['archived'].items()), '归档与原版本不一致')
        if journal['state'] in {'rolled_back', 'automatic_rollback'}:
            require(all(state.matches(observation) for observation in prepared['original'].values()), '已回滚状态与实际文件不符')
            return {'state': 'already_rolled_back', 'output': prepared['original']['upload'], 'applied': False}
        installed = state.load(journal['installed_manifest']['path'])
        require(state.digest(output) in {prepared['original']['upload']['sha256'], installed['output']['sha256']}
                and state.digest(manifest_path) in {prepared['original']['upload_manifest']['sha256'], journal['installed_manifest']['sha256']},
                '当前文件不是事务内的新旧版本')
        validate_current(work, state.load(prepared['archived']['upload_manifest']['path']), prepared['archived']['upload'], database)
        if not apply:
            return {'state': 'rollback_planned', 'output': prepared['original']['upload'], 'applied': False, 'transaction': str(transaction)}
        restore_files(prepared, transaction.parent)
        journal.update(state='rolled_back', rollback_at=state.now())
        save_json(transaction, journal)
        return {'state': 'rolled_back', 'output': prepared['original']['upload'], 'applied': True, 'transaction': str(transaction)}
