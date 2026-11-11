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
DASH_URL      = f"{BASE_URL}/dash"
EMAIL         = os.environ.get("EMAIL") or ""
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""
MWS_COOKIES   = os.environ.get("MWS_COOKIES") or os.environ.get("COOKIES") or ""

DC_TOKEN = ""
if DISCORD_TOKEN:
    _parts = DISCORD_TOKEN.split(",", 1)
    DC_TOKEN = _parts[-1].strip()

DISCORD_UA = (
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
    lines.append(f"👤 账户: {masked_email}")
    lines.append(f"⏱️ 时间: {now}")
    return "\n".join(lines)


def get_current_ip(proxy_server: str = "") -> str:
    proxies = {"http": proxy_server, "https": proxy_server} if proxy_server else None
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()


def inject_cookies_to_browser(sb, cookies_raw: str) -> bool:
    if not cookies_raw:
        return False
    try:
        sb.open(BASE_URL)
        sb.sleep(2)
        target_domain = urllib.parse.urlparse(BASE_URL).hostname

        if cookies_raw.startswith("["):
            cookies = json.loads(cookies_raw)
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
        elif cookies_raw.startswith("{"):
            cookies = json.loads(cookies_raw)
            for k, v in cookies.items():
                sb.driver.add_cookie({"name": k, "value": str(v), "domain": target_domain, "path": "/"})
        else:
            val = cookies_raw.split("=", 1)[-1].strip()
            sb.driver.add_cookie({
                "name": "__Host-mrtcloud_token",
                "value": val,
                "domain": target_domain,
                "path": "/",
                "secure": True
            })

        sb.open(DASH_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        return True
    except Exception as e:
        print(f"⚠️ 注入 Cookies 异常: {e}")
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
        print(f"⚠️ 导出 Cookie 失败: {e}")
    return False


def setup_network_interceptor(sb):
    """注入网络监听钩子，实时捕获页面发出的所有 API 请求与返回结果"""
    js = """
    window.__net_logs = [];
    const origFetch = window.fetch;
    window.fetch = async (...args) => {
        const url = String(args[0]);
        const opts = args[1] || {};
        const method = (opts.method || 'GET').toUpperCase();
        try {
            const resp = await origFetch(...args);
            const clone = resp.clone();
            let body = '';
            try { body = await clone.text(); } catch (e) {}
            window.__net_logs.push({ url, method, status: resp.status, body: body.substring(0, 300) });
            return resp;
        } catch (err) {
            window.__net_logs.push({ url, method, status: 0, error: String(err) });
            throw err;
        }
    };
    """
    try:
        sb.execute_script(js)
    except Exception:
        pass


def get_latest_network_logs(sb) -> list:
    js = "const logs = window.__net_logs || []; window.__net_logs = []; return logs;"
    try:
        return sb.execute_script(js) or []
    except Exception:
        return []


def browser_api_get(sb, path: str) -> dict:
    js = """
    const [path, callback] = [arguments[0], arguments[1]];
    (async () => {
        try {
            const res = await fetch(path, {
                method: 'GET',
                headers: { 'Accept': 'application/json, text/plain, */*' },
                credentials: 'include'
            });
            let text = await res.text();
            let parsed = null;
            try { parsed = JSON.parse(text); } catch (e) { parsed = text; }
            callback({ ok: res.ok, status: res.status, data: parsed });
        } catch (err) {
            callback({ ok: false, status: 0, error: String(err) });
        }
    })();
    """
    try:
        sb.driver.set_script_timeout(15)
        return sb.driver.execute_async_script(js, path)
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


def check_auth(sb) -> tuple[bool, str]:
    res = browser_api_get(sb, "/api/auth/me")
    if res.get("ok") and res.get("status") == 200:
        data = res.get("data") or {}
        username = data.get("username") or data.get("name") or "已登录用户"
        return True, username
    return False, ""


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

    sb.open(f"{BASE_URL}/login")
    sb.wait_for_ready_state_complete()
    sb.sleep(3)

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
    sb.sleep(3)

    sb.open(DASH_URL)
    sb.wait_for_ready_state_complete()
    sb.sleep(4)

    ok, who = check_auth(sb)
    if ok:
        print(f"✅ 登录成功，当前身份: {who}")
        return True
    return False


def switch_sidebar_menu(sb, target_name: str):
    """侧边栏切换 Bot / Web 标签视图"""
    js = f"""
    const btns = Array.from(document.querySelectorAll('aside button, [class*="navItem"]'));
    const btn = btns.find(b => (b.textContent || '').trim().toLowerCase().includes('{target_name.lower()}'));
    if (btn) {{
        btn.click();
        return true;
    }}
    return false;
    """
    sb.execute_script(js)
    sb.sleep(3)


def solve_turnstile_modal(sb, timeout: int = 15) -> bool:
    """针对弹窗中的 Cloudflare Turnstile 执行精准 CDP 物理坐标穿透点击"""
    start_time = time.time()
    clicked_once = False

    while time.time() - start_time < timeout:
        sb.sleep(1)

        # 1. 检查弹窗是否存在
        modal_info = sb.execute_script("""
            const modal = document.querySelector('div[style*="z-index: 10000"]') ||
                          Array.from(document.querySelectorAll('div')).find(d => (d.textContent || '').includes('確認しています'));
            if (!modal) return null;

            // 获取内部卡片容器
            const card = modal.querySelector('div[style*="border-radius"]') || modal.children[0];
            if (!card) return null;

            // 获取 Turnstile 容器（位于标题与取消按钮之间）
            const cancelBtn = Array.from(card.querySelectorAll('button')).find(b => (b.textContent || '').includes('キャンセル'));
            const wrapper = cancelBtn ? cancelBtn.previousElementSibling : card.children[1];

            let targetRect = null;
            if (wrapper) {
                const r = wrapper.getBoundingClientRect();
                targetRect = { left: r.left, top: r.top, width: r.width, height: r.height };
            }

            const tokenInput = modal.querySelector('input[name="cf-turnstile-response"]');
            const token = tokenInput ? tokenInput.value : '';

            return {
                hasModal: true,
                rect: targetRect,
                token: token
            };
        """)

        # 弹窗已自然关闭，说明验证完成并提交成功
        if not modal_info:
            if clicked_once:
                print("   ✅ Turnstile 验证弹窗已自动关闭，请求已发出")
                return True
            continue

        # 检查是否已生成 Token
        if modal_info.get("token"):
            print("   ✅ 成功获取 Turnstile Token，等待网站后端处理...")
            sb.sleep(2)
            return True

        # 2. 如果弹窗出现且尚未点击，计算坐标并派发 CDP 原生物理点击
        rect = modal_info.get("rect")
        if rect and not clicked_once:
            print("   🛡️ 检测到 Turnstile 验证弹窗 [確認しています…]，正在计算物理坐标...")
            # 复选框固定在 widget 左侧 ~32px，垂直居中位置
            click_x = int(rect["left"] + 32)
            click_y = int(rect["top"] + rect["height"] / 2)
            print(f"   🎯 派发 CDP 物理点击 -> ({click_x}, {click_y})")

            try:
                # 派发硬件级鼠标按下与抬起事件（生成 isTrusted: true）
                sb.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                    "type": "mousePressed",
                    "x": click_x,
                    "y": click_y,
                    "button": "left",
                    "clickCount": 1
                })
                time.sleep(0.1)
                sb.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                    "type": "mouseReleased",
                    "x": click_x,
                    "y": click_y,
                    "button": "left",
                    "clickCount": 1
                })
                clicked_once = True
            except Exception as e:
                print(f"   ⚠️ CDP 点击异常: {e}")

        # 打印捕获到的相关请求
        logs = get_latest_network_logs(sb)
        for log in logs:
            if "/renew" in log.get("url", "") or "/start" in log.get("url", ""):
                print(f"      🌐 [API] {log.get('method')} {log.get('url')} -> HTTP {log.get('status')} | {log.get('body')}")
                return True

    return False


