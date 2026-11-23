# 原版 SHELDON 与共享状态 v2：轨迹核查

> 后续版本说明：用户已确认该历史目录应标为 early-stop-only；目录也补入了另外 5 题。本文件保留首次 15 次轨迹审计的历史内容，当前 30 对 30 分析请看 earlystop-v2-analysis.md。


直接可比较的原版轨迹来自 `runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts`：5 道 preview 题各 3 次，共 15 次。v2 同样取这 15 次；完整 10 题的原版只有用户提供的历史汇总，不能据此编造 full 的命令级对照。

| 指标（15 次 preview） | 原版 | v2 |
|---|---:|---:|
| 正式得分 | 10/15 | 10/15 |
| 模型直接发出的 shell 调用 | 3011 | 2345 |
| 实际 shell 执行（含 download_analysis 内部 curl） | 3011 | 2469 |
| 命令中显式出现旧记录路径 | 1550 | 892 |
| 完全相同命令在其他阶段/轮次再次调用 | 30 | 9 |
| 专用下载调用 | 无此工具 | 145 |
| 专用下载明确缓存命中 | 无此工具 | 21 |
| 平均 agent_seconds | 35.3 分钟 | 24.1 分钟 |
| 求解模型费用估算合计 | $1.959 | $1.870 |

“出现旧记录路径”按命令中包含 retrievals、SHELDON_RETRIEVALS、analysis/state.json 或 analysis/summary.md 的字符串规则统计；它不是所有重读行为的语义分类，也不表示这些读取都没有价值。完全相同命令的重复采用标准化空白后的严格字符串匹配，抓不到参数或路径稍变的重复分析。v2 的共享下载本身也调用 shell，不能把它从 shell 工作量中漏掉。实际执行减少约 18%，不是直接 Bash 数量暗示的 22%。

## 1. 明确的新作用：下载协调

原版 CTCF 第 3 次，h2 的 r36 下载 `/workspace/hg38.fa.gz`；并行 h3 的 r21、r23 在文件完成前读取/检查同一路径，均出现 `gzip: ... unexpected end of file`。另一次 CTCF 运行的 explore 已下载 chr1 到 `/workspace/refs`，随后 h3/r31 又下载到 `/workspace/genome`。这些是原版已有记录但没有统一资源协调的具体实例。

v2 CTCF 第 1 次：h3/r31 下载 chr1；另一测试 h1/r33 收到 `Reusing shared download`，返回完全相同的缓存文件路径。整个 preview 有 21 次明确命中，能确认绕过了新一次下载。此证据支持专用缓存确实被使用；不能据此推算节省的总字节或认定所有下载重复都消失了。

## 2. 测试目录真正被使用，但路径问题没有消失

v2 hb035 第 2 次的 h3.1、h3.2、h3.3，运行目录均为 `/workspace/analysis/tests/round-01/candidate_analysis.h3`，脚本也在该目录生成 iteration2_enrichment.py 和 iteration3_sensitivity.py。不同测试各用自己的目录，修复的是意外相对路径冲突。

反例：v2 CTCF 第 1 次 h1/r44 把下载文件 hash 抄错，又把输入写成 `/workspace/analysis/data/peaks_file.bed`，仍出现文件不存在。不能声称隔离目录消除了路径错误。

## 3. hb035 的信息流变化，不能解释为原版没有记忆

原版第 3 次直到第 3 轮的 h10.2/r251 才完成包含 let-7f=360 的计数。模型把它写为“反对 miR-145”的观察；原版最终提示词已包含这条 360-hit 的观察，但最终仍回答 let-7a（该答案也被 rubric 接受）。

v2 第 2 次第 2 轮的比较提交已明确记录 let-7f=360、let-7d=356、let-7b=325；第 3 轮 explore 依据该结果提出 let-7f 候选，h11/r198 再次计数，最终回答 let-7f。能观察到证据—候选—复核的链条，但原生 observations 也在携带这些信息，新 ledger 不是唯一通道。新一轮还更早找到了合适的数据资源，不能把答案差异单独归因于共享摘要。

## 4. 摘要替代重读，只实现了一部分

v2 仍有 892 条显式访问记录路径的命令。hb035 第 2 次就有 `ls $SHELDON_RETRIEVALS | tail -20`，以及读取整个 state.json、筛选和打印其中条目的命令。某些 handoff 内容与原生状态重复。v2 的 plan 输出还把空的 Shared analysis handoff 抄进了 situation，使该说明可能被重复带入后续 prompt。新的记录也带来了新的“读记录”对象；轨迹不能支持“无需重读原始结果”的强说法。

## 5. 一次失分实际是 API 故障

v2 hb002 第 3 次的 result.status 是 agent_error、answer 为 null；trace 终止于 RemoteProtocolError: peer closed connection without sending complete message body (incomplete chunked read)。它不是输出了错误物种，也不是证据表明共享状态把正确结论改错。原版该题 3/3，v2 2/3 的差异包含这一次运行可靠性问题。

## 限制及判断

v2 同时启用 early stop；这 15 次中有一次 settled，原版无该检查。原版保存的 probe 配置没有显式 max_output，v2 显式设为 8000，因此仅凭文档不能宣称输出设置严格相同。模型调用来自不同时间，服务延迟、随机轨迹及可访问资源也可能不同。

最有直接证据的新收益是下载/文件协调；旧记录访问减少也是可观察的。共享摘要作为新科学记忆的独立收益尚未证实：原版已有观察传播，且 v2 仍大量读日志。建议把缓存/目录隔离与摘要注入拆开做消融，再决定是否保留第二套记忆系统，而不是仅因 20 分钟的平均时间就宣称整套 shared state 有效。
