#!/usr/bin/env python3
"""为模型画面取证生成限尺寸、限体积的 JPEG，不修改原始素材。"""

import argparse
import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path


def probe(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
         'stream=width,height', '-of', 'json', str(path)],
        capture_output=True, text=True, check=True, timeout=30)
    streams = json.loads(result.stdout).get('streams', [])
    if not streams or not streams[0].get('width') or not streams[0].get('height'):
        raise ValueError('输入没有可读取的画面')
    return streams[0]['width'], streams[0]['height']


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1048576), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def prepare(source, output, max_bytes=196608, max_side=1280, timestamp=None,
            crop=None, overwrite=False):
    source = Path(source).resolve(strict=True)
    output = Path(output).resolve()
    manifest_path = output.with_suffix(output.suffix + '.json')
    if source in (output, manifest_path):
        raise ValueError('不得覆盖原始素材')
    if output.suffix.lower() not in ('.jpg', '.jpeg'):
        raise ValueError('输出必须为 .jpg 或 .jpeg')
    if not overwrite and (output.exists() or manifest_path.exists()):
        raise ValueError('输出已存在；如需替换请显式使用 --overwrite')
    if max_bytes < 1024 or max_side < 32:
        raise ValueError('字节上限至少 1024，长边上限至少 32')
    if timestamp is not None and (not math.isfinite(timestamp) or timestamp < 0):
        raise ValueError('取帧时间必须是非负有限秒数')
    width, height = probe(source)
    original_dimensions = [width, height]
    filters = []
    if crop is not None:
        if len(crop) != 4:
            raise ValueError('裁剪格式为 x,y,width,height')
        left, top, width, height = crop
        if min(left, top) < 0 or min(width, height) < 2:
            raise ValueError('裁剪起点不能为负，宽高至少为 2')
        if left + width > original_dimensions[0] or top + height > original_dimensions[1]:
            raise ValueError('裁剪超出原始画面')
        filters.append(f'crop={width}:{height}:{left}:{top}')
    source_digest = digest(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    attempts = []
    with tempfile.TemporaryDirectory(prefix='.review-image-', dir=output.parent) as temporary:
        candidate = Path(temporary) / 'candidate.jpg'
        side = max_side
        while side >= 32:
            ratio = min(1, side / max(width, height))
            scaled_width = max(2, int(width * ratio) // 2 * 2)
            scaled_height = max(2, int(height * ratio) // 2 * 2)
            for quality in (5, 9, 15, 23, 31):
                command = ['ffmpeg', '-v', 'error', '-nostdin', '-y']
                if timestamp is not None:
                    command.extend(['-ss', str(timestamp)])
                command.extend(['-i', str(source), '-map', '0:v:0', '-vf',
                                ','.join([*filters, f'scale={scaled_width}:{scaled_height}']),
                                '-frames:v', '1', '-q:v', str(quality), '-pix_fmt', 'yuvj420p',
                                '-threads', '1', '-filter_threads', '1', '-update', '1', str(candidate)])
                subprocess.run(command, capture_output=True, text=True, check=True, timeout=90)
                if not candidate.exists():
                    raise ValueError('指定时间未取得画面')
                size = candidate.stat().st_size
                attempts.append({'dimensions': [scaled_width, scaled_height],
                                 'jpeg_quality': quality, 'bytes': size})
                if size > max_bytes:
                    continue
                dimensions = probe(candidate)
                if max(dimensions) > max_side:
                    raise ValueError('编码后尺寸超过限制')
                if digest(source) != source_digest:
                    raise ValueError('处理期间原始素材发生变化')
                manifest = {
                    'source': str(source), 'source_sha256': source_digest,
                    'source_dimensions': original_dimensions, 'timestamp_seconds': timestamp,
                    'crop': crop, 'output': str(output), 'sha256': digest(candidate),
                    'dimensions': list(dimensions), 'bytes': size,
                    'base64_bytes_estimate': 4 * ((size + 2) // 3),
                    'max_bytes': max_bytes, 'max_side': max_side, 'attempts': attempts,
                    'scope': '仅限制单张图像；不保证第三方网关的整项请求载荷限制',
                }
                pending_manifest = Path(temporary) / 'manifest.json'
                pending_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                            encoding='utf-8')
                candidate.replace(output)
                pending_manifest.replace(manifest_path)
                return manifest
            side = int(side * 0.75)
    raise ValueError('无法在字节和尺寸限制内生成 JPEG；未替换已有输出')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--max-bytes', type=int, default=196608)
    parser.add_argument('--max-side', type=int, default=1280)
    parser.add_argument('--time', type=float, help='从视频取帧的绝对秒数')
    parser.add_argument('--crop', help='原图像素坐标：x,y,width,height；先裁剪再缩放')
    parser.add_argument('--overwrite', action='store_true')
    arguments = parser.parse_args()
    try:
        crop = [int(value) for value in arguments.crop.split(',')] if arguments.crop else None
        manifest = prepare(arguments.source, arguments.output, arguments.max_bytes,
                           arguments.max_side, arguments.time, crop, arguments.overwrite)
    except subprocess.CalledProcessError as error:
        parser.exit(1, f'画面处理失败：{error.stderr[-1500:]}\n')
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'画面处理失败：{error}\n')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
