# 任意 GitHub 项目的自动运行与在线测试系统方案

> 目标：从任意 GitHub 项目出发，自动拉取仓库、分析项目结构、生成可执行 Runbook、在沙箱中验证项目能否启动，并在项目拉起后自动生成和执行在线测试用例，最终输出可复现的测试报告。

***

## 1. 背景与问题定义

现在很多 AI 编程工具可以根据代码生成测试，但真正落地时，经常卡在更前面的问题：陌生项目不知道怎么安装、怎么配置环境、怎么启动、需要哪些依赖服务、启动后如何判断服务真的可用。

因此，这个系统的核心不应该只是“生成测试用例”，而是先把一个陌生 GitHub 仓库转化成一个可执行、可验证、可回放的运行对象。核心产物是结构化 Runbook，而不是一段自然语言说明。

本方案的目标链路是：

```text
GitHub Repo -> Repository Profile -> Runbook Candidate -> Sandbox Verification -> Verified Runtime -> Test Target Discovery -> Generated Tests -> Online Test Report
```

其中最重要的资产是 Verified Runbook。只要 Runbook 稳定，后续自动化测试、Bug 复现、PR 修复、CI 接入、Agent 编排都可以基于它展开。

***

## 2. 目标与非目标

### 2.1 目标

1. 支持输入任意 GitHub 仓库 URL 和可选 commit SHA。
2. 自动识别项目语言、框架、包管理器、启动入口、依赖服务、端口、环境变量。
3. 自动生成结构化 Runbook，说明如何安装、构建、启动、健康检查和测试。
4. 在隔离沙箱中验证 Runbook 是否能成功启动项目。
5. 当启动失败时，根据日志进行有限次数的自动修复。
6. 项目启动成功后，自动发现 API、页面、CLI 等测试目标。
7. 自动生成弱 Oracle 测试和部分强 Oracle 测试。
8. 执行在线测试，输出结构化报告、失败证据和可复现命令。
9. 支持后续接入 Claude Code / Codex / CoderFleet / CI。

### 2.2 非目标

1. 第一阶段不追求理解所有业务逻辑。
2. 第一阶段不保证任意项目 100% 启动成功。
3. 第一阶段不自动使用真实生产凭证或真实第三方服务。
4. 第一阶段不直接修改仓库代码，除非后续增加“修复 PR”模式。
5. 第一阶段不把 LLM 当成唯一决策源，而是以静态证据、执行结果和日志为主。

***

## 3. 总体架构

```text
                 +----------------------+
                 |  GitHub Repository   |
                 +----------+-----------+
                            |
                            v
+-------------------+  +---------------------+  +---------------------+
| Repo Cloner       |->| Repository Profiler |->| Run Signal Extractor|
+-------------------+  +---------------------+  +---------------------+
                                                       |
                                                       v
                                             +---------------------+
                                             | Runbook Generator   |
                                             +----------+----------+
                                                        |
                                                        v
                                             +---------------------+
                                             | Sandbox Executor    |
                                             +----------+----------+
                                                        |
                                  +---------------------+---------------------+
                                  |                                           |
                                  v                                           v
                         +----------------+                         +---------------------+
                         | Repair Agent   |<------------------------| Failure Diagnosis   |
                         +----------------+                         +---------------------+
                                  |
                                  v
                         +-------------------+
                         | Verified Runbook  |
                         +---------+---------+
                                   |
                                   v
                         +-------------------+
                         | App Launcher      |
                         +---------+---------+
                                   |
                                   v
+--------------------+   +----------------------+   +---------------------+
| Target Discovery   |-->| Test Case Generator  |-->| Online Test Executor|
+--------------------+   +----------------------+   +---------------------+
                                                                |
                                                                v
                                                       +----------------+
                                                       | Test Report    |
                                                       +----------------+
```

系统可以拆成三层：

1. 理解层：分析仓库，提取运行信号，生成 Repository Profile。
2. 运行层：生成 Runbook，沙箱执行，失败修复，产出 Verified Runbook。
3. 测试层：发现测试目标，生成测试用例，执行在线测试，输出报告。

***

## 4. 核心设计原则

### 4.1 Evidence-first，而不是 LLM-first

不要让 LLM 直接猜项目怎么跑。LLM 应该在证据不足、信号冲突、日志诊断时辅助决策。运行步骤要尽量来自这些证据：

1. `.github/workflows/*.yml`
2. `README.md` / `docs/`
3. `Dockerfile` / `docker-compose.yml` / `compose.yaml`
4. `.devcontainer/devcontainer.json`
5. `package.json` / `pyproject.toml` / `pom.xml` / `build.gradle` / `go.mod` / `Cargo.toml`
6. `Makefile` / `Taskfile.yml` / `justfile`
7. `.env.example` / `config.example.*`
8. 源码入口推断
9. LLM 推断

每个结论都要绑定 evidence，包括文件路径、行号、原始片段、推断理由和置信度。

### 4.2 Runbook 是核心资产

Runbook 必须结构化、可执行、可验证、可修复。它不只是“运行说明”，而是系统后续所有动作的接口协议。

### 4.3 执行验证优先于静态判断

一个命令是否正确，不看 LLM 说得多有道理，而看它是否能在沙箱里成功执行。执行结果、日志、退出码、端口探测、HTTP 健康检查才是最终依据。

### 4.4 默认不信任仓库代码

任意 GitHub 项目都可能包含恶意脚本。所有安装、构建、启动、测试动作都必须在一次性隔离环境中执行，不能泄露宿主机密钥，不能直接暴露宿主 Docker socket，不能无限制访问内网。

### 4.5 测试用例必须绑定 Oracle

AI 生成测试的核心问题不是“生成多少测试”，而是“怎么判断对错”。第一阶段优先使用弱 Oracle：不能 500、不能崩溃、不能超时、不能泄露堆栈、响应格式稳定、页面无严重 console error。

