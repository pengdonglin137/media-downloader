"""内置术语发现库与配音停顿候选；只审计，不自动改读音或剪静音。"""

import argparse
import bisect
import fcntl
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import wave
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import dubbing_state as state
from prepare_videos import DEFAULT_OUT, atomic_write, save_json, video_id

CATALOG = Path(__file__).with_name('data') / 'speech_terms.json'
PROFILE = {'version': 1, 'noise_db': -40, 'min_silence': 0.45,
           'tail_gap': 0.65, 'max_tail': 1.2, 'long_internal_gap': 1.4,
           'short_fragment_tokens': 6, 'automatic_audio_edit': False}
DECISIONS = {'confirmed_correct', 'needs_fix', 'dismissed'}


def catalog():
    data = state.load(CATALOG)
    if data.get('version') != 1 or not isinstance(data.get('references'), dict):
        raise ValueError('术语库格式或版本无效')
    if any(not isinstance(value, str) or not value.startswith('https://') for value in data['references'].values()):
        raise ValueError('术语库出处必须为 HTTPS 文本地址')
    identifiers, aliases = set(), set()
    for term in data['terms']:
        identifier = term['id']
        if (not isinstance(identifier, str) or not re.fullmatch(r'[a-z][a-z0-9-]+', identifier)
                or identifier in identifiers or not isinstance(term['aliases'], list) or not term['aliases']):
            raise ValueError('术语库编号或别名无效')
        identifiers.add(identifier)
        for key in ['term', 'domain', 'reading', 'context']:
            if not isinstance(term[key], str) or not term[key].strip():
                raise ValueError(f'术语库字段无效: {identifier}/{key}')
        if term['kind'] not in {'dictionary-reference', 'project-style'}:
            raise ValueError(f'术语库来源类型无效: {identifier}')
        if not isinstance(term['references'], list) or any(key not in data['references'] for key in term['references']):
            raise ValueError(f'术语库出处无效: {identifier}')
        if term['kind'] == 'dictionary-reference' and not term['references']:
            raise ValueError(f'字音参考必须提供出处: {identifier}')
        for alias in term['aliases']:
            if not isinstance(alias, str) or not alias.strip() or alias in aliases:
                raise ValueError(f'术语别名重复或无效: {identifier}')
            aliases.add(alias)
    return data


