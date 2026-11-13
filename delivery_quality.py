"""正式交付门禁；技术验收、已确认问题和人工全片验收分别记录。"""

from pathlib import Path

import dubbing_state as state
import speech_review
from prepare_videos import save_json


def bindings(work):
    narration = state.validate_narration(work)
    return {'inputs': state.hashes(work, [*state.BINDINGS, 'spoken.srt', 'en.srt']),
            'waveform': narration['waveform'], 'source': state.identity(state.source_video(work))}


def text_problems(work):
    manifest_path = work / 'review_manifest.json'
    manifest = state.load(manifest_path) if manifest_path.exists() else {}
    current_revision = manifest.get('review_revision')
    current_bindings = state.hashes(work, ['compilation.json', 'narration.json', 'render_state.json', 'translation_audit.md'])
    current = manifest.get('bindings') == current_bindings
    problems = []
    for path in sorted(work.glob('review_feedback_*.json')):
        reply = state.load(path)
        if reply.get('id') != work.name.removeprefix('.zh_work_yt_'):
            raise ValueError('复核历史属于其他工程')
        for item in reply['feedback']:
            if item['text_status'] == 'needs_fix':
                problems.append({'problem_id': state.signature({'feedback': str(path.resolve()),
                                 'revision': reply['review_revision'], 'unit_id': item['unit_id']})[:24],
                                 'kind': 'text', 'unit_id': item['unit_id'], 'note': item['note'],
                                 'current': current and reply['review_revision'] == current_revision,
                                 'feedback_path': str(path), 'feedback_sha256': state.digest(path)})
    receipts_path = work / 'quality_resolutions.json'
    receipts = state.load(receipts_path) if receipts_path.exists() else []
    if not isinstance(receipts, list) or any(not isinstance(receipt, dict) or not isinstance(receipt.get('note'), str)
                                             for receipt in receipts):
        raise ValueError('历史问题复验记录格式无效')
    expected = bindings(work)
    return [problem for problem in problems if problem['current'] or not any(
        receipt.get('problem_id') == problem['problem_id'] and receipt.get('bindings') == expected
        and receipt.get('feedback_sha256') == problem['feedback_sha256']
        and receipt.get('listened') is True and receipt.get('note', '').strip()
        for receipt in receipts)]


def evaluate(work, database=None):
    snapshot = bindings(work)
    speech = speech_review.status(work, database)
    blockers = []
    if speech['state'] in {'stale', 'not_generated'} or not speech.get('audio_scanned'):
        blockers.append({'kind': 'missing_speech_evidence', 'note': '需按当前 WAV 完成 speech-review，且数据库必须可核对'})
    blockers.extend({'kind': 'speech', **item} for item in speech.get('confirmed_problems', []))
    if speech['state'] not in {'stale', 'not_generated'} and speech.get('audio_scanned'):
        import speech_groups
        blockers.extend(speech_groups.quality_blockers(work, state.load(work / 'speech_review.json'),
                        speech_review.database_path(work, database)))
    blockers.extend(text_problems(work))
    if bindings(work) != snapshot:
        raise ValueError('质量检查期间输入发生变化')
    return {'version': 1, 'checked_at': state.now(), 'bindings': snapshot,
            'allowed': not blockers, 'state': 'blocked' if blockers else 'eligible_with_unreviewed_candidates',
            'blockers': blockers, 'speech': speech, 'full_human_listening': False,
            'scope': '已确认问题及高风险停顿复核门禁；候选不自动判错，不代表语言质量或全片人耳验收'}


def require(work, database=None):
    report = evaluate(work, database)
    if not report['allowed']:
        details = '；'.join(item.get('note', item['kind']) for item in report['blockers'][:5])
        raise ValueError('交付门禁未通过：' + details)
    return report


def resolve_text(work, problem_id, unit_id, listened, note):
    if not listened or not isinstance(note, str) or not note.strip():
        raise ValueError('解除历史问题需实际对照听辨和具体复验说明')
    if type(unit_id) is not int or not 1 <= unit_id <= len(state.cues(work / 'spoken.srt')):
        raise ValueError('当前复验单元编号无效')
    problem = next((item for item in text_problems(work) if item['problem_id'] == problem_id), None)
    if problem is None or problem['current']:
        raise ValueError('历史问题不存在；当前文字问题须从当前复核页重新导入反馈')
    path = work / 'quality_resolutions.json'
    receipts = state.load(path) if path.exists() else []
    receipt = {'problem_id': problem_id, 'unit_id': unit_id, 'bindings': bindings(work),
               'feedback_sha256': problem['feedback_sha256'], 'listened': True, 'note': note, 'reviewed_at': state.now()}
    receipts.append(receipt)
    save_json(path, receipts)
    return receipt


def release(work, database=None):
    import check_videos
    report = require(work, database)
    project = state.load(work / 'meta.json')
    output = check_videos.output_path(work.parent, project['title'])
    render = state.validate_render(work, output)
    record = check_videos.inspect(work.name.removeprefix('.zh_work_yt_'), work.parent, True)
    if record['state'] != 'verified':
        raise ValueError('成品技术验收未通过：' + '；'.join(record.get('findings', [])))
    final = require(work, database)
    if final['bindings'] != report['bindings'] or not state.matches(render['output']):
        raise ValueError('交付检查期间成品或输入发生变化')
    result = {'version': 1, 'created_at': state.now(), 'output': state.identity(output),
              'quality': final, 'technical_state': record['state'], 'full_human_listening': False}
    save_json(work / 'release.json', result)
    return result
