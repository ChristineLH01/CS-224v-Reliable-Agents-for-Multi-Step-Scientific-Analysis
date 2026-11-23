# runs 2 · SHELDON 失分逐条定位

运行：`runs 2/preview_sheldon_gpt-6-luna_high_3-rounds_timeout-1h_3-attempts`（10 题 × 3 次，正式得分 17/30）。本文只分析 13 次失分。下文行号均指对应 attempt 的 `transcript.md`，即 `problems/<题>/attempt-<n>/transcript.md`。

**行号与上下文的口径**：
- transcript.md 里的工具输出被截在约 2.5k 字符。
- 模型实际看到的工具结果上限约 8k 字符。判断"模型是否看到某条输出"时，用的是 `agent_session/sheldon/runs/*/retrievals/problem/rN.json` 和 `traces/problem.jsonl.gz`，不看 transcript 截断后的显示。

对照组：
- Claude Code（`preview_claude-code_opus-5-5_high_timeout-1h_3-attempts`）：这 5 题上 hb035、hb048、recsvhviava5okg19 为 3/3，hb024、recaikavdwoimjy3b 为 0/3。
- Codex（`preview_codex_gpt-6-1-sol_high_timeout-1h_3-attempts`）：只跑了其中 hb024（0/3）和 hb035（3/3）。

## 1. 总表

| 题 | attempt | 答案 | 标准答案 | 失分类别 | 第一处决定性错误 | 正确信号是否进入过上下文 | 可挽回性 |
|---|---|---|---|---|---|---|---|
| hb024 | 1 | 弃答 | 2 组织（口腔/肠道），3 种蛇 | 题干丢失 → 中间阶段做技术伪影分析 → 弃答 | Plan L13（situation 不含题干）；R1 explore L348 | 部分：r4 文件分组；r36/r59（L3241–3250）口腔型 Suttonella/Cardiobacterium 与肠道型 E. coli | 低（学名无 agent 能恢复） |
| hb024 | 2 | 弃答 | 同上 | 题干丢失；唯一的宿主/组织假设被"保守化"改写后搁置 | R1 exploit L3599–3630（h2→h5） | 是：o8（L872）分组，o27（L6069）Campylobacter/肠杆菌 | 低 |
| hb024 | 3 | 弃答 | 同上 | 题干丢失；分组被 silhouette 门槛否掉 | R2 exploit L7644（h7→h10） | 是，三次中最强：o8（L1434），o13（L3702）E. coli 对 C. showae | 低 |
| hb035 | 1 | 弃答 | 任一 let-7 成员 | 拿到正确排名后 finalize 仍弃答 | R1 h1 r17（L903–905）全部 403，从此进入"不可恢复"框架；致命在 R3 comparative L8999 与 finalize L9079 | **是**：R3 r186（L8629）let-7 家族 193 对 120 | **高** |
| hb035 | 2 | miR-155-5p | 同上 | 先验封闭面板 + PubMed 共现代理计数 | R2 explore 凭先验提 h6=miR-155；R2 h5 r140（L4588–4691） | 否 | 中 |
| hb048 | 1 | morphine | diphenhydramine | 信号在上下文里但从未提成假设；把 piperine 误读为吗啡 | R1 explore L337–395；R2 explore 选 286 不选 256（L3496） | **是**：r6 L297 `256.1699 → 167.0858` | **高** |
| hb048 | 2 | morphine | 同上 | 同上；另有 finalize 违背自身证据 | R1 explore L466–486 | **是**：r11 第 34 行 `256.169870 bp=167.08575`（模型可见，transcript 截断） | **高** |
| hb048 | 3 | ibuprofen | 同上 | R1 解析全失败 → 先验镇痛药候选 → "镇痛药"框架 | R1 explore L602–655 | 基本否：r163 中的 242.15/167.09 行在 8k 截断之外 | 中 |
| recaik | 1 | DTX3 | ALKBH5, FTO | 统计量选错（近零缺失 ≠ 敲除）；正确方向的 h2 未经检验就被判负 | R1 h2 iter1 L663（o1 refutes h2 → h4） | 否 | 低（题目本身难） |
| recaik | 2 | HHEX, DTX3 | 同上 | R1 explore 就把 top-k 基因名写死进假设 | R1 explore L198–241 | 否 | 低 |
| recaik | 3 | EPDR1 | 同上 | 题干丢失，三轮都不知道要找敲除基因；finalize 临时排序 | Plan L14；finalize r188（L9544） | 否 | 低 |
| recsvh | 1 | SBS5 | SBS3 | 汇总口径选错（等权均值 vs 合并），SBS3 假设被改写后消失 | R1 h1 被改写为 h4（L1557）；R1 comparative o11（L1680） | **是**：r32（L470 起）pooled SBS3 35.3% | **高** |
| recsvh | 2 | SBS5 | 同上 | 同上；h3 局部被证实后仍被改写成相反结论 | R1 h3 local（L1058–1074，r36 支持 SBS3 → 改写为 h5 "SBS5 mean"） | **是**：r36 aggregate SBS3 729,516 > SBS5 594,289 | **高** |

