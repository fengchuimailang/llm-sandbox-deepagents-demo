# llm-sandbox DeepAgents 多用户沙箱服务

多用户、多会话的代码执行服务，使用 `llm-sandbox` 作为轻量级沙箱后端，DeepAgents 作为 Agent 框架。

## 核心特性

| 特性 | 说明 |
|------|------|
| **会话隔离** | 每个 `thread_id` 对应独立 Sandbox Pool |
| **文件持久化** | bind mount 宿主机目录到容器 `/workspace` |
| **容器预热** | Container Pool 预创建容器，加速响应 |
| **多语言支持** | Python, Node.js, Go, Java, C++, Shell 等 |
| **DeepAgents 集成** | 自定义 `SandboxBackend` 实现 |
| **Docker 部署** | 主服务运行在 Docker 中，复用 docker.sock |

## 系统架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                           Host Machine                                 │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                    Docker Engine (daemon)                       │  │
│  │                                                                  │  │
│  │   ┌──────────────┐    ┌──────────────────────────────────┐   │  │
│  │   │  Sandbox     │    │       Sandbox Containers          │   │  │
│  │   │  Service     │    │  ┌────────┐ ┌────────┐ ┌──────┐ │   │  │
│  │   │  Container   │◄───┼─►│Pool 1  │ │Pool 2  │ │Pool N│ │   │  │
│  │   │              │    │  │/workspace│ │/workspace│ │/ws   │ │   │  │
│  │   │  FastAPI     │    │  └────────┘ └────────┘ └──────┘ │   │  │
│  │   │  + llm-     │    │       ▲           ▲       ▲      │   │  │
│  │   │  sandbox    │    │       │           │       │      │   │  │
│  │   │  PoolMgr    │    │  ┌────┴───────────┴───────┴────┐ │   │  │
│  │ └──────────────┘    │  │   /user_workspaces (bind)  │ │   │  │
│  │          │             │  │   /workspace (bind)         │ │   │  │
│  └──────────┼─────────────┼──┴────────────────────────────┴─┼──┘  │
│             │             └─────────────────────────────────────┼──┘  │
│             │                                                    │      │
│             └────────────────────────────────────────────────────┘      │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐     │
│  │ docker.sock  │  │  workspace/ │  │  user_workspaces/      │     │
│  │  (socket)   │  │  (volume)   │  │  (per-user volumes)    │     │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
llm-sandbox-deepagents-demo/
├── docker/
│   ├── Dockerfile.sandbox      # 自定义多语言沙箱镜像
│   └── Dockerfile.service      # FastAPI 服务镜像
├── llm_sandbox_deepagents_adapter/
│   ├── __init__.py
│   ├── llm_sandbox_backend.py  # 核心适配器 (SandboxBackendProtocol)
│   └── demos/
│       └── sandbox_service.py   # FastAPI 服务入口
├── tests/
│   ├── test_async.py           # 异步 API + 协议完整性测试
│   ├── test_concurrent_multi_user.py  # 多用户多线程并发测试
│   └── test_integration/       # DeepAgents 集成测试
│       ├── __init__.py
│       ├── conftest.py         # .env 加载、fixtures
│       ├── test_agent_execute.py  # 单轮执行测试
│       └── test_agent_multiturn.py # 多轮状态保持测试
├── workspace/                  # 共享工作空间 (bind mount)
├── user_workspaces/            # 用户隔离工作空间
├── docker-compose.yml          # 生产部署配置
├── pyproject.toml
├── env.example                 # 环境变量模板
├── EVALUATION_REPORT.md
└── README.md
```

## 快速开始

### 前置条件

```bash
# 1. 配置环境变量
cp env.example .env
# 编辑 .env 填入你的 API key
# 必须变量: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

# 2. 安装依赖
cd llm-sandbox-deepagents-demo
uv sync

# 3. 激活虚拟环境
source .venv/bin/activate
```

### 方式一: Docker Compose 部署 (推荐生产环境)

```bash
# 1. 构建自定义沙箱镜像
docker build -f docker/Dockerfile.sandbox -t llm-sandbox-multilang:latest ./docker

# 2. 启动所有服务
docker compose up -d

# 3. 检查服务状态
docker compose ps

# 4. 查看日志
docker compose logs -f sandbox-service
```

### 方式二: 开发模式

```bash
# 1. 启动 FastAPI 服务
python llm_sandbox_deepagents_adapter/demos/sandbox_service.py

