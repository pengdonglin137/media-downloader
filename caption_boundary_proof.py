"""核验真实v2预览，仅规范化旧证明中无语义的多命中集合顺序。"""

import json
from pathlib import Path

import caption_boundary_repair_v2 as repair
import dubbing_state as state
import upload_export
import upload_export_v3
import video_package


require = repair.require


def canonical_proof(proof):
    result = json.loads(json.dumps(proof, ensure_ascii=False, allow_nan=False))
    require(isinstance(result, dict) and isinstance(result.get('reviews'), list), '缺少完整逐项审校证明')
    for review in result['reviews']:
        matches = review.get('resolved_literal_matches')
        require(isinstance(matches, list) and bool(matches)
            and all(isinstance(match, list) and len(match) == 5
                and all(type(value) is int for value in match[:2])
                and all(isinstance(value, str) for value in match[2:]) for match in matches),
            '登记命中集合结构无效')
        require(len(matches) == len({tuple(match) for match in matches}), '登记命中不能重复')
        expected = [match for match in result['before_literal_matches']
            if match[:2] == [review['left_cue_id'], review['right_cue_id']]]
        require(sorted(matches) == sorted(expected), '逐项命中必须精确覆盖原边界全部命中')
        review['resolved_literal_matches'] = sorted(matches)
    return result


def verify_preview(work, preview_path):
    work, preview_path = [Path(path).resolve(strict=True) for path in [work, preview_path]]
    preview_reference = state.identity(preview_path)
    record = state.load(preview_path)
    require(record['version'] == repair.VERSION == 2 and record['kind'] == repair.KIND
        and record['work'] == str(work) and record['implementation'] == state.identity(repair.__file__)
        and record['reused_implementations'] == repair.implementations()
        and record['preview_only'] is True and record['production_applied'] is False
        and state.matches(record['plan']) and state.matches(record['preview_captions'])
        and state.matches(record['preview_ass']), '真实v2预览版本、实现或输出身份失效')
    manifest = video_package.validate(work)
    inputs, forms, original, repaired, proof = repair.validate_plan(work,
        Path(record['inputs']['terms']['path']), Path(record['plan']['path']), manifest)
    require(inputs == record['inputs'] and canonical_proof(proof) == canonical_proof(record['proof'])
        and state.cues(record['preview_captions']['path']) == repaired
        and Path(record['preview_captions']['path']).read_text() == state.srt_text(repaired),
        '角色、原声音窗、完整命中集合或逐字字幕无法精确重建')
    video = next(stream for stream in upload_export.probe(Path(inputs['master']['path']))['streams']
        if stream['codec_type'] == 'video')
    script, layout = upload_export_v3.subtitles(work, int(video['width']), int(video['height']),
        manifest['duration_seconds'], forms, Path(record['preview_captions']['path']))
    require(script == Path(record['preview_ass']['path']).read_text() and layout == record['layout']
        and upload_export_v3.verify_script(script, Path(record['preview_captions']['path']),
            manifest['duration_seconds']) == record['script_check'] and state.matches(preview_reference),
        '完整ASS、显示布局或绑定预览无法确定性重建')
    return {'preview': record, 'preview_reference': preview_reference, 'manifest': manifest,
        'original': original, 'repaired': repaired, 'forms': forms,
        'proof_validation': {'implementation': state.identity(__file__),
            'legacy_preview_format_version': 2, 'canonical_proof_signature': state.signature(canonical_proof(proof)),
            'only_order_normalized': '/reviews/*/resolved_literal_matches',
            'all_members_and_multiplicities_checked': True, 'all_other_fields_compared_exactly': True}}
