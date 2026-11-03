# KnowTalking 网页端部署

网页端由 `app.py` 同源托管：`web/` 中的静态 H5 页面调用同一服务器上的 `/api/*`
和 `/offer`。浏览器不运行模型，只接收服务器输出的 WebRTC 音视频，因此应把 GPU、模型、
RAG 和主服务全部部署在服务器上。

## 访问模型

推荐让用户访问 `https://knowtalk.example.edu/`，而不是把 8010 直接暴露给校园网。

```text
浏览器 ── HTTPS :443 ──> Nginx/Caddy ── HTTP :8010 ──> app.py + GPU 模型
       └──────────── WebRTC 媒体（需要时经 TURN） ────────────┘
```

主服务本身已监听 `0.0.0.0:8010`，但反向代理应仅转发到 `127.0.0.1:8010`。防火墙只
对校园网开放 `443/tcp`；不要公开 RAG、模型服务或 8010。

## Nginx 示例

将域名和证书路径替换为学校实际分配的值。`/offer` 是普通 HTTP 信令接口，不是 WebSocket；
代理仍须保留较长超时，避免模型冷启动被误判为失败。

```nginx
server {
    listen 443 ssl http2;
    server_name knowtalk.example.edu;

    ssl_certificate     /etc/letsencrypt/live/knowtalk.example.edu/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/knowtalk.example.edu/privkey.pem;

    client_max_body_size 512m;

    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

`CORS_ALLOWED_ORIGINS` 无须配置：网页、API 和 `/offer` 都是同源请求。若把 H5 部署到
另一个域名，才需要把那个完整来源填入该变量。

## TURN：跨网访问的必要条件

当前项目内置的 STUN 只适合发现候选地址，不能保证跨校区、严格 NAT 或移动网络能建立
WebRTC 媒体通道。正式校园网部署应提供 TURN，例如 `coturn`：

- 为 TURN 配置固定域名、长期凭据或 time-limited credentials；
- 在防火墙放行 TURN 的 `3478/udp`、`3478/tcp` 和分配的 UDP 中继端口范围；
- 取得学校网络管理员批准后，在服务器 `.env` 填入 `WEBRTC_ICE_SERVERS`；网页端会在
  登录后从受保护的 `/api/web/rtc-config` 读取它，凭据不要提交到仓库。

示例（实际用户名、凭据和域名必须替换，且只保存于服务器 `.env`）：

```dotenv
WEBRTC_ICE_SERVERS=[{"urls":["turns:turn.example.edu:5349?transport=tcp"],"username":"temporary-user","credential":"temporary-secret"}]
```

未完成 TURN 前，先在“服务器与观看终端同一校园网段”做试运行；若浏览器显示“媒体通道
异常”，优先检查 TURN，而不是反复重启模型服务。

## 上线前检查

1. 用服务器的项目解释器启动 `app.py`，确认 `https://域名/api/status` 经登录后可访问。
2. 在另一台校园网电脑打开 `https://域名/`，首次部署创建管理员，随后重新登录验证 Cookie。
3. 选择形象并连接，确认音视频、菜单、字幕、全屏都正常。
4. 从另一网段或手机热点测试；若不能播放，再完成 TURN 配置后复测。
5. 确认服务器账号、TLS 私钥、API Key 和 TURN 凭据均只保留在服务器，不提交 Git。
