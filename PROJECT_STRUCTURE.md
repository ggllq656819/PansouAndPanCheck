# PansouAndPanCheck 项目结构

## 概述

PansouAndPanCheck 是一个整合了 pansou 搜索服务和 PanCheck 链接有效性校验的代理服务，经过美化和重构后，项目结构更加清晰，代码更易维护。

## 文件结构

```
PansouAndPanCheck/
├── main.py                 # 应用入口和 Flask app 创建
├── auth.py                 # 认证路由、JWT 与鉴权中间件
├── proxy.py                # pansou 搜索与健康检查代理
├── pansou_auth.py          # 上游 pansou 登录、token 缓存和刷新
├── pancheck.py             # PanCheck 调用、链接检测与搜索结果过滤
├── config.py               # 配置管理模块
├── tests/                  # 基础单元测试
├── __init__.py             # Python包初始化文件
├── requirements.txt        # 项目依赖
├── Dockerfile              # Docker镜像构建文件
├── .env.example            # 环境变量配置示例
├── start.sh                # 启动脚本
├── README.md               # 项目说明文档
├── PROJECT_STRUCTURE.md    # 项目结构说明（当前文件）
└── test_syntax.py          # 语法检查脚本
```

## 主要改进

### 1. 代码结构优化
- **模块化设计**：将配置分离到独立的 [config.py](file:///F:/Workspace/PansouAndPanCheck/config.py) 文件
- **路由拆分**：认证、代理和链接检测分别放在独立模块中
- **函数拆分**：将大函数拆分为多个小函数，提高可读性
- **错误处理**：改进了异常处理机制

### 2. 可配置性增强
- **集中式配置**：所有配置项集中在 [Config](file:///F:/Workspace/PansouAndPanCheck/config.py#L6-L39) 类中管理
- **环境变量支持**：支持通过环境变量自定义配置
- **.env 支持**：本地运行会自动加载项目根目录下的 `.env`
- **配置验证**：添加了配置验证功能

### 3. 文档完善
- **详细README**：提供了完整的部署和使用说明
- **项目结构文档**：解释项目文件组织结构
- **函数文档**：为关键函数添加了详细的文档字符串

### 4. 安全性提升
- **非root用户**：Docker容器以非root用户运行
- **输入验证**：增强了输入参数验证
- **认证兼容**：补齐 pansou 认证接口，可按需启用 JWT 保护
- **错误隔离**：改进了错误处理，避免敏感信息泄露

### 5. 性能优化
- **连接复用**：优化了HTTP客户端使用
- **批量处理**：链接验证采用批量处理方式
- **内存效率**：减少了不必要的数据复制

## 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `SEARCH_API_URL` | `http://127.0.0.1:8888` | pansou 服务地址 |
| `CHECK_API_URL` | `http://127.0.0.1/api/v1/links/check` | PanCheck 服务校验接口地址 |
| `PORT` | `1566` | 应用运行端口 |
| `HOST` | `0.0.0.0` | 应用绑定主机地址 |
| `CLIENT_TIMEOUT` | `60.0` | HTTP客户端超时时间（秒） |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `DEBUG` | `false` | 是否开启调试模式 |
| `AUTH_ENABLED` | `false` | 是否启用认证 |
| `AUTH_USERS` | 空 | 认证用户，格式为 `user1:pass1,user2:pass2` |
| `AUTH_TOKEN_EXPIRY` | `24` | JWT 有效期（小时） |
| `AUTH_JWT_SECRET` | 空 | JWT 签名密钥 |
| `PANSOU_AUTH_ENABLED` | `false` | 是否启用上游 pansou 认证 |
| `PANSOU_AUTH_USERNAME` | 空 | 上游 pansou 登录用户名 |
| `PANSOU_AUTH_PASSWORD` | 空 | 上游 pansou 登录密码 |
| `PANSOU_AUTH_TOKEN` | 空 | 可选，上游 pansou 固定 token |
| `PANSOU_AUTH_LOGIN_URL` | 空 | 可选，自定义上游登录接口路径或完整 URL |
| `CHECK_LINKS_PASSTHROUGH_ENABLED` | `true` | `/api/check/links` 优先透传上游新版接口 |
| `CHECK_LINKS_FALLBACK_ENABLED` | `true` | 上游不可用时回退本地 PanCheck 检测 |
| `CHECK_LINKS_MAX_ITEMS` | `256` | 单次检测链接数量上限，与上游一致 |

启用认证时必须配置固定的 `AUTH_JWT_SECRET`，否则服务启动会失败，避免重启或多进程部署后 token 无法验证。

`AUTH_*` 用于保护本代理服务，`PANSOU_AUTH_*` 用于代理访问上游 pansou。启用上游认证时，配置用户名/密码后代理会自动登录并缓存 token，也支持直接配置固定 token。默认优先尝试 `/api/auth/login`，若返回 404 会自动回退到 `/api/login`；如你的部署使用了自定义路由，可设置 `PANSOU_AUTH_LOGIN_URL`。

### 支持的网盘平台

- `quark` - 夸克网盘
- `uc` - UC网盘
- `baidu` - 百度网盘
- `tianyi` - 天翼网盘
- `pan123` - 123云盘
- `pan115` - 115网盘
- `xunlei` - 迅雷网盘
- `aliyun` - 阿里云盘

## 部署方式

### Docker 部署

```bash
# 构建镜像
docker build -t pansou-and-pancheck .

# 运行容器
docker run -d -p 1566:1566 \
  --name pansou-and-pancheck \
  -e SEARCH_API_URL=http://your-pansou-url \
  -e CHECK_API_URL=http://your-check-url \
  pansou-and-pancheck
```

### 本地部署

```bash
# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export SEARCH_API_URL=http://your-pansou-url
export CHECK_API_URL=http://your-check-url

# 启动应用
gunicorn -k gevent -w 2 -b 0.0.0.0:1566 main:app
```

## API 接口

### 接口列表

- **POST** `/api/auth/login` - 认证登录，返回平铺 `{token, expires_at, username}`（与上游 pansou 一致）
- **POST** `/api/auth/verify` - 验证令牌，返回 `{valid, username}`
- **POST** `/api/auth/logout` - 登出，返回 `{message}`
- **POST** `/api/search` - 搜索网盘资源
- **GET** `/api/search` - 搜索网盘资源（查询参数形式，透传 `cloud_types`、`filter` 等新版参数）
- **POST** `/api/check/links` - 链接有效性检测，优先透传新版上游（支持 `proxy_url`、`view_token`），上游旧版/不可用时回退本地 PanCheck；响应均为平铺 `{results: [...]}`
- **GET** `/api/health` - 健康检查，透传上游 `liveness`、`tg` 字段
- **GET/POST** `/gying/<param>`、`/qqpd/<param>`、`/weibo/<param>`、`/panlian/<param>` - 上游插件 Web 管理页代理

## 日志说明

应用会输出详细的过滤统计日志，包括：
- 请求类型（GET/POST）
- 验证的链接总数
- 过滤前后各网盘类型的数据量变化
- 过滤耗时
