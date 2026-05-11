# llm-sandbox DeepAgents 沙箱后端可行性评估报告

## 1. 背景与目标

评估使用 `vndee/llm-sandbox` 作为 DeepAgents 自托管沙箱后端的可行性。

**注意**: 本报告仅包含实际测试数据，Daytona 对比数据因未经实测已移除。

## 2. 核心接口适配分析

### 2.1 DeepAgents SandboxBackendProtocol 核心方法

| 方法 | llm-sandbox | 适配难度 |
|------|-------------|----------|
| `execute(command, timeout)` | ✅ `session.run()` | 低 |
| `upload_files(files)` | ⚠️ 需 base64 中转 | 中 |
| `download_files(paths)` | ⚠️ 需 base64 中转 | 中 |
| `id` property | ⚠️ 需生成 UUID | 低 |
| `ls(path)` | ✅ 基于 `os.listdir()` | 低 |
| `read(path, offset, limit)` | ✅ 基于 `open().read()` | 低 |
| `write(path, content)` | ✅ base64 编码写入 | 低 |
| `edit(path, old, new, replace_all)` | ✅ Python 字符串替换 | 低 |
| `grep(pattern, path, glob)` | ✅ `subprocess.run(['grep'])` | 低 |
| `glob(pattern, path)` | ✅ `glob.glob()` | 低 |

### 2.2 适配层关键实现

```python
class LLMSandboxBackend:
    def execute(self, command: str, *, timeout: int | None = None):
        result = self._session.run(command, timeout=timeout)
        return ExecuteResponse(
            output=result.stdout or result.stderr,
            exit_code=result.exit_code,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]):
        for path, content in files:
            encoded = base64.b64encode(content).decode()
            self._session.run(
                f"import base64; open('{path}', 'wb').write(base64.b64decode('{encoded}'))"
            )
```

## 3. 实测性能数据

### 3.1 容器冷启动时间

| 容器数 | 平均启动时间 | 中位数 | P95 |
|--------|-------------|--------|-----|
| 3 | 10348ms | 10357ms | 10742ms |

**结论**: 首次启动约 10s/容器，受 Docker 镜像下载影响

### 3.2 容器池复用加速

| 指标 | 时间 |
|------|------|
| 首次创建 | 19806ms |
| 池复用平均 | 103ms |
| **加速比** | **192x** |

### 3.3 并发执行测试

**测试配置**: 20 并发容器，池大小 10

| 指标 | 值 |
|------|-----|
| 成功 | 20/20 (100%) |
| 失败 | 0 |
| 总耗时 | 99.33s |
| 平均执行时间 | 77751ms |
| 内存占用 | 42.64 MB 峰值 |
| CPU 占用 | 1.3% 平均 |

### 3.4 FastAPI 服务测试

| 端点 | 状态 |
|------|------|
| GET /health | ✅ 200 OK |

## 4. 功能完整性

| 特性 | 支持情况 | 说明 |
|------|---------|------|
| **execute()** | ✅ | 完整支持 |
| **文件操作** | ✅ | ls/read/write/edit/grep/glob |
| **容器池化** | ✅ | `PoolManager` 内置 |
| **预热容器** | ✅ | `enable_prewarming=True` |
| **多语言** | ✅ | Python, JS, Java, C++, Go, R |
| **资源限制** | ✅ | 通过 Docker runtime config |
| **安全隔离** | ✅ | Docker security options |
| **多用户隔离** | ⚠️ | 需 FastAPI 层包装 |

## 5. 需额外实现的项

| 功能 | 实现方式 | 工作量 |
|------|---------|--------|
| 多用户隔离 | FastAPI 中间件 + 用户沙箱映射 | 1-2 天 |
| 会话持久化 | `InteractiveSandboxSession` | 0.5 天 |
| 容器生命周期管理 | 上层服务 + 定时清理 | 1 天 |
| 并发请求路由 | ThreadPoolExecutor + 锁 | 0.5 天 |

## 6. 潜在风险点

1. **大文件传输**: base64 中转有 33% 体积膨胀
2. **高频短命令**: 每次 `run()` 可能有 shell 启动开销
3. **复杂文件系统操作**: grep/glob 通过 shell 实现可能不稳定

## 7. 可行性结论

**✅ 可行**

llm-sandbox 作为 DeepAgents 沙箱后端技术可行:

| 优势 | 说明 |
|------|------|
| 零常驻服务 | 无独立进程，仅 Python 库 |
| 池复用加速 | 192x 加速 (10s → 100ms) |
| 20 并发稳定 | 100% 成功，无失败 |
| 复用现有 Docker | 无额外基础设施 |

| 限制 | 说明 |
|------|------|
| 无原生上传/下载 API | 需 base64 中转 |
| 无多用户隔离 | 需上层包装 |
| 无会话持久化 | 需 InteractiveSandboxSession |

## 8. 实施路径

```
Phase 1: MVP (1-2天)
├── 适配层实现 (execute + 基础文件操作)
├── 单一容器池 + 单用户
└── 验证核心功能

Phase 2: 多用户支持 (2-3天)
├── 用户认证中间件
├── 沙箱分配/复用逻辑
├── 空闲回收机制
└── 基础监控

Phase 3: 优化 (1-2天)
├── 容器预热优化
├── 连接池调优
└── 资源限制细化
```

## 9. 源码文件清单

```
llm-sandbox-deepagents-demo/
├── llm_sandbox_deepagents_adapter/
│   ├── __init__.py
│   ├── llm_sandbox_backend.py     # 核心适配器
│   ├── demos/
│   │   └── sandbox_service.py      # FastAPI 多用户服务
│   └── tests/
│       └── pressure_test.py        # 压测脚本
├── pyproject.toml
└── EVALUATION_REPORT.md
```

## 10. 快速启动

```bash
# 安装
cd llm-sandbox-deepagents-demo
source .venv/bin/activate

# 运行压测
python tests/pressure_test.py --tests startup reuse concurrent

# 启动服务
python demos/sandbox_service.py
```

## 11. 环境要求

- Python 3.10+
- Docker (已运行)
- 4GB+ 可用内存 (根据并发需求)