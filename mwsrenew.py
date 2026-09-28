#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import json
import urllib.parse
import requests
from seleniumbase import SB

BASE_URL      = "https://cloud.m-ws.cc"
EMAIL         = os.environ.get("EMAIL") or ""
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""
MWS_COOKIES   = os.environ.get("MWS_COOKIES") or os.environ.get("COOKIES") or ""

DC_TOKEN = ""
if DISCORD_TOKEN:
    _parts = DISCORD_TOKEN.split(",", 1)
    DC_TOKEN = _parts[-1].strip()

DISCORD_API = "https://discord.com/api/v9/oauth2/authorize"
DISCORD_UA  = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
STATE_RE = re.compile(r"[?&]state=([^&]+)")


def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")


def format_notification(status: str, extra: str = "", error: str = "") -> str:
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if "@" in EMAIL:
        name, domain = EMAIL.split("@", 1)
        masked_email = f"{name[:2]}****{name[-2:]}@{domain}" if len(name) > 4 else f"{name}@{domain}"
    elif EMAIL:
        masked_email = EMAIL[:2] + "****"
    else:
        masked_email = "MWS 账户"

    lines = [
        "🚀 MWS 自动续期通知",
        "",
        f"📌 状态: {status}",
    ]
    if extra:
        lines.append("")
        lines.append(extra)
    if error:
        lines.append("")
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append("")
    lines.append(f"👤 登录账户: {masked_email}")
    lines.append(f"⏱️ 执行时间: {now}")
    return "\n".join(lines)


def get_current_ip(proxy_server: str = "") -> str:
    proxies = {"http": proxy_server, "https": proxy_server} if proxy_server else None
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()


