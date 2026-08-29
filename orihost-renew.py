#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 模板名称：Orihost 免费服务器续期测试脚本
# 描述：通过 Jexactyl 面板 Cookie 直调续期接口（测试模式：无阈值、无等待）
# ============================================================
import os
import sys
import requests
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta

# ============================================================
# 📌 配置区域
# ============================================================
BASE_URL = "https://panel.orihost.com"

# ============================================================
# 代理配置
# ============================================================
ORIHOST_PROXY = os.environ.get("ORIHOST_PROXY") or ""
PROXIES = {}
if ORIHOST_PROXY:
    PROXIES = {"http": ORIHOST_PROXY, "https": ORIHOST_PROXY}
    print(f"  🔗 尝试使用 ORIHOST_PROXY: {ORIHOST_PROXY}")
    try:
        requests.get("http://www.gstatic.com/generate_204", proxies=PROXIES, timeout=5)
        print("  ✅ 代理可用")
    except Exception:
        print(f"  ⚠️ 代理不可达 ({ORIHOST_PROXY})，回退直连")
        PROXIES = {}
else:
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    if http_proxy or https_proxy:
        PROXIES = {"http": http_proxy, "https": https_proxy or http_proxy}
        print(f"  🔗 使用 HTTP_PROXY: {http_proxy}")

# ============================================================
# Telegram 配置
# ============================================================
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

# ============================================================
# 多账号检测
# ============================================================
ACCOUNTS = []

for i in range(1, 100):
    cookie = os.environ.get(f"ORIHOST_COOKIE_{i}")
    if cookie:
        server_ids_raw = os.environ.get(f"ORIHOST_SERVER_IDS_{i}") or ""
        server_ids = [s.strip() for s in server_ids_raw.split(",") if s.strip()]
        if not server_ids:
            print(f"⚠️ 账号{i} ORIHOST_SERVER_IDS_{i} 为空，跳过")
            continue
        ACCOUNTS.append({
            "cookie": cookie,
            "server_ids": server_ids,
            "label": f"账号{i}"
        })
    else:
        break

# 向下兼容：单账号
if not ACCOUNTS:
    legacy_cookie = os.environ.get("ORIHOST_COOKIE") or ""
    if legacy_cookie:
        server_ids_raw = os.environ.get("ORIHOST_SERVER_IDS") or ""
        server_ids = [s.strip() for s in server_ids_raw.split(",") if s.strip()]
        if server_ids:
            ACCOUNTS.append({
                "cookie": legacy_cookie,
                "server_ids": server_ids,
                "label": "默认账号"
            })

if not ACCOUNTS:
    print("❌ 未配置任何 Cookie，脚本终止。")
    sys.exit(1)

print(f"📋 检测到 {len(ACCOUNTS)} 个账号（测试模式：已停用阈值与阅读等待）")
for acc in ACCOUNTS:
    print(f"   {acc['label']}: {', '.join(acc['server_ids'])}")


# ------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------
def send_telegram(message: str):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10, proxies=PROXIES or None)
    except Exception as e:
        print(f"  ❌ Telegram 发送失败: {e}")


def parse_cookies(cookie_str: str) -> dict:
    """将 Cookie 字符串解析为字典"""
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookies[key.strip()] = unquote(value.strip())
    return cookies


def build_headers(xsrf_token: str, server_id: str) -> dict:
    """构造标准请求头"""
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "x-requested-with": "XMLHttpRequest",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/server/{server_id[:8]}",
    }
    if xsrf_token:
        headers["x-xsrf-token"] = xsrf_token
    return headers


def format_notification(status: str, label: str, server_id: str, detail: str) -> str:
    """格式化通知信息"""
    now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "🖥 Orihost 免费服务器续期",
        "",
        f"{status}",
        f"👤 {label}",
        f"🆔 服务器: {server_id}",
        f"📌 结果: {detail}",
        f"⏰ 执行时间: {now}",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------
