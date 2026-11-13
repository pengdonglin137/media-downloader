"""真实preview4的独立v7母版候选：保留双组、只重封装中文字幕。"""

import argparse
import fcntl
import os
import re
import subprocess
from pathlib import Path

import caption_boundary_master as packet_helpers
import caption_boundary_master_v2 as stream_helpers
import caption_boundary_proof_v2 as validation
import dubbing_state as state
import prepare_review_image
import upload_export
import video_package
from prepare_videos import atomic_write, save_json


VERSION = 7
PRODUCER_REVISION = 1
KIND = 'caption-boundary-master-candidate-v7'
require = validation.require


def implementations():
    return {**validation.implementations(), **{module.__name__: state.identity(module.__file__) for module in
        [packet_helpers, stream_helpers, validation, state, prepare_review_image, upload_export, video_package]}}


def validate_raster(preview, raster):
    count, geometry = preview['proof']['caption_count'], preview['layout']['geometry']
    require(raster['passed'] is True and raster['all_cues_inside_added_band'] is True
        and type(raster['cues_rasterized']) is int and raster['cues_rasterized'] == count
        and all(state.matches(raster[key]) for key in ['ass', 'metadata', 'log']), '必须复用完整实际栅格收据')
    header, first = Path(preview['preview_ass']['path']).read_text().split('Dialogue: ', 1)
    original = ['Dialogue: ' + first.splitlines()[0],
        *[line for line in first.splitlines()[1:] if line.startswith('Dialogue: ')]]
    rows = []
    for index, line in enumerate(original):
        fields = line.split(',', 9)
        fields[1:3] = [upload_export.ass_time(index), upload_export.ass_time(index + 1)]
        rows.append(','.join(fields) + '\n')
    require(len(rows) == count and Path(raster['ass']['path']).read_text() == header + ''.join(rows)
        and not Path(raster['log']['path']).read_text(), '栅格脚本或真实日志不属于全部当前字幕')
    bounds, current = [], None
    for line in Path(raster['metadata']['path']).read_text().splitlines():
        if line.startswith('frame:'):
            current = {'frame': int(re.match(r'frame:(\d+)', line)[1])}
            bounds.append(current)
        elif line.startswith('lavfi.bbox.') and current is not None:
            key, value = line.split('=', 1)
            current[key.removeprefix('lavfi.bbox.')] = int(value)
    require(validation.strict_equal(bounds, raster['bounds']) and len(bounds) == count
        and [entry['frame'] for entry in bounds] == list(range(count)), '全部实际栅格元数据必须逐项重建')
    guard = geometry['outline'] + 4
    require(raster['guard_pixels'] == guard, '安全带guard必须来自实际布局')
    for entry in bounds:
        require(entry.get('w', 0) > 0 and entry.get('h', 0) > 0 and entry.get('x1', -1) >= guard
            and entry.get('x2', geometry['output_width']) < geometry['output_width'] - guard
            and entry.get('y1', -1) >= geometry['source_height'] + guard
            and entry.get('y2', geometry['output_height']) < geometry['output_height'] - guard, '实际字形越出新增黑带')


