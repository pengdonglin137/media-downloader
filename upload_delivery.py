"""上传交付的版本分派、候选晋升与可回滚事务；调用者持工程锁。"""

import copy
import os
import shutil
import uuid
from pathlib import Path

import caption_layout
import check_videos
import delivery_quality
import dubbing_state as state
import upload_export
import upload_export_v2
import video_package
from prepare_videos import save_json


TERMINAL = {'committed', 'rolled_back', 'automatic_rollback', 'failed_before_install'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def paths(work):
    work = Path(work)
    require(work.is_dir() and not work.is_symlink(), '需要真实工程目录')
    work = work.resolve()
    master = check_videos.output_path(work.parent, state.load(work / 'meta.json')['title'])
    output = master.with_name(master.stem.removesuffix('_中文配音') + '_中文上传版.mp4')
    require(not output.is_symlink() and not (work / 'upload_export.json').is_symlink(), '交付不能是符号链接')
    return work, output, work / 'upload_export.json'


def equivalent(original, archived):
    return (state.matches(archived)
            and {key: value for key, value in original.items() if key != 'path'}
            == {key: value for key, value in archived.items() if key != 'path'})


def pending_transactions(work, except_path=None):
    history = work / 'upload_revisions'
    require(not history.is_symlink(), '事务目录不能是符号链接')
    for transaction in history.glob('*/transaction.json'):
        if transaction != except_path:
            require(state.load(transaction)['state'] in TERMINAL,
                    f'存在未完成上传事务，先核对或回滚：{transaction}')


def candidate(work, directory, previous=None, database=None):
    work, _, _ = paths(work)
    directory = Path(directory).resolve(strict=True)
    require(directory.is_relative_to(work) or directory.is_relative_to(work.parent / '.workflow_logs'),
            '候选必须位于隐藏工程或工作流证据目录')
    result_path = directory / 'candidate-result.json'
    result = state.load(result_path)
    require(result['version'] == 2 and result['profile']['layout'] == 'registered-terms-v2'
            and result['production_applied'] is False and state.matches(result['output']), '候选版本或文件身份无效')
    dependencies = result['dependencies']
    expected_modules = {'implementation': caption_layout.__file__, 'legacy': upload_export.__file__,
                        'candidate_exporter': upload_export_v2.__file__, 'package_implementation': video_package.__file__}
    require(all(dependencies[key] == state.identity(module) for key, module in expected_modules.items()),
            '候选绑定的实现不是当前显式版本')
    for key, identity in dependencies.items():
        if previous is not None and key in {'upload', 'upload_manifest'}:
            require(equivalent(identity, previous[key]), f'候选原输入的归档身份不一致：{key}')
        else:
            require(state.matches(identity), f'候选输入已变化：{key}')
    package = video_package.validate(work)
    require(dependencies['master'] == package['output'] and dependencies['captions'] == package['subtitles']['zh'],
            '候选不属于当前组合母版或中文字幕')
    preflight = state.load(dependencies['preflight']['path'])
    require(preflight['work'] == str(work) and preflight['report'] == result['layout_report'], '预检工程或布局不一致')
    media = upload_export.probe(Path(package['output']['path']))
    video = next(item for item in media['streams'] if item['codec_type'] == 'video')
    script, report = caption_layout.subtitles(work, int(video['width']), int(video['height']),
                                            float(media['format']['duration']), 'bottom',
                                            caption_layout.load_forms(Path(dependencies['terms']['path'])),
                                            Path(package['subtitles']['zh']['path']))
    require(script == Path(dependencies['ass']['path']).read_text() and report == result['layout_report'],
            '候选ASS无法由当前输入独立重建')
    decode = result['verification']['full_decode']
    require(decode['exit_code'] == 0 and state.matches(decode['log']), '候选全片解码证据失效')
    runtime = state.load(directory / 'candidate-vlc-result.json')
    require(runtime['passed'] and runtime['candidate']['passed']
            and runtime['candidate']['media'] == result['output']
            and all(state.matches(image) for image in runtime['candidate']['rendered_snapshots']),
            '候选当前播放器运行证据不完整')
    images = state.load(directory / 'review-image-inputs.json')
    notes = state.load(directory / 'actual-image-review.json')
    changes = result['layout_report']['changes']
    require(images['candidate'] == result['output'] and notes['candidate_sha256'] == result['output']['sha256']
            and notes['actually_viewed'] is True and len(images['images']) == len(notes['observations']) == len(changes),
            '候选变化字幕尚未完成绑定图审')
    require({image['cue_id'] for image in images['images']} == {change['cue_id'] for change in changes}
            == {note['cue_id'] for note in notes['observations']}, '变化字幕图审编号不完整或重复')
    for image in images['images']:
        require(state.matches(image['image']) and state.matches(image['receipt']), '候选图审文件已变化')
        receipt = state.load(image['receipt']['path'])
        require(receipt['source_sha256'] == result['output']['sha256'] and max(receipt['dimensions']) <= 1280
                and image['image']['bytes'] <= 192 * 1024
                and receipt['sha256'] == image['image']['sha256']
                and any(note['cue_id'] == image['cue_id'] and note['image_sha256'] == image['image']['sha256']
                        for note in notes['observations']), '图审未绑定当前受限JPEG')
    require(delivery_quality.require(work, database)['bindings'] == result['quality']['bindings'], '候选质量输入已变化')
    video_package.verify_media(Path(result['output']['path']), package, upload=True)
    require(upload_export_v2.audio_digest(Path(result['output']['path']))
            == upload_export_v2.audio_digest(Path(package['output']['path'])), '候选中文AAC与当前母版不一致')
    return result, package, state.identity(result_path)


def validate_current(work, record=None, output_override=None, database=None):
    work, output, manifest_path = paths(work)
    record = state.load(manifest_path) if record is None else record
    actual = record['output'] if output_override is None else output_override
    require(record['output']['path'] == str(output) and equivalent(record['output'], actual), '当前上传文件身份失效')
    package = video_package.validate(work)
    quality = delivery_quality.require(work, database)
    if record['version'] == 1:
        dependencies = record['dependencies']
        source = dependencies.get('self_contained', {})
        require(dependencies['implementation_sha256'] == state.digest(upload_export.__file__)
                and source.get('manifest') == state.load(work / 'render_state.json')['package_manifest']
                and source.get('master') == package['output'] and dependencies['quality_bindings'] == quality['bindings']
                and state.matches(dependencies['font']), '旧版上传的实现、母版、质量或字体绑定失效')
    elif record['version'] == 2:
        dependencies = record['dependencies']
        require(dependencies['implementation'] == state.identity(__file__)
                and state.matches(dependencies['prepared']) and state.matches(dependencies['candidate_result']),
                '新版上传的实现或事务绑定失效')
        prepared_path = Path(dependencies['prepared']['path'])
        require(prepared_path.name == 'prepared.json' and prepared_path.parent.parent == work / 'upload_revisions',
                '新版事务不是本工程归档')
        prepared = state.load(prepared_path)
        require(prepared['work'] == str(work) and prepared['implementation'] == dependencies['implementation'],
                '事务归属或实现不一致')
        reviewed, _, reference = candidate(work, Path(dependencies['candidate_result']['path']).parent,
                                            prepared['archived'], database)
        require(reference == dependencies['candidate_result'] and equivalent(reviewed['output'], actual)
                and record['verification'] == reviewed['verification'] and record['profile'] == reviewed['profile']
                and dependencies['quality_bindings'] == quality['bindings'], '新版当前输出不等于已验收候选')
    else:
        raise ValueError('不支持的上传交付版本')
    decode = record['verification']['full_decode']
    require(decode['exit_code'] == 0 and state.matches(decode['log']), '当前上传的完整解码证据失效')
    video_package.verify_media(Path(actual['path']), package, upload=True)
    require(upload_export_v2.audio_digest(Path(actual['path'])) == upload_export_v2.audio_digest(Path(package['output']['path'])),
            '当前上传的中文AAC与母版不一致')
    return record


def install(pending, destination):
    Path(pending).replace(destination)


def restore_files(prepared, transaction_directory):
    work, output, manifest_path = paths(Path(prepared['work']))
    archived = prepared['archived']
    require(all(state.matches(identity) for identity in archived.values()), '回滚归档不完整，拒绝覆盖当前文件')
    installed = state.load(transaction_directory / 'installed-manifest.json')
    require(state.digest(output) in {prepared['original']['upload']['sha256'], installed['output']['sha256']}
            and state.digest(manifest_path) in {prepared['original']['upload_manifest']['sha256'],
                                               state.digest(transaction_directory / 'installed-manifest.json')},
            '当前文件存在事务之外的修改，拒绝自动覆盖')
    pending_video = output.with_name(f'.upload-rollback-{uuid.uuid4().hex}.mp4')
    pending_manifest = transaction_directory / f'rollback-{uuid.uuid4().hex}.json'
    try:
        os.link(archived['upload']['path'], pending_video)
        shutil.copy2(archived['upload_manifest']['path'], pending_manifest)
        install(pending_video, output)
        install(pending_manifest, manifest_path)
        require(all(state.matches(identity) for identity in prepared['original'].values()), '回滚后原文件身份不一致')
    finally:
        pending_video.unlink(missing_ok=True)
        pending_manifest.unlink(missing_ok=True)


def promote(work, directory, apply=False, database=None):
    work, output, manifest_path = paths(work)
    pending_transactions(work)
    current = validate_current(work, database=database)
    directory = Path(directory).resolve(strict=True)
    reference = state.identity(directory / 'candidate-result.json')
    if current['version'] == 2 and current['dependencies']['candidate_result'] == reference:
        return {'state': 'already_current', 'output': current['output'], 'applied': False}
    reviewed, _, reference = candidate(work, directory, database=database)
    plan = {'state': 'planned', 'work': str(work), 'candidate_result': reference,
            'previous_output': current['output'], 'candidate_output': reviewed['output'], 'applied': False}
    if not apply:
        return plan
    require(Path(reviewed['output']['path']).stat().st_dev == output.parent.stat().st_dev,
            '候选与交付不在同一文件系统，不能执行当前硬链接事务')
    transaction_directory = work / 'upload_revisions' / uuid.uuid4().hex
    transaction_directory.mkdir(parents=True)
    archived_video, archived_manifest = transaction_directory / 'previous.mp4', transaction_directory / 'previous-manifest.json'
    os.link(output, archived_video)
    shutil.copy2(manifest_path, archived_manifest)
    prepared = {'version': 2, 'created_at': state.now(), 'work': str(work), 'implementation': state.identity(__file__),
                'original': {'upload': current['output'], 'upload_manifest': state.identity(manifest_path)},
                'archived': {'upload': state.identity(archived_video), 'upload_manifest': state.identity(archived_manifest)},
                'candidate_result': reference, 'candidate_output': reviewed['output']}
    prepared_path = transaction_directory / 'prepared.json'
    save_json(prepared_path, prepared)
    updated = {'version': 2, 'created_at': state.now(),
               'dependencies': {'implementation': state.identity(__file__), 'prepared': state.identity(prepared_path),
                                'candidate_result': reference, 'quality_bindings': reviewed['quality']['bindings']},
               'output': {**reviewed['output'], 'path': str(output)},
               'profile': reviewed['profile'], 'verification': copy.deepcopy(reviewed['verification']),
               'quality': reviewed['quality'], 'human_listened': False, 'production_applied': True}
    installed_path = transaction_directory / 'installed-manifest.json'
    save_json(installed_path, updated)
    journal_path = transaction_directory / 'transaction.json'
    journal = {'version': 2, 'created_at': state.now(), 'state': 'prepared',
               'prepared': state.identity(prepared_path), 'installed_manifest': state.identity(installed_path)}
    save_json(journal_path, journal)
    pending_video = output.with_name(f'.upload-v2-{transaction_directory.name}.mp4')
    pending_manifest = transaction_directory / 'pending-manifest.json'
    touched = False
    try:
        os.link(reviewed['output']['path'], pending_video)
        shutil.copy2(installed_path, pending_manifest)
        candidate(work, directory, database=database)
        require(all(state.matches(identity) for identity in prepared['original'].values()), '晋升前原交付发生变化')
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
    except Exception as error:
        if touched:
            try:
                restore_files(prepared, transaction_directory)
            except Exception as recovery_error:
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
    work, output, manifest_path = paths(work)
    transaction = Path(transaction).resolve(strict=True)
    require(transaction.name == 'transaction.json' and transaction.parent.parent == work / 'upload_revisions',
            '回滚事务必须属于当前工程')
    pending_transactions(work, transaction)
    journal = state.load(transaction)
    require(state.matches(journal['prepared']) and state.matches(journal['installed_manifest']), '事务证据已变化')
    prepared = state.load(journal['prepared']['path'])
    require(prepared['work'] == str(work) and prepared['implementation'] == state.identity(__file__), '事务归属或实现不一致')
    original = prepared['original']
    require(all(equivalent(original[key], identity) for key, identity in prepared['archived'].items()),
            '回滚归档不等于原版本')
    if journal['state'] in {'rolled_back', 'automatic_rollback'}:
        require(all(state.matches(identity) for identity in original.values()), '已回滚状态与实际文件不一致')
        return {'state': 'already_rolled_back', 'output': original['upload'], 'applied': False}
    installed = state.load(journal['installed_manifest']['path'])
    require(state.digest(output) in {original['upload']['sha256'], installed['output']['sha256']}
            and state.digest(manifest_path) in {original['upload_manifest']['sha256'], journal['installed_manifest']['sha256']},
            '当前文件不是事务已知的新旧版本，拒绝覆盖未知修改')
    previous = state.load(prepared['archived']['upload_manifest']['path'])
    validate_current(work, previous, prepared['archived']['upload'], database)
    if not apply:
        return {'state': 'rollback_planned', 'output': original['upload'], 'applied': False, 'transaction': str(transaction)}
    restore_files(prepared, transaction.parent)
    journal.update(state='rolled_back', rollback_at=state.now())
    save_json(transaction, journal)
    return {'state': 'rolled_back', 'output': original['upload'], 'applied': True, 'transaction': str(transaction)}


def export(work, database=None, position='bottom', crf=20):
    work, _, manifest_path = paths(work)
    pending_transactions(work)
    if manifest_path.exists() and state.load(manifest_path).get('version') == 2:
        result = validate_current(work, database=database)
        require(result['profile']['position'] == position and result['profile']['crf'] == crf,
                '当前v2布局参数不同；需制作并审验新候选，不静默降级')
        return result
    return upload_export.export(work, database, position, crf)
