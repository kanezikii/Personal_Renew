#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, json, requests, urllib
from seleniumbase import SB

# 环境变量配置
EMAIL         = os.environ.get("EMAIL") or ""           # 邮箱，仅用于通知
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN") or ""   # Discord Token
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""      # TG chat id
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""    # TG bot token

BASE_URL = "https://cloud.puratya.com"
API_BASE = "https://cloud-api.puratya.com"

# 解析 DISCORD_TOKEN
DC_TOKEN = ""
if DISCORD_TOKEN:
    _parts = DISCORD_TOKEN.split(",", 1)
    DC_TOKEN = _parts[-1].strip()
else:
    print("❌ 未配置 DISCORD_TOKEN，脚本退出")
    sys.exit(1)


def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")


def format_notification(status: str, extra: str = "", error: str = "", expiry_date: str = "") -> str:
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****'

    lines = [
        "🚀 MWS 续期通知",
        "",
        f"{status}",
    ]
    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")
    if extra:
        lines.append("")
        lines.append(extra)
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append("")
    lines.append(f"👤 登录账户: {masked_email}")
    lines.append(f"⏱️ 登录时间: {now}")
    return "\n".join(lines)


def strip_remaining(t: str) -> str:
    return re.sub(r"\s*remaining\s*$", "", t or "", flags=re.I).strip()


def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()


# Discord OAuth 配置（与 Pingless 相同）
DISCORD_CLIENT_ID   = "1461061267069600018"
OAUTH_REDIRECT_URI  = f"{BASE_URL}/api/auth/discord/callback"
OAUTH_SCOPE         = "identify email"
DISCORD_API         = "https://discord.com/api/v9/oauth2/authorize"
DISCORD_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
STATE_RE = re.compile(r"[?&]state=([^&]+)")


def fetch_authorize_url_from_site() -> str:
    """从站点后端获取 Discord OAuth 授权 URL。
    Puratya 的后端 API 在独立的 cloud-api.puratya.com 子域，入口是 /auth/login。
    """
    entries = [
        (f"{API_BASE}/auth/login", "cloud-api/auth/login"),
        (f"{BASE_URL}/api/discord/login", "主站/api/discord/login"),
    ]
    for url, label in entries:
        try:
            resp = requests.get(
                url,
                allow_redirects=False,
                timeout=15,
                headers={"User-Agent": DISCORD_UA},
            )
            location = resp.headers.get("Location", "")
            if location and "discord.com" in location:
                print(f"🔗 从 {label} 重定向获取到授权 URL")
                return location
            if resp.text and "discord.com/oauth2/authorize" in resp.text:
                m = re.search(r'(https://discord\.com/oauth2/authorize\?[^\s"\'<>]+)', resp.text)
                if m:
                    print(f"🔗 从 {label} 页面内容提取授权 URL")
                    return m.group(1)
        except Exception as e:
            print(f"⚠️ HTTP 获取授权 URL 失败({label}): {e}")
    return ""


