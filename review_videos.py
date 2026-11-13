"""离线风险复核：候选提示不等于错误判定，文字与听辨分别登记。"""

import html
import json
import os
import re
from urllib.parse import quote

import dubbing_state as state
from prepare_videos import atomic_write, save_json

REVIEW_FILES = [*state.BINDINGS, 'translation_audit.md']
TEXT_STATUSES = {'unreviewed', 'confirmed', 'needs_fix'}


def audit_candidates(notes, count):
    candidates = {}
    for note in notes:
        citations = re.findall(r'(?:单元|编号|尤其|第)\s*([\d、,/–—\- ]+)', note)
        leading = re.match(r'^\s*(?:[-*]\s*)?(?:\|\s*)?(\d{1,4}(?:\s*[–—-]\s*\d{1,4})?)(?=\s|\|)', note)
        if leading:
            citations.append(leading[1])
        for citation in citations:
            for first, last in re.findall(r'(\d{1,4})(?:\s*[–—-]\s*(\d{1,4}))?', citation):
                start, end = int(first), int(last or first)
                if 1 <= start <= end <= count:
                    for number in range(start, end + 1):
                        if note not in candidates.setdefault(number, []):
                            candidates[number].append(note)
    return candidates


def review_status(work):
    manifest_path = work / 'review_manifest.json'
    summary = {'text_reviewed': 0, 'listened_units': 0, 'issues': 0,
               'full_human_listening': False, 'state': 'not_generated',
               'text_audit_record_exists': (work / 'translation_audit.md').exists()}
    if not manifest_path.exists():
        return summary
    manifest = state.load(manifest_path)
    if manifest['bindings'] != state.hashes(work, REVIEW_FILES):
        return {**summary, 'state': 'stale'}
    feedback = work / f'review_feedback_{manifest["review_revision"]}.json'
    records = state.load(feedback)['feedback'] if feedback.exists() else []
    return {**summary, 'state': 'partial' if records else 'awaiting_review',
            'text_reviewed': sum(record['text_status'] == 'confirmed' for record in records),
            'listened_units': sum(record['listened'] for record in records),
            'issues': sum(record['text_status'] == 'needs_fix' for record in records),
            'total_units': len(manifest['units']), 'risk_units': manifest['risk_units']}


def build_manifest(work):
    import check_videos
    narration = state.validate_narration(work)
    output = check_videos.output_path(work.parent, state.load(work / 'meta.json')['title'])
    delivery = state.validate_render(work, output)
    english = state.cues(work / 'en.srt')
    chinese = state.cues(work / 'zh.srt')
    spoken = state.cues(work / 'spoken.srt')
    stats = state.load(work / 'tts_stats.json')
    import speech_groups
    effective = speech_groups.units(work)
    grouped = {number: (entry, timing) for entry, timing in zip(effective, stats['per'])
               for number in entry['unit_ids']}
    audit = work / 'translation_audit.md'
    audit_risks = [line.strip() for line in audit.read_text().splitlines()
                   if re.search(r'存疑|不确定|误识别|歧义|未决|待.{0,8}核|听辨|听不清|无法确认', line)] if audit.exists() else []
    cited_notes = audit_candidates(audit_risks, len(english))
    units = []
    for number, (original, translated, speech) in enumerate(zip(english, chinese, spoken), 1):
        group, timing = grouped[number]
        if len(group['unit_ids']) > 1:
            speech = (group['start'], group['end'], group['spoken'])
        risks = []
        if len(group['unit_ids']) > 1:
            risks.append('共享完整语义朗读组：' + '、'.join(map(str, group['unit_ids'])))
        if timing[0] > 0:
            risks.append(f'配音提速 +{timing[0]}%')
        if re.search(r'\d', original[2]):
            risks.append('数字/单位需对照')
        if re.search(r"\b(?:not|never|no|unless|without|cannot|can.t|only|except)\b", original[2], re.I):
            risks.append('否定/限制条件')
        if re.search(r'\b[A-Z]{2,}\b|\w+::\w+|\w+_\w+|\w+\(\)|C\+\+', original[2]):
            risks.append('缩写/代码标识符')
        if speech[2] != translated[2]:
            risks.append('字幕/朗读差异')
        notes = cited_notes.get(number, [])
        if notes:
            risks.append('审校存疑引用（待人工对照）')
        end = max(original[1], english[number][0] - 0.05) if number < len(english) else original[1]
        unit = {'unit_id': number, 'start': original[0], 'end': end, 'en': original[2],
                'zh': translated[2], 'spoken': speech[2], 'risks': risks, 'rate': timing[0],
                'priority': (100 if notes else 0) + timing[0] + len(risks), 'audit_notes': notes}
        if len(group['unit_ids']) > 1:
            unit.update(start=group['start'], end=group['end'], source_start=original[0], source_end=original[1],
                        speech_unit_ids=group['unit_ids'], group_zh=group['zh'])
        unit['unit_revision'] = state.signature({'unit': unit, 'source': delivery['dependencies']['source']['sha256'],
                                                 'waveform': narration['waveform']['sha256'],
                                                 'output': delivery['output']['sha256']})
        units.append(unit)
    manifest = {'version': 1, 'id': work.name.removeprefix('.zh_work_yt_'),
                'bindings': state.hashes(work, REVIEW_FILES), 'units': units,
                'audit_risks': audit_risks, 'risk_units': sum(bool(unit['risks']) for unit in units),
                'media': {'original': str(state.source_video(work)), 'dubbed': narration['waveform']['path']},
                'technical_state': check_videos.inspect(work.name.removeprefix('.zh_work_yt_'), work.parent, False)['state']}
    manifest['review_revision'] = state.signature({key: value for key, value in manifest.items() if key != 'technical_state'})
    return manifest