***

## 5. 关键概念

### 5.1 Repository Profile

Repository Profile 是仓库静态分析结果，描述项目是什么、可能怎么跑、依赖什么。

```json
{
  "repo": "https://github.com/org/repo",
  "commit": "abc123",
  "languages": ["typescript", "python"],
  "frameworks": ["react", "fastapi"],
  "package_managers": ["pnpm", "uv"],
  "entrypoints": [
    {
      "type": "script",
      "file": "package.json",
      "key": "scripts.dev",
      "command": "vite --host 0.0.0.0"
    }
  ],
  "runtime_versions": {
    "node": ">=20",
    "python": ">=3.11"
  },
  "services": ["postgres", "redis"],
  "env_vars": {
    "required": ["DATABASE_URL"],
    "optional": ["LOG_LEVEL"]
  },
  "ports": [3000, 8000],
  "evidence": []
}
```

### 5.2 Runbook Candidate

Runbook Candidate 是候选运行方案，可能有多个。例如 Docker Compose 方案、CI 转换方案、本地包管理器方案。

```json
{
  "id": "node_pnpm_dev",
  "confidence": 0.82,
  "setup": ["corepack enable", "pnpm install --frozen-lockfile"],
  "build": ["pnpm build"],
  "run": ["pnpm dev --host 0.0.0.0"],
  "healthcheck": {
    "type": "http",
    "url": "http://127.0.0.1:3000",
    "expected_status": [200, 302, 404]
  },
  "fallbacks": ["pnpm start", "npm run dev"],
  "evidence": [
    {"file": "package.json", "reason": "scripts.dev exists"},
    {"file": "README.md", "reason": "README mentions pnpm dev"}
  ]
}
```

### 5.3 Verified Runbook

Verified Runbook 是经过真实执行验证的运行方案。

```json
{
  "status": "verified",
  "repo": "https://github.com/org/repo",
  "commit": "abc123",
  "runtime": {
    "image": "node:20-bookworm",
    "workdir": "/workspace/repo"
  },
  "setup_commands": ["corepack enable", "pnpm install --frozen-lockfile"],
  "start_commands": ["pnpm dev --host 0.0.0.0"],
  "services": [],
  "env": {},
  "ports": [{"container": 3000, "host": 49152}],
  "healthcheck_result": {
    "passed": true,
    "url": "http://127.0.0.1:49152",
    "status_code": 200
  },
  "logs_summary": "App started successfully on port 3000",
  "reproduce": [
    "git clone https://github.com/org/repo",
    "cd repo",
    "corepack enable",
    "pnpm install --frozen-lockfile",
    "pnpm dev --host 0.0.0.0"
  ]
}
```

***

## 6. 运行信号提取策略

### 6.1 GitHub Actions

GitHub Actions workflow 是非常高价值的信号。它通常包含项目真实使用的 runtime、安装命令、构建命令、测试命令和服务依赖。

需要解析的字段：

| 字段                               | 用途                                              |
| -------------------------------- | ----------------------------------------------- |
| `jobs.<job_id>.runs-on`          | 判断基础运行环境                                        |
| `jobs.<job_id>.container.image`  | 判断容器镜像                                          |
| `jobs.<job_id>.services`         | 判断数据库、Redis、MQ 等依赖                              |
| `steps[*].uses`                  | 判断 setup-node/setup-python/setup-java 等 runtime |
| `steps[*].run`                   | 提取安装、构建、测试命令                                    |
| `defaults.run.working-directory` | 判断 monorepo 子目录                                 |
| `strategy.matrix`                | 判断多版本、多语言组合                                     |

转换策略：

```text
GitHub Actions step -> Local Execution Step
setup-node@v4 -> select node runtime
setup-python@v5 -> select python runtime
services.postgres -> docker compose service postgres
run: npm ci -> setup command
run: npm test -> existing test command
```

### 6.2 README / docs

README 是运行说明的重要来源，但可信度略低于 CI，因为 README 可能过时。需要提取：

1. Install / Setup / Development / Run / Test / Docker / Environment 等章节。
2. 代码块中的命令。
3. `.env` 配置说明。
4. 端口和访问地址。
5. 数据库初始化、migration、seed 命令。

提取时不要只做全文 LLM 总结，而要保留代码块和上下文位置。

### 6.3 Dockerfile / Docker Compose

如果存在 `docker-compose.yml` 或 `compose.yaml`，优先生成 Compose Runbook。

需要识别：

1. services
2. build context
3. image
4. command / entrypoint
5. ports
6. depends\_on
7. environment
8. env\_file
9. healthcheck
10. volumes

### 6.4 Dev Container

`.devcontainer/devcontainer.json` 对自动化运行非常有价值，因为它描述开发容器环境、features、postCreateCommand、postStartCommand、forwardPorts、containerEnv、remoteEnv 等。

需要提取：

1. image / build.dockerfile
2. dockerComposeFile / service
3. features
4. containerEnv / remoteEnv
5. postCreateCommand / postStartCommand
6. forwardPorts
7. customizations

### 6.5 包管理器文件

| 文件                 | 语言/生态       | 提取内容                                                |
| ------------------ | ----------- | --------------------------------------------------- |
| `package.json`     | Node.js     | scripts、dependencies、engines、packageManager         |
| `pnpm-lock.yaml`   | Node.js     | pnpm 项目确认                                           |
| `yarn.lock`        | Node.js     | yarn 项目确认                                           |
| `pyproject.toml`   | Python      | dependencies、scripts、requires-python、tool.uv/poetry |
| `requirements.txt` | Python      | pip 依赖                                              |
| `Pipfile`          | Python      | pipenv 依赖                                           |
| `pom.xml`          | Java        | Maven、Spring Boot、插件                                |
| `build.gradle`     | Java/Kotlin | Gradle task、Spring Boot                             |
| `go.mod`           | Go          | module、Go version                                   |
| `Cargo.toml`       | Rust        | binary、features、workspace                           |

