# frontend-asset-delivery Specification

## Purpose
定义 KnowTalk 前端页面的渲染前提：样式框架、图标字体与必需脚本由服务自身提供，使页面在没有外网的环境下也能完整渲染，并且本地资产可追溯、可复现。

## Requirements

### Requirement: 渲染框架资源由服务自身提供

前端页面的渲染框架资源（CSS 框架、图标字体、页面代码依赖的 JS 库）MUST 放在仓库内由主服务静态路由提供的目录下并在页面中以同源相对路径引用。页面 MUST NOT 依赖外网 CDN 才能获得完整布局、组件样式或图标。

#### Scenario: 在无外网环境下打开主控台

- **WHEN** 机器无法访问任何外部域名，浏览器打开主控台页面
- **THEN** 页面的栅格布局、组件样式与图标 MUST 与有外网时完全一致
- **AND** 浏览器控制台 MUST NOT 出现因外部资源加载失败导致的脚本或样式错误

#### Scenario: 页面引用样式与脚本

- **WHEN** 检查任一页面引用的 CSS 与 JS 地址
- **THEN** 每个地址 MUST 指向主服务自身提供的同源路径
- **AND** 该路径对应的文件 MUST 真实存在于仓库中

### Requirement: 本地资产必须记录版本与来源

`web/vendor/` 下每个第三方资源文件 MUST 在 `web/vendor/README.md` 中登记其名称、版本号、下载来源与许可证。升级版本时 MUST 同步更新该清单与页面中的引用。

#### Scenario: 新增一个第三方前端资源

- **WHEN** 向前端引入一个新的第三方库文件
- **THEN** 该文件的版本、来源 URL 与许可证被写入 `web/vendor/README.md`
- **AND** 页面引用的是本地路径而不是外网地址

#### Scenario: 核对资源可复现性

- **WHEN** 需要确认某个本地资产与上游发布版本一致
- **THEN** 依据清单中的来源 URL 与版本号可以重新获取同一份文件

### Requirement: 不得保留未被使用的第三方脚本引用

页面 MUST NOT 加载其代码从未调用的第三方脚本。发现遗留的无用引用时 MUST 删除该引用，而不是改为本地副本继续加载。

#### Scenario: 存在调用不到的外部脚本

- **WHEN** 某页面加载了一个第三方脚本，但该页面及其引入的脚本中从未调用该库的任何全局符号
- **THEN** 该 `<script>` 引用被删除
- **AND** 页面原有功能不受影响

#### Scenario: 明文 HTTP 的外部脚本

- **WHEN** 某页面通过 `http://` 加载外部脚本
- **THEN** 该引用被删除或改为同源本地路径
- **AND** 页面不再产生混合内容请求

### Requirement: 页面不请求外部字体服务

页面 MUST NOT 通过外链请求外部字体服务来决定排版。若需要特定字体，MUST 使用随页面提供的字体文件或已在本机存在的系统字体，并使字体栈在缺少该字体时可优雅回退。

#### Scenario: 打开管理后台

- **WHEN** 在无法访问外部字体服务的情况下打开管理后台页面
- **THEN** 页面文本 MUST 正常显示，使用字体栈中可用的系统字体
- **AND** 页面在渲染前 MUST NOT 等待任何外部字体请求

#### Scenario: 字体栈回退

- **WHEN** 页面样式声明的首选字体在本机不存在
- **THEN** 样式 MUST 声明可回退的通用字体族
- **AND** 页面版式不因缺字而错乱

### Requirement: 本地资源必须可被主服务访问

`web/vendor/` 下的资源 MUST 通过主服务已有的静态目录对外提供，MUST NOT 需要额外启动独立服务或修改部署方式。资源的响应 MUST 带正确的 `Content-Type`。

#### Scenario: 请求本地样式文件

- **WHEN** 浏览器请求主服务下的本地 Bootstrap 样式文件
- **THEN** 服务返回 200 且 `Content-Type` 为 `text/css`
- **AND** 无需额外配置即可获得该文件

#### Scenario: 请求图标字体文件

- **WHEN** 浏览器根据图标样式文件请求其中的字体文件
- **THEN** 服务返回 200 且内容为该字体文件本身
- **AND** 页面上以图标类名标记的元素显示为图标而不是空白

### Requirement: 服务根路径必须返回入口页面

`GET /` MUST 返回入口页面的完整内容，MUST NOT 返回目录请求被拒的响应。入口页面按运行模式选定（与现有 `opt.transport` 的映射一致），其内容 MUST 与通过该页面文件名直接访问所得一致。目录列举 MUST 保持关闭：除根路径外，对 `web/` 子目录的请求 MUST NOT 返回文件列表。不存在的静态路径 MUST 仍然返回未找到。

#### Scenario: 直接访问根路径

- **WHEN** 客户端请求 `GET /`（不带任何路径）
- **THEN** 响应状态码为 200，`Content-Type` 为 `text/html`
- **AND** 响应体与 `GET /index.html`（或当前运行模式对应的入口页面）一致
- **AND** 响应体 MUST NOT 是目录拒绝或文件列表

#### Scenario: 入口页面可直接访问

- **WHEN** 客户端请求 `GET /index.html`
- **THEN** 响应与改动前一致，行为 MUST NOT 因本变更改变

#### Scenario: 目录列举仍然关闭

- **WHEN** 客户端请求一个静态目录，例如 `GET /vendor/`
- **THEN** 响应 MUST NOT 是文件列表
- **AND** 根路径的放行 MUST NOT 被解释为"整个 `web/` 目录可列举"

#### Scenario: 不存在的静态路径仍然未找到

- **WHEN** 客户端请求一个不存在的静态文件
- **THEN** 响应为未找到，MUST NOT 回退到入口页面
