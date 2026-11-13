"""既有语义组显示域v3的母版候选；只重封装字幕，不替换正式双版本。"""

import argparse
import fcntl
import subprocess
from pathlib import Path

import caption_boundary_master as packet_helpers
import caption_boundary_master_v2 as stream_helpers
import caption_boundary_repair_v3 as repair
import dubbing_state as state
import prepare_review_image
import upload_export
import video_package
from prepare_videos import atomic_write, save_json


VERSION = 6
KIND = 'caption-boundary-master-candidate-v6'
require = repair.require


def implementations():
    return {module.__name__: state.identity(module.__file__) for module in
            [packet_helpers, stream_helpers, repair, state, prepare_review_image, upload_export, video_package]}


def validate_reviews(verified, text_validation, visual_review):
    text_reference, visual_reference = [state.identity(path) for path in [text_validation, visual_review]]
    text, visual = state.load(text_validation), state.load(visual_review)
    preview_reference = verified['preview_reference']
    expected_cues = verified['preview']['proof']['changed_cue_ids']
    require(text['passed'] is True and text['preview'] == preview_reference
            and text['cue_count'] == len(verified['repaired'])
            and text['all_cue_raster']['passed'] is True
            and text['all_cue_raster']['cues_rasterized'] == text['cue_count']
            and text['changed_cues'] == expected_cues
            and state.matches(text['implementation']), '需要完整逐字幕文字及栅格验证')
    raster = text['all_cue_raster']
    require(all(state.matches(raster[key]) for key in ['ass', 'metadata', 'log']), '栅格证据失效')
    require(visual['kind'] == 'caption-boundary-ass-overlay-review-v1' and visual['passed'] is True
            and visual['preview'] == preview_reference and state.matches(visual['prepared_images'])
            and visual['human_listened'] is False and visual['production_applied'] is False,
            '需要真实逐张ASS预览图审，不接受听辨或正式验收声明')
    prepared = state.load(visual['prepared_images']['path'])
    require(prepared['preview'] == preview_reference and len(visual['images']) == len(prepared['images'])
            and [entry['cue_id'] for entry in prepared['images'] if entry['kind'] == 'caption-band'] == expected_cues,
            '每条改动字幕均需独立字幕带图审')
    for observed, source in zip(visual['images'], prepared['images']):
        require(observed['source'] == source and observed['agent_viewed'] is True
                and observed['passed'] is True and isinstance(observed['observation'], str)
                and bool(observed['observation'].strip()) and source['actual_encoded_upload_frame'] is False
                and state.matches(source['image']) and state.matches(source['preparation'])
                and source['overlay_ass'] == verified['preview']['preview_ass'], '图审来源或逐张观察失效')
        image = Path(source['image']['path'])
        with image.open('rb') as handle:
            magic = handle.read(3)
        metadata = state.load(source['preparation']['path'])
        dimensions = list(prepare_review_image.probe(image))
        require(image.suffix.lower() in {'.jpg', '.jpeg'} and magic == b'\xff\xd8\xff'
                and 0 < source['image']['bytes'] == metadata['bytes'] <= 196608
                and dimensions == metadata['dimensions'] and max(dimensions) <= 1280
                and metadata['sha256'] == source['image']['sha256'], 'JPEG尺寸、字节或实际身份不合格')
    require(state.matches(text_reference) and state.matches(visual_reference), '审读期间证据变化')
    return {'text_validation': text_reference, 'visual_review': visual_reference}


