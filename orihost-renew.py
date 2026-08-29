#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Orihost 自动续期脚本 (Playwright 物理按压穿透 Turnstile 版)
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


def solve_turnstile_smoothly(page, claim_btn, max_wait=35) -> bool:
    """模拟物理按压点击 Turnstile 并静默等待人机校验完成"""
    print("  🛡️ 正在定位 Cloudflare Turnstile 并触发人机验证...")

    cf_frame = None
    for f in page.frames:
        if "challenges.cloudflare.com" in f.url or "turnstile" in f.url:
            cf_frame = f
            break

    # 1. 触发物理按压点击
    if cf_frame:
        try:
            fe = cf_frame.frame_element()
            bbox = fe.bounding_box()
            if bbox and bbox["width"] > 0 and bbox["height"] > 0:
                # 点击复选框至文本区域（X+45px，Y居中），覆盖整个 label
                target_x = bbox["x"] + 45
                target_y = bbox["y"] + (bbox["height"] / 2)
                print(f"  👆 物理鼠标平滑移动并执行真实按压 ({target_x:.1f}, {target_y:.1f})...")
                page.mouse.move(target_x, target_y, steps=8)
                time.sleep(0.2)
                page.mouse.down()
                time.sleep(0.12)  # 真实物理按压停留时长
                page.mouse.up()
        except Exception as e:
            print(f"  ⚠️ 物理坐标点击失败: {e}")

    # 2. Frame 内部补充派发原生交互
    if cf_frame:
        try:
            cf_frame.locator("label, body, input[type='checkbox']").first.click(force=True, timeout=2000)
        except Exception:
            pass

    take_shot(page, "05_1_after_trigger_click")

    # 3. 静默等待验证结果（切勿高频重复点击打断计算）
    print("  ⏳ 正在静默等待 Cloudflare 完成人机校验 (最多 35 秒)...")
    start_time = time.time()
    while time.time() - start_time < max_wait:
        if claim_btn.first.is_enabled():
            print("  🎉 Claim Renewal 按钮已成功激活！")
            return True

        # 检测隐藏的 Token 响应字段
        has_token = page.evaluate("""
            () => {
                const input = document.querySelector('input[name="cf-turnstile-response"], input[name="cf_challenge_response"], [name*="turnstile"]');
                return input && input.value && input.value.length > 20;
            }
        """)
        if has_token:
            print("  🎉 已成功生成 Cloudflare 验证 Token！")
            time.sleep(2)
            return True

        # 检测 Frame 内部是否已显示打勾完成状态
        if cf_frame:
            try:
                success_elem = cf_frame.locator("svg, #success, .success, [data-state='success']")
                if success_elem.count() > 0 and success_elem.first.is_visible():
                    print("  🎉 Turnstile 内部显示验证打勾成功！")
                    time.sleep(2)
                    return True
            except Exception:
                pass

        time.sleep(1)

    return claim_btn.first.is_enabled()


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

    # 触发并等待 Turnstile 验证
    is_ready = solve_turnstile_smoothly(page, claim_btn, max_wait=35)
    take_shot(page, f"{server_id[:8]}_05_turnstile_finished")

    # 提交续期（附带 DOM 强制激活兜底）
    try:
        print("  🔘 提交 Claim Renewal...")
        try:
            claim_btn.first.click(force=True, timeout=5000)
        except Exception:
            page.evaluate("""
                () => {
                    const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Claim'));
                    if (btn) {
                        btn.removeAttribute('disabled');
                        btn.click();
                    }
                }
            """)
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
    print(" Orihost 自动续期 (Playwright 物理按压版)")
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
