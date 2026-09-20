import json
import os
import sys
import time
from playwright.sync_api import sync_playwright

SERVER_URL = os.getenv("SERVER_URL", "https://control.heavencloud.in/server/d9063afd/overview")
COOKIES_STR = os.getenv("COOKIES", "[]")
PROXY_PORT = os.getenv("LOCAL_PROXY_PORT", "")

def format_cookies(cookies_input):
    """解析并标准化 Cookies 格式"""
    try:
        data = json.loads(cookies_input)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    
    # 兼容 key1=val1; key2=val2 格式
    formatted = []
    for item in cookies_input.split(";"):
        if "=" in item:
            k, v = item.strip().split("=", 1)
            formatted.append({
                "name": k,
                "value": v,
                "domain": "control.heavencloud.in",
                "path": "/"
            })
    return formatted

def run():
    cookies = format_cookies(COOKIES_STR)
    if not cookies:
        print("[!] 错误: 未检测到有效 COOKIES，请检查 Secrets 配置。")
        sys.exit(1)

    with sync_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        }
        
        # 本地代理配置
        if PROXY_PORT:
            launch_args["proxy"] = {"server": f"http://127.0.0.1:{PROXY_PORT}"}
            print(f"[*] 已挂载本地代理: 127.0.0.1:{PROXY_PORT}")

        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900}
        )

        # 注入 Cookies
        context.add_cookies(cookies)
        page = context.new_page()

        print(f"[*] 正在访问目标页面: {SERVER_URL}")
        page.goto(SERVER_URL, wait_until="networkidle", timeout=60000)
        time.sleep(3)

        # 检查是否被重定向到登录页
        if "/auth/login" in page.url:
            print("[!] 登录失效: 页面被重定向至登录页，请更新 COOKIES。")
            page.screenshot(path="login_failed.png")
            browser.close()
            sys.exit(1)

        print("[+] 登录有效，正在查找续期按钮...")

        # 定位续期按钮（结合 HTML 结构特征：属性含 Renews / renew 或包含类似 6d 22h 格式的按钮）
        renew_btn = page.locator(
            'button[title*="renew" i], button[aria-label*="Renews" i], button:has-text("d ")'
        ).first

        if renew_btn.count() == 0:
            print("[!] 未在当前页面找到续期按钮，尝试查找 Manage 入口...")
            # 兼容从首页进入的情况
            manage_btn = page.locator('a[href*="/overview"], button:has-text("Manage")').first
            if manage_btn.count() > 0:
                manage_btn.click()
                page.wait_for_load_state("networkidle")
                time.sleep(3)
                renew_btn = page.locator(
                    'button[title*="renew" i], button[aria-label*="Renews" i], button:has-text("d ")'
                ).first

        if renew_btn.count() > 0:
            btn_text = renew_btn.inner_text().replace("\n", " ").strip()
            print(f"[*] 找到续期模块/按钮，当前显示状态: [{btn_text}]，执行点击...")
            renew_btn.click()
            
            # 等待绿色 Toast 弹窗消息
            time.sleep(2)
            toast = page.locator('div[role="status"] p, div.shadow-panel p').first
            
            if toast.count() > 0 and toast.is_visible():
                toast_msg = toast.inner_text().strip()
                print(f"[+] 捕获到反馈提示: {toast_msg}")
            else:
                print("[*] 点击已执行，未检测到显式 Toast 文本，请查看生成的截图确认状态。")
        else:
            print("[!] 未定位到续期按钮，请核对是否已加载到服务器概览界面。")

        # 截图保存现场
        page.screenshot(path="renew_result.png")

        # 提取最新 Cookies
        latest_cookies = context.cookies()
        with open("new_cookies.json", "w", encoding="utf-8") as f:
            json.dump(latest_cookies, f, ensure_ascii=False, indent=2)
        print("[+] 已成功获取当前会话的最新 Cookies。")

        browser.close()

if __name__ == "__main__":
    run()
