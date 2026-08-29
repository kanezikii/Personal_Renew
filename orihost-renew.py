#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Orihost 自动续期脚本 (Playwright + Turnstile Token 精准对接版)
# ============================================================
import os
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
    """清理遮挡点击的第三方广告弹窗与 Cookie 提示"""
    try:
        page.evaluate("""
            document.querySelectorAll('iframe:not([src*="challenges.cloudflare.com"]):not([src*="cloudflare"])').forEach(el => el.remove());
            document.querySelectorAll('ins.adsbygoogle, div[class*="ad-"], div[id*="google_ads"]').forEach(el => el.remove());
            document.querySelectorAll('div[class*="cookie"], #cookie-banner, button:has-text("Got it")').forEach(el => el.remove());
        """)
    except Exception:
        pass


def wait_and_solve_turnstile(page, max_wait=35) -> str:
    """触发 Turnstile 勾选并获取生成的 cf-turnstile-response Token"""
    print("  🛡️ 正在触发 Turnstile 验证并提取 Token...")

    start_time = time.time()
    last_click_time = 0

    while time.time() - start_time < max_wait:
        # 1. 检查 DOM 中是否已生成有效 Token
        token = page.evaluate("""
            () => {
                const el = document.querySelector('input[name="cf-turnstile-response"], input[name="cf_challenge_response"], [name*="turnstile"]');
                return (el && el.value && el.value.length > 20) ? el.value : '';
            }
        """)
        if token:
            print(f"  🎉 成功获取 Turnstile 验证 Token (前30位): {token[:30]}...")
            return token

        # 2. 每隔 4 秒尝试一次轻微物理点击触发
        if time.time() - last_click_time > 4:
            last_click_time = time.time()
            for f in page.frames:
                if "challenges.cloudflare.com" in f.url or "turnstile" in f.url:
                    try:
                        fe = f.frame_element()
                        bbox = fe.bounding_box()
                        if bbox and bbox["width"] > 0:
                            target_x = bbox["x"] + 35
                            target_y = bbox["y"] + (bbox["height"] / 2)
                            page.mouse.move(target_x, target_y, steps=5)
                            time.sleep(0.1)
                            page.mouse.down()
                            time.sleep(0.1)
                            page.mouse.up()
                            print(f"  👆 物理点击勾选框坐标 ({target_x:.1f}, {target_y:.1f})")
                    except Exception:
                        pass

                    try:
                        f.locator("body, label, input[type='checkbox']").first.click(position={"x": 35, "y": 32}, force=True, timeout=1000)
                    except Exception:
                        pass

        time.sleep(1)

    return ""