def inject_cookies_to_browser(sb, cookies_str: str) -> bool:
    if not cookies_str:
        return False
    try:
        sb.open(BASE_URL)
        sb.sleep(2)
        cookies = json.loads(cookies_str)
        if isinstance(cookies, dict):
            for k, v in cookies.items():
                sb.driver.add_cookie({"name": k, "value": str(v), "domain": urllib.parse.urlparse(BASE_URL).hostname})
        elif isinstance(cookies, list):
            for c in cookies:
                c_dict = {"name": c["name"], "value": c["value"]}
                if "domain" in c and c["domain"]:
                    c_dict["domain"] = c["domain"]
                if "path" in c and c["path"]:
                    c_dict["path"] = c["path"]
                try:
                    sb.driver.add_cookie(c_dict)
                except Exception:
                    pass
        sb.open(BASE_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(4)
        return True
    except Exception as e:
        print(f"⚠️ 注入 Cookies 发生异常: {e}")
        return False


def save_cookies_to_file(sb, filepath: str = "cookies.json"):
    try:
        cookies = sb.driver.get_cookies()
        if cookies:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            print(f"💾 成功导出 {len(cookies)} 个 Cookie 到 {filepath}")
            return True
    except Exception as e:
        print(f"⚠️ 保存 Cookie 文件失败: {e}")
    return False


def check_is_logged_in(sb) -> bool:
    try:
        url = sb.get_current_url()
        if "/login" in url or "discord.com" in url:
            return False
        # 检测是否出现控制台核心元素（Menu、侧边栏或机器人列表）
        if sb.is_element_present('aside, .bot-grid, button:contains("Bots"), button:contains("Web")'):
            return True
    except Exception:
        pass
    return False


def extract_authorize_url_from_browser(current_url: str) -> str:
    if not current_url:
        return ""
    parsed = urllib.parse.urlparse(current_url)
    if parsed.path.startswith("/oauth2/authorize"):
        return current_url
    if parsed.path.startswith("/login"):
        raw_qs = parsed.query
        idx = raw_qs.find("redirect_to=")
        if idx >= 0:
            val = urllib.parse.unquote(raw_qs[idx + len("redirect_to="):])
            if val.startswith("/"):
                return f"https://discord.com{val}"
            if val.startswith("http"):
                return val
    return ""


def discord_authorize(state: str, authorize_url: str, proxy_server: str = "") -> str:
    if "/oauth2/authorize" in authorize_url and "/api/" not in authorize_url:
        authorize_url = authorize_url.replace("/oauth2/authorize", "/api/v9/oauth2/authorize", 1)

    _parsed = urllib.parse.urlparse(authorize_url)
    _params = urllib.parse.parse_qs(_parsed.query)
    _redirect_uri = _params.get("redirect_uri", [f"{BASE_URL}/api/auth/discord/callback"])[0]
    _scope = _params.get("scope", ["identify email"])[0]

    headers = {
        "accept": "*/*",
        "authorization": DC_TOKEN,
        "content-type": "application/json",
        "origin": "https://discord.com",
        "referer": authorize_url,
        "user-agent": DISCORD_UA,
        "x-discord-locale": "en-US",
    }

    body = json.dumps({
        "permissions": "0",
        "authorize": True,
        "scope": _scope,
        "integration_type": 0,
        "location_context": {
            "guild_id": "10000",
            "channel_id": "10000",
            "channel_type": 10000,
        },
    })

    proxies = {"http": proxy_server, "https": proxy_server} if proxy_server else None

    try:
        resp = requests.post(authorize_url, headers=headers, data=body, proxies=proxies, timeout=20)
        if resp.status_code != 200:
            print(f"❌ Discord 授权失败: HTTP {resp.status_code} - {resp.text[:200]}")
            return ""
        resp_data = resp.json()
        location = resp_data.get("location", "")
        if not location:
            match = re.search(r'"location"\s*:\s*"([^"]+)"', resp.text)
            location = match.group(1) if match else ""
        return location
    except Exception as e:
        print(f"❌ Discord 授权请求异常: {e}")
        return ""


def do_discord_login(sb, proxy_server: str = "") -> bool:
    if not DC_TOKEN:
        print("❌ 未提供 DISCORD_TOKEN")
        return False

    login_selectors = [
        'button:contains("Log in with Discord")',
        'a:contains("Log in with Discord")',
        'a:contains("Discord")',
        'a[href*="discord"]',
    ]
    for sel in login_selectors:
        try:
            if sb.is_element_visible(sel):
                sb.click(sel)
                break
        except Exception:
            pass

    authorize_url = ""
    for _ in range(15):
        sb.sleep(1)
        curr = sb.get_current_url()
        authorize_url = extract_authorize_url_from_browser(curr)
        if authorize_url:
            break

    if not authorize_url:
        print(f"❌ 未能提取到授权链接，当前地址: {sb.get_current_url()}")
        return False

    state_match = STATE_RE.search(authorize_url)
    state = state_match.group(1) if state_match else f"mws-{int(time.time() * 1000)}"

    location = discord_authorize(state, authorize_url, proxy_server)
    if not location:
        return False

    print("↩️ 携带授权 Code 回调进入控制台...")
    sb.uc_open_with_reconnect(location, reconnect_time=4)
    sb.wait_for_ready_state_complete()
    sb.sleep(5)
    return check_is_logged_in(sb)


def switch_menu_tab(sb, target_name: str) -> bool:
    """在 SPA 侧边栏中点击切换标签页（Bots / Web）"""
    js_click_tab = f"""
    const navButtons = Array.from(document.querySelectorAll('aside button, [class*="navItem"]'));
    const target = navButtons.find(b => (b.textContent || '').trim().toLowerCase() === '{target_name.lower()}');
    if (target) {{
        target.click();
        return true;
    }}
    return false;
    """
    clicked = sb.execute_script(js_click_tab)
    if clicked:
        print(f"🔀 已点击侧边栏菜单: [{target_name}]")
        sb.sleep(3)
        return True
    return False


def wait_and_get_cards(sb, max_wait: int = 10) -> list:
    """等待 .bot-grid 渲染并获取所有实例卡片"""
    js_extract_cards = """
    return (() => {
        // 卡片容器直接选取带有 data-zoom-id 或 class 包含 card 的元素
        const cards = Array.from(document.querySelectorAll('.bot-grid > div, [data-zoom-id]'));
        return cards.map((c, i) => {
            const zoomId = c.getAttribute('data-zoom-id') || ('card-' + i);
            const text = c.innerText || c.textContent || '';
            const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
            
            // 首行一般是名称（例如 renqi）
            const name = lines[0] || '未知实例';
            
            // 匹配剩余小时数（例如 167h 或 SLEEP IN 167h）
            const sleepMatch = text.match(/SLEEP\\s+IN\\s*([^\\n\\r]+)/i) || text.match(/(\\d+\\s*h)/i);
            const sleep = sleepMatch ? sleepMatch[1].trim() : '?';

            // 查找 Renew 按钮
            const buttons = Array.from(c.querySelectorAll('button'));
            const renewBtn = buttons.find(b => (b.textContent || '').trim().toLowerCase().includes('renew'));
            
            return {
                zoomId: zoomId,
                name: name,
                sleep: sleep,
                hasRenew: !!renewBtn,
                disabled: renewBtn ? (renewBtn.disabled || renewBtn.getAttribute('aria-disabled') === 'true') : false
            };
        });
    })();
    """
    for _ in range(max_wait):
        cards = sb.execute_script(js_extract_cards) or []
        if cards:
            return cards
        sb.sleep(1)
    return []


def handle_modal_confirm(sb):
    """检测并确认二次确认弹窗"""
    js_modal = """
    const buttons = Array.from(document.querySelectorAll('.modal button, [role="dialog"] button'));
    const confirmBtn = buttons.find(b => {
        const t = (b.textContent || '').trim().toLowerCase();
        return t.includes('confirm') || t.includes('renew') || t.includes('确认') || t.includes('yes');
    });
    if (confirmBtn) {
        confirmBtn.click();
        return true;
    }
    return false;
    """
    try:
        return bool(sb.execute_script(js_modal))
    except Exception:
        return False


def process_tab(sb, tab_name: str) -> list:
    """处理当前选项卡下的所有实例续期"""
    switch_menu_tab(sb, tab_name)
    cards = wait_and_get_cards(sb, max_wait=8)

    if not cards:
        print(f"ℹ️ [{tab_name}] 页面未发现卡片实例")
        return []

    print(f"📋 [{tab_name}] 共发现 {len(cards)} 个项目")
    results = []

    for item in cards:
        zoom_id = item["zoomId"]
        name = item["name"]
        sleep_before = item["sleep"]
        has_renew = item["hasRenew"]
        disabled = item["disabled"]

        info = f"🖥️ [{tab_name}] {name} | 剩余时间: {sleep_before}"

        if not has_renew:
            print(f"   ⚠️ [{name}] 未找到 Renew 按钮")
            results.append(f"{info}\n📋 结果: ⚠️ 无续期按钮")
            continue

        if disabled:
            print(f"   ⏩ [{name}] Renew 按钮处于冷却状态，无需点击")
            results.append(f"{info}\n📋 结果: ⏩ 冷却中")
            continue

        # 执行点击卡片内的 Renew 按钮
        click_js = f"""
        const card = document.querySelector('[data-zoom-id="{zoom_id}"]') || Array.from(document.querySelectorAll('.bot-grid > div'))[{cards.index(item)}];
        if (card) {{
            const btn = Array.from(card.querySelectorAll('button')).find(b => (b.textContent || '').trim().toLowerCase().includes('renew'));
            if (btn) {{
                btn.scrollIntoView({{ block: 'center' }});
                btn.click();
                return true;
            }}
        }}
        return false;
        """
        success = sb.execute_script(click_js)
        if success:
            print(f"   👉 已触发 [{name}] 的 Renew 按钮")
            sb.sleep(2)
            handle_modal_confirm(sb)
            sb.sleep(3)

            # 重新获取更新后的时间
            updated_cards = wait_and_get_cards(sb, max_wait=4)
            updated = next((c for c in updated_cards if c["name"] == name or c["zoomId"] == zoom_id), None)
            sleep_after = updated["sleep"] if updated else sleep_before

            print(f"   ✅ [{name}] 续期完成 ({sleep_before} → {sleep_after})")
            results.append(f"{info} → {sleep_after}\n📋 结果: ✅ 续期成功")
        else:
            print(f"   ❌ [{name}] 点击失败")
            results.append(f"{info}\n📋 结果: ❌ 点击异常")

    return results


def main():
    print("#" * 35)
    print("      MWS 自动化续期脚本")
    print("#" * 35)

    is_proxy = os.environ.get("IS_PROXY", "false").lower() == "true"
    proxy_server = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1081"
    headless = os.environ.get("HEADLESS", "true").lower() == "true"

    sb_kwargs = {"uc": True, "headless": headless}
    if is_proxy:
        print(f"🔗 配置代理: {proxy_server}")
        sb_kwargs["proxy"] = proxy_server

    with SB(**sb_kwargs) as sb:
        # 设置桌面级高分辨率，防止 SPA 响应式折叠侧边栏
        sb.set_window_size(1600, 900)

        try:
            ip = get_current_ip(proxy_server if is_proxy else "")
            print(f"📍 当前出口 IP: {ip}")
        except Exception:
            pass

        logged_in = False

        # 1. 尝试使用现有 Cookie 恢复会话
        if MWS_COOKIES:
            print("🍪 检测到现有 MWS_COOKIES，尝试免密进入控制台...")
            if inject_cookies_to_browser(sb, MWS_COOKIES) and check_is_logged_in(sb):
                logged_in = True
                print("✅ 成功通过 Cookie 登录！")

        # 2. Cookie 失效或不存在，退回 Discord OAuth 授权
        if not logged_in:
            print("🔄 Cookie 不可用或未提供，启动 Discord OAuth 授权...")
            sb.open(f"{BASE_URL}/login")
            sb.wait_for_ready_state_complete()
            sb.sleep(3)
            if do_discord_login(sb, proxy_server if is_proxy else ""):
                logged_in = True
                print("✅ Discord 授权登录成功！")

        if not logged_in:
            err = "未能成功进入 MWS 控制台，请核查 DISCORD_TOKEN 或网络配置"
            print(f"❌ {err}")
            sb.save_screenshot("login_failed.png")
            send_telegram_message(format_notification("❌ 登录失败", error=err))
            sys.exit(1)

        # 登录成功后保存当前 Cookie，供 Actions 步骤覆盖回写 GitHub Secrets
        save_cookies_to_file(sb, "cookies.json")

        all_results = []

        # 3. 在 SPA 内分别切换处理 Bots 和 Web 模块
        for tab in ["Bots", "Web"]:
            res = process_tab(sb, tab)
            all_results.extend(res)

        # 4. 汇总通知
        if not all_results:
            msg = "未扫描到任何运行中的实例，请核查页面是否正常加载"
            print(f"⚠️ {msg}")
            sb.save_screenshot("empty_cards.png")
            send_telegram_message(format_notification("⚠️ 无可续期项目", extra=msg))
        else:
            summary = "\n\n".join(all_results)
            print("\n" + "=" * 35)
            print(summary)
            print("=" * 35)
            send_telegram_message(format_notification("✅ 执行完成", extra=summary))

    print("🏁 任务结束")


if __name__ == "__main__":
    main()