"可挽回性高"：正确答案的直接证据已经进入模型上下文，或已被 SHELDON 自己计算出来，只要某一阶段换个处理就能答对。这样的 attempt 有 5 个（hb035-1、hb048-1、hb048-2、recsvh-1、recsvh-2），挽回后可从 17/30 提到约 22/30。hb035-2、hb048-3 改动更多才可能挽回。hb024、recaik 的 6 次失分在 Claude Code 上同样全错，主要难在题目本身。

## 2. 失分的阶段分布

按"第一处决定性错误"所在的阶段归类：

| 阶段 | 失分 attempt | 机制 |
|---|---|---|
| plan | hb024 ×3，recaik-3 | 题干被拆进 `deliverable`，中间阶段看不到 |
| explore（候选生成） | hb048-1/2/3，hb035-2，recaik-2 | 只按单一强度指标取 top-k；凭先验点名；解析失败时仍提交假设 |
| candidate analysis（局部改写） | recsvh-1/2，recaik-1 | revised_claim 改写了题目目标量，或把已被证实的假设改成相反结论；未检验就判负 |
| comparative / exploit | hb024-2/3，hb035-1 | "保守化 refine"把可证伪主张改成"数据不能确定"；家族级证据判为 neutral |
| finalize | hb035-1（主因），hb048-2（加重） | 有排名仍弃答；没有任何支持假设时仍输出药名 |

## 3. 跨题根因

### 3.1 题干在 plan 拆分后丢失（硬 bug，影响 7/30）

plan 阶段把原题拆成 `situation` 和 `deliverable`。缓存的 sheldon 包中：
- `sheldon/methods/sheldon/run.py:134` 的 `base_context()` 只传 `situation()`。explore、select、candidate analysis、comparative、exploit 都用它。
- `deliverable` 只在 finalize 的 `final_step()`（`stages.py:315-322`）中才拼回题目。

核查方法：对 30 次 trace 逐个 LLM 调用检查是否含题干原文。plan 把问句放进 deliverable 的 attempt 中，只有 plan 和 finalize 的调用含题干。

| 题干丢失的 attempt | 结果 |
|---|---|
| hb024-1、hb024-2、hb024-3 | ✗ |
| recaik-3 | ✗ |
| hb002-2、hb010-2、recsvh-3 | ✓（题目对中间阶段不敏感，或侥幸） |

hb024-2 的 situation 保留了背景句 "microbial communities from tissues of non-human species"，但问句仍在 deliverable 中。plan 是否丢题是随机的，`plan_min_chars` 的长度检查拦不住，因为环境描述本身就足够长。

后果有两点：
- hb024 三次、recaik-3 的整整三轮都在回答"文件或样本之间为什么不同"这类自造问题。
- recsvh-3 反而因丢题采用了跨组织的 aggregate 口径，侥幸答对。这说明 recsvh 的对错并非流程本身裁决出来的。

### 3.2 候选生成不从数据全集出发

- **hb048**：explore 按复现次数或 MS2 基峰强度取前几名（293.098、卤素族、286.1439）。没有"共享产物离子聚类"或"药物样筛选"。256/272/432 三个前体共享 167.0858 二苯甲基正离子，这条线索在 attempt-1/2 的上下文中出现过，却从未成为假设。
- **hb035-2**：凭先验点名 miR-21/16/let-7a/34a/29b/155，在 8 个候选的封闭面板内比较，结果实际衡量的是文献热度。
- **recaik-2**：R1 explore 直接把 top-k 下调基因名写进 h1–h3（L231/236/241），解空间在第 0 轮就封闭了。
- **hb048-3**：R1 的 8 次解析命令全部失败（L196–533）后仍提交了 acetaminophen、caffeine、ibuprofen 三个先验药名。

