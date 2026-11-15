# 216/217实际原声字幕与独立质量预检

本轮继续`cjiUXZh0ZCo`的216/217完整原声候选；不是重新处理新视频、发布平台或扩大播放列表。当前只完成字幕与独立音频质量预检，正式母版／上传版不变。

## 来源与字幕

- 复用上一阶段已核验的3707.01—3745.41秒、38.4秒真实原声，源`0:a:0`、0 dB；不重合成、不重转写、不重写PCM。
- `media_workflow/source_reservation_selection.py`创建独立选择派生工程。两项`cases`明确为`actual-source-retention`，带原窗、来源、流和区间；`TTS_preview_or_word_boundaries`为null，`spoken`为`…`。不是给旧合成检查器编造MP3／WordBoundary。
- `media_workflow/source_reservation_projection.py`使用既有本地ASR的文件身份、段／词索引、词面和边界；216四句、217六句，不使用旧中文合成时刻。源窗不变，正式导读偏移仍40.2秒。
- 216仅重复已登记的定义／画面冲突提示，不向肯定原文猜补not。217保留“并不是”及指代未核提示，后续完整中文信息保持。提示不口播；不能把解释歧义说成原声听不清。
- 完整英文1019字幕原字节保持，仍采用原轨的既有分句时刻；双语选段另行ASR分句，完整当前英文词流守恒。所有窗外字幕文字／时刻不变。两轨分句不同，不声称新投影证明原英文轨逐词同步。
- `application`和`memory`的局部ASR略跨现有单元边界，后段`understand`／`extend`识别不同，仍公开。ASR不是人耳，也不能确认216/217的定义或指代。

## 实测边界

28项有限测试通过，覆盖真实来源、否定、完整词流、虚假合成、无效锚点和精确作用域恢复；不外推自然度、专业发音或情感胜者。

一次真实当前音频扫描发生在独立`quality-v1`数据库，未改正式库：

- 候选库扫描125→126，issues11600→11786，occurrences20396→20721；所有旧行保持。
- 186个既有声音候选全部保留；12个原声暂停的合成尾句规则不适用分类保持，不是dismiss。
- 决定／解决均0；状态`eligible_with_unreviewed_candidates`不是人工批准，也不是渲染／安装准入。
- 候选24原声区／41预留项；321严格原窗、21EOF零样本保持。统计316 normal含原声占位，5项既有rate_up；不是316条新TTS或全正常速率合成。
- 本轮新增TTS、ASR、PCM改写、视频编码和安装均0，无新交付外挂或网页。

正式两版MP4及31正式目标保持，仍23原声区／39预留项。正式数据库仍125扫描、11600 issues、20396 occurrences。正式音轨、40.2秒有声导读与85章不因本轮预检而替换。

## 只读入口

```bash
cd /home/pengdl/media-downloader
R=/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114
PYTHONPATH=$PWD /vol_1t/pyvideotrans/.venv/bin/python \
  "$R/source-reservation-captions-v1/check-preflight-v1.py"
```

候选预检的真实作用域为：

```python
with source_reservation_projection.runtime(projection_config, projection_output):
    editorial_quality.check(quality_config, quality_output)
```

只分发精确配置与输出，其余路径仍走旧检查器；异常退出恢复原函数。旧恢复暂存和真实失败不改。

## 后续

直接复用成功`quality-v1`，继续独立双版媒体和安装流程，不重新扫描或生成成功原声。渲染前后仍须绑定40.2秒实际导读、验证中文字幕布局／中文AAC及PCM一致、167187正文视频帧表、完整解码、受限取帧与有限VLC，并明确安装历史授权及新的只读入口。本轮没有进行这些媒体步骤或新图审。

断点为同请求`checkpoint-source-reservation-captions-v1/execution-progress.json`；独立验证、登记及最终Git收据分别绑定实物。9569旧身份、98冻结根Python、293生产MP3、其他130行和13文件保持且不混入提交。

目标继续active。所有人耳、语义定义、专业发音、情感、全片视觉、逐词声画、PotPlayer实机及最终批准未完成；Linux原有27 holds／23 markers／F08保持。没有自动后台续跑或其他任务停机声明。
