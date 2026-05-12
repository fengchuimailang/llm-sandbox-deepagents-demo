# llm-sandbox DeepAgents 多用户沙箱服务

多用户、多会话的代码执行服务，使用 `llm-sandbox` 作为轻量级沙箱后端，DeepAgents 作为 Agent 框架。

## 核心特性

| 特性 | 说明 |
|------|------|
| **会话隔离** | 每个 `thread_id` 对应独立 Sandbox |
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
│  │   └──────────────┘    │  │   /user_workspaces (bind)  │ │   │  │
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
│   ├── demos/
│   │   └── sandbox_service.py   # FastAPI 服务入口
│   └── tests/
│       ├── test_protocol.py     # 协议完整性测试
│       └── test_multi_user.py  # 多用户并发测试
├── workspace/                  # 共享工作空间 (bind mount)
├── user_workspaces/            # 用户隔离工作空间
├── docker-compose.yml          # 生产部署配置
├── pyproject.toml
├── EVALUATION_REPORT.md
└── README.md
```

## 快速开始

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
# 1. 安装依赖
cd llm-sandbox-deepagents-demo
uv sync

# 2. 激活虚拟环境
source .venv/bin/activate

# 3. 启动 FastAPI 服务
python demos/sandbox_service.py

# 4. 另一个终端运行压测
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

### 测试层级

```
Level 1: 基础设施测试
├── docker-compose 能启动
├── 服务健康检查
└── 网络连通性

Level 2: API 端点测试
├── /health 健康检查
├── /execute 代码执行
└── /stats 统计接口

Level 3: 文件操作测试
├── /write_file 写文件
├── /read_file 读文件
├── /upload_files 上传
└── /download_files 下载

Level 4: 协议完整性测试
├── ls 文件列表
├── edit 文件编辑
├── grep 内容搜索
└── glob 模式匹配

Level 5: 多用户隔离测试
├── 用户间文件隔离
├── 用户间容器隔离
└── 并发请求隔离

Level 6: 持久化测试
├── 容器重启后文件保留
├── 容器复用后状态保留
└── bind mount 验证
```

### 测试文件

```
tests/
├── test_level1_infrastructure.py   # Level 1: 基础设施
├── test_level2_api.py              # Level 2: API 端点
├── test_level3_file_ops.py          # Level 3: 文件操作
├── test_level4_protocol.py          # Level 4: 协议完整性
├── test_level5_multi_user.py        # Level 5: 多用户隔离
├── test_level6_persistence.py       # Level 6: 持久化
└── pressure_test.py                 # 性能压力测试
```

### 运行测试

```bash
# Level 1: docker-compose 启动测试
pytest tests/test_level1_infrastructure.py -v

# Level 2: API 端点测试
pytest tests/test_level2_api.py -v

# Level 3: 文件操作测试
pytest tests/test_level3_file_ops.py -v

# Level 4: 协议完整性测试
pytest tests/test_level4_protocol.py -v

# Level 5: 多用户隔离测试
pytest tests/test_level5_multi_user.py -v

# Level 6: 持久化测试
pytest tests/test_level6_persistence.py -v

# 运行所有级别
pytest tests/test_level*.py -v

# 性能压力测试
python tests/pressure_test.py --tests startup reuse concurrent
```

### 测试前置条件

```bash
# 1. 构建沙箱镜像
docker build -f docker/Dockerfile.sandbox -t llm-sandbox-multilang:latest ./docker

# 2. 启动服务
docker compose up -d

# 3. 确认服务就绪
curl http://localhost:8000/health
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

除了同步方法外，backend 还支持异步方法，适合在 async 代码中使用：

```python
import asyncio
from llm_sandbox_deepagents_adapter import get_factory

async def demo_async():
    factory = get_factory()
    backend = factory.create_backend()
    
    # 异步执行命令
    result = await backend.async_execute("print('hello async')")
    print(result.output)
    
    # 异步读写文件
    await backend.async_write("/workspace/test.txt", "Hello World")
    read_result = await backend.async_read("/workspace/test.txt")
    print(read_result.file_data.content)
    
    # 异步编辑文件
    edit_result = await backend.async_edit(
        "/workspace/test.txt",
        "World",
        "Async World"
    )
    
    backend.close()

asyncio.run(demo_async())
```

### 上下文管理器

支持 sync 和 async 上下文管理器：

```python
# Sync 上下文管理器
with factory.create_backend() as backend:
    result = backend.execute("print('sync')")

# Async 上下文管理器
async with factory.create_backend() as backend:
    result = await backend.async_execute("print('async')")
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
    # 可能会超时或资源不足的操作
    result = await backend.async_execute("some_command")
    return result

# 手动分类错误
try:
    result = backend.execute("command")
except Exception as e:
    error_type = classify_error(e)
    print(f"Got {error_type.__name__}: {e}")
```

### 监控指标

通过 `get_stats()` 获取执行统计：

```python
# 获取统计信息
stats = backend.get_stats()
print(f"总执行次数: {stats['total_executions']}")
print(f"成功率: {stats['success_rate']}%")
print(f"平均执行时间: {stats['average_execution_time']}s")
print(f"超时次数: {stats['timeout_count']}")
print(f"资源耗尽次数: {stats['resource_exhausted_count']}")
```

## DeepAgents 集成

### 方式一: 直接作为 Tool 使用

```python
from deepagents import create_deep_agent
from llm_sandbox_deepagents_adapter import LLMSandboxBackend, LLMSandboxBackendFactory

# 获取共享的 backend
factory = get_factory()
backend = factory.create_backend(pool_key="default")

# 创建 Agent
agent = create_deep_agent(
    model=init_chat_model("openai:gpt-4o"),
    tools=[backend],  # 直接作为 Tool
)
```

### 方式二: 作为 SandboxBackend 使用

```python
from deepagents.backends.sandbox import BaseSandbox

class LLMSandboxBackendWrapper(BaseSandbox):
    def __init__(self, backend):
        self._backend = backend

    @property
    def id(self):
        return self._backend.id

    def execute(self, command, *, timeout=None):
        return self._backend.execute(command, timeout=timeout)

    # ... 实现其他协议方法
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