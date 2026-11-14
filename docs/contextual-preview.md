# 原画与原生音频的完整交接预览

`media_workflow.contextual_preview`为已经存在的完整AI纠正声音制作「原画原声 → 明确标识的AI卡片 → 原画原声」内部预览。它不是正文翻译器，不选声音赢家，不修改正式工程或安装正片，也不重新执行TTS／ASR。

```bash
python3 -m media_workflow.contextual_preview render PLAN.json --output /outside/repository/new-preview
python3 -m media_workflow.contextual_preview check PLAN.json --output /outside/repository/new-preview
```

## 明确的时间和来源契约

计划类型为`av-context-insertion-preview-v1`，scope固定为`isolated-audition-no-production-installation`。完整字段实例见`data/translation_reviews/0sZlaQr3e2w-contextual-preview-20261114.json`。

- `source`、`native_ledger`、每个case的`waveform`／`card`以及`evidence`均绑定完整文件身份。原生参考必须是此前真实完成的float32音频framehash，不以容器声明代替实际样本。
- `profile`明确原片视频／音频流号、尺寸、整数帧率、视频time_base、采样率和声道。当前只支持yuv420p、零起点、原生单／双声道以及每视频帧对应整数视频tick和整数音频样本的来源；不猜测VFR、非零起点或不共格时钟。
- `context_frames`为原片绝对、从零计索引的半开视频帧区间，最多十分钟；`at_frame`必须位于其中。每个case是独立替代方案，最多四条；完整PCM16纠正声音最多40秒。配置中的字幕参考秒数与实际预览交接帧分别保留，不能互相冒充。
- 输出只能位于仓库与正式`.zh_work_*`工程之外，不能包含任何输入。未知、失败、部分、移动或输入已变化的目录不自动覆盖；已成功`render`重入和`check`只读核验，不重编码媒体。

## 实际生成和检查什么

仅从语境附近预滚动解码，不重复完整原片原生音频观察。先提取覆盖两端的完整原生音频参考帧，逐帧SHA-256对照此前全片framehash；再精确裁出语境，保留其完整原生float32样本。视频独立直接解码参考和无损重编码语境比较，保留原视频time_base检查全部解码帧、PTS和duration，不用较粗输出帧率掩盖时间误差。

纠正声音从完整原WAV显式重采样到原片采样率；单声道到双声道时明确复制声道。它不再与原24kHz文件声称逐字节相同，而是与完整独立转换结果比较。只在完整声音**之后**补到下一个源帧格点所需的零样本，不裁声音、不借原声窗、不做atempo、增益或交叉淡化。卡片画面通过滤镜自身的有限帧区间结束，不用全局`-frames:v`提前终止音频输出。

每条完整MOV预览逐样本比较原声前段、完整转换后纠正及显式尾静音、原声后段；原声所有样本各保留一次。全部视频帧分别等于原语境前段、完整卡片帧和原语境后段，并检查精确有理PTS网格。音频另查原生时间戳从零连续至所有样本结束。音频为PCM float32、视频为无损H.264；这是内部精确比较格式，不是交付编码，更不是源AAC载荷保持证明。

新的`review_card`复用完整审校显示稿生成预览卡片，同时显示实际暂停帧及精确秒数、不同的字幕参考和未选胜／未安装状态。过长正文拒绝裁切；原片文件未改与本预览确实暂停原画是两件事。卡片、真实视频取帧和完整声音仍需实际审看、听辨，不能只据配置批准。

## 收据与范围

每个FFmpeg／ffprobe命令独立记录参数、PID、开始／结束时间和实际出口；中断终止所拥有的子进程并保留失败目录，不伪造零。manifest绑定全部产物、实现和工具身份；检查同时拒绝将原目录的身份拿来认证已移动文件。

生成夹具覆盖完整原画／原生音频、两种插段长度、明确补静音、全段转换、精确时戳以及只读重入；另有输入、目录、参数、身份、卡片超长和媒体篡改拒绝检查。首轮夹具确实暴露`-frames:v`导致的声音截短，失败输出和旧共享实现归档保留；修复后才允许真实素材进入新入口。有限夹具不是全局完备证明。

即使全部低层比较成功，`source_handoff_approved`、`source_speakers_verified`、发音／情感／人耳批准、自动选胜和正式安装仍为false。格点共时、ASR间隙或低幅波形都不能证明无人说话、角色相同或听起来衔接自然。最终中文正文、两中文主轨的原声回填、母版／上传版及全片验收应继续使用各自明确的生产契约。

