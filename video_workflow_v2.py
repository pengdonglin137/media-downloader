"""版本化字幕预览入口：由候选生产器独占工程锁，其余阶段保持旧CLI。"""

import argparse
import sys
from pathlib import Path

import video_workflow


def main():
    arguments = sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    if not arguments or arguments[0] != 'upload-preview':
        if 'upload-preview' in arguments:
            parser.error('版本化预览入口要求先写upload-preview，再写选项与工程')
        return video_workflow.main()
    parser.add_argument('stage', choices=['upload-preview'])
    parser.add_argument('inputs', nargs='+', help='显式指定一个YouTube视频ID或链接')
    parser.add_argument('-o', '--out-dir', type=Path, default=video_workflow.DEFAULT_OUT)
    parser.add_argument('--speech-db', type=Path)
    parser.add_argument('--caption-candidate', required=True, type=Path)
    parser.add_argument('--caption-terms', required=True, type=Path)
    parser.add_argument('--sub-pos', choices=['bottom'], default='bottom')
    parser.add_argument('--crf', type=int, choices=[20], default=20)
    options = parser.parse_args()
    if len(options.inputs) != 1:
        parser.error('字幕候选每次只处理一个明确工程')
    try:
        identity = video_workflow.video_id(options.inputs[0])
        work = options.out_dir.expanduser().resolve() / f'.zh_work_yt_{identity}'
        if not work.is_dir() or work.is_symlink():
            raise ValueError('需要现有真实工程目录')
        import upload_export_v2
        result = upload_export_v2.export_candidate(work, options.caption_terms,
                                                  options.caption_candidate, options.speech_db)
        print(result['output']['path'], flush=True)
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(f'{options.inputs[0]}: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
