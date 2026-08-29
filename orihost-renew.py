#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 模板名称：Orihost 免费服务器续期脚本
# 描述：通过 Jexactyl 面板 Cookie 直调续期接口
#       支持多账号多服务器，支持自定义剩余天数续期阈值
# 归类：Jexactyl/Pterodactyl 续期类型
# ============================================================
import os
import sys
import time
import requests
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta

# ============================================================
# 📌 配置区域
# ============================================================
BASE_URL = "https://panel.orihost.com"

# 续期阈值（单位：天）：当剩余天数 <= 该阈值时发起续期，默认 3 天
RENEW_THRESHOLD_DAYS = int(os.environ.get("ORIHOST_RENEW_THRESHOLD_DAYS") or 3)

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
        print(f"  ✅ 代理可用")
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

print(f"📋 检测到 {len(ACCOUNTS)} 个账号，续期阈值: <= {RENEW_THRESHOLD_DAYS} 天")
for acc in ACCOUNTS:
    print(f"   {acc['label']}: {', '.join(acc['server_ids'])}")


# ------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------
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


def parse_renewal_days(val) -> float:
    """解析服务器剩余续期天数"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip())
        except ValueError:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(val.split("+")[0], fmt).replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                return max(0.0, (dt - now).total_seconds() / 86400.0)
            except Exception:
                continue
    return None


def get_server_details(cookie_dict: dict, headers: dict, server_id: str) -> dict:
    """获取服务器详情与剩余天数"""
    url = f"{BASE_URL}/api/client/servers/{server_id}"
    try:
        resp = requests.get(url, headers=headers, cookies=cookie_dict, timeout=30, proxies=PROXIES or None)
        if resp.status_code == 200:
            data = resp.json()
            attributes = data.get("attributes", {})
            renewal_val = attributes.get("renewal")
            name = attributes.get("name", server_id[:8])
            days_left = parse_renewal_days(renewal_val)
            return {"name": name, "days_left": days_left}
    except Exception as e:
        print(f"  ⚠️ 获取服务器详情异常: {e}")
    return {"name": server_id[:8], "days_left": None}


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
# 续期核心流程
# ------------------------------------------------------------
def renew_server(cookie: str, server_id: str) -> dict:
    """检查剩余天数并在 <= 3 天时执行续期"""
    cookie_dict = parse_cookies(cookie)
    headers = build_headers(cookie_dict.get("XSRF-TOKEN", ""), server_id)

    # Step 0: 查询当前服务器剩余天数
    server_info = get_server_details(cookie_dict, headers, server_id)
    days_left = server_info["days_left"]
    server_name = server_info["name"]

    if days_left is not None:
        display_days = int(days_left) if days_left.is_integer() else round(days_left, 1)
        print(f"  📊 [{server_id[:8]}] 服务器名称: {server_name} | 剩余天数: {display_days} 天")
        
        # 判断条件：大于 3 天则跳过续期
        if days_left > RENEW_THRESHOLD_DAYS:
            print(f"  ⏭️ 剩余天数 ({display_days} 天) > 阈值 ({RENEW_THRESHOLD_DAYS} 天)，无需续期")
            return {
                "status": "skipped",
                "message": f"剩余 {display_days} 天 (> 阈值 {RENEW_THRESHOLD_DAYS} 天，跳过)"
            }
        print(f"  ⚠️ 剩余天数 ({display_days} 天) <= {RENEW_THRESHOLD_DAYS} 天，开始发起续期...")
    else:
        print(f"  ℹ️ 未获取到剩余天数，直接尝试发起续期...")

    # Step 1: 发起续期请求
    begin_url = f"{BASE_URL}/api/client/servers/{server_id}/renew/begin"
    print(f"  🔄 [{server_id[:8]}] 开始发起续期会话...")
    try:
        resp = requests.post(begin_url, headers=headers, cookies=cookie_dict, timeout=30, proxies=PROXIES or None)
    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return {"status": "error", "message": f"请求失败: {e}"}

    if resp.status_code == 419:
        print(f"  ❌ CSRF mismatch (419) - Cookie 已过期")
        return {"status": "error", "message": "CSRF mismatch (419) - Cookie 过期"}
    if resp.status_code == 401:
        print(f"  ❌ 401 Unauthenticated - Cookie 已失效")
        return {"status": "error", "message": "401 Unauthenticated - Cookie 失效"}

    if resp.status_code != 200:
        if "UnexpectedValueException" in resp.text or "limit" in resp.text.lower():
            print(f"  ⏭️ 面板提示已达续期上限，跳过")
            return {"status": "skipped", "message": "已达续期天数上限 (无需续期)"}
        print(f"  ❌ begin 失败 HTTP {resp.status_code}: {resp.text[:200]}")
        return {"status": "error", "message": f"begin HTTP {resp.status_code}"}

    try:
        data = resp.json()
    except Exception:
        print(f"  ❌ begin 响应解析失败: {resp.text[:200]}")
        return {"status": "error", "message": "begin 响应解析失败"}

    # 更新服务端下发的新 Session 与 Token
    new_cookies = resp.cookies.get_dict()
    if new_cookies:
        cookie_dict.update(new_cookies)
        if "XSRF-TOKEN" in cookie_dict:
            headers["x-xsrf-token"] = cookie_dict["XSRF-TOKEN"]
        print(f"  🔄 Session 状态已同步更新")

    article_url = data.get("url", "")
    dwell_seconds = data.get("dwell_seconds", 15)
    print(f"  📰 对应文章: {article_url}")

    # 等待时限并增加 3 秒缓冲
    wait_time = dwell_seconds + 3
    print(f"  ⏳ 等待 {wait_time} 秒（模拟阅读文章）...")
    time.sleep(wait_time)

    # Step 2: 提交完成续期
    complete_url = f"{BASE_URL}/api/client/renewal/complete"
    try:
        resp2 = requests.get(complete_url, headers=headers, cookies=cookie_dict, timeout=30, proxies=PROXIES or None)
    except Exception as e:
        print(f"  ❌ complete 请求失败: {e}")
        return {"status": "error", "message": f"complete 请求失败: {e}"}

    if resp2.status_code != 200:
        if "UnexpectedValueException" in resp2.text or "limit" in resp2.text.lower():
            print(f"  ⏭️ 服务器已达最大续期上限，跳过")
            return {"status": "skipped", "message": "已达续期天数上限 (无需续期)"}
        print(f"  ❌ complete 失败 HTTP {resp2.status_code}: {resp2.text[:200]}")
        return {"status": "error", "message": f"complete HTTP {resp2.status_code}"}

    try:
        result = resp2.json()
    except Exception:
        result = {}

    renewed = result.get("renewed_count", 0)
    skipped = result.get("skipped_count", 0)

    if renewed > 0:
        print(f"  ✅ 续期成功! renewed_count={renewed}")
        return {"status": "success", "message": f"续期成功 (+{renewed})"}
    elif skipped > 0:
        print(f"  ⏭️ 服务器被跳过 (skipped={skipped})，已达续期上限")
        return {"status": "skipped", "message": "服务器被跳过 (已达续期上限)"}
    else:
        print(f"  ✅ 续期处理完成: {result}")
        return {"status": "success", "message": f"续期完成: {result}"}


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def main():
    print("=" * 40)
    print(" Orihost 免费服务器自动续期")
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
                    "skipped": "⏭️ 剩余充足",
                    "error": "❌ 续期失败",
                    "unknown": "⚠️ 未知结果",
                }
                info = {
                    "label": label,
                    "server_id": server_id,
                    "status": status_map.get(result["status"], "❌ 续期失败"),
                    "message": result.get("message", ""),
                }
            except Exception as e:
                print(f"  ❌ 服务器 {server_id} 处理失败: {e}")
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
    skipped = sum(1 for r in all_results if "充足" in r["status"] or "上限" in r["status"])
    accounts = len(set(r["label"] for r in all_results))
    print(f"\n{'=' * 40}")
    print(f"📊 汇总: {accounts} 个账号, {success} 成功, {skipped} 跳过, {fail} 失败, 共 {len(all_results)} 个服务器")
    print(f"{'=' * 40}")


if __name__ == "__main__":
    main()
