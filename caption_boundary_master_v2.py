"""识别MP4章节数据轨的跨条字幕母版候选，仅重封装，不晋升正式媒体。"""

import argparse
import fcntl
import subprocess
from pathlib import Path

import caption_boundary_master as previous
import caption_boundary_repair as repair
import caption_layout
import dubbing_state as state
import upload_export
import video_package
from prepare_videos import atomic_write, save_json


def layout(data):
    streams = data['streams']
    previous.require(len(streams) in {6, 7} and [stream['index'] for stream in streams] == list(range(len(streams)))
        and [stream['codec_type'] for stream in streams[:6]] == ['video', 'audio', 'audio', 'subtitle', 'subtitle', 'subtitle']
        and all(stream['codec_name'] == 'mov_text' for stream in streams[3:6]), '媒体核心流顺序或字幕格式未核对')
    extra = streams[6:]
    previous.require(not extra or (bool(data.get('chapters')) and extra[0]['codec_type'] == 'data'
        and extra[0]['codec_name'] == 'bin_data' and extra[0]['codec_tag_string'] == 'text'),
        '只允许已识别的MP4章节text数据轨，不忽略未知数据流')
    return {'media_stream_indices': list(range(6)), 'chapter_data_stream_indices': [stream['index'] for stream in extra],
        'preserved_packet_stream_indices': [0, 1, 2, 4],
        'chapter_data_policy': 'Remux logical chapters through map_chapters; verify all chapter times/titles. '
            'Chapter data packets are regenerated, not claimed byte-identical.'}


