# 原生音频尾界观察

`media_workflow.native_audio` 是只读媒体观察入口。它对明确音轨执行一次完整解码，保留原生采样率、声道数及时间戳，输出 float32 PCM 的逐帧 SHA-256 账本，不写完整大 WAV，不修改正文、旁白、字幕、角色或交付媒体。

```bash
python3 -m media_workflow.native_audio observe /absolute/plan.json --output /outside/repository/new-observation
python3 -m media_workflow.native_audio check /absolute/plan.json --output /outside/repository/new-observation
```

计划严格为：

```json
{
  "version": 1,
  "source": {
    "path": "/absolute/source.mp4",
    "sha256": "替换为来源文件的64位小写SHA-256",
    "audio_stream": 0
  }
}
```

`audio_stream` 是从零起的音频序号，即 FFmpeg 的 `0:a:N`，不是容器全局 stream index。来源须为绝对本地文件，哈希必须实际匹配；重复键、未知字段、非有限 JSON 数字、布尔整数及越界音轨序号均拒绝。调用机器需要已有的 FFmpeg、FFprobe 和标准库 Python；不安装依赖。

## 完整解码而非重采样估计

FFprobe 保存明确选中音轨的原生采样率、声道数、时间基、起点和声明时长。FFmpeg 使用 `-copyts -xerror`、明确音轨与 `pcm_f32le` / SHA-256 framehash；没有 `-ar`、`-ac`、滤镜、裁剪、速度、增益或时间戳归零。实际命令和进程终态分别保留。

账本的每行必须是单一音轨、DTS 等于 PTS、正整数时长，且字节数等于「时长 × 原生声道数 × 4」。时间基必须为原生采样率的倒数。报告分别给出：

- `decoded_sample_frames`：每声道的解码样本帧总数，不是把所有声道相加的标量样本数。
- 首帧 PTS、末帧止点、最早／最晚止点、精确有理秒数及全量间隙／重叠计数；最多列出32个不连续例子，并明确标记例子是否截断。
- 容器声明起点／时长／尾界和实际解码最晚止点的精确差值；声明缺失保持 `null`，不从格式时长或 resampled WAV 猜补。
- `decoded_payload_bytes` 是账本验证过的解码 PCM 字节总量，不代表磁盘上存在等大的 WAV，也不是连续 PCM 文件哈希；逐帧摘要只代表对应解码帧。

AAC 解码可能超过声明时长，不能强制截到容器尾界，也不自动将差额叫作讲话、padding 或静音。若存在时间戳间隙，样本帧总数不能直接当作原片绝对止点。该观察器不解决编码包保持或不同解码器／平台的逐样本等同性。

## 收据与重入

输出必须在仓库外；工程 `.zh_work_*` 内只允许 `.managed_workflow` 子树。输出不能包含计划或来源。成功目录包含：

```text
observation.lock
inputs.json
probe.json
probe-process.json
probe.stderr.log
decoded.framehash
decode-process.json
decode.stderr.log
report.json
```

输入绑定计划、源媒体、当前模块、冻结根帮助模块和 FFmpeg／FFprobe 可执行文件身份，不声称固定所有动态库或操作系统。成功前再次检查输入；成功报告绑定全部观察文件的路径、大小、mtime及 SHA-256。

同计划重复 `observe`，或调用 `check`，只验证输入／产物／真实子出口并重新解析既有账本，不启动 FFmpeg／FFprobe，也不重写成功文件。这是只读一致性检查，不是第二次独立声学解码。改变任何输入、实现、工具或观察文件均拒绝复用。未知非空目录、未完成／失败目录不覆盖，不将历史未知出口补为零；使用新的目录保留重试历史。

进程继承独占锁，防止父进程消失后立即重复解码。每个子进程的开始、命令、PID、实际出口和结束分别记录。SIGINT／SIGTERM终止本次拥有的进程组；顶层返回130／143，子出口按实际观察保留。不可捕获退出可能留下 `running` / `returncode=null`，不据此声称成功。每个子命令1200秒上限；账本解析上限256 MiB，超限明确失败，不缩短来源冒充完整。

退出0只表示完整观察或既有观察一致性验证成功；退出1表示输入、尾界格式、身份、锁或进程失败。音轨实际语音止点、角色、可懂度、发音、情感、逐词声画、全片人耳／视觉、PotPlayer与生产批准始终不由本工具授予。
