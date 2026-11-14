# 性能报告257/258：精确采样暂存与候选双版本

本轮只续做`cjiUXZh0ZCo`的257/258完整句来源归位，不展开播放列表、不修改`0sZlaQr3e2w`、共享队列或全局总量。旧资源说明原已在257译出，不计作全局漏译；新257恢复线程/核心限定，资源说明归回258。`there/red`仍无声学定案，257采用中性“这里”，英文来源不改。259从4477.899秒开始的实际原声保持独立。

## 新采样契约

旧暂存器真实出口1保留；它用旧绝对浮点分配，258跨度比精确原窗多1样本。没有修改旧绑定实现、扩大原窗或伪改试读长度。

独立共享适配器`media_workflow/editorial_sample_grid.py`使用`exact-window-legacy-owned-editorial-stage-v1`及`legacy-owned-span-exact-contained-synthesis-v1`：保留旧样本所有权，只清空选中旧分配区；完整语音放入精确原窗与旧所有权的交集。全部新来源、映射和实现身份显式登记，不冒充旧暂存成功。操作说明见`editorial-sample-grid.md`。

- 257精确原窗为`[106218456,106599816)`，旧所有权末尾为106599815；完整语音317376样本/13.224秒，交集余量63983样本。试读的精确原窗余量63984不能偷换成暂存交集余量。
- 258旧所有权从106599815开始，精确原窗及新声音从106599816开始；完整449856样本/18.744秒，交集余量419904。旧所有权多出的前导1样本清零，不提前朗读。
- 两条既有完整句一起采用，正常速率，不裁尾、不去静音、不借窗、无新TTS或ASR。293个既有生产MP3保持。
- 321严格原窗、23实际原声区、39原声/事件占位及未编辑PCM保持。40.2秒导读偏移与259原声边界不变。

## 已完成的预检

42项有限合同检查通过，其中26项为预期拒绝；使用合成PCM/模拟分派，不是42次媒体生产。另有namespace CLI检查。真实stage prepare/check、caption prepare/check、quality prepare/check和media prepare均0，共9个已完成命令收据。

字幕投影将257的3段改为4段、258的5段改为6段，共10完整显示短句；中文显示/朗读拼接及英文token流保持，英文字幕字节不变。仅该精确stage配置/目录使用新检查器；其他stage仍走原检查器，退出恢复，不提供跳过校验的通用补丁。

当前音频质量预检仍191候选：189未编辑候选完整保留，2个旧编辑项候选留在历史扫描；12个原声占位仅分类为不适用合成尾句门禁，不当作人耳dismiss。私库从86到87扫描、4286到4477 issues、7805到8128 occurrences；原行/表结构保持，decisions和repair_resolutions仍0。候选数未下降不表示修订失败，也不能据此批准声音。

`quality-prepare.json`是包含扫描进度的混合stdout，不是完整纯JSON；一次只读解析失败留在工具历史。以`quality-v1/manifest.json`、`quality-evaluation.json`、`database-history.json`和`candidate-history.json`为权威。

## 编码快照与后续

预检断点记录时，候选双版本编码真实运行，工具会话82054，Python PID3784545和FFmpeg PID3787946已按进程起始标识核对。该快照不是编码完成收据，也不是持续后台运行保证。先轮询同一会话或真实`media-encode.exit`，不因观察超时重启。

尚未完成的事项：候选完整packet/frame/AAC与全解码核验、10个变更字幕实际上传版局部图审、有限VLC、保留历史的正式安装事务。只有实际完成后另存新收据，不能把准备成功或旧媒体成功外推成新成片成功。

预检入口：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/checkpoint-sample-grid-preflight-v1/execution-progress.json`。同目录登记117个完成依赖、独立核对521个旧身份，并分别保留真实命令收据；不绑定仍在写入的编码媒体。新工作目录为同请求的`vtune-sample-grid-v1`。

截至该快照正式双版未改，98根Python与旧成功媒体保持。没有声音决定、人耳选胜、发音/情感批准、全片视觉、逐词声画或PotPlayer实机批准。后续正式采用也不得自动关闭这些门禁；总目标保持active。
