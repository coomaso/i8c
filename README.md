# i8 工作流待办监测 + 企业微信通知

自动监测 i8 工程企业管理软件的工作流待办任务，通过企业微信 Bot（WebSocket 长连接 / Webhook）发送通知提醒。

## 功能特性

- ✅ **自动登录** i8 系统（AES-128-ECB 加密，支持 Session 管理 + 强制踢下线重连）
- ✅ **定时监测** 工作流待办任务和预警消息
- ✅ **企业微信通知** 支持群机器人 Webhook 和智能机器人 WebSocket 长连接两种模式
- ✅ **去重提醒** 仅新任务出现或冷却期满时通知
- ✅ **自动重连** WebSocket 断线自动恢复
- ✅ **跨平台** 支持 Windows 计划任务 / Linux systemd / Linux cron

## 快速开始

### 1. 克隆并配置

```bash
git clone https://github.com/your-org/i8-workflow-monitor.git
cd i8-workflow-monitor
cp config.ini.example config.ini
vi config.ini    # 填入 i8 账号和微信 Bot 凭证
```

### 2. 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 运行测试

```bash
python i8_workflow_monitor.py
```

选择模式 `1` 执行一次性检测，确认登录和通知正常。

### 4. 服务器部署

#### 方案 A：systemd 服务（推荐）

```bash
sudo ./linux-deploy/install_linux.sh
# 选择 1 → systemd 服务
```

自动完成：目录创建 → 虚拟环境 → 依赖安装 → 服务注册 → 开机自启

#### 方案 B：Docker 部署

参见下方 Docker 说明。

## 企业微信通知配置

### WebSocket 长连接模式（推荐）

1. 在 [企业微信后台](https://work.weixin.qq.com/) 创建智能机器人，获取 `bot_id` 和 `secret`
2. 填入 `config.ini` 的 `[wecom]` 节
3. 首次运行后，在企业微信群中 **@机器人** 发任意消息，程序自动捕获群聊 ID

### Webhook 模式

1. 在企业微信群中添加群机器人，复制 Webhook URL
2. 设置 `mode = webhook` 并填入 `webhook_url`

## 目录结构

```
i8-workflow-monitor/
├── i8_workflow_monitor.py    # 主程序
├── config.ini                # 配置文件（已加入 .gitignore）
├── config.ini.example        # 配置模板（可提交）
├── requirements.txt          # Python 依赖
├── .gitignore
├── linux-deploy/
│   ├── install_linux.sh      # Linux 一键部署脚本
│   ├── i8-monitor.service    # systemd 服务文件
│   └── README-linux.md       # Linux 部署指南
├── install_task.bat          # Windows 计划任务脚本
└── README.md
```

## GitHub Actions 自动部署

推送 `main` 分支后，GitHub Actions 自动将代码部署到 Linux 服务器：

```yaml
# .github/workflows/deploy.yml
# 需在 GitHub Secrets 中配置：
#   SSH_HOST      - 服务器 IP
#   SSH_USER      - SSH 用户名
#   SSH_KEY       - SSH 私钥
#   CONFIG_INI    - 生产环境 config.ini 内容（Base64 编码）
```

## 许可证

MIT