def execute_card_button_click(sb, card_sel: str, btn_keywords: list, action_name: str) -> bool:
    """在卡片内定位按钮，等待可用后派发原生点击，随后处理 Turnstile 弹窗"""
    try:
        if not sb.is_element_present(card_sel):
            print(f"   ⚠️ 未定位到卡片: {card_sel}")
            return False

        sb.scroll_to_element(card_sel)
        sb.sleep(1)

        # 动态重搜按钮并等待 disabled 解除
        target_btn = None
        for _ in range(10):
            buttons = sb.find_elements(f"{card_sel} button")
            for b in buttons:
                txt = (b.text or "").strip().lower()
                if any(k.lower() in txt for k in btn_keywords):
                    target_btn = b
                    break

            if not target_btn and len(buttons) >= 2 and "续期" in btn_keywords:
                target_btn = buttons[1]

            if target_btn and target_btn.is_enabled() and target_btn.get_attribute("disabled") is None:
                break
            sb.sleep(1)

        if not target_btn:
            print(f"   ⚠️ 未在卡片内定位到「{action_name}」按钮")
            return False

        btn_txt = (target_btn.text or "").strip()
        print(f"   🎯 点击卡片按钮: [{btn_txt}]")

        # 派发物理事件序列
        js_dispatch_click = """
        const btn = arguments[0];
        btn.scrollIntoView({ block: 'center' });
        ['mouseenter', 'mouseover', 'mousedown', 'mouseup', 'click'].forEach(eventType => {
            btn.dispatchEvent(new MouseEvent(eventType, {
                view: window,
                bubbles: true,
                cancelable: true,
                buttons: 1
            }));
        });
        """
        try:
            target_btn.click()
        except Exception:
            sb.execute_script(js_dispatch_click, target_btn)

        print(f"   👉 已触发「{action_name}」按钮指令，检测弹窗与人机验证...")
        # 核心：调用 CDP 穿透处理 Turnstile 弹窗
        solve_turnstile_modal(sb, timeout=12)
        return True
    except Exception as e:
        print(f"   ❌ 点击「{action_name}」失败: {e}")
        return False


