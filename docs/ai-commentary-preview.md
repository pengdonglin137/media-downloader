# 明确AI归属的完整句试读

## 为什么单独提供入口

正式源角色试读器`media_workflow.speech_variants`要求当前正式译文、角色核验及渲染基线。没有这些条件时，不应伪造正式工程来试读，也不能把混合自动字幕直接送TTS。

`media_workflow.commentary_preview`只接受明确标识的AI补充说明。技术纠正可以独立试读，但不冒充原主持的话、正式正文、已安装插段或声音批准。旧试读器及98个冻结根Python不改；新模块不依赖未提交的runner／`__init__`。

## 使用方法

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
RUN=/home/pengdl/Videos/translated_backlog/.workflow_logs/request-0sZlaQr3e2w-20261114/checkpoint-commentary-v1
OUT=/home/pengdl/Videos/translated_backlog/.workflow_logs/request-0sZlaQr3e2w-20261114/commentary-previews-v1
$PY -m media_workflow.commentary_preview bind data/translation_reviews/0sZlaQr3e2w-ai-commentary-20261114.json --output "$RUN/bound-plan.json"
$PY -m media_workflow.commentary_preview validate "$RUN/bound-plan.json"
$PY -m media_workflow.commentary_preview preview "$RUN/bound-plan.json" --output "$OUT"
$PY -m media_workflow.commentary_preview check "$RUN/bound-plan.json" --output "$OUT"
```

上例已经成功执行。续做只运行最后的`check`或读取收据；不要重复前三步、删缓存或覆盖既有目录。新请求使用自己的作者计划、新绑定文件及新隔离目录。部分失败清单及成功缓存保留，本版不提供自动恢复／重启。

## 配置与权威边界

- 作者计划`kind=ai-commentary-editorial-plan-v1`包含规范工程路径、相对计划目录解析的事实审校／参考文件、明确中文声音、正常速率`+0%`、≤40秒独立时段及1–16个完整候选。绑定计划另记作者稿、原片、`en.srt`／`translation_input.tsv`／`translation_task.json`／`preparation.json`的字节、mtime与SHA。
- 每条保存审校类别、源ID集合、原字幕起点、完整显示／口播、理由、语气意图和语义约束。口播必须以“AI补充说明。”开头，显示必须以“AI补充说明（非讲者原话）：”开头。格式检查不证明纠正事实为真；作者仍须逐项审校。
- `source_anchor`精确绑定原字幕起点，仅表示拟暂停原片插入AI说明的位置。没有把它当成真实角色交接或词级对齐，也不强迫新说明塞进原字幕窗。插入画面、独立时段、原片恢复点及交付时间映射尚未实现／验证。
- 多个候选可以属于同一事实类别与同一锚点；它们是对照，不是要全部插入视频。无自动选胜、全局词典更新、数据库决定或源角色批准。
- 使用冻结`speech_polish.synthesize_sample`实际联网生成完整MP3及WordBoundary；WAV为全量单声道PCM16／24kHz解码。不裁首尾、不调速、不拼字片段、不对候选做隐式响度匹配。
- `check`重新解码完整MP3并对照PCM样本／摘要，校验来源、代码、文字、真实词界与未批准标志；即使篡改WAV及收据使其内部一致，一采样点截尾仍拒绝。WordBoundary与作者词流相符，不表示实际音素、自然度、重音或情感正确。
- 源工程仅共享只读锁，输出只能是与工程分离的新目录；绑定文件拒绝覆盖。本模块不验证调用者所有其他成片均未变，生产保护还须独立基线复核。

## 2026-11-14 实跑

`0sZlaQr3e2w`F01–F12十二类纠正均已完整句试读，另四条比较术语原词／字母与中文解释、平直说明与反问表达。全部使用`zh-CN-YunxiNeural`正常速率，16条16.152–21.264秒，共295.368秒；这个总数是候选音频总时长，不是已插入的片长。

| 全句对照 | 第一条／第二条秒数 | 未决范围 |
| --- | --- | --- |
| 负载平直解释／反问解释 | 18.336／19.824 | 措辞不同，不把时长差独立归因于标点或情感。 |
| nice原词／中文权重释义 | 16.848／19.656 | 不宣称原词读音正确，显示仍保留PRI／NI／nice。 |
| OOM／SSH字母展开／中文解释 | 20.040／18.936 | 都明确纠正原讲方向；可懂度及语气未试听。 |
| free／available原词／中文解释 | 16.632／18.144 | 屏幕仍保留两个表头，不靠省掉标识冒称等义批准。 |

38输入合同检查含37拒绝、20输出检查含19拒绝，另有namespace CLI验证；bind／preview／check与工具会话32932均真实0。16条完整MP3／WAV／词界身份通过，规范化词界文本无缺项，词界无越解码尾界；模拟检查不写真实输出，也不是人耳批准。

断点：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-0sZlaQr3e2w-20261114/checkpoint-commentary-v1/execution-progress.json`。作者计划、实际manifest、逐项词界／间隔观察、输入／输出有限检查、namespace CLI及独立保护核验分别留收据。143原身份含98根Python、旧源稿、旧试读器与已安装cji媒体保持。

正式工程仍`awaiting_translation`。没有中文正文TTS、原角色审定、ASR、视频编码或实际AI插段；441隔离稿不得直接生产。下一步仍是原始完整意群／角色重建、未覆盖声音处理和明确AI局部画面／时间计划，再按实际声音核验专业发音与情感；四对候选及所有最终质量门禁未批准，目标active。