### 6.6 源码入口推断

当没有明确文档时，使用规则推断：

| 项目类型        | 识别信号                             | 候选启动命令                                          |
| ----------- | -------------------------------- | ----------------------------------------------- |
| Vite        | `vite` dependency / `index.html` | `npm run dev -- --host 0.0.0.0`                 |
| Next.js     | `next` dependency                | `npm run dev` / `next dev`                      |
| Express     | `express` dependency             | `node index.js` / `npm start`                   |
| FastAPI     | `fastapi` + `app = FastAPI()`    | `uvicorn module:app --host 0.0.0.0 --port 8000` |
| Django      | `manage.py`                      | `python manage.py runserver 0.0.0.0:8000`       |
| Flask       | `flask` dependency               | `flask run --host 0.0.0.0 --port 5000`          |
| Spring Boot | `@SpringBootApplication`         | `mvn spring-boot:run`                           |
| Go HTTP     | `main.go` + `ListenAndServe`     | `go run ./...`                                  |
| Rust web    | `Cargo.toml` + axum/actix/rocket | `cargo run`                                     |

***

## 7. Runbook 生成算法

### 7.1 候选方案生成顺序

优先级建议：

1. Docker Compose / DevContainer Compose 方案
2. Dockerfile 方案
3. GitHub Actions 转换方案
4. README 明确命令方案
5. 包管理器脚本方案
6. 源码入口推断方案
7. LLM 补全方案

每个方案都要生成 confidence。示例：

| 信号                          |   权重 |
| --------------------------- | ---: |
| CI 中真实执行过                   | 0.30 |
| README 明确说明                 | 0.25 |
| Docker/Compose/DevContainer | 0.25 |
| 包管理器脚本                      | 0.15 |
| 源码入口推断                      | 0.10 |
| LLM 推断                      | 0.05 |

最终置信度不是简单相加，而是 Evidence Aggregation：相互独立且一致的证据提升置信度，冲突证据降低置信度。

### 7.2 冲突处理

常见冲突：

| 冲突                           | 处理策略                              |
| ---------------------------- | --------------------------------- |
| README 说 npm，lockfile 是 pnpm | 优先 lockfile，同时保留 npm 方案为 fallback |
| README 端口 3000，源码默认 5173     | 运行时自动端口探测                         |
| CI 只跑 test，不跑 server         | CI 用于 setup/test，启动命令从其他信号补充      |
| Dockerfile 只是生产构建            | 尝试 Dockerfile 方案，同时生成 dev 方案      |
| monorepo 多个 app              | 生成多个 app profile，分别验证             |

### 7.3 Runbook YAML Schema

```yaml
schema_version: "v1"
repo:
  url: "https://github.com/org/repo"
  commit: "abc123"
  subdir: "."

profile:
  project_type: ["web_backend", "frontend"]
  languages: ["typescript"]
  frameworks: ["nextjs"]
  package_managers: ["pnpm"]

runtime:
  strategy: "container"
  image: "node:20-bookworm"
  user: "sandbox"
  workdir: "/workspace/repo"
  resources:
    cpu: 2
    memory: "4Gi"
    timeout_seconds: 900

services:
  - name: "postgres"
    image: "postgres:16"
    env:
      POSTGRES_DB: "app"
      POSTGRES_USER: "app"
      POSTGRES_PASSWORD: "app"
    ports:
      - container: 5432
    healthcheck:
      type: "command"
      command: "pg_isready -U app"

env:
  generated:
    DATABASE_URL: "postgresql://app:app@postgres:5432/app"
  required: []
  optional: []

steps:
  setup:
    - command: "corepack enable"
    - command: "pnpm install --frozen-lockfile"
  build:
    - command: "pnpm build"
      optional: true
  migrate:
    - command: "pnpm db:migrate"
      optional: true
  start:
    - command: "pnpm dev --host 0.0.0.0"
      background: true
      expected_ports: [3000]

healthcheck:
  strategy: "http"
  url: "http://127.0.0.1:${PORT}/"
  acceptable_status: [200, 301, 302, 404]
  timeout_seconds: 120

test:
  existing_commands:
    - "pnpm test"
  generated:
    enabled: true
    types: ["smoke", "api", "ui"]

evidence:
  - file: "package.json"
    kind: "script"
    value: "dev"
    confidence: 0.8
  - file: "README.md"
    kind: "command_block"
    value: "pnpm dev"
    confidence: 0.7
```

***

## 8. 沙箱执行设计

### 8.1 为什么必须沙箱化

运行任意 GitHub 项目的安装脚本、构建脚本和测试脚本，本质上是在执行不可信代码。风险包括：

1. 窃取环境变量和密钥。
2. 扫描内网。
3. 修改宿主机文件。
4. 挖矿或消耗资源。
5. 通过 Docker socket 提权。
6. 下载恶意依赖。

### 8.2 最小安全要求

| 维度     | 要求                             |
| ------ | ------------------------------ |
| 文件系统   | 临时 workspace，用后销毁，不挂载宿主敏感目录    |
| 用户权限   | 非 root 用户运行项目命令                |
| 密钥     | 默认不注入任何真实 token                |
| 网络     | 默认只允许访问公网依赖源，可配置禁用内网访问         |
| 资源     | CPU、内存、磁盘、运行时间限制               |
| 进程     | 每个项目独立进程组，超时后全部清理              |
| Docker | 不直接挂载宿主 `/var/run/docker.sock` |
| 日志     | 捕获 stdout/stderr，做脱敏处理         |

