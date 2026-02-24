# **🚀 一键快速部署（小白模式）**

> 适用系统：Ubuntu / Debian

> 前提条件：域名已解析到服务器 IP，80/443 端口开放


## **✅ 第一步：准备三个参数（先记下来）**

你需要准备好下面三个东西：

### **1️⃣ 域名（DOMAIN）**

例子：

```
tg.example.com
```

必须已经解析到你的 VPS。

------


### **2️⃣ Bot Token（BOT_TOKEN）**

在 BotFather 创建 Bot 后得到：

```
123456789:ABCdefGhIjKlmNoPQRstuVWxYZ
```

------


### **3️⃣ 你的 Telegram ID（OWNER_ID）**

用 @userinfobot 查询，例如：

```
987654321
```


------

## **✅ 第二步：拉代码**

登录服务器后执行：

```
sudo mkdir -p /opt
sudo chown -R $USER:$USER /opt

cd /opt
git clone https://github.com/annex1337/Telegram_chats_monitor.git tgbot
cd tgbot
```



------





## **✅ 第三步：运行一键部署脚本**





先给脚本权限：

```
chmod +x deploy.sh
```

然后运行（必须用 sudo）：

```
sudo ./deploy.sh
```





## **✅ 第四步：按提示复制粘贴参数**



运行后你会看到类似：

```
==============================
Config required:
1) DOMAIN
2) BOT_TOKEN
3) OWNER_ID
==============================
```

此时：

### **按顺序输入（直接粘贴）**

#### **输入域名：**

```
tg.example.com
```

回车

#### **输入 Bot Token：**



```
123456789:ABCdefGhIjKlmNoPQRstuVWxYZ
```

回车

#### **输入 Telegram ID：**

```
987654321
```

回车



⚠️ 不要加引号，不要加空格。



## **✅ 第五步：等待自动安装（5~15 分钟）**

脚本会自动完成：

- 安装 Node.js
- 安装 PM2
- 安装 Conda
- 创建 Python 环境
- 安装依赖
- 构建前端
- 配置 Nginx
- 申请 HTTPS 证书
- 启动服务



期间不要 Ctrl+C。

------



## **✅ 第六步：确认是否成功**



部署完成后运行：

```
pm2 ls
```

正常应看到：

```
tgbot   online
```

查看日志：

```
pm2 logs tgbot
```

没有报错即可。

------



## **✅ 第七步：配置 Telegram Mini App**



打开 BotFather：

1. 找到你的 Bot
2. 选择 Bot Settings
3. 设置 Mini App URL 为：

```
https://tg.example.com
```

（换成你自己的域名）

------



# ✅ 第八步：开启私信权限

否则读不到私聊。

操作：
	1.	打开 Telegram
	2.	进入：Settings → Telegram Business → Chatbots
	3.	添加你的 Bot Username
	4.	勾选：All 1-to-1 chats（也可以在这里选择仅对哪些用户生效）


⸻


## **✅ 第九步：使用系统**



在 Telegram 里：

- 打开你的 Bot
- 点击 Mini App
- 即可进入控制台



------

