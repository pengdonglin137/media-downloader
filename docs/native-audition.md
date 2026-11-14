# 原生原声待核语境

`media_workflow/native_audition.py` 从**已有、已验证的原生音频观察**导出待核语境，不运行新的 `native_audio.observe`，不授予来源、角色、声学词尾或声音批准。它不修改规范工程、字幕、中文主音轨或生产门禁；HTML 和 WAV 仅是内部复核材料，不是新增交付附件。

## 命令与输入

从仓库根目录使用已有媒体环境：

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
$PY -m media_workflow.native_audition extract PLAN.json --output NEW_DIRECTORY
$PY -m media_workflow.native_audition check PLAN.json --output EXISTING_DIRECTORY
```

计划最大512 KiB，仅接受 `kind`、`observation_plan`、`observation_report`、`windows`、`evidence`。`kind` 必须为 `native-source-audition-windows-v1`；两个观察引用及最多64个证据项均为既有文件身份。观察报告须名为 `report.json`，原观察的输入、工具、代码和逐帧账本仍须有效，原生时间线须从0开始且无间隙、重叠或不连续。

`windows` 为1至128项，每项仅含 `id`、`start_sample`、`end_sample`、`label`、`entries`。窗口为原采样率下的整数半开区间，不接受布尔值或越过原生音频尾界；ID必须唯一且安全。每个窗口的1至128个文字项仅含字符串 `id`、`en`、`zh`、`note`。所有文字做HTML转义；中文是待核候选，不因进入页面而变成已批准译文。

## 导出与核验契约

- 每次新导出只启动一次顺序FFmpeg解码，从源起点开始到最后所需原生帧；无输入跳转、重采样、增益或声道变换。若窗口包含尾部，就实际顺序解码至源尾，不能称没有全源解码。
- 每个解码帧先对照已有SHA-256账本，才向对应WAV写入。请求窗口向外覆盖完整原生解码帧；完整帧不是语言完整句、说话人边界或获准生产交接窗。
- WAV使用44字节RIFF IEEE float32头及未经改写的完整帧载荷；单项受WAV32约束，所有WAV合计不超过1 GiB。保留原采样率、声道和float32字节，不将文件头差异混作音频载荷变化。
- `check` 重新验证输入身份、确切命令、真实进程终态0、完整文件集合、各WAV头及逐帧载荷、页面和固定未批准标志；不启动解码器。对已成功目录再次 `extract` 也只读检查，不重做成功解码。
- 输出必须位于仓库和规范 `.zh_work_*` 工程之外。不完整目录拒绝续写；保留原失败材料并显式选择新目录。锁、异常及SIGTERM清理只管理本工具所启动的解码器，错误证据不改成成功。

浏览器支持必须按真实环境另验。页面用 `preload="none"`，初始0:00/0:00不是时长检测结果。静音播放时钟、seek、媒体元数据、摘要和人工看图都不等于人耳听辨、发音或情感验收。本工具没有听辨反馈导入、自动角色选择、静音分类、TTS或正式安装能力。

本次实物、16项有限夹具、浏览器成功与失败边界见 `docs/linux-performance-native-auditions-2026-11-15.md`；原观察契约见 `docs/native-audio.md`。
