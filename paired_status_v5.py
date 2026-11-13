"""独立v5基础状态路由，不更改旧检查器或缓存。"""

from pathlib import Path

import check_videos
import dubbing_state as state
import paired_delivery_transaction as files
import paired_delivery_v5 as pairs
from review_videos import review_status
from speech_review import status as speech_status


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stable_quality(quality):
    return {key: value for key, value in quality.items() if key != 'checked_at'}


def inspect_v5(work, expected, source_record, runtime_reference):
    current = pairs.validate_current(work)
    require({**current, 'quality': stable_quality(current['quality'])}
            == {**expected, 'quality': stable_quality(expected['quality'])}, '正式v5包与已验收的34部表不符')
    manifest = state.load(current['manifest']['path'])
    rendering = state.validate_render(work, Path(current['master']['path']))
    state.validate_compilation(work)
    narration = state.validate_narration(work)
    originals = state.cues(work / 'en.srt')
    translations = state.cues(work / 'zh.srt')
    spoken = state.cues(work / 'spoken.srt')
    require(len(originals) == len(translations) == len(spoken) == source_record['source_units'],
            'v5源/译稿/朗读条数变化')
    require(all(original[:2] == translated[:2] == speech[:2]
                for original, translated, speech in zip(originals, translations, spoken)), 'v5时间窗不符')
    task = state.load(work / 'translation_task.json')
    require(task['source_sha256'] == state.digest(work / 'en.srt'), 'v5原文任务绑定变化')
    translated_units = check_videos.translations(work / 'translation.tsv', len(originals))
    require(translated_units is not None and all(translated_units[index] == cue[2]
                for index, cue in enumerate(translations, 1)), 'v5译文TSV与当前字幕不符')
    stats = state.load(work / 'tts_stats.json')
    state.check_stats(stats, state.narration_count(work))
    require(state.load(work / 'timing_review.json') == [], 'v5配音时窗仍有问题')
    require(stable_quality(source_record['quality_gate']) == stable_quality(current['quality']),
            'v5质量门禁与当前源译稿扫描不符')
    files.checked(runtime_reference)
    runtime = state.load(runtime_reference['path'])
    require(runtime['passed'] and runtime['transaction'] == current['transaction']
            and runtime['package_manifest'] == current['manifest'], '正式路径VLC收据不属于当前版本')
    return {'id': current['id'], 'work': str(work), 'state': 'verified', 'findings': [],
        'title': state.load(work / 'meta.json')['title'], 'source_units': len(originals),
        'translated_units': len(translations), 'source_task_bound': True,
        'provenance_origin': narration['origin'],
        'historical_generation_proven': narration['historical_generation_proven'],
        'tts_summary': {key: value for key, value in stats.items() if key != 'per'},
        'output': current['master']['path'], 'output_fingerprint': state.fingerprint(Path(current['master']['path'])),
        'output_identity': current['master'], 'source_fingerprint': state.fingerprint(state.source_video(work)),
        'source_identity': state.identity(state.source_video(work)), 'output_duration': manifest['duration_seconds'],
        'track_counts': manifest['verification']['tracks'],
        'self_contained': {'body_offset_seconds': current['intro_seconds'], 'chapters': current['chapters'],
                           'manifest': current['manifest'], 'package_version': 5},
        'render_state': state.identity(work / 'render_state.json'),
        'verification_kind': 'fresh-v5-dependencies-tracks-subtitles-chapters-plus-retained-exact-decode-proof',
        'fresh_full_decode_performed': False, 'fresh_tts_performed': False,
        'retained_decode_basis': manifest['decode_basis'],
        'fresh_canonical_path_vlc': runtime_reference, 'transaction': current['transaction'],
        'legacy_delivery_check_reused_as_current': False,
        'review': review_status(work), 'speech_review': speech_status(work), 'quality_gate': current['quality'],
        'full_human_listening': False, 'potplayer_tested': False,
        'verification_checked_at': state.now()}

