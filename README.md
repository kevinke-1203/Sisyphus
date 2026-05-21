# Evo — 自进化多 Agent 编排框架

> 基于 LangGraph + Claude Code CLI + 自建进化层的多智能体编排系统

## 项目愿景

Evo 是一个可配置的多 Agent 编排框架，核心能力：
1. **灵活编排** — 支持线性流水线、DAG、条件分支、循环重试
2. **可观测演化** — 记录执行轨迹，并基于失败轨迹辅助优化 agent prompt

## 系统架构

```
+-----------------------------------------------------------+
|                      Evo Framework                         |
+-----------------------------------------------------------+
|  Orchestration Layer (LangGraph)                          |
|  [Workflow Engine]  [Router]  [Checkpoint Store]           |
+-----------------------------------------------------------+
|  Agent Pool                                               |
|  [Planner]  [Coder]  [Tester]  [Reviewer]  [Custom...]   |
+-----------------------------------------------------------+
|  Evolution Layer (Custom)                                 |
|  [Tracker]  [Prompt Optimizer]                            |
+-----------------------------------------------------------+
```

## 技术选型

| 组件 | 技术 | 用途 |
|------|------|------|
| 编排引擎 | LangGraph >=0.4 | 图执行、状态管理、checkpoint |
| Agent Runtime | Claude Code CLI | Agent 执行、工具调用、权限控制 |
| LLM | Anthropic Claude（兼容 Messages API） | Agent 推理 |
| 存储 | SQLite | 轨迹、prompt patch、checkpoint |
| 语言 | Python 3.11+ | 全栈 |

## 支持的工作流形态

| 形态 | 描述 | 示例 |
|------|------|------|
| Pipeline | 线性流水线 | plan -> code -> test -> review |
| DAG | 有向无环图（并行+汇聚） | plan -> [code, design] -> test |
| Loop | 带退出条件的循环 | code -> test -> fix -> test（直到通过） |
| Conditional | 条件分支 | review -> approve/reject -> merge/fix |
| Human-in-Loop | 人工审批节点 | code -> human_review -> deploy |

## 快速开始

```bash
# 安装
cd evo
pip install -e .

# 确认 Claude Code CLI 可用
claude --version

# 可选：配置模型（编辑 .env）
# 不配置时默认使用 Claude Code 的 sonnet alias

# 运行工作流
python -m evo run --task "写一个计算斐波那契数列的函数"
```

## .env 配置

Evo 会在启动时读取当前项目根目录的 `.env`。系统环境变量优先级更高，`.env` 不会覆盖已经存在的环境变量。

```bash
# Claude Code CLI 模型和供应商配置由 Claude Code 自身管理
# 例如 ~/.claude/settings.json；Evo 不再读取 LLM_MODEL/LLM_BASE_URL/LLM_API_KEY

# 可选：自定义 claude 二进制路径
CLAUDE_CODE_BIN=/path/to/claude

```

## CLI 命令

```bash
# 运行工作流
python -m evo run --task "你的任务"                      # 默认工作流（plan->code->test）
python -m evo run --task "你的任务" --workflow X         # 指定 YAML 定义的工作流
python -m evo run --task "你的任务" --workflow X --config /path/to/workflow.yaml
python -m evo run --task "你的任务" --debug              # 显示完整 prompt 和 LLM 响应
python -m evo run --task "你的任务" --project myapp      # 在指定项目下运行

# 工作流管理
python -m evo list                                       # 列出可用工作流
python -m evo validate                                   # 验证所有工作流配置
python -m evo validate plan_code_test                    # 验证指定工作流

# 查看执行历史
python -m evo trajectories list                          # 列出所有轨迹
python -m evo trajectories show <id>                     # 查看轨迹详情

# Prompt 自动优化
python -m evo optimize status                            # 查看各 agent 的 patch 状态
python -m evo optimize run coder                         # 分析失败并生成改进建议
python -m evo optimize list                              # 列出所有 patch
python -m evo optimize apply <id>                        # 激活一个 patch

# 项目管理
python -m evo projects                                   # 列出所有项目

```

## 完整测试流程

