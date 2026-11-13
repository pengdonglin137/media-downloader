#!/usr/bin/env python3
"""批量译配入口及品质流程：render/review → release/upload，局部 speech-plan/preview。"""

import argparse
import fcntl
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import wave
from pathlib import Path

import check_videos
import dubbing_state as state
import speech_review
from prepare_videos import DEFAULT_OUT, SCRIPT_DIR, atomic_write, locked_prepare, save_json, video_id


def compile_translation(work, normalization=None):
    import speech_normalization
    previous = state.cues(work / 'spoken.srt') if (work / 'spoken.srt').exists() else []
    if normalization is not None:
        speech_normalization.configure(work, normalization)
    if (work / 'compilation.json').exists():
        try:
            state.validate_compilation(work)
            print('编译输入未变化，保留字幕及下游缓存', flush=True)
            return
        except ValueError:
            pass
    contract = state.compilation_contract(work)
    expected = state.expected_compilation(work)
    if state.compilation_contract(work) != contract:
        raise ValueError('编译期间输入变化')
    for name, entries in expected.items():
        atomic_write(work / name, state.srt_text(entries))
    state.bind_compilation(work)
    changes = [{'unit_id': number, 'before': previous[number - 1][2] if number <= len(previous) else None,
                'after': entry[2]} for number, entry in enumerate(expected['spoken.srt'], 1)
               if number > len(previous) or previous[number - 1][2] != entry[2]]
    save_json(work / 'normalization_changes.json', {'contract': contract, 'changes': changes,
                                                  'production_audio_modified': False})


def synthesize(work, workers=6):
    import translate_to_zh as pipeline
    state.validate_compilation(work)
    source = state.source_video(work)
    count = len(state.cues(work / 'spoken.srt'))
    selection = work / 'tts_voice.txt'
    voice = selection.read_text().strip() if selection.exists() else None
    if selection.exists() and not voice:
        raise ValueError('tts_voice.txt 不能为空')
    voices_path = work / 'voices.json'
    voices = [voice] * count if voice else state.load(voices_path) if voices_path.exists() else ['zh-CN-YunxiNeural'] * count
    if not voices_path.exists() or state.load(voices_path) != voices:
        save_json(voices_path, voices)
    import speech_groups
    speech_groups.preflight(work)
    if (work / speech_groups.POLICY).exists():
        return speech_groups.synthesize(work, workers)
    if (work / 'narration.json').exists():
        try:
            narration = state.validate_narration(work)
            if narration['origin'] == 'legacy-adopted':
                print('保留已显式接管的旧配音；未追认历史生成来源', flush=True)
                return
            producer = narration['producer']
            engine_version = subprocess.run([str(pipeline.PVT_DIR / '.venv/bin/python'), '-c',
                                             "import importlib.metadata; print(importlib.metadata.version('edge-tts'))"],
                                            capture_output=True, text=True, check=True).stdout.strip()
            expected = state.synthesis_contract(work / 'spoken.srt', voices_path, pipeline.probe_duration(source),
                                                state.QUALITY, engine_version)
            if producer['contract'] == expected:
                print('配音依赖未变化，复用已绑定 WAV', flush=True)
                return
        except ValueError:
            pass
    dependencies = state.narration_dependencies(work)
    duration = pipeline.probe_duration(source)
    pending = work / 'dub_pending.wav'
    pipeline.run([str(pipeline.PVT_DIR / '.venv/bin/python'), str(SCRIPT_DIR / 'dub.py'),
                  str(work / 'spoken.srt'), str(pending), str(work / 'tts_cache'),
                  '--voices', str(voices_path), '--total', str(duration), '--workers', str(workers)],
                 logfile=work / 'tts.log')
    pipeline.verify_dub_tail(pending, duration)
    if state.narration_dependencies(work) != dependencies:
        raise ValueError('合成期间输入变化，未替换已验收配音')
    statistics = state.load(pending.with_suffix('.stats.json'))
    state.check_stats(statistics, count)
    producer = state.load(pending.with_suffix('.provenance.json'))
    destination = work / f'dub_{state.signature(producer["contract"])[:20]}.wav'
    pending.replace(destination)
    producer['waveform'] = state.identity(destination)
    save_json(destination.with_suffix('.provenance.json'), producer)
    save_json(destination.with_suffix('.stats.json'), statistics)
    save_json(work / 'tts_stats.json', statistics)
    save_json(work / 'timing_review.json', [])
    state.bind_narration(work, destination, 'synthesized', producer)
    pending.with_suffix('.stats.json').unlink(missing_ok=True)
    pending.with_suffix('.provenance.json').unlink(missing_ok=True)