## seek原生参考不一致时的明确恢复

Linux性能实际候选首次在第113584128原生样本处未能对上全片float32参考，生产外层真实1，即使所有媒体子命令已经0，也不能把它报告为完整通过。这里只确认带seek与既有顺序参考不同，尚未证明底层解码器差异的具体原因。失败媒体、实现、日志与出口保留，不修改容差或把PCM字节比较换成较弱的相关性。

`media_workflow.contextual_native_repair`为这一精确失败提供独立、可恢复的两个阶段：

```bash
python3 -m media_workflow.contextual_native_repair extract PLAN.json --output /outside/new-reference
python3 -m media_workflow.contextual_native_repair check-reference PLAN.json --output /outside/new-reference
python3 -m media_workflow.contextual_native_repair repair PLAN.json --previous-output /outside/failed-preview --reference /outside/new-reference --output /outside/new-repaired-preview
python3 -m media_workflow.contextual_native_repair check PLAN.json --output /outside/new-repaired-preview
```

`extract`不使用输入seek，从原片开头顺序解码音频至所需语境尾界，保存包含两端的完整原生参考帧，并再次逐帧对照**已有**全片framehash。这是新语境PCM的必要取窗，不重跑全片framehash观察、不重做ASR／TTS。成功参考重入只核验，不重复解码。

`repair`只接受同一绑定计划、预期原生参考失败以及全部实际成功的既有媒体子步骤，不能把无关失败或已成功生产重贴标签。接管时绑定全部旧文件的当前身份，明确是后置接管而非捏造旧前置身份；旧目录保持。原画视频轨仅stream-copy复用，完整纠正转换及显式静音也复用；用顺序参考PCM重建原声前后段后重新封装音轨。新MOV再完整解码视频／音频和时戳，重新执行所有原画、原生样本、完整纠正及连续时间线比较。

修复有15项生成夹具检查，原预览另有38项及8项补充检查；模拟失败明确标记为夹具，不冒充真实源片失败。最终必须检查真实修复终态和当前实物，不能只据这些有限测试宣布恢复成功，更不能据此批准听感或F08生产准入。

## 原生音频正确但拼接画面仍不相等

实际原生修复v2的两条完整PCM和原生音频时戳已经通过独立复核，但每条旧H.264拼接有1747个位置未能与完整源帧比较相等，外层仍真实1。视频轨stream-copy确实保持了旧候选编码包及其时戳，但这不能倒推旧候选已保持原片解码帧；差异的底层原因尚未确立。源语境缓存本身的6527帧仍与源解码参考相等，不能把正确缓存与未通过的拼接成片混为一谈。

`media_workflow.contextual_picture_repair`仅针对这一明确画面比较失败，复用已证明的原生PCM、源画语境和完整纠正转换，另走FFV1／PCM float32／NUT内部预览路径：

```bash
python3 -m media_workflow.contextual_picture_repair repair PLAN.json --generation /outside/first-preview --previous-output /outside/native-repair --reference /outside/sequential-reference --output /outside/picture-repair
python3 -m media_workflow.contextual_picture_repair check PLAN.json --output /outside/picture-repair
```

该入口不重新TTS、ASR或顺序取声，不覆盖两轮失败媒体；新视频完整解码后仍须对上全部源帧／卡片帧以及精确有理时间，音频完整PCM与原生连续时戳也必须保持。NUT容器time_base可能换用等价有理刻度，不能用帧率四舍五入掩盖差异。视频编码器时间基使用`-enc_time_base:v`，不将视频刻度误施加到音频编码器；首轮生成夹具确实检出了未限定流选项造成的一样本时戳跳变，该真实1与旧实现保留，修复后的13项有限夹具通过才允许实际源片运行。

新路径仍以真实当前终态与实物为准；选择不同的内部无损编解码器不是放宽像素或样本门禁，也不是证明最终MP4、播放器兼容性或听感已经通过。

本片当前两条NUT的完整解码尾界分别为3607/15、3611/15秒，容器`format.duration`却报240.465850、240.721270秒；声明值与完整解码值分别登记，不伪造相等，不将该差异当作静音或裁音许可，播放器尾界仍待实机验证。
