# Linux性能访谈：来源观察与隔离阅读修订（2026-11-14）

仅处理用户链接`0sZlaQr3e2w`，续接全文隔离稿和16条完整句AI说明试读。正式工程仍为`awaiting_translation`，没有安装`translation.tsv`、正文配音或AI插段。本轮不是成片完成，也不是全片听辨或声音批准。

## 实际执行与来源

断点目录：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-0sZlaQr3e2w-20261114/checkpoint-source-observation-v1`。

- 使用未修改的`asr.py`、已有medium和distil-large-v3本地模型，CPU int8、无热词、明确离线。无模型下载或依赖安装；`environment-observation.json`仅记录成功执行后观察到的环境，不倒称过去进程的依赖证明。
- 原片`0:a:0`声明为AAC、44100Hz、双声道。只解码一次完整单声道PCM16／16000Hz诊断参考，再按绝对样本提取六段；这不是原声道或生产增益保持证明。
- 一次完整解码、六次取段、十二次ASR共19个实际子步骤均0，原外层工具会话22595实际0。成功结果只读取、核验，不自动重试。
- `execution-state.json`、逐步日志和`.exit`保留真实命令、终态与输入输出身份；`clip-bindings.json`绑定源流、绝对样本及与完整诊断PCM相等的片段。

| 诊断片段 | 原片起止秒 | 目的 |
| --- | --- | --- |
| opening | 0–54.480 | 无字幕片头及完整开场语境 |
| feels-wordplay | 65.680–96.520 | 8–9的措辞和重复，不从笑话猜词 |
| dd-demo-purpose | 441.120–499.880 | 58的磁盘问题及演示前提 |
| vmstat-name | 1292.400–1358.640 | 170疑似称呼及工具名自纠 |
| discarded-credit | 1604.360–1639.120 | 缩写解释、插话和聊天致谢 |
| ending | 3584.080–3641.399750 | 完整结束语、重叠及无字幕片尾 |

每模型共302.399750秒。ASR输出均在诊断片段边界内；模型没有输出，不代表对应原声静音、纯音乐或无人讲话。

## 逐项结论与阅读修订

作者审校：`data/translation_reviews/0sZlaQr3e2w-source-observation-20261114.json`；探查计划：`data/translation_reviews/0sZlaQr3e2w-source-probes-20261114.json`。

- 8–9：medium两次识别为unimportant，distil只保留一次；没有证据正式改成unperformant，也不能称每次重复均有双模型支持。
- 58：medium为disk down problem，distil为disk problem；不根据技术预期强改disk-bound，保留谨慎的“磁盘问题”。
- 170：原字幕But name，medium为But Nate，distil为But may。简介列出Nate Lager／Scott McBrien只能增加称呼可能性，不证明声纹／角色或正式人名。移除旧中文凭空增加的“名字归名字”，新阅读稿显式保留`［此处疑似称呼，待听辨］`；绝不将这个备注送TTS。
- 212–214：两个模型均报告DSCD解释与Rick致谢，与旧字幕相符；不推断完整姓名、账号、说话人或普遍缩写定义。
- 436：将结尾改为“谁能想到，性能排障一聊就是一个小时？看来，还真有不少东西可聊啊。”，减少机械逗号并保留轻微自嘲；这是文字语气意图，尚无实际情感配音批准。
- 438–441：下一期话题的重叠识别分歧保留，不补造park或具体主题。两模型把节目名误成end of the terminal；仍按原节目名Into the Terminal，不把模型一致当事实。

新稿`quarantined-reading-v2.json`只在断点目录供审读，441项中仅170／436改变，其他439项完全保持。`reading-materialization.json`绑定旧稿、审校与新稿。旧hash绑定的全文草稿、事实审校及AI计划不改；英文来源、时窗、正式译文和数据库决定不改，全部未批准／隔离标志保留。

## 全文原字幕索引

使用冻结`prepare_videos.json3_entries`复现原JSON3到441字幕块的既有显示分段，不将其冒充正式同人意群。

- 3166个原事件中1583个有正文，9456个空白分词token包含23个`>>`标记，位于21个来源块。
- 7850个token有供应商segment偏移；1606个只有事件起点默认值，不能改称明确词级时间。
- `caption-token-provenance.json`逐token绑定原事件、segment索引、元数据起点与原块；`source-event-map.json`保留全文映射和原显示时间处理，`caption-turn-candidates.json`只登记标记候选。
- 全文词流守恒只针对任何显式词面修订之前的原字幕；没有声学词尾、已确认说话人、真实交接或批准同人单元。无标记也不证明同一人，不用下一token起点捏造当前词尾。

`caption-index-inputs.json`和`caption-index-summary.json`分别绑定输入与统计。原JSON3独立核对，不冒称已经包含在旧232项保护集合内。

## 画面与实际原声保留政策

五张实际来源JPEG均已逐张打开，长边≤1280、文件≤32768字节；不是成片图审或全片视觉验收。

- 1秒为节目标题、瓶颈文字和两张静态人物肖像；15秒同卡有真实`00:02`倒计时。
- 1320秒为实际`vmstat`表格及`bi/bo`高亮，不证明170的称呼或讲话人。
- 3634秒为黑底Red Hat结束卡；3640秒为黑底小型红白结束图标，不据画面断言原声静音。

`source-image-extraction.json`和`agent-source-image-observations.json`绑定图像身份与观察；工具会话67422实际0。开场`[0,29.840)`和片尾`[3630.680,原0:a:0实际结束)`应在未来两用途中文主音轨保留真实原声。本轮只登记政策，没有生产回填；44100Hz生产尾界仍须实际核验，不以16000Hz诊断结束样本代替。

## 核验、提交与续做

`preservation-verification.json`核对232项原身份、另计原JSON3和9项本地模型资产不变；16旧试读、98根Python和既有成功媒体保持。原只读核验会话17590实际0，历史成功／失败收据不改。本轮独立核验v1错误要求新阅读稿的版本`kind`仍等于v1而真实1，日志／出口保留；v2只明确接受已登记的revision-v2类型，其余继承元数据仍严格不变，全文映射／诊断PCM／实际出口与身份复核真实0。没有为通过核验改稿、重做ASR或媒体。

最终登记和独立复核见本断点的`metadata-registration.json`、`independent-verification.json`及`execution-progress.json`；Git结果单列`git-receipt.json`，不把待提交计划当已push。

五个共享说明只插入本轮段落，提交暂存内容以HEAD加本轮插入构建；并行修改另留工作区，不整体`git add`。媒体、模型、运行缓存及独立收据不进Git。

下一步继续完整意群／主持交接重建、疑词听辨、原生采样边界与明确AI插入画面／时间映射，再制作正文；不重跑这12次ASR或16条成功试读。发音、可懂度、情感、人耳、全片视觉、逐词声画、PotPlayer及最终批准均未完成。目标仍active；不改共享队列或并行总数，不声明后台自动续跑。
