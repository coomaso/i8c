#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
i8 工作流待办任务监测 + 企业微信 Bot 提醒
=========================================
功能：
  1. 自动登录 i8 工程企业管理软件
  2. 定时查询工作流待办任务
  3. 检测到新待办时通过企业微信发送通知
  4. 支持 Webhook 模式和 WebSocket 长连接模式
  5. 避免重复提醒（可配置冷却时间）
  6. 支持 Windows 计划任务 / Linux cron 两种运行模式

依赖安装：
  pip install requests pycryptodome websocket-client

配置方法：
  编辑 config.ini 填入 i8 账号及企业微信通知配置
"""

import os
import sys
import json
import time
import uuid
import logging
import hashlib
import re
import configparser
import urllib.parse
import base64
import threading
from datetime import datetime
from typing import Optional, Dict, Set

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# ──────────────────────────────────────────────
# 配置加载
# ──────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")
    cfg.read(CONFIG_PATH, encoding="utf-8")
    return cfg

CONFIG = load_config()

# ──────────────────────────────────────────────
# 日志
# ──────────────────────────────────────────────
LOG_FILE = CONFIG.get("monitor", "log_file", fallback="monitor.log")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOG_FILE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,
)
# 修复 Windows 终端 UTF-8 编码问题
for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

logger = logging.getLogger("i8_monitor")


# ══════════════════════════════════════════════
# Part 1: i8 系统登录与任务查询
# ══════════════════════════════════════════════

class I8Auth:
    """i8 系统登录认证，处理 AES-128-ECB 加密及会话管理。"""

    BASE_URL = f"http://{CONFIG.get('i8', 'host')}:{CONFIG.get('i8', 'port')}"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        self.dh_id: Optional[str] = None
        self.authorization: Optional[str] = None
        self.token_login: Optional[str] = None
        self.logged_in = False

    @staticmethod
    def _parse_extjs_response(text: str) -> dict:
        """解析 i8 系统的 ExtJS 风格响应，如 { success: false, msg:'{...}' }"""
        result = {"success": False, "msg": "", "realuserid": "", "userNo": ""}
        m = re.search(r"success\s*[:\=]\s*(true|false)", text, re.IGNORECASE)
        if m:
            result["success"] = m.group(1).lower() == "true"
        m = re.search(r"""msg\s*[:\=]\s*'(.*?)'\s*[,\}]""", text, re.DOTALL)
        if m:
            val = m.group(1)
            try:
                result["msg"] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                result["msg"] = val
        for field in ["realuserid", "userNo"]:
            m = re.search(rf"{field}\s*[:\=]\s*'(.*?)'\s*[,}}]", text, re.DOTALL)
            if m:
                result[field] = m.group(1)
        return result

    @staticmethod
    def _aes_encrypt(key_str: str, plaintext: str) -> str:
        """AES-128-ECB PKCS7 加密，返回 Base64。"""
        key = key_str.encode("utf-8")
        cipher = AES.new(key, AES.MODE_ECB)
        padded = pad(plaintext.encode("utf-8"), AES.block_size)
        return base64.b64encode(cipher.encrypt(padded)).decode()

    def _get_login_page(self) -> bool:
        """获取登录页面，提取 Session Cookie 和 dhId。"""
        try:
            resp = self.session.get(f"{self.BASE_URL}/i8/web/", timeout=15)
            if resp.status_code == 302:
                redirect = resp.headers.get("Location", "")
                if redirect:
                    resp = self.session.get(f"{self.BASE_URL}{redirect}", timeout=15)
            match = re.search(r"var dhId = '(\d+)'", resp.text)
            if match:
                self.dh_id = match.group(1)
                logger.info(f"获取 dhId: {self.dh_id}")
                return True
            logger.error("未在页面中找到 dhId")
            return False
        except requests.RequestException as e:
            logger.error(f"访问登录页失败: {e}")
            return False

    def _get_ucode(self) -> Optional[str]:
        """根据客户号获取企业代码。"""
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/Portal.mvc/GetUCodeByCustomerCode",
                params={"code": CONFIG.get("i8", "customer_code")},
                timeout=10,
            )
            data = resp.json()
            if data.get("errorCode") == 200:
                account = data["data"]["account"]
                logger.info(f"客户号 {CONFIG.get('i8', 'customer_code')} → 企业代码: {account}")
                return account
            logger.error(f"获取企业代码失败: {data}")
            return None
        except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
            logger.error(f"获取企业代码异常: {e}")
            return None

    def login(self) -> bool:
        """执行 i8 系统登录，含强制踢下线处理。"""
        if not self._get_login_page():
            return False

        account = self._get_ucode()
        if not account:
            return False

        customer_code = CONFIG.get("i8", "customer_code")
        username = CONFIG.get("i8", "username")
        password = CONFIG.get("i8", "password")
        ocode = CONFIG.get("i8", "ocode", fallback="660029")

        userid_b64 = base64.b64encode(
            urllib.parse.quote(username, safe="").encode("utf-8")
        ).decode()
        pwd_b64 = base64.b64encode(password.encode("utf-8")).decode()

        send_body = {
            "ocode": ocode,
            "UserID": userid_b64,
            "UserPwd": pwd_b64,
            "DataBase": account,
            "CustomerCode": customer_code,
            "IsOnlineCheck": "",
            "Language": "zh-CN",
            "verifyCode": "",
            "loginModel": 0,
            "phone": "",
            "phoneCode": "",
            "IsTwoFactorLogin": True,
        }

        def _post(body: dict) -> tuple:
            bj = json.dumps(body, separators=(",", ":"))
            enc = self._aes_encrypt(self.dh_id, bj)
            r = self.session.post(
                f"{self.BASE_URL}/SUP/Login/WebLogin",
                data={"body": enc}, timeout=15,
            )
            return r, self._parse_extjs_response(r.text.strip())

        resp, result = _post(send_body)

        # 处理强制踢下线
        if not result.get("success") and isinstance(result.get("msg"), dict):
            msg_body = result["msg"]
            if "强行清退" in msg_body.get("Message", ""):
                logger.warning("账号在其他地方登录，尝试强制踢下线...")
                send_body["IsOnlineCheck"] = "1"
                resp, result = _post(send_body)
                if result.get("success"):
                    logger.info("强制踢下线成功，已重新登录")

        if result.get("success"):
            self.authorization = resp.headers.get("authorization", "")
            self.token_login = resp.headers.get("tokenlogin", "")
            self.session.headers.update({
                "Authorization": self.authorization,
                "TokenLogin": self.token_login,
            })
            self.logged_in = True
            logger.info(f"i8 登录成功！用户: {username}")
            return True

        logger.error(f"i8 登录失败: {result.get('msg', '未知错误')}")
        return False

    def check_session(self) -> bool:
        """检查 session 有效性，失效则重新登录。"""
        if not self.logged_in:
            return self.login()
        try:
            resp = self.session.get(f"{self.BASE_URL}/i8/web/", timeout=10)
            if resp.status_code == 200 and "dhId" in resp.text:
                return True
        except requests.RequestException:
            pass
        logger.warning("Session 已失效，重新登录...")
        return self.login()


class I8TaskFetcher:
    """查询 i8 工作流待办和预警消息。"""

    PENDING_API = f"{I8Auth.BASE_URL}/WorkFlow3/FlowManager/GetPendingTaskByUser"
    ALERT_API = f"{I8Auth.BASE_URL}/ScheduleJob/ScheduleJob/GetMyAlertMsgList"

    def __init__(self, auth: I8Auth):
        self.auth = auth

    def get_pending_tasks(self) -> Optional[Dict]:
        if not self.auth.check_session():
            return None
        try:
            resp = self.auth.session.get(
                self.PENDING_API, params={"rows": 100, "page": 1}, timeout=15,
            )
            data = resp.json()
            logger.info(f"工作流待办: totalRows={data.get('totalRows', 0)}")
            return data
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.error(f"查询待办失败: {e}")
            return None

    def get_alert_messages(self) -> Optional[Dict]:
        if not self.auth.check_session():
            return None
        try:
            resp = self.auth.session.get(self.ALERT_API, timeout=15)
            data = resp.json()
            logger.info(f"预警消息: totalRows={data.get('totalRows', 0)}")
            return data
        except (requests.RequestException, json.JSONDecodeError) as e:
            logger.error(f"查询预警消息失败: {e}")
            return None


# ══════════════════════════════════════════════
# Part 2: 企业微信通知 (Webhook + WebSocket)
# ══════════════════════════════════════════════

class WeComNotifier:
    """统一通知接口，根据 mode 自动选择 Webhook 或 WebSocket。"""

    def __init__(self):
        self.mode = CONFIG.get("wecom", "mode", fallback="webhook").strip().lower()
        self._webhook = None
        self._ws = None

    def start(self):
        """启动通知客户端。"""
        if self.mode == "websocket":
            self._ws = WeComWSClient()
            self._ws.start()
            logger.info("企业微信 WebSocket 长连接已启动")
        else:
            self._webhook = WeComWebhook()

    def stop(self):
        """停止通知客户端。"""
        if self._ws:
            self._ws.stop()

    @property
    def is_configured(self) -> bool:
        if self.mode == "websocket":
            return bool(CONFIG.get("wecom", "bot_id", fallback=""))
        return bool(CONFIG.get("wecom", "webhook_url", fallback=""))

    def send_notification(self, content: str) -> bool:
        """发送通知消息。content 为 Markdown 格式文本。"""
        if self.mode == "websocket":
            return self._ws.send_markdown(content) if self._ws else False
        return self._webhook.send_markdown(content)


class WeComWebhook:
    """企业微信群机器人 Webhook 模式。"""

    def __init__(self):
        self.webhook_url = CONFIG.get("wecom", "webhook_url", fallback="").strip()

    def send_markdown(self, content: str) -> bool:
        if not self.webhook_url:
            logger.warning("企业微信 Webhook URL 未配置")
            logger.info(f"[待发送]\n{content}")
            return False
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            result = resp.json()
            if result.get("errcode") == 0:
                logger.info("Webhook 消息发送成功")
                return True
            logger.error(f"Webhook 发送失败: {result}")
            return False
        except requests.RequestException as e:
            logger.error(f"Webhook 请求异常: {e}")
            return False


class WeComWSClient:
    """
    企业微信智能机器人 WebSocket 长连接客户端。

    流程：
      1. 连接 wss://openws.work.weixin.qq.com
      2. 发送 aibot_subscribe 鉴权
      3. 定时 ping 保活
      4. 接收消息回调，自动学习 chatid
      5. 通过 aibot_send_msg 主动推送通知
    """

    WS_URL = "wss://openws.work.weixin.qq.com"
    HEARTBEAT_INTERVAL = 30  # 秒

    def __init__(self):
        self.bot_id = CONFIG.get("wecom", "bot_id", fallback="")
        self.secret = CONFIG.get("wecom", "secret", fallback="")
        self.target_chatid = CONFIG.get("wecom", "target_chatid", fallback="").strip()
        self.chat_type = CONFIG.getint("wecom", "chat_type", fallback=2)

        self._ws = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_timer: Optional[threading.Timer] = None
        self._subscribed = threading.Event()
        self._known_chatids: Dict[str, int] = {}  # chatid -> chat_type
        self._conn_lock = threading.Lock()
        self._req_id_counter = 0

    def _next_req_id(self) -> str:
        self._req_id_counter += 1
        return f"{int(time.time() * 1000)}_{self._req_id_counter}"

    # ── 连接管理 ──

    def start(self):
        """在后台线程启动 WebSocket 客户端。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止 WebSocket 客户端。"""
        self._running = False
        self._cancel_heartbeat()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _run_loop(self):
        """WebSocket 主循环，支持自动重连。"""
        retry_delay = 1
        while self._running:
            try:
                self._connect_and_serve()
            except Exception as e:
                logger.error(f"WebSocket 连接异常: {e}")
            if not self._running:
                break
            logger.info(f"等待 {retry_delay}s 后重连...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)
        self._subscribed.clear()

    def _connect_and_serve(self):
        """建立连接并进入消息循环，导入 websocket 在内部。"""
        import websocket as _ws_lib

        self._ws = _ws_lib.WebSocketApp(
            self.WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        logger.info("正在连接企业微信 WebSocket...")
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    # ── 回调处理 ──

    def _on_open(self, ws):
        logger.info("WebSocket 已连接，发送鉴权...")
        self._send_subscribe()

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        cmd = data.get("cmd", "")
        headers = data.get("headers", {})
        body = data.get("body", {})
        errcode = data.get("errcode", -1)

        if cmd == "aibot_subscribe" or "req_id" in (headers or {}):
            if errcode == 0:
                logger.info("企业微信 Bot 鉴权成功")
                self._subscribed.set()
                self._start_heartbeat()
                # 如果配置了 target_chatid，发送测试通知
                if self.target_chatid:
                    logger.info(f"目标会话已配置: {self.target_chatid}")
            else:
                logger.error(f"鉴权失败: errcode={errcode}, errmsg={data.get('errmsg','')}")

        elif cmd == "aibot_msg_callback":
            self._handle_msg_callback(body)

        elif cmd == "pong":
            # 心跳响应，无需处理
            pass

        elif errcode == 0 and cmd == "aibot_send_msg":
            # 消息发送成功响应
            pass

    def _on_error(self, ws, error):
        logger.error(f"WebSocket 错误: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info(f"WebSocket 连接关闭 (code={close_status_code})")
        self._subscribed.clear()
        self._cancel_heartbeat()

    # ── 消息发送 ──

    def _send_json(self, data: dict) -> bool:
        """通过 WebSocket 发送 JSON 消息。"""
        if not self._ws:
            return False
        try:
            self._ws.send(json.dumps(data, ensure_ascii=False))
            return True
        except Exception as e:
            logger.error(f"WebSocket 发送失败: {e}")
            return False

    def _send_subscribe(self):
        """发送鉴权订阅消息。"""
        req_id = self._next_req_id()
        msg = {
            "cmd": "aibot_subscribe",
            "headers": {"req_id": req_id},
            "body": {
                "bot_id": self.bot_id,
                "secret": self.secret,
            },
        }
        self._send_json(msg)

    def _send_ping(self):
        """发送心跳。"""
        if not self._subscribed.is_set():
            return
        msg = {"cmd": "ping", "headers": {"req_id": self._next_req_id()}}
        self._send_json(msg)

    def _start_heartbeat(self):
        """启动定时心跳。"""
        self._cancel_heartbeat()

        def _heartbeat():
            if not self._running:
                return
            self._send_ping()
            if self._running:
                self._heartbeat_timer = threading.Timer(self.HEARTBEAT_INTERVAL, _heartbeat)
                self._heartbeat_timer.daemon = True
                self._heartbeat_timer.start()

        self._heartbeat_timer = threading.Timer(self.HEARTBEAT_INTERVAL, _heartbeat)
        self._heartbeat_timer.daemon = True
        self._heartbeat_timer.start()

    def _cancel_heartbeat(self):
        if self._heartbeat_timer:
            self._heartbeat_timer.cancel()
            self._heartbeat_timer = None

    def _handle_msg_callback(self, body: dict):
        """处理收到的用户消息，从中学习 chatid。"""
        chatid = body.get("chatid", "")
        chattype = body.get("chattype", "")
        from_user = body.get("from", {}).get("userid", "")
        content = body.get("text", {}).get("content", "")
        msgid = body.get("msgid", "")

        # 学习 chatid
        if chatid:
            ct = 2 if chattype == "group" else 1
            self._known_chatids[chatid] = ct
            logger.info(f"学习到群聊: chatid={chatid}")

            # 如果未配置 target_chatid，自动使用第一个学到的群聊
            if not self.target_chatid and ct == 2:
                self.target_chatid = chatid
                self.chat_type = ct
                logger.info(f"自动设置目标会话: {chatid}")

        if from_user:
            logger.info(f"收到消息: from={from_user} chattype={chattype}")

    def send_markdown(self, content: str) -> bool:
        """主动推送 Markdown 消息。需要事先获取到 target_chatid。"""
        chatid = self.target_chatid
        if not chatid:
            # 尝试从已学习的 chatid 中选一个群聊
            for cid, ct in self._known_chatids.items():
                if ct == 2:
                    chatid = cid
                    self.target_chatid = chatid
                    break
        if not chatid:
            logger.warning(
                "未指定 target_chatid，且未收到过群聊消息。\n"
                "请在企业微信群中 @机器人 发送任意消息，或手动在 config.ini 中配置 target_chatid。"
            )
            logger.info(f"[待发送内容]\n{content}")
            return False

        # 等待鉴权完成
        if not self._subscribed.wait(timeout=10):
            logger.error("WebSocket 未就绪，跳过消息发送")
            return False

        # 截断内容（企业微信限制 20480 字节）
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > 20000:
            content = content_bytes[:20000].decode("utf-8", errors="ignore") + "\n\n...（内容过长已截断）"

        req_id = self._next_req_id()
        msg = {
            "cmd": "aibot_send_msg",
            "headers": {"req_id": req_id},
            "body": {
                "chatid": chatid,
                "chat_type": self.chat_type,
                "msgtype": "markdown",
                "markdown": {"content": content},
            },
        }

        logger.info(f"主动推送消息到 chatid={chatid}")
        return self._send_json(msg)


# ══════════════════════════════════════════════
# Part 3: 任务监测引擎
# ══════════════════════════════════════════════

class TaskMonitor:
    """待办任务监测引擎，管理状态、去重、通知。"""

    STATE_FILE = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".monitor_state.json",
    )

    def __init__(self):
        self.auth = I8Auth()
        self.fetcher = I8TaskFetcher(self.auth)
        self.notifier = WeComNotifier()
        self.last_task_ids: Set[str] = set()
        self.last_notify_time: float = 0
        self.remind_interval = CONFIG.getint("wecom", "remind_interval", fallback=3600)
        self._load_state()

    def _load_state(self):
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.last_task_ids = set(state.get("task_ids", []))
                self.last_notify_time = state.get("notify_time", 0)
                logger.info(f"加载状态: {len(self.last_task_ids)} 个历史任务 ID")
        except (json.JSONDecodeError, IOError):
            pass

    def _save_state(self):
        try:
            state = {
                "task_ids": list(self.last_task_ids),
                "notify_time": self.last_notify_time,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"保存状态失败: {e}")

    @staticmethod
    def _extract_task_ids(data: Dict) -> Set[str]:
        ids = set()
        for rec in data.get("Record", []):
            tid = rec.get("id_") or rec.get("phid") or rec.get("task_id") or ""
            if tid:
                ids.add(str(tid))
            else:
                biz = rec.get("proc_inst_id_", "") + rec.get("name_", "")
                if biz:
                    ids.add(hashlib.md5(biz.encode()).hexdigest())
        return ids

    def _build_notification(self, task_data: Dict, alert_data: Optional[Dict] = None) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total = task_data.get("totalRows", 0)
        alerts = alert_data.get("totalRows", 0) if alert_data else 0

        lines = [
            f"## 🔔 i8 待办任务提醒",
            f"> 更新时间：{now}",
            f"> 企业：湖北盛荣建设集团有限公司",
            f"> 用户：{CONFIG.get('i8', 'username')}",
            "",
            f"**工作流待办**: {total} 条" + (f" | **预警消息**: {alerts} 条" if alerts else ""),
            "",
            "---",
        ]

        records = task_data.get("Record", [])
        if records:
            lines.append(f"当前共有 **{total}** 条待办任务：\n")
            for i, rec in enumerate(records[:20], 1):
                name = rec.get("name_") or rec.get("msg") or "(无标题)"
                parts = [f"{i}. **{name}**"]
                proc_name = rec.get("proc_def_name_") or rec.get("cd_NAME_") or ""
                if proc_name:
                    parts[0] += f" [{proc_name}]"
                starter = rec.get("start_user_name_") or ""
                create_time = rec.get("create_time_") or ""
                detail_parts = []
                if starter:
                    detail_parts.append(f"发起人: {starter}")
                if create_time:
                    detail_parts.append(f"时间: {create_time}")
                if detail_parts:
                    parts.append("   " + " | ".join(detail_parts))
                lines.append("\n".join(parts))
            if total > 20:
                lines.append(f"\n...及另外 {total - 20} 条待办")
        else:
            lines.append("✅ 暂无待办任务。")

        return "\n".join(lines)

    def run_once(self) -> bool:
        logger.info("=" * 50)
        logger.info("开始待办任务检测...")

        if not self.auth.check_session():
            logger.error("登录失败，跳过检测")
            return False

        task_data = self.fetcher.get_pending_tasks()
        if task_data is None:
            return False

        alert_data = self.fetcher.get_alert_messages()

        total = task_data.get("totalRows", 0)
        current_ids = self._extract_task_ids(task_data)
        new_ids = current_ids - self.last_task_ids
        now = time.time()

        logger.info(
            f"待办: {total} | 历史: {len(self.last_task_ids)} | 新增: {len(new_ids)}"
        )

        should_notify = False
        reason = ""

        if total > 0 and new_ids:
            should_notify = True
            reason = f"检测到 {len(new_ids)} 条新待办"
        elif total > 0 and (now - self.last_notify_time) >= self.remind_interval:
            should_notify = True
            reason = f"定时提醒（距上次通知 > {self.remind_interval // 60} 分钟）"
        elif total == 0 and CONFIG.getboolean("monitor", "notify_on_empty", False) and not self.last_task_ids:
            should_notify = True
            reason = "首次启动，暂无待办"

        if should_notify:
            content = self._build_notification(task_data, alert_data)
            if total > 0:
                content = "**@所有人**\n\n" + content

            logger.info(f"发送通知（原因: {reason}）")
            self.notifier.start()
            time.sleep(1)  # 等待 WebSocket 就绪
            success = self.notifier.send_notification(content)
            if success:
                self.last_notify_time = now
                logger.info("通知已发送")
            else:
                logger.warning("通知发送失败")
        else:
            logger.info("跳过通知: " + ("无新增" if total > 0 else "暂无待办"))

        # 下次检测前断开 WebSocket
        self.notifier.stop()

        self.last_task_ids = current_ids if total > 0 else set()
        self._save_state()
        logger.info("检测完成。")
        return True

    def run_loop(self):
        """持续监测模式。"""
        interval = CONFIG.getint("monitor", "check_interval", fallback=300)
        logger.info("i8 待办监测服务启动")
        logger.info(f"检测间隔: {interval}s | 提醒间隔: {self.remind_interval}s")
        self.run_once()
        while True:
            time.sleep(interval)
            self.run_once()


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

def run_once_and_exit():
    """执行一次检测后退出（Windows 计划任务模式）。"""
    monitor = TaskMonitor()
    monitor.run_once()


def main():
    print("=" * 60)
    print("  i8 工作流待办任务监测 + 企业微信 Bot 提醒")
    print("=" * 60)

    # 检查配置
    notifier = WeComNotifier()
    if not notifier.is_configured:
        print()
        print("⚠️ 通知未配置！")
        if CONFIG.get("wecom", "mode", fallback="webhook") == "websocket":
            print("   请确保 config.ini 中 [wecom] 节的 bot_id / secret 已正确填写。")
        else:
            print("   请编辑 config.ini，在 [wecom] 节中填入 webhook_url。")
        print()

    print("运行模式：")
    print("  1. 一次性检测（配合 Windows 计划任务）")
    print("  2. 持续循环检测")
    print()

    try:
        choice = input("请选择[1/2]（默认 2）: ").strip() or "2"
    except (EOFError, KeyboardInterrupt):
        choice = "2"

    if choice == "1":
        run_once_and_exit()
    else:
        monitor = TaskMonitor()
        try:
            monitor.run_loop()
        except KeyboardInterrupt:
            print("\n服务已停止。")


if __name__ == "__main__":
    main()