### 8.3 执行环境选型

| 方案               | 优点         | 缺点                  | 建议             |
| ---------------- | ---------- | ------------------- | -------------- |
| 普通 Docker worker | 简单、容易实现    | 隔离一般，不能安全跑嵌套 Docker | MVP 可用         |
| Docker-in-Docker | 支持 Compose | 安全和性能需要控制           | 需要 Compose 时使用 |
| gVisor/Kata      | 隔离更强       | 运维复杂                | 中期增强           |
| Firecracker      | 安全强、隔离好    | 实现复杂                | 长期目标           |
| Kubernetes Job   | 易扩展        | 安全策略要设计好            | 多租户部署可用        |

### 8.4 Worker 执行流程

```text
1. 创建 job_id 和临时 workspace
2. 拉取仓库到 workspace
3. checkout 指定 commit
4. 静态分析并生成 candidates
5. 选择最高置信度 Runbook
6. 启动依赖服务
7. 执行 setup/build/migrate/start
8. 捕获日志、退出码、端口、进程状态
9. 执行 healthcheck
10. 成功则产出 Verified Runbook
11. 失败则进入 Repair Loop
12. 清理临时资源或保留调试 artifact
```

***

## 9. Repair Loop 设计

### 9.1 基本流程

```python
for attempt in range(max_attempts):
    result = executor.run(runbook)

    if result.healthcheck_passed:
        return VerifiedRunbook(runbook, result)

    diagnosis = failure_analyzer.analyze(
        logs=result.logs,
        exit_code=result.exit_code,
        profile=repo_profile,
        runbook=runbook,
    )

    patch = repair_agent.propose_patch(diagnosis, runbook, repo_profile)

    if not patch or patch.risk_too_high:
        break

    runbook = apply_patch(runbook, patch)

return FailedRunbook(runbook, diagnosis)
```

### 9.2 常见失败与修复策略

| 失败现象                       | 可能原因         | 修复动作                                               |
| -------------------------- | ------------ | -------------------------------------------------- |
| `node: command not found`  | runtime 镜像错误 | 切换 Node 镜像                                         |
| `pnpm: command not found`  | 未启用 corepack | 插入 `corepack enable`                               |
| `ModuleNotFoundError`      | Python 依赖未安装 | 插入 `uv sync` / `pip install -r requirements.txt`   |
| `ECONNREFUSED postgres`    | 缺少数据库服务      | 自动生成 postgres service                              |
| `DATABASE_URL is required` | 缺少环境变量       | 从 `.env.example` 生成默认值                             |
| `address already in use`   | 端口冲突         | 改用随机 host port                                     |
| migration 报错               | 数据库未初始化      | 插入 migrate/seed 命令                                 |
| healthcheck 超时             | 服务启动慢        | 增加 wait strategy 或日志 ready 判断                      |
| 404                        | 根路径不是健康路径    | 尝试 `/health`、`/api/health`、`/docs`、`/openapi.json` |

### 9.3 修复边界

Repair Loop 只能修改 Runbook，不应该默认修改项目源码。除非进入“修复 PR 模式”，否则不对仓库文件做永久变更。

每次修复都要记录：

```json
{
  "attempt": 2,
  "diagnosis": "pnpm not found",
  "patch": "insert setup command: corepack enable",
  "evidence": "stderr contains 'pnpm: command not found'",
  "result": "passed setup, failed healthcheck"
}
```

***

## 10. 项目启动成功后的测试目标发现

### 10.1 测试目标类型

| 类型         | 发现方式                                | 测试方式          |
| ---------- | ----------------------------------- | ------------- |
| HTTP API   | OpenAPI、路由源码、抓包                     | API 测试        |
| Web UI     | 首页、链接、表单、DOM                        | Playwright 测试 |
| CLI        | package scripts、console entrypoints | 命令行测试         |
| Worker/Job | README、代码入口                         | 启动与日志测试       |
| Library    | existing tests、examples             | 单元/集成测试       |

### 10.2 API 发现策略

优先级：

1. 访问 `/openapi.json`
2. 访问 `/swagger.json`
3. 访问 `/docs`
4. 框架源码路由提取
5. Controller/Router 文件解析
6. README API 文档提取
7. 运行时流量探索

API Target 示例：

```json
{
  "type": "api",
  "base_url": "http://127.0.0.1:49152",
  "source": "openapi",
  "endpoints": [
    {
      "method": "GET",
      "path": "/api/health",
      "auth_required": false,
      "params_schema": {}
    }
  ]
}
```

### 10.3 UI 发现策略

UI 探索流程：

```text
1. 打开首页
2. 等待网络空闲或主要元素出现
3. 收集 title、heading、button、link、form、input
4. 捕获 console error 和 network error
5. 对主要链接做一层 BFS
6. 生成页面图 Page Graph
7. 从 Page Graph 中选择高价值路径生成测试
```

Page Graph 示例：

```json
{
  "pages": [
    {
      "url": "/",
      "title": "Dashboard",
      "actions": [
        {"type": "click", "text": "Login"},
        {"type": "fill", "label": "Email"}
      ],
      "assertions": ["page loads", "no console error"]
    }
  ]
}
```

***

## 11. 测试生成策略

### 11.1 测试分层

| 层级                | 目标        | 适用范围           | Oracle     |
| ----------------- | --------- | -------------- | ---------- |
| Smoke Test        | 服务是否活着    | 所有 Web 项目      | 弱 Oracle   |
| API Contract Test | API 格式稳定  | API 项目         | Schema/状态码 |
| Negative Test     | 错误输入不崩溃   | API/表单         | 不能 500     |
| UI Smoke Test     | 页面可打开     | 前端项目           | 无崩溃/无严重错误  |
| Flow Test         | 核心链路      | 有明确业务文档项目      | 强 Oracle   |
| Regression Test   | 已知 Bug 复现 | 有 issue/失败日志项目 | 精确 Oracle  |

