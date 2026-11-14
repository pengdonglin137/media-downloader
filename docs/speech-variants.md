# 完整意群试读

`media_workflow.speech_variants`是独立的配置驱动试读适配器，不依赖其他新增编排模块；不接管既有正文、特殊原声复合或导读／章节安装。每片写审校JSON、收据与日志，不复制生产脚本。使用已有包含Edge-TTS依赖的媒体Python：

```bash
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.speech_variants preview REVIEWED_PLAN.json --output NEW_DIRECTORY
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.speech_variants check REVIEWED_PLAN.json --output NEW_DIRECTORY
```

## 审校计划

计划必须由Agent对完整原文及上下文审校，不从声音候选列表自动批准或推断说话人。当前格式`kind=complete-utterance-speech-variants-v1`：

| 字段 | 约束 |
| --- | --- |
| `work` | 已有正式工程的绝对规范路径 |
| `inputs` | 按名称绑定`en.srt`、`translation.tsv`、`spoken_overrides.json`、`tts_voice.txt`、`voices.json`、`narration.json`、`render_state.json`完整文件身份 |
| `master` | 当前`render_state.output`的精确媒体身份，不接受旧成片冒充当前基线 |
| `reference_evidence` | 已有来源／实际音频证明的文件身份列表 |
| `voice`、`rate` | 当前正式声音，固定正常速率`+0%` |
| `cases` | 1至12项完整来源单元，可以为同一单元比较不同安全key；每项原窗大于0且不超过40秒 |
| 每项源文 | `unit_id`、完整`source`、`source_start`／`source_end`十进制字符串，以及当前`current_display`／`current_spoken`，必须与工程对应单元逐项相同 |
| 每项审校 | `speaker_review.same_speaker=true`及具体证据、`display`／`spoken`／`reason`、明确非空`preserved_facts`列表 |
| `before_waveform` | 调用方给出的完整原窗参考WAV身份，单声道PCM16／24kHz，长度必须精确等于整个来源窗；其来源真实性仍依赖所附证明，不由此适配器独立重采集证明 |

已有语义组或多人交接须先按真实来源处理，不把多个说话人拼成一次合成。拒绝空白、`…`／`♪`及以`【原声保留`开头的显式占位；其他事件仍须由来源审校排除，不能把这几种文本检查当成完整事件分类器，也不因为标点或疑问句自动确定角色。

## 实际执行与边界

- 调用冻结`speech_polish.synthesize_sample`生成正常速率完整MP3和真实WordBoundary，输出仅在新目录及其独立缓存。复用当前生产声音，不写正式稿件、音频、旧缓存或数据库，不自动选择胜者。
- WAV是完整MP3解码，不做首尾幅度裁剪、atempo、原窗借用或拼接。`check`逐样本摘要对照完整MP3解码、实际词界元数据、当前输入及样本数；保存实际余量，明确标出零余量和超窗。
- 生成成功仅表示试读完成；即使长度正常，也不代表严格生产准入、响度匹配、事实／发音／情感或人耳通过。候选不自动归一到正式成片响度，不将原始候选和正式AAC假称为等响度盲测。
- 使用正式工程共享锁，新输出不得包含或位于正式工程。已有输出一律拒绝重复，部分成功／失败清单保留；本版不提供自动重启或未知中断恢复，不删除已成功缓存来重跑。
- `check`不改已有试读收据；需要保存验证结论，由通用调用器捕获真实标准输出、日志与出口，不以文件存在代替执行证据。

## 已有实证

2026-11-14，`cjiUXZh0ZCo`142／270完整补句候选分别18.456／21.360秒，适配25.109／26.540秒原窗；实际正常合成及完整解码检查均0。35项有限配置／模拟合同检查通过，其中33项拒绝路径，不等于35次媒体制作。正式两版、190声音候选、98根Python及290生产MP3保持；候选未安装、未人耳批准。见`data/translation_reviews/cjiUXZh0ZCo-editorial-followup-20261114.json`。