def connect_database(path, readonly=False):
    if not readonly:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=60) if readonly else sqlite3.connect(path, timeout=60)
    try:
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        application = connection.execute('PRAGMA application_id').fetchone()[0]
        if version not in {0, 1, 2} or application not in {0, 1397772886} or (version in {1, 2} and application != 1397772886):
            raise ValueError('发音数据库版本不兼容；未改写数据库')
        if version == 0 and connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
            raise ValueError('目标不是本项目的发音数据库；未改写数据库')
        if readonly:
            if version not in {1, 2}:
                raise ValueError('数据库尚未初始化')
            return connection
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS terms (
                term_id TEXT PRIMARY KEY, term TEXT NOT NULL, domain TEXT NOT NULL,
                reading TEXT NOT NULL, kind TEXT NOT NULL, entry_json TEXT NOT NULL,
                catalog_revision TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scans (
                revision TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                work_path TEXT NOT NULL, created_at TEXT NOT NULL, report_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY, revision TEXT NOT NULL REFERENCES scans(revision));
            CREATE TABLE IF NOT EXISTS occurrences (
                revision TEXT NOT NULL REFERENCES scans(revision),
                term_id TEXT NOT NULL REFERENCES terms(term_id), unit_id INTEGER NOT NULL,
                field TEXT NOT NULL, offset INTEGER NOT NULL, phrase TEXT NOT NULL,
                start REAL NOT NULL, end REAL NOT NULL, context TEXT NOT NULL,
                PRIMARY KEY (revision, term_id, unit_id, field, offset));
            CREATE TABLE IF NOT EXISTS issues (
                revision TEXT NOT NULL REFERENCES scans(revision), issue_id TEXT NOT NULL,
                kind TEXT NOT NULL, unit_id INTEGER NOT NULL, detail_json TEXT NOT NULL,
                PRIMARY KEY (revision, issue_id));
            CREATE TABLE IF NOT EXISTS decisions (
                revision TEXT NOT NULL, issue_id TEXT NOT NULL, status TEXT NOT NULL,
                listened INTEGER NOT NULL, note TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                PRIMARY KEY (revision, issue_id),
                FOREIGN KEY (revision, issue_id) REFERENCES issues(revision, issue_id));
            CREATE INDEX IF NOT EXISTS occurrences_term ON occurrences(term_id);
            CREATE TABLE IF NOT EXISTS repair_resolutions (
                problem_revision TEXT NOT NULL, problem_issue_id TEXT NOT NULL,
                reviewed_revision TEXT NOT NULL REFERENCES scans(revision),
                unit_id INTEGER NOT NULL, note TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                PRIMARY KEY (problem_revision, problem_issue_id, reviewed_revision),
                FOREIGN KEY (problem_revision, problem_issue_id) REFERENCES issues(revision, issue_id));
            PRAGMA application_id=1397772886;
            PRAGMA user_version=2;
        ''')
        return connection
    except Exception:
        connection.close()
        raise


def dependencies(work, with_audio):
    state.validate_compilation(work)
    result = {'project': str(work.resolve()), 'profile': dict(PROFILE), 'audio_scanned': with_audio,
              'catalog_sha256': state.digest(CATALOG), 'implementation_sha256': state.digest(__file__),
              'inputs': state.hashes(work, ['compilation.json', 'en.srt', 'zh.srt', 'spoken.srt', 'meta.json'])}
    if (work / 'speech_groups.json').exists():
        import speech_groups
        speech_groups.load_policy(work)
        result['semantic_groups'] = {'policy_sha256': state.digest(work / 'speech_groups.json'),
                                     'implementation_sha256': state.digest(speech_groups.__file__)}
    if with_audio:
        narration = state.validate_narration(work)
        result['narration_sha256'] = state.digest(work / 'narration.json')
        result['waveform'] = state.identity(Path(narration['waveform']['path']))
    return result


def find_terms(text, terms):
    matches = []
    for term in terms:
        for alias in term['aliases']:
            pattern = re.escape(alias)
            if alias[0].isascii() and alias[0].isalnum():
                pattern = r'(?<![A-Za-z0-9_])' + pattern
            if alias[-1].isascii() and alias[-1].isalnum():
                pattern += r'(?![A-Za-z0-9_])'
            matches.extend((match.start(), match.end(), term['id'], alias) for match in re.finditer(pattern, text))
    accepted, previous_end = [], -1
    for match in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]), item[2])):
        if match[0] >= previous_end:
            accepted.append(match)
            previous_end = match[1]
    return accepted


def issue(kind, number, start, end, text, **details):
    item = {'kind': kind, 'unit_id': number, 'start': round(start, 3), 'end': round(end, 3),
            'text': text, **details, 'automatic_error_verdict': False}
    item['issue_id'] = state.signature(item)[:24]
    return item


def discover(work, terms):
    entries = {field: state.cues(work / f'{field}.srt') for field in ['en', 'zh', 'spoken']}
    counts = {len(value) for value in entries.values()}
    if len(counts) != 1:
        raise ValueError('术语扫描要求中英文与朗读单元对应')
    by_id = {term['id']: term for term in terms}
    occurrences, issues, unknown = [], [], []
    for field, cues in entries.items():
        identifiers = list(range(1, len(cues) + 1))
        if field == 'spoken' and (work / 'speech_groups.json').exists():
            import speech_groups
            grouped = speech_groups.units(work)
            cues = [(item['start'], item['end'], item['spoken']) for item in grouped]
            identifiers = [item['unit_ids'][0] for item in grouped]
        for number, (start, end, text) in zip(identifiers, cues):
            matched = find_terms(text, terms)
            for offset, finish, identifier, phrase in matched:
                occurrences.append({'term_id': identifier, 'unit_id': number, 'field': field,
                                    'offset': offset, 'phrase': phrase, 'start': start, 'end': end, 'context': text})
            if field != 'spoken':
                continue
            for identifier in sorted({match[2] for match in matched}):
                term = by_id[identifier]
                issues.append(issue('pronunciation-term', number, start, end, text,
                                    term_id=identifier, term=term['term'], reading=term['reading'],
                                    reason=term['context'], source_kind=term['kind']))
            seen = set()
            for token in re.finditer(r'(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9+_#.-]+(?![A-Za-z0-9_])', text):
                spelling = token[0].rstrip('.-')
                if (not spelling or spelling in seen or not (re.search(r'[A-Z].*[A-Z]', spelling) or re.search(r'[\d+#]', spelling))
                        or any(token.start() < finish and token.end() > offset for offset, finish, _, _ in matched)):
                    continue
                seen.add(spelling)
                unknown.append({'token': spelling, 'unit_id': number, 'start': start, 'end': end, 'context': text})
                issues.append(issue('unknown-abbreviation', number, start, end, text, token=spelling,
                                    reason='未收录的缩写/字母数字标识符；不猜测读音，不自动加入确认词库'))
    if (work / 'speech_groups.json').exists():
        import speech_groups
        grouped = speech_groups.units(work)
        return [(item['start'], item['end'], item['spoken']) for item in grouped], occurrences, issues, unknown
    return entries['spoken'], occurrences, issues, unknown


def detect_silences(path, duration):
    result = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-nostats', '-i', str(path),
                             '-af', f'silencedetect=noise={PROFILE["noise_db"]}dB:d={PROFILE["min_silence"]}',
                             '-f', 'null', '-'], capture_output=True, text=True, timeout=max(120, duration / 5), check=True)
    pending, silences = None, []
    for event in re.finditer(r'silence_(start|end):\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)', result.stderr):
        instant = max(0, min(duration, float(event[2])))
        if event[1] == 'start':
            pending = instant
        else:
            beginning = pending if pending is not None else 0
            if instant > beginning:
                silences.append((beginning, instant))
            pending = None
    if pending is not None and pending < duration:
        silences.append((pending, duration))
    return silences


def pause_candidates(cues, silences, duration):
    voiced, cursor = [], 0
    for start, end in silences:
        if start > cursor:
            voiced.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        voiced.append((cursor, duration))
    voiced_ends = [span[1] for span in voiced]
    unit_spans = []
    for number, (start, end, _) in enumerate(cues, 1):
        following = cues[number][0] if number < len(cues) else duration
        finish = min(duration, max(end, following - 0.05))
        offset = bisect.bisect_right(voiced_ends, start)
        spans = []
        for left, right in voiced[offset:]:
            if left >= finish:
                break
            beginning, ending = max(left, start), min(right, finish)
            if ending - beginning >= 0.06:
                spans.append((beginning, ending))
        unit_spans.append(spans)
    issues = []
    for number, ((start, end, text), spans) in enumerate(zip(cues, unit_spans), 1):
        for index, (previous, following) in enumerate(zip(spans, spans[1:])):
            gap = following[0] - previous[1]
            tail = following[1] - following[0]
            at_tail = index == len(spans) - 2
            enough_before = sum(right - left for left, right in spans[:index + 1]) >= 1
            if at_tail and enough_before and gap >= PROFILE['tail_gap'] and tail <= PROFILE['max_tail']:
                kind = 'short-tail-after-pause'
                reason = '单元内长静音后仅剩短有声尾段；没有词级对齐，不能断言恰好为最后一两个字'
            elif gap >= PROFILE['long_internal_gap']:
                kind = 'long-internal-pause'
                reason = '单元内部长静音；可能是标点、列举或合成韵律，需试听'
            else:
                continue
            issues.append(issue(kind, number, max(start, previous[1] - 2), min(duration, following[1] + 0.5), text,
                                gap_start=previous[1], gap_end=following[0], gap_seconds=round(gap, 3),
                                tail_seconds=round(tail, 3), reason=reason))
        if number >= len(cues) or not spans or not unit_spans[number]:
            continue
        next_text = cues[number][2]
        token_count = len(re.findall(r'[A-Za-z0-9]+|[\u3400-\u9fff]', next_text))
        gap_start, gap_end = spans[-1][1], unit_spans[number][0][0]
        if (0 < token_count <= PROFILE['short_fragment_tokens'] and gap_end - gap_start >= PROFILE['tail_gap']
                and not re.search(r'[。！？.!?；;][”’"\')）]*\s*$', text)):
            issues.append(issue('cross-unit-fragment', number, max(start, gap_start - 2),
                                min(duration, unit_spans[number][-1][1] + 0.5), text,
                                next_unit_id=number + 1, next_text=next_text, gap_start=gap_start, gap_end=gap_end,
                                gap_seconds=round(gap_end - gap_start, 3),
                                reason='后续短片段可能是前句尾部，却按下一单元绝对时间开始；须对照源文，不能直接移动时间轴'))
    return issues


def persist(connection, report, data, work):
    revision = report['revision']
    with connection:
        for term in data['terms']:
            connection.execute('INSERT INTO terms VALUES (?,?,?,?,?,?,?) ON CONFLICT(term_id) DO UPDATE SET '
                               'term=excluded.term, domain=excluded.domain, reading=excluded.reading, kind=excluded.kind, '
                               'entry_json=excluded.entry_json, catalog_revision=excluded.catalog_revision',
                               (term['id'], term['term'], term['domain'], term['reading'], term['kind'],
                                json.dumps({**term, 'sources': [data['references'][key] for key in term['references']]}, ensure_ascii=False),
                                report['dependencies']['catalog_sha256']))
        connection.execute('INSERT OR IGNORE INTO scans VALUES (?,?,?,?,?)',
                           (revision, report['project_id'], str(work), report['created_at'], json.dumps(report, ensure_ascii=False)))
        connection.execute('INSERT INTO projects VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision',
                           (report['project_id'], revision))
        connection.executemany('INSERT OR IGNORE INTO occurrences VALUES (?,?,?,?,?,?,?,?,?)',
                               [(revision, row['term_id'], row['unit_id'], row['field'], row['offset'], row['phrase'],
                                 row['start'], row['end'], row['context']) for row in report['occurrences']])
        connection.executemany('INSERT OR IGNORE INTO issues VALUES (?,?,?,?,?)',
                               [(revision, row['issue_id'], row['kind'], row['unit_id'], json.dumps(row, ensure_ascii=False))
                                for row in report['issues']])


def write_page(work, report, connection):
    decisions = {row[0]: {'status': row[1], 'listened': bool(row[2]), 'note': row[3]}
                 for row in connection.execute('SELECT issue_id,status,listened,note FROM decisions WHERE revision=?', (report['revision'],))}
    labels = {'pronunciation-term': '术语读音候选', 'unknown-abbreviation': '未收录缩写',
              'short-tail-after-pause': '句尾短段前长停顿', 'long-internal-pause': '句内长停顿',
              'cross-unit-fragment': '跨单元尾部片段'}
    rows = []
    terms = {term['id']: term for term in report['catalog_snapshot']['terms']}
    for item in report['issues']:
        decision = decisions.get(item['issue_id'], {})
        details = item.get('reading', item.get('token', ''))
        if 'gap_seconds' in item:
            details += f' 静音 {item["gap_seconds"]:.2f} 秒'
        references = ''
        if 'term_id' in item:
            term = terms[item['term_id']]
            references = '字音参考' if term['kind'] == 'dictionary-reference' else '项目约定，非唯一标准'
            for reference in term['references']:
                address = html.escape(report['catalog_snapshot']['references'][reference], quote=True)
                references += f' · <a href="{address}" target="_blank" rel="noopener">出处</a>'
        text = item['text'] + (' → ' + item['next_text'] if 'next_text' in item else '')
        rows.append(f'<tr id="issue-{item["issue_id"]}"><td>' + html.escape(labels[item['kind']]) + f'<br>单元 {item["unit_id"]}<br>'
                    f'试听 {item["start"]:.2f}–{item["end"]:.2f} 秒<br>'
                    f'<code>{item["issue_id"]}</code></td><td>{html.escape(text)}<br><strong>{html.escape(details)}</strong>'
                    f'<br><small>{html.escape(item["reason"])}<br>{references}</small></td><td>'
                    f'<button data-start="{item["start"]:.3f}" data-end="{item["end"]:.3f}" data-player="dub">听配音</button> '
                    f'<button data-start="{item["start"]:.3f}" data-end="{item["end"]:.3f}" data-player="source">对照原片</button>'
                    f'<br>{html.escape(decision.get("status", "未复核"))}；听辨：{"是" if decision.get("listened") else "未登记"}'
                    f'<br>{html.escape(decision.get("note", ""))}</td></tr>')
    waveform = report['dependencies'].get('waveform', {}).get('path')
    audio = '<audio id="dub" controls src="' + html.escape(quote(os.path.relpath(waveform, work)), quote=True) + '"></audio>' if waveform else '<span id="no-audio">本次未扫描音频，配音试听不可用。</span>'
    source = html.escape(quote(os.path.relpath(state.source_video(work), work)), quote=True)
    title = html.escape(state.load(work / 'meta.json')['title'])
    atomic_write(work / 'speech_review.html', '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1"><title>术语与停顿复核</title>'
                 '<style>body{font:16px/1.6 system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem}'
                 'td,th{padding:.6rem;border-bottom:1px solid #ccc;text-align:left;vertical-align:top}'
                 'table{width:100%;border-collapse:collapse}small{color:#555}code{font-size:.75rem}'
                 'video{max-width:100%;max-height:360px}button{margin:.2rem}header{background:white;padding:.5rem}</style>'
                 f'<h1>术语与停顿复核</h1><p>{title}</p><p>修订：<code>{report["revision"]}</code></p>'
                 '<p>候选不等于错误。库内读音参考与缩写风格约定分开；没有自动修音、剪静音或全片人耳验收。'
                 '长停顿可能来自合成韵律或字幕分段，需听辨与原文对照。</p>'
                 f'<header>{audio}<br><video id="source" controls preload="metadata" src="{source}"></video>'
                 '<p id="message" role="status"></p></header><table><thead><tr><th>类型 / 编号</th><th>上下文 / 目标读法</th>'
                 '<th>局部对照 / 人工状态</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
                 '<script>let active=null,ending=0;document.querySelectorAll("button[data-player]").forEach(button=>{'
                 'button.addEventListener("click",async()=>{const player=document.getElementById(button.dataset.player);'
                 'if(!player){document.getElementById("message").textContent="本次未提供配音试听";return;}'
                 'if(active)active.pause();active=player;ending=Number(button.dataset.end);'
                 'try{player.currentTime=Number(button.dataset.start);await player.play();'
                 'document.getElementById("message").textContent="正在局部对照；播放不会自动标记为已复核";}'
                 'catch(error){document.getElementById("message").textContent="媒体播放失败："+error.message;}});});'
                 'document.querySelectorAll("audio,video").forEach(player=>player.addEventListener("timeupdate",()=>{'
                 'if(player===active&&player.currentTime>=ending){player.pause();active=null;}}));</script></html>\n')


def scan(work, database=None, with_audio=True):
    work = Path(work).resolve()
    before = dependencies(work, with_audio)
    data = catalog()
    revision = state.signature(before)
    destination = work / 'speech_review.json'
    report = state.load(destination) if destination.exists() else {}
    reused = report.get('version') == 1 and report.get('revision') == revision and report.get('dependencies') == before
    if not reused:
        cues, occurrences, issues, unknown = discover(work, data['terms'])
        silences = []
        if with_audio:
            waveform = Path(before['waveform']['path'])
            with wave.open(str(waveform)) as audio:
                duration = audio.getnframes() / audio.getframerate()
            silences = detect_silences(waveform, duration)
            pauses = pause_candidates(cues, silences, duration)
            if (work / 'speech_groups.json').exists():
                import speech_groups
                grouped = speech_groups.units(work)
                for item in pauses:
                    item['unit_id'] = grouped[item['unit_id'] - 1]['unit_ids'][0]
                    if 'next_unit_id' in item:
                        item['next_unit_id'] = grouped[item['next_unit_id'] - 1]['unit_ids'][0]
                    item['issue_id'] = state.signature({key: value for key, value in item.items() if key != 'issue_id'})[:24]
            issues.extend(pauses)
        priority = {'short-tail-after-pause': 1, 'cross-unit-fragment': 2, 'long-internal-pause': 3,
                    'pronunciation-term': 4, 'unknown-abbreviation': 5}
        issues.sort(key=lambda item: (0 if item.get('term_id') == 'cache-line' else priority[item['kind']], item['unit_id'], item['start']))
        if dependencies(work, with_audio) != before:
            raise ValueError('扫描期间输入发生变化；未替换既有发音报告')
        report = {'version': 1, 'project_id': work.name.removeprefix('.zh_work_yt_'),
                  'revision': revision, 'created_at': state.now(), 'dependencies': before,
                  'catalog_snapshot': data,
                  'occurrences': occurrences, 'unknown_abbreviations': unknown, 'silences': [list(span) for span in silences],
                  'issues': issues, 'full_human_listening': False, 'automatic_audio_edit': False}
    database = Path(database).expanduser().resolve() if database else work.parent / 'speech_review.sqlite3'
    with closing(connect_database(database)) as connection:
        persist(connection, report, data, work)
        if not reused:
            save_json(destination, report)
        write_page(work, report, connection)
    save_json(work / 'speech_database.json', {'path': str(database)})
    counts = {kind: sum(item['kind'] == kind for item in report['issues']) for kind in sorted({item['kind'] for item in report['issues']})}
    print(f'{report["project_id"]} 发音候选 {json.dumps(counts, ensure_ascii=False)}；数据库 {database}', flush=True)
    return report


def database_path(work, database=None):
    if database is not None:
        return Path(database).expanduser().resolve()
    pointer = Path(work) / 'speech_database.json'
    return Path(state.load(pointer)['path']).resolve() if pointer.exists() else Path(work).parent / 'speech_review.sqlite3'


def status(work, database=None):
    path = work / 'speech_review.json'
    if not path.exists():
        return {'state': 'not_generated', 'full_human_listening': False}
    try:
        report = state.load(path)
        if (report['revision'] != state.signature(report['dependencies'])
                or report['dependencies'] != dependencies(work, report['dependencies']['audio_scanned'])):
            return {'state': 'stale', 'full_human_listening': False}
        result = {'state': 'awaiting_review', 'revision': report['revision'],
                'term_units': sum(item['kind'] == 'pronunciation-term' for item in report['issues']),
                'unknown_abbreviations': len(report['unknown_abbreviations']),
                'pause_candidates': sum(item['kind'] in {'short-tail-after-pause', 'long-internal-pause', 'cross-unit-fragment'}
                                        for item in report['issues']),
                'audio_scanned': report['dependencies']['audio_scanned'], 'full_human_listening': False}
        with closing(connect_database(database_path(work, database), readonly=True)) as connection:
            stored = connection.execute('SELECT report_json,work_path FROM scans WHERE revision=?', (report['revision'],)).fetchone()
            if (stored is None or Path(stored[1]).resolve() != Path(work).resolve()
                    or state.signature(json.loads(stored[0])) != state.signature(report)):
                raise ValueError('发音报告与数据库记录不一致')
            decisions = {row[0]: row[1] for row in connection.execute(
                'SELECT issue_id,status FROM decisions WHERE revision=?', (report['revision'],))}
            problems = connection.execute(
                'SELECT decisions.revision,decisions.issue_id,issues.unit_id,decisions.note FROM decisions '
                'JOIN scans USING(revision) JOIN issues USING(revision,issue_id) '
                'WHERE scans.project_id=? AND scans.work_path=? AND decisions.status="needs_fix"',
                (report['project_id'], str(Path(work).resolve()))).fetchall()
            has_resolutions = connection.execute("SELECT 1 FROM sqlite_master WHERE name='repair_resolutions'").fetchone()
            resolved = set(connection.execute('SELECT problem_revision,problem_issue_id FROM repair_resolutions '
                                              'WHERE reviewed_revision=?', (report['revision'],))) if has_resolutions else set()
            unresolved = [{'revision': revision, 'issue_id': identifier, 'unit_id': number, 'note': note,
                           'current': revision == report['revision']}
                          for revision, identifier, number, note in problems
                          if revision == report['revision'] or (revision, identifier) not in resolved]
        result.update(reviewed_candidates=len(decisions), unreviewed_candidates=len(report['issues']) - len(decisions),
                      confirmed_problems=unresolved, needs_fix=sum(item['current'] for item in unresolved),
                      pending_recheck=sum(not item['current'] for item in unresolved))
        if result['needs_fix']:
            result['state'] = 'needs_fix'
        elif result['pending_recheck']:
            result['state'] = 'pending_recheck'
        elif not result['unreviewed_candidates']:
            result['state'] = 'reviewed_candidates' if report['issues'] else 'no_candidates'
        return result
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error):
        return {'state': 'stale', 'full_human_listening': False}


def review(work, database, revision, identifier, decision, listened, note):
    if decision not in DECISIONS or not note.strip() or (decision == 'confirmed_correct' and not listened):
        raise ValueError('复核需有效状态与说明；确认发音正确必须登记实际听辨')
    current = status(work, database)
    if current.get('revision') != revision:
        raise ValueError('发音报告已经过期；请按当前音频与词库重新复核')
    if not current.get('audio_scanned') and listened:
        raise ValueError('纯术语扫描未绑定配音，不能登记已听辨')
    with closing(connect_database(database)) as connection:
        if connection.execute('SELECT 1 FROM issues WHERE revision=? AND issue_id=?', (revision, identifier)).fetchone() is None:
            raise ValueError('候选编号不属于当前修订')
        previous = connection.execute('SELECT status FROM decisions WHERE revision=? AND issue_id=?', (revision, identifier)).fetchone()
        if previous and previous[0] == 'needs_fix' and decision != 'needs_fix' and not listened:
            raise ValueError('解除已确认问题必须登记当前音频的实际听辨')
        with connection:
            connection.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?) ON CONFLICT(revision,issue_id) DO UPDATE SET '
                               'status=excluded.status,listened=excluded.listened,note=excluded.note,reviewed_at=excluded.reviewed_at',
                               (revision, identifier, decision, int(listened), note, state.now()))
        write_page(work, state.load(work / 'speech_review.json'), connection)


def resolve_problem(work, database, revision, previous_revision, identifier, unit_id, listened, note):
    current = status(work, database)
    if (current.get('revision') != revision or not current.get('audio_scanned') or not listened
            or not isinstance(note, str) or not note.strip()):
        raise ValueError('复验需当前音频修订、实际听辨及具体说明')
    if type(unit_id) is not int or not 1 <= unit_id <= len(state.cues(work / 'spoken.srt')):
        raise ValueError('当前复验单元编号无效')
    if previous_revision == revision:
        raise ValueError('当前修订的问题请直接用 review 复核，不能跳过当前候选')
    with closing(connect_database(database)) as connection:
        problem = connection.execute('SELECT scans.work_path,decisions.status FROM decisions JOIN scans USING(revision) '
                                     'WHERE revision=? AND issue_id=?', (previous_revision, identifier)).fetchone()
        if not problem or Path(problem[0]).resolve() != work.resolve() or problem[1] != 'needs_fix':
            raise ValueError('旧问题不存在、不属于本工程或未被确认为需修订')
        with connection:
            connection.execute('INSERT OR REPLACE INTO repair_resolutions VALUES (?,?,?,?,?,?)',
                               (previous_revision, identifier, revision, unit_id, note, state.now()))
        write_page(work, state.load(work / 'speech_review.json'), connection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['scan', 'query', 'review', 'resolve'])
    parser.add_argument('inputs', nargs='*')
    parser.add_argument('-o', '--out-dir', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--skip-audio', action='store_true')
    parser.add_argument('--query', default='')
    parser.add_argument('--limit', type=int, default=30)
    parser.add_argument('--revision')
    parser.add_argument('--issue')
    parser.add_argument('--previous-revision')
    parser.add_argument('--unit', type=int)
    parser.add_argument('--decision', choices=sorted(DECISIONS))
    parser.add_argument('--listened', action='store_true')
    parser.add_argument('--note', default='')
    arguments = parser.parse_args()
    if arguments.limit < 1:
        parser.error('limit 必须为正数')
    root = arguments.out_dir.expanduser().resolve()
    database = arguments.database.expanduser().resolve() if arguments.database else root / 'speech_review.sqlite3'
    try:
        if arguments.action == 'query':
            if not database.is_file():
                raise ValueError('数据库尚未生成，请先 scan')
            with closing(connect_database(database, readonly=True)) as connection:
                for term, reading, domain, count in connection.execute(
                    'SELECT terms.term,terms.reading,terms.domain,COUNT(occurrences.term_id) FROM terms '
                    'LEFT JOIN occurrences ON terms.term_id=occurrences.term_id AND occurrences.field="spoken" '
                    'AND occurrences.revision IN (SELECT revision FROM projects) '
                    'WHERE instr(terms.term,?) OR instr(terms.domain,?) OR instr(terms.reading,?) '
                    'GROUP BY terms.term_id ORDER BY COUNT(occurrences.term_id) DESC,terms.term_id LIMIT ?',
                    (arguments.query, arguments.query, arguments.query, arguments.limit)):
                    print(f'{term}\t{reading}\t{domain}\t朗读命中 {count}')
                if arguments.query:
                    locations = connection.execute(
                        'SELECT scans.project_id,occurrences.unit_id,occurrences.start,occurrences.phrase '
                        'FROM occurrences JOIN scans USING(revision) JOIN terms USING(term_id) '
                        'WHERE occurrences.field="spoken" AND revision IN (SELECT revision FROM projects) '
                        'AND (instr(terms.term,?) OR instr(occurrences.phrase,?) OR instr(terms.domain,?)) '
                        'ORDER BY scans.project_id,occurrences.unit_id,occurrences.offset LIMIT ?',
                        (arguments.query, arguments.query, arguments.query, arguments.limit))
                    for identity, number, start, phrase in locations:
                        print(f'位置\t{identity}\t单元 {number}\t{start:.3f} 秒\t{phrase}')
                unknown = connection.execute('SELECT issues.detail_json,scans.project_id FROM issues JOIN scans USING(revision) '
                                             'WHERE kind="unknown-abbreviation" AND revision IN (SELECT revision FROM projects)').fetchall()
                displayed = 0
                for detail, identity in unknown:
                    item = json.loads(detail)
                    if arguments.query in item['token']:
                        print(f'待收录\t{item["token"]}\t{identity}\t单元 {item["unit_id"]}')
                        displayed += 1
                        if displayed >= arguments.limit:
                            break
            return 0
        if not arguments.inputs or (arguments.action in {'review', 'resolve'} and
                (len(arguments.inputs) != 1 or not arguments.revision or not arguments.issue)):
            parser.error('scan 需显式工程；review 还需单工程、revision、issue、decision 与 note')
        if arguments.action == 'review' and not arguments.decision:
            parser.error('review 需要 --decision')
        if arguments.action == 'resolve' and (not arguments.previous_revision or arguments.unit is None):
            parser.error('resolve 需要 --previous-revision 和当前 --unit')
        for value in dict.fromkeys(arguments.inputs):
            work = root / f'.zh_work_yt_{video_id(value)}'
            if not work.is_dir():
                raise ValueError(f'工程不存在: {work}')
            with (work / '.workflow.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if arguments.action == 'scan':
                    scan(work, database, not arguments.skip_audio)
                elif arguments.action == 'resolve':
                    resolve_problem(work, database, arguments.revision, arguments.previous_revision,
                                    arguments.issue, arguments.unit, arguments.listened, arguments.note)
                else:
                    review(work, database, arguments.revision, arguments.issue, arguments.decision, arguments.listened, arguments.note)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError, wave.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
