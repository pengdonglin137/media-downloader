# 编辑后的字幕投影与当前音频质量

本页接续`editorial-stage.md`：两个共享适配器把已经验证的完整意群编辑变成可供下一步封装使用的显示字幕、显式旁白来源和真实音频扫描。不修改98个冻结根Python，不复制单片生产／恢复／诊断脚本，不安装正式媒体。

## 字幕投影

```bash
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.caption_projection prepare CAPTIONS.json --output NEW_CAPTIONS
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.caption_projection check CAPTIONS.json --output EXISTING_CAPTIONS
```

配置`kind`为`whole-unit-word-boundary-projection-v1`，绑定`stage_config`、`stage_manifest`、精确归档的`baseline_package`、该包三个`baseline_subtitles`身份和实际`intro_offset_seconds`。每个实际编辑都须提供唯一`unit_id/key`、具体`review_note`及`chunks`；每个短句有`zh/spoken/en`。

- 中文和朗读分句连接后必须逐字等于采用的完整试读，英文分句必须保持完整来源词流。分句是显示投影，不是重新分割或合成声音；源字幕原有ASR词面不会因此被悄悄修正。
- 分界只能落在真实WordBoundary词起点，不得拆开已观测合成词。保留原单元首尾；内部边界按实际TTS偏移映射，采用毫秒精度，不添加atempo或改动来源窗。
- 新中文／双语字幕只替换实际编辑窗内的旧显示项，所有其他文本和时间保持；整条英文字幕字节保持。另输出去掉真实导读偏移的三个正文`*_display.srt`，不把编译长源句当作显示分句。
- 原字幕先精确归档，新的字幕和清单在独立目录。既有成功或中断目录不能覆盖，也不自动重试未知中断。

真实词界并不证明人耳可懂度、中文音素与画面逐词对齐、英文原声音素对齐或所有数量／术语原子性。英文软字幕本身保持；新的双语英文短句是人工配置的来源语义投影，不冒充原声强制对齐。烧字前仍需排版预检及实际画面审查。

## 当前音频扫描

```bash
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.editorial_quality prepare QUALITY.json --output NEW_QUALITY
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.editorial_quality check QUALITY.json --output EXISTING_QUALITY
```

配置`kind`为`derived-complete-utterance-editorial-quality-v1`；绑定上游`stage_config/stage_manifest`、`projection_config/projection_manifest`、当前基线`database`及`baseline_speech_report`。`review_files`必须完整列出原工程现有`meta.json`、`translation_audit.md`、`review_manifest.json`、`quality_resolutions.json`和`review_feedback_*.json`中实际存在的文件，不能通过遗漏历史问题获得准入。

新工程保留任务basename，复制已经核验的源译、朗读、声音、统计、显示及审校输入。`package-plan.json`只重新绑定当前来源哈希，导读内容、声音、章节和原计划其余字段保持，不重新生成已成功导读。新`editorial-derivation.json`绑定全部上游与复制内容；旁白增加显式依赖，`historical_generation_proven:false`。裸冻结旁白验证器必须拒绝，不将旧全片生成证据追认为编辑后音频的生成者。

`narration_context(work)`仅在显式作用域内为这个精确工程接入新来源与规则适用性，其他工程仍走冻结验证器；正常退出和异常都恢复原函数，拒绝嵌套或混入未知补丁。上下文入口重新核验完整上游字幕／音频链路。它不是允许任意WAV通过的通用绕过器，也不是155专用旧运行时的别名。

### 数据库和声音候选

只读打开原数据库，使用SQLite一致性备份创建私库。保存全部七张表、schema、application ID和版本；新扫描后逐行核验旧扫描、词项、问题、决定与修复历史保留。仅允许新扫描／词项／候选追加，以及当前项目指针更新；不允许新决定、伪造修复、旧条目改写或无关项目指针变化。未知表直接拒绝。原项目历史有`needs_fix`时本版拒绝继续，不能靠换工程路径躲过已确认问题。

实际调用冻结`speech_review.scan(..., with_audio=True)`扫描编辑后的整个当前复合WAV，不借用旧报告或只扫描两段预览。未编辑单元的当前候选逐项保持；编辑单元的旧候选仍完整留在历史扫描中，不冒称它们仍是新修订的当前候选。

对于已登记原声占位的高风险短尾，逐项核验原窗、0dB源区、绝对样本、基础TTS全零、复合音频等于实际源PCM及显式EOF静音。只把这些候选分类为“不适用合成尾句规则”，调用冻结门禁时使用报告副本；实际报告／数据库候选不删除，不写dismiss、人耳决定或角色／原声停顿质量批准。其他短尾、跨单元及所有已确认问题仍受原门禁约束。

`quality-evaluation.json`的`eligible_with_unreviewed_candidates`是当前音频预检，不是成片准入或声音批准。此时没有`render_state.json`，其质量绑定为null。新视频产生后必须重新评估当前render／媒体依赖；不能直接复用本次预渲染质量结果或抄旧成功release。

## 2026-11-14 实跑

- `cjiUXZh0ZCo`142／270分别投影为6／7个完整短句，40.2秒导读偏移保持，其他字幕文本／时间及整条英文字幕字节保持。两个补句仍仅在候选工程，不在正式成片中。
- 当前完整复合WAV真实扫描为191候选：189个未编辑单元候选逐项保持，编辑单元原1项保留于旧扫描，新当前2项是142的CPU术语与SPEC缩写；不是两个声音错误诊断，也未经听辨。
- 12个实际原声短尾仅分类为不适用合成尾句规则。私库从83扫描／3713候选／6842词项增加至84／3904／7163；41术语、60项目、0决定及0修复解决保持，全部旧行保留。原私库和正式工程不写入。
- 45项有限配置／词界／源PCM／数据库历史／作用域检查通过，含36拒绝；不称全面测试或人耳／情感／发音通过。

实际配置和日志位于`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/`，新字幕为`editorial-captions-v1`，新工程和扫描为`editorial-delivery-v1`。其工程内`speech_review.html`可供后续实际听辨；生成页面并不代表已试听。正式双版仍是155的SPEC修订v4；视频生成、正式质量重绑与保留历史的安装仍待完成，目标保持active。
