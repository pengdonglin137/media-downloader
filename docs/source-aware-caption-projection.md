# 显式源文修订后的字幕投影

`media_workflow.source_caption_projection`接续已经成功的`source_revision`暂存，在新的外部目录生成三种完整字幕及各自正文显示字幕。它不改冻结的`caption_projection`：旧模块仍保证完整英文轨不变；新模块明确承认并逐项登记英文变更，不能把旧合同改成默许来源漂移。

## 使用入口

```bash
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.source_caption_projection prepare PROJECTION_CONFIG.json --output NEW_PROJECTION
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.source_caption_projection check PROJECTION_CONFIG.json --output NEW_PROJECTION
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.source_caption_projection quality QUALITY_CONFIG.json --operation prepare --output NEW_QUALITY --projection-config PROJECTION_CONFIG.json --projection NEW_PROJECTION
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.source_caption_projection quality QUALITY_CONFIG.json --operation check --output NEW_QUALITY --projection-config PROJECTION_CONFIG.json --projection NEW_PROJECTION
```

成功目录只运行`check`。失败输出、原命令、实现身份及真实非零出口分别保留；不要覆盖它们或重做已成功试读。这里的质量操作只在隔离工程扫描当前复合旁白，不生成、安装或发布成片。

## 投影合同

- 配置精确包含`kind`、`stage_config`、`stage_manifest`、`baseline_package`、`baseline_subtitles`、`intro_offset_seconds`和`edits`；种类为`source-revised-word-boundary-projection-v1`。
- 成功源文暂存、原完整字幕、母版包、真实毫秒导读偏移和所有实现身份必须一致。源窗起点还须精确落在既有24 kHz采样网格上。
- 每个实际完整意群编辑都必须提供`unit_id`、完整试读`key`、具体审校理由及1至16个`zh/spoken/en`短句。中文和朗读逐字串接等于当前完整意群；英文完整词流等于已显式修订的源文。
- 中文及双语短句只复用旧模块的已观测`WordBoundary`投影算法，不能切开实际合成词、丢失条件或扩展原窗。所有未选中文、双语及导读字幕保留原文字与时刻。
- 每个英文修订必须覆盖完整源单元对应的所有旧英文短句，原完整词流须匹配修订前源文。单一精确替换必须全部位于一个既有英文短句内；跨句、歧义、多次出现和重复所有权均拒绝。
- 完整英文SRT以明确LF/UTF-8字节局部替换，句号编号、全部时间戳和未改文本/空白逐字节保留。修改后对应整个源单元词流须等于修订后源文；不声称修订后纯词流守恒。
- 输出严格为`zh/en/bilingual.srt`与各自`*_display.srt`六个文件，另存原始精确归档和变更清单。清单明确`english_track_bytes_unchanged=false`，并明确不提供人耳逐词声画对齐批准。

## 限域质量桥接

`runtime(config, output)`先验证成功投影，再仅对这一对源文暂存路径及这一对字幕投影路径分派新检查。其他路径继续调用原冻结检查；拒绝嵌套和外来分派，正常或异常退出都还原原函数。没有放宽通用旁白/来源验证器。

质量配置必须精确绑定同一源文暂存及字幕投影。既有`editorial_quality`保持不变：其`caption_adapter`字段仍是被复用的冻结模块文件身份，**并不等于实际新分派器身份**；新投影清单独立绑定新模块及其依赖，调用者必须保留这个显式运行时，不能以旧字段伪称裸旧入口支持新英文修订。

隔离质量工程一致性备份正式声音数据库；所有旧扫描、候选、出现、术语、决定和修复历史保留，仅追加当前隔离路径的一次真实完整复合旁白扫描。原声短尾不适用合成规则的分类不是删除、dismiss、听辨或角色确认。技术状态`eligible_with_unreviewed_candidates`也不是发音、情感或可懂度批准。

## 2026-11-15 实测边界

仅`cjiUXZh0ZCo`九句161/163/167/172/173/176/179/180/182，复用九条既有完整正常速率试读及283条已观测词界；33个中文/双语显示短句通过。完整英文实际1028句，仅565的`ATMs for`→`8 DIMMs / 4`和566的`2002`→`2000`改变；对应172/173的完整包络分别3/4句，真实导读偏移40.2秒。条件33%仍是讲者预期，4通道证据只来自已看源图，2020仍是讲者当时预计；不背书当前配置或标准发布日期。

90个有限检查通过，含76个拒绝路径。两个测试夹具真实出口1分别保留：第一轮漏接原冻结路由的`FileNotFoundError`；输出测试把可信源暂存路径误替换成投影路径，导致一个预期拒绝的测试输出实际生成。后者10个测试文件精确移到独立失败证据目录，原目录布局恢复，原文件未改；父目录元数据不在文件保全证明范围。修正的是新版本夹具，不是生产模块或守卫，成功生产投影未重跑。

实际投影`prepare/check`和隔离质量`prepare/check`均真实0。私库98→99扫描，6575→6761问题，11681→12004出现，全部旧行保留、0决定；正式库仍98扫描/190未人耳候选。隔离当前186候选中184个未编辑候选逐项保持，6个已编辑旧候选保留于历史扫描；这不是人工修复6项的声明。12个原声分类保持非dismiss。

独立文件核验覆盖3021个不可变证据、31个活动目标、98根Python、293生产MP3及13个其他未跟踪文件。没有新TTS、ASR、全片解码或成片编码；正式双版本、321源窗、23原声区及39预留单元保持。后续还须显式衔接媒体候选及历史安装，不能把本轮字幕/质量预检说成正式采用。Linux直播的27个hold、23个标记、关键F08及所有人耳、情感、发音、全片视觉和PotPlayer门禁保持未完成。

运行证据在`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/memory-storage-projection-v1/`，续做入口为同请求`checkpoint-memory-storage-projection-v1/execution-progress.json`。只提交本轮适配器、审校结果与文档，媒体/缓存/运行证据不入Git，不覆盖并行队列或全局总数。
