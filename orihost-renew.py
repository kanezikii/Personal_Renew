#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Orihost 服务器剩余时间监控脚本 (<= 3 天 TG 提醒版)
# ============================================================
import os
import re
import sys
import time
import requests
from pathlib import Path
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

BASE_URL = "https://panel.orihost.com"
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""
ORIHOST_PROXY = os.environ.get("ORIHOST_PROXY") or os.environ.get("HTTP_PROXY") or ""
WARN_THRESHOLD_DAYS = 3  # 报警阈值：剩余 <= 3 天

SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def take_shot(page, name: str):
    """保存屏幕截图"""
    try:
        file_path = SCREENSHOT_DIR / f"{name}.png"
        page.screenshot(path=str(file_path), full_page=True)
        print(f"  📸 已保存截图: {file_path}")
    except Exception as e:
        print(f"  ⚠️ 截图失败: {e}")


def send_telegram(message: str):
    """发送 Telegram 消息通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("  ⚠️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过通知发送")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    proxies = {"http": ORIHOST_PROXY, "https": ORIHOST_PROXY} if ORIHOST_PROXY else None
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10, proxies=proxies)
        print("  📨 Telegram 预警通知已发送")
    except Exception as e:
        print(f"  ❌ Telegram 发送失败: {e}")


def parse_cookies_for_playwright(cookie_str: str) -> list:
    """解析 Cookie 为 Playwright 格式"""
    playwright_cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, val = item.split("=", 1)
            playwright_cookies.append({
                "name": key.strip(),
                "value": unquote(val.strip()),
                "domain": "panel.orihost.com",
                "path": "/",
            })
    return playwright_cookies


def clean_ad_overlays(page):
    """清理遮挡的第三方广告与提示层"""
    try:
        page.evaluate("""
            document.querySelectorAll('iframe:not([src*="challenges.cloudflare.com"])').forEach(el => el.remove());
            document.querySelectorAll('ins.adsbygoogle, div[class*="ad-"], div[id*="google_ads"]').forEach(el => el.remove());
            document.querySelectorAll('div[class*="cookie"], #cookie-banner, button:has-text("Got it")').forEach(el => el.remove());
        """)
    except Exception:
        pass


def extract_server_info(page, server_id: str) -> dict:
    """进入服务器控制台并提取剩余有效天数与服务器名称"""
    target_url = f"{BASE_URL}/server/{server_id[:8]}"
    print(f"\n🔍 正在检查服务器: {target_url}")

    page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(4)
    clean_ad_overlays(page)
    take_shot(page, f"{server_id[:8]}_status")

    if "login" in page.url.lower():
        return {"status": "error", "message": "Cookie 已失效，请在 Secrets 中更新 ORIHOST_COOKIE"}

    if "Something went wrong" in page.content() or "could not be found" in page.content():
        return {"status": "error", "message": "服务器加载失败 (404)，请检查服务器 ID 是否正确"}

    # 提取服务器名称
    server_name = "Orihost Server"
    try:
        name_el = page.locator("h1, h2, div[class*='ServerName'], div[class*='title']").first
        if name_el.count() > 0:
            server_name = name_el.inner_text().strip()
    except Exception:
        pass

    # 提取剩余天数（多规则匹配：右侧卡片与主面板文字）
    remaining_days = None
    page_text = page.inner_text("body")

    # 规则 1: 匹配 RENEWAL IN 卡片中的天数
    match = re.search(r'RENEWAL\s+IN[\s\n]*(\d+)\s*Days?', page_text, re.IGNORECASE)
    if match:
        remaining_days = int(match.group(1))

    # 规则 2: 点击 Renew 弹窗兜底提取
    if remaining_days is None:
        try:
            renew_btn = page.locator("button:has-text('Renew'), button:has-text('续期')")
            if renew_btn.count() > 0:
                renew_btn.first.click(force=True)
                time.sleep(1.5)
                modal_text = page.inner_text("body")
                match_modal = re.search(r'Current\s+renewal\s+in:\s*(\d+)\s*days?', modal_text, re.IGNORECASE)
                if match_modal:
                    remaining_days = int(match_modal.group(1))
        except Exception:
            pass

    if remaining_days is not None:
        return {
            "status": "success",
            "server_id": server_id[:8],
            "server_name": server_name,
            "days": remaining_days
        }

    return {"status": "error", "message": "未能从页面中解析出剩余天数"}


def main():
    print("=" * 45)
    print(" Orihost 服务器剩余时间监控 (低于 3 天预警)")
    print("=" * 45)

    cookie = os.environ.get("ORIHOST_COOKIE") or os.environ.get("ORIHOST_COOKIE_1") or ""
    server_ids_raw = os.environ.get("ORIHOST_SERVER_IDS") or os.environ.get("ORIHOST_SERVER_IDS_1") or ""
    server_ids = [s.strip() for s in server_ids_raw.split(",") if s.strip()]

    if not cookie or not server_ids:
        print("❌ 未配置 ORIHOST_COOKIE 或 ORIHOST_SERVER_IDS，退出。")
        sys.exit(1)

    proxy_cfg = {"server": ORIHOST_PROXY} if ORIHOST_PROXY else None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            proxy=proxy_cfg,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--start-maximized",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai"
        )
        context.add_cookies(parse_cookies_for_playwright(cookie))
        page = context.new_page()

        # 首页预热会话
        print("🌐 访问首页初始化会话...")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)

        for server_id in server_ids:
            res = extract_server_info(page, server_id)
            now_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

            if res["status"] == "success":
                days = res["days"]
                server_name = res["server_name"]
                print(f"  📊 服务器 [{server_name}] 当前剩余: {days} 天")

                if days <= WARN_THRESHOLD_DAYS:
                    print(f"  ⚠️ 剩余天数 <= {WARN_THRESHOLD_DAYS} 天，正在推送预警通知...")
                    msg = (
                        f"主人，您的 Orihost 服务器即将到期，请续期！\n\n"
                        f"🖥 <b>服务器名称</b>：{server_name}\n"
                        f"🆔 <b>服务器 ID</b>：<code>{server_id[:8]}</code>\n"
                        f"⏳ <b>剩余有效时间</b>：<b>{days} 天</b>（已低于预警阈值 3 天）\n"
                        f"🔗 <b>控制台直达</b>：https://panel.orihost.com/server/{server_id[:8]}\n"
                        f"⏰ <b>检测时间</b>：{now_str}\n\n"
                        f"💡 <i>请及时登录手动续期。</i>"
                    )
                    send_telegram(msg)
                else:
                    print(f"  ✅ 剩余时间充裕（{days} 天 > {WARN_THRESHOLD_DAYS} 天），无需发送通知。")
            else:
                print(f"  ❌ 检测异常: {res['message']}")
                error_msg = (
                    f"主人，Orihost 服务器状态检测异常！\n\n"
                    f"🆔 <b>服务器 ID</b>：<code>{server_id[:8]}</code>\n"
                    f"❌ <b>异常原因</b>：{res['message']}\n"
                    f"⏰ <b>检测时间</b>：{now_str}\n\n"
                    f"请检查 GitHub Secrets 中的 <code>ORIHOST_COOKIE</code> 是否过期。"
                )
                send_telegram(error_msg)

        browser.close()


if __name__ == "__main__":
    main()
