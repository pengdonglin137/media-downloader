"""为已审校的跨条文字预览生成内嵌字幕母版候选，不替换正式文件。"""

import argparse
import fcntl
import json
import subprocess
from pathlib import Path

import caption_boundary_repair
import dubbing_state as state
import upload_export
import video_package
from prepare_videos import atomic_write, save_json


def require(condition, message):
    if not condition:
        raise ValueError(message)


def project_bilingual(original, repaired, bilingual):
    require(len(original) == len(repaired) == len(bilingual) and bool(original), '双语与中文字幕条数不一致')
    result = []
    for number, (before, after, dual) in enumerate(zip(original, repaired, bilingual), 1):
        require(before[:2] == after[:2] == dual[:2] and dual[2].startswith(before[2] + '\n'),
                f'双语字幕{number}时间或中文前缀与原中文字幕不一致')
        english = dual[2][len(before[2]) + 1:]
        require(bool(english.strip()), f'双语字幕{number}缺少原文')
        result.append((after[0], after[1], after[2] + '\n' + english))
    require(''.join(cue[2] for cue in original) == ''.join(cue[2] for cue in repaired), '中文完整字词序列不守恒')
    return result


def packet_inventory(path, stream_indices):
    command = ['ffprobe', '-v', 'error', '-show_packets', '-show_data_hash', 'sha256',
        '-show_entries', 'packet=stream_index,pts,dts,duration,size,flags,data_hash', '-of', 'json', str(path)]
    process = subprocess.run(command, capture_output=True, text=True, check=True)
    require(not process.stderr.strip(), '编码包身份检查有错误输出')
    streams = {index: [] for index in stream_indices}
    for packet in json.loads(process.stdout)['packets']:
        index = packet['stream_index']
        if index in streams:
            require('data_hash' in packet, '编码包缺少实际载荷哈希')
            streams[index].append({key: value for key, value in packet.items() if key != 'stream_index'})
    require(all(streams.values()), '某个应保留流没有编码包')
    return {str(index): {'packets': len(packets), 'ordered_packet_signature': state.signature(packets)}
            for index, packets in streams.items()}


