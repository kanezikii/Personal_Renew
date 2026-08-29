#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Orihost 自动续期脚本 (Playwright 稳定静默等待 Turnstile 版)
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
    """清理遮挡点击的第三方广告弹窗"""
    try:
        page.evaluate("""
            document.querySelectorAll('iframe:not([src*="challenges.cloudflare.com"]):not([src*="cloudflare"])').forEach(el => el.remove());
            document.querySelectorAll('ins.adsbygoogle, div[class*="ad-"], div[id*="google_ads"]').forEach(el => el.remove());
        """)
    except Exception:
        pass


def solve_turnstile_carefully(page, claim_btn, max_wait=25) -> bool:
    """精准单次点击 Turnstile，并静默等待 Cloudflare 完成人机校验"""
    print("  🛡️ 正在检测并触发 Cloudflare Turnstile 验证...")

    if claim_btn.first.is_enabled():
        return True

    # 1. 查找 Turnstile iframe 并执行单次精准点击
    clicked = False
    for _ in range(5):
        for f in page.frames:
            if "challenges.cloudflare.com" in f.url or "turnstile" in f.url:
                try:
                    fe = f.frame_element()
                    bbox = fe.bounding_box()
                    if bbox and bbox["width"] > 0 and bbox["height"] > 0:
                        target_x = bbox["x"] + 32
                        target_y = bbox["y"] + (bbox["height"] / 2)
                        print(f"  👆 物理鼠标平滑移动并单次点击勾选框 ({target_x:.1f}, {target_y:.1f})...")
                        page.mouse.move(target_x, target_y, steps=10)
                        time.sleep(0.3)
                        page.mouse.click(target_x, target_y)
                        clicked = True
                        break
                except Exception:
                    pass
        if clicked:
            break
        time.sleep(1)

    if not clicked:
        print("  ⚠️ 未能定位到 iframe 坐标，尝试备用区域点击...")
        try:
            cf_frame = page.frame_locator("iframe[src*='challenges.cloudflare.com']").first
            cf_frame.locator("body").click(position={"x": 32, "y": 32}, force=True, timeout=2000)
        except Exception:
            pass

    # 2. 静默等待验证通过（切勿重复点击打断计算）
    print("  ⏳ 正在静默等待 Cloudflare 完成人机校验计算...")
    start_time = time.time()
    while time.time() - start_time < max_wait:
        if claim_btn.first.is_enabled():
            print("  🎉 Claim Renewal 按钮已解除禁用（验证已通过）！")
            return True

        # 检测是否已生成 cf-turnstile-response Token
        has_token = page.evaluate("""
            () => {
                const el = document.querySelector('input[name="cf-turnstile-response"], input[name="cf_challenge_response"]');
                return el && el.value && el.value.length > 10;
            }
        """)
        if has_token:
            print("  🎉 已捕获到 Turnstile 验证 Token！")
            time.sleep(1)
            return True

        time.sleep(1)

    return claim_btn.first.is_enabled()


def renew_single_server(page, context, server_id: str) -> dict:
    """处理单个服务器的完整续期流程"""
    target_url = f"{BASE_URL}/server/{server_id[:8]}"
    print(f"\n🔄 打开服务器控制台: {target_url}")
    page.goto(target_url, wait_until="networkidle", timeout=60000)
    time.sleep(3)
    take_shot(page, f"{server_id[:8]}_01_console")

    # 关闭 Cookie 提示与广告
    try:
        got_it = page.locator("button:has-text('Got it')")
        if got_it.count() > 0:
            got_it.first.click(force=True)
            time.sleep(1)
    except Exception:
        pass
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

    # 单次精准点击并静默等待验证完成
    is_ready = solve_turnstile_carefully(page, claim_btn, max_wait=25)
    take_shot(page, f"{server_id[:8]}_05_turnstile_finished")

    if not is_ready:
        take_shot(page, f"{server_id[:8]}_error_claim_still_disabled")
        return {"status": "error", "message": "Turnstile 验证码未能在时限内激活按钮"}

    # 提交续期
    try:
        print("  🔘 点击 Claim Renewal 提交续期...")
        claim_btn.first.click(force=True, timeout=8000)
        time.sleep(4)
    except Exception as e:
        take_shot(page, f"{server_id[:8]}_error_click_claim_failed")
        return {"status": "error", "message": f"点击 Claim 失败: {e}"}

    take_shot(page, f"{server_id[:8]}_06_final_result")

    content = page.content()
    if "success" in content.lower() or "renewed" in content.lower():
        return {"status": "success", "message": "续期成功 (+7 天)"}
    elif "limit" in content.lower():
        return {"status": "skipped", "message": "已达续期上限"}

    return {"status": "success", "message": "续期流程已执行完毕"}


def main():
    print("=" * 40)
    print(" Orihost 自动续期 (Playwright 稳定验证版)")
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
                status_icon = "✅" if res["status"] == "success" else ("⏭️" if res["status"] == "skipped" else "❌")
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
