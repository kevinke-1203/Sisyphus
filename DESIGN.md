# Evo — 设计与实现文档

> 项目愿景、系统架构、技术选型、设计原则见 [README.md](README.md)

---

## 已完成阶段概要

详细的里程碑规划已在实现过程中完成，以下为各阶段的核心交付：

| Phase | 周期 | 核心交付 |
|-------|------|----------|
| 1 基础编排 | Week 1-2 | LangGraph StateGraph, plan→code→test pipeline, 条件重试 |
| 2 记忆系统 | Week 3-4 | 本版本已移除，暂不考虑记忆能力 |
| 3 轨迹与评分 | Week 5-6 | Trajectory 数据模型, SQLite 持久化, 规则评分引擎 |
| 4 模式进化 | Week 7-8 | 模式提取 + 向量存储 + 语义检索 + prompt 注入 |
| 5 工作流配置化 | Week 9-10 | YAML 解析器, Agent 注册表, 工作流验证器 |
| 6 可观测性 | Week 11-12 | Rich CLI, 结构化日志, debug 模式, 成本追踪 |
| 7 高级进化 | Week 13+ | Prompt 自动优化, 多项目隔离 |
| 8 Agent SDK | Week 13-14 | claude-agent-sdk 集成, 异步 Agent, 工具调用, 轨迹增强 |

---

---

## 执行进度总览

| Phase | 名称 | 状态 | 完成度 |
|-------|------|------|--------|
| 1 | 基础编排 | **已完成** | 9/9 |
| 2 | 记忆系统 | **已移除** | 0/7 |
| 3 | 轨迹记录与评分 | **已完成** | 7/7 |
| 4 | 模式提取与注入 | **基本完成** | 6/7（整理机制待实现） |
| 5 | 工作流配置化 | **已完成** | 7/7 |
| 6 | 可观测性与 CLI | **基本完成** | 5/6（checkpoint resume 待实现） |
| 7 | 高级进化 | **已完成** | 2/2（optimizer + 多项目） |
| 8 | Agent SDK 集成 | **已完成** | 6/6（SDK 替换、异步化、工具调用、轨迹增强、进化层适配、配置扩展） |

### 已交付能力清单

- LangGraph 图引擎：pipeline / DAG / loop / conditional 四种模式
- 4 个内置 Agent：Planner、Coder、Tester、Reviewer（基于 claude-agent-sdk）
- Agent 工具调用：Read、Write、Edit、Bash、Glob、Grep 等内置工具，按角色配置
- YAML 工作流配置：3 个预置模板 + 自定义 agent 注册 + 工具/权限配置
- 轨迹系统：自动记录 + SQLite 持久化 + 规则评分 + token/cost/工具调用追踪
- 模式进化：高分轨迹提取策略 → SQLite 存储
- Prompt 自动优化：分析失败轨迹 → SDK 生成 patch → 审核 apply
- CLI：`evo run|list|validate|trajectories|patterns|optimize|projects`
- Rich 终端输出 + debug 模式
- 多项目数据隔离

### 待补充项

| 项目 | 来源 | 优先级 |
|------|------|--------|
| 模式整理（consolidate.py） | Phase 4.6 | 低（数据量不足时不需要） |
| Checkpoint resume | Phase 6.5 | 中 |

### 备注
- 本版本暂不考虑记忆能力，相关实现已移除

---

## 后续里程碑

### Phase 8: Agent SDK 集成（已完成）

**目标**：用 claude-agent-sdk 替换 LangChain Agent 层，获得工具调用、权限控制、token 追踪能力

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 8.1 | SDK 集成 | `agents/base.py` 重写（SDKResponse + invoke_sdk） | **已完成** |
| 8.2 | Agent 异步化 | planner/coder/tester 改为 async + SDK 调用 | **已完成** |
| 8.3 | 编排异步化 | engine.py / parser.py 节点异步，graph.ainvoke() | **已完成** |
| 8.4 | 工具配置 | YAML 扩展 allowed_tools / permission_mode / max_turns | **已完成** |
| 8.5 | 轨迹增强 | TrajectoryStep 新增 tokens/cost/tool_calls | **已完成** |
| 8.6 | 进化层适配 | optimizer 用 SDK 替换 LangChain | **已完成** |

**技术栈变化**：
- Agent 执行层：LangChain ChatAnthropic → claude-agent-sdk
- 调用方式：同步 invoke_llm() → 异步 invoke_sdk()
- 工具能力：纯文本输出 → Read/Write/Edit/Bash/Glob/Grep 等内置工具
- 权限控制：无 → permission_mode（bypassPermissions / acceptEdits / plan）
- 轨迹数据：tokens=0 → 真实 token/cost/工具调用追踪

---

### Phase 9: 并行执行与 DAG 增强（Week 15-16）

**目标**：支持真正的并行节点执行和复杂 DAG 编排

| # | 任务 | 产出 | 验收标准 |
|---|------|------|----------|
| 9.1 | 并行节点 | engine.py 改造 | Fan-out: 多节点同时执行 |
| 9.2 | 汇聚节点 | state.py 改造 | Fan-in: 等待所有并行分支完成后合并 |
| 9.3 | 子工作流 | parser.py 改造 | 工作流可嵌套调用其他工作流 |
| 9.4 | 动态分支 | router.py 改造 | 运行时根据输出决定下一步走哪些分支 |
| 9.5 | 超时与取消 | engine.py | 单节点超时 + 整体超时 + 优雅取消 |
| 9.6 | 并行度控制 | config | max_parallel 限制同时执行的节点数 |

