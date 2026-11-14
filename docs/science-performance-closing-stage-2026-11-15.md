# 性能报告结尾：来源忠实度选择、暂存与真实质量准入

本轮把上一轮八条试读推进为七条**来源忠实度选择**：308、315–320。318选“热爱”是为了保留原文`loved research`、`still love research`、`love teaching`的明确词义，不是人耳选胜；完整“喜欢”替代项保留。两种职业、软件/硬件、架构/CPU/内存、两名作者、Java/Hadoop、成本/TCO及结束语均保留。319/320的取舍必须配对归位，不称全局漏译。

作者审校与22段中文/朗读/英文投影保存在`data/translation_reviews/cjiUXZh0ZCo-closing-adoption-20261115.json`。来源英文及原窗不变；人名保留英文拼写，没有新增音译、书名、版次或声学定案。说话人仍沿用已有文档审校，不升级成人耳批准。

## 当前质量收据不是旧结果改名

原历史安装检查的返回结构没有完整`quality`对象，不能直接充当stage的质量权威。本轮实际执行当前native安装检查，并在既有显式历史/sample-grid/installed运行时中调用`delivery_quality.require`，得到当前旁白、全部质量输入、原片、母版、上传版和包的完整真实绑定。运行后全局state读/匹配函数恢复；未写正式数据库、扫描、TTS或媒体。

共享`editorial_sample_grid`实际prepare/check通过。七条既有完整MP3/WAV直接复用，正常速率，无裁尾、借窗或额外atempo；同时满足精确原窗与旧样本所有权。308、319、320的旧所有权各少一个样本如实保留，不把精确窗余量冒充实际可用余量。

| 单元 | 实际所有权内剩余样本（24kHz） |
| --- | ---: |
| 308 | 145295 |
| 315 | 54288 |
| 316 | 300096 |
| 317 | 103104 |
| 318热爱 | 58320 |
| 319 | 61223 |
| 320 | 10727 |

321严格原窗、23实际原声区、39原声占位保持；窗外复合音频等于基础TTS，原声区样本及显式EOF静音精确相等。正式文本和两版媒体没有被stage改写。

22显示短句按真实WordBoundary投影，英文全词流及完整中文/朗读串守恒；显示英文文件字节不变。登记术语完整位于对应短句，既有词表前缀精确保留，新增布局词表只作显示边界检查，不声称专业词发音修正或全部中文分词覆盖。

## 隔离全音频扫描

真实quality prepare/check通过。规范工程及原私库保持89扫描/4859问题/8774出现；新隔离预检副本为90扫描/5050问题/9097出现，41词、60工程、0声音决定、0修复解决。旧行全部保留，当前191候选中189未编辑候选保持，2个受改动单元的旧候选仍在旧扫描；当前仍191不是已全部听辨。

12原声占位的合成尾句规则分类仍不是dismiss、角色或可懂度批准。既有VLC诊断、人耳、发音/情感、全片视觉、逐词声画和播放器最终批准均保持。

## 不改绑定代码的当前计划精确归档

新候选未来安装会替换规范工程`package-plan.json`，但其上游来源检查仍须读**旧**计划。因此先通过已有`FileTransaction`作一个显式单文件精确归档：原身份与期望身份完全相同；`replace`在实际写入前返回，prepare/archive/staged/commit/check真实完成，记录**0规范文件替换、0意图**。归档字节、mtime和SHA-256与原计划逐字段相同，规范计划身份不变。

这是有真实终态的计划备份，不是重做媒体、假造失败安装或把归档事务当作媒体安装成功。既有`editorial_history`按真实prepared/committed journal验证新候选所需的旧计划，不修改其已绑定代码、全局I/O或任何门禁；新权威validate真实0。无需再复制逐片恢复脚本或增加适配器。

## 证据与后续

- 请求：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/closing-adoption-v1/`。
- 当前阶段断点：同请求`checkpoint-closing-stage-v1/execution-progress.json`。
- 选择与显示短句：`data/translation_reviews/cjiUXZh0ZCo-closing-adoption-20261115.json`。
- 阶段结果：`data/translation_reviews/cjiUXZh0ZCo-closing-stage-result-20261115.json`。
- 独立核验：`stage-independent-verification.json`绑定1037旧身份、98冻结根Python及293生产MP3保持。

本阶段只授权“已暂存、显示投影、当前音频质量通过”，**不授权候选编码验证或正式安装完成**。新媒体作业继续复用既有`editorial_media`；只观察实际会话/进程及子出口，不因等待超时重启。编码完成后须逐项验证真实双版、帧/包/音频、受限图审和播放器证据，再经新历史计划权威作单独归档安装。原片、已成功合成和其他请求不重跑，持续目标保持active。