### 11.2 弱 Oracle

弱 Oracle 适合任意项目，第一阶段重点做这些：

1. HTTP 响应不能是 500/502/503/504。
2. 响应不能超时。
3. JSON 接口返回应是合法 JSON。
4. 错误响应不应包含 stack trace、secret、绝对路径。
5. 页面不应出现未捕获 JS exception。
6. 页面主要资源不应大量 404。
7. 服务日志不应出现 panic、segmentation fault、unhandled exception。

### 11.3 强 Oracle

强 Oracle 来自：

1. OpenAPI schema。
2. README 业务说明。
3. existing tests。
4. examples。
5. 数据库模型和领域模型。
6. issue 描述。
7. 用户提供的需求文档。

示例：

```json
{
  "name": "create_user_then_get_user",
  "steps": [
    {"method": "POST", "path": "/users", "body": {"name": "Alice"}},
    {"method": "GET", "path": "/users/{id}"}
  ],
  "assertions": [
    "POST status is 200 or 201",
    "GET returns same id",
    "GET response.name == 'Alice'"
  ]
}
```

### 11.4 测试生成提示词原则

生成测试时，提示词必须限制 LLM：

```text
你只能基于已发现的 Test Targets 生成测试。
不能编造不存在的接口、按钮、字段。
每个测试必须包含：前置条件、操作步骤、断言、失败诊断信息。
优先生成能发现崩溃、500、权限绕过、输入校验缺失的问题。
如果缺少认证信息，不要假设真实账号，生成未认证访问测试或跳过认证用例。
```

***

## 12. 在线测试执行器设计

### 12.1 API 测试执行

可以用内部 DSL 表达测试，然后转成 pytest / Playwright API / curl 执行。

测试 DSL：

```yaml
name: "missing required field should not 500"
type: "api"
request:
  method: "POST"
  url: "${BASE_URL}/api/users"
  headers:
    content-type: "application/json"
  body:
    name: "test"
assertions:
  - expr: "status_code in [400, 401, 403, 422]"
  - expr: "response_time_ms < 3000"
  - expr: "not contains_stack_trace(response.text)"
```

### 12.2 UI 测试执行

UI 测试建议用 Playwright。每次失败保留：

1. screenshot
2. trace
3. console logs
4. network logs
5. DOM snapshot

UI 测试示例：

```typescript
import { test, expect } from '@playwright/test';

test('home page should load without severe console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', msg => {
    if (msg.type() === 'error') errors.push(msg.text());
  });

  const response = await page.goto(process.env.BASE_URL!, { waitUntil: 'networkidle' });
  expect(response?.status()).toBeLessThan(500);
  expect(errors).toEqual([]);
});
```

### 12.3 失败证据结构

```json
{
  "test_name": "POST /api/users missing email",
  "status": "failed",
  "reason": "returned 500 instead of 400/422",
  "request": {
    "method": "POST",
    "url": "http://127.0.0.1:49152/api/users",
    "body": {"name": "test"}
  },
  "response": {
    "status": 500,
    "body_excerpt": "Internal Server Error"
  },
  "server_logs_excerpt": [
    "KeyError: email"
  ],
  "reproduce": [
    "curl -X POST http://127.0.0.1:49152/api/users -d '{\"name\":\"test\"}'"
  ]
}
```

***

## 13. Agent 分工设计

结合多 Agent 系统，可以拆成以下角色：

| Agent             | 输入                   | 输出                   | 职责边界                       |
| ----------------- | -------------------- | -------------------- | -------------------------- |
| Repo Profiler     | repo path            | RepositoryProfile    | 只做事实提取，不生成最终方案             |
| Signal Extractor  | files                | Evidence Set         | 提取 CI、README、Docker、包管理器信号 |
| Runbook Planner   | Profile + Evidence   | Runbook Candidates   | 生成候选运行方案                   |
| Sandbox Executor  | Runbook              | Execution Result     | 执行命令和探测，不做主观判断             |
| Failure Analyzer  | logs/result          | Diagnosis            | 归因失败类型                     |
| Repair Agent      | Diagnosis + Runbook  | Runbook Patch        | 只修 Runbook，不改源码            |
| Runtime Verifier  | app process          | VerifiedRunbook      | 判断项目是否真正可用                 |
| Target Discoverer | running app + source | TestTarget Set       | 发现 API/UI/CLI 测试目标         |
| Test Generator    | TestTarget Set       | Test Cases           | 基于目标生成测试                   |
| Test Executor     | Test Cases           | TestReport           | 执行测试并收集证据                  |
| Report Writer     | all artifacts        | Markdown/HTML report | 输出人类可读报告                   |

核心原则：

```text
Profiler 不猜运行命令。
Planner 不执行命令。
Executor 不修改 Runbook。
Repair Agent 不修改源码。
Test Generator 不编造目标。
Report Writer 不掩盖失败。
```

***

## 14. 系统模块设计

### 14.1 Repo Cloner

功能：

1. 支持 GitHub URL、branch、tag、commit SHA。
2. 支持 shallow clone。
3. 支持 sparse checkout，用于 monorepo 优化。
4. 记录 commit、remote、submodule 状态。

输出：

```json
{
  "repo_dir": "/workspace/jobs/job-123/repo",
  "commit": "abc123",
  "default_branch": "main",
  "submodules": []
}
```

### 14.2 Repository Profiler

实现建议：

1. 文件树扫描。
2. 语言识别。
3. 包管理器识别。
4. 框架识别。
5. monorepo workspace 识别。
6. 配置文件解析。
7. 入口文件识别。

### 14.3 Evidence Store

所有证据统一存储：

