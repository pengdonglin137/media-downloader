# AI纠正说明：有声卡片与候选时间映射

`media_workflow.commentary_visual`把已验证的完整AI试读制作成独立审片，不重新合成、不切原片，也不把字幕锚点升级成已批准的说话人边界。它不替代正文译配、正式插段或最终自包含MP4交付。

## 入口与当前实物

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
REQUEST=/home/pengdl/Videos/translated_backlog/.workflow_logs/request-0sZlaQr3e2w-20261114
RUN=$REQUEST/checkpoint-commentary-visual-v1
OUT=$REQUEST/commentary-visual-previews-v2
$PY -m media_workflow.commentary_visual check "$OUT"
```

当前`0sZlaQr3e2w`的16条审片均已完成，每条为独立MKV：1280×720、25fps、无损RGB H.264画面及FLAC音频。它们是12类说明的全部候选，含四组措辞／术语对照，不是16个已选胜或已安装的插段。完整音频共295.368秒；7391帧候选画面共295.640秒，不把候选合计冒充成片新增时长。

每帧都有“AI补充说明（非讲者原话）”、隔离候选状态、完整说明、专业词标签和未核验字幕锚点提示。声音逐样本等于既有完整WAV；全部解码RGB帧逐帧等于对应卡片，没有用仅验证PNG或帧数替代实际视频内容核对。低层相同仍不证明专业词实际音素、自然度或情感正确。

新请求先准备作者JSON，再使用新的输出目录：

```bash
$PY -m media_workflow.commentary_visual prepare VISUAL.json --output NEW_OUT
$PY -m media_workflow.commentary_visual render NEW_OUT
$PY -m media_workflow.commentary_visual check NEW_OUT
```

当前成功结果只运行`check`。已有成功或部分执行不自动重启，不覆盖旧目录；本工具不联网做TTS、ASR或自动声音选胜。

## 作者与来源绑定

- 作者JSON为`kind=ai-commentary-visual-editorial-v1`，只接受`bound_audio_plan`、`audio_previews`、`font`及完整`cards`清单；输入用规范绝对路径。现成声音须先通过共享`commentary_preview.check`的完整MP3／WAV／真实词界与来源核验。
- 每卡只有原候选`key`、完整`heading`及1–5个`terms`。候选必须全量、同序，不可偷偷跳过或选择优胜者；词标签必须已经出现在完整显示稿中，不增添未审校事实。
- 简体中文字库明确绑定文件和collection face；本次Noto Sans CJK SC为index2，不把同一TTC中的index0冒充简体字形。字库、编码工具、Pillow版本、作者稿、声音计划和manifest均绑定；旧输入变化即拒绝。
- 完整正文逐字保留；英文／数字原子不在内部拆行。中文闭合标点可悬挂在栏末，实际字框仍须完全在画布内；过短尾行只重新平衡显示，不改变口播或文字顺序。超过宽度、高度或六行的稿件拒绝，不裁字。

## 图审驱动的五项定向修订

v1的16张实际视频JPEG逐张打开后发现：nice、交换空间、指标保留和OOM中文释义候选共四处行首闭合标点；负载说明和指标保留有过短尾行，合计五条需要修改。没有把完整解码通过当成排版批准。

v2只重新编码`load-explanatory`、`nice-native-term`、`swap-use-cases`、`metrics-retention`、`oom-meaning-first`五条。其余11条卡片和布局相同，视频按字节复制，明确登记`reuse`而不伪造新编码退出码；全部16条原声音频均不重合成。当前五张修订版实际JPEG已再逐张打开，11张旧观察只在当前视频字节相同的前提下复用；共21张实际图均长边≤1280、每张≤32768字节，不保证累计请求不触发网关限额。

复用必须显式提供历史清单及旧制作器源码的只读归档：

```bash
$PY -m media_workflow.commentary_visual prepare VISUAL.json --output NEW_OUT \
  --reuse-manifest OLD_OUT/manifest.json --reuse-adapter OLD_RENDERER_SOURCE.txt
```

历史源码只校验字节和SHA，不执行归档。本次旧代码为`checkpoint-commentary-visual-v1/renderer-v1-source.txt`；旧v1实际媒体、manifest和成功收据保持，当前共享代码明确升为visual-preview-v2，不能用新CLI假称旧v1适配器身份仍有效。复用还须声音、工具、字库、布局、完整卡片及视频身份相同；当前解码重新核对全音频和全画面，不凭历史绿色状态放行。

## 时间映射的权威边界

每条保存独立的假设插入映射，时间均为精确有理数：原片字幕锚点为拟插入起点，局部画面长度为`ceil(samples×25/24000)/25`；结束后仍从同一原片时刻恢复，后续原片仅在这个单候选模型中平移该画面时长。

声音始终保留全部原样本，画面尾部最多比音频多不足一帧；没有裁音频、加速或伪造补齐样本。这个算术模型不证明锚点适合中断一句话、不批准真实交接，也不是把16个互为候选的片段顺次加入全片。实际原片没有被剪开或重新封装，生产时间轴与角色标志仍为false。

## 实测与保留的失败

- v2当前27项布局／历史复用检查含8拒绝，加21项输出／权威／复用拒绝，共48项有限检查、29项预期拒绝；独立namespace CLI通过。v1的32项输入／真实编码检查和17项输出检查另作历史保留，不冒充全为当前代码重跑。
- v1及v2各自`prepare／render／check`和持久外层均真实0。v1实际16次编码；v2实际5次编码、11次明确复用。单采样点、单帧、错误卡片、变更来源或虚构编码收据等均有拒绝证据，没有人耳或自动优胜批准。
- 最初有限检查和framehash诊断各真实1：本机FFmpeg4.4.2不支持`fps_mode`，改共享只读解码参数为支持的`vsync 0`，不升级依赖，成功测试媒体不重做。第一次图像包装器在首张成功后调用不存在的`state.save`而1；改用既有`save_json`后核验复用首张、只继续其余15张，失败与原0分别保留。
- 独立核验392项原保护身份、66项旧v1媒体／图片／源码归档、9项模型资产及98根Python不变；当前16条完整声音另与源WAV逐样本核对，7391视频帧独立完整计数。生产模块未改冻结根实现或旧声音适配器。

独立入口：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-0sZlaQr3e2w-20261114/checkpoint-commentary-visual-v1/execution-progress.json`；`independent-verification.json`、`agent-visual-observations.json`、`metadata-registration.json`及Git收据分别绑定实物。

正式工程仍`awaiting_translation`，没有新正文TTS、ASR、数据库决定、完整正文编码或正式安装。原主持交接／完整意群、疑词听辨、原生采样原声边界和真正可中断的AI插入点仍待重建；发音、情感、人耳、逐词声画、全片视觉、PotPlayer及最终语言声音批准未完成。目标active，不改共享队列／并行总数，不发布、不新增交付外挂，不声称后台自动续跑。
