#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 模板名称：FenixHost 免费服务器续期脚本
# 描述：通过 Cookie + Paymenter/Livewire 调用 renewFree 续期
# 支持多账号多服务
# 归类：Paymenter / Livewire 类型
# 仓库: https://github.com/jacksun-king/fenixhost-renew
# ============================================================
import os
import sys
import json
import re
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote

# ============================================================
# 📌 配置区域
# ============================================================
BASE_URL = "https://fenixhost.net"
UPDATE_URL = f"{BASE_URL}/paymenter/update"

# ============================================================
# 代理配置
# ============================================================
FENIX_PROXY = os.environ.get("FENIX_PROXY") or ""
NODE_LINK = os.environ.get("NODE_LINK") or ""
PROXIES = {}


def _detect_local_proxy() -> dict:
    """自动检测本地 sing-box 代理"""
    candidates = {
        "http://127.0.0.1:1081": "HTTP",
        "socks5://127.0.0.1:1080": "SOCKS5",
        "http://127.0.0.1:7890": "HTTP",
    }
    for url, label in candidates.items():
        try:
            requests.get("http://127.0.0.1:1081", timeout=2)
            proxies = {"http": url, "https": url}
            try:
                test = requests.get("https://api.ipify.org", proxies=proxies, timeout=8)
                if test.status_code == 200:
                    print(f"  🔗 自动检测到本地代理 ({label}): {url}")
                    return proxies
            except Exception:
                continue
        except Exception:
            continue
    return {}


if FENIX_PROXY:
    try:
        test = requests.get("http://127.0.0.1:1081", timeout=2)
        proxies = {"http": FENIX_PROXY, "https": FENIX_PROXY}
        test2 = requests.get("https://api.ipify.org", proxies=proxies, timeout=8)
        if test2.status_code == 200:
            PROXIES = proxies
            print(f"  🔗 使用 FENIX_PROXY: {FENIX_PROXY}")
        else:
            raise Exception("proxy unreachable")
    except Exception:
        print(f"  ⚠️  FENIX_PROXY={FENIX_PROXY} 不可达，回退直连")
        PROXIES = {}
elif NODE_LINK:
    print("  🔗 检测到 NODE_LINK，尝试自动检测本地代理...")
    PROXIES = _detect_local_proxy()
    if not PROXIES:
        print("  ⚠️  NODE_LINK 已设置但未检测到本地代理")
else:
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    if http_proxy or https_proxy:
        PROXIES = {"http": http_proxy, "https": https_proxy or http_proxy}
        print(f"  🔗 使用 HTTP_PROXY: {http_proxy}")
    else:
        detected = _detect_local_proxy()
        if detected:
            PROXIES = detected

# ============================================================
# Telegram 配置
# ============================================================
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

# ============================================================
# 多账号检测
# ============================================================
ACCOUNTS = []
_seen_cookies = set()


def _add_account(cookie: str, service_ids_raw: str, label: str, sid_label: str):
    """添加一个账号，自动去重"""
    if not cookie:
        return False
    if cookie in _seen_cookies:
        print(f"  ⚠️ 发现重复账号（{label} 与已有账号 cookie 相同），跳过")
        return False
    service_ids = [s.strip() for s in service_ids_raw.split(",") if s.strip()]
    if not service_ids:
        print(f"  ⚠️ {sid_label} 未配置服务 ID，跳过")
        return False
    _seen_cookies.add(cookie)
    ACCOUNTS.append({
        "cookie": cookie,
        "service_ids": service_ids,
        "label": label,
    })
    return True


_add_account(
    os.environ.get("FENIX_COOKIE") or "",
    os.environ.get("FENIX_SERVICE_IDS") or "",
    "账号1",
    "FENIX_SERVICE_IDS",
)

for _n in range(1, 100):
    cookie = os.environ.get(f"FENIX_COOKIE_{_n}")
    if not cookie:
        continue
    _add_account(
        cookie,
        os.environ.get(f"FENIX_SERVICE_IDS_{_n}") or "",
        f"账号{_n}",
        f"FENIX_SERVICE_IDS_{_n}",
    )

