"""生成与跨条修复母版一致的中文烧录上传候选，正式交付保持不变。"""

import argparse
import fcntl
import math
import os
import shutil
import subprocess
from pathlib import Path

import caption_layout
import delivery_quality
import dubbing_state as state
import upload_export
import upload_export_v2
import upload_export_v3
import video_package
from prepare_videos import atomic_write, save_json


def require(condition, message):
    if not condition:
        raise ValueError(message)


def process_identity(pid):
    require(type(pid) is int and pid > 0, '进程编号无效')
    raw = Path(f'/proc/{pid}/stat').read_text()
    return {'pid': pid, 'start_ticks': int(raw.rsplit(')', 1)[1].split()[19]),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def encode_command(source, output, duration, geometry):
    require(type(duration) in {int, float} and math.isfinite(duration) and duration > 0, '候选时长无效')
    expected = upload_export_v3.geometry(geometry['source_width'], geometry['source_height'])
    require(geometry == expected, '不能改变已验证的字幕带几何或缩放原画')
    return ['ffmpeg', '-nostdin', '-n', '-v', 'error', '-i', str(source),
        '-map', '0:v:0', '-map', '0:a:0', '-map_metadata', '0', '-map_chapters', '0', '-sn',
        '-vf', f'pad=iw:ih+{geometry["band_height"]}:0:0:color=black,subtitles=upload-preview.ass',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
        '-c:a', 'copy', '-metadata:s:a:0', 'language=chi', '-metadata:s:a:0', 'title=中文配音',
        '-disposition:a:0', 'default', '-t', str(duration), '-movflags', '+faststart',
        '-progress', 'candidate-progress.txt', '-nostats', str(output)]


def validate_raster(receipt, preview, preview_reference):
    raster = receipt['all_cue_raster_bounds']
    require(receipt['passed'] is True and receipt['preview_proof'] == preview_reference
        and receipt['all_original_timestamps_identical'] is True
        and receipt['exact_full_character_stream_identical'] is True
        and receipt['all_ass_text_checked'] is True and receipt['all_intra_cue_terms_checked'] is True
        and raster['passed'] is True and raster['all_cues_inside_added_band'] is True
        and receipt['checked_cues'] == raster['cues_rasterized'] == preview['proof']['cue_count'],
        '完整文字或逐字幕字形预检无效')
    return raster


def export(work, checkpoint_path, directory, database=None):
    work, checkpoint_path = [Path(path).resolve(strict=True) for path in [work, checkpoint_path]]
    directory = Path(directory)
    require(directory.is_absolute() and not directory.exists()
        and directory.parent.resolve(strict=True).is_relative_to(work.parent / '.workflow_logs'), '需要全新的隐藏候选目录')
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = video_package.validate(work)
        quality = delivery_quality.require(work, database)
        checkpoint_reference = state.identity(checkpoint_path)
        checkpoint = state.load(checkpoint_path)
        require(checkpoint['state'] == 'caption_boundary_text_and_master_candidate_verified_upload_pending'
            and checkpoint['formal_delivery_iteration'] == 'v90', '需要已验证的v92跨条母版候选断点')
        references = checkpoint['evidence']
        for key in ['master_candidate', 'text_preview', 'independent_text_and_raster', 'visual_review',
                    'text_contracts', 'bilingual_contracts', 'chapter_layout_contracts', 'source_context']:
            require(state.matches(references[key]), '候选断点证据变化: ' + key)
        master = state.load(references['master_candidate']['path'])
        preview = state.load(references['text_preview']['path'])
        require(master['work'] == preview['work'] == str(work) and master['candidate_only'] is True
            and master['production_applied'] is False and master['original_master'] == manifest['output']
            and master['text_preview'] == references['text_preview']
            and state.matches(master['implementation']) and state.matches(master['reused_helpers'])
            and state.matches(master['output']) and state.matches(preview['implementation'])
            and state.matches(preview['plan']) and state.matches(preview['preview_ass'])
            and all(state.matches(reference) for reference in preview['inputs'].values())
            and all(state.matches(reference) for reference in master['subtitles'].values()), '母版或文字候选输入失效')
        for reference in preview['reused_layout_implementations']:
            require(state.matches(reference), '已绑定排版实现变化')
        source = Path(master['output']['path'])
        media = upload_export.probe(source)
        video = next(stream for stream in media['streams'] if stream['codec_type'] == 'video')
        duration = manifest['duration_seconds']
        forms = caption_layout.load_forms(preview['inputs']['terms']['path'])
        script, report = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
            duration, forms, Path(master['subtitles']['zh']['path']))
        require(script == Path(preview['preview_ass']['path']).read_text()
            and report == preview['layout'], '上传与已审图/内嵌母版文字或样式不一致')
        content = upload_export_v3.verify_script(script, Path(master['subtitles']['zh']['path']), duration)
        raster = validate_raster(state.load(references['independent_text_and_raster']['path']),
                                 preview, references['text_preview'])
        for key in ['ass', 'metadata', 'log']:
            require(state.matches(raster[key]), '复用字形预检身份无效')
        require(shutil.disk_usage(directory.parent).free > 2 * source.stat().st_size + 1024 ** 3, '磁盘不足')
        font_lines = subprocess.run(['fc-match', '-f', '%{family}\n%{file}', 'Noto Sans CJK SC'],
            capture_output=True, text=True, check=True).stdout.splitlines()
        require(len(font_lines) == 2 and 'Noto Sans CJK SC' in font_lines[0], '缺少可核对的CJK字体')
        directory.mkdir()
        candidate, live_path = directory / 'candidate.mp4', directory / 'candidate-live-state.json'
        ass = directory / 'upload-preview.ass'
        atomic_write(ass, script)
        dependencies = {**preview['inputs'], 'checkpoint': checkpoint_reference,
            'master_candidate_result': references['master_candidate'], 'master_candidate': master['output'],
            'text_preview': references['text_preview'], 'raster_receipt': references['independent_text_and_raster'],
            'candidate_ass': state.identity(ass), 'preview_ass': preview['preview_ass'],
            'repaired_chinese_captions': master['subtitles']['zh'],
            'repaired_bilingual_captions': master['subtitles']['bilingual'],
            'candidate_exporter': state.identity(__file__), 'layout_implementation': state.identity(caption_layout.__file__),
            'v3_layout_implementation': state.identity(upload_export_v3.__file__),
            'audio_helper': state.identity(upload_export_v2.__file__), 'package_verifier': state.identity(video_package.__file__),
            'quality_implementation': state.identity(delivery_quality.__file__), 'font': state.identity(Path(font_lines[1])),
            'ffmpeg': state.identity(Path(shutil.which('ffmpeg')))}
        live = {'version': 4, 'kind': 'caption-boundary-upload-candidate-v4', 'work': str(work),
            'created_at': state.now(), 'owner': process_identity(os.getpid()), 'state': 'preflight-complete',
            'dependencies': dependencies, 'production_applied': False, 'goal_status': 'active'}
        save_json(live_path, live)
        try:
            require(all(state.matches(reference) for reference in dependencies.values()), '编码前绑定变化')
            command = encode_command(source, candidate, duration, report['geometry'])
            with (directory / 'candidate-encode.log').open('x') as log:
                process = subprocess.Popen(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT)
                live.update(state='encoding', encoder=process_identity(process.pid), command=command,
                    quality_bindings=quality['bindings'])
                save_json(live_path, live)
                exit_code = process.wait()
            live.update(state='checking-media', encoder_exit_code=exit_code)
            save_json(live_path, live)
            require(exit_code == 0, f'上传候选编码失败，真实出口{exit_code}')
            verification = upload_export.verify(candidate, duration)
            verification['self_contained'] = video_package.verify_media(candidate, manifest, upload=True)
            actual_video = next(stream for stream in upload_export.probe(candidate)['streams'] if stream['codec_type'] == 'video')
            geometry = report['geometry']
            require([actual_video['width'], actual_video['height']] == [geometry['output_width'], geometry['output_height']]
                and actual_video.get('sample_aspect_ratio') == video.get('sample_aspect_ratio'), '候选原画比例或字幕带画幅变化')
            live.update(state='full-decoding')
            save_json(live_path, live)
            verification['full_decode'] = video_package.full_decode(candidate, directory, 'candidate-full-decode.log')
            live.update(state='checking-chinese-aac-and-bindings')
            save_json(live_path, live)
            master_audio, candidate_audio = [upload_export_v2.audio_digest(path) for path in [source, candidate]]
            require(master_audio == candidate_audio, '候选中文AAC与母版不一致')
            require(all(state.matches(reference) for reference in dependencies.values())
                and video_package.validate(work) == manifest
                and delivery_quality.require(work, database)['bindings'] == quality['bindings'], '制作期间输入/质量绑定变化')
            result = {'version': 4, 'kind': 'caption-boundary-upload-candidate-v4', 'created_at': state.now(),
                'work': str(work), 'dependencies': dependencies, 'output': state.identity(candidate),
                'verification': verification, 'quality': quality, 'layout_report': report, 'content_check': content,
                'reused_all_cue_raster_check': raster, 'profile': {'layout': 'caption-boundary-safe-band-v4',
                    'crf': 20, 'position': 'added-bottom-band', 'audio': 'copy-candidate-master-chinese-aac',
                    'chapters': 'copy-candidate-master', 'geometry': geometry},
                'master_chinese_aac': master_audio, 'candidate_chinese_aac': candidate_audio, 'chinese_aac_identical': True,
                'production_applied': False, 'current_delivery_unchanged': True, 'visual_review_required': True,
                'original_video_fidelity_pending': True, 'paired_promotion_implemented': False,
                'word_alignment_verified': False, 'tts_performed': False, 'human_listened': False,
                'potplayer_tested': False, 'goal_status': 'active'}
            save_json(directory / 'candidate-result.json', result)
            live.update(state='candidate-complete', result=state.identity(directory / 'candidate-result.json'), finished_at=state.now())
            save_json(live_path, live)
            return result
        except BaseException as error:
            live.update(state='failed', error=f'{type(error).__name__}: {error}', finished_at=state.now())
            save_json(live_path, live)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--checkpoint', required=True, type=Path)
    parser.add_argument('--candidate-directory', required=True, type=Path)
    parser.add_argument('--speech-db', type=Path)
    arguments = parser.parse_args()
    result = export(arguments.work, arguments.checkpoint, arguments.candidate_directory, arguments.speech_db)
    print('UPLOAD CANDIDATE COMPLETE', result['output']['path'], '; formal pair unchanged')
