# 原声疑句离线探查

`media_workflow.source_probes` 是配置驱动的只读来源观察适配器。每个视频只提供JSON计划和收据，不复制单片Python运行器；不会导入译文、选角色、合成声音或关闭任何人耳门禁。

```bash
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.source_probes validate PLAN.json
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.source_probes run PLAN.json --output NEW_DIRECTORY
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.source_probes check PLAN.json --output EXISTING_DIRECTORY
```

## 计划与约束

计划使用 `kind=source-utterance-asr-probes-v1`，精确字段可参照 `data/translation_reviews/gYfd25Bdmws-source-probes-20261114.json`：

- `work` 是已准备工程的规范绝对路径，`source` 必须是工程原片，绑定SHA-256和零起始音频流编号。
- `reference_evidence` 绑定已审源文、草稿等文件的完整身份。模型使用1至3个现有本地规范目录，拒绝 `medium` 这类可能触发下载的模型名；模型文件集及其实际字节分别绑定。
- 本版固定英语、空热词、CPU/int8，`cpu_threads` 为1至8。调用冻结的 `asr.transcribe`，沿用其beam=5、VAD、词时间及不承接上一段文本的参数；不利用热词暗示答案。
- `probes` 为1至24段，各不超过120秒，总计不超过1800秒。起止用能精确落在16kHz诊断采样格上的非负十进制字符串，附原单元ID和具体疑点。它们是为目标保留上下文的诊断取段，不是已批准的讲话边界。
- 仅支持声明起点为0的源音频。非零或未知起点明确拒绝，不能猜测字幕/声学偏移。

## 实际执行

先通过ffprobe确认选择的音频流，完整解码一次为16kHz单声道PCM16内部参考，再按绝对样本切窗。缺尾不补零、不缩窗；切出的每段必须逐样本等于完整诊断参考的相应范围。这是重采样/混单声道的观察参考，不是原生采样率、声道、增益或源PCM保持证明。

每个模型按不超过180秒源语境的批次启动工作进程，复用现有本地模型；单批进程1200秒上限。模型构造显式 `local_files_only=True` 并启用HF离线环境。原模型资产不改。输出保留每个模型各自的词、概率、相对窗内时间、完整句和文字，模型间不投票合成所谓“真实原文”。

每条实际子命令保存PID、真实出口和日志。`check`核对源/计划/实现/模型及参考、全部窗口逐样本范围、命令本身、成功收据和结果身份，不再解码或运行ASR。它只能证明观察链完整，不能证明词时就是声学边界、概率就是事实可信度，或者双ASR一致就等于人耳听辨。

使用工程共享锁，输出必须在工程外的新目录。旧输出、部分结果及失败不覆盖；本版不自动恢复中断，也不把缺出口补成0。发现部分成功后，不得另建目录无差别重跑；先复核当前进程和结果，再补通用恢复能力或采取明确的安全下一步。`check`不修改旧证据。

## 验证范围

2026-11-14，有限夹具包含8个测试方法，其中配置拒绝有21个子场景，另覆盖重复JSON键、模型缺文件、精确整窗、不补尾、拒绝重复取窗、分批及命令/结果变更。使用假模型和合成PCM，不是8次真实ASR，更不是发音、情感或角色批准。依赖记录绑定所选模块、包版本、工具及模型文件，不声称冻结整个运行系统或所有第三方依赖。

实际视频结果及仍待核对的问题，应分别登记在每个请求中；不因工具跑通就将隔离译稿改为正式 `translation.tsv`。