### 3.3 candidate analysis 的 revised_claim 可以改变目标量或翻转结论

- **recsvh-2**：h3（SBS3）自己跑出的 r36 证实了 SBS3 在 aggregate 下领先，却被改写为 h5 "COSMIC SBS5 has the highest mean relative contribution"（L1074）。最终状态表里 h5 的 region 仍是 "HRD-associated SBS3"。题目里根本没有 "mean"。
- **recsvh-1**：h1 local 本已写下 "pooled estimate … gives a clear SBS3 lead"（L1085–1087），却被改写成"两种口径分立"的 h4（L1557）。此后没有任何假设主张 SBS3。
- **recaik-1**：h2（"被敲除基因的 mRNA 可能保留"）在 L663 以 "multiple genes display consistent near-absence" 为由被判 refutes，并改写成相反的 h4。同轮 o2、o11 承认 h2 的检验根本没有做过。

### 3.4 comparative / exploit 的"保守化"把答案改没了

- **hb024-2**：h2 "consistent with … tissue or host-associated communities" 被改写成 h5 "data do not establish … tissue/host effects"（L3599–3630），之后再没被选中。
- **hb024-3**：h7 "profiles resolve into a small number of coherent assemblages" 因 silhouette 0.04–0.26 被改写成否定式的 h10（L7644）。
- **hb035-1**：let-7 家族 193 对 120 的结果被判 neutral，理由是 "does not resolve one mature strand-specific leader"（L8999–9020）。而题目只需一个名字。

### 3.5 finalize 不做裁决，置信度与证据脱节

| attempt | finalize 行为 | 问题 |
|---|---|---|
| hb035-1 | 置信度 0.99 弃答（L9079–9091） | 手握 let-7 第一的排名 |
| hb048-2 | 0.72 输出 morphine（L10341） | 所有假设均 shelved 或 pending；o35 自己已指出 286 是 piperine |
| hb048-3 | 0.84 输出 ibuprofen（L8981） | 预期碎片缺失，EIC 相关性约 0 |
| recsvh-1/2 | 0.99 输出 SBS5 | 状态中明确记录了 pooled SBS3 第一，没有给出选口径的理由 |
| recaik-2 | 在并列 tested 的 h5（HHEX+DTX3）和 h6（EPDR1）间无理由二选一 | 与 comparative 的倾向相反 |

### 3.6 轮次被同一证据的重复观测占满

- 每个假设被拆成约 3 个观测区域，同一事实被反复写成大约 8–10 条 oN：
  - hb048-1：scan 1742 的同一张谱图约 10 条；
  - hb024-3：1–7+14 共享主导序列 8 条；
  - recaik-1："distinct chromosomes" 7 条。
- 后两轮多在 round-1 的同一候选族内做稳健性检验（hb048-1 的 R3 全在比较 morphine、hydromorphone、codeine；recsvh 的 R2–R3 全在做 SBS5 的 bootstrap 和分层），没有重新扫描尚未解释的信号。
- 停止原因全是 `max_rounds`。除 recaik-3（58 min）外均未触及时间上限，hb035-1 还剩约 37 分钟。

### 3.7 工具与网络（多为诱因，非根因）

- **hb024**：`blastn -remote` 三次全部失败（CRPCClientException），没有改用 Blast.cgi URL API，因此拿不到 nt 宿主注释。Claude Code 正是靠这条绕行看到了 "Heterodon … cloaca"、"snake venom"。
- **hb035**：mirtarbase、mirbase、targetscan 官网返回 403。可行路线是 Bioconductor 离线包（targetscan.Hs.eg.db、miRNAtap.db、ceRNAnetsim），attempt-1 和 attempt-3 直到 R3 才走到，attempt-2 始终没走到。
- **recaik-3**：多次 600 s 超时（L2472、L6480、L8315）。

## 4. 逐题细节

