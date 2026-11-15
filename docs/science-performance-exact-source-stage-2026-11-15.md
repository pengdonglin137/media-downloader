# 完整语音暂存：精确原窗外样本保持

日期：2026-11-15。视频cjiUXZh0ZCo；当前为可核对的严格暂存进展，未正式采用，持续优化目标active。

## 来源与完整句选择

311选择unit-311-helpful-clear，补回very very helpful的数据中心积极评价，仍把预防未来异常表达为希望而非保证。319选择unit-319-hadoop-latin，保留Java、Hadoop、基础理解而非专家、软件与成本限定。选择依据是源文忠实性，不是人耳、专业音素或情感胜者；318研究/教学情感替代及319哈杜普项目别名继续未选。

两条完整云溪正常速率语音分别12.912/10.536秒，原窗17.870/13.039秒。本轮0新TTS、0新原文ASR、0新中文诊断，无额外atempo、截尾、借窗或跨角色合成。复用上轮完整合成及WordBoundary。

## 实测缺陷与修复

311精确源起点是129057600采样，冻结legacy浮点起点是129057599。旧实现虽然完整合成放在交集，却先清空整个legacy跨度，把精确原窗外样本在基础/混合两PCM中由22改为0；原生v1检查全0仍被独立v1真实1拒绝。outside-source-sample-v1-failure.json保留实测及85个旧实物身份。

新media_workflow.exact_source_window_stage使用独立kind=exact-source-preserving-complete-editorial-stage-v1及policy=preserve-exact-source-exterior-within-legacy-ownership-v1，只清空精确源窗/legacy交集，再放入完整仅增益合成；额外样本保持22。通过私有FunctionType命名空间复用冻结父门禁并绑定双方身份，不改冻结父代码或安装全局state钩子。

runtime只分派完全匹配的config/output、拒绝嵌套、finally恢复。26有限回归用例为6接受/20拒绝，涵盖左右额外采样、分数边界、完整插入/静音尾部、旧缺陷复现、映射伪造、非法PCM、超窗、重叠与状态恢复。它们不是所有输入的形式证明。

## 当前正确版本与证明

- stage-config-v2.json / stage-v2：prepare/check均真实0。
- projection-config-v3.json / projection-v3与quality-config-v3.json / quality-v3：各prepare/check真实0；run-projection-quality-v3仅续未完成阶段，未重做成功stage。
- independent-stage-verification-v4.json及.exit=0独立核对完整增益合成、精确原窗外两PCM保持、23实际原声区及39预留项、全部旧库行与正式文件不变。
- 321来源单元和1022英文显示块保持；311五短句、319四短句，共九中文/双语投影。正式双版/正文/数据库没有替换；40.2秒有声导读、85章、5572.982秒保持。

321最后原声区为132719519至132786768采样，实际源末尾132786747，另21个已声明容器零样本。核对真实源前缀和零填充分开，不能把21个零称源音频或借用其他视频14样本说法。

正式库扫描110、问题8807、出现15545保持；新隔离quality-v3扫描111、问题8993、出现15866。旧quality-v1也是从正式110复制出的另一独立111，不是后继112。项目60、术语41、0决定/0解决不变，186当前候选全留，12原声规则分类不是dismiss。

## 失败历史与保留范围

初始验证器语法1、独立v1窗外样本1、run-stage-v2及错绑定projection-prepare-v2各1、独立v2要求旧外层0而1、独立v3未单列21零而1均保留。登记v1因未创建新断点目录而1，未写任何正式媒体或文档；v2只创建缺失目录再登记，不重跑成功制作。独立v4修正检查后0，不追认旧失败或重制成功语音。旧RUNTIME.md保留，新执行入口是本请求RUNTIME-v2.md。

6446不可变实物、31正式目标、98冻结根Python、293生产MP3和13其他未跟踪文件保持。只提交本轮9路径，其他五文档130行保留，不自动全量git add；媒体、缓存和外部日志不纳入Git。

当前断点：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/checkpoint-helpfulness-cost-stage-v1/execution-progress-v2.json`。登记首次把自身尚未结束的日志绑定成旧身份，独立绑定审计真实1保留；v2登记只重绑已关闭日志并核验，旧登记/审计保留，不重复成功语音或媒体。

当前运行时：`/home/pengdl/Videos/translated_backlog/.workflow_logs/request-cjiUXZh0ZCo-20261114/helpfulness-cost-stage-v1/RUNTIME-v2.md`。裸旧CLI、旧sample-grid/安装运行时不能冒称支持新kind；正式媒体采用需另行明确支持新合同，完成真实媒体验证后再进入事务安装。本轮无编码、成片解码或新图审。

216/217/227/248/310/317来源疑点、309深/度学习词内换行、290品牌/319专业音素、318情感、Linux27holds/23markers/F08、全片人耳/视觉、逐词对齐、PotPlayer及最终批准均保持。仅本轮进展，不覆盖并行队列/总数、不发布、不声明后台续跑。
