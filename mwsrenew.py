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

# 基础与平台配置
BASE_URL      = os.environ.get("BASE_URL", "https://cloud.m-ws.cc").rstrip("/")
EMAIL         = os.environ.get("EMAIL") or ""
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""
MWS_COOKIES   = os.environ.get("MWS_COOKIES") or os.environ.get("COOKIES") or ""

# 解析 Discord Token
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
        print("⚠️ Telegram 配置不全，跳过发送")
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
        masked_email = "MWS 用户"

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
    lines.append(f"👤 账户标识: {masked_email}")
    lines.append(f"⏱️ 执行时间: {now}")
    return "\n".join(lines)


def get_current_ip(proxy_server: str = "") -> str:
    proxies = {"http": proxy_server, "https": proxy_server} if proxy_server else None
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()


def inject_cookies_to_browser(sb, cookies_str: str) -> bool:
    """将外部传入的 cookies 注入浏览器并测试登录有效性"""
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
        sb.sleep(3)
        return True
    except Exception as e:
        print(f"⚠️ 注入 Cookies 发生异常: {e}")
        return False


def save_cookies_to_file(sb, filepath: str = "cookies.json"):
    """保存当前有效 Session Cookie 到本地供 Actions 回写 Secret"""
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
    """检查当前是否处于登录状态"""
    try:
        url = sb.get_current_url()
        if "/login" in url or "discord.com" in url:
            return False
        page_source = sb.get_page_source()
        if "Log in with Discord" in page_source:
            return False
        if any(keyword in page_source for keyword in ["Your bots", "Your sites", "Create bot", "Create site", "SLEEP IN"]):
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
    """通过 Discord Token 请求授权接口拿到回调 URL"""
    if "/oauth2/authorize" in authorize_url and "/api/" not in authorize_url:
        authorize_url = authorize_url.replace("/oauth2/authorize", "/api/v9/oauth2/authorize", 1)

    _parsed = urllib.parse.urlparse(authorize_url)
    _params = urllib.parse.parse_qs(_parsed.query)
    _redirect_uri = _params.get("redirect_uri", [f"{BASE_URL}/api/auth/discord/callback"])[0]
    _client_id = _params.get("client_id", [""])[0]
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
    """通过自动化点击与 Discord Token 完成登录流程"""
    if not DC_TOKEN:
        print("❌ 未提供 DISCORD_TOKEN，无法进行 OAuth 登录")
        return False

    print("🔑 正在定位并点击 Discord 登录入口...")
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
    for _ in range(12):
        sb.sleep(1)
        curr = sb.get_current_url()
        authorize_url = extract_authorize_url_from_browser(curr)
        if authorize_url:
            break

    if not authorize_url:
        print(f"❌ 未能从浏览器重定向提取到 Discord 授权 URL (当前: {sb.get_current_url()})")
        return False

    state_match = STATE_RE.search(authorize_url)
    state = state_match.group(1) if state_match else f"mws-{int(time.time() * 1000)}"

    location = discord_authorize(state, authorize_url, proxy_server)
    if not location:
        return False

    print("↩️ 携带授权码访问回调中...")
    sb.uc_open_with_reconnect(location, reconnect_time=4)
    sb.wait_for_ready_state_complete()
    sb.sleep(4)
    return check_is_logged_in(sb)


def handle_modal_confirm(sb):
    """检测并确认可能弹出的二次确认对话框"""
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


