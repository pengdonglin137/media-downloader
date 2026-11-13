"""验证原v2上传及其两层归档；不改写版本、正式文件或旧验证器。"""

from pathlib import Path

import caption_layout
import dubbing_state as state
import paired_delivery_transaction as files
import paired_delivery_v5 as helpers
import upload_delivery
import upload_export
import upload_export_v2
import video_package


PROFILE = {'layout': 'registered-terms-v2', 'crf': 20, 'position': 'bottom',
           'audio': 'copy-current-chinese-aac', 'chapters': 'copy-master'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def module_identities():
    return {'source_validator': state.identity(__file__), **{name: state.identity(module.__file__) for name, module in {
        'state': state, 'file_transaction': files,
        'package_helpers': helpers, 'v2_dispatcher': upload_delivery, 'layout': caption_layout,
        'legacy_exporter': upload_export, 'v2_exporter': upload_export_v2,
        'package_verifier': video_package}.items()}}


def predecessor_routes(work, prepared):
    require(type(prepared['version']) is int and prepared['version'] == 2
        and prepared['work'] == str(work)
        and prepared['implementation'] == state.identity(upload_delivery.__file__)
        and set(prepared['original']) == set(prepared['archived']) == {'upload', 'upload_manifest'},
        '原v2事务的版本、工程、实现或归档范围不符')
    routes = []
    for name in ['upload', 'upload_manifest']:
        original, archived = prepared['original'][name], prepared['archived'][name]
        files.checked(archived)
        require(upload_delivery.equivalent(original, archived), '前任v1归档身份不符：' + name)
        routes.append((original, archived))

    def resolve(reference, fallback=helpers.current_reference):
        for original, archived in routes:
            if reference == original:
                files.checked(archived)
                return archived
        return fallback(reference)

    return resolve


def validate_record(record, original, bindings):
    require(type(record['version']) is int and record['version'] == 2
        and record['output'] == original['upload'] and record['profile'] == PROFILE
        and record['dependencies']['implementation'] == state.identity(upload_delivery.__file__)
        and record['dependencies']['quality_bindings'] == bindings
        and record['verification']['full_decode']['exit_code'] == 0,
        '只接受实际v2上传、原质量绑定和原始成功解码证据')


def validate(work, original, manifest_reference, bindings, resolve=helpers.current_reference):
    work = files.canonical(work)
    files.destinations(work, original)
    for reference in original.values():
        resolve(reference)
    base, rendering = helpers.base_package(work, original, manifest_reference, resolve)
    record = helpers.read_reference(original['upload_manifest'], resolve)
    validate_record(record, original, bindings)
    dependencies = record['dependencies']
    prepared_path = files.canonical(dependencies['prepared']['path'])
    require(prepared_path.name == 'prepared.json' and prepared_path.parent.parent == work / 'upload_revisions'
        and len(prepared_path.parent.name) == 32
        and all(character in '0123456789abcdef' for character in prepared_path.parent.name),
        '原v2事务不属于当前工程')
    prepared = helpers.read_reference(dependencies['prepared'], resolve)
    predecessor = predecessor_routes(work, prepared)
    require(all(prepared['original'][name]['path'] == original[name]['path']
        for name in ['upload', 'upload_manifest']), '前任归档不能映射其他正式路径')
    expected_archives = {'upload': 'previous.mp4', 'upload_manifest': 'previous-manifest.json'}
    require(all(prepared['archived'][name]['path'] == str(prepared_path.parent / filename)
        for name, filename in expected_archives.items()), '前任归档路径必须保持原事务布局')
    require(prepared['candidate_result'] == dependencies['candidate_result'], '原v2候选与事务来源不一致')
    candidate_reference = dependencies['candidate_result']
    candidate_path = files.canonical(candidate_reference['path'])
    require(candidate_path.name == 'candidate-result.json' and (
        candidate_path.parent.is_relative_to(work) or
        candidate_path.parent.is_relative_to(work.parent / '.workflow_logs')), '原v2候选不在隐藏工程范围')
    candidate = helpers.read_reference(candidate_reference, resolve)
    inputs = candidate['dependencies']
    expected_modules = {'implementation': caption_layout, 'legacy': upload_export,
        'candidate_exporter': upload_export_v2, 'package_implementation': video_package}
    require(type(candidate['version']) is int and candidate['version'] == 2
        and candidate['production_applied'] is False and candidate['current_delivery_unchanged'] is True
        and candidate['profile'] == PROFILE
        and all(inputs[name] == state.identity(module.__file__) for name, module in expected_modules.items())
        and upload_delivery.equivalent(candidate['output'], resolve(original['upload']))
        and candidate['verification'] == record['verification']
        and candidate['quality']['allowed'] is True and candidate['quality']['bindings'] == bindings,
        '实际v2候选的实现、上传输出、质量或解码绑定不符')
    combined = lambda reference: predecessor(reference, resolve)
    helpers.references(candidate, combined)
    require(all(inputs[name] == prepared['original'][name] for name in ['upload', 'upload_manifest'])
        and inputs['master'] == base['output'] and inputs['captions'] == base['subtitles']['zh'],
        '原v2候选没有保留实际前任和原母版/中文字幕')
    preflight = helpers.read_reference(inputs['preflight'], combined)
    helpers.references(preflight, combined)
    require(preflight['work'] == str(work) and preflight['report'] == candidate['layout_report'],
        '原v2字幕预检工程或布局变化')
    media = upload_export.probe(Path(resolve(original['master'])['path']))
    video = next(stream for stream in media['streams'] if stream['codec_type'] == 'video')
    script, layout = caption_layout.subtitles(work, int(video['width']), int(video['height']),
        float(media['format']['duration']), 'bottom', caption_layout.load_forms(inputs['terms']['path']),
        Path(base['subtitles']['zh']['path']))
    require(script == Path(inputs['ass']['path']).read_text() and layout == candidate['layout_report'],
        '原v2字幕ASS不能从原始输入精确重建')
    runtime = state.load(candidate_path.parent / 'candidate-vlc-result.json')
    helpers.references(runtime, combined)
    require(runtime['passed'] is True and runtime['candidate']['passed'] is True
        and runtime['candidate']['media'] == candidate['output']
        and runtime['candidate']['rendered_snapshots'], '原v2有限VLC运行证据失效')
    images = state.load(candidate_path.parent / 'review-image-inputs.json')
    visual = state.load(candidate_path.parent / 'actual-image-review.json')
    helpers.references(images, combined)
    helpers.references(visual, combined)
    changes = candidate['layout_report']['changes']
    require(images['candidate'] == candidate['output']
        and visual['candidate_sha256'] == candidate['output']['sha256']
        and visual['actually_viewed'] is True
        and len(images['images']) == len(visual['observations']) == len(changes)
        and {image['cue_id'] for image in images['images']} == {change['cue_id'] for change in changes}
            == {note['cue_id'] for note in visual['observations']}, '原v2变化字幕图审不完整')
    for image in images['images']:
        receipt = helpers.read_reference(image['receipt'], combined)
        require(receipt['source_sha256'] == candidate['output']['sha256']
            and receipt['sha256'] == image['image']['sha256'] and max(receipt['dimensions']) <= 1280
            and image['image']['bytes'] <= 192 * 1024
            and any(note['cue_id'] == image['cue_id'] and note['image_sha256'] == image['image']['sha256']
                for note in visual['observations']), '原v2图审未绑定原始受限图片')
    helpers.references(record['verification'], resolve)
    audio = [upload_export_v2.audio_digest(Path(reference['path'])) for reference in [
        resolve(original['master']), resolve(original['upload']), candidate['output']]]
    require(audio[0] == audio[1] == audio[2] == candidate['master_chinese_aac']
        == candidate['candidate_chinese_aac'] and candidate['chinese_aac_identical'] is True,
        '原v2正式上传、原候选和母版中文AAC不相同')
    video_package.verify_media(Path(resolve(original['upload'])['path']), base, upload=True)
    return {'source_upload_version': 2, 'record': record, 'base': base, 'rendering': rendering,
        'candidate_result': candidate_reference, 'candidate': candidate,
        'prior_upload_archives': prepared['archived'], 'chinese_aac_identical': True,
        'ass_exactly_reconstructed': True, 'original_vlc_evidence_revalidated': True,
        'fresh_full_decode_performed': False, 'production_applied': False}
