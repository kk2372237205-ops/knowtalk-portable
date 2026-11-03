# 获取 KnowTalking 服务器部署信息

下面的信息不含密码、API Key 或证书私钥。拿到后把结果（敏感字段打码）发给我，即可确定
校园网的实际部署方案。

## 先向学校或服务器管理员确认

1. 服务器系统：Windows Server 还是 Linux（Ubuntu / CentOS / 其他）？
2. 服务器是否有校园网固定 IP；该 IP 是否只能校内访问，还是带公网 IP？
3. 是否可以申请一个域名，例如 `knowtalk.xxx.edu.cn`？HTTPS 证书由谁签发？
4. 防火墙可否开放 `443/tcp` 给校园网终端？WebRTC 是否允许 UDP 中继端口？
5. 是否允许部署 coturn；如允许，能否开放 `3478/udp`、`3478/tcp` 与一段 UDP 中继端口？
6. 预计同时观看人数。当前单 GPU 数字人服务应先按**单会话展示**评估，不能直接假设可
   承载多人并发独立数字人。

> 校园网内先试运行可以只使用校内 IP；要获得稳定的跨网 WebRTC 和浏览器 HTTPS，域名、
> 证书与 TURN 仍是正式上线所需条件。

## Linux 服务器：在 SSH 中执行

```bash
hostnamectl
uname -a
ip -br address
nvidia-smi
free -h
df -h /
ss -lntup | head -n 30
```

若服务器允许访问公网，以下命令可帮助区分出口公网 IP（不是密码）：

```bash
curl -4 https://api.ipify.org ; echo
```

把输出中 IP 的最后一段打码即可，例如 `10.12.34.xxx`。不要发送 `/etc/ssl`、`.env`、
`~/.ssh` 或任何 token 内容。

## Windows 服务器：在 PowerShell 中执行

```powershell
Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsArchitecture
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*'} |
  Select-Object InterfaceAlias, IPAddress, PrefixLength
nvidia-smi
Get-Volume | Select-Object DriveLetter, SizeRemaining, Size
Get-NetTCPConnection -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess
```

如果允许访问公网：

```powershell
Invoke-RestMethod https://api.ipify.org
```

## 校园网可达性测试

在一台与服务器不同的校园网电脑上，临时测试服务端口是否可达（把地址替换为服务器 IP）：

```powershell
Test-NetConnection 10.12.34.56 -Port 8010
```

这一步只用于部署前诊断。正式版本应关闭对外的 8010，改由 Nginx/Caddy 对外提供
HTTPS 443；然后在校园网电脑浏览器打开 `https://你的域名/`。
