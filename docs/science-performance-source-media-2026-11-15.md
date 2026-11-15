# 216/217实际原声双版本安装

本轮只续`cjiUXZh0ZCo`的成功来源暂存及字幕预检，不重做其他视频、不扩播放列表、不发布平台。当前母版和烧字上传版已实际采用216/217完整原声，不再仅是独立候选。

## 正式媒体

- 母版：`/home/pengdl/Videos/translated_backlog/Science of Performance 2019_中文配音.mp4`，双音轨／三软字幕。
- 上传版：`/home/pengdl/Videos/translated_backlog/Science of Performance 2019_中文上传版.mp4`，单中文主音轨／烧字。
- 均为5572.982秒、40.2秒有声AI导读、85内嵌章节，每用途一个自包含MP4；无新增交付外挂或学习页。
- 216/217复用3707.01—3745.41秒、38.4秒／921600样本实际来源PCM，源`0:a:0`、0 dB，正文前精确加40.2秒偏移。无新TTS／ASR／PCM改写。
- 正式24来源区／41项，321严格原窗、316 normal含非TTS占位／5既有rate_up、21EOF零样本保持，不把占位项称新正常合成。

216诚实显示定义与画面冲突，不向肯定口述猜补`not`；217保留“这并不是剖析的含义”及指代待核。4／6句字幕来自明确ASR锚点，不假造TTS词界。`application`／`memory`略跨既有边界及`understand`／`extend`分歧仍未获人耳裁决，不称完整词或换人边界已批准。

## 显式运行时

`media_workflow/source_reservation_media.py`保留原生制作，只向媒体局部验证提供精确来源样本视图。物理来源契约不变，不伪造TTS元数据，不全局替换`state.load`／`matches`；独立收据绑定真实读取与26项诊断。

`media_workflow/source_reservation_install.py`分别绑定原生候选及验证、实际来源profile及验证、安装前审查和历史授权；只分发精确路径，异常也恢复旧函数。实际prepare／apply／check继续原生历史安装器与31文件事务，旧31目标完整精确归档；历史计划另有不可变精确归档，不把新计划伪装旧生产者。

裸旧CLI不认识actual-source选择。不要重跑成功制作和安装，只读入口：

```bash
cd /home/pengdl/media-downloader
R=/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114
PYTHONPATH=$PWD /vol_1t/pyvideotrans/.venv/bin/python \
  "$R/source-reservation-media-v1/check-installed-v1.py"
```

同目录`post-install-independent-v1.json`核对安装后媒体、31旧目标归档、生产缓存及数据库历史；`checkpoint-source-reservation-media-v1`分别保存进度、登记、Git独立核验及最终收据。

## 实际验证及边界

- 原生媒体7个操作真实0，两版完整解码日志为空；中文AAC／解码PCM一致，26固定偏移对照通过，最小余弦0.9989228995757993。
- 完整视频帧表167187帧与前版一致，包括1206导读帧；正文165981视频包。更正前轮将167187称正文帧的口径，不改已绑定历史文档。
- 原声和英文轨包保持证明针对前版donor，不声称最终原声与最初源AAC位级保持。全部英文SRT字节和选段外ASS行／时刻保持；新登记术语断词0，旧3项登记断词不借此关闭。
- 库由125扫描经成功预检126、新媒体路径127、安装后当前路径128，现12158 issues／21371 occurrences，旧行全留。186候选、12非dismiss分类、0听辨决定解决，不重扫成功预检。
- 18样本视图加9安装路由夹具共27有限测试通过；路由夹具使用mock，不替代另外真实执行的安装。全库`unittest discover -s tests -v`真实1：无可导入的`tests`目录，未执行全套测试；日志保留，未新增框架或修改无关实现。
- 14张实际上传JPEG逐张打开，最大32639字节、长边≤1280；含10来源短句、导读、相邻两单元、片尾。只是局部图审，不等于全片视觉或逐词对齐。
- Linux libVLC／owned Xvfb／dummy audio有限API通过。日志`get_buffer() failed`32次、`thread_get_buffer() failed`16次、`decode_slice_header error`16次、`no frame!`16次、`Timestamp conversion failed`5次，字面计数可能重叠。原因未证实，不称无警告或诊断原因，不冒充人耳或PotPlayer。

9687旧证据身份、98冻结根Python、293生产MP3保持；其他130行未提交文档和13个无关文件保留并排除本轮提交。不重做成功生产、不覆盖原始视频或旧失败。

## 尚未完成

186候选仍未人工复核。216/217定义指代边界，其他26／95／97／111／248／310／317来源门禁、227读音、229情感人名、242 contention、318情感、品牌及Hadoop旧问题保持。所有人耳、专业发音、情感、全片视觉、逐词、PotPlayer及最终批准仍未取得。

Linux`0sZlaQr3e2w`仍`awaiting_translation`，27 holds／23 markers／criticalAI-F08保持。本轮技术采用不等于这些任务或持续品质目标完成，目标active；没有自动后台续跑或其他会话停机声明。