# 2. 另一个终端运行压测
python tests/pressure_test.py --tests startup reuse
```

## Docker 部署详情

### 镜像说明

| 镜像 | 用途 | 基础镜像 |
|------|------|---------|
| `llm-sandbox-multilang` | 沙箱执行环境 | python:3.12-slim |
| `llm-sandbox-service` | FastAPI 服务 | python:3.12-slim |

### 容器说明

| 容器 | 数量 | 说明 |
|------|------|------|
| `sandbox-service` | 1 | 主 FastAPI 服务 |
| `sandbox-warmer` | 0-1 | 可选的容器预热服务 |

### 卷挂载

| 宿主机路径 | 容器路径 | 说明 |
|-----------|---------|------|
| `/var/run/docker.sock` | `/var/run/docker.sock` | Docker socket (必需) |
| `./workspace` | `/workspace` | 共享工作空间 |
| `./user_workspaces` | `/user_workspaces` | 用户隔离工作空间 |

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY` | - | **必需**。LLM API Key |
| `OPENAI_BASE_URL` | - | **必需**。LLM API Base URL |
| `OPENAI_MODEL` | - | **必需**。模型名称 |
| `SANDBOX_IMAGE` | `llm-sandbox-multilang:latest` | 沙箱容器镜像 |
| `SANDBOX_POOL_SIZE` | `20` | 最大容器池大小 |
| `SANDBOX_MIN_POOL` | `5` | 最小预热容器数 |
| `SANDBOX_IDLE_TIMEOUT` | `300` | 空闲容器回收时间(秒) |
| `SANDBOX_WORKSPACE` | `/workspace` | 工作空间路径 |
| `SANDBOX_DEFAULT_TIMEOUT` | `1800` | 默认执行超时(秒) |
| `SERVICE_PORT` | `8000` | 服务监听端口 |

### 安全配置

```yaml
# 沙箱容器安全限制
SANDBOX_NETWORK_MODE: none      # 禁用网络
SANDBOX_CAP_DROP: ALL           # 丢弃所有 Linux capabilities
SANDBOX_READ_ONLY: false        # 工作空间可写

# 服务资源限制
deploy:
  resources:
    limits:
      memory: 2G       # 服务容器内存上限
    reservations:
      memory: 512M     # 服务容器内存预留
```

## API 端点

| 方法 | 端点 | 描述 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/execute` | 执行代码/命令 |
| POST | `/write_file` | 写文件到 /workspace |
| POST | `/read_file` | 读取 /workspace 文件 |
| POST | `/upload_files` | 上传二进制文件 |
| POST | `/download_files` | 下载二进制文件 |
| GET | `/stats` | 服务统计 |
| DELETE | `/sandbox/{thread_id}` | 释放沙箱 |

## 测试架构

测试按从简单到复杂的层级进行，确保在完整 Docker 架构上验证。

### 测试文件

```
tests/
├── test_async.py                    # 异步 API + 协议完整性测试
├── test_concurrent_multi_user.py    # 多用户多线程并发测试
├── test_integration/               # DeepAgents 集成测试 (真实 LLM)
│   ├── conftest.py                 # .env 加载、fixtures
│   ├── test_agent_execute.py       # 单轮执行测试
│   └── test_agent_multiturn.py     # 多轮状态保持测试
└── pressure_test.py                 # 性能压力测试
```

### 运行测试

```bash
# 协议 + 异步 API 测试 (快，无需 LLM)
pytest tests/test_async.py -v

# 多用户并发测试 (快，无需 LLM)
pytest tests/test_concurrent_multi_user.py -v

# DeepAgents 集成测试 (慢，真实 LLM 调用)
# 需要先配置 .env (OPENAI_API_KEY 等)
pytest tests/test_integration/ -v -s

# 运行全部测试
pytest tests/ -v
```

### 测试前置条件

```bash
# 1. 构建沙箱镜像
docker build -f docker/Dockerfile.sandbox -t llm-sandbox-multilang:latest ./docker

# 2. 启动服务
docker compose up -d

# 3. 确认服务就绪
curl http://localhost:8000/health

# 4. 运行集成测试 (需配置 .env)
cp env.example .env
# 编辑 .env 填入 OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
pytest tests/test_integration/ -v
```

## 自定义配置

### 使用不同的沙箱镜像

```bash
# 构建自定义镜像
docker build -f docker/Dockerfile.sandbox -t my-custom-sandbox:latest ./docker

# 使用自定义镜像
SANDBOX_IMAGE=my-custom-sandbox:latest docker compose up -d
```

### 调整容器池大小

```bash
# 编辑 .env 文件
echo "SANDBOX_POOL_SIZE=50" >> .env
echo "SANDBOX_MIN_POOL=10" >> .env

