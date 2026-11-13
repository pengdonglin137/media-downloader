"""上传版版本化验收、v3安全带晋升及回滚；写入必须显式--apply。"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import dubbing_state as state
import upload_delivery
import upload_delivery_v3
import video_workflow
from prepare_videos import save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['verify', 'promote', 'rollback'])
    parser.add_argument('input', help='明确指定一个视频ID或链接，不展开列表')
    parser.add_argument('-o', '--out-dir', type=Path, default=video_workflow.DEFAULT_OUT)
    parser.add_argument('--caption-candidate', type=Path)
    parser.add_argument('--transaction', type=Path)
    parser.add_argument('--speech-db', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path)
    options = parser.parse_args()
    if ((options.stage == 'promote') != bool(options.caption_candidate)
            or (options.stage == 'rollback') != bool(options.transaction)
            or options.stage == 'verify' and options.apply):
        parser.error('promote只接收候选，rollback只接收事务；verify不得--apply')
    try:
        project_id = video_workflow.video_id(options.input)
        work = options.out_dir.expanduser().resolve() / f'.zh_work_yt_{project_id}'
        if options.report:
            report = options.report.expanduser().resolve()
            if (report.exists() or not report.parent.is_dir()
                    or not (report.is_relative_to(work) or report.is_relative_to(work.parent / '.workflow_logs'))):
                raise ValueError('报告必须是隐藏工程或工作流证据目录中尚不存在的路径')
        if options.stage == 'promote':
            result = upload_delivery_v3.promote(work, options.caption_candidate, options.apply, options.speech_db)
        elif options.stage == 'rollback':
            result = upload_delivery_v3.rollback(work, options.transaction, options.apply, options.speech_db)
        else:
            upload_delivery.pending_transactions(work)
            current = upload_delivery_v3.validate_current(work, database=options.speech_db)
            result = {'state': 'verified', 'version': current['version'], 'output': current['output'], 'applied': False}
        if options.report:
            save_json(report, {'checked_at': state.now(), 'entrypoint': state.identity(__file__), 'result': result})
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f'{options.input}: {type(error).__name__}: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