def get_items_via_api(sb, kind: str) -> list:
    path = "/api/bots" if kind == "Bot" else "/api/sites"
    key = "bots" if kind == "Bot" else "sites"
    res = browser_api_get(sb, path)
    if not res.get("ok"):
        return []
    data = res.get("data") or []
    items_list = data if isinstance(data, list) else data.get(key, [])
    parsed = []
    for item in items_list or []:
        parsed.append({
            "kind": kind,
            "id": item.get("id"),
            "name": item.get("name") or item.get("username") or f"id:{item.get('id')}",
            "status": item.get("status") or "UNKNOWN",
            "remaining": (item.get("timer") or {}).get("remaining_hours")
        })
    return parsed


def main():
    print("#" * 35)
    print("      MWS 自动化续期脚本")
    print("#" * 35)

    is_proxy = os.environ.get("IS_PROXY", "false").lower() == "true"
    proxy_server = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1081"
    headless = os.environ.get("HEADLESS", "false").lower() == "true"

    sb_kwargs = {"uc": True, "headless": headless}
    if is_proxy:
        print(f"🔗 配置代理: {proxy_server}")
        sb_kwargs["proxy"] = proxy_server

    with SB(**sb_kwargs) as sb:
        sb.set_window_size(1600, 900)

        try:
            ip = get_current_ip(proxy_server if is_proxy else "")
            print(f"📍 当前出口 IP: {ip}")
        except Exception:
            pass

        logged_in = False

        if MWS_COOKIES:
            print("🍪 检测到现有 MWS_COOKIES，尝试免密登录...")
            if inject_cookies_to_browser(sb, MWS_COOKIES):
                ok, who = check_auth(sb)
                if ok:
                    logged_in = True
                    print(f"✅ 成功复用 Cookie 登录: {who}")
                else:
                    print("⚠️ Cookie 已失效，准备使用 Discord 重新登录...")

        if not logged_in:
            print("🔄 启动 Discord OAuth 授权登录...")
            if do_discord_login(sb, proxy_server if is_proxy else ""):
                logged_in = True

        if not logged_in:
            err = "未能成功进入 MWS 控制台，请核查 DISCORD_TOKEN 或网络配置"
            print(f"❌ {err}")
            sb.save_screenshot("login_failed.png")
            send_telegram_message(format_notification("❌ 登录失败", error=err))
            sys.exit(1)

        setup_network_interceptor(sb)
        save_cookies_to_file(sb, "cookies.json")

        all_results = []
        total_count = 0
        success_count = 0

        for kind, menu_name in [("Bot", "Bot"), ("Site", "Web")]:
            items = get_items_via_api(sb, kind)
            print(f"\n📋 共获取到 {len(items)} 个 {kind} 实例")
            if not items:
                continue

            switch_sidebar_menu(sb, menu_name)

            for it in items:
                total_count += 1
                oid = it["id"]
                name = it["name"]
                rem_before = it["remaining"]
                status = str(it["status"]).upper()
                is_stopped = (status == "STOPPED")

                status_icon = "🟢" if status == "RUNNING" else "🔴"
                info = f"{status_icon} [{kind}] {name} (id:{oid}) | 剩余: {rem_before}h"

                # 运行中且剩余时间充裕（>=166h）
                if rem_before is not None and int(rem_before) >= 166 and not is_stopped:
                    print(f"   ⏩ [{name}] 剩余时间充足 ({rem_before}h)，无需重复操作")
                    all_results.append(f"{info}\n📋 结果: ⏩ 冷却中 (无需续期)")
                    success_count += 1
                    continue

                print(f"👉 正在处理 [{name}] (当前状态: {status}, 剩余: {rem_before}h)...")
                zoom_attr = f"{kind.lower()}-{oid}"
                card_sel = f'[data-zoom-id="{zoom_attr}"]'

                if not sb.is_element_present(card_sel):
                    print(f"   ⚠️ 未定位到卡片元素: {card_sel}")
                    all_results.append(f"{info}\n📋 结果: ⚠️ 未定位到卡片")
                    continue

                # 步骤 1：直接点击「续期」触发 Turnstile 弹窗
                execute_card_button_click(sb, card_sel, ["续期", "renew", "延長"], "续期")

                # 步骤 2：轮询等待后端数据库同步
                is_renewed = False
                rem_after = rem_before
                for _ in range(4):
                    sb.sleep(3)
                    fresh_items = get_items_via_api(sb, kind)
                    fresh_it = next((x for x in fresh_items if str(x["id"]) == str(oid)), None)
                    if fresh_it:
                        rem_after = fresh_it["remaining"]
                        if rem_after is not None and rem_before is not None:
                            if int(rem_after) > int(rem_before) or int(rem_after) >= 160:
                                is_renewed = True
                                break

                # 步骤 3：若原本处于 STOPPED 状态，在续期成功后拉起服务
                if is_stopped:
                    print("   ⚡ 正在拉起实例服务...")
                    execute_card_button_click(sb, card_sel, ["启动", "start"], "启动")
                    sb.sleep(5)

                fresh_items = get_items_via_api(sb, kind)
                final_it = next((x for x in fresh_items if str(x["id"]) == str(oid)), None)
                final_status = str(final_it["status"]).upper() if final_it else status
                final_rem = final_it["remaining"] if final_it else rem_after

                if is_renewed or (final_rem is not None and int(final_rem) >= 160):
                    success_count += 1
                    print(f"   ✅ [{name}] 处理完成: {rem_before}h → {final_rem}h [{final_status}]")
                    all_results.append(f"{info} → {final_rem}h [{final_status}]\n📋 结果: ✅ 续期成功")
                else:
                    print(f"   ❌ [{name}] 续期未生效 (当前: {final_rem}h [{final_status}])")
                    all_results.append(f"{info} → {final_rem}h [{final_status}]\n📋 结果: ❌ 续期未生效")

        if total_count == 0:
            msg = "账号下未获取到任何 Bot 或 Site 实例"
            print(f"⚠️ {msg}")
            send_telegram_message(format_notification("⚠️ 无可续期项目", extra=msg))
        else:
            summary = "\n\n".join(all_results)
            print("\n" + "=" * 35)
            print(summary)
            print("=" * 35)
            status_text = "✅ 自动续期完成" if success_count == total_count else f"⚠️ 执行完毕 ({success_count}/{total_count})"
            send_telegram_message(format_notification(status_text, extra=summary))

    print("🏁 任务结束")


if __name__ == "__main__":
    main()
