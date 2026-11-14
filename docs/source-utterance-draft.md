# 来源锚定的完整语句文字提案

`media_workflow.source_utterance_draft`把作者逐项写出的中文意群候选与**未改写的原字幕词流**绑定。它不自动翻译、找说话人、确定声学边界、合成声音或导入正式工程。

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
$PY -m media_workflow.source_utterance_draft validate PLAN.json
$PY -m media_workflow.source_utterance_draft build PLAN.json --output NEW_DIRECTORY
$PY -m media_workflow.source_utterance_draft check PLAN.json --output EXISTING_DIRECTORY
```

## 作者计划

计划采用`source-bound-utterance-draft-v1`，例见`data/translation_reviews/gYfd25Bdmws-opening-utterances-20261114.json`。绑定既有完整隔离阅读稿、技术审校和实际证据身份，每项填写：

- 固定`id`、`end_source_unit`及在该原字幕块中只出现一次的精确`end_anchor`。只用于词面分区，不是音频时间。
- 作者写出的完整`zh`与具体`note`，保留数字、条件、否定、问题归属及源话的不确定性；不再把旧字幕边界处的省略号直接当一句朗读。
- `kind`为普通语言、未解决来源、交接候选或供应商标记。来源标记独立，不能和语言合并；标记不证明音乐、换人或静音。
- `tone`仅是作者对解释、设问、对比、谨慎、过渡、自嘲、致谢的文字意图。不是声学分类、情感胜出或模型效果批准。
- 可另写`spoken_candidate`，否则普通无来源hold项使用`zh`作未合成的朗读文字提案；有来源hold、未辨识项、交接和标记均保持候选为空。所有实际`spoken`仍是null、角色unknown、TTS禁入。

每项从上一项的下一个原词开始，到本项锚点结束，不丢、重复或重排源词。最后一项可以停在原字幕块中间；输出准确报告已覆盖词数、完全覆盖块数、下一`[原块编号,零起始词偏移]`及未覆盖量，不能把触及一个块称为整块完成。续做时另建累计作者JSON，保留旧计划与构建结果，不修改旧成功绑定，也不重跑媒体。

## 输出和边界

构建前逐块核对原TSV、SRT和旧阅读稿。结果保留原词区间、源块交集、原字幕外包、全部来源hold/技术发现/历史partial标记及绑定。旧输入缺少历史mtime时，只检查它实际记录的字段，再把当前完整身份写入新绑定，不能伪造旧mtime。

**字幕外包不是讲话窗。**多个条目可以共用或重叠旧外包；不得按词数均分时间，不能将超40秒的旧字幕外包假装成合格原窗。本工具只列出`caption_envelope_exceeds_40s`，不截句迁就外包，也不放宽生产同人完整意群≤40秒、最多+15%及无额外atempo等规则。真正生产前仍须来源、角色、声学窗口和事实纠正分别准入。

`bindings.json`固定计划、输入、证据与实现；`reading.json`仅为隔离文字结果；`verification.json`记录覆盖与未完成范围。`check`重新投影并逐项比较，不修改输出。输出必须是生产工程以外的新目录，拒绝覆盖或静默重建。使用工程共享锁，不改原文、成片、缓存、声音数据库或共享队列。

词流验证只能证明分区完整，不能判断每句语义是否忠实完整。末尾省略号拒绝也不等于自动语义审校。作者仍须检查跨块主从句、交接、未辨识原话、数字与原图口径，不能用绿色检查替代人耳、声音情感或全片验收。

2026-11-14共有11个有限测试方法，使用合成TSV/SRT和假审校数据，覆盖精确/部分前缀、唯一锚点、重复或倒退归属、供应商标记隔离、有hold/交接禁朗读提案、原输入变化、旧身份字段、输出冲突及保持未知角色。这不是11次实际ASR/TTS或真实说话人验收。