### 4.1 hb048（0/3）：diphenhydramine 被 piperine 与"镇痛药"框架挤掉

数据要点：
- 最强外源特征 m/z 286.1439 → 201.0549、135.044、112.076 是 **piperine**（黑胡椒）。SHELDON 自己算出 286→201 的中性丢失为 C5H11N（85.089 Da，哌啶），吗啡的典型碎片（165.05、153.05、229.09、185.06）在 o50 中确认均未检出。
- 答案的指纹是 256.1699 → 167.0858（二苯甲基正离子），并有代谢物佐证：272.1646（N-oxide）、432.2024（N-glucuronide）、242.15（nor-DPH）均以 167.0858 为基峰。
- Claude Code 用"全前体 + 前 8 个碎片"的列表一眼排除了 piperine，然后按产物离子家族加代谢物确认 DPH，三次均在约 60–74 s 内完成。

**Attempt-1（morphine，0.84）**
1. R1 explore（L337–395）：r6（L282–332）已列出 L297 `256.1699 N1 maxBP7.6e+05 prod167.0858`。r6.json 第 61 行（272.1646）和第 80 行（432.2024）也在 5.4k 字符内，全部进入了模型上下文。explore 只提出 h1=293.098（RT 约 0.3 min，接近死体积）、h2=其代谢物、h3=卤素/硫族。
2. R2 explore/select（L3153–3514）：256.169 仍在上下文中，选中的却是 h5=286.1439 和 h6=205/188（L3496、L3501）。
3. R2 h5 iter1（L4647）o23 首次写出 "consistent with protonated morphine"。o37 记录了 C5H11N 丢失，却仍标 morphine-compatible。o50（L11257）的碎片缺失被当作"证据有限"，没有当作反证。
4. R3 三个名额全部给了 hydromorphone、codeine、morphine 对 hydromorphone（L8135–8146）。finalize（L11169）依据 pending 的 h14 输出 Morphine。
5. 最小修复：R2 explore 要求按共享产物离子聚类前体，或在 h5 iter1 把 C5H11N 识别为哌啶并据此否定吗啡。

**Attempt-2（morphine，0.72）**
1. R1 explore（L428–486）：r11（L407–423，MS2 TIC 前 40）第 34 行是 `precursor=256.169870 rt=3.4156 bp=167.08575 top=167.0858,165.0704,152.0623`。transcript 截断了这一行，但 trace 显示模型收到了完整的 8135 字符。explore 只提出 h1=293.098、h2=346.010、h3=代谢网络。此后再没有命令输出含 256.169。
2. R2 comparative 的 o35（L10419）已正确指出 "includes both morphine and piperine; the latter's piperidine-containing structure makes the 85-Da loss … plausible"。R2 exploit（L7533）因此把 h6 精炼成 h11 "does not establish … a unique morphine identification"。
3. R3 select（L7874）没有选 h11，而是选了 293 钠加合物、162.11 肉碱等内源物方向。最终状态（L10363–10381）中没有任何主张药名的存活假设。
4. finalize（L10341）仍输出 morphine。
5. 最小修复：R2 exploit 把 286 重标为膳食成分，并新增"在剩余前体中找药物样特征"的假设；或者让 R3 explore 重读 r11 全表。

**Attempt-3（ibuprofen，0.84）**
1. R1：r5、r6、r7、r10、r11、r13、r14、r16 全部 exit≠0（L196–533，pyteomics 导入失败、XML 命名空间、base64、sed 语法），仍在 L602 提交 explore，h1–h3 为 acetaminophen、caffeine、ibuprofen（L642–655）。
2. R2 加入 naproxen、aspirin，select 理由写着 "both distinct analgesic alternatives"（L4193），框架被定为"镇痛药"。
3. R3 h14 的 r163（L7142–7161，16.7k 字符）在第 11252 字符处含 `242.15 … top=(167.0859…)` 与 `167.09 … (167.0858, 778152)`。这超出了约 8k 的模型可见范围，模型没看到。扩展药物面板（L7255）里没有任何抗组胺药。
4. ibuprofen 的证据很弱：207.138 的 MS1 仅约 1.37e5（o42），预期碎片缺失（o37），EIC 相关性 −0.008 和 −0.032（o44）。exploit（L8953）仍以 "no other coherent competitor" 为由保留。
5. 最小修复：解析未成功前禁止提交 explore；长输出用 grep 或分页，不要 `head -100`。