def validate_reviews(verified, text_path, visual_path):
    text_reference, visual_reference = [state.identity(path) for path in [text_path, visual_path]]
    text, visual = state.load(text_path), state.load(visual_path)
    preview, reference = verified['preview'], verified['preview_reference']
    count, changed = len(verified['repaired']), preview['proof']['changed_cue_ids']
    require(text['passed'] is True and text['preview'] == reference and text['cue_count'] == count
        and text['changed_cues'] == changed and text['all_ass_text_and_registered_intra_cue_terms_checked'] is True
        and text['source_windows_and_display_gap_unchanged'] is True
        and text['word_alignment_verified'] is False and text['human_listened'] is False
        and text['production_applied'] is False and state.matches(text['implementation']), '缺少完整文字、原窗和实际栅格审校')
    validate_raster(preview, text['all_cue_raster'])
    require(visual['passed'] is True and visual['source_review'] == preview['inputs']['agent_source_review']
        and state.matches(visual['prepared_overlay_images']) and state.matches(visual['implementation'])
        and visual['human_listened'] is False and visual['word_alignment_verified'] is False
        and visual['production_applied'] is False and visual['actual_encoded_upload_frames_viewed'] == 0
        and visual['all_preview_subtitles_inside_added_band'] is True, '必须是真实ASS预览图审，而非编码帧或听辨声明')
    prepared = state.load(visual['prepared_overlay_images']['path'])
    images = visual['images_actually_viewed']
    require(prepared['preview'] == reference and len(images) == len(prepared['images']) == 2 * len(changed)
        and {(entry['cue_id'], entry['kind']) for entry in prepared['images']}
            == {(identifier, kind) for identifier in changed for kind in ['full-frame', 'caption-band']},
        '每条修订都需要真实全幅和字幕带两张不同的审图')
    for observed, source in zip(images, prepared['images']):
        require(validation.strict_equal({key: observed[key] for key in source}, source)
            and observed['agent_viewed'] is True and type(observed['note']) is str and bool(observed['note'].strip())
            and source['actual_encoded_upload_frame'] is False and source['source_master'] == preview['inputs']['master']
            and source['overlay_ass'] == preview['preview_ass']
            and all(state.matches(source[key]) for key in ['image', 'preparation', 'raw_frame', 'log']), '逐张来源、实际观察或ASS身份不符')
        image = Path(source['image']['path'])
        metadata = state.load(source['preparation']['path'])
        dimensions = list(prepare_review_image.probe(image))
        with image.open('rb') as handle:
            magic = handle.read(3)
        require(magic == b'\xff\xd8\xff' and 0 < source['image']['bytes'] == metadata['bytes'] <= 98304
            and dimensions == metadata['dimensions'] == observed['dimensions'] and max(dimensions) <= 1280
            and metadata['sha256'] == source['image']['sha256']
            and metadata['source_sha256'] == source['raw_frame']['sha256'], '受限JPEG及原始叠加来源必须精确一致')
    source_review = state.load(preview['inputs']['agent_source_review']['path'])
    original_images = source_review['source_images_reviewed']
    require(visual['overlay_images_actually_viewed'] == len(images)
        and visual['source_images_actually_viewed'] == len(original_images)
        and visual['eight_images_total_bytes'] == sum(entry['image']['bytes'] for entry in original_images + images)
        and sum(entry['image']['bytes'] for entry in images) <= len(images) * 98304
        and state.matches(text_reference) and state.matches(visual_reference), '实际图审数量、累计载荷或收据变化')
    return {'text_validation': text_reference, 'visual_review': visual_reference,
        'all_existing_raster_metadata_reparsed': True, 'new_raster_or_image_render_performed': False}


def command_for(source, chinese, bilingual, candidate, before):
    stream_helpers.layout(before)
    command = ['ffmpeg', '-nostdin', '-n', '-v', 'error', '-copyts', '-i', str(source),
        '-i', str(chinese), '-i', str(bilingual), '-map', '0:v:0', '-map', '0:a:0', '-map', '0:a:1',
        '-map', '1:0', '-map', '0:s:1', '-map', '2:0', '-map_metadata', '0', '-map_chapters', '0',
        '-c', 'copy', '-c:s:0', 'mov_text', '-c:s:2', 'mov_text', '-avoid_negative_ts', 'disabled']
    for index, stream in enumerate(before['streams'][3:6]):
        for key in ['language', 'title', 'handler_name']:
            if key in stream.get('tags', {}):
                command += [f'-metadata:s:s:{index}', f'{key}={stream["tags"][key]}']
        disposition = stream.get('disposition', {})
        require(not any(value for key, value in disposition.items() if key not in {'default', 'forced'}),
            '不能丢弃未知字幕disposition')
        names = [key for key in ['default', 'forced'] if disposition.get(key)]
        command += [f'-disposition:s:{index}', '+'.join(names) if names else '0']
    return command + ['-movflags', '+faststart', str(candidate)]


