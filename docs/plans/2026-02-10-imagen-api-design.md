# Imagen API 设计文档

**日期：** 2026-02-10
**版本：** 1.0
**目标：** 将 Gemini Imagen 自动化流程工程化封装为兼容 OpenAI 的 HTTP REST API

---

## 一、整体架构

### 技术栈
- **FastAPI + Uvicorn** - Web 服务框架
- **Playwright** - 浏览器自动化
- **asyncio.Semaphore** - 并发控制
- **Pydantic** - 数据验证
- **python-dotenv** - 配置管理

### 目录结构
```
imagen-manager/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 应用入口
│   ├── config.py            # 配置类（Settings）
│   ├── auth.py              # API Key 中间件
│   ├── models.py            # Request/Response 模型
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py        # API 路由定义
│   ├── core/
│   │   ├── __init__.py
│   │   ├── browser.py       # 浏览器管理（Cookie、启动）
│   │   ├── generator.py     # 图片生成核心逻辑
│   │   └── semaphore.py     # 并发控制
│   └── utils/
│       ├── __init__.py
│       └── storage.py       # 文件存储、URL 生成
├── static/generated/        # 图片存储
├── data/cookies.json        # Google cookies
├── .env                     # 环境变量
├── pyproject.toml
└── README.md
```

### 职责分离
- **main.py** - FastAPI 应用初始化、中间件注册
- **browser.py** - Cookie 管理、浏览器实例生命周期
- **generator.py** - Gemini 自动化逻辑（导航、上传、生成、下载）
- **semaphore.py** - 全局信号量，控制最大并发数
- **storage.py** - 文件保存、清理策略、URL 生成

---

## 二、API 设计（兼容 OpenAI）

### 1. POST /v1/images/generations
生成新图片

**Request:**
```json
Headers: {"Authorization": "Bearer your-api-key"}
Body: {
  "prompt": "一只可爱的橘猫",
  "n": 1,                    // 可选，仅支持 1
  "size": "1024x1024",       // 可选，被忽略
  "response_format": "url"   // 可选，仅支持 "url"
}
```

**Response (200):**
```json
{
  "created": 1707566096,
  "data": [
    {
      "url": "http://localhost:8000/static/generated/img_abc123.png"
    }
  ]
}
```

### 2. POST /v1/images/edits
编辑现有图片

**Request:**
```
Headers: {"Authorization": "Bearer your-api-key"}
Body (multipart/form-data): {
  "image": <file>,           // PNG, 必需
  "prompt": "将猫改为黑色",
  "n": 1,
  "size": "1024x1024"
}
```

**Response:** 同 generations 接口

### 3. GET /v1/health
健康检查（无需认证）

**Response:**
```json
{
  "status": "ok",
  "concurrent_tasks": 2,
  "max_concurrent": 5
}
```

### 错误响应格式
```json
{
  "error": {
    "message": "错误描述",
    "type": "server_error",
    "code": "rate_limit_exceeded"
  }
}
```

### 兼容性说明
- ✅ 使用 `Authorization: Bearer` 认证
- ✅ 响应格式完全兼容 OpenAI
- ⚠️ `n` 参数仅支持 1
- ⚠️ `size` 参数会被忽略
- ⚠️ `response_format` 仅支持 "url"

---

## 三、并发控制与数据流

### 并发控制机制

使用 `asyncio.Semaphore` 实现全局并发限制（默认 5）：

```python
class ConcurrencyManager:
    def __init__(self, max_concurrent: int = 5):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_tasks = 0

    async def acquire(self):
        if self._semaphore.locked():
            raise HTTPException(429, detail={
                "error": {
                    "message": "Too many concurrent requests",
                    "type": "server_error",
                    "code": "rate_limit_exceeded"
                }
            })
        await self._semaphore.acquire()
        self._active_tasks += 1

    def release(self):
        self._semaphore.release()
        self._active_tasks -= 1
```

### 请求处理流程

```
Client Request
    ↓
API Key 验证 (middleware)
    ↓
请求参数验证 (Pydantic)
    ↓
尝试获取信号量 → [已满] → 429 错误
    ↓ [获取成功]
启动浏览器 + 加载 Cookies
    ↓
上传图片（如果是 edit 请求）
    ↓
提交 prompt 到 Gemini
    ↓
等待生成（60s timeout）
    ↓
下载图片到 static/generated/
    ↓
生成 URL 并返回
    ↓
释放信号量
    ↓
关闭浏览器
```

### 关键设计点
- 信号量在请求进入时立即检查（fail-fast）
- 使用 try-finally 确保信号量一定被释放
- 浏览器实例与请求生命周期绑定

---

## 四、错误处理与 Cookie 管理

### 错误处理策略

#### 1. Cookie 过期
- **检测：** 登录验证失败
- **响应：** 503 Service Unavailable
```json
{
  "error": {
    "message": "Service temporarily unavailable: Google cookies expired",
    "type": "service_error",
    "code": "cookies_expired"
  }
}
```

#### 2. 生成超时
- **检测：** 超过 timeout 参数
- **响应：** 504 Gateway Timeout
```json
{
  "error": {
    "message": "Image generation timed out after 60 seconds",
    "type": "timeout_error",
    "code": "generation_timeout"
  }
}
```

#### 3. 图片上传失败
- **检测：** 无法找到上传按钮
- **处理：** 返回 400 Bad Request，或尝试继续生成（不带参考图）