以下步骤验证完整的执行闭环：执行工作流 → 记录轨迹 → 用 dashboard/CLI 审视执行数据。

```bash
# 第 1 步：运行一个任务
python -m evo run --task "写一个判断回文字符串的函数"

# 预期输出：
#   -> plan（生成实现计划）
#   -> code（编写代码）
#   -> test（评估正确性）
#   Result: PASSED

# 第 2 步：检查轨迹是否记录
python -m evo trajectories list

# 第 3 步：查看执行数据
python -m evo trajectories list
```

## 架构

```
src/evo/
├── main.py              # CLI 入口
├── config.py            # 项目隔离
├── orchestrator/        # 编排引擎
│   ├── engine.py        # LangGraph 图执行 + 轨迹追踪
│   ├── state.py         # 工作流状态定义
│   ├── router.py        # 条件路由（重试逻辑）
│   ├── parser.py        # YAML 工作流解析器
│   ├── validator.py     # 工作流配置验证
│   └── display.py       # Rich CLI 格式化输出
├── agents/              # Agent 定义
│   ├── base.py          # BaseAgent 基类 + Claude Code CLI 调用（invoke_agent）
│   ├── registry.py      # Agent 注册表 + 从 YAML 动态创建
│   ├── planner.py       # 规划 Agent（Read/Glob/Grep）
│   ├── coder.py         # 编码 Agent（Read/Write/Edit/Bash/Glob/Grep）
│   └── tester.py        # 测试 Agent（Read/Bash/Glob/Grep）
├── evolution/           # 自进化系统
│   ├── tracker.py       # 轨迹记录 + SQLite 持久化
│   └── optimizer.py     # 基于失败分析的 prompt 自动优化
```

## 自定义工作流

默认会依次查找 `config/workflows.yaml`、`config/workflow.yaml`、`workflows.yaml`、`workflow.yaml` 和 `~/.evo/config` 下的同名文件。也可以用 `--config` 指定外部配置文件或目录，例如：

```bash
python -m evo list --config /path/to/workflow.yaml
python -m evo validate my_workflow --config /path/to/workflow.yaml
python -m evo run --task "你的任务" --workflow my_workflow --config /path/to/workflow.yaml
EVO_WORKFLOW_CONFIG=/path/to/workflow.yaml python -m evo web
```

在 `config/workflows.yaml` 中定义：

```yaml
agents:
  my_agent:
    role: "你的自定义角色"
    system_prompt: "你的指令"
    allowed_tools: [Read, Glob, Grep]
    permission_mode: bypassPermissions
    max_turns: 10

workflows:
  my_workflow:
    description: "这个工作流做什么"
    nodes:
      - name: step1
        agent: my_agent
      - name: step2
        agent: coder
    edges:
      - from: step1
        to: step2
      - from: step2
        to: END
    max_iterations: 3
```

条件边（实现循环）：

```yaml
edges:
  - from: test
    to: END
    condition: "test_passed == true"
  - from: test
    to: code
    condition: "test_passed == false"
```

## 进化机制

```
执行工作流 → 记录轨迹 → 写入 SQLite
                 ↓
          分析失败轨迹并优化 prompt
```

系统通过以下方式持续变强：
1. 每次执行记录为轨迹，包含每步的耗时、成功/失败
2. 可选：基于失败轨迹分析自动优化 agent 的 system_prompt

## 设计原则

1. **配置优于代码** — 新增 agent 和工作流不需要改框架代码
2. **渐进增强** — 进化能力是可选的，关掉也能正常编排
3. **可观测** — 每步执行都有结构化日志，方便调试
4. **经验驱动** — 进化层不猜测，只基于实际执行数据
5. **人在回路** — 进化建议可以被人审核，不自动修改关键流程
6. **最小依赖** — 核心只需 LangGraph + 一个 LLM API Key

## 数据存储

所有数据存储在本地：

```
data/
├── trajectories.db     # 执行历史（SQLite）
├── prompt_patches.db   # Prompt 优化补丁（SQLite）
```

使用 `--project` 按项目隔离数据：
```bash
python -m evo run --task "..." --project my-project
# 数据存储在 data/projects/my-project/
```
