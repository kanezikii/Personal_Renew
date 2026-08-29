#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Orihost 自动续期脚本 (Playwright 模拟浏览器版本)
# 支持 Cloudflare Turnstile 验证及广告跳转等待
# ============================================================
import os
import sys
import time
import requests
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

BASE_URL = "https://panel.orihost.com"
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

# 代理配置
ORIHOST_PROXY = os.environ.get("ORIHOST_PROXY") or os.environ.get("HTTP_PROXY") or ""


def send_telegram(message: str):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    proxies = {"http": ORIHOST_PROXY, "https": ORIHOST_PROXY} if ORIHOST_PROXY else None
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10, proxies=proxies)
    except Exception as e:
        print(f"  ❌ Telegram 发送失败: {e}")


def parse_cookies_for_playwright(cookie_str: str) -> list:
    """将 Cookie 字符串格式化为 Playwright 所需结构"""
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


def solve_turnstile_if_present(page, timeout=30):
    """检测并点击 Cloudflare Turnstile 验证复选框"""
    print("  🛡️ 检测 Cloudflare Turnstile 验证...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        for frame in page.frames:
            if "challenges.cloudflare.com" in frame.url:
                try:
                    box = frame.locator("input[type='checkbox'], .ctp-checkbox-label, #challenge-stage")
                    if box.count() > 0:
                        print("  👆 点击 Cloudflare 验证框...")
                        box.first.click()
                        time.sleep(2)
                        return True
                except Exception:
                    pass
        time.sleep(1)
    return False


def renew_single_server(page, context, server_id: str) -> dict:
    """处理单个服务器的完整续期流程"""
    target_url = f"{BASE_URL}/server/{server_id[:8]}"
    print(f"\n🔄 打开服务器控制台: {target_url}")
    page.goto(target_url, wait_until="networkidle", timeout=60000)
    time.sleep(3)

    # 检查是否登录有效
    if "login" in page.url.lower():
        return {"status": "error", "message": "Cookie 已失效，跳转到了登录页"}

    # 点击页面右下角的 Renew 按钮
    renew_btn = page.locator("button:has-text('Renew'), button:has-text('续期')")
    if renew_btn.count() == 0:
        return {"status": "error", "message": "未找到 Renew 按钮"}

    print("  🔘 点击主界面的 Renew 按钮...")
    renew_btn.first.click()
    time.sleep(2)

    # 检查弹窗中是否有 Read Article 按钮
    read_article_btn = page.locator("button:has-text('Read Article'), button:has-text('阅读文章')")
    if read_article_btn.count() > 0:
        print("  📰 点击 Read Article 并监听新标签页...")
        with context.expect_page() as new_page_info:
            read_article_btn.first.click()
        
        ad_page = new_page_info.value
        print(f"  🔗 广告页面已打开: {ad_page.url[:60]}...")
        print("  ⏳ 正在等待 18 秒阅读倒计时...")
        time.sleep(18)
        
        try:
            ad_page.close()
            print("  🔒 已关闭广告页面，返回控制台")
        except Exception:
            pass
        
        # 激活原控制台页面
        page.bring_to_front()
        time.sleep(2)

    # 尝试处理 Cloudflare Turnstile 验证
    solve_turnstile_if_present(page, timeout=15)

    # 等待 Claim Renewal 按钮激活并点击
    claim_btn = page.locator("button:has-text('Claim Renewal'), button:has-text('Claim')")
    if claim_btn.count() == 0:
        return {"status": "error", "message": "未找到 Claim Renewal 按钮"}

    try:
        # 等待按钮解除禁用
        claim_btn.first.wait_for(state="visible", timeout=10000)
        print("  🔘 点击 Claim Renewal 提交续期...")
        claim_btn.first.click()
        time.sleep(4)
    except Exception as e:
        return {"status": "error", "message": f"点击 Claim 失败: {e}"}

    # 检查页面反馈
    content = page.content()
    if "success" in content.lower() or "renewed" in content.lower():
        return {"status": "success", "message": "续期成功 (+7 天)"}
    elif "limit" in content.lower():
        return {"status": "skipped", "message": "已达续期上限"}

    return {"status": "success", "message": "续期流程已执行完毕"}


def main():
    print("=" * 40)
    print(" Orihost 自动续期 (Playwright 引擎)")
    print("=" * 40)

    # 读取环境变量中的配置
    cookie = os.environ.get("ORIHOST_COOKIE") or os.environ.get("ORIHOST_COOKIE_1") or ""
    server_ids_raw = os.environ.get("ORIHOST_SERVER_IDS") or os.environ.get("ORIHOST_SERVER_IDS_1") or ""
    server_ids = [s.strip() for s in server_ids_raw.split(",") if s.strip()]

    if not cookie or not server_ids:
        print("❌ 未配置 ORIHOST_COOKIE 或 ORIHOST_SERVER_IDS，退出。")
        sys.exit(1)

    proxy_cfg = {"server": ORIHOST_PROXY} if ORIHOST_PROXY else None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy=proxy_cfg,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )

        # 注入 Cookie
        playwright_cookies = parse_cookies_for_playwright(cookie)
        context.add_cookies(playwright_cookies)

        page = context.new_page()

        for server_id in server_ids:
            try:
                res = renew_single_server(page, context, server_id)
                now_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
                status_icon = "✅" if res["status"] == "success" else ("⏭️" if res["status"] == "skipped" else "❌")
                msg = f"🖥 Orihost 服务器续期\n\n{status_icon} 状态: {res['message']}\n🆔 服务器: {server_id[:8]}\n⏰ 时间: {now_str}"
                print(f"\n{msg}\n")
                send_telegram(msg)
            except Exception as e:
                print(f"❌ 服务器 {server_id} 执行出错: {e}")
                send_telegram(f"🖥 Orihost 服务器续期\n\n❌ 异常: {e}\n🆔 服务器: {server_id[:8]}")

        browser.close()


if __name__ == "__main__":
    main()