def process_section_renew(sb, section_name: str, target_url: str) -> list:
    """在指定模块页面（Bots 或 Web）扫描卡片并点击 Renew 按钮"""
    print(f"\n📂 正在进入 [{section_name}] 页面: {target_url}")
    sb.open(target_url)
    sb.wait_for_ready_state_complete()
    sb.sleep(4)

    # 扫描当前页面所有包含 Renew 的卡片结构
    js_scan = """
    return (() => {
        const renewButtons = Array.from(document.querySelectorAll('button')).filter(b => {
            const txt = (b.textContent || '').trim().toLowerCase();
            return txt.includes('renew');
        });

        return renewButtons.map((btn, idx) => {
            btn.setAttribute('data-mws-renew-id', String(idx));

            let card = btn;
            while (card && card !== document.body) {
                const txt = card.textContent || '';
                if (txt.includes('SLEEP IN') || txt.includes('MEMORY') || txt.includes('RUNNING')) {
                    if (card.parentElement && (
                        card.parentElement.textContent.includes('Your') ||
                        card.classList.toString().includes('card') ||
                        card.parentElement.classList.toString().includes('grid')
                    )) {
                        break;
                    }
                }
                card = card.parentElement;
            }
            if (!card) card = btn.parentElement.parentElement;

            const cardText = card.innerText || card.textContent || '';
            const lines = cardText.split('\\n').map(l => l.trim()).filter(Boolean);
            const name = lines.length > 0 ? lines[0] : '未知服务';

            let sleep = '?';
            const sleepMatch = cardText.match(/SLEEP\\s+IN\\s*([^\\n\\r]+)/i) || cardText.match(/(\\d+\\s*h)/i);
            if (sleepMatch) {
                sleep = sleepMatch[1].trim();
            }

            let status = '未知';
            if (/RUNNING/i.test(cardText)) status = 'RUNNING';
            else if (/STOPPED/i.test(cardText)) status = 'STOPPED';
            else if (/STARTING/i.test(cardText)) status = 'STARTING';

            return {
                index: idx,
                name: name,
                status: status,
                sleep: sleep,
                disabled: btn.disabled || btn.getAttribute('aria-disabled') === 'true'
            };
        });
    })();
    """

    cards = sb.execute_script(js_scan) or []
    if not cards:
        print(f"ℹ️ [{section_name}] 未发现可续期的卡片")
        return []

    print(f"📋 [{section_name}] 共发现 {len(cards)} 个项目:")
    section_results = []

    for item in cards:
        idx = item.get("index")
        name = item.get("name", "未知")
        status = item.get("status", "未知")
        sleep_before = item.get("sleep", "?")
        disabled = item.get("disabled", False)

        status_icon = "🟢" if status == "RUNNING" else "🟡"
        info_header = f"{status_icon} [{section_name}] {name} | 状态: {status} | 剩余: {sleep_before}"

        if disabled:
            print(f"   ⏩ {name} Renew 按钮为禁用状态，无需操作")
            section_results.append(f"{info_header}\n📋 结果: ⏩ 冷却中无需续期")
            continue

        # 执行点击
        try:
            sb.execute_script(f"""
                const btn = document.querySelector('button[data-mws-renew-id="{idx}"]');
                if (btn) btn.click();
            """)
            print(f"   👉 已点击 [{name}] 的 Renew 按钮")
            sb.sleep(2)
            handle_modal_confirm(sb)
            sb.sleep(3)

            # 重新获取剩余时间校验
            updated_cards = sb.execute_script(js_scan) or []
            updated = next((c for c in updated_cards if c.get("index") == idx or c.get("name") == name), None)
            sleep_after = updated.get("sleep", sleep_before) if updated else sleep_before

            print(f"   ✅ [{name}] 续期指令完成 (剩余时间: {sleep_before} → {sleep_after})")
            section_results.append(f"{info_header} → {sleep_after}\n📋 结果: ✅ 续期成功")
        except Exception as e:
            print(f"   ❌ [{name}] 点击失败: {e}")
            section_results.append(f"{info_header}\n📋 结果: ❌ 操作异常: {e}")

    return section_results


def main():
    print("#" * 35)
    print("      MWS 自动化续期脚本")
    print("#" * 35)

    is_proxy = os.environ.get("IS_PROXY", "false").lower() == "true"
    proxy_server = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1081"
    headless = os.environ.get("HEADLESS", "true").lower() == "true"

    sb_kwargs = {"uc": True, "headless": headless}
    if is_proxy:
        print(f"🔗 已配置代理: {proxy_server}")
        sb_kwargs["proxy"] = proxy_server
    else:
        print("🍭 未启用代理，使用直连模式")

    with SB(**sb_kwargs) as sb:
        try:
            ip = get_current_ip(proxy_server if is_proxy else "")
            print(f"📍 当前出口 IP: {ip}")
        except Exception as e:
            print(f"⚠️ 出口 IP 查询略过: {e}")

        logged_in = False

        # 优先使用现有 Cookies 恢复会话
        if MWS_COOKIES:
            print("🍪 检测到现有 MWS_COOKIES，尝试免密进入控制台...")
            if inject_cookies_to_browser(sb, MWS_COOKIES) and check_is_logged_in(sb):
                logged_in = True
                print("✅ 成功通过 Cookie 复用会话！")

        # Cookie 不存在或已过期，退回 Discord 模拟授权登录
        if not logged_in:
            print("🔄 Cookie 不可用或未提供，启动 Discord OAuth 授权...")
            sb.open(f"{BASE_URL}/login")
            sb.wait_for_ready_state_complete()
            sb.sleep(3)
            if do_discord_login(sb, proxy_server if is_proxy else ""):
                logged_in = True
                print("✅ Discord 登录成功！")

        if not logged_in:
            err = "未能成功进入 MWS 控制台，请核查 DISCORD_TOKEN 或网络状态"
            print(f"❌ {err}")
            sb.save_screenshot("login_failed.png")
            send_telegram_message(format_notification("❌ 登录失败", error=err))
            sys.exit(1)

        # 登录成功，将最新的 Session Cookies 导出到本地文件，供 Actions 回写 Secrets
        save_cookies_to_file(sb, "cookies.json")

        all_results = []

        # 1. 扫描与续期 Bots 区域（首页/Bot 视图）
        bot_res = process_section_renew(sb, "Bots", f"{BASE_URL}/")
        all_results.extend(bot_res)

        # 2. 扫描与续期 Web 区域（Web 视图）
        web_res = process_section_renew(sb, "Web", f"{BASE_URL}/web")
        all_results.extend(web_res)

        # 汇总结算
        if not all_results:
            msg = "未检测到任何正在运行的 Bot 或 Web 实例"
            print(f"⚠️ {msg}")
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