def renew_single_server(page, context, server_id: str) -> dict:
    """处理单个服务器的完整续期流程"""
    target_url = f"{BASE_URL}/server/{server_id[:8]}"
    print(f"\n🔄 打开服务器控制台: {target_url}")
    page.goto(target_url, wait_until="networkidle", timeout=60000)
    time.sleep(3)
    take_shot(page, f"{server_id[:8]}_01_console")

    clean_ad_overlays(page)

    if "login" in page.url.lower():
        take_shot(page, f"{server_id[:8]}_error_login")
        return {"status": "error", "message": "Cookie 已失效，跳转到了登录页"}

    renew_btn = page.locator("button:has-text('Renew'), button:has-text('续期')")
    if renew_btn.count() == 0:
        take_shot(page, f"{server_id[:8]}_error_no_renew_btn")
        return {"status": "error", "message": "未找到 Renew 按钮"}

    print("  🔘 点击主界面的 Renew 按钮...")
    renew_btn.first.click(force=True)
    time.sleep(2)
    take_shot(page, f"{server_id[:8]}_02_modal_opened")

    # 点击阅读广告文章
    read_article_btn = page.locator("button:has-text('Read Article'), button:has-text('阅读文章')")
    if read_article_btn.count() > 0:
        print("  📰 点击 Read Article 并监听新标签页...")
        with context.expect_page() as new_page_info:
            read_article_btn.first.click(force=True)

        ad_page = new_page_info.value
        print(f"  🔗 广告页面已打开: {ad_page.url[:60]}...")
        print("  ⏳ 正在等待 18 秒阅读倒计时...")
        time.sleep(18)

        try:
            take_shot(ad_page, f"{server_id[:8]}_03_ad_page")
            ad_page.close()
            print("  🔒 已关闭广告页面，返回控制台")
        except Exception:
            pass

        page.bring_to_front()
        time.sleep(2)

    take_shot(page, f"{server_id[:8]}_04_after_ad_returned")

    claim_btn = page.locator("button:has-text('Claim Renewal'), button:has-text('Claim')")
    if claim_btn.count() == 0:
        take_shot(page, f"{server_id[:8]}_error_no_claim_btn")
        return {"status": "error", "message": "未找到 Claim Renewal 按钮"}

    # 获取 Turnstile Token
    cf_token = wait_and_solve_turnstile(page, max_wait=35)
    take_shot(page, f"{server_id[:8]}_05_turnstile_finished")

    if not cf_token and not claim_btn.first.is_enabled():
        take_shot(page, f"{server_id[:8]}_error_turnstile_failed")
        return {"status": "error", "message": "Turnstile 验证码未能在时限内通过"}

    # 监听网络响应并提交 Claim Renewal
    print("  🔘 提交 Claim Renewal 并监听完成接口...")
    try:
        with page.expect_response(lambda r: "renewal/complete" in r.url, timeout=15000) as resp_info:
            claim_btn.first.click(force=True)

        resp = resp_info.value
        print(f"  📥 收到 renewal/complete 响应 HTTP {resp.status}")
        resp_body = resp.text()
        print(f"  📄 响应内容: {resp_body[:200]}")

        take_shot(page, f"{server_id[:8]}_06_final_result")

        if resp.status == 200:
            return {"status": "success", "message": f"续期成功 (HTTP 200: {resp_body[:60]})"}
        else:
            return {"status": "error", "message": f"续期返回 HTTP {resp.status}: {resp_body[:100]}"}

    except Exception as e:
        # 如果监听超时，尝试直接在当前页面执行请求
        print(f"  ⚠️ 监听超时，尝试前端注入提交: {e}")
        if cf_token:
            direct_res = page.evaluate(f"""
                async () => {{
                    const res = await fetch('{BASE_URL}/api/client/renewal/complete?cf-turnstile-response={cf_token}', {{
                        headers: {{ 'accept': 'application/json', 'x-requested-with': 'XMLHttpRequest' }}
                    }});
                    return {{ status: res.status, text: await res.text() }};
                }}
            """)
            print(f"  📥 直接调用返回: {direct_res}")
            take_shot(page, f"{server_id[:8]}_06_final_result")
            if direct_res.get("status") == 200:
                return {"status": "success", "message": f"续期成功 (HTTP 200)"}

        take_shot(page, f"{server_id[:8]}_error_submit_failed")
        return {"status": "error", "message": f"提交续期失败: {e}"}


def main():
    print("=" * 40)
    print(" Orihost 自动续期 (Turnstile 对接版)")
    print("=" * 40)

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
                "--disable-blink-features=AutomationControlled",
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

        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        """)

        context.add_cookies(parse_cookies_for_playwright(cookie))
        page = context.new_page()

        for server_id in server_ids:
            try:
                res = renew_single_server(page, context, server_id)
                now_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
                status_icon = "✅" if res["status"] == "success" else "❌"
                msg = f"🖥 Orihost 服务器续期\n\n{status_icon} 状态: {res['message']}\n🆔 服务器: {server_id[:8]}\n⏰ 时间: {now_str}"
                print(f"\n{msg}\n")
                send_telegram(msg)
            except Exception as e:
                take_shot(page, f"{server_id[:8]}_fatal_exception")
                print(f"❌ 服务器 {server_id} 执行出错: {e}")
                send_telegram(f"🖥 Orihost 服务器续期\n\n❌ 异常: {e}\n🆔 服务器: {server_id[:8]}")

        browser.close()


if __name__ == "__main__":
    main()
