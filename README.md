# KWCode · 天工开物

<div align="center">

**中国开发者的本地 Coding Agent**

*数据不出网 · Windows 打开就能用 · 越用越懂你的项目*

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Mac%20%7C%20Linux-lightgrey.svg)]()
[![Tests](https://img.shields.io/badge/Tests-292%2F292-brightgreen.svg)]()
[![Version](https://img.shields.io/badge/Version-0.9.0-blue.svg)]()

</div>

---

## 为什么做这个

现有 coding agent 框架（Claude Code、Cursor、OpenCode）都是给强 LLM 设计的——靠模型的强推理能力调用工具、自主决策、完成任务。本地开源模型（8B-30B）做不到这些，会出错，会有幻觉。

KWCode 的思路不同：**LLM 只做分类和生成，确定性流水线做决策和验证。** 专家系统承载领域知识，飞轮从使用中自动积累经验。小模型只需要在极小的 context 里做一件明确的事。

### 本地模型跑 Coding Agent 的五个核心痛点

**痛点一：上下文爆炸**

小模型窗口只有 8K-32K。对话几轮后 context 塞满，模型开始胡说。

> KWCode 解法：**纯算法上下文压缩**（头尾保留 + 中间关键词提取，<10ms），自动在 context 快满时压缩历史对话。

**痛点二：错误重复**

小模型修 bug 失败后，用同样的方式再试一遍，三次机会全浪费在同一个错误上。

> KWCode 解法：**三阶段重试 + Reflection + Debug Subagent**——第一次正常描述，第二次从错误信息出发（注入运行时调试数据），第三次最小化修改。每次重试前先做 Reflection（LLM 分析上次为什么失败）+ Debug Subagent（sys.settrace 捕获真实变量值），绝不重复同样的错。

**痛点三：不能调用工具**

大部分本地 agent 框架只能生成代码文本，不能真正执行命令、读写文件、跑测试。

> KWCode 解法：**内置 5 个确定性工具**（read_file / write_file / run_bash / list_dir / git），Generator 生成 patch 后 Verifier 自动执行语法检查 + pytest，失败立即重试。

**痛点四：数据安全**

Claude Code、Cursor 把代码发到海外服务器。公司代码、内网项目走不通。

> KWCode 解法：**全部本地运行**，代码不出你的电脑。模型跑在本地，搜索跑在本地 SearXNG，统计数据存本地 SQLite。零网络依赖（搜索增强可选）。

**痛点五：代码定位靠猜**

现有工具把文件列表丢给 LLM 让它猜哪个文件相关。小模型猜错文件，后面全错。

> KWCode 解法：**BM25 + AST 调用图**两阶段定位，毫秒级，不调 LLM。沿调用链追踪隐藏依赖，不靠猜。

---

## 核心技术原理

### 原理一：确定性专家流水线

**理论来源**：Agentless（ICSE 2025）——确定性流水线在 SWE-bench 上同时达到最高通过率和最低成本，优于复杂 agent 架构。

```
用户输入
  └─► Gate          任务分类，毫秒级路由
        └─► Locator     精准定位文件和函数（BM25+调用图，不调LLM）
              └─► Generator  只生成修改部分（从文件读original，LLM只写modified）
                    └─► Verifier   语法检查 + pytest 自动验证
                          └─► DebugSubagent  失败时捕获运行时变量值
                                └─► SearchAugmentor  搜索补充信息
```

### 原理二：BM25 + AST 调用图定位

**理论来源**：
- CodeCompass（arXiv:2602.20048，2026）：图遍历 G3 任务准确率 **99.4%** vs BM25 **76.2%**
- KGCompass（arXiv:2503.21710，2025）：SWE-bench Lite 成功率 **58.3%**

**两阶段检索**：

```
用户描述 "修复登录失败的 bug"
  ├─► 阶段1：BM25 关键词召回（毫秒级，不调 LLM）
  └─► 阶段2：AST 调用图展开（毫秒级，不调 LLM）
        沿调用链追踪隐藏依赖
```

技术实现：`tree-sitter` 多语言 AST + `rank-bm25` + `SQLite` 调用图持久化。

### 原理三：Debug Subagent（运行时调试）

**理论来源**：Debug2Fix（Microsoft，ICML 2026）——弱模型 + 交互式调试器 > 强模型裸跑。GPT-5 + Debug2Fix 匹配 Claude Sonnet 基线性能。

```
Verifier 失败
  └─► LLM 决定断点位置和要检查的变量
        └─► sys.settrace 非侵入式捕获运行时变量值
              └─► 真实调试数据注入下一轮 Generator retry
```

不用交互式 PDB，不修改源文件。用 `sys.settrace` 在目标行捕获变量值，失败时 fallback 到 `pytest --tb=long` 获取完整堆栈。

### 原理四：DAG 多任务编排

**理论来源**：LLMCompiler（ICML 2024）——任务分解 + 并行调度显著提升复杂任务完成率。

KWCode 实现了轻量 DAG 调度器（零新依赖，ThreadPoolExecutor + Kahn 拓扑排序）：

```
/multi task1 ; task2 ; task3          ← 全部并行
/multi task1 -> task2 -> task3        ← 串行链
/multi                                ← 交互式混合
  + 给函数add加注释                     (并行)
  + 给函数sub加注释                     (并行)
  + >给修改后的代码写测试                (串行，依赖上面两个)
```

### 原理五：专家飞轮 + Prompt 自动优化

**理论来源**：
- EE-MCP（NeurIPS 2025）——从任务轨迹自动提取经验
- SICA（arXiv:2504.15228）——自我改进编码代理
- Reflexion——失败模式持久化

```
使用过程中，飞轮在后台静默积累：
  同类任务成功 ≥5 次 → 触发专家草稿生成
  回测门：新专家成功率 ≥ 原流水线
  AB 测试门：10 次真实对比，提升 >10%
  三道门全过 → 专家正式投产

Prompt Optimizer（可选，需 Anthropic API key）：
  分析成功轨迹 → 生成经验规则 → 追加到专家 YAML system_prompt
  下次同类任务 Generator 拿到更准确的领域知识
```

### 原理六：Reflexion 持久化

失败不白费。每次任务完成后（成功/失败），结构化记录到 `REFLECTION.md`：

```
## bugfix 失败模式
- [2026-04-29] JWT验证失败 → 根因：token过期时间单位错误

## codegen 注意事项
- [2026-04-29] FastAPI路由 → 注意：必须include_router
```

`/plan` 时自动读取历史 Reflection 作为风险提示，避免重蹈覆辙。

### 原理七：模型能力自适应

| 模型规模 | 自动策略 |
|---------|---------|
| <10B（qwen3:8b） | 强制计划确认 · 任务范围≤2文件 · 第1次失败触发搜索 |
| 10-30B（qwen3:14b） | 可选计划 · 任务范围≤4文件 · 第2次失败触发搜索 |
| >30B（qwen3:72b） | 宽松策略 · 任务范围≤8文件 · 自动处理复杂任务 |

---

## 功能特性

### 代码能力
- BM25 + 调用图两阶段定位，G3 隐藏依赖准确率 99.4%（论文验证）
- Generator 只改必要部分，从文件读 original，LLM 只生成 modified
- 三阶段重试 + Reflection + Debug Subagent，不重复同样的错
- Cross-Encoder 搜索结果重排（可选，`pip install kwcode[rerank]`）

### 多任务执行
- `/multi` 命令：串行（依赖链）+ 并行（独立任务）混合执行
- DAG 拓扑排序 + ThreadPoolExecutor 并行调度
- 依赖上下文自动注入：前置任务结果传递给后续任务

### 流程控制
- `/plan 计划模式`：显示执行步骤+风险等级（High/Medium/Low），确认后才动文件
- `Checkpoint 快照`：任务开始前自动备份，失败一键还原
- `KWCODE.md 项目规则`：写项目约定，按任务类型分段注入

### 知识积累
- 三层记忆：PROJECT.md / EXPERT.md / PATTERN.md
- Reflexion 持久化：REFLECTION.md 结构化记录失败模式和注意事项
- 非代码文件读取：PDF / Word / MD，BM25 匹配相关段落注入

### 搜索增强
- 默认 DuckDuckGo（零配置）
- 可选 SearXNG 自部署：`kwcode setup-search` 一键安装
- 四级内容提取 + BM25 重排 + Cross-Encoder 精排
- 意图感知：代码/论文/包/debug 自动优化搜索词

### Office 文档
- Excel / PPT / Word 生成

### 价值可见
- `kwcode stats`：完成任务数、节省时间估算
- 飞轮通知：专家投产时弹出
- 里程碑提醒：完成 50/100/200 个任务时自动汇报

### 中国本地化

| 场景 | CC / Hermes | KWCode |
|------|------------|--------|
| Windows 运行 | 仅 WSL2 / 云端 | cmd/PowerShell 原生 |
| 搜索增强 | DDG/Brave（被墙） | SearXNG 自部署 / DDG fallback |
| 推荐模型 | GPT / Claude | DeepSeek · Qwen3 · GLM |
| 中文交互 | 英文为主 | 全中文 |

---

## 与竞品对比

| 功能 | Claude Code | Hermes | KWCode |
|------|------------|--------|--------|
| 数据安全 | ❌ 代码上传云端 | ✅ 本地 | ✅ 本地 |
| Windows 原生 | ✅ | ❌ 仅 WSL2 | ✅ |
| 小模型专家流水线 | ❌ | ❌ | ✅ 独有 |
| 运行时调试（Debug Subagent） | ❌ | ❌ | ✅ 独有 |
| 多任务串并行 | ❌ | ❌ | ✅ 独有 |
| AST 调用图定位 | ❌ | ❌ | ✅ 独有 |
| 专家飞轮 + Prompt 自动优化 | ❌ | ❌ | ✅ 独有 |
| /plan 风险评估 | ✅ | ❌ | ✅ |
| Checkpoint 回滚 | ✅ | ❌ | ✅ |
| 价值量化仪表盘 | ❌ | ❌ | ✅ 独有 |
| 开源 | ❌ | ✅ MIT | ✅ MIT |

---

## 快速开始

### 系统要求

- Python 3.10+
- 任意 OpenAI 兼容 API（本地模型 / DeepSeek / 硅基流动 / Qwen 云端 等）
- Docker（可选，用于 SearXNG 搜索增强）

| 使用方式 | 说明 |
|---------|------|
| 本地模型 | 安装本地推理引擎，拉取 qwen3:8b 等模型 |
| 云端 API | `/api default https://api.deepseek.com your-key`，无需本地显卡 |

本地模型显存参考：

| 显存 | 推荐模型 |
|------|---------|
| 4GB | gemma3:4b |
| 8GB | qwen3:8b |
| 16GB | qwen3:14b |
| 24GB+ | qwen3:30b-a3b |

### 安装

```bash
# 安装 KWCode
pip install kwcode

# 国内加速：
pip install kwcode -i https://pypi.tuna.tsinghua.edu.cn/simple

# 可选：Cross-Encoder 搜索重排
pip install kwcode[rerank]

# 启动
kwcode
```

首次启动会引导你配置模型连接，按提示操作即可。

用云端 API 不需要本地显卡：
```
/api default https://api.deepseek.com your-api-key
```

### 可选：安装搜索增强

```bash
kwcode setup-search
```

需要 Docker Desktop 已安装并运行。会自动拉取 SearXNG 镜像并启动容器。不装也能用，默认走 DuckDuckGo 搜索。

---

## 使用指南

### 交互模式（推荐）

```bash
kwcode
```

进入 REPL，直接输入任务描述：

```
 > 修复登录验证失败的问题
 > 写一个 FastAPI 登录接口，包含 JWT 认证
 > 把 calculate_price 拆成更小的函数
```

### 多任务模式

```
 > /multi 给函数add加注释 ; 给函数sub加注释 ; 给函数mul加注释
```

三个任务并行执行。用 `->` 表示串行依赖：

```
 > /multi 重构extract_data函数 -> 给新函数写测试
```

交互式输入（`>` 前缀表示依赖前面的任务）：

```
 > /multi
  + 给函数add加注释
  + 给函数sub加注释
  + >给修改后的代码写测试
```

### 单次执行

```bash
kwcode "修复登录验证失败的问题"
kwcode --plan "重构数据库连接层"
```

### REPL 命令

```
/plan <任务>          计划模式，显示步骤和风险后再执行
/multi               多任务模式（串行+并行）
/model qwen3:14b     切换模型
/api                 API 配置
/experts             查看已注册专家
/memory              查看项目记忆
/init                初始化项目规则文件
/cd <路径>           切换项目目录
/help                显示帮助
```

### 接入任意 API

KWCode 支持任何 OpenAI 兼容的 API，包括 DeepSeek、Qwen 云端、硅基流动、零一万物、Groq 等。

```
/api temp https://api.deepseek.com your-api-key      # 临时切换
/api default https://api.deepseek.com your-api-key   # 永久保存
/api show                                            # 查看当前配置
```

### 项目规则文件

在项目根目录创建 `KWCODE.md`，写入你的项目约定：

```markdown
## [all] 通用规则
- 测试框架：pytest
- 运行测试：pytest tests/ -v

## [bugfix] Bug修复规则
- 修复前先理解错误原因
- 不要改测试代码

## [codegen] 代码生成规则
- 变量命名用 snake_case
- 必须写 docstring
```

KWCode 启动时自动加载，按任务类型注入对应规则。

---

## 开发者安装

```bash
git clone https://github.com/val1813/kwcode.git
cd kwcode
pip install -e ".[dev]"
python -m pytest kaiwu/tests/ -v --ignore=kaiwu/tests/bench_tasks
# 292 tests should pass
```

### 项目结构

```
kaiwu/
├── cli/main.py              # CLI 入口，REPL，/multi 命令
├── core/
│   ├── gate.py              # LLM 任务分类
│   ├── orchestrator.py      # 确定性流水线编排
│   ├── task_compiler.py     # DAG 多任务调度器
│   ├── planner.py           # /plan 计划模式 + 风险评估
│   ├── checkpoint.py        # 文件快照
│   └── model_capability.py  # 模型能力自适应
├── experts/
│   ├── locator.py           # BM25 + 调用图定位
│   ├── generator.py         # 代码生成（只改必要部分）
│   ├── verifier.py          # 语法检查 + pytest
│   ├── debug_subagent.py    # 运行时调试（sys.settrace）
│   └── search_augmentor.py  # 搜索增强 + BM25 + CE 重排
├── search/
│   ├── reranker.py          # Cross-Encoder 可选重排
│   ├── duckduckgo.py        # SearXNG + DDG 并行搜索
│   └── intent_classifier.py # 意图感知分类
├── flywheel/
│   ├── trajectory_collector.py  # 轨迹记录
│   ├── pattern_detector.py      # 模式检测（Gate 1）
│   ├── ab_tester.py             # 三道门验证
│   └── prompt_optimizer.py      # YAML system_prompt 自动优化
├── memory/
│   └── pattern_md.py        # PATTERN.md + REFLECTION.md
├── registry/                # 15 个预置 YAML 专家
├── ast_engine/              # tree-sitter AST + 调用图
└── stats/                   # 价值量化（SQLite）
```

---

## 参考文献

| 论文 | 会议 | KWCode 中的应用 |
|------|------|----------------|
| Agentless | ICSE 2025 | 确定性流水线优于复杂 agent |
| CodeCompass | arXiv:2602.20048, 2026 | 图遍历 G3 任务 99.4% |
| KGCompass | arXiv:2503.21710, 2025 | 多跳图遍历定位 |
| Debug2Fix | ICML 2026 | 弱模型+调试器 > 强模型裸跑 |
| LLMCompiler | ICML 2024 | DAG 任务分解+并行调度 |
| EE-MCP | NeurIPS 2025 | 任务轨迹经验提取 |
| SICA | arXiv:2504.15228 | 自我改进编码代理 |
| AgentCoder | EMNLP 2023 | 多专家分工验证 |
| Reflexion | NeurIPS 2023 | 失败模式持久化 |

---

## 参与贡献

**KWCode 是中国开发者做的，也需要中国开发者一起来完善。**

### 最需要的贡献

**新增预置专家**（最简单，编辑一个 YAML 文件）：

```yaml
# 急需认领：
Vue3Expert / DjangoExpert / GoGinExpert / RustActixExpert
K8sExpert / DockerExpert / RedisExpert / MySQLExpert
```

```bash
kwcode expert create MyExpert  # 生成模板
# 编辑 ~/.kaiwu/experts/MyExpert.yaml
# 提交 PR
```

**其他贡献方向**：
- 多语言 AST 支持（JavaScript/TypeScript/Java/Go）
- bench_tasks 补齐（bugfix 类、跨文件类）
- 文档翻译（英文 README）

---

## License

MIT
