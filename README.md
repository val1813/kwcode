# KWCode · 天工开物

<div align="center">

**中国开发者的本地 Coding Agent**

*数据不出网 · Windows 打开就能用 · 越用越懂你的项目*

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Mac%20%7C%20Linux-lightgrey.svg)]()
[![Tests](https://img.shields.io/badge/Tests-282%2F282-brightgreen.svg)]()
[![Version](https://img.shields.io/badge/Version-0.7.0-blue.svg)]()

</div>

---

## 为什么做这个

### 中国开发者用 AI Coding 工具，有三道绕不开的墙

**第一道：数据安全墙**

Claude Code、Cursor、GitHub Copilot 把你的代码发到海外服务器。公司代码、内网项目、涉密工程，这条路根本走不通。国内访问这些工具本来就不稳定，一个任务跑到一半断连，体验极差。

**第二道：小模型能力墙**

本地部署开源模型是解法。DeepSeek、Qwen3、GLM 都能在自己电脑上跑。但这些 8B、14B 的模型在复杂任务上失误率高——所有主流 coding agent 框架都是为强模型设计的，把整个任务丢给一个 LLM 硬扛，强模型能扛，小模型就垮了。

**第三道：代码定位墙**

现有工具找 bug 的方式是把文件列表丢给 LLM，让它猜哪个文件相关。对强模型勉强可以，对小模型是灾难。猜错文件，后面全错。

**KWCode 为这三个问题，逐一给出工程解法。**

---

## 核心技术原理

### 原理一：确定性专家流水线

**理论来源**：Agentless（ICSE 2025）——确定性流水线在 SWE-bench 上同时达到最高通过率和最低成本，优于复杂 agent 架构。

不让 LLM 自主决定下一步，而是走确定性的专家流水线：

```
用户输入
  └─► Gate          任务分类，毫秒级路由
        └─► Locator     精准定位文件和函数
              └─► Generator  只生成修改部分
                    └─► Verifier   语法 + pytest 验证
                          └─► SearchAugmentor  失败时自动搜索
```

小模型只需要在极小的 context 里做一件明确的事。失误可以被及时发现和纠正，不会滚雪球。

---

### 原理二：BM25 + AST 调用图定位（核心差异化）

**理论来源**：
- CodeCompass（arXiv:2602.20048，2026）：258 次实验证明，隐藏依赖任务（G3类）图遍历准确率 **99.4%** vs BM25 **76.2%**，相差 23.2 个百分点
- KGCompass（arXiv:2503.21710，2025）：SWE-bench Lite 成功率 **58.3%**，89.7% 的成功定位来自多跳图遍历

**什么是 G3 类任务**：bug 所在的文件名和函数名，与错误描述没有任何关键词重叠，只能通过调用链追踪发现。这是真实项目里最常见、最难定位的一类 bug。

**KWCode 的两阶段检索**：

```
用户描述 "修复登录失败的 bug"
  │
  ├─► 阶段1：BM25 关键词召回（毫秒级，不调 LLM）
  │     从代码库所有函数/类中，按关键词相关性
  │     召回 top-20 候选函数
  │
  └─► 阶段2：AST 调用图展开（毫秒级，不调 LLM）
        对每个候选函数，沿调用图向上向下各展开 2 跳
        发现那些名字和 bug 毫无关联但实际是根因的隐藏函数

结果：精准的相关函数集合，直接注入 Generator
```

技术实现：`tree-sitter` 多语言 AST + `rank-bm25` + `SQLite` 调用图持久化。不需要 Neo4j，不需要 Docker，不需要 embedding 模型。

支持语言：Python（已完成）· JavaScript/TypeScript/Java/Go/Rust（规划中）

---

### 原理三：专家飞轮（越用越懂你的项目）

**理论来源**：EE-MCP（NeurIPS 2025）——从任务执行轨迹自动提取经验，验证可显著提升后续同类任务成功率。

KWCode 的专家会随使用自动生长：

```
第1天：15 个预置专家开箱即用

使用过程中，飞轮在后台静默积累：
  同类任务成功 ≥5 次 → 触发专家草稿生成
  回测门：新专家成功率 ≥ 原流水线
  AB 测试门：10 次真实对比，提升 >10%
  三道门全过 → 专家正式投产，弹出通知

一个月后：你的项目有了专属专家池
```

专家可以导出分享：

```bash
kwcode expert export SpringBootExpert
# → SpringBootExpert-1.0.0.kwx

kwcode expert install path/to/Vue3Expert.kwx
```

---

### 原理四：模型能力自适应

KWCode 是全球唯一针对本地小模型能力差异做自适应的 coding agent。

| 模型规模 | 自动策略 |
|---------|---------|
| <10B（qwen3:8b） | 强制计划确认 · 任务范围≤2文件 · 第1次失败触发搜索 |
| 10-30B（qwen3:14b） | 可选计划 · 任务范围≤4文件 · 第2次失败触发搜索 |
| >30B（qwen3:72b） | 宽松策略 · 任务范围≤8文件 · 自动处理复杂任务 |

切换模型，策略自动切换，无需配置。

---

## 功能特性

### 代码能力
- BM25 + 调用图两阶段定位，G3 隐藏依赖准确率 99.4%（论文验证）
- Generator 只改必要部分，从文件读 original，LLM 只生成 modified
- 三阶段重试：正常描述 → 从错误出发 → 最小化修改，不重复同样的错
- Reflection 机制：第一次失败先分析根因再重试

### 流程控制
- `/plan 计划模式`：显示执行步骤+风险等级（High/Medium/Low），确认后才动文件
- `Checkpoint 快照`：任务开始前自动备份，失败一键还原，降级建议
- `KWCODE.md 项目规则`：写项目约定，按任务类型分段注入，永远不忘

### 知识积累
- 三层记忆：PROJECT.md（项目结构）/ EXPERT.md（专家记录）/ PATTERN.md（失败模式）
- 非代码文件读取：PDF 需求文档 / Word 规范 / Markdown，BM25 匹配相关段落注入
- 失败模式记录：历史失败积累，/plan 时作为风险评估依据

### 搜索增强
- 默认 DuckDuckGo（零配置，pip install 就能用）
- 可选 SearXNG 自部署：`kwcode setup-search` 一键安装，数据完全不出网
- 四级内容提取：trafilatura → newspaper3k → readabilipy → BeautifulSoup
- 并行搜索 + BM25 重排：结果质量优先
- 意图感知：代码/论文/包/debug 自动优化搜索词

### Office 文档
- Excel：openpyxl 样式模板，深色表头，斑马纹，公式，冻结首行
- PPT：python-pptx，商务配色，三明治结构，不用默认白底
- Word：python-docx，中文首行缩进，规范字体，表格样式

### 价值可见
- `kwcode stats`：完成任务数、节省时间估算、专属专家数
- 飞轮通知：专家投产时弹出，显示成功率提升和速度对比
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
| 模型能力自适应 | ❌ | ❌ | ✅ 独有 |
| AST 调用图定位 | ❌ | ❌ | ✅ 独有 |
| 专家飞轮三道门 | ❌ | ❌ | ✅ 独有 |
| /plan 风险评估 | ✅ | ❌ | ✅ |
| Checkpoint 回滚 | ✅ | ❌ | ✅ |
| 非代码文件读取 | 部分 | ❌ | ✅ |
| 价值量化仪表盘 | ❌ | ❌ | ✅ 独有 |
| 开源 | ❌ | ✅ MIT | ✅ MIT |

---

## 快速开始

### 系统要求

- Python 3.10+
- [Ollama](https://ollama.com/download)（模型运行环境）
- Docker（可选，用于 SearXNG 搜索增强）

| 显存 | 推荐模型 |
|------|---------|
| 4GB | gemma3:4b |
| 8GB | **qwen3:8b（推荐）** |
| 16GB | qwen3:14b |
| 24GB+ | qwen3:30b-a3b |

### 安装

```bash
# 1. 安装 Ollama 并拉取模型
ollama pull qwen3:8b

# 2. 安装 KWCode
pip install kwcode

# 国内加速：
pip install kwcode -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 启动
kwcode
```

首次启动会引导你配置模型连接，按提示操作即可。

### 可选：安装搜索增强

```bash
kwcode setup-search
```

需要 Docker Desktop 已安装并运行。会自动拉取 SearXNG 镜像（约 200MB）并启动容器。不装也能用，默认走 DuckDuckGo 搜索。

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

### 单次执行

```bash
kwcode "修复登录验证失败的问题"
kwcode --plan "重构数据库连接层"
```

### REPL 命令

```
/plan <任务>          计划模式，显示步骤和风险后再执行
/model qwen3:14b      切换模型
/api                  API 配置（见下方说明）
/experts              查看已注册专家
/memory               查看项目记忆
/init                 初始化项目规则文件
/cd <路径>            切换项目目录
/help                 显示帮助
```

### 接入任意 API

KWCode 支持任何 OpenAI 兼容的 API，包括 DeepSeek、Qwen 云端、硅基流动、零一万物、Groq 等。

**临时切换**（当前窗口有效，关掉就恢复）：

```
/api temp https://api.deepseek.com your-api-key
/api temp https://api.siliconflow.cn/v1 your-api-key
```

**永久保存**（写入配置文件，下次启动自动使用）：

```
/api default https://api.deepseek.com your-api-key
```

**查看当前配置**：

```
/api show
```

也可以在启动时通过命令行指定：

```bash
kwcode --ollama-url https://api.deepseek.com
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

### Office 文档生成

```
 > 做一个季度销售报表 Excel，包含月份、销售额、环比增长
 > 做一个项目汇报 PPT，商务风格，5页
 > 写一份技术方案 Word，包含架构图描述和接口设计
```

### 专家管理

```bash
kwcode expert list                    # 查看所有专家
kwcode expert info BugFix             # 查看专家详情
kwcode expert export BugFix           # 导出为 .kwx 文件
kwcode expert install path/to/x.kwx   # 安装外部专家
kwcode expert create MyExpert         # 创建自定义专家
```

### 价值报告

```bash
kwcode stats
```

显示过去 30 天的任务完成数、节省时间估算、最活跃专家。数据仅存本地。

---

## 开发者安装

```bash
git clone https://github.com/val1813/kwcode.git
cd kwcode
pip install -e ".[dev]"
python -m pytest kaiwu/tests/ -v --ignore=kaiwu/tests/bench_tasks
# 282 tests should pass
```

### 项目结构

```
kaiwu/
├── cli/main.py              # CLI 入口，REPL，spinner，结果摘要
├── core/
│   ├── gate.py              # LLM 任务分类
│   ├── orchestrator.py      # 确定性流水线编排
│   ├── planner.py           # /plan 计划模式 + 风险评估
│   ├── checkpoint.py        # 文件快照（git stash / 文件复制）
│   ├── kwcode_md.py         # KWCODE.md 规则加载
│   └── model_capability.py  # 模型能力自适应
├── experts/
│   ├── locator.py           # BM25 + 调用图定位
│   ├── generator.py         # 代码生成（只改必要部分）
│   ├── verifier.py          # 语法检查 + pytest
│   └── search_augmentor.py  # 搜索增强 + BM25 重排
├── search/
│   ├── duckduckgo.py        # SearXNG + DDG 并行搜索
│   ├── extraction_pipeline.py  # 四级内容提取
│   └── intent_classifier.py # 意图感知分类
├── knowledge/doc_reader.py  # PDF/Word/MD 文档读取
├── flywheel/                # 专家飞轮（轨迹→模式→生成→AB测试）
├── registry/                # 专家注册表 + .kwx 打包
├── memory/                  # 三层记忆系统
├── ast_engine/              # tree-sitter AST + 调用图
├── notification/            # 飞轮通知
├── stats/                   # 价值量化（SQLite）
└── llm/                     # Ollama + llama.cpp 双后端
```

---

## 参考文献

1. **Agentless**：Xia et al. *ICSE 2025* — 确定性流水线优于复杂 agent
2. **CodeCompass**：*arXiv:2602.20048, 2026* — 图遍历 G3 任务 99.4% vs BM25 76.2%
3. **KGCompass**：Yang et al. *arXiv:2503.21710, 2025* — SWE-bench Lite 58.3%
4. **AgentCoder**：Huang et al. *EMNLP 2023* — 多专家分工验证
5. **EE-MCP**：*NeurIPS 2025* — 任务轨迹经验提取机制
6. **Agent Psychometrics**：*arXiv:2604.00594, 2026* — 任务特征预测 agent 成功率

---

## 参与贡献

**KWCode 是中国开发者做的，也需要中国开发者一起来完善。**

不管你在北京还是新加坡，在上海还是旧金山，只要你是华人开发者，都欢迎参与。

### 最需要的贡献

**新增预置专家**（最简单，编辑一个 YAML 文件）：

```yaml
# 急需认领：
Vue3Expert / DjangoExpert / GoGinExpert / RustActixExpert
K8sExpert / DockerExpert / RedisExpert / MySQLExpert
```

```bash
kwcode expert create MyExpert
# 用自己真实项目测试 ≥5 个任务，跑通率 ≥80%
# 提 PR
```

**语言支持扩展**：PHP · C# · Kotlin · Swift · Dart（AST 调用图）

**B 站视频教程 / 技术博客**

### 贡献流程

```bash
git clone https://github.com/val1813/kwcode.git
cd kwcode
pip install -e ".[dev]"
python -m pytest kaiwu/tests/ -v --ignore=kaiwu/tests/bench_tasks
git checkout -b feat/your-feature
# 开发 → 测试 → PR
```

**Issues**：Bug 报告、功能建议
**Discussions**：技术讨论、专家设计

---

## License

MIT — 自由使用、修改、分发。

---

<div align="center">

**如果 KWCode 对你有帮助，请给一个 ⭐**

这不只是一个工具，是华人开发者社区共同的技术资产。

*天工开物 · KWCode*

</div>
