"""安全字幕带专用候选入口；不分派旧版晋升、回滚或正式上传。"""

import argparse
import subprocess
import sys
from pathlib import Path

import video_workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['upload-preview'])
    parser.add_argument('inputs', nargs='+', help='明确指定一个YouTube视频ID或链接，不展开列表')
    parser.add_argument('-o', '--out-dir', type=Path, default=video_workflow.DEFAULT_OUT)
    parser.add_argument('--speech-db', type=Path)
    parser.add_argument('--caption-candidate', required=True, type=Path)
    parser.add_argument('--caption-terms', required=True, type=Path)
    options = parser.parse_args()
    if len(options.inputs) != 1:
        parser.error('安全字幕带候选每次只处理一个明确工程')
    try:
        project_id = video_workflow.video_id(options.inputs[0])
        work = options.out_dir.expanduser().resolve() / f'.zh_work_yt_{project_id}'
        if not work.is_dir() or work.is_symlink():
            raise ValueError('需要现有真实工程目录')
        import upload_export_v3
        result = upload_export_v3.export_candidate(work, options.caption_terms,
                                                  options.caption_candidate, options.speech_db)
        print(result['output']['path'], flush=True)
        return 0
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f'{options.inputs[0]}: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