def discord_authorize(state: str, authorize_url: str = "") -> str:
    if not authorize_url:
        query = urllib.parse.urlencode({
            "client_id":     DISCORD_CLIENT_ID,
            "response_type": "code",
            "redirect_uri":  OAUTH_REDIRECT_URI,
            "scope":         OAUTH_SCOPE,
            "state":         state,
        })
        authorize_url = f"{DISCORD_API}?{query}"

    if "/oauth2/authorize" in authorize_url and "/api/" not in authorize_url:
        authorize_url = authorize_url.replace("/oauth2/authorize", "/api/v9/oauth2/authorize", 1)

    _parsed = urllib.parse.urlparse(authorize_url)
    _params = urllib.parse.parse_qs(_parsed.query)
    _redirect_uri = _params.get("redirect_uri", [OAUTH_REDIRECT_URI])[0]
    _client_id = _params.get("client_id", [DISCORD_CLIENT_ID])[0]
    _scope = _params.get("scope", [OAUTH_SCOPE])[0]

    _query = _parsed.query or ""
    _missing = []
    if "response_type=" not in _query:
        _missing.append("response_type=code")
    if "scope=" not in _query:
        _missing.append(f"scope={urllib.parse.quote(_scope)}")
    if _missing:
        sep = "&" if _query else "?"
        authorize_url += f"{sep}{'&'.join(_missing)}"

    print(f"🔗 scope={_scope} | redirect_uri={_redirect_uri}")

    referer = (
        "https://discord.com/oauth2/authorize?" +
        urllib.parse.urlencode({
            "client_id":     _client_id,
            "redirect_uri":  _redirect_uri,
            "response_type": "code",
            "scope":         _scope,
            "state":         state,
        })
    )

    headers = {
        "accept":           "*/*",
        "authorization":    DC_TOKEN,
        "content-type":     "application/json",
        "origin":           "https://discord.com",
        "referer":          referer,
        "user-agent":       DISCORD_UA,
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

    proxies = None
    _is_proxy = os.environ.get("IS_PROXY", "false").lower() == "true"
    _proxy_server = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1081"
    if _is_proxy:
        proxies = {"http": _proxy_server, "https": _proxy_server}

    try:
        resp = requests.post(authorize_url, headers=headers, data=body, proxies=proxies, timeout=20)
        if resp.status_code != 200:
            print(f"❌ Discord OAuth2 授权失败: HTTP {resp.status_code} - {resp.text[:300]}")
            if resp.status_code == 401:
                send_telegram_message(format_notification(
                    "❌ Discord Token 已失效",
                    error="401 Unauthorized — 请更新 DISCORD_TOKEN",
                ))
            return ""
        resp_data = {}
        try:
            resp_data = resp.json()
            location = resp_data.get("location", "")
        except Exception:
            body_text = resp.text.strip()
            print(f"⚠️ Discord 返回非 JSON 响应（前200字符）：{body_text[:200]}")
            location_match = re.search(r'"location"\s*:\s*"([^"]+)"', body_text)
            if location_match:
                location = location_match.group(1)
            else:
                location = ""
    except Exception as e:
        print(f"❌ Discord OAuth2 授权异常: {e}")
        return ""

    if not location:
        print(f"❌ 授权响应中未找到 location 字段: {resp_data or resp.text[:200]}")
        return ""

    masked = re.sub(r"code=[^&]+", "code=***", location)
    print(f"✅ 拿到回调 URL: {masked}")
    return location


def click_activity_confirm(sb, in_modal: bool = False) -> tuple[bool, str]:
    # MWS 可能不需要活动确认，保留以防万一
    if in_modal:
        for _ in range(10):
            js_code = """
            const modal = document.querySelector('#host-activity-confirmation-modal');
            if (!modal) return false;
            const candidates = Array.from(modal.querySelectorAll('button'));
            const targets = [
                'je confirme utiliser ce serveur',
                'i confirm i am using this server',
                'confirm',
            ];
            for (const btn of candidates) {
                const text = (btn.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                if (targets.some(item => text.includes(item))) {
                    btn.scrollIntoView({ block: 'center' });
                    btn.click();
                    return true;
                }
            }
            return false;
            """
            try:
                clicked = sb.execute_script(js_code)
                if clicked:
                    return True, 'modal::text-match'
            except Exception:
                pass
            sb.sleep(0.5)
        return False, ""

    target_selectors = [
        'button[data-activity-confirm]',
        'button:contains("Confirm")',
        'button:contains("确认")',
    ]
    for selector in target_selectors:
        try:
            if sb.is_element_present(selector):
                sb.click(selector)
                return True, selector
        except Exception:
            pass
    return False, ""


def extract_authorize_url_from_browser(current_url: str) -> str:
    """从浏览器当前 URL 提取 Discord OAuth 授权 URL。
    优先用浏览器地址栏里的 URL（对应浏览器已持有的 oauth_state cookie），
    这样后续打开回调链接时 cloud-api 才能校验通过。
    """
    if not current_url:
        return ""
    parsed = urllib.parse.urlparse(current_url)
    path = parsed.path
    # 已在 oauth2/authorize 页面（路径匹配，而非 query 里的 redirect_to）
    if path.startswith("/oauth2/authorize"):
        return current_url
    # discord.com/login?redirect_to=/oauth2/authorize?client_id=...&redirect_uri=...
    if path.startswith("/login"):
        raw_qs = parsed.query
        idx = raw_qs.find("redirect_to=")
        if idx >= 0:
            val = raw_qs[idx + len("redirect_to="):]
            # val 是完整的 redirect_to 值（含后续 oauth 参数）
            if val.startswith("/"):
                return f"https://discord.com{val}"
            if val.startswith("http"):
                return val
    return ""


def do_discord_login(sb) -> bool:
    """通过 Discord OAuth 完成 MWS 登录"""

    # 第1步：点击登录按钮，尝试让浏览器跳转到 Discord
    login_selectors = [
        'button:contains("Log in with Discord")',
        'a:contains("Log in with Discord")',
        'a:contains("Continue with Discord")',
        'a[href*="/api/discord/login"]',
        'a:contains("Discord")',
        'a[href*="discord"]',
    ]
    for selector in login_selectors:
        try:
            if sb.is_element_visible(selector):
                sb.click(selector)
                print(f"✅ 已点击 Discord 登录按钮: {selector}")
                break
        except Exception:
            pass

    sb.sleep(5)
    current_url = sb.get_current_url()

    # 第2步：尝试从浏览器 URL 提取授权 URL（浏览器已有对应 oauth_state cookie）
    authorize_url = extract_authorize_url_from_browser(current_url)

    if not authorize_url:
        # 第3步：浏览器直接访问 cloud-api/auth/login，让后端设置 oauth_state cookie
        print("🔄 浏览器未跳转到 Discord，直接访问 cloud-api/auth/login...")
        sb.open(f"{API_BASE}/auth/login")
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        current_url = sb.get_current_url()
        authorize_url = extract_authorize_url_from_browser(current_url)
        if not authorize_url:
            # 尝试等待重定向
            for _ in range(10):
                current_url = sb.get_current_url()
                authorize_url = extract_authorize_url_from_browser(current_url)
                if authorize_url:
                    break
                sb.sleep(1)

    if not authorize_url:
        # 第4步：最后尝试通过 HTTP 获取授权 URL（会生成新 state，需注入 cookie）
        print("🔄 浏览器手动访问失败，通过 HTTP 获取授权 URL...")
        authorize_url = fetch_authorize_url_from_site()

    if not authorize_url:
        print(f"❌ 无法获取 Discord 授权 URL，当前 URL: {sb.get_current_url()}")
        sb.save_screenshot("login_no_auth_url.png")
        return False

    print(f"🔗 授权 URL: {authorize_url[:120]}...")

    state_match = STATE_RE.search(authorize_url)
    state = state_match.group(1) if state_match else f"mws-{int(time.time() * 1000)}"
    print(f"🔎 OAuth state: {state[:30]}...")

    # 如果授权 URL 是从浏览器获取的（第2/3步），浏览器已有 cookie
    # 如果是从 HTTP 获取的（第4步），需要将 oauth_state cookie 注入浏览器
    location = discord_authorize(state, authorize_url=authorize_url)
    if not location:
        return False

    print("↩️ 携带授权码打开回调链接...")
    sb.uc_open_with_reconnect(location, reconnect_time=4)
    time.sleep(3)
    return _verify_login(sb)


def _verify_login(sb) -> bool:
    """验证是否登录成功"""
    url = sb.get_current_url()
    if "/error/banned" in url:
        print("🚫 账号已被封禁")
        sb.save_screenshot("login_banned.png")
        return False

    base_url_lower = BASE_URL.lower()
    if base_url_lower not in url.lower():
        print(f"❌ 回调后未跳转至 MWS，当前 URL：{url}")
        sb.save_screenshot("login_no_redirect.png")
        return False

    try:
        body_text = sb.get_text("body")
    except Exception:
        body_text = ""
    if "fraud" in body_text.lower():
        print("🚫 触发风控（fraud attempt），可能是 IP 被拦截")
        sb.save_screenshot("login_fraud.png")
        return False

    for _ in range(30):
        url = sb.get_current_url()
        path = urllib.parse.urlparse(url).path
        if base_url_lower in url.lower() and path not in ["/login", "/login/discord"]:
            print(f"✅ Discord 登录成功！当前页面：{url}")
            return True
        time.sleep(0.5)

    print(f"❌ 登录超时或未跳转成功，最终停留在：{url}")
    try:
        body_text = sb.get_text("body")
        print(f"📄 页面正文片段：{body_text[:200].strip()!r}")
    except Exception:
        pass
    sb.save_screenshot("login_timeout.png")
    return False


def get_browser_cookies(sb) -> dict:
    """从浏览器提取 cookie（用于 HTTP API 后手）"""
    try:
        return {c['name']: c['value'] for c in sb.driver.get_cookies()}
    except Exception:
        return {}


def get_bots_via_api(cookies: dict) -> list:
    """通过 dashboard API 获取所有 bot"""
    try:
        resp = requests.get(
            f"{BASE_URL}/api/dashboard",
            cookies=cookies,
            timeout=15,
            headers={"Accept": "application/json, text/plain, */*", "User-Agent": DISCORD_UA},
        )
        if resp.status_code != 200:
            print(f"   ⚠️ dashboard API HTTP {resp.status_code}")
            return []
        data = resp.json()
        return data.get("bots", {}).get("bots", [])
    except Exception as e:
        print(f"⚠️ 获取 dashboard API 失败: {e}")
        return []


def renew_via_api(cookies: dict, bot_id) -> bool:
    """HTTP API 后手续期"""
    try:
        resp = requests.post(
            f"{BASE_URL}/api/bots/{bot_id}/renew",
            cookies=cookies,
            timeout=20,
            headers={"Accept": "application/json, text/plain, */*", "User-Agent": DISCORD_UA},
        )
        ok = resp.status_code == 200
        if ok:
            print(f"   ✅ HTTP API 续期成功 (id={bot_id})")
        else:
            print(f"   ❌ HTTP API 续期失败: HTTP {resp.status_code}")
        return ok
    except Exception as e:
        print(f"   ❌ HTTP API 续期异常: {e}")
        return False


def click_renew_on_page(sb, bot_id) -> bool:
    """在页面上点击指定 bot 的 Renew 按钮"""
    try:
        return bool(sb.execute_script(f"""
            const card = document.querySelector('[data-zoom-id="bot-{bot_id}"]');
            if (!card) return false;
            const renewBtn = Array.from(card.querySelectorAll('button'))
                .find(b => (b.textContent || '').includes('Renew'));
            if (!renewBtn) return false;
            renewBtn.click();
            return true;
        """))
    except Exception as e:
        print(f"   ⚠️ 页面点击 Renew 失败: {e}")
        return False


def get_bots_from_page(sb) -> list:
    """从页面读取所有 bot 卡片信息（API 不可用时的兜底）"""
    try:
        cards = sb.execute_script("""
            return Array.from(document.querySelectorAll('[class*="FrKoNG__card"]')).map(card => {
                const zoomId = card.getAttribute('data-zoom-id') || '';
                const idMatch = zoomId.match(/bot-(\\d+)/);
                const titleEl = card.querySelector('[class*="__title"]');
                const pillEl = card.querySelector('[class*="__pill"]');
                const metrics = Array.from(card.querySelectorAll('[class*="__metric"]'))
                    .map(m => m.textContent.trim());
                let sleepH = '?';
                for (let i = 0; i < metrics.length - 1; i++) {
                    if (metrics[i].includes('Sleep')) sleepH = metrics[i + 1];
                }
                return {
                    id: idMatch ? idMatch[1] : '',
                    name: titleEl ? titleEl.textContent.trim() : '',
                    status: (pillEl ? pillEl.childNodes[0].textContent.trim() : ''),
                    sleep_hours: sleepH,
                };
            });
        """) or []
        return [c for c in cards if c.get("id")]
    except Exception as e:
        print(f"⚠️ 从页面读取 bot 失败: {e}")
        return []


def process_bot(sb, cookies, idx, total, bot) -> str:
    """处理单个 bot 的续期（页面点击为主，HTTP API 为后手）"""
    bot_id = bot.get("id")
    name = bot.get("name") or "未知"
    status = bot.get("status") or "未知"
    timer = bot.get("timer") or {}
    sleep_before = timer.get("remaining_hours", "?")
    last_renewed_before = timer.get("last_renewed_at", "")

    status_icon = "🟢" if status == "running" else "🔴"
    server_info = f"{status_icon} Bot[{idx + 1}/{total}]: {name} (id={bot_id}) | {status} | Sleep: {sleep_before}h"

    # 主路径：页面点击续期
    clicked = click_renew_on_page(sb, bot_id)
    if clicked:
        print(f"🔁 [{name}] 已点击 Renew 按钮，等待生效...")
    else:
        print(f"⚠️ [{name}] 未找到页面 Renew 按钮")

    # 等待 dashboard 刷新
    sb.sleep(4)

    # 验证：通过 API 检查 last_renewed_at 是否变化
    renew_success = False
    hours_after = sleep_before
    try:
        new_bots = get_bots_via_api(cookies)
        new_bot = next((b for b in new_bots if str(b.get("id")) == str(bot_id)), None)
        if new_bot:
            new_timer = new_bot.get("timer") or {}
            hours_after = new_timer.get("remaining_hours", sleep_before)
            new_last_renewed = new_timer.get("last_renewed_at", "")
            if new_last_renewed and new_last_renewed != last_renewed_before:
                renew_success = True
                print(f"   ✅ [{name}] 续期成功（last_renewed_at 已更新: {last_renewed_before[:16]} → {new_last_renewed[:16]}）")
    except Exception as e:
        print(f"   ⚠️ 验证续期结果时异常: {e}")

    # 后手：HTTP API 续期
    if not renew_success:
        print(f"🔄 [{name}] 页面续期未确认，使用 HTTP API 后手...")
        if renew_via_api(cookies, bot_id):
            renew_success = True
            sb.sleep(2)
            try:
                new_bots = get_bots_via_api(cookies)
                new_bot = next((b for b in new_bots if str(b.get("id")) == str(bot_id)), None)
                if new_bot:
                    hours_after = (new_bot.get("timer") or {}).get("remaining_hours", sleep_before)
            except Exception:
                pass
        else:
            pass  # 已打印失败信息

    if renew_success:
        return f"{server_info}\n📋 续期结果: ✅ 成功\n⏳ Sleep: {sleep_before}h → {hours_after}h"
    return f"{server_info}\n📋 续期结果: ❌ 失败"


def main():
    print("#" * 25)
    print("   MWS 自动续期")
    print("#" * 25)

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1081"
    HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"

    sb_kwargs = {"uc": True, "headless": HEADLESS}

    if IS_PROXY:
        print(f"🔗 挂载代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🍭 未使用代理，直连访问")

    with SB(**sb_kwargs) as sb:
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            print(f"⚠️ 获取出口 IP 失败: {e}")

        login_ok = False

        print("🚀 启动浏览器...")
        sb.open(f"{BASE_URL}")
        sb.wait_for_ready_state_complete()
        sb.sleep(5)

        print("🔑 通过 Discord token 登录...")
        if do_discord_login(sb):
            # 登录成功后导航到主页面查看 bot 列表
            sb.open(f"{BASE_URL}/")
            sb.wait_for_ready_state_complete()
            sb.sleep(4)
            page_source = sb.get_page_source()
            if "Log in with Discord" not in page_source:
                login_ok = True
                print("✅ 登录成功")
            else:
                print("❌ 登录后页面仍显示登录按钮，可能未登录成功")

        if not login_ok:
            error_msg = "Discord 登录失败或页面异常"
            send_telegram_message(format_notification("❌ 登录失败", error=error_msg))
            return

        # 提取浏览器 cookie 用于 HTTP API 后手
        cookies = get_browser_cookies(sb)
        print(f"🍪 已提取 {len(cookies)} 个 cookie")

        # 获取 bot 列表（优先 API，其次页面读取）
        bots = get_bots_via_api(cookies)
        if not bots:
            print("⚠️ API 获取 bot 列表失败，尝试从页面读取...")
            bots = get_bots_from_page(sb)
        if not bots:
            print("❌ 未获取到任何 bot")
            send_telegram_message(format_notification("❌ 续期失败", error="未获取到任何 bot"))
            return

        total = len(bots)
        print(f"📋 共发现 {total} 个 bot")
        for b in bots:
            t = b.get("timer", {})
            print(f"   🤖 {b.get('name','?')} (id={b.get('id','?')}) | {b.get('status','?')} | Sleep: {t.get('remaining_hours','?')}h")

        results = []
        for idx, bot in enumerate(bots):
            try:
                result = process_bot(sb, cookies, idx, total, bot)
            except Exception as e:
                print(f"❌ 处理 bot[{idx+1}/{total}]异常: {e}")
                result = f"🖥️ Bot[{idx+1}/{total}]: {bot.get('name','未知')}\n📋 续期结果: ❌ 处理异常: {e}"
            print(result)
            results.append(result)

        send_telegram_message(format_notification(
            f"📋 共处理 {total} 个 bot",
            extra="\n\n".join(results),
        ))
        print("🏁 脚本执行完毕")


if __name__ == "__main__":
    main()
