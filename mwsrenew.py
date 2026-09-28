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
    """支持 JSON 数组、JSON 对象或纯 token 字符串注入"""
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
            # 兼容纯 token 或 key=value
            val = cookies_raw.split("=", 1)[-1].strip()
            sb.driver.add_cookie({
                "name": "__Host-mrtcloud_token",
                "value": val,
                "domain": target_domain,
                "path": "/",
                "secure": True
            })

        sb.open(f"{BASE_URL}/")
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        return True
    except Exception as e:
        print(f"⚠️ 注入 Cookies 异常: {e}")
        return False


def save_cookies_to_file(sb, filepath: str = "cookies.json"):
    """持久化保存当前有效的 Cookies"""
    try:
        cookies = sb.driver.get_cookies()
        if cookies:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            print(f"💾 成功导出 {len(cookies)} 个 Cookie 到 {filepath}")
            return True
    except Exception as e:
        print(f"⚠️ 导出 Cookie 文件失败: {e}")
    return False


def browser_api_request(sb, path: str, method: str = "GET", body: dict = None) -> dict:
    """在具备真实 Cloudflare 会话与 Cookie 态的浏览器内部执行 fetch 调用"""
    js = """
    const [path, method, body, callback] = [arguments[0], arguments[1], arguments[2], arguments[3]];
    (async () => {
        try {
            const options = {
                method: method,
                headers: {
                    'Accept': 'application/json, text/plain, */*'
                },
                credentials: 'include'
            };
            if (body && method !== 'GET') {
                options.headers['Content-Type'] = 'application/json';
                options.body = JSON.stringify(body);
            }
            const res = await fetch(path, options);
            let text = await res.text();
            let parsed = null;
            try {
                parsed = JSON.parse(text);
            } catch (e) {
                parsed = text;
            }
            callback({ ok: res.ok, status: res.status, data: parsed });
        } catch (err) {
            callback({ ok: false, status: 0, error: String(err) });
        }
    })();
    """
    try:
        sb.driver.set_script_timeout(20)
        return sb.driver.execute_async_script(js, path, method, body)
    except Exception as e:
        return {"ok": False, "status": 0, "error": str(e)}


def check_auth(sb) -> tuple[bool, str]:
    """通过 /api/auth/me 校验登录态"""
    res = browser_api_request(sb, "/api/auth/me", method="GET")
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
    sb.sleep(4)

    # 显式打开主控制台
    sb.open(f"{BASE_URL}/")
    sb.wait_for_ready_state_complete()
    sb.sleep(3)

    ok, who = check_auth(sb)
    if ok:
        print(f"✅ 登录成功，当前身份: {who}")
        return True
    return False


def get_items(sb, kind: str) -> list:
    """拉取 Bot 或 Site 列表"""
    path = "/api/bots" if kind == "Bot" else "/api/sites"
    key = "bots" if kind == "Bot" else "sites"
    res = browser_api_request(sb, path, method="GET")

    if not res.get("ok"):
        print(f"⚠️ 拉取 {kind} 列表失败: HTTP {res.get('status')} {res.get('error')}")
        return []

    data = res.get("data") or []
    items_list = data if isinstance(data, list) else data.get(key, [])
    parsed = []
    for item in items_list or []:
        oid = item.get("id")
        name = item.get("name") or item.get("username") or f"id:{oid}"
        timer = item.get("timer") or {}
        rem = timer.get("remaining_hours")
        status = item.get("status") or "UNKNOWN"
        parsed.append({
            "kind": kind,
            "id": oid,
            "name": name,
            "status": status,
            "remaining": rem
        })
    return parsed


def renew_item(sb, kind: str, oid: str or int) -> tuple[bool, int, str]:
    """通过 API 触发续期"""
    path = f"/api/bots/{oid}/renew" if kind == "Bot" else f"/api/sites/{oid}/renew"
    res = browser_api_request(sb, path, method="POST")
    ok = res.get("ok", False) and (res.get("status") == 200)
    return ok, res.get("status", 0), str(res.get("data") or res.get("error") or "")


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
        sb.set_window_size(1600, 900)

        try:
            ip = get_current_ip(proxy_server if is_proxy else "")
            print(f"📍 当前出口 IP: {ip}")
        except Exception:
            pass

        logged_in = False

        # 1. 尝试使用 MWS_COOKIES 恢复会话
        if MWS_COOKIES:
            print("🍪 检测到现有 MWS_COOKIES，尝试免密登录...")
            if inject_cookies_to_browser(sb, MWS_COOKIES):
                ok, who = check_auth(sb)
                if ok:
                    logged_in = True
                    print(f"✅ 成功复用 Cookie 登录: {who}")
                else:
                    print("⚠️ Cookie 已失效或过期，准备使用 Discord 重新登录...")

        # 2. 回退至 Discord OAuth 模拟授权
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

        # 3. 登录成功，将最新的 Cookies 导出到本地文件，供 Actions 回写覆盖 Secrets
        save_cookies_to_file(sb, "cookies.json")

        all_results = []
        total_count = 0
        success_count = 0

        # 4. 分别查询与执行 Bot 和 Site 的续期
        for kind in ["Bot", "Site"]:
            items = get_items(sb, kind)
            print(f"\n📋 共获取到 {len(items)} 个 {kind} 实例")

            for it in items:
                total_count += 1
                oid = it["id"]
                name = it["name"]
                rem_before = it["remaining"]
                status = it["status"]

                status_icon = "🟢" if status.lower() == "running" else "🟡"
                info = f"{status_icon} [{kind}] {name} (id:{oid}) | 剩余: {rem_before}h"

                # 触发续期 API
                ok, code, body = renew_item(sb, kind, oid)
                if ok:
                    success_count += 1
                    # 重新拉取获取刷新后的时间
                    sb.sleep(2)
                    fresh_items = get_items(sb, kind)
                    fresh_it = next((x for x in fresh_items if str(x["id"]) == str(oid)), None)
                    rem_after = fresh_it["remaining"] if fresh_it else "已重置"

                    print(f"   ✅ [{name}] 续期成功: {rem_before}h → {rem_after}h")
                    all_results.append(f"{info} → {rem_after}h\n📋 结果: ✅ 续期成功")
                else:
                    print(f"   ❌ [{name}] 续期失败: HTTP {code} {body}")
                    all_results.append(f"{info}\n📋 结果: ❌ 续期失败 (HTTP {code})")

        # 5. 汇总处理
        if total_count == 0:
            msg = "账号下未获取到任何 Bot 或 Site 实例"
            print(f"⚠️ {msg}")
            send_telegram_message(format_notification("⚠️ 无可续期项目", extra=msg))
        else:
            summary = "\n\n".join(all_results)
            print("\n" + "=" * 35)
            print(summary)
            print("=" * 35)
            status_text = "✅ 全部续期完成" if success_count == total_count else f"⚠️ 完成 ({success_count}/{total_count})"
            send_telegram_message(format_notification(status_text, extra=summary))

    print("🏁 任务结束")


if __name__ == "__main__":
    main()
