"""生成词级折行修订的自包含上传候选；不替换既有交付或旧绑定。"""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import caption_layout
import delivery_quality
import dubbing_state as state
import upload_export
import video_package
from prepare_videos import save_json


def audio_digest(path):
    process = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                              '-map', '0:a:0', '-c:a', 'copy', '-f', 'adts', '-'],
                             capture_output=True, check=True, timeout=120)
    if process.stderr.strip():
        raise ValueError(process.stderr.decode())
    return {'sha256': hashlib.sha256(process.stdout).hexdigest(), 'bytes': len(process.stdout)}


def export_candidate(work, terms, directory, database=None):
    work, terms, directory = (Path(path).resolve(strict=True) for path in (work, terms, directory))
    if not (directory.is_relative_to(work) or directory.is_relative_to(work.parent / '.workflow_logs')):
        raise ValueError('候选只允许写入隐藏工程或工作流证据目录')
    result_path = directory / 'candidate-result.json'
    if result_path.exists():
        raise ValueError('候选证据已经存在；先核对原进程和终态，不重复编码')
    candidate = directory / 'candidate.mp4'
    log_path = directory / 'candidate-encode.log'
    live_path = directory / 'candidate-live-state.json'
    if any(path.exists() for path in (candidate, log_path, live_path)):
        raise ValueError('存在候选或原执行记录；先检查原进程，不盲目覆盖')
    live = {'version': 2, 'owner_pid': os.getpid(), 'work': str(work), 'state': 'starting',
            'production_applied': False, 'goal_status': 'active'}
    with (work / '.workflow.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('工程正在执行其他流程，未启动候选编码') from error
        save_json(live_path, live)
        try:
            ass_path, preflight_path = directory / 'upload-preview.ass', directory / 'preflight.json'
            if not ass_path.exists() and not preflight_path.exists():
                caption_layout.preflight(work, terms, ass_path, preflight_path)
            preflight = state.load(preflight_path)
            dependencies = {**preflight['inputs'], 'ass': preflight['ass'],
                            'preflight': state.identity(preflight_path),
                            'candidate_exporter': state.identity(__file__),
                            'package_implementation': state.identity(video_package.__file__)}
            if (preflight['work'] != str(work) or preflight['inputs']['terms'] != state.identity(terms)
                    or preflight['layout'] != 'registered-terms-v2'
                    or not all(state.matches(identity) for identity in dependencies.values())):
                raise ValueError('预检不是当前工程、词表或实现，保留旧成片')
            manifest = video_package.validate(work)
            quality = delivery_quality.require(work, database)
            source = Path(manifest['output']['path'])
            media = upload_export.probe(source)
            video = next(item for item in media['streams'] if item['codec_type'] == 'video')
            duration = float(media['format']['duration'])
            script, report = caption_layout.subtitles(work, int(video['width']), int(video['height']),
                                                      duration, 'bottom', caption_layout.load_forms(terms),
                                                      Path(manifest['subtitles']['zh']['path']))
            if script != ass_path.read_text() or report != preflight['report']:
                raise ValueError('预检字幕与当前独立重建结果不一致')
            font_lines = subprocess.run(['fc-match', '-f', '%{family}\n%{file}', 'Noto Sans CJK SC'],
                                        capture_output=True, text=True, check=True).stdout.splitlines()
            if len(font_lines) != 2 or 'Noto Sans CJK SC' not in font_lines[0]:
                raise ValueError('缺少可核对的中文字幕字体')
            dependencies['font'] = state.identity(Path(font_lines[1]))
            if shutil.disk_usage(directory).free < 2 * source.stat().st_size + 1024 ** 3:
                raise ValueError('磁盘不足以安全保留旧交付并生成候选')
            command = ['ffmpeg', '-nostdin', '-n', '-v', 'error', '-i', str(source),
                       '-map', '0:v:0', '-map', '0:a:0', '-map_metadata', '0', '-map_chapters', '0', '-sn',
                       '-vf', 'subtitles=upload-preview.ass', '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
                       '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-metadata:s:a:0', 'language=chi',
                       '-metadata:s:a:0', 'title=中文配音', '-disposition:a:0', 'default',
                       '-t', str(duration), '-movflags', '+faststart', str(candidate)]
            with log_path.open('x') as log:
                process = subprocess.Popen(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT)
                live.update(state='encoding', encoder_pid=process.pid, command=command,
                            dependencies=dependencies, quality_bindings=quality['bindings'])
                save_json(live_path, live)
                exit_code = process.wait()
            live.update(encoder_exit_code=exit_code, state='checking-media')
            save_json(live_path, live)
            if exit_code:
                raise ValueError(f'候选编码失败，出口 {exit_code}；原交付未动')
            verification = upload_export.verify(candidate, duration)
            verification['self_contained'] = video_package.verify_media(candidate, manifest, upload=True)
            live.update(state='full-decoding')
            save_json(live_path, live)
            verification['full_decode'] = video_package.full_decode(candidate, directory, 'candidate-full-decode.log')
            live.update(state='checking-bindings-and-aac')
            save_json(live_path, live)
            master_audio, candidate_audio = audio_digest(source), audio_digest(candidate)
            if master_audio != candidate_audio:
                raise ValueError('候选中文 AAC 与母版不一致')
            if (not all(state.matches(identity) for identity in dependencies.values())
                    or video_package.validate(work) != manifest
                    or delivery_quality.require(work, database)['bindings'] != quality['bindings']):
                raise ValueError('候选制作期间输入变化，未替换旧交付')
            result = {'version': 2, 'created_at': state.now(), 'dependencies': dependencies,
                      'output': state.identity(candidate), 'verification': verification,
                      'profile': {'layout': 'registered-terms-v2', 'crf': 20, 'position': 'bottom',
                                  'audio': 'copy-current-chinese-aac', 'chapters': 'copy-master'},
                      'quality': quality, 'layout_report': report,
                      'master_chinese_aac': master_audio, 'candidate_chinese_aac': candidate_audio,
                      'chinese_aac_identical': True, 'production_applied': False,
                      'current_delivery_unchanged': True, 'human_listened': False,
                      'potplayer_tested': False, 'goal_status': 'active'}
            save_json(result_path, result)
            live.update(state='candidate-complete', result=state.identity(result_path), finished_at=state.now())
            save_json(live_path, live)
            return result
        except Exception as error:
            live.update(state='failed', error=f'{type(error).__name__}: {error}', finished_at=state.now())
            save_json(live_path, live)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--terms', required=True, type=Path)
    parser.add_argument('--directory', required=True, type=Path)
    options = parser.parse_args()
    result = export_candidate(options.work, options.terms, options.directory)
    print(json.dumps({'candidate': result['output'], 'production_applied': False,
                      'full_decode_exit_code': result['verification']['full_decode']['exit_code']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
