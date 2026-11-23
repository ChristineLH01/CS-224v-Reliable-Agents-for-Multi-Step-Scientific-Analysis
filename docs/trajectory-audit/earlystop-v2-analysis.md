# Early-stop-only 与 shared-state v2：30 对 30 轨迹比较

## 比较范围及版本说明

基线使用用户更新后的 `runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts/`，现含 10 题、30 次。按用户明确确认，标为 early-stop-only，而不是原版。日志未提供 adaptations 或 early-stop 检查标记，无法从日志独立验证该开关。v2 使用四个独立运行目录中共 30 次，去重后每题 3 次。

两组不是配对随机种子，也不是同时间运行；模型服务延迟、资源访问和随机探索可能不同。基线 probe 未显式记录 max_output；v2 为 8000，故不能宣称除了 shared state 所有底层条件都经过严格验证一致。

## 全量统计

| 指标 | early-stop-only（用户确认） | v2 |
|---|---:|---:|
| 正式得分 | 17/30 | 17/30 |
| 实际 shell 执行总次数 | 5446 | 4499 |
| 平均 shell 执行/attempt | 181.5 | 150.0 |
| 显式访问记录路径的命令 | 2938 | 1926 |
| 严格相同命令跨阶段/轮次再调用 | 86 | 24 |
| 专用下载调用 | 无此工具 | 154 |
| 专用下载明确缓存命中 | 不适用 | 22 |
| 平均 agent_seconds | 28.9 分钟 | 20.0 分钟 |
| 求解模型 API-price 估算合计 | $3.859 | $3.670 |

v2 shell 总数包含专用下载内部实际执行的 curl，不能只算工具标签为 Bash 的 4367 次。真实 shell 执行减少约 17.4%；显式记录路径访问减少约 34.4%，但仍为 1926 条。这一指标按命令包含 retrievals、SHELDON_RETRIEVALS、analysis/state.json 或 analysis/summary.md 判断；不是所有重读行为的语义分类，也不能把这些读取都视为无用。缺失文件字符串会被后续日志复制，未把原始匹配次数冒充真实文件错误次数。

## 具体差异一：CTCF 的资源协调有实际收益

基线 attempt-3 h2/r36 下载 `/workspace/hg38.fa.gz`，并行 h3/r21、r23 在下载完成前读/检查它，出现 `gzip: ... unexpected end of file`，同时还有 FASTA 索引错误。文件未完成是直接可见问题；没有把所有索引错误都归因于竞争。

v2 attempt-1 h3/r31 下载 chr1，另一个测试 h1/r33 收到 `Reusing shared download`，返回同一个缓存路径。全 30 次有 22 个明确缓存命中，能证实工具复用已发生；无法从这些记录精确推出节省的流量总量。

该题两组均为 3/3。平均时间 59.7 → 39.9 分钟，观察到更少重复劳动与更短时间，但不是准确率提升或单因素因果证据。

## 具体差异二：hb035 的新结果更早成为候选，但原有状态本来就在传递观察

基线 attempt-3 在第三轮 h10.2/r251 算到 miRNAtap 的 mirtar_all 中 let-7f=360，主要用作反驳 miR-145 的观察；最终提示词已经有这条事实，最终答 let-7a（rubric 接受）。不能说基线没有看到过这条证据。

v2 attempt-2 在第二轮 comparative_analysis 已记录 let-7f=360、let-7d=356、let-7b=325；第三轮 explore 据此明确提出 let-7f，h11/r198 重算并经功能性相互作用子集检查，最终答 let-7f。

能观察到发现—比较—候选—复核的链条提前了一轮。但这些信息同时通过原生 observations 传递，不能证明新增 handoff 是唯一或必要原因。v2 也更早找到可用的本地数据库资源。

本题得分 1/3 → 2/3，平均时间 33.9 → 16.8 分钟，是本次唯一增加正确次数的题。

## 具体差异三：RNA-seq 的重复基因查询没有被专用缓存解决

基线 attempt-1 的 h1/r14、h2/r18、h3/r19 分别请求同一 Ensembl 基因 ENSG00000178498（DTX3）的 lookup。

v2 attempt-1 中，explore/r4 先查了该基因；h1/r16、h2/r17、h3/r20 仍在各自脚本里查询包含这个基因的列表。部分请求参数/目的不同，不能都算作无用，但能确认不存在通用的 gene-ID 查询缓存：这些 urllib/request 调用绕过 download_analysis。此题所有 v2 attempts 的专用下载调用均为 0。

两组该题仍为 0/3。平均时间 37.5 → 20.4 分钟，错误候选依然围绕 DTX3/HHEX/EPDR1；共享记录未修复候选生成和因果判断。

## 具体差异四：药物题仍然没有提出正确候选

基线三次最终答 Morphine、morphine、ibuprofen。v2 三次答“不足以识别”、另一种“不足以判断”、Codeine。两组正常 explore 提交中的假设都没有明确提出 diphenhydramine，得分仍为 0/3。

这支持“当前共享状态没有解决候选生成”的判断，不支持从检索摘要中没看到某个药名就推断原始输出里完全不存在相关碎片线索。保存记录和更好地组织信息，本身不会自动产生正确解释。

## 新问题与剩余局限

- v2 CTCF attempt-1 h1/r44 抄错缓存 hash，又访问错误的 `/workspace/analysis/data/` 输入路径，仍报文件不存在。目录隔离不是路径正确性保证。
- v2 仍有 1926 条显式访问旧记录路径的命令，包括直接读取 state.json。新增 ledger 也可能成为新的翻阅对象，且部分 handoff 与原生状态重复。
- v2 的 hb002 attempt-3 是 agent_error、answer=null；trace 结束于 RemoteProtocolError/incomplete chunked read。3/3 → 2/3 的下降包含 API 可靠性问题，不能算成模型选错物种，也不能无证据归因 shared state。
- v2 的记录中有 2 次 settled；用户确认的基线未提供此类检查日志。耗时差异不能只归给摘要和缓存。

## 结论

最明确的新收益是专用下载复用和测试文件协调。整体工作量与时间下降、得分保持，是此次历史比较的观测结果。新增摘要是否优于 SHELDON 本来已有的观察和 retrieval 机制，仍未被独立证明；尤其 RNA-seq 查询重复与药物候选遗漏仍存在。

如需验证组件贡献，应在相同镜像与设置下比较“只保留缓存/目录隔离”与“同时加入 handoff”，而不是继续用最终分数为所有 shared-state 组件统一背书。本次仅分析已有记录，没有新跑求解或评分。

逐命令证据见 `earlystop-v2-evidence.md`，60 行 attempt 统计见 `earlystop-v2-all.csv`，汇总数值见 `earlystop-v2-summary.json`。
