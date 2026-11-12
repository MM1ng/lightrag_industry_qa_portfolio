# 内部工具受控验证审计

审计基于 2026-09-10 当前内部工具的本地 staging 验证环境；页面结论来自本次实际访问，数据库结论来自只读检查。未执行文档上传、删除、Promote 或 Rollback。该审计证明的是受控验证环境和内部工作流可用，不等于已完成真实公司生产部署。

| User path | Status | Evidence / boundary |
| --- | --- | --- |
| 启动 | READY | `scripts/start_workbench.ps1 -NoBrowser` 已存在；当前 API 8000、前端 5173 正在运行 |
| `/chat` | READY | `http://127.0.0.1:5173/chat` HTTP 200；页面显示 API 就绪和当前 KB |
| 提问 | READY | 高频问题可提交；真实回答耗时 5851 ms，状态为“可执行回答” |
| 查看回答 | READY | 回答按正文（含可点击的行内引用）、依据和反馈分区展示；与正文重复的论点不再二次渲染 |
| 查看 citation | READY | 引用 `[1]`、`[2]` 可点击；抽屉展示文档、页码、Generation、版本和摘录 |
| 查看来源 | READY | 抽屉提供页面上下文和 `打开原始 PDF` 链接 |
| Knowledge Base 状态 | PARTIAL → Phase16 UI 补强 | 原页面能选择当前 KB、显示文档/chunks；补强后显示 Active 标记和 Active Generation |
| Active Generation | PARTIAL → Phase16 UI 补强 | 后端已有 detail/lifecycle 数据；补强管理页面的可见性，不改变语义 |
| 文档更新 | READY | “文档与更新”页面提供上传/替换/删除入口，操作进入生命周期任务 |
| Candidate | READY | “Generation 生命周期”页面列出代际和状态，可执行受保护验证 |
| Validation | READY | 页面有验证入口；当前数据库最新正式 run 为 `passed` |
| Promote | READY（受保护） | 页面有二次确认；本次审计未执行会改变 Active 指针的操作 |

## 当前数据快照

- Active KB：当前受控验证环境的脱敏内部知识库
- 文档：2；child chunks：453
- Active Generation：`g9b05e83c30b45eb7da1c5224`
- Active Generation 对应数据库活动指针：`b856c823e4894ed4bea7c1e41a0b7db8`
- 最新 canonical validation：`passed`
- `/readyz`：`{"status":"ready"}`
- `/chat`：HTTP 200

## 无障碍与可用性观察

- 页面使用 `main`、`nav`、`header`、按钮、原生 `select` 和可访问名称；引用抽屉有 dialog、关闭按钮和 Escape 关闭行为。
- 全局提供可见 focus ring，并尊重 `prefers-reduced-motion`。
- 仍建议后续用键盘完成一次完整管理员流程，并用屏幕阅读器确认表格列标题关联；截图和当前静态检查不能证明完整 WCAG 合规。
- 管理页面受 Bearer 权限保护；本次没有通过 UI 输入或暴露管理员凭据，因此管理页面的视觉截图属于待授权项。