# 重启服务
docker compose up -d
```

## 异步支持

### 异步方法

所有协议方法都有对应的 `a` 前缀异步版本：

```python
import asyncio
from llm_sandbox_deepagents_adapter import get_factory

async def demo_async():
    factory = get_factory()
    backend = factory.create_backend(pool_key="my-session")

    # 异步执行命令
    result = await backend.aexecute("print('hello async')")
    print(result.output)

    # 异步读写文件
    await backend.awrite("/workspace/test.txt", "Hello World")
    read_result = await backend.aread("/workspace/test.txt")
    print(read_result.file_data.content)

    # 异步编辑文件
    await backend.aedit("/workspace/test.txt", "World", "Async World")

    # 异步文件搜索
    grep_result = await backend.agrep("/workspace", "async")
    for match in grep_result.matches:
        print(f"{match.file}:{match.line}: {match.content}")

    backend.close()

asyncio.run(demo_async())
```

### 上下文管理器

支持 sync 和 async 上下文管理器：

```python
# Sync 上下文管理器
factory = get_factory()
with factory.create_backend() as backend:
    result = backend.execute("print('sync')")

# Async 上下文管理器
async with factory.create_backend() as backend:
    result = await backend.aexecute("print('async')")
```

### 错误分类与重试

内置错误分类和重试装饰器：

```python
from llm_sandbox_deepagents_adapter import (
    TimeoutError,
    ResourceExhaustedError,
    async_retry,
    classify_error,
)

@async_retry(max_attempts=3, base_delay=0.5)
async def unreliable_operation():
    result = await backend.aexecute("some_command")
    return result

try:
    result = backend.execute("command")
except Exception as e:
    error_type = classify_error(e)
    print(f"Got {error_type.__name__}: {e}")
```

### 监控指标

通过 `get_stats()` 获取执行统计：

```python
stats = backend.get_stats()
print(f"总执行次数: {stats['total_executions']}")
print(f"成功率: {stats['success_rate']}%")
print(f"平均执行时间: {stats['average_execution_time']}s")
print(f"超时次数: {stats['timeout_count']}")
print(f"资源耗尽次数: {stats['resource_exhausted_count']}")
```

## DeepAgents 集成

### 集成方式

使用 `create_deep_agent(backend=...)` 方式集成：

```python
import os
from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent
from llm_sandbox_deepagents_adapter import (
    LLMSandboxBackendConfig,
    get_factory,
)

# 1. 初始化 LLM
llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-4o"),
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)

# 2. 创建 backend (每个 thread_id 独立 pool)
config = LLMSandboxBackendConfig(
    enable_prewarming=True,
    default_timeout=30,
    idle_timeout=300.0,
    max_pool_size=5,
    min_pool_size=2,
)
factory = get_factory()
backend = factory.create_backend(pool_key="my-thread", config=config)

# 3. 创建 Agent (backend= 是正确方式)
agent = create_deep_agent(
    model=llm,
    backend=backend,     # ✅ 正确：用 backend= 参数
    debug=False,
)

# 4. 使用 agent
result = await agent.ainvoke(
    {"messages": ["Write fibonacci for n=10 and print the result"]},
    config={"configurable": {"thread_id": "my-thread"}},
)
print(result["messages"][-1].content)
```

### 会话隔离

`thread_id` 即 `pool_key`，确保不同用户/对话的 sandbox 完全隔离：

```python
# 用户 A 的会话
backend_a = factory.create_backend(pool_key="user-a-session-1")

# 用户 B 的会话
backend_b = factory.create_backend(pool_key="user-b-session-1")

# 各自独立 container pool，互不干扰
```

### 多用户多线程并发

多个用户并发使用时，每个用户的 thread_id 映射到独立 pool：

```python
async def handle_user(user_id: str, thread_id: str):
    factory = get_factory()
    backend = factory.create_backend(pool_key=thread_id)
    agent = create_deep_agent(model=llm, backend=backend)

    result = await agent.ainvoke(
        {"messages": [f"Hello from {user_id}"]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result

# 并发处理多个用户
await asyncio.gather(
    handle_user("alice", "alice-thread-1"),
    handle_user("bob", "bob-thread-1"),
    handle_user("charlie", "charlie-thread-1"),
)
```

## 性能基准

| 指标 | 值 |
|------|-----|
| 容器冷启动 | ~10s |
| 池复用加速 | 192x |
| 20 并发成功率 | 100% |
| 内存占用 (池) | ~50MB/容器 |

详细测试数据见 [EVALUATION_REPORT.md](EVALUATION_REPORT.md)。

## License

MIT
