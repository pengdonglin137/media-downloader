"""v14正式包的独立基础状态路由，旧检查器保持冻结。"""

from pathlib import Path

import check_videos
import dubbing_state as state
import paired_delivery_transaction as files
import paired_delivery_v14 as pairs
from review_videos import review_status
from speech_review import status as speech_status


require = pairs.require


def stable_quality(quality):
    return {name: value for name, value in quality.items() if name != 'checked_at'}


def inspect_v14(work, expected, source_record, runtime_reference):
    current = pairs.validate_current(work)
    require({**current, 'quality': stable_quality(current['quality'])}
        == {**expected, 'quality': stable_quality(expected['quality'])}, '正式v14与当前全批身份不符')
    manifest = state.load(current['manifest']['path'])
    state.validate_render(work, Path(current['master']['path']))
    state.validate_compilation(work)
    narration = state.validate_narration(work)
    originals, translations, spoken = [state.cues(work / name) for name in ['en.srt', 'zh.srt', 'spoken.srt']]
    require(len(originals) == len(translations) == len(spoken) == source_record['source_units']
        and all(original[:2] == translated[:2] == speech[:2]
            for original, translated, speech in zip(originals, translations, spoken)), 'v14来源条数或原时间窗变化')
    require(state.load(work / 'translation_task.json')['source_sha256'] == state.digest(work / 'en.srt'), 'v14源任务绑定变化')
    translated_units = check_videos.translations(work / 'translation.tsv', len(originals))
    require(translated_units is not None and all(translated_units[index] == cue[2]
        for index, cue in enumerate(translations, 1)), 'v14译文TSV与当前字幕不符')
    stats = state.load(work / 'tts_stats.json')
    state.check_stats(stats, state.narration_count(work))
    require(state.load(work / 'timing_review.json') == []
        and stable_quality(source_record['quality_gate']) == stable_quality(current['quality']), 'v14质量或声音时窗不符')
    files.checked(runtime_reference)
    runtime = state.load(runtime_reference['path'])
    require(runtime['passed'] is True and runtime['transaction'] == current['transaction']
        and runtime['package_manifest'] == current['manifest'], 'v14正式路径VLC收据不属于当前包')
    return {'id': current['id'], 'work': str(work), 'state': 'verified', 'findings': [],
        'title': state.load(work / 'meta.json')['title'], 'source_units': len(originals),
        'translated_units': len(translations), 'source_task_bound': True,
        'provenance_origin': narration['origin'], 'historical_generation_proven': narration['historical_generation_proven'],
        'tts_summary': {name: value for name, value in stats.items() if name != 'per'},
        'output': current['master']['path'], 'output_fingerprint': state.fingerprint(Path(current['master']['path'])),
        'output_identity': current['master'], 'source_fingerprint': state.fingerprint(state.source_video(work)),
        'source_identity': state.identity(state.source_video(work)), 'output_duration': manifest['duration_seconds'],
        'track_counts': manifest['verification']['tracks'],
        'self_contained': {'body_offset_seconds': current['intro_seconds'], 'chapters': current['chapters'],
            'manifest': current['manifest'], 'package_version': 14},
        'render_state': state.identity(work / 'render_state.json'),
        'verification_kind': 'fresh-v14-dependencies-tracks-subtitles-chapters-plus-exact-retained-decode-proof',
        'fresh_full_decode_performed': False, 'fresh_tts_performed': False,
        'retained_decode_basis': manifest['decode_basis'], 'fresh_canonical_path_vlc': runtime_reference,
        'transaction': current['transaction'], 'legacy_delivery_check_reused_as_current': False,
        'review': review_status(work), 'speech_review': speech_status(work), 'quality_gate': current['quality'],
        'full_human_listening': False, 'potplayer_tested': False, 'verification_checked_at': state.now()}