def build(work, preview_proof, text_validation, visual_review, directory):
    work, preview_proof, text_validation, visual_review = [Path(path).resolve(strict=True)
        for path in [work, preview_proof, text_validation, visual_review]]
    directory = Path(directory)
    require(directory.is_absolute() and not directory.exists()
            and directory.parent.resolve(strict=True).is_relative_to(work.parent / '.workflow_logs'),
            '需要新的隐藏母版候选目录')
    implementation, helpers = state.identity(__file__), implementations()
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        verified = repair.verify_preview(work, preview_proof)
        reviews = validate_reviews(verified, text_validation, visual_review)
        preview, manifest = verified['preview'], verified['manifest']
        original, repaired = verified['original'], verified['repaired']
        bilingual = packet_helpers.project_bilingual(original, repaired,
            state.cues(manifest['subtitles']['bilingual']['path']))
        source = Path(manifest['output']['path'])
        before = upload_export.probe(source)
        mapping = stream_helpers.layout(before)
        directory.mkdir()
        chinese, dual = directory / 'zh.srt', directory / 'bilingual.srt'
        atomic_write(chinese, state.srt_text(repaired))
        atomic_write(dual, state.srt_text(bilingual))
        require(state.cues(chinese) == repaired and state.cues(dual) == bilingual, '字幕时间或文字变化')
        candidate = directory / 'master-caption-boundary-preview.mp4'
        command = ['ffmpeg', '-nostdin', '-n', '-v', 'error', '-copyts', '-i', str(source),
            '-i', str(chinese), '-i', str(dual), '-map', '0:v:0', '-map', '0:a:0', '-map', '0:a:1',
            '-map', '1:0', '-map', '0:s:1', '-map', '2:0', '-map_metadata', '0', '-map_chapters', '0',
            '-c', 'copy', '-c:s:0', 'mov_text', '-c:s:2', 'mov_text', '-avoid_negative_ts', 'disabled']
        for subtitle_index, stream in enumerate(before['streams'][3:6]):
            for key in ['language', 'title', 'handler_name']:
                if key in stream.get('tags', {}):
                    command += [f'-metadata:s:s:{subtitle_index}', f'{key}={stream["tags"][key]}']
            disposition = stream.get('disposition', {})
            require(not any(value for key, value in disposition.items() if key not in {'default', 'forced'}),
                    '不能丢弃未知字幕disposition')
            names = [key for key in ['default', 'forced'] if disposition.get(key)]
            command += [f'-disposition:s:{subtitle_index}', '+'.join(names) if names else '0']
        command += ['-movflags', '+faststart', str(candidate)]
        process = subprocess.run(command, cwd=directory, capture_output=True, text=True)
        log = directory / 'master-remux.log'
        atomic_write(log, process.stdout + process.stderr)
        save_json(directory / 'master-remux-exit.json', {'returncode': process.returncode,
            'command': command, 'log': state.identity(log)})
        require(process.returncode == 0 and not process.stderr.strip(), '重封装失败，保留原日志及出口')
        after = upload_export.probe(candidate)
        require(stream_helpers.layout(after) == mapping, '流或章节数据轨布局变化')
        for old_stream, new_stream in zip(before['streams'], after['streams']):
            for key in ['codec_type', 'codec_name', 'time_base', 'width', 'height', 'sample_rate', 'channels', 'disposition']:
                require(old_stream.get(key) == new_stream.get(key), '流格式或默认选择变化: ' + key)
            for key in ['language', 'title', 'handler_name']:
                require(old_stream.get('tags', {}).get(key) == new_stream.get('tags', {}).get(key),
                        '流语言或名称变化: ' + key)
        old_packets = packet_helpers.packet_inventory(source, mapping['preserved_packet_stream_indices'])
        new_packets = packet_helpers.packet_inventory(candidate, mapping['preserved_packet_stream_indices'])
        require(old_packets == new_packets, '原画、双音轨或英文字幕包/PTS/DTS变化')
        expected = dict(manifest)
        expected['subtitles'] = {'zh': state.identity(chinese), 'en': manifest['subtitles']['en'],
                                 'bilingual': state.identity(dual)}
        media_verification = video_package.verify_media(candidate, expected)
        require(before['chapters'] == after['chapters'], '逻辑章节时间或标题变化')
        require(all(state.matches(reference) for reference in preview['inputs'].values())
                and state.matches(verified['preview_reference']) and state.matches(implementation)
                and all(state.matches(reference) for reference in reviews.values())
                and helpers == implementations() and video_package.validate(work) == manifest,
                '生成候选期间正式输入、预览或实现变化')
        result = {'version': VERSION, 'producer_revision': 4, 'kind': KIND, 'checked_at': state.now(),
            'work': str(work), 'implementation': implementation, 'reused_implementations': helpers,
            'text_preview': verified['preview_reference'], 'review_evidence': reviews,
            'input_package_manifest': preview['inputs']['inner_package_manifest'],
            'original_master': manifest['output'], 'output': state.identity(candidate),
            'subtitles': expected['subtitles'], 'media_verification': media_verification,
            'stream_mapping': mapping, 'preserved_encoded_stream_packets': old_packets,
            'all_logical_chapters_identical': True, 'chapter_data_packets_claimed_identical': False,
            'command': command, 'log': state.identity(log), 'exit': state.identity(directory / 'master-remux-exit.json'),
            'english_in_bilingual_preserved': True, 'audio_windows_changed': False,
            'video_encoded': False, 'audio_encoded': False, 'tts_performed': False, 'candidate_only': True,
            'production_applied': False, 'upload_candidate_generated': False, 'fresh_full_decode_performed': False,
            'word_alignment_verified': False, 'human_listened': False, 'goal_status': 'active'}
        save_json(directory / 'candidate-result.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--text-preview', required=True, type=Path)
    parser.add_argument('--text-validation', required=True, type=Path)
    parser.add_argument('--visual-review', required=True, type=Path)
    parser.add_argument('--candidate-directory', required=True, type=Path)
    arguments = parser.parse_args()
    result = build(arguments.work, arguments.text_preview, arguments.text_validation,
                   arguments.visual_review, arguments.candidate_directory)
    print('MASTER V6 CANDIDATE VERIFIED', result['output']['path'], '; formal media unchanged')


if __name__ == '__main__':
    main()