def verify_streams(before, after):
    mapping = stream_helpers.layout(before)
    require(stream_helpers.layout(after) == mapping, '核心流或显式章节辅助流布局改变')
    for original_stream, candidate_stream in zip(before['streams'][:6], after['streams'][:6]):
        for key in ['index', 'codec_type', 'codec_name', 'width', 'height', 'pix_fmt', 'time_base',
                'sample_rate', 'channels', 'disposition']:
            require(original_stream.get(key) == candidate_stream.get(key), '核心流格式、时间基或默认选择变化: ' + key)
        for key in ['language', 'title', 'handler_name']:
            require(original_stream.get('tags', {}).get(key) == candidate_stream.get('tags', {}).get(key), '流语言或标题变化: ' + key)
    require(before['chapters'] == after['chapters'], '全部逻辑章节、时间或元数据变化')
    return mapping


def build(work, preview_proof, text_validation, visual_review, directory):
    work, preview_proof, text_validation, visual_review = [Path(path).resolve(strict=True)
        for path in [work, preview_proof, text_validation, visual_review]]
    directory = Path(directory)
    require(directory.is_absolute() and not directory.exists()
        and directory.parent.resolve(strict=True).is_relative_to(work.parent / '.workflow_logs'), '只允许新的隐藏候选目录')
    implementation, helpers = state.identity(__file__), implementations()
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        verified = validation.verify_preview(work, preview_proof)
        preview, manifest = verified['preview'], verified['manifest']
        reviews = validate_reviews(verified, text_validation, visual_review)
        source_upload = state.load(work / 'upload_export.json')
        require(type(manifest['version']) is int and manifest['version'] == 1
            and type(source_upload['version']) is int and source_upload['version'] == 2
            and source_upload['output'] == preview['inputs']['upload'], '此入口明确要求实际内层package1/正式upload2来源')
        original, repaired = verified['original'], verified['repaired']
        bilingual = packet_helpers.project_bilingual(original, repaired, state.cues(manifest['subtitles']['bilingual']['path']))
        source = Path(manifest['output']['path'])
        before = upload_export.probe(source)
        mapping = stream_helpers.layout(before)
        chinese, dual = directory / 'zh.srt', directory / 'bilingual.srt'
        candidate = directory / 'master-caption-boundary-preview.mp4'
        command = command_for(source, chinese, dual, candidate, before)
        directory.mkdir()
        atomic_write(chinese, state.srt_text(repaired))
        atomic_write(dual, state.srt_text(bilingual))
        require(state.cues(chinese) == repaired and state.cues(dual) == bilingual, '新字幕序列化改变原字符或时间')
        live_path = directory / 'candidate-live-state.json'
        live = {'version': VERSION, 'producer_revision': PRODUCER_REVISION, 'owner_pid': os.getpid(),
            'work': str(work), 'state': 'remux-starting', 'source_preview_version': 4, 'production_applied': False}
        save_json(live_path, live)
        try:
            process = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            started = directory / 'master-remux-start.json'
            save_json(started, {'checked_at': state.now(), 'child_pid': process.pid, 'parent_pid': os.getpid(),
                'command': command, 'implementation': implementation, 'source_master': manifest['output'],
                'text_preview': verified['preview_reference'], 'production_applied': False})
            live.update(state='remux-running', child_pid=process.pid)
            save_json(live_path, live)
            stdout, stderr = process.communicate()
            log = directory / 'master-remux.log'
            atomic_write(log, stdout + stderr)
            terminal = directory / 'master-remux-exit.json'
            save_json(terminal, {'checked_at': state.now(), 'returncode': process.returncode, 'command': command,
                'child_pid': process.pid, 'started': state.identity(started), 'log': state.identity(log)})
            live.update(state='remux-terminal', remux_exit_code=process.returncode)
            save_json(live_path, live)
            require(process.returncode == 0 and not stderr.strip(), '原生字幕重封装未成功，不覆盖或重试原目录')
            after = upload_export.probe(candidate)
            require(verify_streams(before, after) == mapping, '独立流布局验证不符')
            live.update(state='checking-preserved-packets')
            save_json(live_path, live)
            old_packets = packet_helpers.packet_inventory(source, mapping['preserved_packet_stream_indices'])
            new_packets = packet_helpers.packet_inventory(candidate, mapping['preserved_packet_stream_indices'])
            require(old_packets == new_packets, '原视频、双音轨或英文字幕有序编码包/PTS/DTS变化')
            expected = {**manifest, 'subtitles': {'zh': state.identity(chinese), 'en': manifest['subtitles']['en'], 'bilingual': state.identity(dual)}}
            media = video_package.verify_media(candidate, expected)
            require(validation.verify_preview(work, preview_proof)['preview_reference'] == verified['preview_reference']
                and validate_reviews(verified, text_validation, visual_review) == reviews
                and all(state.matches(reference) for reference in preview['inputs'].values())
                and state.matches(implementation) and helpers == implementations() and video_package.validate(work) == manifest,
                '候选生成期间正式输入、原审校或实现变化')
            result = {'version': VERSION, 'producer_revision': PRODUCER_REVISION, 'kind': KIND, 'checked_at': state.now(),
                'work': str(work), 'implementation': implementation, 'reused_implementations': helpers,
                'source_package_format_version': 1, 'source_upload_version': 2, 'source_preview_version': 4,
                'text_preview': verified['preview_reference'], 'proof_validation': verified['proof_validation'],
                'required_review_receipts': reviews, 'distinct_original_synthesis_contexts': preview['proof']['distinct_original_synthesis_contexts'],
                'input_package_manifest': preview['inputs']['inner_package_manifest'], 'original_master': manifest['output'],
                'output': state.identity(candidate), 'subtitles': expected['subtitles'], 'media_verification': media,
                'stream_mapping': mapping, 'preserved_encoded_stream_packets': old_packets,
                'all_logical_chapters_identical': True, 'chapter_data_packets_claimed_identical': False,
                'command': command, 'start': state.identity(started), 'log': state.identity(log), 'exit': state.identity(terminal),
                'english_in_bilingual_preserved': True, 'source_full_decode_evidence_retained': manifest['full_decode'],
                'new_complete_av_decode_performed': False, 'audio_windows_changed': False, 'video_encoded': False,
                'audio_encoded': False, 'tts_performed': False, 'new_synthesis_groups_authorized': False,
                'new_roles_registered': False, 'candidate_only': True, 'production_applied': False,
                'upload_candidate_generated': False, 'word_alignment_verified': False, 'human_listened': False, 'goal_status': 'active'}
            save_json(directory / 'candidate-result.json', result)
            live.update(state='candidate-complete', result=state.identity(directory / 'candidate-result.json'))
            save_json(live_path, live)
            return result
        except BaseException as error:
            live.update(state='failed', error=f'{type(error).__name__}: {error}')
            save_json(live_path, live)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['work', 'text-preview', 'text-validation', 'visual-review', 'candidate-directory']:
        parser.add_argument('--' + name, required=True, type=Path)
    arguments = parser.parse_args()
    result = build(arguments.work, arguments.text_preview, arguments.text_validation, arguments.visual_review, arguments.candidate_directory)
    print('MASTER CANDIDATE v7/revision1 VERIFIED;', result['output']['path'], '; ORIGINAL A/V PACKETS PRESERVED;FORMAL UNCHANGED')


if __name__ == '__main__':
    main()
