# 完整意群编辑的共享媒体生产

`media_workflow.editorial_media`接续`editorial-quality.md`，把已经验证的完整意群配音和显示投影生成隔离的正文、中文母版及烧字上传版。它不重新合成正文或导读、不修改98个冻结根Python，也不复制单片生产／恢复／诊断程序。原工程、历史数据库和正式成片不安装、不覆盖。

## 配置与执行

配置`kind`为`complete-utterance-editorial-media-v1`，只接受以下字段：

- `preflight_config`、`preflight_manifest`：成功当前音频预检的完整文件身份；预检目录继续保持不可变，不能向其添加render文件冒充先前证据。
- `terms`：已登记显示术语文件身份；排版保留显示文字和时间，不承诺识别所有未登记术语。
- `threads`：1至16的整数编码线程数，不接受布尔值。
- `scope`：固定`isolated-media-pair-no-installation`，不能通过配置声称正式安装。

文件身份含绝对路径、字节数、纳秒mtime和SHA-256；`kind`、未知字段、缺失字段、旧身份及不支持的作用域均拒绝。

```bash
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.editorial_media prepare MEDIA.json --output NEW_MEDIA
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.editorial_media encode MEDIA.json --output EXISTING_MEDIA
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.editorial_media verify MEDIA.json --output EXISTING_MEDIA
/vol_1t/pyvideotrans/.venv/bin/python -m media_workflow.editorial_media check MEDIA.json --output EXISTING_MEDIA
```

命令使用同级独占锁，所有输出在新目录，工程basename保持来源ID。`prepare`不能覆盖已存在目录；每个编码／解码子命令保存实际参数、输入身份、日志、终态和输出身份。已成功且身份匹配的子步骤明确复用；失败、中断、参数变化或无收据输出不会自动重试。成功验证后用`check`只读复核，不重复完整编码、解码或ASR。

## 来源和历史保留

工程复制完整上游文字、声音、显示、审校与章节计划输入，下载目录指向同一实际原片。新的`editorial-media-source.json`绑定共享生产者、冻结依赖、上游合同、归档母版、正文、导读PCM、字体及实际字幕／ASS。当前旁白增加显式来源依赖，裸冻结旁白验证器必须拒绝；不把旧全片生成记录追认为新编辑的生产者。

SQLite使用上游**预检私库**的一致性备份，而非回退到原工程较早的扫描。新工程再次实际扫描完整当前WAV；全部旧表／行／schema和无关项目指针保持，只允许新扫描、词项、候选追加及当前项目指针变化。与预检相同的当前候选必须逐项一致；不产生人耳决定、dismiss或修复解决。

已登记原声短尾沿用共享质量分类：逐项证明0dB固定原窗、基础TTS为零、复合PCM等于实际源PCM及显式EOF静音，只判“不适用合成尾句规则”。实际数据库与报告保留全部候选；角色、停顿、可懂度不由此批准。

`runtime(work)`仅为精确工程接入来源、规则适用性和当前包验证，其他工程仍走冻结验证器。拒绝嵌套／混入未知补丁，正常退出和异常都恢复。包记录真正共享生产者的SHA，不伪称由冻结`video_package.py`生成；核验本生产者、当前来源、所有媒体／字幕和历史几何之后，才允许冻结包验证器检查当前依赖。

## 编码与检查范围

- 正文复制归档正文的视频、原声AAC和英文字幕，替换中文主音轨、中文／双语显示字幕；母版复制归档母版视频、原声AAC、英文字幕与内嵌章，只重新编码完整导读加当前中文正文音频。
- 真实导读偏移、场景音频、章节和两版总时长保持。先检查正文时长之后的全部复合WAV样本为零，禁止以容器时长剪掉非零源音／TTS；不使用atempo、借窗、裁尾或跨人合成。
- 上传版从新母版烧录已登记术语保护的实际ASS，只保留中文主音轨并复制其AAC。使用兼容本机FFmpeg4.4的`-vsync 0`、`-enc_time_base -1`及原视频timescale，不假设较新`-fps_mode`存在。
- 实际验证正文／母版复制流的逐包载荷及精确PTS／DTS／时长／flags，另核对timebase和编码配置；母版／上传版中文AAC包及完整解码PCM相同。
- 上传版与母版逐个解码帧的精确有理时间、时长、帧数和尺寸一致；烧字改变像素，不声称两版像素相等。两版完整音视频解码、软字幕实际内容／时间、章节、轨道、总时长及原生技术release分别检查。
- 每个实际编辑与原声区按真实导读偏移测完整固定窗AAC信号相似度／电平；这是有损编码和固定偏移诊断，不是PCM与源逐样本相等、词级声画、发音或人耳批准。

编码后重新运行当前质量门禁，其`render_state.json`绑定必须非空且对应新成片；预渲染质量不能充当最终release。原生release仅对应隔离候选目录，不能被报告为正式路径已更新。保留历史的多文件安装仍是后续独立事务。

全片实际听辨、专业发音、情感表现、全片视觉、逐词声画、PotPlayer和最终语言声音批准均需额外证据。生成审听页、自动扫描、相关性或有限图片观察不自动关闭这些门禁。