### 4.2 recsvhviava5okg19（1/3）：汇总口径（estimand）

数字在三次 SHELDON 与 Claude Code 之间完全一致（COSMIC v3.4 GRCh37，scipy NNLS）：

| 口径 | 第一 | 第二 |
|---|---|---|
| 合并：占乳腺全部突变的份额 | **SBS3 35.3%** | SBS5 28.4% |
| 等权：每样本归一化后取均值 | **SBS5 36.5%** | SBS3 23.7% |

SBS3 集中在高突变负荷的 HRD 样本中（如 Breast.14 中 SBS3 占 93.4%）。SBS5 作为平坦签名，在低负荷样本中吸收残差。参考库、下载、拟合稀疏性都不是问题：Claude Code 换用 `cosmic_fit` 后排名不变。

**Attempt-1（SBS5）**
1. r32（L470 起）首次拟合同时给出 pooled 和 mean 两个口径。
2. h1 local（L1085–1087）的判断是对的，但 h1 在 L1557 被改写成 h4（"两种口径分立"），此后没有任何假设主张 SBS3。
3. R1 comparative o11（L1680）以等权口径判 h1 为 refutes。o14（L1771）承认 "The prompt does not specify which aggregation convention"，却没有安排裁决。
4. R2 explore（L1911–1923）的 h5 和 h6 都是以 SBS5 为前提的逐样本统计。R3 继续做 leave-one-out、bootstrap、分层。h7 已经点出需要明确口径，exploit 只 keep 不执行。
5. finalize（L6063）的原文同时写出 "equal-weight mean … SBS5 … Pooling … ranks SBS3 first"，却以 0.99 选了 SBS5。

**Attempt-2（SBS5）**
1. R1 h1 local（L587–606）：自己的 finding 写的是 "pooling … gave SBS3 the largest share (35.3%)"，revised_claim 却是 "SBS5 has the highest average relative contribution"。"average" 是模型自己加进去的。
2. R1 h3 local（L1058–1074）：r36 的 aggregate SBS3 为 729,516，大于 SBS5 的 594,289，假设本身被证实，却仍被改写为 h5 "SBS5 … mean"。
3. R1 comparative（约 L2013）把 "For highest average … favors SBS5" 固化成定论。R2 select 没有选 h7（口径假设），L2165–2178；R2 名额给了 SBS13、SBS8 这类陪跑候选。
4. finalize（L6159）只输出 `<answer>SBS5</answer>`，置信度 0.99。

**Attempt-3（SBS3 ✓）**：plan 把题目放进 deliverable。R2 explore 做跨组织 aggregate 拟合（r67，L3072–3075，"Breast … SBS3 0.352"）时，默认用的就是合并口径。答对靠的是随机性，流程本身没有裁决口径。

**Claude Code**：同样算出两组数字，在 attempt-1 L282 用一句领域惯例裁决："a cohort-level 'relative contribution' usually means the share of all mutations attributed to each signature"。

### 4.3 hb035（1/3）：靶基因表反推 miRNA

数据是删掉了 miRNA 名称列的 miRTarBase 风格表（1190 行，473 个唯一基因）。正确做法是拿这些基因去对 miRNA–靶基因数据库，按 miRNA 统计覆盖基因数。不需要 miRBase mature.fa。

**Attempt-1（弃答）**
1. R1 h1 iter1 的 r17（L873–905）同时请求 miRTarBase、mirbase.org、ebi.ac.uk，三个全部 403。r25（L1035）请求 ftp.ebi.ac.uk 根目录，在 10 s 时超时；同一域名后来正常访问了 6 次。
2. 此后 h1–h14 全部是"miRNA 身份能否恢复"这类方法论假设，没有一个点名具体 miRNA。42 条观察中约 20 条重复描述输入表结构。
3. R3 h12：L6780 下载了 targetscan.Hs.eg.db，但两个 iteration 之后才统计。r186（L8629）的结果是 `('let-7/98/4458/4500', 193), ('miR-202-3p', 120)`，写入 o40、o42。
4. R3 comparative（L8999–9020）判为 neutral，exploit（L9034–9070）把 h12 进一步削弱。finalize（L9079–9091）以 "prediction-based family grouping rather than a strand-specific miRNA" 为由，以 0.99 弃答。
5. 最小修复：finalize 规定"有可计算的排名时禁止弃答"，从第一家族中选代表成员即可得分。