if not ACCOUNTS:
    print("❌ 未配置任何 Cookie，脚本终止。")
    print("   单账号: 设置 FENIX_COOKIE (Secret) + FENIX_SERVICE_IDS (Variable)")
    sys.exit(1)

print(f"📋 检测到 {len(ACCOUNTS)} 个账号")
for acc in ACCOUNTS:
    print(f"   {acc['label']}: {', '.join(acc['service_ids'])}")


# ------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------
def set_github_output(name: str, value: str):
    """将变量输出到 GitHub Actions 步骤上下文中"""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        try:
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(f"{name}={value}\n")
        except Exception as e:
            print(f"  ⚠️ 写入 GITHUB_OUTPUT 失败: {e}")


def send_telegram(message: str):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10, proxies=PROXIES or None)
        print("  ✅ Telegram 通知已发送")
    except Exception as e:
        print(f"  ❌ Telegram 发送失败: {e}")


def send_telegram_photo(photo_path: str, caption: str = ""):
    """发送 Telegram 图片"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过图片通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            requests.post(
                url,
                data={"chat_id": TG_CHAT_ID, "caption": caption},
                files={"photo": f},
                timeout=20,
                proxies=PROXIES or None,
            )
        print(f"  ✅ 截图已发送到 Telegram: {photo_path}")
    except Exception as e:
        print(f"  ❌ 截图发送失败: {e}")


def take_page_screenshot(cookie: str, service_id: str, save_path: str) -> bool:
    """使用 Playwright 截图（兜底用）"""
    page_url = f"{BASE_URL}/services/{service_id}"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ⚠️  未安装 playwright，无法截图")
        return False

    cookies = []
    clean_cookie = cookie.replace("；", ";")
    for item in clean_cookie.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies.append({"name": k.strip(), "value": v.strip(), "domain": "fenixhost.net", "path": "/"})

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
                proxy={k: v for k, v in PROXIES.items()} if PROXIES else None,
            )
            context.add_cookies(cookies)
            page = context.new_page()
            page.goto(page_url, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(3000)
            page.screenshot(path=save_path, full_page=True)
            print(f"  📸 页面截图已保存: {save_path}")
            browser.close()
            return True
    except Exception as e:
        print(f"  ⚠️  截图失败: {e}")
        return False


def format_notification(status: str, label: str, service_id: str, new_expiry: str) -> str:
    """格式化续期通知消息（开头修改为 💗主人）"""
    now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "💗主人 FenixHost 免费服务器续期",
        "",
        f"{status}",
        f"👤 {label}",
        f"🆔 服务ID: {service_id}",
        f"📅 到期时间: {new_expiry}",
        f"⏰ 执行时间: {now}",
    ]
    return "\n".join(lines)


def parse_cookies(cookie_str: str) -> dict:
    """将 Cookie 字符串解析为字典（兼容中英文分号）"""
    cookies = {}
    clean_cookie = cookie_str.replace("；", ";")
    for item in clean_cookie.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def serialize_cookies(cookies_dict: dict) -> str:
    """将 Cookie 字典序列化为标准规范字符串，优先保证关键键顺序"""
    ordered_keys = []
    # 优先键
    if "paymenter_session" in cookies_dict:
        ordered_keys.append("paymenter_session")
    for k in cookies_dict:
        if k.startswith("remember_web_"):
            ordered_keys.append(k)
    if "XSRF-TOKEN" in cookies_dict:
        ordered_keys.append("XSRF-TOKEN")
    # 其他非 cart 键
    for k in cookies_dict:
        if k not in ordered_keys and k != "cart":
            ordered_keys.append(k)
    return "; ".join([f"{k}={cookies_dict[k]}" for k in ordered_keys])


def _unescape_html(s: str) -> str:
    return s.replace("&quot;", '"').replace("&#039;", "'").replace("&amp;", "&")\
            .replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")


def get_browser_headers() -> dict:
    return {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "zh-CN,zh;q=0.9",
        "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    }


def get_livewire_headers(referer: str) -> dict:
    return {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "content-type": "application/json",
        "origin": BASE_URL,
        "referer": referer,
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "x-livewire": "",
        "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }


def extract_csrf_token(html: str) -> str:
    m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'csrfToken\s*:\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def extract_livewire_snapshot(html: str) -> dict:
    for m in re.finditer(r'wire:snapshot=["\']([^"\']+)["\']', html, re.IGNORECASE):
        snap_str = _unescape_html(m.group(1))
        try:
            data = json.loads(snap_str)
        except json.JSONDecodeError:
            continue
        if data.get("memo", {}).get("name") == "services.show":
            return data
    return None


def extract_expiry(html: str) -> str:
    m = re.search(r'data-expires="(\d+)"', html)
    if m:
        ts = int(m.group(1))
        try:
            dt = datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=8)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return m.group(1)
    m = re.search(r'Expires at:\s*([A-Za-z]{3}\s+\d{1,2},\s*\d{4})', html)
    if m:
        return m.group(1)
    m = re.search(r'data-expires[^0-9]*(\d+)', html)
    if m:
        return m.group(1)
    return ""


# ------------------------------------------------------------
# 续期核心
# ------------------------------------------------------------
def renew_service(cookie: str, service_id: str) -> dict:
    service_url = f"{BASE_URL}/services/{service_id}"
    cookies_dict = parse_cookies(cookie)

    session = requests.Session()
    session.cookies.update(cookies_dict)

    print(f"  🔄 获取服务页面 {service_id}...")
    try:
        resp = session.get(service_url, headers=get_browser_headers(),
                           timeout=30, proxies=PROXIES or None, allow_redirects=True)
    except Exception as e:
        print(f"  ❌ 获取服务页面失败: {e}")
        return {"status": "error", "message": f"页面请求失败: {e}"}

    if "login" in resp.url.lower() and ("Sign in" in resp.text or "sign in" in resp.text.lower()):
        print(f"  ❌ 被重定向到登录页，Cookie 已过期")
        return {"status": "error", "message": "Cookie 已过期"}

    if resp.status_code == 403:
        print(f"  ❌ 403 Forbidden - Cookie 可能已过期，或服务ID不存在")
        return {"status": "error", "message": "403 Forbidden"}
    if resp.status_code != 200:
        print(f"  ❌ HTTP {resp.status_code}: 获取服务页面失败")
        return {"status": "error", "message": f"HTTP {resp.status_code}"}

    csrf_token = extract_csrf_token(resp.text)
    if not csrf_token:
        print(f"  ❌ 未找到 CSRF token")
        return {"status": "error", "message": "未找到 CSRF token"}

    snapshot = extract_livewire_snapshot(resp.text)
    if not snapshot:
        print(f"  ❌ 未找到 services.show Livewire snapshot")
        return {"status": "error", "message": "未找到续期组件"}

    print(f"  ✅ 已获取 CSRF token 和续期组件")
    old_expiry = extract_expiry(resp.text)

    # 调用 renewFree
    payload = {
        "_token": csrf_token,
        "components": [
            {
                "snapshot": json.dumps(snapshot),
                "updates": {},
                "calls": [
                    {"path": "", "method": "renewFree", "params": []}
                ]
            }
        ]
    }

    print(f"  🔄 调用 renewFree...")
    try:
        lw_resp = session.post(UPDATE_URL, headers=get_livewire_headers(service_url),
                               json=payload, timeout=30, proxies=PROXIES or None)
    except Exception as e:
        print(f"  ❌ Livewire 请求失败: {e}")
        return {"status": "error", "message": f"请求失败: {e}"}

    if lw_resp.status_code != 200:
        print(f"  ❌ Livewire HTTP {lw_resp.status_code}")
        return {"status": "error", "message": f"Livewire HTTP {lw_resp.status_code}"}

    # 再次 GET 页面以确认到期时间，并刷新最新 Cookie
    print(f"  🔄 重新获取页面确认到期时间...")
    try:
        resp2 = session.get(service_url, headers=get_browser_headers(),
                            timeout=30, proxies=PROXIES or None)
    except Exception as e:
        print(f"  ⚠️  重新获取页面失败: {e}")
        return {
            "status": "success",
            "new_expiry": old_expiry or "续期成功",
            "fresh_cookie": serialize_cookies(session.cookies.get_dict())
        }

    new_expiry = extract_expiry(resp2.text)
    # 提取经过所有请求后 Session 保存的最全最新 Cookie
    fresh_cookie = serialize_cookies(session.cookies.get_dict())

    if new_expiry:
        if old_expiry and new_expiry != old_expiry:
            print(f"  ✅ 续期成功! 到期时间从 {old_expiry} 更新为 {new_expiry}")
        else:
            print(f"  ✅ 续期请求成功，当前到期时间: {new_expiry}")
            print(f"     （注：若到期时间未变化，说明服务已满额续期或存在冷却时间）")
        return {"status": "success", "new_expiry": new_expiry, "fresh_cookie": fresh_cookie}
    else:
        print(f"  ✅ Livewire 请求成功，但无法解析到期时间")
        screenshot_path = f"/tmp/fenixhost_{service_id}.png"
        if take_page_screenshot(cookie, service_id, screenshot_path):
            send_telegram_photo(
                screenshot_path,
                caption=f"💗主人 FenixHost 续期成功\n🆔 服务ID: {service_id}\n到期时间见截图",
            )
        return {"status": "success", "new_expiry": "请查看截图", "fresh_cookie": fresh_cookie}


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def main():
    print("=" * 40)
    print(" FenixHost 免费服务器自动续期")
    print("=" * 40)

    all_results = []
    latest_account1_cookie = None

    for acc in ACCOUNTS:
        label = acc["label"]
        cookie = acc["cookie"]
        service_ids = acc["service_ids"]

        print(f"\n{'=' * 40}")
        print(f" {label}")
        print(f" 服务ID: {', '.join(service_ids)}")
        print(f"{'=' * 40}")

        for service_id in service_ids:
            try:
                result = renew_service(cookie, service_id)
                status_ok = (result["status"] == "success")
                info = {
                    "label": label,
                    "service_id": service_id,
                    "status": "✅ 续期成功" if status_ok else "❌ 续期失败",
                    "new_expiry": result.get("new_expiry", "未知"),
                }
                # 如果账号1有成功更新的 Cookie，记录下来
                if status_ok and label == "账号1" and result.get("fresh_cookie"):
                    latest_account1_cookie = result["fresh_cookie"]

            except Exception as e:
                print(f"  ❌ 服务 {service_id} 续期失败: {e}")
                info = {
                    "label": label,
                    "service_id": service_id,
                    "status": "❌ 续期失败",
                    "new_expiry": str(e)[:50],
                }

            all_results.append(info)

            # 发送 Telegram 通知
            msg = format_notification(
                info["status"],
                info["label"],
                info["service_id"],
                info["new_expiry"]
            )
            send_telegram(msg)

    # 如果抓取到了新的 Cookie，输出给 GitHub Actions 供后续步骤更新 Secret
    if latest_account1_cookie:
        print("\n🍪 成功提取最新会话 Cookie，已写入 GITHUB_OUTPUT")
        set_github_output("NEW_FENIX_COOKIE", latest_account1_cookie)

    # 汇总
    success = sum(1 for r in all_results if "成功" in r["status"])
    fail = sum(1 for r in all_results if "失败" in r["status"])
    accounts = len(set(r["label"] for r in all_results))
    print(f"\n{'=' * 40}")
    print(f"📊 汇总: {accounts} 个账号, {success} 成功, {fail} 失败, 共 {len(all_results)} 个服务")
    print(f"{'=' * 40}")


if __name__ == "__main__":
    main()
