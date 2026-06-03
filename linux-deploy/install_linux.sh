#!/bin/bash
# =============================================
# i8 工作流待办监测系统 - Linux 部署脚本
# 支持: systemd 服务模式 / cron 定时模式
# =============================================

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
INSTALL_DIR="/opt/i8-workflow-monitor"
SERVICE_NAME="i8-workflow-monitor"

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  i8 工作流待办监测系统 - Linux 部署${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ── 检查 Python3 ──
echo -e "${YELLOW}[1/6] 检查 Python 环境...${NC}"
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✗ 未找到 python3，请先安装:${NC}"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv"
    echo "  CentOS/RHEL:   sudo yum install python3 python3-pip"
    exit 1
fi
PYTHON=$(command -v python3)
echo -e "${GREEN}✓ Python: $($PYTHON --version)${NC}"

# ── 创建安装目录 ──
echo -e "${YELLOW}[2/6] 创建安装目录...${NC}"
sudo mkdir -p "$INSTALL_DIR"
echo -e "${GREEN}✓ 目录: $INSTALL_DIR${NC}"

# ── 拷贝文件 ──
echo -e "${YELLOW}[3/6] 拷贝项目文件...${NC}"
sudo cp "$PROJECT_DIR/i8_workflow_monitor.py" "$INSTALL_DIR/"
sudo cp "$PROJECT_DIR/config.ini" "$INSTALL_DIR/"
sudo cp "$PROJECT_DIR/requirements.txt" "$INSTALL_DIR/"
if [ -f "$PROJECT_DIR/.monitor_state.json" ]; then
    sudo cp "$PROJECT_DIR/.monitor_state.json" "$INSTALL_DIR/"
fi
echo -e "${GREEN}✓ 文件已复制到 $INSTALL_DIR${NC}"

# ── 创建 Python 虚拟环境 ──
echo -e "${YELLOW}[4/6] 创建 Python 虚拟环境...${NC}"
cd "$INSTALL_DIR"
sudo $PYTHON -m venv venv
sudo ./venv/bin/pip install --upgrade pip -q
sudo ./venv/bin/pip install -r requirements.txt -q
echo -e "${GREEN}✓ 虚拟环境已创建，依赖已安装${NC}"

# ── 配置选择 ──
echo ""
echo -e "${YELLOW}[5/6] 选择运行模式...${NC}"
echo "  1) systemd 服务 (开机自启，持续监测)"
echo "  2) cron 定时任务 (每 5 分钟执行一次)"
echo "  3) 跳过，稍后手动配置"
echo ""
read -r -p "请选择 [1/2/3] (默认 1): " MODE_CHOICE
MODE_CHOICE=${MODE_CHOICE:-1}

case "$MODE_CHOICE" in
    1)
        # ── systemd 服务 ──
        echo -e "${YELLOW}  配置 systemd 服务...${NC}"

        sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=i8 Workflow Task Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/i8_workflow_monitor.py
Restart=always
RestartSec=10
StandardOutput=append:$INSTALL_DIR/monitor.log
StandardError=append:$INSTALL_DIR/monitor.log

[Install]
WantedBy=multi-user.target
EOF

        sudo systemctl daemon-reload
        sudo systemctl enable ${SERVICE_NAME}.service
        sudo systemctl restart ${SERVICE_NAME}.service
        echo -e "${GREEN}✓ systemd 服务已安装并启动${NC}"
        echo ""
        echo -e "  管理命令:"
        echo -e "    ${YELLOW}sudo systemctl status ${SERVICE_NAME}${NC}    # 查看状态"
        echo -e "    ${YELLOW}sudo systemctl restart ${SERVICE_NAME}${NC}   # 重启"
        echo -e "    ${YELLOW}sudo journalctl -u ${SERVICE_NAME} -f${NC}    # 查看日志"
        ;;

    2)
        # ── cron 定时任务 ──
        echo -e "${YELLOW}  配置 cron 定时任务...${NC}"

        # 创建 cron wrapper 脚本（一次性运行模式）
        sudo tee "$INSTALL_DIR/run_once.sh" > /dev/null <<EOF
#!/bin/bash
cd $INSTALL_DIR
$INSTALL_DIR/venv/bin/python $INSTALL_DIR/i8_workflow_monitor.py <<< "1" >> $INSTALL_DIR/monitor.log 2>&1
EOF
        sudo chmod +x "$INSTALL_DIR/run_once.sh"

        # 添加 crontab (每 5 分钟)
        CRON_JOB="*/5 * * * * $INSTALL_DIR/run_once.sh"
        (sudo crontab -l 2>/dev/null | grep -v "$INSTALL_DIR/run_once.sh"; echo "$CRON_JOB") | sudo crontab -
        echo -e "${GREEN}✓ cron 定时任务已添加 (每 5 分钟)${NC}"
        echo ""
        echo -e "  管理命令:"
        echo -e "    ${YELLOW}sudo crontab -l${NC}    # 查看定时任务"
        echo -e "    ${YELLOW}sudo crontab -e${NC}    # 编辑定时任务"
        ;;

    *)
        echo -e "${YELLOW}  已跳过，可稍后手动配置。${NC}"
        ;;
esac

# ── 安全设置 ──
echo -e "${YELLOW}[6/6] 安全设置...${NC}"
sudo chmod 600 "$INSTALL_DIR/config.ini"   # 配置文件仅 root 可读
if [ -f "$INSTALL_DIR/.monitor_state.json" ]; then
    sudo chmod 600 "$INSTALL_DIR/.monitor_state.json"
fi
echo -e "${GREEN}✓ 配置文件权限已收紧${NC}"

# ── 完成 ──
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  ✅ 部署完成！${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "  项目路径: $INSTALL_DIR"
echo "  配置文件: $INSTALL_DIR/config.ini"
echo "  日志文件: $INSTALL_DIR/monitor.log"
echo ""
echo -e "${YELLOW}  重要提醒:${NC}"
echo "  1. 请检查 config.ini 中的配置是否正确"
echo "  2. 如使用 WebSocket 模式，首次需在企业微信群 @机器人"
echo "  3. 日志查看: tail -f $INSTALL_DIR/monitor.log"
echo ""
