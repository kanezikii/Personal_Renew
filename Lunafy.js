const { chromium } = require('playwright');
const axios = require('axios');
require('dotenv').config();

const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const PROXY_URL = process.env.PROXY_URL;
const TG_BOT_TOKEN = process.env.TG_BOT_TOKEN;
const TG_CHAT_ID = process.env.TG_CHAT_ID;

// 发送 Telegram 状态通知
async function sendNotification(text) {
  if (!TG_BOT_TOKEN || !TG_CHAT_ID) return;
  try {
    await axios.post(`https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage`, {
      chat_id: TG_CHAT_ID,
      text: text,
      parse_mode: 'Markdown',
    });
    console.log('[Notification] Telegram message sent.');
  } catch (err) {
    console.error('[Notification] Failed to send Telegram message:', err.message);
  }
}

// 解析并适配各种协议代理 (HTTP / HTTPS / SOCKS5)
function getProxyConfig() {
  if (!PROXY_URL) return undefined;
  try {
    const parsed = new URL(PROXY_URL);
    const config = {
      server: `${parsed.protocol}//${parsed.hostname}:${parsed.port}`,
    };
    if (parsed.username) config.username = decodeURIComponent(parsed.username);
    if (parsed.password) config.password = decodeURIComponent(parsed.password);
    return config;
  } catch (err) {
    console.error('[Proxy] Invalid PROXY_URL format:', err.message);
    return undefined;
  }
}

(async () => {
  if (!DISCORD_TOKEN) {
    console.error('Error: DISCORD_TOKEN secret is required.');
    process.exit(1);
  }

  const proxy = getProxyConfig();
  if (proxy) console.log(`[Proxy] Running through proxy: ${proxy.server}`);

  const browser = await chromium.launch({
    headless: true,
    proxy: proxy,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  });

  const page = await context.newPage();

  try {
    console.log('[Step 1] Initializing Discord login session...');
    await page.goto('https://discord.com/login', { waitUntil: 'domcontentloaded', timeout: 45000 });

    // 注入 Discord Token 实现免密登录
    await page.evaluate((token) => {
      setInterval(() => {
        try {
          document.body.appendChild(document.createElement('iframe')).contentWindow.localStorage.token = `"${token}"`;
        } catch (e) {}
      }, 50);
      setTimeout(() => {
        location.reload();
      }, 500);
    }, DISCORD_TOKEN);

    // 等待 Discord 登录状态生效
    await page.waitForTimeout(5000);

    console.log('[Step 2] Navigating to Lunafy Panel...');
    await page.goto('https://panel.lunafy.run/dashboard', { waitUntil: 'networkidle', timeout: 45000 });

    // 如果未登录重定向到了登录页，触发 Discord 授权
    if (page.url().includes('/login') || (await page.locator('text=Discord').isVisible().catch(() => false))) {
      console.log('[Step 2.1] Clicking Discord OAuth Login button...');
      const discordBtn = page.locator('a[href*="discord"], button:has-text("Discord")').first();
      if (await discordBtn.isVisible()) {
        await discordBtn.click();
        await page.waitForTimeout(4000);

        // 检测是否有 Discord OAuth "Authorize" 按钮
        const authorizeBtn = page.locator('button:has-text("Authorize"), button:has-text("授权")').first();
        if (await authorizeBtn.isVisible({ timeout: 10000 }).catch(() => false)) {
          console.log('[Step 2.2] Authorizing Discord app...');
          await authorizeBtn.click();
          await page.waitForNavigation({ waitUntil: 'networkidle', timeout: 30000 }).catch(() => {});
        }
      }
    }

    // 确保回到仪表盘页面
    await page.goto('https://panel.lunafy.run/dashboard', { waitUntil: 'networkidle', timeout: 30000 });

    console.log('[Step 3] Extracting renewal and status information...');
    await page.waitForSelector('.lunafy-server-status, section[class*="lunafy-server-status"]', { timeout: 20000 });

    // 提取服务器当前状态
    const statusText = await page.locator('section[class*="lunafy-server-status"] .fi-badge, section[class*="lunafy-server-status"] [class*="status__heading"]').innerText().catch(() => 'Unknown');

    // 提取下次续期时间与删除时间
    const datesText = await page.locator('.lunafy-server-status__dates, [class*="status__dates"]').innerText().catch(() => 'Dates not found');

    // 检查右侧续期操作区
    const actionElement = page.locator('.lunafy-server-status__action, [class*="status__action"]');
    const actionText = (await actionElement.innerText().catch(() => 'Unavailable')).trim();

    console.log(`\n================= Server Status =================`);
    console.log(`[Status] Current Status: ${statusText.replace(/\n/g, ' ')}`);
    console.log(`[Schedule] ${datesText.replace(/\n/g, ' | ')}`);
    console.log(`[Action State] ${actionText}`);
    console.log(`=================================================\n`);

    let renewResult = 'No action needed';

    // 判断是否可续期（按钮出现且非 Unavailable 状态）
    const renewBtn = actionElement.locator('button, a').first();
    const canRenew = (await renewBtn.isVisible().catch(() => false)) && !actionText.toLowerCase().includes('unavailable');

    if (canRenew) {
      console.log('[Step 4] Renewal available! Triggering renewal...');
      await renewBtn.click();
      await page.waitForTimeout(4000);
      renewResult = 'Renewal Triggered Successfully';
      console.log(`[Success] ${renewResult}`);
    } else {
      console.log('[Step 4] Renewal currently unavailable. Waiting for next schedule.');
    }

    // 发送汇总通知
    const summaryMessage = `*Lunafy Server Status Report*\n\n` +
      `• *Status:* \`${statusText.trim()}\`\n` +
      `• *Dates:* \`${datesText.replace(/\n/g, ' ')}\`\n` +
      `• *Action:* \`${actionText}\`\n` +
      `• *Result:* *${renewResult}*`;

    await sendNotification(summaryMessage);

  } catch (error) {
    console.error('[Error] Execution failed:', error.message);
    await sendNotification(`❌ *Lunafy Auto Renew Failed*\nError: \`${error.message}\``);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
