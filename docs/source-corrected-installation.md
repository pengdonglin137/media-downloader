# 源文修订双版本的历史安装

`media_workflow.source_corrected_install`组合已有冻结历史计划适配器和事务安装器，不重写它们，不更改旧英文保持型媒体检查的含义。

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
$PY -m media_workflow.source_corrected_install prepare CONFIG.json --output NEW_INSTALL
$PY -m media_workflow.source_corrected_install apply CONFIG.json --output NEW_INSTALL
$PY -m media_workflow.source_corrected_install check CONFIG.json --output NEW_INSTALL
```

成功安装只check；不重复prepare/apply，不改已经绑定的配置、生产实现或历史收据。候选必须先完成实际双版本验证；未完成候选、旧成功媒体和来源文件不得为取得绿色状态而重做。

## 显式配置

配置精确包含`kind/scope/adapter/implementations/authority/media_config/installation_config`：

- `kind`为`source-corrected-historical-installation-runtime-v1`，范围只允许一个已验证候选及其精确历史，不授权全局迁移。
- 新桥接器和依赖的完整文件身份固定。历史权威仍是`editorial_history`的原格式，绑定实际质量派生工程、原计划的精确归档事务及终态。
- 嵌套原生安装配置须指向同一规范工程、同一来源感知媒体配置和实际候选`verification.json`；新字幕投影的source stage须等于历史权威所绑定的stage。
- 原生安装器后续仍调用完整媒体check，不能仅凭`passed`字段、虚构完成标记或预渲染质量安装候选。

## 限域桥接

新运行时只改变两个明确的分派点：选定候选的冻结媒体check分派到来源感知媒体check；选定source revision stage的旧采样stage上下文分派到真实`source_revision.check`，进入和退出各自核验。后者不伪装成旧采样stage，也不额外套一层字幕运行时；实际候选检查自行建立、验证并恢复自己的完整来源感知运行时。

其他候选和stage继续调用原冻结函数；拒绝嵌套和外来分派，正常或异常都恢复原函数。历史适配器仍只为一个质量派生工程、一个精确原计划提供归档读取，不替换全局`state.load`或`state.matches`。

原生安装保留旧语义/媒体/数据库并执行有日志的多文件事务，当前来源绑定、扫描、原声分类、render、package、upload与release按真实正式路径重建。新增的`source-corrected-installation-runtime.json`固定桥接实现、配置、权威和候选；apply/check都要求精确相同。

单独归档当前原计划可使用已有通用文件事务：来源与目标是同一完整身份，实测0替换/0意图后提交，只生成精确归档，不改变原计划。后续必须依据真实归档身份建立权威；旧版本计划不能冒充当前计划。

正式安装不意味着发音/情感、原声角色和停顿、人耳、逐词声画、全片视觉、PotPlayer或最终批准通过。无新增TTS/ASR/编码由本安装器执行；不会改变其他请求、队列或全局计数。

## 2026-11-15 正式采用

`request-cjiUXZh0ZCo-20261114/memory-storage-media-v1/install-v1`的prepare/apply/check三操作真实终态0；31事务目标、25实际替换，事务已提交。所有31个旧活动文件均以原字节/mtime/哈希精确归档，不再把它们声称为仍在原位不变；3116另列不可变文件、98冻结根Python、293既有生产MP3及13其他未跟踪文件原位保持。

- 正式母版`/home/pengdl/Videos/translated_backlog/Science of Performance 2019_中文配音.mp4`，566010599字节，SHA-256 `a0c99aa3da6af87f1c62e4746ad66903a66471348827cb79e1c4c13ddf1e8e74`。
- 正式上传版`/home/pengdl/Videos/translated_backlog/Science of Performance 2019_中文上传版.mp4`，452319296字节，SHA-256 `72eab552af8d58f7d712afbe1dff3291c8c3e0032103dd3a502e0178df892312`。
- 两版正式身份精确等于已完整验证候选的字节/mtime/哈希，不重复编码、TTS、ASR或全片解码。40.2秒有声导读、85章节、5572.982秒、321来源窗、23实际原声区及39预留保持。中文九单元/33短句与172/173两项显式源修订同步正式采用，完整母版英文只改565/566词面。
- 原生安装包仅继承既有`PACKAGE_FIELDS`，没有候选专属`English_stream_copied`字段；它通过`media_producer`和`upstream_package`精确绑定真正新生产者及`English_stream_copied=false`候选，字幕记录完整继承。首次独立核验误以为该字段存在而真实1，旧脚本/日志/出口保留；v2按实际原生schema和上游身份完整核验0，没有改包、媒体或任何绑定生产模块。
- 当前正式私库101扫描/7133历史问题/12650出现，186当前未人工复核候选，12原声规则仅非dismiss分类、0决定/修复批准；100→101的全部旧行保持，历史来源98扫描库不覆盖。不能把候选190→186当作声音或情感批准。

旧英文保持型生产检查仍拒绝新候选，后续只能通过当前`source-corrected-install-config-v1.json`和本模块执行check，不重复prepare/apply。候选有限VLC的解码诊断保留；正式路径补验首次因帮助器同目录模块未加入搜索路径而1，单独修正只读启动入口，不重做媒体。正式v2有限VLC真实0，章跳转和音轨/字幕切换通过，但仍有64行解码诊断/16条`no frame!`，未解决或隐去；dummy音频不是人耳。完整收据见`checkpoint-memory-storage-installed-v1/execution-progress.json`。总目标仍active，Linux的27项hold、23标记和critical F08不变，没有Linux中文双版或全量任务完成声明。