#### 4. 浏览器崩溃
- **检测：** Playwright 异常
- **处理：** 释放信号量，返回 500 Internal Server Error

### Cookie 管理

```python
class CookieManager:
    def __init__(self, cookies_path: str):
        self.cookies_path = Path(cookies_path)
        self._cookies_cache = None
        self._last_load_time = None

    def load_cookies(self) -> list:
        """加载并转换 cookies（带 5 分钟缓存）"""
        if self._cookies_cache and (time.time() - self._last_load_time < 300):
            return self._cookies_cache

        with open(self.cookies_path) as f:
            raw_cookies = json.load(f)

        self._cookies_cache = self._convert_cookies(raw_cookies)
        self._last_load_time = time.time()
        return self._cookies_cache
```

**设计要点：**
- Cookie 文件读取带缓存，避免频繁磁盘 I/O
- Cookie 过期需手动更换文件并重启服务
- 所有错误符合 OpenAI 格式

---

## 五、文件存储与配置管理

### 文件存储策略

```python
class ImageStorage:
    def save_image(self, source_path: Path) -> tuple[str, str]:
        """保存图片，返回 (url, file_path)"""
        # 文件命名：img_<timestamp>_<random>.png
        filename = f"img_{int(time.time())}_{secrets.token_hex(8)}.png"
        dest_path = self.storage_dir / filename

        shutil.move(source_path, dest_path)
        url = f"{self.base_url}/static/generated/{filename}"
        return url, str(dest_path)

    def cleanup_old_files(self, max_age_hours: int = 24):
        """清理超过指定时间的文件"""
        cutoff = time.time() - (max_age_hours * 3600)
        for file in self.storage_dir.glob("img_*.png"):
            if file.stat().st_mtime < cutoff:
                file.unlink()
```

### 配置管理

```python
# app/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # API 配置
    api_key: str
    host: str = "0.0.0.0"
    port: int = 8000

    # 并发控制
    max_concurrent_tasks: int = 5

    # 生成配置
    default_timeout: int = 60
    proxy: str | None = "http://127.0.0.1:7897"

    # 存储配置
    storage_dir: Path = Path("./static/generated")
    cleanup_hours: int = 24

    # Cookie 配置
    cookies_path: Path = Path("./data/cookies.json")

    class Config:
        env_file = ".env"
```

### .env 示例
```bash
API_KEY=sk-your-secret-key-here
MAX_CONCURRENT_TASKS=5
DEFAULT_TIMEOUT=60
PROXY=http://127.0.0.1:7897
CLEANUP_HOURS=24
```

### 自动清理机制
- 启动时执行一次清理
- 可选：后台定时任务每小时清理一次

---

## 六、部署与运行

### 依赖管理

```toml
# pyproject.toml
[project]
name = "imagen-api"
version = "1.0.0"
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "playwright>=1.41.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "python-multipart>=0.0.6",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "httpx>=0.26.0",
    "pytest-asyncio>=0.23.0",
]
```

### 启动命令

```bash
# 安装依赖
uv sync
uv run playwright install chromium

# 开发模式（热重载）
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 生产模式
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

### 注意事项
- ⚠️ 生产环境建议 `--workers 1`，因为并发控制基于进程内信号量
- 如需多 worker，需使用 Redis 等外部存储共享信号量状态

### Docker 部署（可选）

```dockerfile
FROM python:3.11-slim
RUN pip install uv
WORKDIR /app
COPY . .
RUN uv sync
RUN uv run playwright install --with-deps chromium
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0"]
```

### 健康检查与监控
- `/v1/health` 端点返回当前并发任务数
- 建议配合日志监控 Cookie 过期、生成失败等错误
- 可选：集成 Prometheus metrics

---

## 七、测试策略

### 测试层次

#### 1. 单元测试
- `CookieManager.load_cookies()` - Mock 文件读取
- `ImageStorage.save_image()` - 测试文件命名和移动
- `convert_cookies()` - 测试格式转换逻辑

#### 2. 集成测试
- API Key 验证中间件
- 并发限制（同时发送 6 个请求，验证第 6 个返回 429）
- 文件上传解析

#### 3. 端到端测试（可选）
- 需要真实 cookies，测试完整生成流程
- 建议在 CI 中跳过，仅本地手动测试

### 测试示例

```python
# tests/test_api.py
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_generate_without_auth():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/v1/images/generations",
            json={"prompt": "test"})
        assert response.status_code == 401

@pytest.mark.asyncio
async def test_concurrent_limit():
    async with AsyncClient(app=app, base_url="http://test") as client:
        tasks = [
            client.post("/v1/images/generations",
                json={"prompt": f"test {i}"},
                headers={"Authorization": "Bearer test-key"})
            for i in range(6)
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        assert any(r.status_code == 429 for r in responses if hasattr(r, 'status_code'))
```

### Mock 策略
- 测试时 Mock `generator.py` 的 Playwright 调用
- 返回预先准备的测试图片文件

---

## 总结

本设计提供了一个完整的、兼容 OpenAI API 的图片生成服务架构：

✅ **兼容性** - 可直接使用 OpenAI Python SDK
✅ **并发控制** - 信号量限制浏览器实例，防止资源耗尽
✅ **错误处理** - 完善的错误类型和符合 OpenAI 格式的响应
✅ **配置灵活** - 环境变量管理，易于部署
✅ **自动清理** - 定期清理旧图片，避免磁盘占满
✅ **可测试** - 清晰的模块划分，便于单元测试和集成测试
