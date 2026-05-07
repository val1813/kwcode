# KWCode · 天工开物

<div align="center">

**中国开发者的本地 Coding Agent**

*数据不出网 · Windows 打开就能用 · 越用越懂你的项目*

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Mac%20%7C%20Linux-lightgrey.svg)]()
[![Multi-Platform Tests](https://github.com/val1813/kwcode/actions/workflows/test.yml/badge.svg)](https://github.com/val1813/kwcode/actions/workflows/test.yml)
[![Version](https://img.shields.io/badge/Version-1.6.1-blue.svg)]()

</div>

---

> **v1.6.1 已发布！** 架构收敛：删除冗余Expert类，纯确定性机制驱动pipeline。安装命令：
>
> ```bash
> pip install kwcode
> # 国内加速
> pip install kwcode -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```

---

## 更新日志

| 日期 | 内容 |
|------|------|
| 05-07 | **v1.6.1** 架构收敛：删除WholeFileImplExpert/DependencyFixExpert，纯确定性机制驱动pipeline。Generator增强(upstream_constraints注入system prompt + retry_hint携带上次代码 + tier=small填空框架)。License改为Apache-2.0。513 tests green |
| 05-07 | **v1.6.0** MoE确定性架构：GapDetector(11种GapType,零LLM) + ExecutionStateTracker(回归检测) + EnvProber(工具链/依赖自动修复) + Gate确定性优先路由 + 63个专项诊断测试 |
| 05-06 | **v1.5.1** Hashline锚点编辑(61%输出token减少) + AdaptThink自适应推理(easy→off/hard→4096) + Fast/Slow双阶段(首次fast失败升级slow) + 三飞轮(策略统计/用户模式/SKILL提炼) + 匿名遥测(opt-in, HMAC防护) + 审计日志(`kwcode log`) + model命令 + model_capability全量接入(tier检测→ctx自适应→prompt约束) + 缩进对齐修复 |
| 05-06 | **v1.5.0** SearchSubagent隔离搜索(独立context窗口+并行读取) + UpstreamManifest跨文件契约(AST提取签名/常量，零LLM) + PENCIL式context压缩 + Verifier跨文件一致性检查 + orchestrator run()拆分(410→253行) + CLI拆分(1861→173行) + ruff/mypy配置 |
| 05-06 | **v1.4.0** 多语言AST支持(Go/TS/Rust/Java + ast-grep预定义模板) + FastAPI Server(SSE端口7355) + Textual TUI(`kwcode --tui`) + VSCode插件(薄客户端) + 多语言Verifier(jest/go test/cargo test/mvn test) |
| 05-06 | **v1.3.0** EventBus事件总线 + ToolGateway权限隔离 + 错误策略路由(按error_type切换重试序列) + 认知门控(patch行数递减检测) + 3层渐进压缩 + Plan自动触发 + Worktree并行隔离 + Speculative Prefetch + SearchRouter意图感知搜索(arxiv/S2/GitHub/PyPI/Open-Meteo零key) + Wink自修复监控 |
| 05-06 | **v1.1.0** 熔断器+智能重试(syntax/import快速熔断+scope缩小) + Gate置信度 + Verifier结构化错误 + Experience Replay(BM25历史轨迹) + Session多轮连贯 + Locator精准裁剪 |
| 04-30 | 三层上下文架构 + SSH持久会话 + Gate/路由优化 + PCED-Lite多源聚合 + 搜索site:自动限定 + qwen3:8b 20题真实验证 + 13项bug修复 |
| 04-29 | 5元专家体系定稿 + 15个SKILL.md渐进加载 + DAG多任务编排 + Debug Subagent + Token预算/Guardrails/可观测性 |

详细变更见 [CHANGELOG.md](CHANGELOG.md)

---

## 为什么做这个

现有 coding agent 框架（Claude Code、Cursor、OpenCode）都是给强 LLM 设计的——靠模型的强推理能力调用工具、自主决策、完成任务。本地开源模型（8B-30B）做不到这些，会出错，会有幻觉。

KWCode 的思路不同：**LLM 只做分类和生成，确定性流水线做决策和验证。** 专家系统承载领域知识，飞轮从使用中自动积累经验。小模型只需要在极小的 context 里做一件明确的事。

### 本地模型跑 Coding Agent 的五个核心痛点

**痛点一：上下文爆炸**

小模型窗口只有 8K-32K。对话几轮后 context 塞满，模型开始胡说。

> KWCode 解法：**纯算法上下文压缩**（3层渐进压缩：70%裁剪tool冗余→85%压缩中间轮次→95%摘要化早期对话，<10ms），自动在 context 快满时分级压缩历史对话。

**痛点二：错误重复**

小模型修 bug 失败后，用同样的方式再试一遍，三次机会全浪费在同一个错误上。

> KWCode 解法：**错误策略路由 + 认知门控 + Wink自修复**——按error_type切换重试序列（syntax→熔断/import→确定性修复/runtime→先debug/patch_apply→重新定位）；patch行数递减检测边际收益递减自动停止；Wink监控偏离行为注入课程纠正。

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

### 原理一：确定性专家流水线 + 5 元专家体系

**理论来源**：
- Agentless（ICSE 2025）——确定性流水线优于复杂 agent
- GitHub Copilot Atomic Skills（2025）——5 原子能力组合出所有复杂任务
- MoE Routing Geometry（arXiv:2604.09780）——专家按能力分，不按领域分

KWCode 的 5 个元专家（原子能力层，固定不变）：

```
用户输入
  └─► Gate          任务分类，毫秒级路由 + 领域知识匹配
        └─► Locator     精准定位文件和函数（BM25+调用图，不调LLM）
              └─► Generator  只生成修改部分 + 领域知识注入（SKILL.md Level 2）
                    └─► Verifier   语法检查 + pytest 自动验证
                          └─► Debugger   失败时捕获运行时变量值（sys.settrace）
                                └─► Reviewer   需求对齐审查（LLM对比意图vs变更）
```

15 个领域知识（SKILL.md 注入层，可扩展，不改变流水线）：
BugFix · FastAPI · TestGen · API · DeepSeekAPI · Docstring · MyBatis · Office(3) · Refactor · SpringBoot · SQLOpt · TypeHint · UniApp · **Golang · TypeScript · Rust · Java**

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

技术实现：`tree-sitter` 多语言 AST + `ast-grep` 预定义模板查询 + `rank-bm25` + `SQLite` 调用图持久化。支持 Python/JavaScript/TypeScript/Go/Rust/Java（可选依赖 `pip install kwcode[multilang]`）。

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

### 原理八：SearchSubagent 隔离搜索 + 跨文件契约（v1.5.0）

**理论来源**：WarpGrep（隔离搜索子代理减少context rot 70%）；CGM（图结构注入注意力，Qwen2.5-72B达43% SWE-bench Lite）；PENCIL（生成后擦除中间状态）；SWE-ContextBench（上下文质量 > 模型参数量）

```
SearchSubagent（独立context窗口）：
  搜索中间状态永远不进入Generator工作记忆
  并行文件读取：ThreadPoolExecutor，8个并发
  只返回精确结果：{file, start_line, end_line, content}

UpstreamManifest（跨文件契约追踪）：
  确定性AST提取：函数签名 + 常量 + import依赖
  Generator prompt注入跨文件约束，模型不用猜接口
  Verifier前置检查：参数数量/常量一致性（零LLM）

PENCIL式压缩：
  子任务完成 → 只保留签名/常量/文件路径/测试状态
  下游子任务拿到结构化摘要，不是完整推理链
```

### 原理九：EventBus 统一事件系统（v1.3.0）

**理论来源**：Event Sourcing（Martin Fowler）；CC 27 个 hook 事件（arXiv:2604.14228）；Codified Context append-only 日志（arXiv:2602.20478）

```
所有模块通过 EventBus 发射事件：
  专家层 → emit("reading_file", {path}) → CLI 追加式渲染
  重试层 → emit("circuit_break", {reason}) → 用户可见
  搜索层 → emit("search_solution", {msg}) → 实时反馈

append-only 日志支持 replay/时间旅行调试
```

### 原理十：错误策略路由 + 认知门控（v1.3.0）

**理论来源**：Turn-Control Strategies（arXiv:2510.16786）动态预算比固定预算好 12-24%；SpecEyes（arXiv:2603.23483）认知门控

```
错误类型 → 专用重试序列：
  syntax    → [generator, verifier]（1次后熔断）
  import    → [import_fixer, verifier]（确定性修复，不调LLM）
  runtime   → [debugger, generator, verifier]（先debug再修）
  patch_apply → [locator, generator, verifier]（重新定位）
  assertion → [generator, verifier]（2次后搜索）
  contract_violation → [locator, generator, verifier]（跨文件契约冲突）

认知门控：patch行数持续递减 → 边际收益递减 → 自动停止
Wink监控：scope_creep/repetitive_fix/patch_miss → 注入纠正hint
```

### 原理十一：ToolGateway 权限隔离（v1.3.0）

**理论来源**：CC 工具沙箱隔离（arXiv:2604.14228）；deny-first 权限模型

```
专家层（只做生成，输出 patch 结构）
  ↓
ToolGateway（权限白名单 + 文件缓存 + 脏标记 + 事件emit）
  ↓
executor.py（实际执行 read_file / write_file / run_bash）

每个专家只能调用白名单内的工具：
  locator:   [read_file, list_dir]
  generator: [read_file]（只读，不写）
  verifier:  [apply_patch, write_file, run_bash]
```

---

## 功能特性

### 代码能力
- BM25 + 调用图两阶段定位，G3 隐藏依赖准确率 99.4%（论文验证）
- Generator 只改必要部分，从文件读 original，LLM 只生成 modified
- 错误策略路由：按 error_type 切换重试序列，不重复同样的错
- 认知门控：patch 行数递减检测边际收益递减，自动停止无效重试
- Wink 自修复：检测 scope creep / 原地打转 / patch 失败，注入纠正
- Speculative Prefetch：Locator 完成后后台预读文件，减少 Generator IO 等待
- Cross-Encoder 搜索结果重排（可选，`pip install kwcode[rerank]`）

### 多任务执行
- `/multi` 命令：串行（依赖链）+ 并行（独立任务）混合执行
- DAG 拓扑排序 + ThreadPoolExecutor 并行调度
- Worktree 隔离：并行任务在独立工作目录执行，避免文件冲突
- 依赖上下文自动注入：前置任务结果传递给后续任务

### 流程控制
- `/plan 计划模式`：显示执行步骤+风险等级（High/Medium/Low），确认后才动文件
- `Plan 自动触发`：hard 任务自动生成执行计划，不打断用户
- `Checkpoint 快照`：任务开始前自动备份，失败一键还原
- `KWCODE.md 项目规则`：写项目约定，按任务类型分段注入

### 知识积累
- 三层记忆：PROJECT.md / EXPERT.md / PATTERN.md
- Reflexion 持久化：REFLECTION.md 结构化记录失败模式和注意事项
- 非代码文件读取：PDF / Word / MD，BM25 匹配相关段落注入

### 搜索增强
- SearchRouter 意图感知路由：按任务类型选最精准搜索源
- 零 key 默认可用：arXiv API / Semantic Scholar / GitHub REST / PyPI JSON / Open-Meteo
- 错误驱动搜索：按失败类型精准触发（import→立刻搜/runtime→debug后搜/assertion→2次后搜）
- 可选 SearXNG 自部署：`kwcode setup-search` 一键安装
- 可选 Tavily key：通用搜索质量提升（1000次/月免费）
- 四级内容提取 + BM25 重排 + Cross-Encoder 精排

### Office 文档
- Excel / PPT / Word 生成

### 多模态图片处理
- `/paste` 命令：从剪贴板粘贴图片
- `/image <path>` 命令：添加图片文件
- 图片分析：代码截图、UI设计图、文档表格
- 基于图片的代码生成：UI截图→HTML/CSS、错误截图→修复代码
- 支持格式：PNG、JPG、JPEG、GIF、WebP、BMP
- 安装：`pip install kwcode[multimodal]`

**Vision API 配置**（使用前必配）：

```bash
# 设置环境变量（支持任何兼容 Anthropic Messages API 的服务）
export KWCODE_VISION_API_URL="https://your-provider.com/v1/messages"
export KWCODE_VISION_API_KEY="your-api-key"
export KWCODE_VISION_MODEL="your-multimodal-model"
```

支持的 Vision 模型示例：
- OpenAI: `gpt-4o` (需通过代理转换为 Anthropic Messages API 格式)
- Anthropic: `claude-sonnet-4-20250514` (endpoint: `https://api.anthropic.com/v1/messages`)
- 小米 MiMo: `mimo-v2-omni` (Anthropic 格式代理)
- 本地模型: Ollama 多模态模型（通过 Ollama 兼容 endpoint）

> **注意**：模型必须支持图片输入（多模态），纯文本模型无法处理图片任务。

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

**macOS 用户注意**：Apple Silicon (M1/M2/M3/M4) 使用统一内存架构，无需单独显卡显存。推荐使用 [Ollama](https://ollama.com) 本地运行模型，原生支持 Apple Silicon。

### 安装

#### macOS 安装

```bash
# 1. 安装 Python（如果尚未安装）
brew install python@3.12

# 2. 安装 KWCode
pip3 install kwcode

# 国内加速：
pip3 install kwcode -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. （推荐）安装 Ollama 用于本地模型
brew install ollama
ollama pull qwen3:8b

# 4. 启动
kwcode
```

**Apple Silicon 兼容性**：KWCode 完全支持 Apple Silicon (M1/M2/M3/M4) Mac。GPU 信息显示为 "Apple Silicon GPU"，系统会自动检测统一内存。

#### Windows / Linux 安装

```bash
# 安装 KWCode
pip install kwcode

# 国内加速：
pip install kwcode -i https://pypi.tuna.tsinghua.edu.cn/simple

# 可选：Cross-Encoder 搜索重排
pip install kwcode[rerank]

# 可选：多语言 AST 支持（Go/TS/Rust/Java）
pip install kwcode[multilang]

# 可选：TUI 界面
pip install kwcode[tui]

# 可选：HTTP Server（供 VSCode 插件连接）
pip install kwcode[server]

# 全部安装
pip install kwcode[full]

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
kwcode --tui                          # TUI 界面
kwcode serve                          # 启动 HTTP server（端口 7355）
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
/paste               从剪贴板粘贴图片
/image <路径>        添加图片文件
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
# 424 tests should pass
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
│   ├── locator.py           # [元专家] BM25 + 调用图定位
│   ├── generator.py         # [元专家] 代码生成（只改必要部分）
│   ├── verifier.py          # [元专家] 多语言语法检查 + 测试
│   ├── debug_subagent.py    # [元专家] 运行时调试（sys.settrace）
│   ├── reviewer.py          # [元专家] 需求对齐审查
│   ├── search_augmentor.py  # 搜索增强 + BM25 + CE 重排
│   └── vision_expert.py     # [元专家] 多模态图片处理
├── server/
│   ├── app.py               # FastAPI + SSE 事件流（端口 7355）
│   ├── pipeline_factory.py  # 共享 pipeline 构建
│   └── models.py            # Pydantic 请求/响应模型
├── tui/
│   └── app.py               # Textual TUI（文件树 + 事件流）
├── ast_engine/
│   ├── parser.py            # tree-sitter 多语言 AST
│   ├── ast_grep_engine.py   # ast-grep 预定义模板查询
│   ├── language_detector.py # 项目语言检测
│   └── graph_builder.py     # SQLite 调用图持久化
├── search/
│   ├── search_router.py     # 意图感知搜索路由
│   ├── duckduckgo.py        # SearXNG + DDG 并行搜索
│   └── intent_classifier.py # 意图感知分类
├── flywheel/
│   ├── trajectory_collector.py  # 轨迹记录
│   ├── pattern_detector.py      # 模式检测（Gate 1）
│   ├── ab_tester.py             # 三道门验证
│   └── prompt_optimizer.py      # SKILL.md 领域知识自动优化
├── memory/
│   └── pattern_md.py        # PATTERN.md + REFLECTION.md
├── builtin_experts/         # 19 个 SKILL.md 领域知识目录
├── registry/                # 专家注册表（加载 SKILL.md）
└── stats/                   # 价值量化（SQLite）

extension/                   # VSCode 插件（薄客户端）
├── src/extension.ts         # 插件入口
├── src/server-client.ts     # SSE 客户端
├── src/panel.ts             # Webview 面板
└── package.json
```

---

## 参考文献

| 论文/项目 | 来源 | KWCode 中的应用 |
|-----------|------|----------------|
| **Agentless** | Xia et al., ICSE 2025 | 确定性流水线优于复杂 agent，KWCode 整体架构基于此思路 |
| **CodeCompass** | arXiv:2602.20048, 2026 | 图遍历 G3 任务 99.4%，KWCode 的 AST 调用图定位直接借鉴 |
| **KGCompass** | Yang et al., arXiv:2503.21710, 2025 | 多跳图遍历定位，验证了调用图展开的有效性 |
| **Debug2Fix** | Garg & Huang (Microsoft), ICML 2026 | 弱模型+调试器 > 强模型裸跑，KWCode 的 Debug Subagent 直接实现此论文思路 |
| **LLMCompiler** | Kim et al., ICML 2024 | DAG 任务分解+并行调度，KWCode 的 TaskCompiler 借鉴其调度思想（自研轻量实现） |
| **EE-MCP** | NeurIPS 2025 | 任务轨迹经验提取，KWCode 飞轮的轨迹→模式→专家生成流程借鉴此机制 |
| **SICA** | arXiv:2504.15228, 2025 | 自我改进编码代理，KWCode 的 Prompt Optimizer 借鉴其自我优化循环 |
| **Self-Play** | arXiv:2502.14948, 2025 | 自博弈提升代码能力，飞轮 AB 测试门的设计参考 |
| **Reflexion** | Shinn et al., NeurIPS 2023 | 失败模式持久化+重试时注入，KWCode 的 REFLECTION.md 直接实现 |
| **AgentCoder** | Huang et al., EMNLP 2023 | 多专家分工验证，KWCode 的 Gate→专家流水线参考此分工模式 |
| **Agent Psychometrics** | arXiv:2604.00594, 2026 | 任务特征预测 agent 成功率，KWCode 的模型能力自适应参考此研究 |
| **TRUSTEE** | 2026 | 8B 模型可靠 tool calling 验证，KWCode 的 Gate 设计参考 |
| **Dive into Claude Code** | arXiv:2604.14228, 2026 | ToolGateway 分层、EventBus 27 事件、5 层压缩管道、Worktree 隔离 |
| **Wink** | arXiv:2602.17037, 2026 | Wink 自修复监控；失败类型分类（Drift/Reasoning/Tool） |
| **ARCS** | arXiv:2504.20434, 2026 | 搜索前置于生成（retrieval-before-generation），按失败类型精准触发 |
| **Speculative Actions** | arXiv:2510.04371 | Speculative Prefetch，下一步预测准确率 55% |
| **SpecEyes** | arXiv:2603.23483 | 认知门控熔断，基于答案可分性检测边际收益递减 |
| **OPENDEV** | arXiv:2603.05344 | 渐进上下文压缩，token 使用率分级触发 |
| **Codified Context** | arXiv:2602.20478 | EventBus append-only 日志，跨 session 三层记忆 |
| **Turn-Control Strategies** | arXiv:2510.16786 | 错误策略路由，动态预算比固定预算好 12-24% |
| **CodeScout** | arXiv:2603.05744 | 问题陈述增强，输入质量是关键瓶颈 |

### 借鉴的开源项目

| 项目 | 借鉴点 |
|------|--------|
| **Claude Code** (Anthropic) | CLAUDE.md 项目规则文件 → KWCode 的 KWCODE.md；Checkpoint 文件快照机制；/plan 计划模式；ToolGateway 权限隔离；EventBus 事件系统 |
| **Hermes** (Anthropic) | REPL 交互模式、MEMORY.md 记忆系统的交互设计 |
| **OpenHands V1** (All Hands AI) | Agent delegation 任务分解思路、Context Condensation 上下文压缩、LLM-based 集成测试回检 |
| **OpenCode** | 本地模型 coding agent 的产品形态参考；早期版本曾作为执行层底座探索 |
| **SearXNG** | 零 API key 的本地搜索引擎，KWCode 集成为搜索后端 |
| **rank-bm25** | BM25Plus 算法实现，用于代码定位和搜索结果重排 |
| **tree-sitter** | 多语言 AST 解析，用于调用图构建 |
| **sentence-transformers** | Cross-Encoder 模型，用于搜索结果精排（可选依赖） |

### 设计决策的来源

以下关键设计决策来自项目早期的架构讨论和实验：

- **不用 ReAct 循环，用确定性流水线**：小模型在 ReAct 循环里容易失控，确定性流水线每步输入输出格式固定，LLM 只在 Generator 出现一次
- **专家是 SKILL.md 知识载体，不是 Python 类**：早期实验过 Python 专家（ExpertBase 继承体系），发现把领域知识和执行逻辑混在一起方向错误，回退到 SKILL.md 渐进式加载
- **不用 LoRA 训练专家**：早期实验证明 LoRA 效果差、换模型要重训，改为 SKILL.md 内容自动进化
- **任务拆分不枚举模板**：参考 OpenHands V1 的 agent delegation，复杂任务让 LLM 一次性输出 DAG JSON，失败退化为单任务
- **专家约束越严格越好**：不是教 8B 模型做什么，是限制它只能在什么范围内做。约束越严格，犯错空间越小

---

## 参与贡献

**KWCode 是中国开发者做的，欢迎贡献代码。** 详细规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 快速开始

```bash
git clone https://github.com/val1813/kwcode.git
cd kwcode
pip install -e ".[dev]"
python -m pytest kaiwu/tests/ -v --ignore=kaiwu/tests/bench_tasks
# 全部绿才能提 PR
```

### 架构红线（违反即拒绝）

| 红线 | 说明 |
|------|------|
| RED-1 | Gate 必须输出结构化 JSON，不得字符串解析 |
| RED-2 | LLM 只做分类和生成，不得让 LLM 决定流水线下一步 |
| RED-3 | 每个专家独立上下文，不继承上一个专家的对话历史 |
| RED-4 | 新增依赖必须离线可用 |
| RED-5 | 重试次数必须有硬上限 |

**一票否决**：向量数据库、企业级安全库、需要云服务的依赖、多 Agent 并行框架、自动修改 Gate 路由规则。

### 最欢迎的贡献

| 类型 | 说明 |
|------|------|
| 新增专家 | 创建 `kaiwu/builtin_experts/<name>/SKILL.md`，最简单的贡献方式 |
| Bug 修复 | 附复现步骤和测试用例 |
| 多语言 AST | JS/TS/Go/Rust/Java 调用图支持（✅ v1.4.0 已实现） |
| 性能优化 | Locator 定位速度、ContextPruner 压缩质量 |

急需认领的专家：Vue3 · React · Django · FastAPI · Go Gin · Rust Actix · K8s · Docker · MySQL · Redis

### PR 要求

1. **测试全绿**：`python -m pytest kaiwu/tests/ --ignore=kaiwu/tests/bench_tasks`
2. **新功能必须有测试**（改 core/experts/flywheel 目录时）
3. **新增依赖需说明**用途和离线可用性
4. **改 Gate/Orchestrator 逻辑**请先开 Issue 讨论

完整 PR 模板和代码风格规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## License

MIT
