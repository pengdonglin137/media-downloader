# 平台与功耗12项完整候选正式采用（2026-11-15）

本轮为cjiUXZh0ZCo的276、277、278、280、281、282、283、284、285、286、287、288，复用既有12条云溪正常速率完整语音，无新TTS或ASR。不是将持续优化目标缩为本批，也不宣布任何发音或情感听辨胜者。

## 来源与字幕

五个来源单元的九组件明确校正：280的Locks and Waits和Intel Platform Profiler；282的General Exploration；283的Intel Platform Profiler和Intel Application Performance Snapshot；284的Storage Performance Snapshot；288的turbostat、cpupowerutils和Intel Power Governor。Power Governor不是Power Gadget。

来源提案补齐真实evidence/JSON3身份，再由原生source_revision实际暂存；原始来源、原始复数组件、精确备份和321自映射保留。规范单一跨度未改变完整前后文本或来源时窗。

原生英文字幕投影真实拒绝跨块替换，未删除难项或修改冻结实现。新增独立组件契约逐词定位，合并相交的最小相邻旧块；909–910和917–919分别合并，其余组件仅改所属块。10旧块形成7校正组，全轨1028→1025；所有未选英文块的解析文本和时间保持，不声称旧字节、序号或逐词声画保持。中文/双语36段仍绑定实际合成词界。

45有限用例8接受37拒绝，另有真实输入与精确运行时分派/恢复核验。入口为media_workflow/source_component_projection.py；新kind和profile不伪装旧原生清单兼容。

## 音频与媒体实证

完整12条原始语音按既有增益/float32量化契约组装，逐样本验证所选旧所有权窗内等于完整候选加静音余量，窗外等于旧音频；不是声称未增益的原始候选PCM等于生产PCM。全部23原声区固定绝对样本与原PCM相同，39预留单元保持。

两用途候选完整解码通过；167187视频帧日程、从旧成片复制的视频/原声AAC包、两版中文AAC和解码PCM一致，12编辑区加23原声区共35固定偏移诊断通过。原声包比较以旧正文/旧母版为供体，不外推成从原始上传至最终容器的全链位级证明。

15张实际上传版JPEG逐张打开，长边≤1280、实际每张≤32768字节；覆盖12编辑单元中的15/36字幕段，仅局部画面/字幕检查，不是全片或逐词对齐。有限VLC在受控Xvfb/dummy音频下运行，API检查通过，但实际日志仍有buffer/slice-header/no-frame及5次时间戳转换诊断；字面计数可能重叠，原因未证明。完整FFmpeg解码无错误不能消除VLC诊断，也不能推导无错播放、人耳试听、GUI全片验收或PotPlayer实机通过；原日志和独立诊断收据保留。

## 正式采用与历史

31个旧正式目标按内容、mtime精确归档后事务安装；母版/上传版与候选字节/mtime相同，完整解码和包日程证明以文件身份转移，安装未重编码。仍321来源窗、40.2秒导读、85章、总5572.982秒；母版双音轨三软字幕，上传版单中文轨烧字，未发布或扩播放列表。

- 母版：565967667字节，SHA-256 `7081371353425a1925e486bce16ebf4c8bb00b4ad00647cb9c17581140bda417`。
- 上传版：452286964字节，SHA-256 `2f1873681ec5c556e7fa787d1cff01bc952f60cb6bef3f55bcbb9fcbbcea1b21`。
- 正式数据库104→107扫描、8249问题、14582出现；所有旧行和决定保留，186未复核候选/12非dismiss分类/0决定/0解决。
- 4964不可变文件、98冻结根Python、293既有生产MP3及13其他任务文件保持。独立复核与完整当前质量权威均通过，不把allowed理解成人工语言/声音批准。

首次source-stage缺evidence、旧投影跨块拒绝、独立v1把voice字符串误当对象的失败及run-stages外层1均原样保留。安装独立核验v1又把候选库106扫描误当作正式旧库104而1，原finalize外层随之1；新v2只读核验明确正式104/候选106/安装107三级并恢复。六个非零步骤均保留，不覆盖失败，不重做成功语音、媒体或安装事务。

## 未决与续做

216/217否定落点、227工具漏项、248下载对象/否定、310监督方式、317作者姓氏仍未决；299保留原讲vertically，294原问和321掌声继续真实原声。Linux0sZlaQr3e2w的27holds/23markers/critical F08保持。

专业发音、情感、全片人耳/视觉、逐词声画、PotPlayer及最终语言声音批准仍未完成。下一步继续其他完整批次或获取未决来源的更强证据，不重做本批成功生产，不重新运行已有弱声ASR追逐识别结果。

外部根：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/platform-power-adoption-v1`。显式入口见同目录RUNTIME.md；正式当前质量见current-quality-after-install-v1.json，安装独立复核见post-install-independent-v2.json。登记/进度/收据位于`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/checkpoint-platform-power-adoption-v1`。

本轮仅提交本文、两个审校索引、一个新模块及五共享文档的自有段落；其他任务130新增行/13未跟踪文件保留，不改共享队列或全局总数，不声称后台自动续跑。目标active。