**Attempt-2（miR-155-5p，0.79）**
1. R1–R2 explore 每个假设都先验点名一个 miRNA（h1=miR-21 … h6=miR-155，约 L3840）。
2. R2 h5 iter1（L4449）把面板写死为 8 个候选，用 PubMed 标题/摘要共现计数。r129 宽松检索中 miR-21 第一；r140 改为精确 arm 名检索后 miR-155 以 43 个基因排第一（L4588–4691，o30）。精确 arm 名检索对 let-7 惩罚最重：文献多写 "let-7a" 而不写 -5p，且家族信号分散在十多个成员上。
3. 唯一的功能证据（miR-155 mimic 数据集）方向相反，o25、o28、o41、o53 均判 refutes。
4. R2、R3 exploit（L7655–7680、L12091）都保留了 h7。o67（L12021）承认 "do not yield a stable aggregate ranking"。finalize（L12121–12131）没有推理。
5. 多个 Bioconductor 离线包都能解此题，它一个也没试，因此全程没有做过数据库计数。

**Attempt-3（let-7a-5p ✓）**：let-7a 在 R1 explore 就被先验猜中（L221–222，理由是 HMGA2 是 let-7 的经典靶基因）。R3 h10 iter2 下载 miRNAtap.db（L12411），r251（L12519–12539）得到 let-7f=360、let-7d=356、let-7a=315，这才有了数据支撑。数据第一的其实是 let-7f，它仍答 let-7a，因为 rubric 接受全家族才判对。

### 4.4 hb024（0/3）：题干丢失 + 弃答

18 个文件都是细菌 16S V4 扩增子。几条长读段在 Codex 中被鉴定为 PhiX，所以靠蛇 mtDNA 鉴定宿主这条路本来就走不通。可用的信号只有两类：菌群组成（口腔型对肠道型），以及 nt 命中记录的 host/isolation_source 注释。

| attempt | 用时 | 关键位置 |
|---|---|---|
| 1 | 959 s | Plan L13 situation 不含题干和背景句 → R1 explore L348 的轴是 "Explanation for cross-file sequence patterns" → 17 个假设全是深度、稀释、错误变体、聚类阈值。r36/r59（L3241–3250）中文件 11/12 的优势菌是口腔型的 Suttonella/Cardiobacterium，却被当作"16S 分辨率不足"。remote BLAST 在 r21 失败（L1317–1328）。finalize（L10782–10912）只跑了 `ls`/`head`。 |
| 2 | 2424 s | situation 只有背景句。R1 exploit L3599–3630 把 h2 改写为 h5（"do not establish tissue/host"），R2 select（L3810–3825）没选 h5。o8（L872）已有分组，o27（L6069）已有 Campylobacter showae（口腔型）与肠杆菌。`-remote -db nt` 在 L1178、1719、2289、2925、4399、6417 失败。finalize（L9552–9570）2 次调用，0 条命令。 |
| 3 | 955 s | R2 exploit L7644 把 h7（可分为少数清晰的组）改写为 h10（不能分组），依据是 silhouette 0.04–0.26。o13（L3702）中 1–7、14 为 E. coli 100%，文件 8 为 C. showae 97.2%，已接近"肠道对口腔"的结构。 |

对照：
- Claude Code 三次都给出了"蛇 + 口腔/肠道（泄殖腔）"方向的具体答案，物种和数目不对。
- Codex 同样弃答。
- 三家 agent 的 transcript 中都从未出现 Boiga、Trimeresurus、Laticauda。

结论：学名部分大概率不可恢复。SHELDON 特有的损失是连"蛇、口腔/肠道"这一层都没走到。

### 4.5 recaikavdwoimjy3b（0/3）：题目本身难，加上一次丢题

