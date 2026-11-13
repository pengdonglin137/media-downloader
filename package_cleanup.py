"""仅清理已验证自包含工程中可再生的视频中间文件，默认只预览。"""

import argparse
import fcntl
import json
import re
import stat
import time
from pathlib import Path

import dubbing_state as state
import upload_export
import video_package
from prepare_videos import save_json


REGENERABLE = ('body-video.mp4', 'video.mp4', 'intro-video.mp4')


def validate_inputs(work, fidelity_path):
    manifest = video_package.validate(work)
    rendering = state.load(work / 'render_state.json')
    directory = Path(manifest['body']['path']).parent
    if (directory.parent != work / 'self_contained'
            or not re.fullmatch(r'[0-9a-f]{20}', directory.name)
            or directory.is_symlink() or directory.parent.is_symlink()
            or directory.resolve() != directory):
        raise ValueError('组合目录不是工程内的真实归档目录，拒绝清理')
    full_decode = manifest['full_decode']
    if full_decode['exit_code'] != 0 or not state.matches(full_decode['log']):
        raise ValueError('母版全片解码证据缺失或已变化')
    upload = state.load(work / 'upload_export.json')
    packaged = upload['dependencies'].get('self_contained', {})
    if (packaged.get('manifest') != rendering['package_manifest']
            or packaged.get('master') != manifest['output']
            or upload['dependencies']['implementation_sha256'] != state.digest(upload_export.__file__)
            or not state.matches(upload['output'])
            or upload['verification']['full_decode']['exit_code'] != 0
            or not state.matches(upload['verification']['full_decode']['log'])):
        raise ValueError('需先完成当前组合母版的有效上传版及完整解码')
    fidelity = state.load(fidelity_path)
    if (fidelity.get('passed') is not True
            or fidelity.get('decoded_body_frames_identical') is not True
            or fidelity.get('max_offset_error_seconds', float('inf')) > fidelity.get('offset_tolerance_seconds', 0)
            or fidelity.get('expected_body_offset_seconds') != manifest['body_offset_seconds']
            or fidelity.get('body', {}).get('sha256') != manifest['body']['sha256']
            or fidelity.get('master', {}).get('sha256') != manifest['output']['sha256']
            or not fidelity.get('frame_hash_logs')
            or not all(state.matches(identity) for identity in fidelity['frame_hash_logs'])):
        raise ValueError('缺少当前正文和母版内容的逐帧保持及时间偏移证明')
    video_package.verify_media(Path(manifest['output']['path']), manifest)
    video_package.verify_media(Path(upload['output']['path']), manifest, upload=True)
    return directory, manifest, upload


def candidates(directory):
    result = []
    for name in REGENERABLE:
        path = directory / name
        if not path.exists() and not path.is_symlink():
            continue
        attributes = path.lstat()
        if not stat.S_ISREG(attributes.st_mode) or attributes.st_nlink != 1:
            raise ValueError(f'中间文件不是唯一链接的普通文件，拒绝清理: {name}')
        result.append(state.identity(path))
    return result


def cleanup(work, fidelity_path, apply=False):
    work = Path(work)
    if (work.is_symlink() or not work.is_dir()
            or not re.fullmatch(r'\.zh_work_(?:yt_[A-Za-z0-9_-]{11}|x_[0-9]+)', work.name)):
        raise ValueError('仅支持现有真实YouTube或X工程目录，不接受链接或任意目录')
    work = work.resolve(strict=True)
    fidelity_path = Path(fidelity_path).resolve(strict=True)
    with (work / '.workflow.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('工程正在执行其他流程，保留全部文件，稍后再清理') from error
        directory, manifest, upload = validate_inputs(work, fidelity_path)
        selected = candidates(directory)
        report = {'version': 1, 'created_at': state.now(), 'work': str(work),
                  'mode': 'apply' if apply else 'dry-run', 'state': 'planned',
                  'implementation_sha256': state.digest(__file__),
                  'fidelity': state.identity(fidelity_path), 'body': manifest['body'],
                  'master': manifest['output'], 'upload': upload['output'],
                  'candidates': selected, 'candidate_bytes': sum(item['bytes'] for item in selected),
                  'removed': [], 'human_listened': False}
        if not apply or not selected:
            report['state'] = 'planned' if selected else 'nothing_to_remove'
            return report
        evidence = directory / 'intermediate_cleanup' / f'{time.time_ns()}.json'
        if evidence.parent.is_symlink():
            raise ValueError('清理记录目录不能是符号链接')
        evidence.parent.mkdir(exist_ok=True)
        report['evidence_path'] = str(evidence)
        save_json(evidence, report)
        try:
            for identity in selected:
                path = Path(identity['path'])
                if (directory.resolve() != directory or directory.is_symlink()
                        or path.parent != directory or path.name not in REGENERABLE
                        or path.is_symlink() or not state.matches(identity)
                        or path.lstat().st_nlink != 1):
                    raise ValueError('清理前中间文件身份变化，停止，不删除其他文件')
                path.unlink()
                report['removed'].append(identity)
                report['state'] = 'in-progress'
                save_json(evidence, report)
            after = video_package.validate(work)
            if after != manifest or not state.matches(upload['output']):
                raise ValueError('清理后成片或恢复归档身份变化，保留清理记录待复核')
            report['state'] = 'complete'
            report['removed_bytes'] = sum(item['bytes'] for item in report['removed'])
            report['retained_bindings_revalidated'] = True
            save_json(evidence, report)
            return report
        except Exception as error:
            report['state'] = 'interrupted'
            report['error'] = f'{type(error).__name__}: {error}'
            save_json(evidence, report)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--fidelity', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='实际删除白名单中间文件；默认只预览')
    arguments = parser.parse_args()
    try:
        result = cleanup(arguments.work, arguments.fidelity, arguments.apply)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'清理未完成: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
