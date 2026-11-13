"""从实际v2上传来源和已验证v4格式母版生成独立v6上传候选。"""

import argparse
import fcntl
import os
import shutil
import subprocess
from pathlib import Path

import caption_boundary_upload_v2 as tools
import caption_layout
import delivery_quality
import dubbing_state as state
import paired_delivery_transaction as files
import paired_migration_preflight as source_preflight
import upload_export
import upload_export_v2
import upload_export_v3
import video_package
from prepare_videos import atomic_write, save_json


VERSION = 6
KIND = 'caption-boundary-upload-candidate-v6'
LAYOUT = 'caption-boundary-safe-band-v6'
require = source_preflight.require


def profile(geometry):
    require(geometry == upload_export_v3.geometry(geometry['source_width'], geometry['source_height']),
        '不能缩放、裁切原画或改变字幕安全带')
    return {'layout': LAYOUT, 'crf': 20, 'position': 'added-bottom-band',
        'audio': 'copy-candidate-master-chinese-aac', 'chapters': 'copy-candidate-master', 'geometry': geometry}


def validate_baseline(work, reference, original, manifest_reference):
    import mixed_paired_batch_v3 as batch

    files.checked(reference)
    baseline = state.load(reference['path'])
    files.checked(baseline['configuration'])
    context = batch.load_configuration(baseline['configuration']['path'])
    require(baseline['passed'] is True and baseline['action'] == 'baseline' and baseline['phase'] == 'planned'
        and baseline['implementation'] == state.identity(batch.__file__)
        and baseline['modules'] == batch.module_identities()
        and context['configuration']['phase'] == 'planned' and baseline['both_variants_generated'] == 34
        and baseline['complete_scan'] is True and baseline['production_baseline_for_encoding'] is True
        and len(baseline['videos']) == 40 and baseline['summary']['source_units']
            == baseline['summary']['translated_units'] == 6840
        and baseline['current_versions'] == batch.version_counts(
            entry.get('upload_version', 1) for entry in context['previous']['projects'])
        and [entry['id'] for entry in baseline['projects']] == context['project_ids'],
        '编码必须先由当前v6生产链路完成实际全34部基线')
    source_preflight.assert_same_pairs(baseline['projects'], context['previous']['projects'])
    identifier = work.name.removeprefix('.zh_work_yt_').removeprefix('.zh_work_')
    selected = context['selected'].get(identifier)
    prior = next((entry for entry in baseline['projects'] if entry['id'] == identifier), None)
    require(selected is not None and selected['from'] == 2 and selected['to'] == VERSION and prior is not None
        and prior['master'] == original['master'] and prior['upload'] == original['upload']
        and prior['manifest'] == manifest_reference, '生产基线必须覆盖本次实际v2来源及原正式双版本')
    return context, prior


def validate_layout(preview, master, base, receipt):
    forms = caption_layout.load_forms(preview['inputs']['terms']['path'])
    geometry = preview['layout']['geometry']
    script, layout = upload_export_v3.subtitles(Path(master['work']), geometry['source_width'],
        geometry['source_height'], base['duration_seconds'], forms, Path(master['subtitles']['zh']['path']))
    content = upload_export_v3.verify_script(script, Path(master['subtitles']['zh']['path']), base['duration_seconds'])
    require(script == Path(preview['preview_ass']['path']).read_text()
        and state.signature(layout) == state.signature(preview['layout'])
        and content == preview['script_check'], '上传必须保持最终已审字幕、布局和时间')
    raster = source_preflight.validate_registered_raster(receipt, preview, master['text_preview'],
        script, state.cues(master['subtitles']['zh']['path']), forms)
    for name in ['ass', 'metadata', 'log']:
        files.checked(raster[name])
    return script, layout, content, raster


