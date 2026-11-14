# 复用已核验参考的新增原声探查

`media_workflow.source_probe_extension` 为已有成功的 `source_probes` 作业增加**新目标**。它不重新解码整片，不重跑父作业的模型或旧窗口；也不修改已经绑定的原适配器、译稿、字幕、媒体、声音缓存或人工决定。

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
$PY -m media_workflow.source_probe_extension validate NEW_PLAN.json --reference PARENT_PROBES
$PY -m media_workflow.source_probe_extension run NEW_PLAN.json --reference PARENT_PROBES --output NEW_DIRECTORY
$PY -m media_workflow.source_probe_extension check NEW_PLAN.json --reference PARENT_PROBES --output EXISTING_DIRECTORY
```

## 输入与借用证明

- 新计划沿用 `source-utterance-asr-probes-v1`，字段及每段120秒、总1800秒的限制见 `source-probes.md`。视频、音频流、模型路径、CPU线程、英语和空热词设置必须与父作业一致。
- 父作业必须能通过原适配器的只读 `check`。其源文件、工具、实现、Python、已选识别器文件、包版本、模型资产和字幕绑定，都必须仍然有效。不是只检查一个旧 `verification.json` 中的成功标志。
- 父目录全部有限普通文件、父计划、实际参考音频及原进程证明绑定进 `extension.json`。新 `reference.json` 指向父作业的原始诊断WAV，不复制、不重新解码，不创建假的本地解码或ffprobe收据。
- 新计划拒绝与父作业重复的key、完全相同窗口及已观察的来源单元目标。上下文可以邻接；任何重叠都不是对旧ASR的无差别重跑许可。去重范围仅是指定父作业，不是共享队列或所有历史作业的全局索引。
- 本版父输入仅支持带真实原始解码证明的 `source_probes` 作业，不递归接管扩展作业。调用方必须另外核对已有扩展，不能以换目录或改key为由重复成功观察。

## 实际执行与复核

新片段只从父作业未变化的16kHz单声道PCM16内部参考按绝对样本切出。每段必须完整，缺尾拒绝，不补零、不截短。`inputs.json` 保留原工作进程要求的完整绑定格式；只调用原适配器的 `asr-*` 命令，不调用 `source-probe` 或 `reference-decode`。

每个模型分批不超过180秒语境，子进程1200秒上限，复用原工作进程的本地模型限定、空热词和离线设置。新作业保存各自真实进程PID、命令、出口与日志；父作业成功数和新子进程数分别报告，绝不伪装成本次重新执行的步骤。

只读 `check` 重新验证父作业、实现与模型绑定、借用音频身份、新窗与完整参考逐样本相等、每条新工作进程命令和真实成功收据、原始词/句有限数值及文本完整性、全部结果身份。它不解码、不跑ASR、不写旧目录。

使用生产工程的共享锁。生产目录、父目录和扩展输出不能互为父子；输出必须是新目录。部分失败及旧结果保留，本版不自动恢复或盲目重试。再次运行同输出明确拒绝；存在部分成功时先检查真实终态，再补通用恢复能力，而不是另起目录重做所有步骤。

## 证据边界

借用参考与切片一致只证明**诊断参考**的完整性，不证明原生声道/采样率/增益不变。识别结果、概率、词时间、双模型一致都不等于人耳、说话人、声学边界、事实、发音、情感、逐词声画或播放器批准。

2026-11-14新增10个有限夹具方法，使用合成PCM、假父证明和模拟工作进程；覆盖完整执行/只读复核、父profile变化、重复目标、已存在输出、目录冲突、父证据改变、非等值切片、假解码收据、进程变化、非有限词句、模型归属、缺尾拒绝。夹具不是实际ASR或真实媒体验收；实际原声观察须另外绑定生产日志与进程出口。