```json
{
  "id": "ev_001",
  "file": "package.json",
  "kind": "package_script",
  "path": "scripts.dev",
  "value": "vite --host 0.0.0.0",
  "confidence": 0.8,
  "source_excerpt": "\"dev\": \"vite --host 0.0.0.0\""
}
```

### 14.4 Runbook Planner

输入 Evidence Set，输出多个 Runbook Candidate。

选择策略：

```text
1. 按置信度排序
2. 优先选择隔离性更好的方案
3. 优先选择依赖最少的方案
4. 优先选择有 healthcheck 的方案
5. 保留 fallback candidates
```

### 14.5 Sandbox Executor

核心能力：

1. 命令执行。
2. 后台进程管理。
3. 日志流式采集。
4. 超时控制。
5. 端口探测。
6. HTTP 探测。
7. artifact 收集。
8. 资源清理。

### 14.6 Report Writer

报告至少包含：

1. 项目信息。
2. 识别结果。
3. Runbook 候选列表。
4. 验证过程。
5. 最终 Verified Runbook。
6. 测试目标。
7. 测试用例。
8. 测试结果。
9. 失败问题。
10. 复现命令。
11. 后续建议。

***

## 15. 数据库与存储设计

### 15.1 主要实体

| 表/集合                 | 作用             |
| -------------------- | -------------- |
| repositories         | 仓库元信息          |
| analysis\_jobs       | 一次分析任务         |
| repository\_profiles | 仓库分析结果         |
| evidence\_items      | 证据项            |
| runbook\_candidates  | 候选运行方案         |
| execution\_attempts  | 每次执行记录         |
| verified\_runbooks   | 验证后的运行方案       |
| test\_targets        | 测试目标           |
| generated\_tests     | 生成的测试用例        |
| test\_runs           | 测试执行记录         |
| test\_failures       | 失败详情           |
| artifacts            | 日志、截图、trace、报告 |

### 15.2 Artifact 存储

建议目录结构：

```text
/artifacts/{job_id}/
  repo-profile.json
  evidence.jsonl
  runbook-candidates/
    candidate-001.yaml
  attempts/
    attempt-001/
      stdout.log
      stderr.log
      process.json
      diagnosis.json
  verified-runbook.yaml
  test-targets.json
  generated-tests/
  test-results.json
  report.md
  report.html
  screenshots/
  traces/
```

***

## 16. 技术选型建议

### 16.1 后端语言

建议优先 Python，因为：

1. 适合快速做文件分析、规则引擎和 LLM 编排。
2. 生态里有成熟的 YAML/TOML/AST/HTTP/Playwright/pytest 工具。
3. 适合与你现有 Python 多 Agent / Worker 体系集成。

核心组件：

| 模块       | 技术                                               |
| -------- | ------------------------------------------------ |
| API 服务   | FastAPI                                          |
| Worker   | Python asyncio / Celery / 自研 Redis Stream Worker |
| 队列       | Redis Streams                                    |
| 状态存储     | PostgreSQL                                       |
| Artifact | 本地 FS / MinIO / S3                               |
| 容器执行     | Docker SDK / Kubernetes Job                      |
| 浏览器测试    | Playwright                                       |
| API 测试   | httpx / pytest                                   |
| 文档解析     | tree-sitter / markdown-it / pyyaml / tomllib     |
| LLM 编排   | LangGraph / 自研 Agent Runner                      |

### 16.2 执行层

MVP：

```text
FastAPI Controller + Redis Streams + Docker Worker
```

中期：

```text
FastAPI Controller + Redis Streams + Kubernetes Job Worker + Artifact Store
```

长期：

```text
Multi-tenant Controller + K8s + gVisor/Kata/Firecracker + Policy Engine
```

### 16.3 测试层

| 测试类型           | 工具                                          |
| -------------- | ------------------------------------------- |
| API smoke      | httpx                                       |
| API contract   | schemathesis / 自研 OpenAPI runner            |
| UI smoke       | Playwright                                  |
| CLI            | subprocess + snapshot                       |
| Existing tests | pytest/npm test/mvn test/go test/cargo test |
| 报告             | Allure / 自研 HTML                            |

***

## 17. MVP 方案

### 17.1 MVP 范围

第一版只支持：

1. 公共 GitHub 仓库。
2. Node.js、Python、Java Spring Boot、Go 四类项目。
3. Dockerfile / Docker Compose / package manager / README / CI 信号分析。
4. 单服务 Web 项目优先。
5. 自动生成 Runbook。
6. 沙箱验证启动。
7. Smoke Test。
8. Markdown/HTML 报告。

暂不支持：

1. 私有仓库。
2. 复杂微服务集群。
3. 真实第三方服务凭证。
4. 自动代码修复 PR。
5. 完整业务测试。

### 17.2 MVP 交互方式

CLI：

```bash
autotest-agent run https://github.com/org/repo --commit abc123
```

输出：

```text
Job: job-123
Status: success
Verified Runbook: artifacts/job-123/verified-runbook.yaml
Report: artifacts/job-123/report.html
```

API：

```http
POST /jobs
Content-Type: application/json

{
  "repo_url": "https://github.com/org/repo",
  "commit": "abc123",
  "mode": "run_and_test"
}
```

### 17.3 MVP 模块优先级

| 优先级 | 模块                       | 原因           |
| --- | ------------------------ | ------------ |
| P0  | Repo Cloner              | 入口基础         |
| P0  | File Scanner / Profiler  | 运行理解基础       |
| P0  | Runbook Schema           | 后续模块接口       |
| P0  | Docker Sandbox Executor  | 验证基础         |
| P0  | Log Collector            | Repair 和报告依赖 |
| P1  | GitHub Actions Parser    | 高价值信号        |
| P1  | README Command Extractor | 高价值信号        |
| P1  | Package Manager Detector | 覆盖多数项目       |
| P1  | Healthcheck Prober       | 判断启动成功       |
| P2  | Repair Loop              | 提升成功率        |
| P2  | API/UI Smoke Test        | 在线测试基础       |
| P3  | OpenAPI Test Generator   | 进阶测试         |
| P3  | Playwright Explorer      | 进阶 UI 测试     |

