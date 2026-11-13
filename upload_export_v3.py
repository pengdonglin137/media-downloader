"""独立安全字幕带候选：保留原画幅，在新增黑带内烧录全部中文字幕。"""

import fcntl
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import caption_layout
import delivery_quality
import dubbing_state as state
import upload_export
import upload_export_v2
import video_package
from prepare_videos import atomic_write, save_json


LAYOUT = 'registered-terms-safe-band-v3'


def geometry(width, height):
    if (type(width) is not int or type(height) is not int or width < 256 or height < 144
            or width % 2 or height % 2):
        raise ValueError('安全字幕带要求可读的偶数尺寸原画幅，不自动缩放或裁切')
    font_size = max(18, round(height * .032))
    outline = max(1, round(height / 720))
    margin = max(12, round(height * .0185))
    band = 2 * math.ceil(max(height * 2 / 15, 2 * font_size + 2 * margin + 2 * outline + 12) / 2)
    return {'source_width': width, 'source_height': height, 'output_width': width,
            'output_height': height + band, 'band_height': band, 'font_size': font_size,
            'margin_vertical': margin, 'outline': outline, 'source_scaled': False,
            'source_cropped': False, 'source_origin': [0, 0]}


def subtitles(work, width, height, duration, forms, caption_path):
    layout = geometry(width, height)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('安全字幕带需要有效的原片总时长')
    script, report = caption_layout.subtitles(work, width, height, duration, 'bottom', forms, caption_path)
    header, rows = script.split('Dialogue: ', 1)
    if header.count(f'PlayResY: {height}\n') != 1:
        raise ValueError('无法唯一确定原ASS画幅')
    header = header.replace(f'PlayResY: {height}\n', f'PlayResY: {layout["output_height"]}\n')
    lines = header.splitlines(keepends=True)
    style_indices = [index for index, line in enumerate(lines) if line.startswith('Style: ')]
    if len(style_indices) != 1:
        raise ValueError('安全带只接受唯一、可核对的默认字幕样式')
    style_index = style_indices[0]
    fields = lines[style_index].removeprefix('Style: ').strip().split(',')
    if (len(fields) != 23 or fields[0] != 'Default' or fields[1] != 'Noto Sans CJK SC'
            or fields[2] != str(layout['font_size']) or fields[18] != '2'):
        raise ValueError('原字幕样式不符合安全带布局契约')
    fields[21] = str(layout['margin_vertical'])
    lines[style_index] = 'Style: ' + ','.join(fields) + '\n'
    return ''.join(lines) + 'Dialogue: ' + rows, {**report, 'geometry': layout}


def ass_seconds(value):
    hours, minutes, seconds = value.split(':')
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def verify_script(script, caption_path, duration):
    expected = state.cues(caption_path)
    rows = [line.split(',', 9) for line in script.splitlines() if line.startswith('Dialogue: ')]
    if len(rows) != len(expected):
        raise ValueError('安全带必须保留全部片头和正文字幕')
    errors = []
    previous_end = 0
    for number, (row, cue) in enumerate(zip(rows, expected), 1):
        start, end, text = cue
        if (len(row) != 10 or not 0 <= start < end <= duration + .005
                or row[3:9] != ['Default', '', '0', '0', '0', '']
                or any(character in row[9] for character in '{}')
                or '\\' in row[9].replace(r'\N', '')
                or not 1 <= len(row[9].split(r'\N')) <= 2
                or any(not line.strip() for line in row[9].split(r'\N'))
                or ''.join(row[9].replace(r'\N', '').split()) != ''.join(text.split())):
            raise ValueError(f'安全带字幕{number}内容、时间范围或样式不守恒')
        error = max(abs(ass_seconds(row[1]) - start), abs(ass_seconds(row[2]) - end))
        if error > .005001:
            raise ValueError(f'安全带字幕{number}时间发生非格式量化变化')
        actual_start, actual_end = ass_seconds(row[1]), ass_seconds(row[2])
        if actual_start < previous_end or actual_end <= actual_start:
            raise ValueError('安全带不自动修剪重叠或量化后消失的字幕；需独立审校')
        previous_end = actual_end
        errors.append(error)
    return {'cues': len(rows), 'non_whitespace_text_identical': True, 'maximum_lines': 2,
            'maximum_time_quantization_seconds': max(errors), 'per_cue_content_and_time_checked': True}


