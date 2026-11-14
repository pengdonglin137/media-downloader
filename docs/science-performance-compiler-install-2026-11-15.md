# 性能报告：三处否定、强调与上线对比正式采用

仅续做`cjiUXZh0ZCo`的98、113、114。此前完整正常速率试读和13显示短句现已进入中文母版与烧字上传版；来源选择依据和完整作者稿仍见`science-performance-compiler-explanation-2026-11-15.md`，没有修改其阶段快照或把试读选择称为声学胜者。

## 当前成片

- 母版：`/home/pengdl/Videos/translated_backlog/Science of Performance 2019_中文配音.mp4`，566100374字节，SHA-256 `910f4f29987fdd3bf09c0b2357fedab2bbd9fadf38c311d0c02f216e884b5bc1`。
- 上传版：`/home/pengdl/Videos/translated_backlog/Science of Performance 2019_中文上传版.mp4`，452343948字节，SHA-256 `f2a2685f0f73a8647686eddb9dc568f94279a36d21238c2afb9e1d964f8c21e4`。
- 40.2秒中文有声AI导读、85内嵌中文章节、5572.982秒总时长保持；母版双音轨/三软字幕，上传版单中文主轨烧字，每用途一个自包含MP4，不增加交付外挂。

98恢复修正主语后的`they don't`否定，保留好代码、并行化/向量化、相对固定的编译器和加库；113恢复“对我”及两次“很重要”，不再弱化讲者对开发者的否定；114恢复开发者主语、我们推送更新/从零开发应用的动作及上线前后对比。它们是历史讲者的论述，不背书为对所有开发者的概括或推迟安全工作的建议。95、97、111的来源疑点保持未决，没有改英文、来源角色或借用相邻原声窗。

## 真实制作与安装

本轮直接复用三条完整试读，不新增TTS或ASR，也不修改绑定适配器、98冻结根Python或293旧生产MP3。原媒体作业连续完成prepare/encode/verify/check，七个实际编码/解码操作均0；没有因等待时间重启成功步骤。

正文和母版相对各自既有供体的视频、原声音轨及英文字幕编码包与精确有理时序保持；两版中文AAC包和完整解码PCM一致，167187解码视频帧的完整PTS/时长安排一致。三编辑窗及23原声区的固定偏移信号诊断通过，两版完整解码无错误。这里不把原声音轨相对既有供体的保持，外推为加过导读的最终原声等同最初源AAC。

既有sample-grid的321严格原窗、23实际原声区、39占位及窗外/源PCM合同保持。独立逐行比较321项翻译，只有98/113/114按作者稿变化，其余318项保持；朗读覆盖仅三项显式变化，英文和翻译任务字节保持。13短句保留完整词流和实际WordBoundary证据，不代表逐词声画验收。

本轮新`history-authority-v1.json`验证当前计划的精确历史归档；安装prepare/apply/check均0，31项事务committed、23写入意图。1100旧身份中1077原位保持、23绑定精确事务归档；再独立核对上一轮1037身份为1014+23，更早826身份为803+23。旧媒体、旧文本、旧私库及失败证据不删除，新正式媒体的字节、mtime纳秒和SHA与已完整验证候选精确一致；安装不重新编码或全解码。

预检93扫描，媒体候选94扫描，正式95扫描；正式私库为6005问题、10712出现、41词、60工程、0声音决定、0修复解决。独立保留所有旧不可变行、所有无关工程指针，只有当前工程指针推进到实际当前扫描；不是要求可变指针永不更新，也不是删除旧扫描。当前191声音候选与12原声尾句规则分类保持，分类不是dismiss或人耳批准。

## 图审与有限播放器证据

13张实际上传版JPEG逐张打开，覆盖全部新显示短句；均960×540，最大31735字节、长边≤1280且每张≤32768字节。样图字幕在画面内、未见切边或分行；既有小字号仍保留，不能由此批准所有设备可读性、全片视觉或逐词声画。取帧记录请求时间及真实导读偏移，但未独立核实每张的精确帧PTS。

候选与正式路径有限VLC各真实0：逐版85章节枚举、六次跳转及音轨/字幕切换等有限API检查。保留70/72条匹配诊断，分别有15/16条`no frame!`，原因未解决；此前59/57及更早55/56/68等旧诊断不因本轮覆盖。dummy音频不是人耳，六张/轮VLC内部快照也没有另作图审；不称无告警、全片播放或PotPlayer通过。

独立核验首轮把数据库快照元数据当表迭代而真实1，第二轮错误要求当前工程指针也不可变而真实1；两次日志/实际工具出口证据保留。第三轮明确验证全部旧不可变行、无关指针与当前实际revision，真实0；只修正只读核验契约，不放宽生产门禁、不改数据库或重做媒体。不是所有探索性命令均成功。

## 当前断点与检查

请求根为`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/compiler-explanation-v1`。候选/正式独立证明、图审、VLC结果/完整诊断与31项事务各自绑定实物；新入口为同请求`checkpoint-compiler-installed-v1/execution-progress.json`，登记、独立核验和最终运行收据分开保存。提交后的真实远端状态读取该checkpoint的`git-final-verification.json`，不把提交前快照当push证明。

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
REQUEST=/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114
NEW=$REQUEST/compiler-explanation-v1
$PY -m media_workflow.editorial_history install "$NEW/history-authority-v1.json" \
  --operation check --config "$NEW/install-config-v1.json" --output "$NEW/install-v1" \
  --stage-config "$NEW/stage-config.json" --stage "$NEW/stage-v1"
```

以后只读检查沿用当前显式权威；不裸套旧CLI，不重跑prepare/apply，不改绑定作者稿或先前阶段收据。专业词/人名发音、情感自然度、全片人耳/视觉、逐词声画、VLC诊断原因、PotPlayer与最终批准仍待完成，持续品质目标active。不扩播放列表、不发布，不修改Linux等其他请求、共享队列或并行总数；选择性提交自身记录并保留其他未提交改动，不承诺后台自动续跑。
