# Linux Performance Tools完整意群朗读候选（2026-11-15）

## 范围与产物

只处理用户给定iJ_eIsA5E1U，不展开播放列表、发布或修改队列总数。此为有实物的隔离编辑候选，并非正式选用或人耳批准。

- 候选母版：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-iJ_eIsA5E1U-20261115/group-phrasing-v1/candidate/master.mp4`。
- 候选烧字版：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-iJ_eIsA5E1U-20261115/group-phrasing-v1/candidate/upload.mp4`。
- 控制：`data/translation_reviews/iJ_eIsA5E1U-group-phrasing-20261115.json`；实现：`media_workflow/grouped_phrase_refinement.py`。
- 独立断点：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-iJ_eIsA5E1U-20261115/checkpoint-group-phrasing-v1/execution-progress.json`。

## 实际文字和语音变化

既有15项含14个完整双单元语义组和单项1；仅12组各删一个旧字幕切分处的逗号，例如“问题不在，性能”改为“问题不在性能”、“间歇性，变慢”改为“间歇性变慢”。原事实、原窗、音色、来源角色说明不变；五处I/O只在朗读展开为“输入输出”，规范显示不换字。保留五秒查询/预期、十秒网页、一/五/十五分钟负载、80端口、历史数据与迭代调查的所有限定。原英文自动字幕及其旧错词未在本候选中修订，不把文字顺畅当作原音已核实。

12个完整MP3各只合成一次：正常语速、默认零音高，全部返回词文本覆盖完整spoken，完全解码后仅按既有规则做等长增益。最小原窗余量2.159秒、最大7.008秒；不裁头尾、用atempo或借邻窗。三未改项[1]、[8,9]、[28,29]及其余窗外PCM逐样本保持。独立重解码12个MP3并重新计算增益，逐样本对比完整候选WAV，而非仅信任合成器统计。字元数据不证明发音、情绪、真实说话人或末音素听感。

## 字幕和媒体证据

中文/英文/双语实轨回读77/91/77屏。中文删指定逗号并按新引擎完整词起点重新定位组内屏，不能切开返回词的旧屏会合并；末屏覆盖至完整WAV末尾，不猜最后音素。英文轨逐项保持；双语英文及其旧时间保持，故双语不是新中文词级对齐字幕，轨内已标明该限制。未改窗字幕保持。

两份候选各为一个自包含MP4，420.452秒，沿用20秒有声AI导读和11内嵌中文章。母版2音轨/3软字幕，烧字版单中文主轨。原母版10510视频包与18109备用原声AAC包的载荷、PTS/DTS/时长及编码头保持；两候选中文AAC包/解码PCM一致，10510解码视频帧的pkt_pts和时长相同，两份完整解码成功。此比较以既有母版为父代，不额外声称其原声与下载原片位级相同。AAC解码器尾填充样本数与容器声明时长分别登记，不把二者混为一谈。

原正文9653760样本；只为匹配旧母版声明长度移除42912个已证全零EOF样本，未删语音。导读479333样本完整复用，加667零样本到480000样本；全长10090848样本。

17张实际烧字JPEG逐张由Agent打开：覆盖12个改逗号组、I/O规范显示和复用导读/结尾；8张直接复用首次成功图。每张实际长边≤1280、文件≤32768字节，采样帧的真实PTS落在绑定字幕屏内。只是这些局部图的文本与遮挡观察，关键/指标等旧跨屏显示边界没有在本轮重排，不称完整视觉或逐词声画验收。

图审实际发现：cue3的导读底注、cue36的Process标签、cue47的top列表底行、cue71的全景图底部区域与烧字字幕相交。四处均登记待修，不给候选无挡图或视觉通过结论；应另做安全字幕带版本，复用本次成功中文音频，不为修显示重跑TTS。

## 命令与失败保留

用`PYTHONPATH=/home/pengdl/media-downloader /vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.grouped_phrase_refinement`执行三个阶段，依次synthesize、assemble、package，参数`--plan /home/pengdl/media-downloader/data/translation_reviews/iJ_eIsA5E1U-group-phrasing-20261115.json --output /home/pengdl/Videos/translated_backlog/.workflow_logs/request-iJ_eIsA5E1U-20261115/group-phrasing-v1/candidate`。只有显式`--resume`允许恢复同身份中断合成，完成后拒绝重做。每个实际包装命令另有持久终态收据。

有限契约14项覆盖源文、原窗、角色、速度、审批、父媒体身份、重复组、输出隔离、缩写跨屏和完整字边界。首次构造补丁不完整、随后缺测试文件调用真实2；测试捕获I/O跨旧片段的累计规范化错误真实1，修复后14/14真实0。媒体核验初版用本机FFprobe4.4不输出的frame.pts而1；新核验改用实际pkt_pts并确认等于best_effort_timestamp，复用已成功的完整解码/包探测。首轮取图第九张无法只降低JPEG质量达到限额而1；保留原日志/八张成功图，后续压图允许缩放，未重做媒体。所有旧失败出口保持，不把重新核验当原步骤成功。

## 未批准及保护

没有人耳试听或声音优劣结论。情感表达、专业术语发音、源角色/换人、声学词级对齐、全片视觉、PotPlayer实机和最终选用仍待完成；未绕过正式state、旧门禁或用户决定。未自动覆盖正式字幕、朗读、TTS缓存及成片。

11975旧身份、31正式目标、98冻结根Python、293生产MP3和15无关未跟踪文件另行独立保护核对。五共享文档既有149行不纳入本次选择性提交。媒体、缓存、日志与核验收据留在外部目录；本轮代码、审校控制及文档提交并push，远端身份另记提交后收据。总目标继续active，不承诺无人值守后台续跑。
