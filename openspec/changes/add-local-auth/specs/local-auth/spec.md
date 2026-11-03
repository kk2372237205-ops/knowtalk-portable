# Spec Delta

## ADDED Requirements

### Requirement: 账号密码必须只以哈希形式保存

系统 MUST 把本地账号存在 `data/auth/users.json`，每条记录含用户名、显示名、盐、迭代次数、密码哈希。
密码 MUST 用 PBKDF2-HMAC-SHA256、不少于 200000 次迭代、每账号独立随机盐，MUST NOT 落明文或可逆密文。
账号文件 MUST 写在版本控制忽略的路径下。

#### Scenario: 创建首个管理员

- **WHEN** 系统内还没有任何账号，用户提交用户名、显示名与不少于 8 位密码
- **THEN** 系统 MUST 创建该账号并写入 `data/auth/users.json`
- **THEN** 该文件里 MUST NOT 出现该密码的明文

#### Scenario: 弱密码被拒绝

- **WHEN** 提交的密码短于 8 位
- **THEN** 系统 MUST 拒绝并把「至少 8 位」写在返回的提示里
- **THEN** MUST NOT 留下任何账号记录

### Requirement: 登录会话必须用签名 Cookie 表达

登录成功后系统 MUST 下发名为 `kt_session` 的 Cookie，带 `HttpOnly` 与 `SameSite=Lax`，
有效期 7 天。会话内容 MUST 只由签名密钥校验，MUST NOT 依赖服务端会话表，因此重启服务后仍有效。
签名密钥 MUST 保存在 `data/auth/secret.key`，首次使用时自动生成。修改密码 MUST 轮换该密钥，
使此前签发的所有会话立即失效。

#### Scenario: 重启后仍在线

- **WHEN** 用户登录成功后服务进程重启
- **THEN** 用同一浏览器再次访问页面 MUST 仍是已登录状态

#### Scenario: 改密踢掉旧会话

- **WHEN** 用户 A 在浏览器甲登录，随后在浏览器乙修改了自己的密码
- **THEN** 浏览器甲再访问受保护页面 MUST 被当作未登录

#### Scenario: 篡改 Cookie 无效

- **WHEN** 请求携带被改动过签名的 `kt_session`
- **THEN** 系统 MUST 判定为未登录，MUST NOT 抛出 500

### Requirement: 登录保护必须可开关且不阻断首次使用

系统内没有任何账号时，所有页面与接口 MUST 保持可访问，否则无法创建首个账号。
创建管理员时 MUST 提供「打开页面需要登录」选项，默认勾选，取值持久化在账号文件里。
该选项开启后，除 `/login.html`、`/api/auth/*` 与静态资源外，页面与接口 MUST 要求登录：
页面请求 MUST 302 到 `/login.html?next=<原路径>`，接口请求 MUST 返回 401 与 JSON 错误体。
选项关闭时 MUST NOT 拦截任何请求，登录仅用于表明身份。

#### Scenario: 未登录访问工作台被跳转

- **WHEN** 已开启登录保护，未登录用户打开 `/workbench.html`
- **THEN** MUST 302 到 `/login.html?next=/workbench.html`
- **THEN** 登录成功后 MUST 回到 `/workbench.html`

#### Scenario: 未登录调用接口

- **WHEN** 已开启登录保护，未登录用户 `POST /api/quiz/lecture`
- **THEN** MUST 返回 401，响应体 JSON 的 `code` MUST 为非 0
- **THEN** MUST NOT 执行该接口的任何业务逻辑

#### Scenario: 开放模式不拦人

- **WHEN** 账号已存在但「打开页面需要登录」为关闭
- **THEN** 未登录用户打开 `/knowledge-base.html` MUST 正常渲染
- **THEN** 顶栏 MUST 仍显示「未登录」

#### Scenario: 没有账号时不拦人

- **WHEN** 系统内没有任何账号
- **THEN** 打开任一页面 MUST 正常渲染
- **THEN** 页面 MUST 提供创建管理员的入口

### Requirement: 登录态必须在界面上可见

所有使用 `.site-bar` 的页面 MUST 在顶栏右侧显示当前登录态：已登录显示显示名，未登录显示「未登录」并链接到 `/login.html`。
工作台左栏底部 MUST 有登录态条目：未登录显示「未登录」与「去登录」；已登录显示显示名，
点开 MUST 提供「修改密码」与「退出登录」。退出登录 MUST 清掉会话并回到未登录态。

#### Scenario: 未登录看工作台左下角

- **WHEN** 未登录打开 `/workbench.html`
- **THEN** 左栏底部 MUST 显示「未登录」与一个可点的「去登录」
- **THEN** 顶栏右侧 MUST 同样显示「未登录」

#### Scenario: 登录后状态明显变化

- **WHEN** 用户登录后回到 `/workbench.html`
- **THEN** 左栏底部 MUST 显示该账号的显示名
- **THEN** 点开该条目 MUST 能看到「修改密码」与「退出登录」
- **THEN** 点「退出登录」后 MUST 回到「未登录」且顶栏同步

#### Scenario: 登录态跨页保持

- **WHEN** 已登录用户从工作台跳到数字人页、再跳到接口与参数
- **THEN** 两页顶栏 MUST 都显示同一个显示名

### Requirement: 密码必须可修改可重置

已登录用户 MUST 能凭旧密码修改自己的密码；旧密码错误时 MUST 拒绝。
忘记密码时 MUST 能用本机命令行 `python scripts/reset_password.py <用户名>` 重置：
脚本 MUST 接受新密码或自动生成一个，写入后 MUST 轮换签名密钥。
系统 MUST NOT 提供删除账号的接口，避免把唯一账号删掉后彻底进不去。

#### Scenario: 改密后旧密码失效

- **WHEN** 已登录用户把密码从 A 改成 B
- **THEN** 用 A 登录 MUST 失败
- **THEN** 用 B 登录 MUST 成功

#### Scenario: 旧密码不对不给改

- **WHEN** 已登录用户提交的旧密码与当前密码不符
- **THEN** MUST 返回失败并提示旧密码错误
- **THEN** 密码 MUST 保持不变

#### Scenario: 命令行重置

- **WHEN** 在服务器上执行 `python scripts/reset_password.py admin` 并给出新密码
- **THEN** 该账号密码 MUST 变成新密码
- **THEN** 此前签发的会话 MUST 全部失效

### Requirement: 微信扫码必须明确标注未开放

登录页 MUST 呈现「微信扫码登录」入口，且该入口 MUST 处于禁用态并注明「即将开放」。
点击它 MUST NOT 发起任何网络请求，MUST NOT 改变页面状态。
登录页 MUST NOT 出现暗示该能力可用的文案。

#### Scenario: 点击微信扫码

- **WHEN** 在 `/login.html` 点击「微信扫码登录」
- **THEN** MUST NOT 发出任何请求
- **THEN** 该按钮 MUST 仍是禁用态，页面上 MUST 有「即将开放」字样
