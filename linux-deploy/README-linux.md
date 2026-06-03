# i8 工作流待办监测 — Linux 部署指南

## 两种部署方式

### 方式一：一键部署脚本（推荐）

将整个项目目录上传到 Linux 服务器后执行：

```bash
# 1. 上传项目到服务器（从你的本地机器执行）
scp -r E:\Codeing\i8-workflow-monitor user@your-server:/tmp/

# 2. SSH 登录到服务器
ssh user@your-server

# 3. 赋予执行权限并运行安装脚本
chmod +x /tmp/i8-workflow-monitor/linux-deploy/install_linux.sh
sudo /tmp/i8-workflow-monitor/linux-deploy/install_linux.sh
```

### 方式二：手动部署

```bash
# 1. 创建目录
sudo mkdir -p /opt/i8-workflow-monitor

# 2. 拷贝文件
sudo cp i8_workflow_monitor.py /opt/i8-workflow-monitor/
sudo cp config.ini /opt/i8-workflow-monitor/
sudo cp requirements.txt /opt/i8-workflow-monitor/

# 3. 创建虚拟环境并安装依赖
cd /opt/i8-workflow-monitor
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 4. 安装 systemd 服务
sudo cp linux-deploy/i8-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable i8-monitor.service
sudo systemctl start i8-monitor.service

# 5. 收紧权限
sudo chmod 600 /opt/i8-workflow-monitor/config.ini
```

## 配置 WebSocket 模式

安装完成后：

1. **编辑配置**：`sudo vi /opt/i8-workflow-monitor/config.ini`
2. **检查 `target_chatid`**：留空则首次 @机器人 自动获取
3. **重启服务**：`sudo systemctl restart i8-workflow-monitor`

首次运行时，在企业微信群中 @机器人 发送任意消息，程序会自动捕获群聊 ID。

## 管理命令

```bash
# 服务状态
sudo systemctl status i8-workflow-monitor

# 查看实时日志
sudo journalctl -u i8-workflow-monitor -f

# 或查看日志文件
tail -f /opt/i8-workflow-monitor/monitor.log

# 重启服务
sudo systemctl restart i8-workflow-monitor

# 停止服务
sudo systemctl stop i8-workflow-monitor

# 卸载
sudo systemctl stop i8-workflow-monitor
sudo systemctl disable i8-workflow-monitor
sudo rm /etc/systemd/system/i8-monitor.service
sudo systemctl daemon-reload
sudo rm -rf /opt/i8-workflow-monitor
```

## 使用 cron 替代 systemd

如果不想使用 systemd，部署脚本中可选择 cron 模式（每 5 分钟执行一次一次性检测）。

手动配置 cron：

```bash
sudo crontab -e
# 添加以下行（每 5 分钟执行）
*/5 * * * * /opt/i8-workflow-monitor/run_once.sh
```