# 续期核心流程（测试模式）
# ------------------------------------------------------------
def renew_server(cookie: str, server_id: str) -> dict:
    """直接发起续期请求并打印原始响应"""
    cookie_dict = parse_cookies(cookie)
    headers = build_headers(cookie_dict.get("XSRF-TOKEN", ""), server_id)

    # Step 1: 发起续期请求
    begin_url = f"{BASE_URL}/api/client/servers/{server_id}/renew/begin"
    print(f"  🔄 [{server_id[:8]}] Step 1: POST {begin_url}")
    try:
        resp = requests.post(begin_url, headers=headers, cookies=cookie_dict, timeout=30, proxies=PROXIES or None)
        print(f"  📥 Step 1 HTTP 状态码: {resp.status_code}")
        print(f"  📄 Step 1 原始响应: {resp.text[:300]}")
    except Exception as e:
        print(f"  ❌ Step 1 请求失败: {e}")
        return {"status": "error", "message": f"Step 1 请求失败: {e}"}

    if resp.status_code == 419:
        return {"status": "error", "message": "CSRF mismatch (419) - Cookie 过期"}
    if resp.status_code == 401:
        return {"status": "error", "message": "401 Unauthenticated - Cookie 失效"}
    if resp.status_code != 200:
        return {"status": "error", "message": f"Step 1 HTTP {resp.status_code}: {resp.text[:100]}"}

    # 更新服务端下发的新 Session 与 Token
    new_cookies = resp.cookies.get_dict()
    if new_cookies:
        cookie_dict.update(new_cookies)
        if "XSRF-TOKEN" in cookie_dict:
            headers["x-xsrf-token"] = cookie_dict["XSRF-TOKEN"]
        print("  🔄 Session 状态已同步更新")

    # Step 2: 立即提交完成续期（已取消阅读等待任务）
    complete_url = f"{BASE_URL}/api/client/renewal/complete"
    print(f"  🔄 [{server_id[:8]}] Step 2: GET {complete_url} (已取消阅读等待)")
    try:
        resp2 = requests.get(complete_url, headers=headers, cookies=cookie_dict, timeout=30, proxies=PROXIES or None)
        print(f"  📥 Step 2 HTTP 状态码: {resp2.status_code}")
        print(f"  📄 Step 2 原始响应: {resp2.text[:300]}")
    except Exception as e:
        print(f"  ❌ Step 2 请求失败: {e}")
        return {"status": "error", "message": f"Step 2 请求失败: {e}"}

    if resp2.status_code != 200:
        return {"status": "error", "message": f"Step 2 HTTP {resp2.status_code}: {resp2.text[:150]}"}

    try:
        result = resp2.json()
    except Exception:
        result = {"raw": resp2.text}

    print(f"  ✅ 续期接口返回: {result}")
    return {"status": "success", "message": f"响应: {result}"}


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def main():
    print("=" * 40)
    print(" Orihost 免费服务器自动续期 (测试模式)")
    print("=" * 40)

    all_results = []

    for acc in ACCOUNTS:
        label = acc["label"]
        cookie = acc["cookie"]
        server_ids = acc["server_ids"]

        print(f"\n{'=' * 40}")
        print(f" {label}")
        print(f" 服务器: {', '.join(server_ids)}")
        print(f"{'=' * 40}")

        for server_id in server_ids:
            try:
                result = renew_server(cookie, server_id)
                status_map = {
                    "success": "✅ 续期成功",
                    "error": "❌ 续期失败",
                }
                info = {
                    "label": label,
                    "server_id": server_id,
                    "status": status_map.get(result["status"], "❌ 续期失败"),
                    "message": result.get("message", ""),
                }
            except Exception as e:
                print(f"  ❌ 处理失败: {e}")
                info = {
                    "label": label,
                    "server_id": server_id,
                    "status": "❌ 续期失败",
                    "message": str(e)[:80],
                }

            all_results.append(info)

            # 发送 Telegram 通知
            msg = format_notification(
                info["status"],
                info["label"],
                info["server_id"][:8],
                info["message"]
            )
            send_telegram(msg)

    # 汇总
    success = sum(1 for r in all_results if "成功" in r["status"])
    fail = sum(1 for r in all_results if "失败" in r["status"])
    print(f"\n{'=' * 40}")
    print(f"📊 汇总: {len(all_results)} 个服务器, {success} 成功, {fail} 失败")
    print(f"{'=' * 40}")


if __name__ == "__main__":
    main()
