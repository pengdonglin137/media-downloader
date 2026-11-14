# 性能报告结尾七窗正式采用与双版本安装

仅处理`cjiUXZh0ZCo`《Science of Performance 2019》。308、315–320七处来源忠实度修订已进入正式中文母版和中文烧字上传版；不是只有试读或隔离候选。318采用“热爱”对应原文的`loved/love`，属于文字忠实度选择，不是人耳情感选胜；完整“喜欢”替代项仍保留。319/320的取舍配对归回实际原窗，不称全局漏译，也不把幻灯片上未口述的内容新增为讲者发言。

## 实际成片与边界

- 正式目录：`/home/pengdl/Videos/translated_backlog/`。
- 母版：`Science of Performance 2019_中文配音.mp4`，566087086字节，SHA-256 `170d8fc38a62246890ef2517172f940843c306c831a75c51ccc6da0114513455`。
- 上传版：`Science of Performance 2019_中文上传版.mp4`，452336780字节，SHA-256 `e8d88dac5237ccee55a6604d43948531f9473c89ded16aee487831dd10b334d9`。
- 总5572.982秒，沿用40.2秒中文有声AI导读和85内嵌章节；母版双音轨/三软字幕，上传版单中文主轨烧字，每用途一个自包含MP4。
- 321严格原窗、23实际原声区、39原声占位保持；321片尾掌声不纳入合成。七个完整正常速率候选直接复用，无新TTS/ASR、借窗、裁尾或额外atempo。实际最小余量10727个24kHz样本，不能改报精确窗余量10728。
- 22中文显示短句来自实际WordBoundary；完整中文/朗读串和来源英文词流保留，独立英文显示文件字节不变。姓名保留英文拼写，未声称新音译或发音获得批准。

## 真实媒体验证

既有`editorial_media`的prepare/encode/verify/check均实际0；三个编码、两个中文PCM解码和两个完整A/V解码共七项操作均有终态0。续做只接收原作业已完成的收据，没有因工具等待重启成功编码或验证。

母版167187视频包、240010原声音轨包及1032英文字幕包的载荷/精确有理时序保持；上传版167187解码帧的完整有理时序与母版相同。两版中文AAC包与完整解码PCM一致；七个选定合成区和23原声区共30个固定偏移诊断通过，最小余弦相似度约0.99891553。这些不是角色、可懂度、停顿、专业发音或情感批准。正式安装的媒体字节、mtime与已完整验证的候选精确一致，因此转移其完整解码/帧/包证明，不冒称安装后再次完整解码。

实际上传版22张受限JPEG均逐张打开：18张为压缩上下文前已有实看，后四张本轮实际打开。长边1152或1280、最大32567字节；只代表局部字幕图审，不代表全片视觉或逐词声画。保留首次提取在315第二短句超限的真实1和三张成功图；第二次只补19张，复用原三张，实际0。提取清单的早期三张观察快照不改写，后续实际观察另存`caption-visual-observations-v1.json`。

候选与正式路径分别使用同一个既有libVLC帮助器，均实际0：各85章、每版六次有限章节跳转、2对1音轨、中文默认、母版三软字幕切换及播放位置断言通过。首次导入缺帮助器同目录模块，真实1保留；只补导入搜索路径后执行，未复制或修改帮助器。候选日志59条、正式路径57条匹配`error|failed|decode|buffer|timestamp|late`，包含H.264缓冲/解码及时间戳错误，原因未确立；另保留`no frame!`等完整日志。不得解释为已证实无害、关闭此前55/56/68条诊断或冒充全片播放器验收。声音输出为dummy，不是试听；PNG截图不自动算作已看。

## 归档与当前质量历史

复用本轮已建立的真实精确计划归档和`editorial_history`权威，通过既有安装prepare/apply/check，三者真实0；31项事务`committed`，31项旧文件归档逐项核验。没有修改98冻结根Python或任何已绑定共享适配器，没有重试旧失败/回滚事务。

1037个旧身份中1014原位保持、23项有精确旧版归档；更早826身份的803原位/23归档覆盖继续成立。293生产MP3与旧私库字节保持，规范原文及翻译任务不变；安装仅切换到新增历史的私库，不覆盖旧库。

| 范围 | 扫描 | 问题 | 出现 | 词 | 工程 | 声音决定 | 修复解决 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 旧正式私库 | 89 | 4859 | 8774 | 41 | 60 | 0 | 0 |
| 本轮隔离预检 | 90 | 5050 | 9097 | 41 | 60 | 0 | 0 |
| 编码候选私库 | 91 | 5241 | 9420 | 41 | 60 | 0 | 0 |
| 新正式路径私库 | 92 | 5432 | 9743 | 41 | 60 | 0 | 0 |

每层实际扫描保留旧行，更新本工程指针，不篡改其他工程或增加人工决定。当前仍191个未人工复核声音候选；12原声占位的合成尾句规则分类不是dismiss，也不是原声角色/可懂度批准。

## 入口与续做

- 请求实物：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/closing-adoption-v1/`。
- 当前断点：同请求上级`checkpoint-closing-installed-v1/execution-progress.json`。
- 独立候选/安装核验：`candidate-independent-verification.json`、`installation-independent-verification.json`。
- 正式历史合同：`history-authority-v1.json`、`install-config-v1.json`、`install-v1/historical-application.json`。
- 仓库结果：`data/translation_reviews/cjiUXZh0ZCo-closing-install-result-20261115.json`；来源选择仍以已绑定的`cjiUXZh0ZCo-closing-adoption-20261115.json`为准，不重写旧阶段结果。

以后只读检查必须沿用本次显式运行时，不能裸套旧CLI或历史安装：

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
REQUEST=/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114
NEW=$REQUEST/closing-adoption-v1
$PY -m media_workflow.editorial_history install "$NEW/history-authority-v1.json" \
  --operation check --config "$NEW/install-config-v1.json" --output "$NEW/install-v1" \
  --stage-config "$NEW/stage-config.json" --stage "$NEW/stage-v1"
```

专业词/姓名发音、情感自然度、全片人耳/视觉、逐词声画、VLC诊断原因、PotPlayer及最终批准仍待完成，持续品质目标保持active。不扩播放列表、不发布、不修改其他请求/共享队列/并行总数；媒体和缓存不入Git，只选择性提交本轮登记/文档，保留其他未提交改动，不承诺后台自动续跑。
