# 保留历史的编辑双版正式安装

`media_workflow.editorial_install`把已通过共享媒体验证的候选，连同当前源译、显示字幕和质量证据正式安装到原工程。它不重新编码、合成或转写，不把候选的旧路径成功直接抄成正式路径成功；安装时重新扫描当前完整WAV并生成正式路径的render-bound质量与原生技术release。

## 配置与命令

配置是显式JSON，不接受任意脚本，包含且仅包含以下字段：

```json
{
  "kind": "installed-complete-utterance-editorial-media-v1",
  "work": "/absolute/output/.zh_work_yt_VIDEO_ID",
  "candidate_config": {"path": "/absolute/media-config.json", "bytes": 0, "mtime_ns": 0, "sha256": "actual-digest"},
  "candidate_verification": {"path": "/absolute/candidate/verification.json", "bytes": 0, "mtime_ns": 0, "sha256": "actual-digest"},
  "scope": "explicit-history-preserving-installation"
}
```

身份示例是占位符，须替换为实际已验证文件的字节、mtime和SHA-256。

```bash
python -m media_workflow.editorial_install prepare INSTALL.json --output NEW_INSTALL
python -m media_workflow.editorial_install apply INSTALL.json --output NEW_INSTALL
python -m media_workflow.editorial_install check INSTALL.json --output NEW_INSTALL
```

CLI持有原工程锁。`prepare`只接受隐藏工作流区内的新安装目录，重新检查完整候选，并要求原工程来源、旧正式双版、审校文件和数据库仍与候选基线完全一致。较新的人工修改或审校历史必须先纳入，不能覆盖。成功后不再修改已绑定适配器的实现身份。

## 多文件事务与恢复

共享`media_workflow.file_transaction.FileTransaction`登记每个明确目标、原身份或原来不存在、候选源及证据身份。目标只能是工程直接文件或同级MP4；拒绝重复目标、符号链接、越界路径及不安全名称。

- `archive/`保留原文件的完整字节与mtime，`staged/`保留候选及每个生成版本；不靠旧可变路径维持候选 authority。
- 准备合同不可变，事务日志先登记每次写入的完整版本和目标身份，随后通过同目录pending文件与rename逐文件原子替换。
- 多文件整体不是文件系统原子操作。进程被中断时，普通运行时拒绝未提交事务；不能把一部分新文件视为正式成功。
- 只有原身份或已登记版本才可替换。未知人工修改不会被覆盖或自动删除；已提交事务也不会隐式回滚。
- `recover`显式恢复所有原文件及原来不存在的状态，保存失败、生成版本和已知pending文件。恢复依赖事务自己的快照，不要求退役候选源仍可用；未知或不完整pending会失败关闭并保留。

```bash
python -m media_workflow.editorial_install recover INSTALL.json --output INTERRUPTED_INSTALL
```

已失败／中断的安装不自动重试，终态恢复不算安装成功。先检查日志和真实进程终态，再决定新的显式尝试。配置、事务实现及准备收据本身仍须有效；无法验证这些身份时不猜测恢复。

## 正式路径证据

安装31项当前文件：19项候选来源／显示／计划／审校输入、2个正式MP4、10项生成的来源指针／旁白／render／upload／声音报告与页面／数据库指针／质量／检查／release。清单按实际候选生成，不把这个实例数量当所有工程常量。

正式媒体直接从已验证候选精确复制，保留字节和mtime。包记录实际安装适配器，并另列原共享编码器`media_producer`；不冒称冻结包实现或本次重新编码。候选的完整解码、逐包、逐帧时间和AAC证明，只可通过精确文件身份转移，不能声称本次新做过完整解码或ASR。

私库从最新候选一致性备份，安装时在正式工程路径实际扫描当前WAV；全部旧schema／表／行及无关项目指针保持，仅允许追加扫描、词项和候选及更新当前项目指针。原私库不改，正式指针指向本次私库。未人工试听的候选不会写成dismiss、needs-fix解决或人耳决定。

原声短尾分类逐项绑定当前报告与实际源PCM，只表示合成尾句规则不适用；候选仍在报告和数据库，不批准原声角色、停顿或可懂度。

`runtime(work)`仅作用于精确正式工程；其他工程仍走冻结验证器。拒绝嵌套或混入未知补丁，并在正常或异常退出时恢复。裸冻结旁白验证必须拒绝额外安装来源合同；后续检查需显式使用本共享运行时，不套用旧单片运行时。

正式安装仍不等于专业发音、情感、完整人耳／视觉、逐词声画或PotPlayer通过。有限Linux libVLC、WordBoundary、ASR和相关性均不能代替这些批准。

## 2026-11-14 实跑

`cjiUXZh0ZCo`142／270补句已安装到两条正式MP4路径。31项事务`committed`，媒体字节和mtime精确等同此前完整验证的候选；均5572.982秒、40.2秒有声导读、85章，母版双音轨三软字幕、上传版单中文主轨烧字。此次无新编码、TTS、ASR或完整解码。

64项有限事务／恢复／作用域检查（27拒绝）、namespace CLI和prepare／apply／check通过。独立核验435个原身份：414不变、21替换且有精确旧版归档；31当前项／112登记身份另核，98根Python、293MP3、原私库保持。候选在模拟37个旧可变路径不可用时仍通过且未访问这些路径；原始下载与不可变导读资产仍须保留。

正式路径实扫完整当前WAV，私库85→86扫描、4095→4286问题、7484→7805词项；独立原始SQL核验全旧行／schema、41术语、60项目和无关指针保持，0决定／解决。191候选未听辨，12原声短尾只作合成尾句规则适用性分类。正式路径render-bound质量和原生技术release重建通过，不继承旧render成功。

prepare／apply／check／有限VLC四持久外层真实0。正式路径有限VLC章节／轨道切换和六真实截图断言通过，但日志68行H.264缓冲／解码及时间戳告警原因未确立，不称无告警／全片播放验收或擅称只是退出告警。完整解码证明来自精确同身份候选，不冒称此轮重做。六受限JPEG逐张局部查看：开场页、片尾培训页和第一导读场景可见；上传版有烧字、母版未选软字幕。v1限额断言真实1与部分文件保留，v2只缩尺寸压图，各长边≤1280、实际≤32768字节，未重编码视频。

配置、准备、事务和收据在`request-cjiUXZh0ZCo-20261114/editorial-install-config.json`、`editorial-install-v1/`、`editorial-install-audit/`与`checkpoint-editorial-install-v1/`。专业发音／情感、全片人耳／视觉、逐词声画和PotPlayer待验，持续品质目标active，不覆盖其他请求或全局总数。
