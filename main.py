#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Veloera 通用签到服务 - GitHub Action 版
"""

import os
import json
import logging
import requests
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin

# --- 基础配置类与枚举 ---

class CheckinStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    ALREADY_CHECKED = "already_checked"
    UNAUTHORIZED = "unauthorized"

@dataclass
class CheckinResult:
    status: CheckinStatus
    message: str
    data: Optional[Dict[str, Any]] = None

@dataclass
class VeloeraConfig:
    base_url: str
    user_id: str
    access_token: str
    checkin_endpoint: str = "/api/user/check_in"
    timeout: int = 30
    retry_count: int = 3
    retry_delay: float = 1.0
    
    @property
    def checkin_url(self) -> str:
        return urljoin(self.base_url, self.checkin_endpoint)

# --- 日志管理器 ---

class Logger:
    def __init__(self):
        self.logger = logging.getLogger("VeloeraCheckin")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
    def info(self, msg): self.logger.info(msg)
    def error(self, msg): self.logger.error(msg)
    def warning(self, msg): self.logger.warning(msg)

# --- 核心签到逻辑 ---

class VeloeraCheckinService:
    def __init__(self, config: VeloeraConfig, logger: Logger):
        self.config = config
        self.logger = logger
        self.session = requests.Session()
        self.session.headers.update(self._get_headers())

    def _get_headers(self) -> Dict[str, str]:
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Authorization': f'Bearer {self.config.access_token}',
            'Veloera-User': self.config.user_id,
            'Origin': self.config.base_url,
            'Referer': f'{self.config.base_url}/personal',
            'Content-Length': '0'
        }

    def checkin(self) -> CheckinResult:
        import time
        for attempt in range(1, self.config.retry_count + 1):
            try:
                self.logger.info(f"⏳ 第 {attempt} 次尝试签到 (UID: {self.config.user_id})")
                response = self.session.post(self.config.checkin_url, timeout=self.config.timeout)
                
                # 处理 401 认证失败
                if response.status_code == 401:
                    return CheckinResult(CheckinStatus.UNAUTHORIZED, "Token或User ID无效/过期")

                if response.status_code == 200:
                    try:
                        data = response.json()
                        success = data.get('success', False)
                        msg = data.get('message', '无消息')
                        
                        # 判断是否重复签到
                        if not success and any(k in msg for k in ["已签到", "already", "重复"]):
                            return CheckinResult(CheckinStatus.ALREADY_CHECKED, msg)
                        
                        if success:
                            quota = data.get('data', {}).get('quota', 0)
                            mb = quota / (1024 * 1024)
                            return CheckinResult(CheckinStatus.SUCCESS, f"{msg} | 剩余配额: {mb:.2f} MB")
                        
                        return CheckinResult(CheckinStatus.FAILED, f"API返回失败: {msg}")
                    except json.JSONDecodeError:
                        return CheckinResult(CheckinStatus.FAILED, "响应非JSON格式")
                
                self.logger.warning(f"⚠️ HTTP {response.status_code}")

            except Exception as e:
                self.logger.error(f"❌ 网络请求异常: {str(e)}")
            
            if attempt < self.config.retry_count:
                time.sleep(self.config.retry_delay)

        return CheckinResult(CheckinStatus.FAILED, "重试次数耗尽")

# --- 主程序 ---

def main():
    logger = Logger()
    logger.info("🚀 Veloera 自动签到启动")
    
    # 1. 尝试读取配置文件路径
    config_path = os.getenv('VELOERA_CONFIG_FILE', 'config.json')
    
    if not os.path.exists(config_path):
        logger.error(f"❌ 找不到配置文件: {config_path}")
        logger.error("请确保在 GitHub Secrets 中设置了 JSON 配置，并且 Workflow 正确生成了文件。")
        sys.exit(1)

    # 2. 加载配置
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
            # 兼容两种格式：直接是 list 或是 {"accounts": []}
            accounts_list = config_data.get('accounts', []) if isinstance(config_data, dict) else config_data
    except Exception as e:
        logger.error(f"❌ 配置文件解析失败: {e}")
        sys.exit(1)

    if not accounts_list:
        logger.error("❌ 配置文件中没有账号信息")
        sys.exit(1)

    # 3. 执行批量签到
    success_count = 0
    results = []

    for idx, acc in enumerate(accounts_list):
        logger.info("-" * 30)
        cfg = VeloeraConfig(**acc)
        service = VeloeraCheckinService(cfg, logger)
        res = service.checkin()
        results.append(res)
        
        if res.status == CheckinStatus.SUCCESS:
            logger.info(f"✅ 成功: {res.message}")
            success_count += 1
        elif res.status == CheckinStatus.ALREADY_CHECKED:
            logger.info(f"🆗 跳过: {res.message}")
            success_count += 1 # 已签到也算任务成功
        else:
            logger.error(f"❌ 失败: {res.message}")

    logger.info("=" * 30)
    # 只要有一个账号签到失败（非重复签到），就让 Action 报错，以便发送邮件通知
    if success_count < len(accounts_list):
        logger.error("⚠️ 部分账号签到失败")
        sys.exit(1)
    else:
        logger.info("🎉 所有账号处理完毕")

if __name__ == "__main__":
    main()