def raster_check(script, layout, directory):
    header, first = script.split('Dialogue: ', 1)
    original = ['Dialogue: ' + first.splitlines()[0],
                *[line for line in first.splitlines()[1:] if line.startswith('Dialogue: ')]]
    rows = []
    for index, line in enumerate(original):
        fields = line.split(',', 9)
        fields[1:3] = [upload_export.ass_time(index), upload_export.ass_time(index + 1)]
        rows.append(','.join(fields) + '\n')
    audit_path = directory / 'geometry-audit.ass'
    metadata_path = directory / 'geometry-bounds.txt'
    log_path = directory / 'geometry-raster.log'
    if any(path.exists() for path in [audit_path, metadata_path, log_path]):
        raise ValueError('已经存在逐字幕栅格证据，不覆盖或盲目重跑')
    atomic_write(audit_path, header + ''.join(rows))
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
               f'color=c=black:s={layout["output_width"]}x{layout["output_height"]}:r=1:d={len(rows)}',
               '-vf', 'subtitles=geometry-audit.ass,bbox=min_val=16,metadata=mode=print:file=geometry-bounds.txt',
               '-frames:v', str(len(rows)), '-an', '-sn', '-f', 'null', '-']
    process = subprocess.run(command, cwd=directory, capture_output=True, text=True)
    atomic_write(log_path, process.stdout + process.stderr)
    if process.returncode or process.stderr.strip():
        raise ValueError('逐字幕栅格校验失败，未启动全片编码')
    bounds, current = [], None
    for line in metadata_path.read_text().splitlines():
        if line.startswith('frame:'):
            current = {'frame': int(re.match(r'frame:(\d+)', line)[1])}
            bounds.append(current)
        elif line.startswith('lavfi.bbox.') and current is not None:
            key, value = line.split('=', 1)
            current[key.removeprefix('lavfi.bbox.')] = int(value)
    if len(bounds) != len(rows) or [entry['frame'] for entry in bounds] != list(range(len(rows))):
        raise ValueError('没有取得每一条字幕的完整实际栅格边界')
    guard = layout['outline'] + 4
    for number, entry in enumerate(bounds, 1):
        if (entry.get('w', 0) <= 0 or entry.get('h', 0) <= 0
                or entry.get('x1', -1) < guard or entry.get('x2', layout['output_width']) >= layout['output_width'] - guard
                or entry.get('y1', -1) < layout['source_height'] + guard
                or entry.get('y2', layout['output_height']) >= layout['output_height'] - guard):
            raise ValueError(f'字幕{number}实际渲染越出安全带或被边缘裁切')
    return {'passed': True, 'cues_rasterized': len(rows), 'all_cues_inside_added_band': True,
            'guard_pixels': guard, 'bounds': bounds, 'ass': state.identity(audit_path),
            'metadata': state.identity(metadata_path), 'log': state.identity(log_path),
            'command': command, 'scope': 'Every actual cue text and style rasterized once with sequential proof-only timestamps; '
            'not source timestamps, full-video pixels, actual playback, or audio listening.'}


