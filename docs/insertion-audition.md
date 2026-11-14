# 保留完整原声前后文的插段试听

`media_workflow.insertion_audition`用于制作内部「完整原声前段 → 完整插段 → 完整原声后段」PCM试听对照。每个case是独立替代方案，不把多个候选顺次塞入同一条音轨；不改来源、不做TTS／ASR／视频编码、不安装正式时间线或写听辨决定。

```bash
python3 -m media_workflow.insertion_audition render PLAN.json --output /outside/repository/new-auditions
python3 -m media_workflow.insertion_audition check PLAN.json --output /outside/repository/new-auditions
```

计划只接受以下字段；每个文件身份为实际`dubbing_state.identity`返回的绝对path、bytes、mtime_ns、sha256：

```json
{
  "kind": "pcm-insertion-audition-v1",
  "scope": "isolated-audition-no-production-installation",
  "source": {"path": "/absolute/source.wav", "bytes": 0, "mtime_ns": 0, "sha256": "actual-digest"},
  "cases": [
    {
      "key": "candidate-a",
      "at_sample": 240000,
      "insert": {"path": "/absolute/complete-insert.wav", "bytes": 0, "mtime_ns": 0, "sha256": "actual-digest"}
    }
  ],
  "evidence": [
    {"path": "/absolute/anchor-evidence.json", "bytes": 0, "mtime_ns": 0, "sha256": "actual-digest"}
  ]
}
```

示例身份为占位符，必须替换。`at_sample`是来源WAV自己的每声道样本帧坐标，不是原片秒数，也不是把44100 Hz原生坐标塞进24000 Hz诊断WAV。锚点必须是整数，严格在来源开头与EOF之间，使两端原声都非空；样本数、声道及PCM格式不能靠布尔值或浮点整数混过。

## 精确范围与拒绝行为

- 只接受标准未压缩PCM16、单／双声道、8000–192000 Hz。插段与来源格式必须完全一致，不自动重采样、混声、增益、变速、交叉淡化、补零、截尾或去静音。
- 每个完整插段最多40秒；来源和完整输出最多八小时且不能超过标准RIFF容量。1–16个唯一安全命名case；1–32个明确证据身份。证据的字节绑定不是语义正确、没有重叠讲话或生产交接批准。
- 每条输出保留来源全部PCM样本，分别与来源前段、完整插段、来源后段逐样本流式对照；输出样本总数严格等于两输入总和。完整WAV头会重新生成，不声称来源整个文件字节不变或保留其可选元数据块。
- 输出必须在仓库外，工程`.zh_work_*`内仅允许`.managed_workflow`子树；不能包含任何输入。锁、manifest和明确WAV之外的额外文件亦拒绝。输入／实现变化、旧目录未知／部分／失败、输入截短或输出PCM变化均不覆盖。
- 成功重入`render`或调用`check`只读核对全部输入／实现、输出身份及完整PCM，不重写已有成功试听。未完成记录保留`complete=false`；SIGTERM等不可捕获退出可能留下部分WAV，不据此冒认成功。

本模块只用标准库及冻结根帮助模块。退出0代表生成／一致性检查成功；退出1代表配置、身份、范围、锁或执行失败；可捕获Ctrl-C返回130，不编造独立子进程出口。没有音频子进程、联网服务、数据库或生产路径修改。

## 当前实物

Linux性能直播的两条完整新OOM纠正复用既有217.520秒原声语境，分别形成240.416／240.656秒完整前后文WAV。新插段549504／555264样本，原声5220480样本在每条输出中完整保留，锚点为诊断WAV第3818400样本。仅比较24kHz诊断PCM，不冒充原生44.1kHz或最终AAC安装证明。

32项有限生成夹具／拒绝检查覆盖双替代方案、全样本顺序、只读重入、输入／输出篡改、不同采样率／声道拒绝、44100 Hz双声道保留及超40秒拒绝；新增拒绝源身份／证据变化、未知身份字段、截短来源和失败目录重试，原27项收据保留。它们不是32次实际制作，也不是人耳验收。source-handoff、角色、发音、情感、响度衔接、全片声画和最终交付始终不由本工具批准。
