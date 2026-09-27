# PansouAndPanCheck

一个整合了 [pansou](https://github.com/Silent1566/pansou) 搜索服务和 [PanCheck](https://github.com/Lampon/PanCheck) 链接有效性校验的代理服务。

## 功能特性

- 自动过滤无效的网盘链接，只返回有效链接
- 支持多种网盘平台（夸克、UC、百度、天翼、123云盘、115网盘、迅雷、阿里云等）
- 提供与新版 pansou 完全兼容的 API 接口
- 认证接口响应格式与上游 pansou 一致（平铺 `token`/`expires_at`/`username`）
- `/api/check/links` 优先透传新版上游 pansou（支持 `proxy_url`、`view_token` 等参数），上游为旧版或不可用时自动回退本地 PanCheck 检测，回退响应格式也对齐上游平铺 `results` 结构
- 代理上游 pansou 插件 Web 管理页（`/gying/...`、`/qqpd/...`、`/weibo/...`、`/panlian/...`）
- 支持 POST 和 GET 请求方式
- 详细的过滤统计日志

## 部署方式

### Docker 部署（推荐）

```bash
docker run -d -p 1566:1566 \
  --name pansou-and-pancheck \
  -e SEARCH_API_URL=http://192.168.50.50:1514 \
  -e CHECK_API_URL=http://192.168.50.50:7024/api/v1/links/check \
  silent7897/pansou-and-pancheck:latest
```

### 环境变量说明

- `SEARCH_API_URL`：pansou 服务地址（默认值：http://127.0.0.1:8888）
- `CHECK_API_URL`：PanCheck 服务校验接口地址（默认值：http://127.0.0.1/api/v1/links/check），仅回退检测时使用
- `AUTH_ENABLED`：是否启用认证（默认值：false）
- `AUTH_USERS`：认证用户，格式为 `user1:pass1,user2:pass2`
- `AUTH_TOKEN_EXPIRY`：JWT 有效期，单位小时（默认值：24）
- `AUTH_JWT_SECRET`：JWT 签名密钥，生产环境建议显式配置固定强随机字符串
- `PANSOU_AUTH_ENABLED`：是否启用上游 pansou 认证（默认值：false）
- `PANSOU_AUTH_USERNAME`：上游 pansou 登录用户名
- `PANSOU_AUTH_PASSWORD`：上游 pansou 登录密码
- `PANSOU_AUTH_TOKEN`：可选，上游 pansou 固定 token；配置后优先使用，不再自动登录
- `PANSOU_AUTH_LOGIN_URL`：可选，自定义上游登录接口路径或完整 URL；默认优先尝试 `/api/auth/login`，其次回退 `/api/login`
- `CHECK_LINKS_PASSTHROUGH_ENABLED`：`/api/check/links` 是否优先透传上游新版接口（默认值：true）
- `CHECK_LINKS_FALLBACK_ENABLED`：上游不可用时是否回退本地 PanCheck 检测（默认值：true）
- `CHECK_LINKS_MAX_ITEMS`：单次检测链接数量上限，与上游一致（默认值：256）

启用认证时必须配置 `AUTH_USERS` 和固定的 `AUTH_JWT_SECRET`。本地运行会自动加载项目根目录下的 `.env` 文件。

`AUTH_*` 用于保护本代理服务，`PANSOU_AUTH_*` 用于本代理访问上游 pansou。上游 pansou 开启认证时，推荐配置 `PANSOU_AUTH_ENABLED=true`、`PANSOU_AUTH_USERNAME`、`PANSOU_AUTH_PASSWORD`，代理会自动登录并缓存 token；默认兼容 `/api/auth/login` 和旧版 `/api/login`，上游返回 401 时会自动刷新一次。若你的部署使用了自定义登录路由，可额外设置 `PANSOU_AUTH_LOGIN_URL`。

### 本地部署

```bash
git clone https://github.com/your-repo/pansou-and-pancheck.git
cd pansou-and-pancheck
pip install -r requirements.txt
SEARCH_API_URL=http://your-pansou-url CHECK_API_URL=http://your-check-url python main.py
```

生产环境建议使用 gunicorn：

```bash
gunicorn -k gevent -w 2 -b 0.0.0.0:1566 main:app
```

## API 接口

- `POST /api/auth/login`：认证登录，返回平铺格式 `{token, expires_at, username}`（与上游 pansou 一致）
- `POST /api/auth/verify`：验证 JWT 是否有效，返回 `{valid, username}`
- `POST /api/auth/logout`：登出，返回 `{message}`（客户端丢弃 JWT）
- `POST /api/search`：搜索网盘资源
- `GET /api/search`：搜索网盘资源，完整透传 pansou 查询参数（含新版 `cloud_types`、`filter` 等参数）
- `POST /api/check/links`：链接有效性检测，优先透传新版上游（支持 `items[].disk_type/url/password`、`proxy_url`/`proxy`、`view_token`）；上游旧版/不可用时回退本地 PanCheck，两种模式响应均为平铺 `{results: [...]}`，`state` 取值 `ok/bad/locked/unsupported/uncertain`
- `GET /api/health`：健康检查，透传上游 `liveness`、`tg` 等新增字段，并覆盖 `auth_enabled` 为本代理实际状态
- `GET/POST /gying/<param>`、`/qqpd/<param>`、`/weibo/<param>`、`/panlian/<param>`：上游 pansou 插件 Web 管理页代理

启用认证后，除 `/api/auth/login`、`/api/auth/logout`、`/api/health` 外，其他接口都需要携带 `Authorization: Bearer <token>`，认证失败返回上游兼容格式 `{error, code: "AUTH_TOKEN_*"}`。

## 项目链接

- [pansou](https://github.com/Silent1566/pansou)
- [pansou-web](https://github.com/fish2018/pansou-web)
- [PanCheck](https://github.com/Lampon/PanCheck)

## Star History

[<image-card alt="Star History Chart" src="https://api.star-history.com/svg?repos=Silent1566/PansouAndPanCheck&type=Date" ></image-card>](https://star-history.com/#Silent1566/PansouAndPanCheck&Date)

<!-- 或者更推荐带暗色适配的写法 -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Silent1566/PansouAndPanCheck&type=Date&theme=dark" />
  <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Silent1566/PansouAndPanCheck&type=Date" />
  <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=Silent1566/PansouAndPanCheck&type=Date" />
</picture>

