# 完整意群编辑暂存

后续进度：预渲染字幕投影与当前音频扫描已接入共享适配器，见`editorial-quality.md`。本页以下保留该暂存阶段自身的契约；正式视频生成、渲染后质量绑定及安装仍未完成。

`media_workflow.editorial_stage`把已审校、已成功试读的完整单元编译到隔离工程，复用完整MP3，只做冻结响度处理和原窗内零填充。它不联网合成、不编码视频、不安装正式成片，也不写声音复核数据库。

```bash
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.editorial_stage prepare CONFIG.json --output NEW_STAGE
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.editorial_stage check CONFIG.json --output EXISTING_STAGE
```

输出必须在正式工程之外且尚不存在。失败保留`manifest.json`的真实错误；不自动续做未知中断、不覆盖失败或成功暂存。`check`只读重验已有暂存，不重新合成。此子模块只依赖已跟踪的`media_workflow.speech_variants`及既有根模块，不要求其他尚未提交的编排模块。

## 配置与基线

配置为JSON，`kind`固定为`complete-utterance-editorial-stage-v1`：

- `work`：正式工程的规范绝对路径；暂存保留其basename，以满足冻结翻译任务的来源绑定。
- `preview_plan`、`preview_manifest`：成功完整意群试读的文件身份，均含`path/bytes/mtime_ns/sha256`。`prepare`调用试读检查器，绑定当前整项源文、译文、声音、旁白、母版及真实词界。
- `baseline_files`：绑定`en.srt`、`translation.tsv`、`spoken_overrides.json`、`translation_task.json`、`tts_voice.txt`、`voices.json`、`tts_stats.json`、`timing_review.json`、`zh.srt`、`spoken.srt`、`bilingual.srt`、`compilation.json`、`narration.json`、`render_state.json`；存在`speech_policy.json`时也必须绑定。
- `authority`：`runtime`、`receipt`、`exit`三个文件身份，分别指向显式基线检查器、该次JSON检查结果和真实出口文件。结果必须绑定当前来源、文本、旁白和母版，出口必须为0。配置不会执行任何给定脚本或命令；外部基线检查的事实范围仍由原收据决定。
- `edits`：1–12项，按试读`key`选择。每项有具体`reason`、`source_fidelity_selected:true`、`human_listened:false`、`pronunciation_or_emotion_approved:false`。同一源单元只能选一个版本。这是源译完整性选择，不是听辨赢家。

首版支持未分组、已有显式原声合成证据的基线。`narration.producer`须提供`base_tts_waveform`、`original_source_pcm`、`original_source_ranges`、`original_source_unit_ids`及`source_eof_gap_samples`；只支持已登记0dB原声，不猜测缺失布局或重建角色。已有`speech_groups.json`或其他来源布局直接拒绝，不能退化为逐条合成。

## 暂存与验证

`baseline/`保存旧输入、两版媒体、旧包清单、检查收据／运行时及基础TTS／复合／源PCM。优先硬链接，跨文件系统回退保留时间戳复制；这是精确归档，不是文件系统不可变标记。不得原地改写被归档的旧文件，后续安装须使用保留旧版的原子替换；身份变化会使验证失败。

隔离工程只复制编译／声音所需输入，不复制旧render、quality、upload或release的成功状态。两处或多处修订通过冻结编译器产生新字幕绑定；其他文本、源窗、声音及统计保持。所有新音频均完整解码为24kHz单声道float PCM，调用冻结`dub.level`做增益处理，然后转PCM16，禁止首尾幅度裁剪、额外atempo、借窗、截尾或溢出。

检查器重放完整增益转换，逐样本验证每个替换窗及窗外未变；检查全部原窗、间隙、事件静音、真实原声区与显式EOF容器静音。绝对样本沿用既有`int(float(time)*24000)`映射，不擅自“修正”旧一采样点边界；所选完整音频还须适配精确Decimal时窗。WordBoundary文本完整覆盖检查不是声学可懂度或逐词画面对齐证明。

`editorial-source.json`是显式新来源合同；`narration.json`增加`editorial_stage_contract`依赖，`historical_generation_proven:false`，不把旧全片TTS生产者追认为新音频生产者。裸冻结`validate_narration`必须拒绝，不能直接交给旧专片运行时或常规render。通用包／当前质量／安装桥接仍待实现；显示分句也不能拿编译长源句冒充完成投影。

后续`check`以精确归档重验旧文本和媒体，不要求后来被有意替换的旧live文本路径保持旧字节；源视频、预览文件、配置、实现和暂存本身仍须可读且身份有效。因此可保留可复核历史，而不是把下一次正式安装误报为历史基线丢失。

## 本次实际验证

2026-11-14，`cjiUXZh0ZCo`的142／270两处遗漏补句成功进入`editorial-stage-v1`，并未安装：完整正常速率18.456／21.360秒适配25.109／26.540秒原窗；321原窗、23原声区和39原声／事件占位通过。39项有限配置／模拟检查含33拒绝，另有不依赖未提交编排模块的namespace CLI启动检查；不把它们称为全量测试、人耳批准或正式生产准入。

配置与收据根目录：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/`。正式双版仍为此前155的SPEC语境修订。新单片`0sZlaQr3e2w`等其他请求不因此完成；全片人耳、情感、专业词发音、视觉、逐词声画、PotPlayer及最终批准仍待完成。