def build(work, preview_proof, directory):
    work, preview_proof = [Path(path).resolve(strict=True) for path in [work, preview_proof]]
    directory = Path(directory)
    previous.require(directory.is_absolute() and not directory.exists()
        and directory.parent.resolve(strict=True).is_relative_to(work.parent / '.workflow_logs'), '需要新的隐藏候选目录')
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        preview_reference = state.identity(preview_proof)
        preview = state.load(preview_proof)
        manifest = video_package.validate(work)
        previous.require(preview['kind'] == repair.KIND and preview['version'] == repair.VERSION
            and preview['work'] == str(work) and preview['preview_only'] is True
            and state.matches(preview['implementation']) and state.matches(preview['plan'])
            and all(state.matches(reference) for reference in preview['inputs'].values())
            and state.matches(preview['preview_captions']) and state.matches(preview['preview_ass'])
            and preview['inputs']['master'] == manifest['output']
            and preview['inputs']['captions'] == manifest['subtitles']['zh'], '文字预览或当前输入身份无效')
        plan = state.load(preview['plan']['path'])
        original = state.cues(manifest['subtitles']['zh']['path'])
        repaired, actual_proof = repair.repartition(original, plan['edits'],
            repair.contexts_for(work, manifest['body_offset_seconds']), caption_layout.load_forms(preview['inputs']['terms']['path']))
        previous.require(state.signature(actual_proof) == state.signature(preview['proof'])
            and repaired == state.cues(preview['preview_captions']['path']), '不能复现逐项文字审校')
        bilingual = previous.project_bilingual(original, repaired, state.cues(manifest['subtitles']['bilingual']['path']))
        source = Path(manifest['output']['path'])
        before = upload_export.probe(source)
        mapping = layout(before)
        directory.mkdir()
        chinese, dual = directory / 'zh.srt', directory / 'bilingual.srt'
        atomic_write(chinese, state.srt_text(repaired))
        atomic_write(dual, state.srt_text(bilingual))
        candidate = directory / 'master-caption-boundary-preview.mp4'
        command = ['ffmpeg', '-nostdin', '-v', 'error', '-copyts', '-i', str(source), '-i', str(chinese), '-i', str(dual),
            '-map', '0:v:0', '-map', '0:a:0', '-map', '0:a:1', '-map', '1:0', '-map', '0:s:1', '-map', '2:0',
            '-map_metadata', '0', '-map_chapters', '0', '-c', 'copy', '-c:s:0', 'mov_text', '-c:s:2', 'mov_text',
            '-avoid_negative_ts', 'disabled']
        for subtitle_index, stream in enumerate(before['streams'][3:6]):
            for key in ['language', 'title', 'handler_name']:
                if key in stream.get('tags', {}):
                    command += [f'-metadata:s:s:{subtitle_index}', f'{key}={stream["tags"][key]}']
            disposition = stream.get('disposition', {})
            previous.require(not any(value for key, value in disposition.items() if key not in {'default', 'forced'}),
                '不能丢弃未知字幕disposition')
            names = [key for key in ['default', 'forced'] if disposition.get(key)]
            command += [f'-disposition:s:{subtitle_index}', '+'.join(names) if names else '0']
        command += ['-movflags', '+faststart', str(candidate)]
        process = subprocess.run(command, cwd=directory, capture_output=True, text=True)
        log = directory / 'master-remux.log'
        atomic_write(log, process.stdout + process.stderr)
        previous.require(process.returncode == 0 and not process.stderr.strip(), '母版候选重封装失败')
        after = upload_export.probe(candidate)
        previous.require(layout(after) == mapping, '候选流与章节数据轨布局发生变化')
        for old_stream, new_stream in zip(before['streams'], after['streams']):
            for key in ['codec_type', 'codec_name', 'time_base', 'width', 'height', 'sample_rate', 'channels', 'disposition']:
                previous.require(old_stream.get(key) == new_stream.get(key), '流格式/默认选择变化: ' + key)
            for key in ['language', 'title', 'handler_name']:
                previous.require(old_stream.get('tags', {}).get(key) == new_stream.get('tags', {}).get(key), '流语言/名称变化')
        old_packets = previous.packet_inventory(source, mapping['preserved_packet_stream_indices'])
        new_packets = previous.packet_inventory(candidate, mapping['preserved_packet_stream_indices'])
        previous.require(old_packets == new_packets, '原画/双音轨/英文字幕的编码包或PTS/DTS变化')
        expected = dict(manifest)
        expected['subtitles'] = {'zh': state.identity(chinese), 'en': manifest['subtitles']['en'], 'bilingual': state.identity(dual)}
        media_verification = video_package.verify_media(candidate, expected)
        previous.require(before['chapters'] == after['chapters'], '内嵌章节时间、标题或元数据变化')
        previous.require(all(state.matches(reference) for reference in preview['inputs'].values())
            and state.matches(preview_reference) and video_package.validate(work) == manifest, '正式输入或媒体发生变化')
        result = {'version': 4, 'producer_revision': 2, 'kind': 'caption-boundary-master-candidate-v4',
            'checked_at': state.now(), 'work': str(work), 'implementation': state.identity(__file__),
            'reused_helpers': state.identity(previous.__file__), 'text_preview': preview_reference,
            'input_package_manifest': preview['inputs']['inner_package_manifest'],
            'original_master': manifest['output'], 'output': state.identity(candidate), 'subtitles': expected['subtitles'],
            'media_verification': media_verification, 'stream_mapping': mapping,
            'preserved_encoded_stream_packets': old_packets, 'all_logical_chapters_identical': True,
            'chapter_data_packets_claimed_identical': False, 'command': command, 'log': state.identity(log),
            'reused_package_verifier': state.identity(video_package.__file__), 'english_in_bilingual_preserved': True,
            'video_encoded': False, 'audio_encoded': False, 'tts_performed': False, 'candidate_only': True,
            'production_applied': False, 'upload_candidate_generated': False, 'word_alignment_verified': False,
            'human_listened': False, 'goal_status': 'active'}
        save_json(directory / 'candidate-result.json', result)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--text-preview', required=True, type=Path)
    parser.add_argument('--candidate-directory', required=True, type=Path)
    arguments = parser.parse_args()
    result = build(arguments.work, arguments.text_preview, arguments.candidate_directory)
    print('MASTER CANDIDATE VERIFIED', result['output']['path'], '; formal media unchanged')
