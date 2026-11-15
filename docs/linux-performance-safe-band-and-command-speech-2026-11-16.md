# Linux Performance Tools安全字幕带与命令名试听（2026-11-16续接）

## 范围与实物

仅处理用户指定的iJ_eIsA5E1U（Linux Performance Tools!）。以下媒体、试听及ASR观察已在11月15日生成；11月16日恢复后只做记录、身份核验和Git提交/推送，不重做已成功的编码、TTS或ASR。不展开播放列表、发布、覆盖正式工程或重算并行总量。

- 新完整上传候选：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-iJ_eIsA5E1U-20261115/safe-band-v1/candidate/upload.mp4`。
- 复用且未重制的母版：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-iJ_eIsA5E1U-20261115/group-phrasing-v1/candidate/master.mp4`。
- 独立断点：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-iJ_eIsA5E1U-20261115/checkpoint-safe-band-v1/execution-progress.json`；Git完成后另存`post-git-final-receipt.json`与`sealed-final-evidence-v1.json`，不倒改原始运行出口。
- 两项控制分别是`data/translation_reviews/iJ_eIsA5E1U-safe-band-20261115.json`和`data/translation_reviews/iJ_eIsA5E1U-clarity-terms-20261115.json`。以下相对外部路径均以请求目录中的`safe-band-v1`为根。

**两种分支必须区分：上传候选实际沿用上轮12组已成功的中文音频；本轮三组新试听并未装入它，也未选优或正式采用。**

## 底部字幕安全带

使用未修改的`upload_export_v3.geometry/subtitles/verify_script/raster_check`与`caption_layout`。源画面1920×1080仍在(0,0)，无缩放、裁剪；新增144像素黑色底带，输出1920×1224。字体Noto Sans CJK SC、35像素、底边距20、描边2。77个实际字幕文本与样式全部栅格检查，字形y范围1139..1205，均处于新增带内。文本及非空白字符保持，ASS百分之一秒格式仅带来继承的≤5毫秒量化，不是编辑重定时。

上传候选为一个自包含MP4，420.452秒、1条中文音轨、无软字幕、11个内嵌章节。母版仍为原有2音轨/3软字幕，不复制重制。上传候选74801437字节，SHA256为`76f47417d5c6432d4db32e9ca6c37418502668216feb19f2a668dc8878fb37a3`。

独立`independent-media-v1.json`核验了9856个中文AAC包的载荷、PTS/DTS/时长精确相同，解码中文PCM也一致。解码有10091520样本，声明时长对应10090848样本，AAC尾填充另计。10510个解码帧的精确时间与时长、11章标题和时间保持，完整解码实际出口0。原画上部区域全部10510帧SSIM均值0.9993213934348278、最低0.983088；新上传视频确实重新有损编码，不能称原像素或视频编码包无损。七个有限契约覆盖几何、文本守恒、缺屏、错字、位置覆盖及无效时窗拒绝，全部通过。

17张实际上传版JPEG在11月15日已逐张由Agent打开，绑定真实帧PTS与对应字幕窗；长边均≤1280、最大实际32707字节。`image-observations-v1.json`记录四处既有采样发现的对照：cue3导读底注、cue36第三个Process标签、cue47的top列表底行、cue71总览图底部标签/箭头现在露出。只批准这些局部观察，不推断全片人眼验收。字幕字体较旧覆盖版小，不承诺手机或任意播放器的可读性。

旧关键/指标等跨屏边界和旧URL拆分没有重排；母版软字幕位置也没有修改。全屏幕逐词画面对齐和完整视觉验收仍未完成。

## 既有语音的无提示中文回录

已有本地medium和small模型分别观察12个完整意群，共24份结果。实际参数均为`language=zh`、`beam_size=5`、`temperature=0`、`word_timestamps=True`、`condition_on_previous_text=False`、`vad_filter=False`、`initial_prompt=None`、`hotwords=None`；CUDA/int8，cpu_threads=4，不下载新模型，也未重新识别弱原声。

两个模型均恢复五处“输入输出”。但原vmstat被写成WinSTAT/WindStat，原iostat两次均为ISDAT。这些是转写歧义的线索，不足以证明原音确有错误。确认/缺认、计数/技术、栈/站、基线/极限等差异同样保留，不为降低回录误差把规范中文改成错字。

模型身份、输入、原始结果和每组WAV引用见`chinese-recapture-{medium,small}-v1`与`speech-audit-review-v1.json`。首次medium在baseline完成前启动，因缺少`baseline-v1.exit`真实出口1；失败发生在输出目录创建和模型推理之前。确认baseline0后，medium-v2及small-v1真实0，每个成功组仅实际推理一次，原失败出口不改写。

## 三组独立完整试听

三组保留源文、角色、完整原窗和其余上下文，只研究中文措辞及字母拼读，不添加技术结论或借用别人的时窗。

| 完整组 | 允许变化 | 完整语音时长 | 原窗余量 |
| --- | --- | --- | --- |
| 6–7 | “分析前确认确有性能问题至关重要”改为“分析前务必确认性能问题确实存在。这样……” | 25.224秒 | 2.335秒 |
| 16–17 | spoken中的vmstat一次改为`V M S T A T`，显示仍为vmstat | 27.504秒 | 1.815秒 |
| 18–19 | spoken中的iostat两次改为`I O S T A T`，显示仍为iostat | 23.256秒 | 4.344秒 |

6–7保留真实问题、务必确认的强度、省时和聚焦实际问题，以及同组十秒网页/查询超时的完整信息；避免“确认/确有”拗口邻接是独立编辑理由，不依据ASR把确认写成缺认。命令只做ASCII字母拼读，不猜中文谐音。

完整WAV分别在`clarity-terms-audition-v1/group-6-7.wav`、`clarity-terms-audition-v1/group-16-17.wav`、`clarity-terms-audition-v1/group-18-19.wav`。音色仍为云溪，正常`+0%`、默认`+0Hz`；完整MP3全解码，只做等长增益，无截头尾、额外变速、借窗或重叠。`independent-clarity-v1.json`独立按完整解码与增益复现逐样本核验，并检查完整返回字词与允许文本变化。

这三组各用两个相同模型无提示回录一次，另有6份完整观察。两模型对新命令分别返回VMSTAT与两次IOST-AT；只做去非字母数字及大小写归一化后命令匹配，原始连字符差异没有隐藏。medium恢复新6–7的目标完整句，small仍有物必/季节省/补风捉影，以及海岸设备等同音、近音错写。不能只选择好看的转写，也不据此宣布字音、自然度、重音或情绪通过。

## 复现入口与实际终态

外部脚本已绑定输入/实现身份，历史成功输出存在时不要重跑。解释器为`/vol_1t/pyvideotrans/.venv/bin/python`，`PYTHONPATH=/home/pengdl/media-downloader`；冻结根Python未修改。

- `pipeline-v1.py`的create_plan、prepare、encode：字幕安全带计划、准备与完整上传编码。
- `verify-media-v1.py`、`finite-contracts-v1.py`、`images-v1.py`及`record-images-v1.py`：媒体核验、7契约、受限取图与逐张观察登记。有限契约的依据是已有`.log`/`.exit`，没有同名结果JSON，不补造它。
- `chinese-recapture-v1.py`：原12组两模型观察；`clarity-terms-v1.py`的create_plan、synthesize及`verify-clarity-v1.py`：三组试听及独立音频核验。
- `clarity-recapture-v1.py`与`record-speech-audit-v1.py`：新组两模型观察及全部30份源绑定审校记录。

既有17项操作保留16个实际0和1个实际1，详见新断点的metadata-registration。恢复登记首次因进展文档新前缀未显式写视频ID被断言拒绝，真实出口1，发生在metadata写入及暂存之前；补全ID后用新收据继续，旧脚本/日志/出口保留。新增恢复登记/核验/Git步骤各自记录真实日志和终态，不将重试的0冒充旧1，也不为漂亮收据重新合成、推理或编码。

## 待验与保护

无人耳试听、自动胜者或正式采用。专业发音、情感表达、自然度、重音、说话人/交接、声学逐词、全片听视、PotPlayer和最终选用仍待验；原有质量门禁与人工决定不关闭。下一步如需采用新三组，必须先完成相应选择/准入，再单独处理组内字幕投影与完整正文包装，不能说本上传版已经包含它们。

12232旧身份、31正式目标、98冻结根Python、293生产MP3及15无关未跟踪文件按基线独立复核。五共享文档旧149新增行保留且不混入本批提交，只提交本批新增前缀、两项审校控制与本说明；媒体、模型、缓存、日志和收据均留在外部。Git提交/推送和远端一致性写入新断点，不在提交前宣称完成。持续优化目标保持active，不声称后台自动续跑或其他并行任务停止。
