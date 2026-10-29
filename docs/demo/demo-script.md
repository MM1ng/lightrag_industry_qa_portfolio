# 内部工具 Walkthrough — 泵检知识问答工作台

目标：说明实习公司内部人员如何使用一个可追溯、可解释、可安全更新的工业手册问答和知识库运维工具。当前 walkthrough 使用脱敏后的受控验证数据，不上传真实公司文档、不执行设备控制，也不触发新的离线检索实验。

## 1. 项目解决什么问题（约 30 秒）

工业手册问答的难点不只是生成流畅答案：系统必须找到正确文档和页码，把答案要点绑定到真实证据；当证据不足时应拒答；文档更新后还要保证线上版本不会被未验证的候选内容污染。

## 2. 架构一句话（约 20 秒）

`Documents → Parsing / Chunking → Generation → Retrieval Foundation → Grounded Answer → Citation`。

更新链路是 `Document Operation → UpdateJob → Candidate Generation → Validation → Promote → Active Generation`。只有 canonical validation 通过并显式 Promote，Active Generation 才会改变。

## 3. Scene A：Grounded QA（约 60 秒）

1. 按 README 启动工作台，打开 `http://127.0.0.1:5173/chat`。
2. 确认顶部显示当前内部知识库和 `API 就绪`。
3. 点击高频问题：**离心泵启动前需要检查哪些项目？**
4. 指出回答中的结论、操作步骤和“核验要点”，说明回答状态为“可执行回答”。
5. 强调引用不是模型自己写出的页码，而是服务端从当前 Generation 的证据元数据投影出来的文档、页码和引用 ID。

## 4. Scene B：Evidence Explainability（约 45 秒）

1. 点击回答下方的 `查看依据` 引用按钮。
2. 展示证据抽屉中的文档名、物理页码、Generation、文档版本、受限摘录和页面上下文。
3. 点击 `打开原始 PDF`，说明页面上下文与摘录来自同一份来源；普通用户看到的是可核验信息，不直接暴露内部调试 Trace。
4. 如面试官追问可观测性，补充：API 响应携带 request ID，受保护的管理诊断保留 retrieval trace。

## 5. Scene C：Knowledge Base 与更新生命周期（约 90 秒）

1. 进入管理员入口后打开“知识库”，展示当前 KB 的文档数、已生效文档数、453 child chunks 和 Active Generation。
2. 打开“文档与更新”，说明上传、替换、删除都会进入 UpdateJob，而不是直接覆盖线上索引。
3. 打开“更新任务”，展示任务阶段和状态。
4. 打开“Generation 生命周期”：Candidate 先处于 building/ready，使用 `验证` 运行 canonical validation；验证失败的 Candidate 不能 Promote。
5. 在 Promote 前记下 Active Generation；强调 Candidate validation 成功前线上 Active Generation 保持不变。演示环境不需要为了展示而执行 Promote。
6. 若已经有经授权的 Candidate，可展示确认对话框与 Promote 结果；不要在公开材料或非授权环境中上传真实或敏感文档。

## 6. 工程亮点总结（约 30 秒）

- Citation 绑定真实 document/page/chunk 身份，并可回到原文上下文。
- Evidence Guard 在生成后检查答案要点，证据不足返回 `insufficient_evidence`。
- Generation 隔离、canonical validation 和显式 Promote 共同保证更新一致性。
- 评测数字明确标注为 Development scope，不把受控验证结果包装成公司生产 SLO。

## 7. Known limitations（约 20 秒）

- **D005：Candidate recall edge case。** 正确证据在 `t1739cn.pdf` 第 12 页，但当前 initial candidate pool 未包含该 chunk，因此该问题可能返回 `insufficient_evidence`；这不是 citation 伪造或生命周期故障。
- **Sparse runtime technical debt。** 当前 Active Generation 没有 generation chunk registry，runtime sparse retrieval 初始化仍依赖该注册表。本轮不修复，除非它直接阻塞内部工具主路径。

## 稳定验证问题

| Question | Expected topic | Expected document / page | Visible citation behavior |
| --- | --- | --- | --- |
| 离心泵启动前需要检查哪些项目？ | 启动前检查、灌注与阀门状态 | `t1739cn.pdf` p.31；`2196-ANSI-Manual-Chinese.pdf` p.17 | 回答显示多条 `[n]` 引用；点击后打开对应文档、页码和摘录 |
| 离心泵运行中振动突然增大怎么排查？ | 运行振动故障排查 | `t1739cn.pdf` p.44 | 回答显示可点击依据标签与证据抽屉 |
| 离心泵检修前如何执行断电和泄压？ | 维修安全前置条件 | 已验证的 Active Generation 安全章节证据 | 回答显示安全相关引用；证据不足时明确拒答 |
| 拆卸泵体前有哪些安全注意事项？ | 拆卸前安全注意事项 | `t1739cn.pdf` p.48–49 | 回答显示多条安全引用；点击后打开对应证据 |

D005 不作为正式验证问题。问题与页码以当前 Active Generation 的实际返回为准，不修改 golden answer 迎合展示。