def export(work, checkpoint_path, directory, baseline_preflight, database=None):
    work, checkpoint_path = [Path(path).resolve(strict=True) for path in [work, checkpoint_path]]
    directory = files.canonical(directory)
    require(not directory.exists() and directory.parent.resolve(strict=True).is_relative_to(
        work.parent / '.workflow_logs'), '需要全新的隐藏上传候选目录')
    with (work / '.workflow.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = video_package.validate(work)
        quality = delivery_quality.require(work, database)
        checkpoint_reference = state.identity(checkpoint_path)
        checkpoint = state.load(checkpoint_path)
        preview = state.load(checkpoint['evidence']['final_preview']['path'])
        baseline_reference = state.identity(baseline_preflight)
        context, prior = validate_baseline(work, baseline_reference,
            {'master': manifest['output'], 'upload': preview['inputs']['upload']},
            preview['inputs']['inner_package_manifest'])
        selected = context['selected'][prior['id']]
        require(selected['preview_checkpoint'] == checkpoint_reference
            and selected['master_candidate'] == checkpoint['evidence']['master_candidate'],
            '当前候选必须属于已完成生产基线的明确迁移')
        source_preflight.validate_candidate(work, selected, prior)
        master_reference = checkpoint['evidence']['master_candidate']
        master = state.load(master_reference['path'])
        require(master['version'] == 4 and master['kind'] == 'caption-boundary-master-candidate-v4',
            '复用母版必须保留实际v4来源格式，不伪装为v6')
        raster_reference = checkpoint['evidence']['final_raster']
        script, layout, content, raster = validate_layout(preview, master, manifest,
            state.load(raster_reference['path']))
        source = Path(master['output']['path'])
        require(shutil.disk_usage(directory.parent).free > 2 * source.stat().st_size + 1024 ** 3, '磁盘不足')
        font_lines = subprocess.run(['fc-match', '-f', '%{family}\n%{file}', 'Noto Sans CJK SC'],
            capture_output=True, text=True, check=True).stdout.splitlines()
        require(len(font_lines) == 2 and 'Noto Sans CJK SC' in font_lines[0], '缺少可核对的CJK字体')
        directory.mkdir()
        candidate, live_path = directory / 'candidate.mp4', directory / 'candidate-live-state.json'
        ass = directory / 'upload-preview.ass'
        atomic_write(ass, script)
        dependencies = {**preview['inputs'], 'checkpoint': checkpoint_reference,
            'master_candidate_result': master_reference, 'master_candidate': master['output'],
            'baseline_preflight': baseline_reference, 'text_preview': master['text_preview'],
            'raster_receipt': raster_reference, 'candidate_ass': state.identity(ass),
            'preview_ass': preview['preview_ass'], 'repaired_chinese_captions': master['subtitles']['zh'],
            'repaired_bilingual_captions': master['subtitles']['bilingual'],
            'candidate_exporter': state.identity(__file__), 'source_preflight': state.identity(source_preflight.__file__),
            'candidate_tools': state.identity(tools.__file__), 'legacy_exporter': state.identity(upload_export.__file__),
            'v2_audio_helper': state.identity(upload_export_v2.__file__),
            'safe_band': state.identity(upload_export_v3.__file__),
            'package_implementation': state.identity(video_package.__file__),
            'quality_implementation': state.identity(delivery_quality.__file__),
            'font': state.identity(Path(font_lines[1])), 'ffmpeg': state.identity(Path(shutil.which('ffmpeg')))}
        live = {'version': VERSION, 'kind': KIND, 'work': str(work), 'created_at': state.now(),
            'owner': tools.process_identity(os.getpid()), 'state': 'preflight-complete',
            'dependencies': dependencies, 'production_applied': False, 'goal_status': 'active'}
        save_json(live_path, live)
        try:
            require(all(state.matches(reference) for reference in dependencies.values()), '编码前输入身份变化')
            command = tools.encode_command(source, candidate, manifest['duration_seconds'], layout['geometry'])
            with (directory / 'candidate-encode.log').open('x') as log:
                process = subprocess.Popen(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT)
                live.update(state='encoding', encoder=tools.process_identity(process.pid), command=command,
                    quality_bindings=quality['bindings'])
                save_json(live_path, live)
                exit_code = process.wait()
            live.update(state='checking-media', encoder_exit_code=exit_code)
            save_json(live_path, live)
            require(exit_code == 0, f'上传候选编码失败，真实出口{exit_code}')
            verification = upload_export.verify(candidate, manifest['duration_seconds'])
            verification['self_contained'] = video_package.verify_media(candidate, manifest, upload=True)
            video = next(stream for stream in upload_export.probe(candidate)['streams'] if stream['codec_type'] == 'video')
            original_video = next(stream for stream in upload_export.probe(source)['streams'] if stream['codec_type'] == 'video')
            geometry = layout['geometry']
            require([video['width'], video['height']] == [geometry['output_width'], geometry['output_height']]
                and video.get('sample_aspect_ratio') == original_video.get('sample_aspect_ratio'), '候选画幅或原画比例变化')
            live.update(state='full-decoding')
            save_json(live_path, live)
            verification['full_decode'] = video_package.full_decode(candidate, directory, 'candidate-full-decode.log')
            live.update(state='checking-chinese-aac-and-bindings')
            save_json(live_path, live)
            master_audio, candidate_audio = [upload_export_v2.audio_digest(path) for path in [source, candidate]]
            require(master_audio == candidate_audio, '上传候选中文AAC与已验证母版不同')
            require(all(state.matches(reference) for reference in dependencies.values())
                and video_package.validate(work) == manifest
                and delivery_quality.require(work, database)['bindings'] == quality['bindings'], '制作期间输入或质量绑定变化')
            result = {'version': VERSION, 'kind': KIND, 'created_at': state.now(), 'work': str(work),
                'dependencies': dependencies, 'output': state.identity(candidate), 'verification': verification,
                'quality': quality, 'layout_report': layout, 'content_check': content,
                'reused_all_cue_raster_check': raster, 'profile': profile(geometry),
                'master_candidate_format_version': 4, 'source_upload_version': 2,
                'master_chinese_aac': master_audio, 'candidate_chinese_aac': candidate_audio,
                'chinese_aac_identical': True, 'production_applied': False, 'current_delivery_unchanged': True,
                'visual_review_required': True, 'original_video_fidelity_pending': True,
                'required_paired_protocol': 'caption-boundary-paired-package-v6', 'installed': False,
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
    parser.add_argument('--baseline-preflight', required=True, type=Path)
    parser.add_argument('--speech-db', type=Path)
    options = parser.parse_args()
    result = export(options.work, options.checkpoint, options.candidate_directory, options.baseline_preflight, options.speech_db)
    print('UPLOAD v6 CANDIDATE COMPLETE', result['output']['path'], '; formal pair unchanged', flush=True)