本地复算（`data/full/problems/recaikavdwoimjy3b/anonyomized_rnaseq_count.tsv.gz`）：
- FTO：323/348/381 对 774/711/617，log2FC 约 −0.85，在对照 CPM>20 的基因中按下调幅度排第 131 位。
- ALKBH5：log2FC 约 −0.34，排第 1265 位。
- 背景：方向完全一致的下调基因共 2,977 个。
- EPDR1、DTX3、HHEX 是 on/off 式缺失，任何常规排序都把它们排在最前。

三次 SHELDON transcript 中 `ALKBH5|FTO|m6A|ENSG00000140718|ENSG00000091542` 的命中均为 0。Claude Code 三次也都答 EPDR1/DTX3/HHEX 的组合。

| attempt | 用时 | 关键位置 |
|---|---|---|
| 1（DTX3） | 1967 s | R1 explore 的 h2 自带"有孤立近零缺失即削弱"的否证条件（L241），而 r3 早已列出近零基因。R1 h2 iter1 L663 判 refutes 并改写为 h4。o12（global_test，L9387）锁定 DTX3。R2/R3 名额全给 multiplex、HHEX 调控、批次、isoform（数据中没有 isoform 信息，o56）。 |
| 2（HHEX, DTX3） | 1310 s | R1 explore L198–241 把 HHEX、HHEX+DTX3、EPDR1 直接写成 h1–h3。R3 exploit（L6434 起）同时 keep h5 和 h6。finalize（L6482）无理由选 h5，且与 comparative L1893 "EPDR1 is the strongest" 的倾向相反。 |
| 3（EPDR1） | 3479 s | Plan L14 把问句放进 deliverable。L26–9427 中 grep "knock" 命中为 0，三轮在做细胞组成、干扰素、上皮分化注释。finalize 才第一次看到题目，临时跑 r185–r190，按 (A+0.5)/(B+0.5) 排序（r188，L9544）输出 EPDR1。 |

## 5. 修复建议（按可挽回的分数排序）

1. **把题干带进所有阶段（plan 修复）**。已实现为 adaptation `plan_keeps_question`（`bmbench/agents/sheldon_runtime/bmb_sheldon/adapted/plan_keeps_question.py`，测试为 `tests/test_plan_keeps_question.py`）：plan 之后检查 situation 是否保留了问句，没有保留就回退到完整题面。在本次 30 份真实拆分上回放，正好标出这 7 次。`base_context()` 同时传 `situation` 和 `deliverable`，或者校验 situation 必须包含原题的问句段，不满足就回退到完整题面。这一条影响 7/30 次 attempt，是确定的 bug。修复后 hb024、recaik-3 未必答对，但至少是在回答对的问题。
2. **finalize 加入裁决规则**。
   - 有可计算的排名时禁止弃答（hb035-1）。
   - 同一数据在不同口径下给出不同答案时，必须按领域惯例裁决并写明理由（recsvh-1/2）。
   - 没有任何存活假设支持某个答案时，降低置信度并回到原始证据（hb048-2）。
   - 置信度应与存活证据挂钩。
3. **约束 revised_claim**。不得改变题目的目标量（不得自行引入 "mean"），不得把局部已被证实的假设改写成对立结论（recsvh-2 的 h3→h5），不得用"存在竞争信号"去判负一个未执行检验的假设（recaik-1 的 h2）。
4. **约束 exploit/refine**。不允许把可证伪的主张改写成"数据无法确定 X"（hb024-2/3、hb035-1）。select 必须保留至少一个直接回答题目的假设。
5. **候选生成从数据全集出发**。
   - explore 前必须有一次成功的全量汇总（hb048-3）。
   - 对质谱这类数据，要求按共享产物离子聚类，并做外源物/药物样筛选（hb048-1/2）。
   - 禁止把先验名字或首个 top-k 列表直接写成初始假设（hb035-2、recaik-2）。
6. **长输出处理**。工具结果在模型端约 8k 字符就被截断，提示模型用 grep、分页或写文件再查（hb048-3 的 r163）。
7. **工具绕行提示**。`blastn -remote` 失败时改用 Blast.cgi URL API。官网数据库返回 403 时优先找 Bioconductor 或 PyPI 离线数据包。
8. **观测去重与轮次复用**。同一事实只记一条观测，后两轮至少留一个名额给"重新扫描未解释信号"，避免全部用于同一候选族的稳健性检验。