def generate(work):
    manifest = build_manifest(work)
    existing_path = work / f'review_feedback_{manifest["review_revision"]}.json'
    manifest['feedback'] = state.load(existing_path)['feedback'] if existing_path.exists() else []
    save_json(work / 'review_manifest.json', manifest)
    title = state.load(work / 'meta.json')['title']
    sources = {kind: quote(os.path.relpath(path, work)) for kind, path in manifest['media'].items()}
    data = json.dumps(manifest, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    data = data.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    document = PAGE.replace('__TITLE__', html.escape(title)).replace('__ORIGINAL__', html.escape(sources['original'], quote=True))
    document = document.replace('__DUBBED__', html.escape(sources['dubbed'], quote=True)).replace('__DATA__', data)
    atomic_write(work / 'risk_review.html', document)
    print(work / 'risk_review.html', flush=True)


def import_feedback(work, path):
    current = build_manifest(work)
    reply = state.load(path)
    if not isinstance(reply, dict) or reply.get('id') != current['id'] or reply.get('review_revision') != current['review_revision']:
        raise ValueError('反馈已过期或属于其他工程；请从当前风险页重新复核')
    records = reply.get('feedback')
    if not isinstance(records, list) or len(records) > len(current['units']):
        raise ValueError('反馈列表无效')
    units = {unit['unit_id']: unit for unit in current['units']}
    validated = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('反馈条目必须为 JSON 对象')
        number = record.get('unit_id')
        if type(number) is not int or number not in units or number in validated:
            raise ValueError('反馈编号无效或重复')
        if record.get('unit_revision') != units[number]['unit_revision']:
            raise ValueError(f'单元 {number} 的内容/音频身份不符')
        if (record.get('text_status') not in TEXT_STATUSES or type(record.get('listened')) is not bool
                or not isinstance(record.get('note'), str) or len(record['note']) > 10000):
            raise ValueError('反馈状态或说明无效')
        validated[number] = {key: record[key] for key in ['unit_id', 'unit_revision', 'text_status', 'listened', 'note']}
    destination = work / f'review_feedback_{current["review_revision"]}.json'
    previous = state.load(destination)['feedback'] if destination.exists() else []
    merged = {record['unit_id']: record for record in previous}
    merged.update(validated)
    save_json(destination, {'id': current['id'], 'review_revision': current['review_revision'],
                            'imported_at': state.now(), 'feedback': [merged[number] for number in sorted(merged)],
                            'full_human_listening': False, 'translations_modified': False})
    generate(work)
    print(f'已导入 {len(validated)} 条反馈；未自动修改译文或宣称全片试听通过', flush=True)


PAGE = '''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>风险复核 — __TITLE__</title>
<style>body{font:16px/1.6 system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem}video{max-width:100%;max-height:360px}
audio{width:100%}.players{position:sticky;top:0;background:#fff;padding:.5rem;border-bottom:1px solid #ccc;z-index:1}
article{padding:1rem;border-bottom:1px solid #ccc}button,select,input,textarea{font:inherit;margin:.3rem}
textarea{width:95%;height:4rem}.risk{color:#974300}pre{white-space:pre-wrap}small{color:#555}</style>
<h1>__TITLE__ — 风险复核</h1>
<p>三个状态独立：技术验收不代表译文正确；已有文字审校记录不等于每个候选已确认；局部听辨不等于全片试听。</p>
<p id="summary"></p><p>候选仅为复核提示，不自动判错。对照原片音频与独立中文 WAV，不依赖浏览器多音轨切换。</p>
<details><summary>审校记录中的存疑提示（需结合原文核对）</summary><pre id="audit"></pre></details>
<div class="players"><video id="original" controls preload="metadata" src="__ORIGINAL__"></video>
<audio id="dubbed" controls preload="metadata" src="__DUBBED__"></audio>
<div><label><input id="context" type="checkbox" checked>前后各 2 秒语境</label>
<button id="export">导出版本绑定反馈</button><small>导出后用 review-import 导入；未导入的浏览器草稿不计入验收。</small></div></div>
<label>筛选 <select id="filter"><option value="risk">风险候选</option><option value="all">全部单元</option>
<option value="unreviewed">尚未复核</option><option value="issues">需修订</option></select></label>
<label>查找 <input id="search" type="search"></label><div id="units"></div>
<script id="review-data" type="application/json">__DATA__</script>
<script>
'use strict';
const manifest=JSON.parse(document.getElementById('review-data').textContent);
const storageKey='dubbing-review-'+manifest.review_revision;
let feedback=Object.fromEntries(manifest.feedback.map(record=>[record.unit_id,record]));
try {const saved=JSON.parse(localStorage.getItem(storageKey)||'{}');feedback={...feedback,...saved};}catch(error){}
const original=document.getElementById('original'), dubbed=document.getElementById('dubbed');
document.getElementById('audit').textContent=manifest.audit_risks.join('\\n')||'审校记录未提取到存疑提示；这不是自动正确性证明。';
function saveDraft(){try{localStorage.setItem(storageKey,JSON.stringify(feedback));}catch(error){}updateSummary();}
function updateSummary(){
 const records=Object.values(feedback);
 document.getElementById('summary').textContent='技术状态：'+manifest.technical_state+'；文字已确认 '+records.filter(record=>record.text_status==='confirmed').length+'/'+manifest.units.length+'；局部听辨 '+records.filter(record=>record.listened).length+' 段；全片试听未验收。';
}
function status(unit){return feedback[unit.unit_id]||{unit_id:unit.unit_id,unit_revision:unit.unit_revision,text_status:'unreviewed',listened:false,note:''};}
function play(player,unit){
  original.pause();dubbed.pause();const context=document.getElementById('context').checked?2:0;
  const begin=()=>{player.currentTime=Math.max(0,unit.start-context);player.play().catch(()=>{});};
  player.ontimeupdate=()=>{if(player.currentTime>=unit.end+context)player.pause();};
  if(player.readyState)begin();else player.addEventListener('loadedmetadata',begin,{once:true});
}
original.addEventListener('play',()=>dubbed.pause());dubbed.addEventListener('play',()=>original.pause());
function element(tag,text){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;}
function render(){
 const filter=document.getElementById('filter').value,query=document.getElementById('search').value.toLowerCase();
 const container=document.getElementById('units');container.replaceChildren();
 updateSummary();
 const selected=manifest.units.filter(unit=>{
  const record=status(unit);
  return !(filter==='risk'&&!unit.risks.length||filter==='issues'&&record.text_status!=='needs_fix'||filter==='unreviewed'&&record.text_status==='confirmed'&&record.listened)&&
   (!query||[unit.unit_id,unit.en,unit.zh,unit.spoken].join(' ').toLowerCase().includes(query));
 }).sort((left,right)=>right.priority-left.priority||left.unit_id-right.unit_id);
 for(const unit of selected){
  const record=status(unit),card=element('article');card.id='unit-'+unit.unit_id;
  card.append(element('h3','#'+unit.unit_id+' · '+unit.start.toFixed(2)+'–'+unit.end.toFixed(2)+' 秒'));
  card.append(element('p','原文：'+unit.en),element('p','中文：'+unit.zh),element('p','朗读：'+unit.spoken));
  const risk=element('p',unit.risks.join('；')||'无规则提示，仍未自动验收');risk.className='risk';card.append(risk);
  if((unit.audit_notes||[]).length)card.append(element('pre',unit.audit_notes.join('\\n')));
  for(const [player,label] of [[original,'听原声'],[dubbed,'听中文']]){const button=element('button',label);button.onclick=()=>play(player,unit);card.append(button);}
  const select=element('select');for(const [value,label] of [['unreviewed','文字待核'],['confirmed','文字已核'],['needs_fix','需修订']]){
   const option=element('option',label);option.value=value;select.append(option);}select.value=record.text_status;
  select.onchange=()=>{feedback[unit.unit_id]={...status(unit),text_status:select.value};saveDraft();};card.append(select);
  const label=element('label'),listened=element('input');listened.type='checkbox';listened.checked=record.listened;
  listened.onchange=()=>{feedback[unit.unit_id]={...status(unit),listened:listened.checked};saveDraft();};label.append(listened,document.createTextNode('本段已听原声/中文对照'));card.append(label);
  const note=element('textarea');note.placeholder='具体疑点、建议、对照理由（不会自动覆盖译文）';note.value=record.note;note.maxLength=10000;
  note.oninput=()=>{feedback[unit.unit_id]={...status(unit),note:note.value};saveDraft();};card.append(note);container.append(card);
 }
}
document.getElementById('export').onclick=()=>{
 const content={id:manifest.id,review_revision:manifest.review_revision,feedback:Object.values(feedback)};
 const url=URL.createObjectURL(new Blob([JSON.stringify(content,null,2)],{type:'application/json'}));
 const anchor=element('a');anchor.href=url;anchor.download=manifest.id+'.repairs.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
document.getElementById('filter').onchange=render;document.getElementById('search').oninput=render;render();
</script></html>
'''
