# 不可变历史导读计划的显式运行时

`media_workflow.editorial_history`只处理一个明确的旧活动计划历史锚点；不修改已绑定的`editorial_quality`、`editorial_media`、`editorial_install`或98个冻结根Python文件，不重新TTS、ASR或编码。

## 问题与契约

旧包的`plan`身份可能仍指向正式工程的活动`package-plan.json`。编辑安装必须将计划的`source_sha256`绑定新来源，合法替换活动路径后，原检查器不能再从该路径读取历史计划。旧失败应保留并完成事务回滚，不能把当前新计划当旧计划、改旧清单SHA或跳过`Historical intro plan changed`。

新合同`immutable-historical-intro-plan-runtime-v1`只接受`one-derived-project-exact-archived-plan-only`：

- 作者JSON绑定该适配器、全部旧实现、一个明确派生工程的`editorial-derivation.json`、历史文件事务的`prepared.json`及终态`transaction.json`，身份必须含完整bytes/mtime/SHA。
- 历史事务必须已`committed`或`rolled_back`，且属于同一正式工程。其`package-plan.json`原身份须逐字段等于旧包的历史计划身份，归档路径必须是该事务的`archive/package-plan.json`，归档内容与mtime必须精确保持。
- 旧包、stage合同和派生工程精确绑定；历史归档必须在该工程的隐藏工作流区域，不能从派生工程自己拿新计划冒充旧计划。活动或外来事务、符号链接、未知字段、实现漂移或归档改动明确拒绝。

## 不改变全局文件读取

运行时只分派`editorial_quality.derivation`中**选定派生工程**的调用，其他工程使用原检查器。选定工程执行原检查器的同一个Python代码对象，私有函数全局字典中的`state`代理只把精确历史计划的身份/读取路由到已绑定旧归档。必须实际执行一次原历史身份检查和一次历史读取；缺任一项则拒绝。原有来源/字幕/术语/章节/依赖检查全部照常执行。

`dubbing_state.load`、`matches`、原检查器模块的全局字典和原文件都不改；代理的其他操作委托原状态实现。禁止嵌套或混入未知钩子，正常或异常退出均恢复分派。新合同不是通用容差、`matches=True`或忽略异常的补丁，也不声明裸旧运行时支持新历史来源。

## 配置与命令

权威JSON字段仅有`kind`、`scope`、`adapter`、`implementations`、`derivation`、`archive_preparation`、`archive_terminal`。实现字段由`editorial_history.implementations()`取实际身份；其他身份来自真实文件，不填写示例哈希。

```bash
PY=/vol_1t/pyvideotrans/.venv/bin/python
$PY -m media_workflow.editorial_history validate AUTHORITY.json
$PY -m media_workflow.editorial_history install AUTHORITY.json \
  --operation prepare --config INSTALL.json --output NEW_INSTALL \
  --stage-config STAGE.json --stage VERIFIED_STAGE
```

随后同一路径分别执行`--operation apply`与`--operation check`。安装仍使用原共享事务和工程锁；新目录保存`historical-runtime.json`，实际成功后追加`historical-application.json`，绑定本运行时、历史权威、原生验证和最终事务身份，不伪称冻结代码原生支持。读检查不写安装媒体；失败不自动重试，也不重启旧回滚事务。

以后检查本次安装，须使用相同历史权威和stage的显式新入口；单独使用旧安装运行时会因活动计划不再是历史身份而拒绝，不能当成新证据丢失。旧归档/权威变化时新入口也拒绝，不能另选近似文件回退。

## 有限验证边界

2026-11-15的41项有限检查含22个预期拒绝，使用临时合成事务和模拟原guard路由；覆盖归档字节/mtime、终态、项目隔离、全局I/O不变、正常/异常恢复、未知钩子拒绝、来源/章节改动仍拒绝、权威漂移和缺少实际历史guard拒绝。另有隔离namespace CLI及真实派生工程检查。不是41个媒体作业，更不是发音、自然度或情感验收。

本次真实采用及失败保留情况见`science-performance-historical-plan-2026-11-15.md`。不得在成功绑定后修改本适配器再追认旧结果；后续行为变化另立显式版本。
