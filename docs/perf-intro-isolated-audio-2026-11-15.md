# Perf入门：隔离配音与实录候选

## 当前范围

仅续做M6ldFtwWup0《Introduction to Perf - Linux Performance Analysis》。原片1098.838秒，2017-04-03为上传日，录制日未确认。原85项工程、JSON3和24份既有ASR观察保持；本轮不扩播放列表、不发布、不重算共享总数。

外部根目录为`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-M6ldFtwWup0-20261114/production-admission-v1`。候选工程为其下`candidate/.zh_work_yt_M6ldFtwWup0`，不是正式来源的替换。

## 已核验实物

- 53项完整候选绑定2235个既有ASR选词，43条显式来源修订和7处语义边界修订；11接缝只作文字复核。不是85原字幕词流守恒，也不是人耳逐字转写。
- 冻结原生编译与TTS成功；52语言缓存、1非TTS原声占位。统计normal=53包括占位，不能写成53句TTS；无提速、额外atempo、重叠或截尾。52缓存逐句完整解码，重建逐样本等于原生成功WAV。
- 基础WAV为24k单声道s16、26420112样本；其中原片EOF后48000样本全零。成功的`narration.json`和WAV未被修改。
- 首次v1验证只查尾界，漏了4／14／16／24／28／51／53的原生浮点取整起点各早1采样，早出的样本均非零。旧“严格原窗通过”声明显式失效，不修改成功日志或伪造原生输出。`sample-grid-v2/base-exact-grid.wav`从原生WAV取出完整句块、只移这七块各1采样；没有截尾、重合成或改冻结代码。精确起止原窗逐项独立验证通过。
- 当前`sample-grid-v2/candidate-with-actual-source.wav`实际回填625.630–645.070秒、466560样本／19.44秒的完整原声窗。绑定源`0:a:0`、24k单声道s16转换、0dB、正文偏移0；窗内等转换后的源PCM，窗外等精确网格基础，不再声称等原生浮点取整基础。v1复合WAV保留为旧证据。源AAC为44100Hz双声道，start_pts=0；不声称转换后与原生采样或源AAC位级相同。
- `ai-guide-v1/intro.wav`为三场景独立AI说明，实际1275353样本／53.139708333秒，三个缓存按原生算法逐样本重建。每场景完整连续朗读，不按显示行分开合成；53.139708333秒尚不是最终视频导读帧偏移。

## 导读与来源

导读明确不是讲者原话。原讲24的“影响最大”、27的硬件事件误分、44的混合目标用法保持可追溯，AI说明分别澄清采样占比不等于事件影响评分／精确耗时、软件事件类别，以及全系统和进程目标不能混为同一范围。文件系统名不猜成XFS，完整19.44秒实录保留，字幕注明未清处。

新导读计划由成功`admission-plan-v1.json`显式派生，不覆盖父提案。AI02加强report口径解释；AI03将父提案同音单位歧义改为吉比字节与吉字节的明确区分。画面`bs=1G,count=2`结合GNU dd自身`--help`的后缀约定推算2 GiB，不宣称实测片中文件字节数。

原生`video_package.validate_plan`通过；20个正文章节只在计划中，尚未内嵌。v4.10上游perf文档与`target.c`仅是有版本的技术参考，不是片中perf二进制版本证据；历史安装演示保留yum改apt的自纠，不背书当前安装命令。

## 仍未通过

一次原生扫描留下六候选：37／52停顿后短尾、3的CPU、14／41的执行、46的CentOS。隔离库1次扫描／6问题／0决定／0修复解决，无dismiss、listened或confirmed-correct；`C P U`字母提示也不证明发音正确。无新ASR、情感或音素胜者，无新视频编码、正式采用或双版本交付。

完整人耳、边界／说话人／可懂度、专业发音、情感、全片视觉、逐词对齐、PotPlayer与最终批准均未完成。不得将原声只留母版备用轨；未来两版中文主轨仍须实际安装并验证。本轮只有独立复合WAV，不声称这项最终要求已完成。

## 续做与保存

先读外部`RUNTIME-continuation-v1.md`及同请求`checkpoint-isolated-audio-v1/execution-progress.json`。复用已有成功音频，不重跑ASR／TTS／扫描；后续媒体入口必须显式识别复合来源，禁止裸旧CLI冒用原生narration或全局替换状态读取器。

`verified-candidate-audio-v1.json`、`source-composite-v1/composition.json`、`ai-guide-v1/authoring.json`及`independent-v1.json`绑定初轮实物；前述v1起点声明被`sample-grid-v2/alignment-and-composition.json`与`independent-v2.json`显式修正，其他真实缓存／导读证明复用，不重跑。assembler-v1错误接缝真实1保留，assembler-v2仅重做观察词拼接，没有重复ASR。10304旧身份、31正式目标、98冻结根Python、293旧生产MP3及其他130行／13文件保持。目标active，本轮是可核对的进展，不是最终品质批准或后台自动续跑。
