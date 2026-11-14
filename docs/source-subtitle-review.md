# 完整原片文字审校版

`media_workflow.source_subtitle_review`为已有完整、隔离的中文文字候选提供**全片原画原声审校载体**。它不生成中文语音，不导入正式译文，不批准角色、声学边界、事实门禁或最终成片，也不是把长片任务缩成局部样片。

## 来源与字幕契约

- 计划使用`kind=complete-source-subtitle-review-v1`，绑定`source`、`reading`、`english_subtitles`、`technical_review`及非空`evidence`。文件身份包含绝对路径、大小、纳秒修改时间与SHA-256。
- 来源限明确的一路H.264视频、一路AAC音频的MP4；不猜选流，不访问网络，不修改原片或正式工程。输出必须是全新仓库外目录。
- 阅读稿须为`cumulative-textual-review-proposal-not-production-v1`，覆盖所有原字幕块，`production_admission=false`、无剩余字幕块。逐条保持完整原词顺序、token归属、原字幕外包；所有角色仍为unknown，声学词尾为空，TTS不准入。
- 重叠候选通过时间边界扫描在同一显示段中并列，保留每个ID和完整中文，不据此认定同人、合并原声或分配朗读时长。供应商标记单列，不能解释为换人或静音；语言hold保留原审校原因。
- 输出一个自包含的内部`source-text-review.mp4`：完整原声单音轨，加中文文字候选、原始英文自动字幕、AI事实及待核说明三条软字幕。默认中文字幕持续标明原声未译配、时间仅旧字幕外包、技术事实未放行。AI说明轨明确非讲者原话，须在播放器中另行选择。
- 可选`correction_overrides`按既有事实ID登记完整作者修订，逐项提供`text`、`reason`、`evidence`身份；未知ID、未绑定修订或清除事实hold均拒绝。它只更新审校说明，不能代替原讲、选择声音或批准现场插入。
- `severity=critical`的完整AI纠正同时进入默认中文审校轨，不要求观众切换说明轨才能看到；原讲对应中文仍保留并明确标为未放行，不偷换成讲者原话。
- 中文及AI说明使用`review_text_layout`显式换行，每行最多100个半角宽度单元；ASCII标识符及可选`protected_terms`不跨行，避免行首闭合标点和行尾开启标点。逐段去掉新增换行后必须逐字等同作者稿。此有限词表不保证识别所有中文词，也不冒称任意播放器排版验收。

## 执行

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
"$PY" -m media_workflow.source_subtitle_review build --plan "$PLAN" --output "$OUT"
"$PY" -m media_workflow.source_subtitle_review check --plan "$PLAN" --output "$OUT"
```

`build`先形成三份内部字幕，再实际执行来源完整编码包观察、copyts复制封装、审校版完整编码包观察及三轨字幕回读，共六个持久子步骤。没有`shortest`、截尾、帧数上限、视频/音频重编码或原声增益。编码流头、extradata、每个包的载荷哈希与精确PTS/DTS/时长必须一致；字幕不能延长来源尾界，三轨内容和毫秒显示窗必须回读相等。

MP4流的说明性handler标签会重新标为审校用途，不能声称整份元数据或容器字节等同原片。编码包相等也不是新做了全片解码或人耳验收。

`check`不运行媒体命令：重核全部输入及工具/实现身份、输出清单、六个实际子出口、已保留的完整编码包观察、三轨回读和覆盖。已有目录不覆盖；失败manifest保留不完整状态，不能改0、移除门禁或自动重做成功媒体。

## 验收边界

候选显示时间仍是**原字幕块外包，不是词级或换人时间**。多人文字可能同时显示，语义未完句也可能与后续候选并列；此格式专用于带完整原声逐项复核。它不能作为最终中文字幕定时或中文主音轨安装的证据。

事实说明可在审校轨中阅读，但critical事实hold仍须在正式中文主轨和明确AI交接中解决。无字幕开头、片尾和块间声音照样完整复制，不能据此宣称已完成最终两版中文主轨的原声回填。

Linux性能全片实测、真实失败及剩余范围见`docs/linux-performance-source-text-review-2026-11-14.md`。
