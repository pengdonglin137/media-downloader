"""生成与跨条修复母版一致的中文烧录上传候选，正式交付保持不变。"""

import argparse
import fcntl
import math
import os
import shutil
import subprocess
from pathlib import Path

import caption_boundary_master_v3 as master_producer
import caption_boundary_repair_v2 as repair
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


def validate_baseline(work, reference, original, manifest_reference):
    import mixed_paired_batch_v2 as batch
    require(state.matches(reference), '全34基线身份变化')
    baseline = state.load(reference['path'])
    require(state.matches(baseline['configuration']), '全34基线配置身份变化')
    context = batch.load_configuration(baseline['configuration']['path'])
    require(baseline['passed'] and baseline['action'] == 'baseline' and baseline['phase'] == 'planned'
        and baseline['implementation'] == state.identity(batch.__file__) and baseline['modules'] == batch.module_identities()
        and context['configuration']['phase'] == 'planned' and baseline['both_variants_generated'] == 34
        and [entry['id'] for entry in baseline['projects']] == context['project_ids']
        and baseline['current_versions'] == batch.version_counts(entry.get('upload_version', 1)
            for entry in context['previous']['projects'])
        and all(all(actual[key] == prior[key] for key in ['master', 'upload', 'manifest'])
            for actual, prior in zip(baseline['projects'], context['previous']['projects'])),
        '必须由当前独立v5混版实现实际完成全34部基线')
    identifier = work.name.removeprefix('.zh_work_yt_').removeprefix('.zh_work_')
    selected = context['selected'].get(identifier)
    project = next((entry for entry in baseline['projects'] if entry['id'] == identifier), None)
    require(selected is not None and selected['from'] == 1 and selected['to'] == 5 and project is not None
        and project['master'] == original['master'] and project['upload'] == original['upload']
        and project['manifest'] == manifest_reference, '全34基线未覆盖本次明确迁移及原正式双版本')
    return baseline


def validate_entities(entity, preview, original, repaired, forms):
    require(entity['passed'], '完整实体收据未通过')
    extended = sorted(set(forms + entity['explicit_requested_entities']), key=lambda value: (-len(value), value))
    before = repair.original.boundary_matches(original, extended)
    after = repair.original.boundary_matches(repaired, extended)
    boundaries = sorted(set(entry[:2] for entry in before))
    require(entity['explicit_requested_entities'] == ['Steam Deck', 'EEVDF', 'David Dai', 'Netlink']
        and entity['complete_reviewed_forms'] == extended
        and state.signature(entity['before_literal_matches']) == state.signature(before)
        and entity['before_literal_match_count'] == len(before)
        and entity['before_affected_boundary_count'] == len(boundaries) == preview['proof']['repaired_boundary_count']
        and state.signature(entity['before_affected_boundaries']) == state.signature(boundaries)
        and not after and not entity['after_literal_matches'] and entity['all_487_ass_rows_checked']
        and entity['extended_forms_rebuild_exact_same_ass'], '完整实体、边界或ASS复核无法重建')
    return extended


def export(work, checkpoint_path, directory, baseline_preflight, database=None):
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
        require(checkpoint['state'] == 'caption_boundary_v2_text_roles_and_master_remux_verified_upload_protocol_pending'
            and checkpoint['passed'] and not checkpoint['production_mutated'], '需要实际v98角色/显示域与母版候选断点')
        references = checkpoint['evidence']
        for key in ['master_candidate', 'text_preview', 'text_and_raster', 'visual_review',
                    'contracts', 'role_registry', 'source_frame_review', 'complete_entity_proof']:
            require(state.matches(references[key]), '候选断点证据变化: ' + key)
        verified = repair.verify_preview(work, references['text_preview']['path'])
        preview = verified['preview']
        master = state.load(references['master_candidate']['path'])
        require(master['work'] == str(work) and master['version'] == 5 and master['kind'] == master_producer.KIND
            and master['implementation'] == state.identity(master_producer.__file__)
            and master['reused_implementations'] == master_producer.implementations()
            and master['candidate_only'] is True and master['production_applied'] is False
            and master['original_master'] == manifest['output']
            and master['text_preview'] == references['text_preview'] and state.matches(master['output'])
            and all(state.matches(reference) for reference in master['subtitles'].values()), '母版候选来源无效')
        entities = state.load(references['complete_entity_proof']['path'])
        require(entities['preview_proof'] == references['text_preview'], '完整实体收据绑定其他预览')
        forms = validate_entities(entities, preview, verified['original'], verified['repaired'], verified['forms'])
        baseline_reference = state.identity(baseline_preflight)
        validate_baseline(work, baseline_reference, {'master': manifest['output'],
            'upload': preview['inputs']['upload']}, preview['inputs']['inner_package_manifest'])


        source = Path(master['output']['path'])
        media = upload_export.probe(source)
        video = next(stream for stream in media['streams'] if stream['codec_type'] == 'video')
        duration = manifest['duration_seconds']
        script, report = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
            duration, forms, Path(master['subtitles']['zh']['path']))
        require(script == Path(preview['preview_ass']['path']).read_text()
            and report == preview['layout'], '上传与已审图/内嵌母版文字或样式不一致')
        content = upload_export_v3.verify_script(script, Path(master['subtitles']['zh']['path']), duration)
        raster = validate_raster(state.load(references['text_and_raster']['path']),
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
            'baseline_preflight': baseline_reference, 'complete_entity_proof': references['complete_entity_proof'],
            'text_preview': references['text_preview'], 'raster_receipt': references['text_and_raster'],
            'candidate_ass': state.identity(ass), 'preview_ass': preview['preview_ass'],
            'repaired_chinese_captions': master['subtitles']['zh'],
            'repaired_bilingual_captions': master['subtitles']['bilingual'],
            'candidate_exporter': state.identity(__file__), 'layout_implementation': state.identity(caption_layout.__file__),
            'v3_layout_implementation': state.identity(upload_export_v3.__file__),
            'audio_helper': state.identity(upload_export_v2.__file__), 'package_verifier': state.identity(video_package.__file__),
            'quality_implementation': state.identity(delivery_quality.__file__), 'font': state.identity(Path(font_lines[1])),
            'ffmpeg': state.identity(Path(shutil.which('ffmpeg')))}
        live = {'version': 5, 'kind': 'caption-boundary-upload-candidate-v5', 'work': str(work),
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
            result = {'version': 5, 'kind': 'caption-boundary-upload-candidate-v5', 'created_at': state.now(),
                'work': str(work), 'dependencies': dependencies, 'output': state.identity(candidate),
                'verification': verification, 'quality': quality, 'layout_report': report, 'content_check': content,
                'reused_all_cue_raster_check': raster, 'profile': {'layout': 'caption-boundary-safe-band-v5',
                    'crf': 20, 'position': 'added-bottom-band', 'audio': 'copy-candidate-master-chinese-aac',
                    'chapters': 'copy-candidate-master', 'geometry': geometry},
                'master_chinese_aac': master_audio, 'candidate_chinese_aac': candidate_audio, 'chinese_aac_identical': True,
                'production_applied': False, 'current_delivery_unchanged': True, 'visual_review_required': True,
                'original_video_fidelity_pending': True, 'required_paired_protocol': 'caption-boundary-paired-package-v5', 'installed': False,
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
    parser.add_argument('--baseline-preflight', required=True, type=Path)
    arguments = parser.parse_args()
    result = export(arguments.work, arguments.checkpoint, arguments.candidate_directory, arguments.baseline_preflight, arguments.speech_db)
    print('UPLOAD CANDIDATE COMPLETE', result['output']['path'], '; formal pair unchanged')
