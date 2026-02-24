

# telegram-chat-monitor

Telegram 私聊信息存储，支持记录删除/编辑消息，内置 Mini App 管理界面，数据完全自托管，无第三方依赖。

---

## 核心理念

本项目坚持三条原则：

- 所有数据只存储在你自己的服务器
- 不接入任何第三方 API 或平台
- 全部源码开源，可自行审计

你拥有完整控制权。

---

## 核心功能

### ✅ 消息完整留存（含删除 / 编辑）

- 自动监听：
  - 私聊消息
  - Business 消息
  - 编辑事件
  - 删除事件
- 每条消息保留完整历史版本
- 防止重要记录丢失



![img](https://i.imgur.com/4xKr0J0.png)


---

### ✅ 内置 IM 控制台（Telegram 内访问）

- 基于 Telegram Mini App
- 仅通过 Telegram 官方入口访问
- 无外部网页登录
- 无账号系统
- 无额外认证流程

直接在 Telegram 内管理全部数据。

![img](https://i.imgur.com/OYY2A50.png)

---

### ✅ 会话与策略管理

- 支持：
  - 全局默认策略
  - 单会话覆盖策略
- 控制：
  - 是否记录
  - 是否通知
  - 是否归档

可精细控制每个会话的审计规则。




## 数据安全与隐私说明

### 📌 数据存储位置

所有数据仅存储于：

```text
你的 VPS / 物理服务器 / 私有云
```

默认路径：

```text
/opt/tgbot/data
```

项目不会：

- ❌ 上传任何数据
- ❌ 同步任何云端
- ❌ 连接外部数据库
- ❌ 调用第三方存储 API

---

### 📌 第三方依赖说明

本系统仅使用：

| 类型 | 来源 |
|------|------|
| 消息接口 | Telegram 官方 API |
| 前端机制 | Telegram Mini App |
| Web 服务 | 自建 FastAPI |


不存在数据外流通道。

---

### 📌 开源与可审计

- 所有核心代码完全开源
- 无加密模块
- 无混淆代码
- 无闭源组件

你可以：

- 自行审查源码
- 自行编译
- 自行部署
- 自行修改

不存在后门机制。

---

### 📌 网络行为说明

部署后，本系统仅产生以下外部通信：

| 目标 | 用途 |
|------|------|
| api.telegram.org | 接收/发送消息 |
| Let's Encrypt | 申请证书（可选） |

不与任何其他服务器通信。

---

## 系统结构

```text
Telegram → Bot → 本地存储 → WebSocket → Mini App
```

无中转节点。

---

## 技术栈

后端：

- Python 3.11
- FastAPI
- WebSocket

前端：

- Vue 3
- TypeScript

基础设施：

- Nginx
- PM2
- Conda
- HTTPS

全部可自行替换。

---

## 部署方式

推荐一键部署：

```bash
sudo ./deploy.sh
```

自动完成：

- 环境配置
- HTTPS
- 服务启动

详见 install.md。

---

## ## Bot 管理命令（推荐在无发搭建Miniapp的情况使用）



仅允许 OWNER 私聊使用。

---

### `/setrecord <target> <on|off>`
设置是否记录消息

```text
/setrecord all on
/setrecord 123456789 off
/setrecord @user on
```

---

### `/setnotify <target> <on|off>`
设置是否通知

```text
/setnotify all off
/setnotify @user on
```

---

### `/getpolicy <target>`
查看策略

```text
/getpolicy all
/getpolicy 123456789
/getpolicy @user
```

---

### `/clearoverride <target>`
清除单会话配置（恢复全局）

```text
/clearoverride 123456789
/clearoverride @user
```

---

### `/exportchat <target>`
导出聊天记录

```text
/exportchat 123456789
/exportchat @user
```

---

## 参数说明

### target 参数

```text
all         全局默认
peer_id     会话ID
@username   用户名
```

### 规则

- `all` 只影响默认策略
- 指定会话会覆盖全局
- Web 管理台修改不会触发 Bot 回复

## 安装
请参考[Install](https://github.com/annex1337/telegram-chat-monitor-Telegram-/blob/main/Install.md)