def render(work, arguments):
    import translate_to_zh as pipeline
    if (work / 'render_state.json').exists() and state.load(work / 'render_state.json').get('package_manifest'):
        raise ValueError('已有AI导读/章节母版；请先 package-restore 保留版本并恢复正文，再 render → package，拒绝静默移除片头')
    narration = state.validate_narration(work)
    metadata = state.load(work / 'meta.json')
    output = check_videos.output_path(work.parent, metadata['title'])
    with (work.parent / '.zh_render.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pipeline.step_display_subs(work)
        import speech_groups
        speech_groups.update_subtitles(work)
        if output.exists() and (work / 'render_state.json').exists():
            previous = state.load(work / 'render_state.json')
            if state.matches(previous['output']) and previous['dependencies'] != state.render_dependencies(
                    work, Path(narration['waveform']['path']), state.source_video(work)):
                archive = work / 'render_history' / state.signature(previous)[:20]
                archive.mkdir(parents=True, exist_ok=True)
                destination = archive / output.name
                if not destination.exists():
                    shutil.copy2(output, destination)
                if state.digest(destination) != previous['output']['sha256']:
                    raise ValueError('旧母版归档校验失败，拒绝覆盖')
        pipeline.step_mux(state.source_video(work), Path(narration['waveform']['path']), work, output,
                          arguments.subs, arguments.sub_pos, arguments.crf if arguments.crf is not None else 23,
                          '64k', not arguments.keep_en_volume,
                          arguments.max_height if arguments.max_height is not None else 1080,
                          preserve_video=arguments.preserve_video)
        for language in ['zh', 'en']:
            external = output.with_name(output.stem.removesuffix('_中文配音') + f'.{language}.srt')
            atomic_write(external, '\ufeff' + (work / f'{language}_display.srt').read_text(encoding='utf-8-sig'))
    print(output, flush=True)


def adopt(work, identity):
    adoption_path = work / 'adoption.json'
    migration = state.load(adoption_path) if adoption_path.exists() else None
    observed_inputs = [*state.COMPILE_OUTPUTS, 'en.srt', 'translation.tsv', 'spoken_overrides.json',
                       'voices.json', 'tts_voice.txt', 'tts_stats.json', 'timing_review.json', 'source_provenance.json']
    resuming = bool(migration and migration.get('stage') == 'pending')
    if resuming and (migration['inputs'] != state.hashes(work, observed_inputs)
                     or any(not state.matches(observation) for observation in migration['media'].values())):
        raise ValueError('未完成的接管基线已变化，拒绝重新绑定')
    if any((work / name).exists() for name in ['narration.json', 'render_state.json']):
        if not resuming:
            state.validate_render(work, check_videos.output_path(work.parent, state.load(work / 'meta.json')['title']))
            print(identity, '已有有效绑定，不覆盖现有基线', flush=True)
            return
    for name, entries in state.expected_compilation(work).items():
        if state.cues(work / name) != entries:
            raise ValueError(f'旧工程 {name} 与当前译文/朗读覆盖不符，拒绝接管')
    evidence = state.load(work / 'source_provenance.json')
    raw = Path(evidence['source_file'])
    if not evidence.get('text_preserved') or state.digest(raw) != evidence['source_sha256']:
        raise ValueError('原字幕来源证据不符')
    raw_text = ' '.join(''.join(segment.get('utf8', '') for segment in event.get('segs', [])).strip()
                        for event in state.load(raw)['events'])
    compiled_text = ' '.join(cue[2] for cue in state.cues(work / 'en.srt'))
    if re.sub(r'\s+', ' ', raw_text).strip() != re.sub(r'\s+', ' ', compiled_text).strip():
        raise ValueError('当前英文字幕与原始来源全文不同，不可直接接管')
    record = check_videos.inspect(identity, work.parent, True, allow_legacy=True)
    if record['state'] != 'verified':
        raise ValueError('旧成品未通过技术复验: ' + '；'.join(record['findings']))
    output = Path(record['output'])
    before = {str(path): state.identity(path) for path in [output, work / 'dub_agent.wav', state.source_video(work)]}
    voices = state.load(work / 'voices.json')
    if not isinstance(voices, list) or len(voices) != record['source_units'] or not all(isinstance(voice, str) and voice for voice in voices):
        raise ValueError('旧工程音色列表无效')
    import wave
    with wave.open(str(work / 'dub_agent.wav'), 'rb') as waveform:
        waveform.setpos(min(waveform.getnframes(), int(record['source_duration'] * waveform.getframerate())))
        while True:
            tail = waveform.readframes(65536)
            if not tail:
                break
            if any(tail):
                raise ValueError('原片结尾后存在非零语音，不可接管')
    save_json(adoption_path, {'stage': 'pending', 'observed_at': state.now(), 'media': before,
                              'inputs': state.hashes(work, observed_inputs), 'historical_generation_proven': False})
    task_path = work / 'translation_task.json'
    if not task_path.exists():
        save_json(task_path, {'id': identity, 'units': len(state.cues(work / 'en.srt')),
                              'source_sha256': state.digest(work / 'en.srt'), 'origin': 'legacy-adopted',
                              'observed_at': state.now(), 'historical_generation_proven': False})
    state.bind_compilation(work, 'legacy-adopted')
    state.bind_narration(work, work / 'dub_agent.wav', 'legacy-adopted')
    state.bind_render(work, output, work / 'dub_agent.wav', state.source_video(work), None, 'legacy-adopted')
    save_json(work / 'video_cache.json', {'origin': 'legacy-preserved', 'settings': None,
                                         'source': state.identity(state.source_video(work)),
                                         'output': state.identity(output)})
    if any(not state.matches(observation) for observation in before.values()):
        raise ValueError('接管期间媒体发生变化，需复验')
    record.update(translation_inputs_sha256=check_videos.translation_inputs(work),
                  workflow_bindings=state.hashes(work, state.BINDINGS),
                  provenance_origin='legacy-adopted', historical_generation_proven=False, source_task_bound=True)
    save_json(work / 'delivery_check.json', record)
    save_json(adoption_path, {'stage': 'complete', 'observed_at': state.now(), 'media': before,
                              'inputs': state.hashes(work, observed_inputs), 'historical_generation_proven': False})
    print(identity, '仅建立当前观察基线；未重跑媒体，未证明历史生成过程', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'compile', 'tts', 'render', 'adopt', 'status', 'verify', 'review',
                                         'review-import', 'speech-review', 'speech-plan', 'speech-preview',
                                         'quality-check', 'quality-resolve', 'release', 'upload', 'upload-preview', 'upload-verify',
                                         'speech-group-plan', 'speech-group-import', 'package', 'package-restore'])
    parser.add_argument('inputs', nargs='*', help='YouTube 链接或 ID；以 - 开头的 ID 请传完整链接')
    parser.add_argument('-o', '--out-dir', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--subs', choices=['soft', 'hard', 'none'], default='soft')
    parser.add_argument('--sub-pos', choices=['top', 'bottom'], default='bottom')
    parser.add_argument('--crf', type=int, choices=range(52))
    parser.add_argument('--max-height', type=int)
    parser.add_argument('--preserve-video', action='store_true', help='显式保留已绑定旧成品画面；不追认未知编码参数')
    parser.add_argument('--keep-en-volume', action='store_true')
    parser.add_argument('--feedback', type=Path)
    parser.add_argument('--speech-db', type=Path, help='术语/停顿数据库；默认输出目录下 speech_review.sqlite3')
    parser.add_argument('--skip-audio', action='store_true', help='speech-review 仅扫描术语，不绑定或检查配音')
    parser.add_argument('--normalization', choices=['legacy', 'technical-v2'], help='compile 显式升级朗读规则；旧工程默认保留 legacy')
    parser.add_argument('--preview-limit', type=int, default=6, help='speech-preview 最多生成多少条局部 A/B（1–20）')
    parser.add_argument('--problem', help='quality-resolve 要复验的历史文字问题编号')
    parser.add_argument('--unit', type=int, help='quality-resolve 当前对照单元')
    parser.add_argument('--listened', action='store_true', help='明确登记本人实际听辨，不由自动播放代替')
    parser.add_argument('--note', default='')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--index', type=Path)
    parser.add_argument('--caption-candidate', type=Path, help='upload-preview的隐藏候选目录；upload使用已审验候选')
    parser.add_argument('--caption-terms', type=Path, help='upload-preview使用的显式词表')
    parser.add_argument('--apply-caption-update', action='store_true', help='upload显式应用候选晋升或回滚；默认只预演')
    parser.add_argument('--rollback-upload', type=Path, help='upload回滚本工程的transaction.json，不重编码')
    arguments = parser.parse_args()
    if arguments.workers < 1 or not 1 <= arguments.preview_limit <= 20 or (arguments.max_height is not None and arguments.max_height < 0):
        parser.error('workers 必须为正数，max-height 不得小于零')
    if arguments.preserve_video and (arguments.crf is not None or arguments.max_height is not None or arguments.subs == 'hard'):
        parser.error('--preserve-video 不得与显式画质/分辨率/硬字幕请求混用')
    if not arguments.inputs and arguments.stage not in {'status', 'verify'}:
        parser.error('修改工程时必须显式指定视频 ID 或链接')
    if arguments.stage == 'review-import' and (not arguments.feedback or len(arguments.inputs) != 1):
        parser.error('review-import 需要一个工程和 --feedback 文件')
    if arguments.stage == 'speech-group-import' and (not arguments.feedback or len(arguments.inputs) != 1):
        parser.error('speech-group-import 需要一个工程和 --feedback 文件')
    if arguments.stage == 'package' and len(arguments.inputs) != 1:
        parser.error('package 每次需显式指定一个工程，首次制作还需 --feedback 导读章节计划')
    if arguments.skip_audio and arguments.stage != 'speech-review':
        parser.error('--skip-audio 仅用于 speech-review；不能跳过合成或交付验证')
    if arguments.normalization is not None and arguments.stage != 'compile':
        parser.error('--normalization 仅用于 compile')
    if arguments.stage in {'upload', 'upload-preview'} and (arguments.preserve_video or arguments.max_height is not None
                                         or arguments.subs != 'soft' or arguments.keep_en_volume):
        parser.error('upload 固定烧录中文字幕、唯一中文音轨和原片尺寸；不接受母版专用选项')
    if (arguments.caption_candidate or arguments.caption_terms or arguments.apply_caption_update or arguments.rollback_upload):
        if arguments.stage not in {'upload', 'upload-preview'} or len(arguments.inputs) != 1:
            parser.error('字幕候选选项仅用于单工程upload/upload-preview')
        if arguments.sub_pos != 'bottom' or arguments.crf not in {None, 20}:
            parser.error('当前字幕候选固定bottom与CRF20，不静默忽略显式参数')
        if arguments.rollback_upload and (arguments.caption_candidate or arguments.caption_terms):
            parser.error('回滚与候选制作/晋升不能混用')
        if arguments.stage == 'upload-preview' and (not arguments.caption_candidate or not arguments.caption_terms
                                                       or arguments.apply_caption_update or arguments.rollback_upload):
            parser.error('upload-preview需要--caption-candidate与--caption-terms，不执行晋升或回滚')
        if arguments.stage == 'upload' and (arguments.caption_terms
                or (arguments.apply_caption_update and not (arguments.caption_candidate or arguments.rollback_upload))):
            parser.error('upload仅接收已审验候选或回滚事务；词表用于upload-preview')
    elif arguments.stage == 'upload-preview':
        parser.error('upload-preview需要隐藏候选目录与显式词表')
    if arguments.stage == 'quality-resolve' and (len(arguments.inputs) != 1 or not arguments.problem or arguments.unit is None):
        parser.error('quality-resolve 需要单工程、--problem、--unit、--listened 和 --note')
    if arguments.stage in {'tts', 'render', 'speech-preview'} and importlib.util.find_spec('numpy') is None:
        interpreter = Path(os.environ.get('PVT_DIR', '/vol_1t/pyvideotrans')) / '.venv/bin/python'
        if not interpreter.exists() or interpreter.absolute() == Path(sys.executable).absolute():
            parser.error('tts/render 需要 pyVideoTrans 环境中的 numpy 与 edge-tts')
        return subprocess.run([str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode
    arguments.out_dir = arguments.out_dir.expanduser().resolve()
    if arguments.stage in {'status', 'verify'}:
        command = [sys.executable, str(SCRIPT_DIR / 'check_videos.py'), '-o', str(arguments.out_dir), *arguments.inputs]
        if arguments.stage == 'verify':
            command.append('--verify')
        for option in ['report', 'index']:
            if getattr(arguments, option):
                command += ['--' + option, str(getattr(arguments, option))]
        return subprocess.run(command).returncode
    failed = False
    for value in dict.fromkeys(arguments.inputs):
        try:
            identity = video_id(value)
            work = arguments.out_dir / f'.zh_work_yt_{identity}'
            if arguments.stage == 'prepare':
                work.mkdir(parents=True, exist_ok=True)
            if not work.exists():
                raise ValueError('工程不存在，请先 prepare')
            with (work / '.workflow.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                print(identity, arguments.stage, flush=True)
                if arguments.stage == 'prepare':
                    downloader = next((str(path) for path in [SCRIPT_DIR / 'bin/yt-dlp', SCRIPT_DIR / '.venv/bin/yt-dlp'] if path.exists()), None) or shutil.which('yt-dlp')
                    if not downloader:
                        raise ValueError('找不到 yt-dlp，请先 setup')
                    locked_prepare(identity, arguments.out_dir, downloader)
                elif arguments.stage == 'compile':
                    normalization = arguments.normalization
                    if normalization is None and not (work / 'compilation.json').exists() and not (work / 'speech_policy.json').exists():
                        normalization = 'technical-v2'
                    compile_translation(work, normalization)
                elif arguments.stage == 'tts':
                    synthesize(work, arguments.workers)
                    speech_review.scan(work, arguments.speech_db)
                elif arguments.stage == 'render':
                    render(work, arguments)
                    speech_review.scan(work, arguments.speech_db)
                elif arguments.stage == 'package':
                    import video_package
                    print(video_package.build(work, arguments.feedback, arguments.speech_db)['output']['path'], flush=True)
                elif arguments.stage == 'package-restore':
                    import video_package
                    print(video_package.restore(work)['output']['path'], flush=True)
                elif arguments.stage == 'adopt':
                    adopt(work, identity)
                elif arguments.stage == 'speech-review':
                    speech_review.scan(work, arguments.speech_db, not arguments.skip_audio)
                elif arguments.stage == 'speech-group-plan':
                    import speech_groups
                    print(speech_groups.task(work)[0], flush=True)
                elif arguments.stage == 'speech-group-import':
                    import speech_groups
                    imported = speech_groups.import_task(work, arguments.feedback)
                    print('分组审校已导入；尚需 tts → render → verify，不表示问题已解除', flush=True)
                elif arguments.stage == 'speech-plan':
                    import speech_polish
                    speech_review.scan(work, arguments.speech_db)
                    planned = speech_polish.plan(work)
                    print(f'局部优化候选 {len(planned["cases"])}；{work / "speech_polish_plan.json"}', flush=True)
                elif arguments.stage == 'speech-preview':
                    import asyncio
                    import speech_polish
                    print(asyncio.run(speech_polish.preview(work, arguments.preview_limit)) / 'index.html', flush=True)
                elif arguments.stage == 'upload-verify':
                    import upload_delivery
                    upload_delivery.pending_transactions(work)
                    result = upload_delivery.validate_current(work, database=arguments.speech_db)
                    print(json.dumps({'version': result['version'], 'output': result['output'],
                                      'current_upload_verified': True}, ensure_ascii=False), flush=True)
                elif arguments.stage in {'quality-check', 'release', 'quality-resolve', 'upload', 'upload-preview'}:
                    import delivery_quality
                    if arguments.stage == 'quality-resolve':
                        delivery_quality.resolve_text(work, arguments.problem, arguments.unit, arguments.listened, arguments.note)
                    else:
                        if not (arguments.stage == 'upload' and (arguments.caption_candidate or arguments.rollback_upload)):
                            speech_review.scan(work, arguments.speech_db)
                        if arguments.stage == 'quality-check':
                            report = delivery_quality.evaluate(work, arguments.speech_db)
                            save_json(work / 'delivery_quality.json', report)
                            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
                            failed = failed or not report['allowed']
                        elif arguments.stage == 'release':
                            print(delivery_quality.release(work, arguments.speech_db)['output']['path'], flush=True)
                        elif arguments.stage == 'upload-preview':
                            import upload_export_v2
                            print(upload_export_v2.export_candidate(work, arguments.caption_terms,
                                  arguments.caption_candidate, arguments.speech_db)['output']['path'], flush=True)
                        else:
                            import upload_delivery
                            if arguments.rollback_upload:
                                result = upload_delivery.rollback(work, arguments.rollback_upload, arguments.apply_caption_update,
                                                                  arguments.speech_db)
                            elif arguments.caption_candidate:
                                result = upload_delivery.promote(work, arguments.caption_candidate, arguments.apply_caption_update,
                                                                 arguments.speech_db)
                            else:
                                result = upload_delivery.export(work, arguments.speech_db, arguments.sub_pos,
                                                                arguments.crf if arguments.crf is not None else 20)
                            print(json.dumps(result, ensure_ascii=False) if arguments.caption_candidate or arguments.rollback_upload
                                  else result['output']['path'], flush=True)
                else:
                    import review_videos
                    if arguments.stage == 'review':
                        review_videos.generate(work)
                        speech_review.scan(work, arguments.speech_db)
                    else:
                        review_videos.import_feedback(work, arguments.feedback)
        except (ValueError, OSError, KeyError, TypeError, IndexError, sqlite3.Error, wave.Error, subprocess.SubprocessError) as error:
            failed = True
            print(f'{value}: {error}', file=sys.stderr, flush=True)
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