***

## 18. 迭代路线图

### Phase 1：静态分析与 Runbook 生成

目标：输入 repo，输出 RepositoryProfile 和 Runbook Candidates。

验收标准：

1. 能识别 Node/Python/Java/Go 项目。
2. 能提取 README、CI、Docker、package scripts 中的运行命令。
3. 每个运行方案都有 evidence 和 confidence。
4. 能输出 runbook.yaml。

### Phase 2：沙箱执行与 Verified Runbook

目标：真实执行候选 Runbook，判断项目是否能启动。

验收标准：

1. 能在 Docker worker 中执行 setup/build/start。
2. 能捕获日志、退出码、端口。
3. 能做 HTTP healthcheck。
4. 能输出 verified-runbook.yaml 或 failure diagnosis。

### Phase 3：Repair Loop

目标：对常见失败做自动修复。

验收标准：

1. 支持缺 runtime、缺包管理器、缺 env、缺数据库、端口冲突等修复。
2. 每次修复都有 patch 记录。
3. 修复次数有限，避免无限循环。

### Phase 4：Smoke Test

目标：项目启动后做基础在线测试。

验收标准：

1. 能测试 `/`、`/health`、`/docs`、`/openapi.json`。
2. 能判断非 5xx、非超时、响应格式合理。
3. 能生成测试报告。

### Phase 5：API 和 UI 生成式测试

目标：基于已发现目标生成测试。

验收标准：

1. 能从 OpenAPI 生成 API 测试。
2. 能从页面探索生成 UI smoke 测试。
3. 每个测试都有 Oracle。
4. 失败有可复现请求和日志证据。

### Phase 6：CI / PR 集成

目标：把系统变成开发流程的一部分。

验收标准：

1. 可以对 PR 分支运行。
2. 可以评论 PR 测试报告。
3. 可以生成失败复现步骤。
4. 可以与 Claude Code / Codex 修复链路打通。

***

## 19. 成功率评估方法

### 19.1 数据集构建

选取 100-300 个开源项目作为评估集：

| 类型                | 数量 |
| ----------------- | -: |
| Node.js 前端        | 50 |
| Node.js 后端        | 50 |
| Python Web        | 50 |
| Java Spring Boot  | 30 |
| Go Web            | 30 |
| Docker Compose 项目 | 30 |
| Monorepo 项目       | 30 |

### 19.2 指标

| 指标                       | 定义                   |
| ------------------------ | -------------------- |
| Profile Accuracy         | 语言、框架、包管理器识别是否正确     |
| Runbook Precision        | 生成的第一候选 Runbook 是否合理 |
| Startup Success Rate     | 项目是否成功启动             |
| Repair Success Rate      | 失败后修复成功比例            |
| Time To Verified Runtime | 从输入 repo 到启动成功的时间    |
| Smoke Test Coverage      | 发现并执行的基础测试数量         |
| Failure Reproducibility  | 失败是否能用报告复现           |
| False Failure Rate       | 系统误报失败比例             |
| Security Incident Rate   | 是否发生越权、泄密、资源失控       |

### 19.3 分层评估

不要只看总体成功率，要分层统计：

```text
有 Docker Compose 的项目成功率
有 GitHub Actions 的项目成功率
README 完整的项目成功率
无文档项目成功率
Node/Python/Java/Go 各语言成功率
单体项目 / monorepo 成功率
```

***

## 20. 安全策略

### 20.1 网络策略

MVP 可以先允许公网访问依赖源，但应禁止访问内网网段：

```text
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
169.254.0.0/16
metadata service: 169.254.169.254
```

中期可以加入 egress proxy 和 allowlist。

### 20.2 Secret 策略

1. 默认不注入 GitHub token、OpenAI key、数据库密码等真实密钥。
2. `.env.example` 中的变量只生成 dummy 值。
3. 日志脱敏：token、password、secret、key、authorization header。
4. 私有仓库 token 只用于 clone，不进入执行容器。

### 20.3 命令策略

对高风险命令做标记：

```text
curl | sh
wget | sh
sudo
chmod 777
rm -rf /
docker run --privileged
mount
iptables
nc -e
```

不是全部禁止，但要降权、隔离或要求策略批准。

***

## 21. 和 CoderFleet / Claude Code / Codex 的结合方式

这个系统可以成为 AI 编程闭环中的“验证层”。

### 21.1 开发前

```text
Repo -> Runbook -> Verified Runtime
```

先确保 AI 编码前项目能跑起来，避免 Claude Code / Codex 在错误环境中开发。

### 21.2 开发中

```text
Issue -> 修改代码 -> Runbook 启动 -> 自动测试 -> 失败日志 -> 继续修复
```

每轮修改后，用 Verified Runbook 拉起项目，再执行在线测试。

### 21.3 PR 前

```text
生成 PR -> 自动运行在线测试 -> 报告通过 -> 提交 PR
```

如果失败，报告给修复 Agent，而不是直接提交。

### 21.4 作为 CoderFleet Worker 的任务类型

可以定义任务：

```json
{
  "task_type": "repo_runtime_test",
  "repo_url": "https://github.com/org/repo",
  "commit": "abc123",
  "mode": "analyze_verify_test",
  "output": {
    "runbook": true,
    "report": true,
    "generated_tests": true
  }
}
```

***

## 22. 示例端到端流程

输入：

```bash
autotest-agent run https://github.com/example/demo-app --commit abc123
```

输出过程：