def build(work, preview_proof, directory):
    work, preview_proof = [Path(path).resolve(strict=True) for path in [work, preview_proof]]
    directory = Path(directory)
    require(directory.is_absolute() and not directory.exists()
            and directory.parent.resolve(strict=True).is_relative_to(work.parent / '.workflow_logs'),
            '母版候选必须使用全新的隐藏工作流目录')
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        preview_reference = state.identity(preview_proof)
        preview = state.load(preview_proof)
        manifest = video_package.validate(work)
        require(preview['kind'] == caption_boundary_repair.KIND and preview['version'] == caption_boundary_repair.VERSION
                and preview['work'] == str(work) and preview['preview_only'] is True
                and state.matches(preview['implementation']) and state.matches(preview['plan'])
                and all(state.matches(reference) for reference in preview['inputs'].values())
                and state.matches(preview['preview_captions']) and state.matches(preview['preview_ass'])
                and preview['inputs']['master'] == manifest['output']
                and preview['inputs']['captions'] == manifest['subtitles']['zh'], '文字预览或当前母版身份无效')
        plan = state.load(preview['plan']['path'])
        original = state.cues(manifest['subtitles']['zh']['path'])
        forms = caption_boundary_repair.caption_layout.load_forms(preview['inputs']['terms']['path'])
        repaired, actual_proof = caption_boundary_repair.repartition(original, plan['edits'],
            caption_boundary_repair.contexts_for(work, manifest['body_offset_seconds']), forms)
        require(state.signature(actual_proof) == state.signature(preview['proof'])
                and repaired == state.cues(preview['preview_captions']['path']), '不能复现已审校文字预览')
        bilingual = project_bilingual(original, repaired, state.cues(manifest['subtitles']['bilingual']['path']))
        source = Path(manifest['output']['path'])
        before = upload_export.probe(source)
        streams = before['streams']
        require([stream['codec_type'] for stream in streams] == ['video', 'audio', 'audio', 'subtitle', 'subtitle', 'subtitle'],
                '当前候选只接受已核对的一视频/双音轨/三字幕顺序，不猜测流映射')
        directory.mkdir()
        chinese_path, bilingual_path = directory / 'zh.srt', directory / 'bilingual.srt'
        atomic_write(chinese_path, state.srt_text(repaired))
        atomic_write(bilingual_path, state.srt_text(bilingual))
        candidate = directory / 'master-caption-boundary-preview.mp4'
        command = ['ffmpeg', '-nostdin', '-v', 'error', '-copyts', '-i', str(source), '-i', str(chinese_path),
            '-i', str(bilingual_path), '-map', '0:v:0', '-map', '0:a:0', '-map', '0:a:1',
            '-map', '1:0', '-map', '0:s:1', '-map', '2:0', '-map_metadata', '0', '-map_chapters', '0',
            '-c', 'copy', '-c:s:0', 'mov_text', '-c:s:2', 'mov_text', '-avoid_negative_ts', 'disabled']
        for subtitle_index, stream in enumerate(streams[3:]):
            for key in ['language', 'title', 'handler_name']:
                if key in stream.get('tags', {}):
                    command += [f'-metadata:s:s:{subtitle_index}', f'{key}={stream["tags"][key]}']
            disposition = stream.get('disposition', {})
            require(not any(value for key, value in disposition.items() if key not in {'default', 'forced'}),
                    '不静默丢弃未知字幕disposition')
            names = [key for key in ['default', 'forced'] if disposition.get(key)]
            command += [f'-disposition:s:{subtitle_index}', '+'.join(names) if names else '0']
        command += ['-movflags', '+faststart', str(candidate)]
        process = subprocess.run(command, cwd=directory, capture_output=True, text=True)
        log = directory / 'master-remux.log'
        atomic_write(log, process.stdout + process.stderr)
        require(process.returncode == 0 and not process.stderr.strip(), '母版字幕重封装失败，不替换正式文件')
        after = upload_export.probe(candidate)
        require(len(after['streams']) == len(streams), '候选流数量变化')
        for old_stream, new_stream in zip(streams, after['streams']):
            for key in ['codec_type', 'codec_name', 'time_base', 'width', 'height', 'sample_rate', 'channels', 'disposition']:
                require(old_stream.get(key) == new_stream.get(key), '流格式或默认选择变化: ' + key)
            for key in ['language', 'title', 'handler_name']:
                require(old_stream.get('tags', {}).get(key) == new_stream.get('tags', {}).get(key), '流语言/名称变化')
        preserved_indices = [0, 1, 2, 4]
        old_packets = packet_inventory(source, preserved_indices)
        new_packets = packet_inventory(candidate, preserved_indices)
        require(old_packets == new_packets, '编码视频/双音轨/英文字幕的载荷、顺序或PTS/DTS变化')
        expected = dict(manifest)
        expected['subtitles'] = {'zh': state.identity(chinese_path), 'en': manifest['subtitles']['en'],
                                 'bilingual': state.identity(bilingual_path)}
        media_verification = video_package.verify_media(candidate, expected)
        require(all(state.matches(reference) for reference in preview['inputs'].values())
                and state.matches(preview_reference) and video_package.validate(work) == manifest,
                '候选生成期间正式媒体或输入变化')
        result = {'version': 4, 'kind': 'caption-boundary-master-candidate-v4', 'checked_at': state.now(),
            'work': str(work), 'implementation': state.identity(__file__), 'text_preview': preview_reference,
            'input_package_manifest': preview['inputs']['inner_package_manifest'],
            'original_master': manifest['output'], 'output': state.identity(candidate),
            'subtitles': expected['subtitles'], 'media_verification': media_verification,
            'preserved_encoded_stream_packets': old_packets, 'command': command, 'log': state.identity(log),
            'reused_package_verifier': state.identity(video_package.__file__),
            'english_in_bilingual_preserved': True, 'video_encoded': False, 'audio_encoded': False,
            'tts_performed': False, 'candidate_only': True, 'production_applied': False,
            'upload_candidate_generated': False, 'word_alignment_verified': False, 'human_listened': False,
            'goal_status': 'active'}
        save_json(directory / 'candidate-result.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--text-preview', required=True, type=Path)
    parser.add_argument('--candidate-directory', required=True, type=Path)
    arguments = parser.parse_args()
    result = build(arguments.work, arguments.text_preview, arguments.candidate_directory)
    print(f'内嵌字幕母版候选：{result["output"]["path"]}；正式双版本尚未替换')


if __name__ == '__main__':
    main()