**交付物**：
```yaml
workflows:
  parallel_dev:
    nodes:
      - name: plan
        agent: planner
      - name: frontend
        agent: coder
        config: {focus: "frontend"}
      - name: backend
        agent: coder
        config: {focus: "backend"}
      - name: integrate
        agent: reviewer
    edges:
      - from: plan
        to: [frontend, backend]   # fan-out
      - from: [frontend, backend]
        to: integrate              # fan-in
```

---

### Phase 10: 多 Agent 协作协议（Week 17-18）

**目标**：Agent 之间能直接通信、协商、委托任务

| # | 任务 | 产出 | 验收标准 |
|---|------|------|----------|
| 10.1 | 消息总线 | `src/evo/comms/bus.py` | Agent 间异步消息传递 |
| 10.2 | 协作协议 | `src/evo/comms/protocol.py` | request/response/delegate/report |
| 10.3 | 任务委托 | agent 改造 | Agent 可将子任务委托给其他 agent |
| 10.4 | 共识机制 | `src/evo/comms/consensus.py` | 多 agent 投票决策（如 code review） |
| 10.5 | 对话历史 | 消息总线扩展 | 记录 agent 间对话，供后续分析 |
| 10.6 | 协作模式模板 | YAML 配置 | pair-programming / review-board / debate |

**交付物**：
```bash
evo run --task "设计并实现支付模块" --workflow collaborative_dev
# Planner 制定方案 → Coder 实现 → Reviewer 提出修改 →
# Coder 收到反馈后修改 → Reviewer 再审 → 通过
# 全程 agent 间有结构化对话记录
```

---

### Phase 11: 进化层 v2 — 自适应编排（Week 19-22）

**目标**：系统根据历史数据自动优化编排策略本身

| # | 任务 | 产出 | 验收标准 |
|---|------|------|----------|
| 11.1 | 编排策略评估 | `src/evo/evolution/meta_judge.py` | 对比不同工作流处理同类任务的效果 |
| 11.2 | 自动工作流推荐 | `src/evo/evolution/recommender.py` | 输入任务描述，推荐最优工作流 |
| 11.3 | 动态节点插入 | engine 改造 | 运行时根据中间结果决定是否插入额外节点 |
| 11.4 | Agent 选择优化 | 路由改造 | 同一角色多个 agent 候选，按历史表现选择 |
| 11.5 | 模式整理 v2 | consolidate.py | 去重/剪枝/合并/降权 完整实现 |
| 11.6 | A/B 测试框架 | `src/evo/evolution/ab_test.py` | 对比两种策略的效果差异 |
| 11.7 | 进化报告 | CLI + Web | 展示系统随时间的改进趋势 |

**交付物**：
```bash
evo run --task "实现搜索功能"
# 系统自动选择最适合的工作流（基于历史数据）
# 运行中发现代码复杂度高，自动插入 reviewer 节点
# 完成后更新策略权重

evo evolution report
# 显示：成功率 72% → 89%（30 天趋势）
# 显示：平均耗时从 45s 降到 28s
# 显示：最有效模式 top-5
```

---

### Phase 12: 插件系统（Week 23-24）

**目标**：第三方可扩展 Agent、Tool、Workflow 模板

| # | 任务 | 产出 | 验收标准 |
|---|------|------|----------|
| 12.1 | 插件接口 | `src/evo/plugins/base.py` | Plugin 基类，声明 agents/tools/workflows |
| 12.2 | 插件加载器 | `src/evo/plugins/loader.py` | 从 pip 包或本地目录加载插件 |
| 12.3 | 插件注册表 | CLI 命令 | `evo plugin list/install/remove` |
| 12.4 | 官方插件包 | 独立 pip 包 | evo-plugin-git / evo-plugin-docker / evo-plugin-db |
| 12.5 | 插件隔离 | 权限系统 | 插件只能访问声明的资源 |

**交付物**：
```bash
pip install evo-plugin-git
evo plugin list
# git: GitAgent + git_commit/git_diff/git_log tools

evo run --task "修复 #123 bug" --workflow git_bugfix
# 自动 git checkout -b fix/123 → 修改代码 → 测试 → commit
```

---

### Phase 13: 生产化（Week 25-28）

**目标**：可部署为服务，支持多用户、API 访问、监控告警

| # | 任务 | 产出 | 验收标准 |
|---|------|------|----------|
| 13.1 | REST API | OpenAPI 规范 | 所有功能可通过 HTTP API 调用 |
| 13.2 | 认证授权 | JWT + RBAC | 多用户隔离，角色权限控制 |
| 13.3 | 任务队列 | Celery / asyncio | 异步执行，支持并发任务 |
| 13.4 | 监控指标 | Prometheus metrics | 成功率/延迟/token 消耗/队列深度 |
| 13.5 | 告警 | webhook / email | 连续失败、成本超限、异常模式 |
| 13.6 | Docker 部署 | Dockerfile + compose | 一键部署完整环境 |
| 13.7 | SDK | `evo-sdk` pip 包 | Python SDK 供外部系统集成 |
