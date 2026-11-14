# 精确原窗与旧样本所有权的完整句暂存

`media_workflow.editorial_sample_grid`是独立版本适配器，不修改已绑定的`editorial_stage.py`、字幕投影、质量扫描或生产器。它复用已成功的完整句试读、冻结增益处理、编译与原声检查，不重新TTS或ASR。

## 两种边界必须分别证明

旧基础/复合PCM使用`int(float(time)*24000)`分配单元；十进制来源窗和完整试读长度则精确计算。二者在整采样边界附近可能相差一个样本，但不能因此扩大窗口或把旧所有权当作声学批准。

新契约`exact-window-legacy-owned-editorial-stage-v1`固定采用`legacy-owned-span-exact-contained-synthesis-v1`：

- 明确登记旧单元拥有的`legacy_start_sample/legacy_end_sample`，只清空这些已选择的旧分配区，不侵入其他单元或实际原声区。
- 从十进制原窗精确推导可完整包含的样本：起点向上取整，终点向下取整；与旧所有权求交集，完整语音必须同时适配交集、精确原窗和试读长度合同。
- 新语音起止、剩余补零、原窗、旧分配、实际样本数都写入`sample_mapping`。不裁首尾、不去静音、不改变时长、变速或借给相邻发言。
- 只做冻结`dub.level`增益转换。逐样本重放替换，并检查旧分配区外PCM相同、全部旧原窗、事件/原声占位、原声回填及显式EOF静音。
- 把旧来源/文本/双版媒体/检查与基础、复合、源PCM精确归档；后续验证以归档保持历史，不把正式路径以后的有意替换误报为历史丢失。

新清单和旁白记录真实新适配器及依赖SHA，不能冒充旧暂存器生产成功。裸冻结旁白验证必须拒绝新来源。源译完整性选择仍显式`human_listened:false`及`pronunciation_or_emotion_approved:false`，不写声音决定。

## CLI

使用现有媒体环境，输出在仓库及正式工程之外的新目录。配置沿用`editorial-stage.md`的基线、试读、成功检查权威与完整句选择字段，修改`kind`并新增固定`mapping_policy`；不接受未知字段。成功/失败目录均不覆盖或自动重启。

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
$PY -m media_workflow.editorial_sample_grid prepare STAGE.json --output NEW_STAGE
$PY -m media_workflow.editorial_sample_grid check STAGE.json --output EXISTING_STAGE
$PY -m media_workflow.editorial_sample_grid project CAPTIONS.json --output NEW_CAPTIONS \
  --operation prepare --stage-config STAGE.json --stage EXISTING_STAGE
$PY -m media_workflow.editorial_sample_grid project CAPTIONS.json --output EXISTING_CAPTIONS \
  --operation check --stage-config STAGE.json --stage EXISTING_STAGE
$PY -m media_workflow.editorial_sample_grid quality QUALITY.json --output NEW_QUALITY \
  --operation prepare --stage-config STAGE.json --stage EXISTING_STAGE
$PY -m media_workflow.editorial_sample_grid quality QUALITY.json --output EXISTING_QUALITY \
  --operation check --stage-config STAGE.json --stage EXISTING_STAGE
```

字幕/质量配置仍使用既有公开格式。桥接前核对精确stage配置与清单，完整验证新stage；仅在进程内显式作用域把**该精确配置/目录**的旧`editorial_stage.check`调用分派给新完整检查器，其他工程仍走未改旧检查器。拒绝嵌套/混入未知补丁，异常或正常退出均恢复；不是忽略旧检查结果的通用绕过。

既有字幕投影按来源起点加真实WordBoundary偏移，因此桥接还要求新音频起点严格等于来源起点采样坐标。可生成但不满足此要求的分数采样来源不能使用未修改的字幕投影；明确拒绝，不猜偏移。WordBoundary也不证明音素、声学词尾、角色或逐词画面对齐。

## 后续媒体验证的显式作用域

公开`runtime(stage_config, stage_directory)`可在既有`editorial_media`/`editorial_install`入口外层提供同一严格分派，仍由原CLI持锁、编码、扫描和验证。以下通用调用不复制单片生产程序；参数为实际已绑定的配置和新输出目录：

```bash
$PY -c 'import sys; from media_workflow import editorial_sample_grid as grid, editorial_media as media; config,stage=sys.argv[1:3]; sys.argv=[sys.argv[0],*sys.argv[3:]]
with grid.runtime(config,stage): media.main()' STAGE.json EXISTING_STAGE prepare MEDIA.json --output NEW_MEDIA
```

编码、逐包/逐帧/完整音视频检查及安装分别使用既有对应操作，不从预渲染质量或旧成片成功推断本次成片成功。安装仍须保留历史的显式事务，不能覆盖未知较新修改。

## 有限检查范围

2026-11-15，42项有限合同检查含26项拒绝，使用合成PCM和模拟分派；覆盖整数/分数精确窗、已知浮点边界、完整语音超交集拒绝、窗外保持、旧输入不变、原声区/重复单元保护、未知配置/伪人耳批准拒绝、精确路由、其他工程回退、异常恢复和不兼容字幕锚点拒绝。隔离namespace CLI也不依赖尚未提交的通用编排入口。这些不是42次媒体制作或人耳测试。

真实两项归位修订的生产与未决门禁另见`science-performance-sample-grid-2026-11-15.md`；候选双版已完整验证，正式安装因旧包仍将活动计划作为历史锚点而真实拒绝并回滚。需要另立不可变历史计划权威合同，不跳过原guard或重试失败事务；不修改本轮已绑定实现身份追认旧结果。
