# Perf：专业缩写与亲近片尾的隔离双版本候选（2026-11-15）

仅 M6ldFtwWup0，Introduction to Perf - Linux Performance Analysis。2017-04-03为上传日，录制日期未确认；历史命令不背书2026环境。目标仍active，正式85单元工程未采用此53项隔离工程，不扩播放列表、不发布、重算全局或增加交付外挂。

## 来源与表达

- 第3项显示CPU不变，完整朗读等义展开中央处理器；保留wiki、perf/perf_events、性能计数器、追踪点、k/u探针与轻量级剖析。旧C P U读法未被证明错误；本句候选不是全局词典修改。
- 第52项完整表达为“希望这段介绍能帮到你！如果有问题，欢迎在评论区告诉我。不管是纠错、指出遗漏、补充内容，还是建议删减，或者有其他意见，都欢迎留言。谢谢观看！”朗读在“告诉我”后用分号，字面事实不变。问题、评论区、correction/omission/addition/subtraction/comments与致谢均保留。
- 两句复用成功完整试读14.256／14.376秒，normal rate、+0Hz；342144／345024样本，原窗余330816／271296样本。gain-only后完整放入原绝对样本窗，无截尾、伸缩、借窗、额外atempo或新TTS／ASR。未选+5Hz或CentOS拼字候选。
- 旧计划human_selected:false、production_eligible:false不改。新admission只批准Agent来源等义审校和隔离技术候选，不是假造通用speaker_review、听辨选优或情感批准。片尾停顿提示仍待人耳。

## 成片与字幕

- 两用途各一个自包含MP4：母版双音轨／三软字幕，上传版单中文主轨烧字；1140.538秒，1251帧／41.7秒三场景有声导读、21内嵌中文章。导读实际声音41.694秒、1000656样本，视频偏移1000800样本；末尾144样本补静音。导读文本／声音／视频不重做，仅计划和音频元数据重绑当前源哈希。
- 中文181屏；3／52使用实际返回完整词起点，末屏到完整WAV止点，不冒充最后音素检测。37保留804.180–808.080与808.080–812.748两屏精确修复，其他中文不变。英文179屏字面／时间不变；双语179屏中只更新52中文，原英文／原片时间保持。
- 原声30的源窗625.630–645.070秒、466560样本仍实际在两用途中文主轨；不是只在备用轨。其预AAC PCM与父版精确相同，37也精确相同；最终按真实1000800样本偏移逐区核验3／52／30／37信号，结果见机器收据，不是角色／可懂度人耳批准。
- 原生正文复制父版32963视频包／47323原AAC包；最终母版复制父版34214视频包和完整备用原声AAC，包载荷及精确有理PTS/DTS/时长核验。两用途中文26733 AAC包、27373568解码样本完全相同；各完整解码34214帧及全部音轨通过。只复用既有正文逐帧证据，未新跑全片framehash。
- 新中文经过AAC重编码，窗外最终AAC／PCM不声称等父版。最终备用原声复制父版不等于初始源AAC位级保持。正文仅移除48000已证实全零的附加尾样本，不截实际声音。

## 实际验证与未决

- 精确WORK运行时、裸原生拒绝、其他WORK委托、嵌套拒绝、成功／异常恢复、load／质量门禁保持共7项有限检查，不冒充全套单测。新原生scan／evaluate／require／render实际执行；门禁eligible_with_unreviewed_candidates不是声音验收。
- 新私库1扫描／4项：执行14／41、CentOS46和52短尾停顿；0决定／0修复解决。旧库6项和父版5项均保持。CPU新提示缺席仅反映本句改写，不是旧提示被dismiss、发音已正确或人耳选择证明。
- 13张实际上传版限额JPEG逐张Agent查看：9张整图为wiki、CPU计数器、探针、剖析、37及4张片尾，另4张实际字幕底裁。wiki整图为满足字节限制缩至640px、中文字面不够清楚，另抽底裁核对，不能把期望文本当作整图已读事实。长边≤1280、实际≤32768字节；视读记录逐图绑定。仅局部图审，不是全片视觉／逐词对齐。VLC自动生成PNG未逐张Agent查看，不混计。
- 有限Linux libVLC/Xvfb/dummy audio检查通过；本次日志72行匹配error|failed|no frame|timestamp，诊断未解决。旧76／74／64保留，API通过不是无错误／人耳／真实GUI／PotPlayer通过。
- 全片人耳、音素／情感／角色／可懂度、全片视觉、逐词声画、PotPlayer、正式采用与最终批准仍未完成。Linux 0sZlaQr3e2w的27holds／23markers／criticalAI-F08保持。

## 失败与恢复

首次build-pair-v1中母版编码子步骤真实0，但新两字幕语言und触发原生核验真实1。旧MP4、脚本、日志及1均保留；v2只把成功编码流修chi/eng/chi后重封装，再制作此前未执行上传版。首次编码与v2中文AAC完全相同，不重跑成功TTS／ASR／正文／导读／编码，不修改冻结根Python或已绑定实现。

首次取图在第一张密集wiki的JPEG字节检查处真实1，未保存成功JPEG，不计已看。v2在质量与长边之间明确逐级限额，另存9张实际合规JPEG；旧错误和脚本保留，未保存PNG不计作图审。

首次最终核验把标准package manifest也当作含passed键的核验报告，真实KeyError／1；原脚本和出口保留。v2按标准包结构独立检查，不给旧manifest补造passed，不重做任何成功媒体或扫描。

## 入口

- 母版：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-M6ldFtwWup0-20261114/editorial-voice-3-52-v1/media-pair-v2/master.mp4`
- 上传版：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-M6ldFtwWup0-20261114/editorial-voice-3-52-v1/media-pair-v2/upload.mp4`
- 请求运行时：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-M6ldFtwWup0-20261114/editorial-voice-3-52-v1/RUNTIME.md`（裸旧CLI不支持新来源）。
- 断点：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-M6ldFtwWup0-20261114/checkpoint-editorial3-52-v1/execution-progress.json`，独立核验、登记、最终收据分开绑定。
- 保持11522旧身份／31正式目标／98根Python／293旧MP3及其他130行／13文件；只选择性提交本批8文件。成功媒体不重跑，无后台自动续跑声明。