```text
[1/8] Cloning repository
[2/8] Scanning files
[3/8] Found package.json, Dockerfile, README.md
[4/8] Generated 3 runbook candidates
[5/8] Trying candidate docker_compose_default
[6/8] Healthcheck failed: database not ready
[7/8] Applied repair: add wait strategy for postgres
[8/8] Verified runtime and executed 12 smoke tests
```

最终报告摘要：

```text
Repository: example/demo-app
Commit: abc123
Runtime: verified
Startup time: 48s
Detected stack: Node.js + Express + PostgreSQL
Verified URL: http://127.0.0.1:49152
Tests: 12 passed, 1 failed
Failure: POST /api/users missing email returned 500
Reproduce: curl -X POST ...
```

***

## 23. 推荐目录结构

```text
autotest-agent/
  apps/
    api/
      main.py
    worker/
      main.py
  autotest/
    cloner/
    profiler/
    evidence/
    runbook/
    executor/
    repair/
    discovery/
    testgen/
    report/
    security/
  schemas/
    runbook.schema.json
    profile.schema.json
    test-target.schema.json
  templates/
    prompts/
    reports/
  tests/
  examples/
```

***

## 24. 关键实现伪代码

### 24.1 主流程

```python
async def run_job(repo_url: str, commit: str | None):
    repo = await cloner.clone(repo_url, commit)

    profile = await profiler.analyze(repo.path)
    evidence = await signal_extractor.extract(repo.path, profile)

    candidates = await runbook_planner.plan(profile, evidence)

    for candidate in candidates:
        result = await verifier.verify(candidate)
        if result.verified:
            targets = await target_discoverer.discover(result.runtime, repo.path)
            tests = await test_generator.generate(targets, profile)
            report = await test_executor.run(result.runtime, tests)
            return await report_writer.write(profile, result, targets, tests, report)

        repaired = await repair_loop.try_repair(candidate, result, profile)
        if repaired.verified:
            targets = await target_discoverer.discover(repaired.runtime, repo.path)
            tests = await test_generator.generate(targets, profile)
            report = await test_executor.run(repaired.runtime, tests)
            return await report_writer.write(profile, repaired, targets, tests, report)

    return await report_writer.write_failure(profile, candidates)
```

### 24.2 健康检查

```python
async def healthcheck(runtime):
    candidate_urls = [
        "/health",
        "/api/health",
        "/ready",
        "/docs",
        "/openapi.json",
        "/",
    ]

    for port in runtime.detected_ports:
        for path in candidate_urls:
            url = f"http://127.0.0.1:{port}{path}"
            response = await try_get(url, timeout=3)
            if response and response.status_code < 500:
                return HealthcheckResult(passed=True, url=url, status=response.status_code)

    return HealthcheckResult(passed=False)
```

***

## 25. 风险与应对

| 风险           | 影响     | 应对                                 |
| ------------ | ------ | ---------------------------------- |
| 任意项目差异太大     | 成功率低   | 分语言/框架渐进支持                         |
| README 过时    | 运行失败   | CI/lockfile/执行验证优先                 |
| 项目依赖真实外部服务   | 无法启动   | mock service / dummy env / 标记需人工配置 |
| 恶意仓库         | 安全风险   | 沙箱、无 secret、网络限制、资源限制              |
| LLM 编造测试     | 测试无效   | 测试必须绑定 TestTarget                  |
| 启动成功但业务不可用   | 测试误判   | 增加强 Oracle 和日志分析                   |
| Monorepo 多应用 | 复杂度高   | 生成多个 subproject profile            |
| GUI/移动端项目    | 难以在线测试 | 第一阶段降级为 build/test                 |

***

## 26. 最小可落地版本建议

建议第一版做成一个 CLI + Worker：

```bash
autotest-agent analyze <repo>
autotest-agent verify runbook.yaml
autotest-agent test verified-runbook.yaml
autotest-agent run <repo>
```

第一版只要做到以下能力，就已经有很高价值：

1. 自动分析仓库。
2. 自动生成 runbook.yaml。
3. 自动在 Docker 沙箱中尝试启动。
4. 自动生成 verified-runbook.yaml。
5. 自动执行基础 smoke test。
6. 自动输出 report.md。

这会把“任意项目如何跑起来”这个不稳定的过程沉淀为结构化资产。

***

## 27. 最终结论

这个系统的本质不是“AI 自动写测试”，而是：

```text
把任意 GitHub 仓库转化为可启动、可验证、可测试、可复现的运行对象。
```

因此第一阶段的核心任务是：

```text
Repo Understanding + Runbook Generation + Sandbox Verification + Smoke Testing
```

后续再扩展到：

```text
API/UI 测试生成 + Repair Loop + PR 集成 + Agent 自动修复
```

最关键的工程判断是：

1. 不让 LLM 直接猜命令，而是让它基于证据生成候选 Runbook。
2. 不用自然语言保存运行步骤，而是使用结构化 Runbook。
3. 不相信静态分析结果，必须沙箱执行验证。
4. 不追求一开始理解业务，先做弱 Oracle 的在线测试。
5. 不把测试生成作为孤立能力，而是接到 Verified Runtime 后面。

这套方案非常适合接入你的 CoderFleet、多 Agent、Claude Code/Codex 工作流，成为 AI 编程系统中的“运行理解与验证层”。

***

## 28. 参考资料

1. GitHub Actions Workflow Syntax： <https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax>
2. Development Container Specification： <https://containers.dev/implementors/spec/>
3. Cloud Native Buildpacks Lifecycle Detect： <https://buildpacks.io/docs/for-platform-operators/concepts/lifecycle/detect/>
4. Testcontainers Wait Strategies： <https://java.testcontainers.org/features/startup_and_waits/>
5. Playwright Testing： <https://playwright.dev/docs/intro>