def export_candidate(work, terms, directory, database=None):
    if Path(directory).is_symlink() or Path(work).is_symlink():
        raise ValueError('工程及候选目录不能是符号链接')
    work, terms, directory = (Path(path).resolve(strict=True) for path in (work, terms, directory))
    if not (directory.is_relative_to(work) or directory.is_relative_to(work.parent / '.workflow_logs')):
        raise ValueError('候选只允许写入隐藏工程或工作流证据目录')
    names = ['candidate.mp4', 'candidate-result.json', 'candidate-live-state.json', 'candidate-encode.log',
             'upload-preview.ass', 'preflight.json', 'geometry-audit.ass', 'geometry-bounds.txt', 'geometry-raster.log']
    if any((directory / name).exists() for name in names):
        raise ValueError('存在候选或原执行证据；先核对原进程与终态，不覆盖或重复编码')
    candidate, result_path, live_path = [directory / name for name in
                                         ['candidate.mp4', 'candidate-result.json', 'candidate-live-state.json']]
    live = {'version': 3, 'owner_pid': os.getpid(), 'work': str(work), 'state': 'starting',
            'production_applied': False, 'goal_status': 'active'}
    with (work / '.workflow.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('工程正在执行其他流程，未启动候选编码') from error
        save_json(live_path, live)
        try:
            manifest = video_package.validate(work)
            quality = delivery_quality.require(work, database)
            current = state.load(work / 'upload_export.json')
            if not state.matches(current['output']):
                raise ValueError('当前正式上传版已变化')
            source = Path(manifest['output']['path'])
            media = upload_export.probe(source)
            video = next(entry for entry in media['streams'] if entry['codec_type'] == 'video')
            duration = float(media['format']['duration'])
            caption_path = Path(manifest['subtitles']['zh']['path'])
            script, report = subtitles(work, int(video['width']), int(video['height']), duration,
                                        caption_layout.load_forms(terms), caption_path)
            content = verify_script(script, caption_path, duration)
            layout = report['geometry']
            font_lines = subprocess.run(['fc-match', '-f', '%{family}\n%{file}', 'Noto Sans CJK SC'],
                                        capture_output=True, text=True, check=True).stdout.splitlines()
            if len(font_lines) != 2 or 'Noto Sans CJK SC' not in font_lines[0]:
                raise ValueError('缺少可核对的中文字幕字体')
            dependencies = {'master': manifest['output'], 'captions': manifest['subtitles']['zh'],
                            'terms': state.identity(terms), 'upload_manifest': state.identity(work / 'upload_export.json'),
                            'upload': current['output'], 'candidate_exporter': state.identity(__file__),
                            'caption_layout': state.identity(caption_layout.__file__),
                            'legacy_exporter': state.identity(upload_export.__file__),
                            'v2_audio_helper': state.identity(upload_export_v2.__file__),
                            'package_implementation': state.identity(video_package.__file__),
                            'quality_implementation': state.identity(delivery_quality.__file__),
                            'font': state.identity(Path(font_lines[1])),
                            'ffmpeg': state.identity(Path(shutil.which('ffmpeg')))}
            if shutil.disk_usage(directory).free < 2 * source.stat().st_size + 1024 ** 3:
                raise ValueError('磁盘不足以保留旧交付并生成候选')
            ass_path = directory / 'upload-preview.ass'
            atomic_write(ass_path, script)
            live.update(state='raster-checking-all-cues', dependencies=dependencies)
            save_json(live_path, live)
            raster = raster_check(script, layout, directory)
            preflight_path = directory / 'preflight.json'
            save_json(preflight_path, {'version': 3, 'checked_at': state.now(), 'work': str(work), 'layout': LAYOUT,
                                      'inputs': dependencies, 'ass': state.identity(ass_path), 'report': report,
                                      'content_check': content, 'raster_check': raster, 'preflight_passed': True,
                                      'production_applied': False, 'human_listened': False})
            dependencies = {**dependencies, 'ass': state.identity(ass_path),
                            'preflight': state.identity(preflight_path),
                            'raster_ass': raster['ass'], 'raster_metadata': raster['metadata'], 'raster_log': raster['log']}
            if not all(state.matches(observation) for observation in dependencies.values()):
                raise ValueError('预检期间输入变化，未启动全片编码')
            command = ['ffmpeg', '-nostdin', '-n', '-v', 'error', '-i', str(source),
                       '-map', '0:v:0', '-map', '0:a:0', '-map_metadata', '0', '-map_chapters', '0', '-sn',
                       '-vf', f'pad=iw:ih+{layout["band_height"]}:0:0:color=black,subtitles=upload-preview.ass',
                       '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p',
                       '-c:a', 'copy', '-metadata:s:a:0', 'language=chi', '-metadata:s:a:0', 'title=中文配音',
                       '-disposition:a:0', 'default', '-t', str(duration), '-movflags', '+faststart', str(candidate)]
            with (directory / 'candidate-encode.log').open('x') as log:
                process = subprocess.Popen(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT)
                live.update(state='encoding', encoder_pid=process.pid, command=command,
                            dependencies=dependencies, quality_bindings=quality['bindings'])
                save_json(live_path, live)
                exit_code = process.wait()
            live.update(state='checking-media', encoder_exit_code=exit_code)
            save_json(live_path, live)
            if exit_code:
                raise ValueError(f'候选编码失败，出口{exit_code}；原交付未动')
            verification = upload_export.verify(candidate, duration)
            verification['self_contained'] = video_package.verify_media(candidate, manifest, upload=True)
            actual_video = next(entry for entry in upload_export.probe(candidate)['streams'] if entry['codec_type'] == 'video')
            if ([actual_video['width'], actual_video['height']] != [layout['output_width'], layout['output_height']]
                    or actual_video.get('sample_aspect_ratio') != video.get('sample_aspect_ratio')):
                raise ValueError('安全带候选画幅或像素宽高比不符')
            verification['geometry'] = layout
            verification['all_caption_raster_bounds'] = raster
            live.update(state='full-decoding')
            save_json(live_path, live)
            verification['full_decode'] = video_package.full_decode(candidate, directory, 'candidate-full-decode.log')
            live.update(state='checking-bindings-and-aac')
            save_json(live_path, live)
            master_audio = upload_export_v2.audio_digest(source)
            candidate_audio = upload_export_v2.audio_digest(candidate)
            if master_audio != candidate_audio:
                raise ValueError('候选中文AAC与母版不一致')
            if (not all(state.matches(observation) for observation in dependencies.values())
                    or video_package.validate(work) != manifest
                    or delivery_quality.require(work, database)['bindings'] != quality['bindings']):
                raise ValueError('候选制作期间输入或质量绑定变化，原交付未动')
            result = {'version': 3, 'created_at': state.now(), 'dependencies': dependencies,
                      'output': state.identity(candidate), 'verification': verification,
                      'profile': {'layout': LAYOUT, 'crf': 20, 'position': 'added-bottom-band',
                                  'audio': 'copy-current-chinese-aac', 'chapters': 'copy-master', 'geometry': layout},
                      'quality': quality, 'layout_report': report, 'content_check': content,
                      'master_chinese_aac': master_audio, 'candidate_chinese_aac': candidate_audio,
                      'chinese_aac_identical': True, 'production_applied': False, 'current_delivery_unchanged': True,
                      'visual_review_required': True, 'human_listened': False, 'potplayer_tested': False,
                      'goal_status': 'active'}
            save_json(result_path, result)
            live.update(state='candidate-complete', result=state.identity(result_path), finished_at=state.now())
            save_json(live_path, live)
            return result
        except Exception as error:
            live.update(state='failed', error=f'{type(error).__name__}: {error}', finished_at=state.now())
            save_json(live_path, live)
            raise
