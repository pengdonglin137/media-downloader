# 完整试读的显式证据补充与音频复用

`media_workflow.preview_evidence_import`解决已有成功完整试读缺少后续阶段所需证据绑定的情况。它不修改原计划、原提案、原清单、原音频或已绑定实现，也不重新调用TTS。新增引用不能悄悄冒充原合成时已验证的来源。

## 输入与输出

配置仅接受`kind`、`source_plan`、`source_manifest`、`additional_evidence`及`reason`。类型为`complete-preview-evidence-import-v1`，绑定一个真实成功的完整试读计划/清单，以及1–12项新证据身份和明确原因；拒绝重复、陈旧或已经在原计划中的补充引用。

先使用原`media_workflow.speech_variants.check`验证原试读。派生计划只在`reference_evidence`末尾追加新证据、导入配置和导入器身份，完整保留文本、原窗、当前生产输入、声音、所有候选及旧证据顺序。原清单中的完整WAV以`copy2`复制到新外部目录，字节数、纳秒mtime及SHA-256保持；MP3和WordBoundary JSONL继续绑定原路径，须保留它们，不把工作日志当作用户交付附件。

输出`plan.json`和`manifest.json`。清单保持完整试读的兼容格式，原`adapter`及`synthesis_implementation`明确指向**原音频来源**，不是声称旧适配器重新执行了合成。新增`evidence_import`单独记录实际导入器、配置、上游计划/清单、新证据、原因和`imported_at`。原`created_at`仍是原试读时间，不能把它冒充本次导入时间。

`new_synthesis_or_waveform_generation=false`表示未新合成或生成波形；验证器仍会调用ffmpeg完整解码已有MP3，与WAV逐样本核验，不表示完全没有解码验证。后续正式暂存的既有增益处理另行证明，不能将生产PCM直接声称等于未增益的预览PCM。

## 操作

```bash
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.preview_evidence_import import CONFIG --output NEW_DIRECTORY
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.preview_evidence_import check CONFIG --output NEW_DIRECTORY
```

`import`仅接受不存在的新外部目录，禁止覆盖原试读、规范工程及仓库路径；持有工程共享锁。成功后只`check`，不重新导入同目录。中断或失败保留已有证据，不自动删除、重试或重新合成。

此检查仍绑定试读时的活动规范输入。正式采用改变来源/译文后，旧预览的live检查应当拒绝，不能改它的绑定使其继续“通过”；后续使用真实暂存、基线归档和历史安装合同回溯原音频与证据。

`check`同时核验新增导入来源及原完整试读合同：派生计划只能追加指定引用；所有WAV必须与原件内容/mtime一致；原清单除计划与波形路径及显式导入记录外全部相同，含时长、词边界、PCM摘要、未试听/未选胜者等权限限制。普通`speech_variants.check`仅验证兼容试读层，不替代新的导入来源检查。补充文件的身份不等于其内容已被自动事实审查，具体源文修订还须经过`source_revision`。

## 已验证实例

`cjiUXZh0ZCo`的GPU/软件12条完整试读原提案遗漏后续`source_revision`必需的`source_provenance_before`。新提案v2只补原`source_provenance.json`身份及旧提案来源说明；两处英文替换、原始JSON3、原审校、321编号映射与所有语义保持。导入器显式追加该v2提案和来源元数据，保留12条成功音频，包括未选的209A，没有重合成。

实际import/check均0；隐藏工作日志的`finite-import-checks-v1.json`含24项有限检查：2个正例、22个拒绝，覆盖改词、移动原窗、换声、改速、丢旧引用/候选、伪造试听/情感/合成/选择权限、错误导入器、增添未知权限、篡改PCM及空/重复/已绑定引用。仅操作独立夹具，成功导入及规范工程未改，不将有限检查等同全局证明。

之后11个选定单元的真实父暂存和188/204真实来源修订均通过，43个短句及完整英文611/657同步投影，隔离质量扫描保留全部旧库行和0决定。209B仅按文本语气与来源忠实度作编辑选择，不宣称听出了情感胜者。203实际中文/音频是历史“15号”，旧审校说明的“第十五场”属元数据误称，另行勘误，没有改成功音频或臆加年月。

实例运行根为`request-cjiUXZh0ZCo-20261114/gpu-software-adoption-v1`；自然度、专业词发音、情感、人耳、全片视觉、逐词及PotPlayer验收均不由导入或技术适配自动批准。
