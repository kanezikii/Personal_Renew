const { chromium } = require('playwright');
const axios = require('axios');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn, execSync } = require('child_process');
require('dotenv').config();

const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const PROXY_NODE = (process.env.PROXY_NODE || process.env.PROXY_URL || '').trim();
const TG_BOT_TOKEN = process.env.TG_BOT_TOKEN;
const TG_CHAT_ID = process.env.TG_CHAT_ID;

const LOCAL_SOCKS_PORT = 10808;
let singboxProcess = null;

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

// 解析多协议节点链接并生成 Sing-box 配置文件
function parseNodeToOutbound(nodeUri) {
  const uri = nodeUri.trim();

  // 1. VMess
  if (uri.startsWith('vmess://')) {
    const raw = Buffer.from(uri.slice(8), 'base64').toString('utf-8');
    const json = JSON.parse(raw);
    const outbound = {
      type: 'vmess',
      tag: 'proxy',
      server: json.add,
      server_port: parseInt(json.port),
      uuid: json.id,
      security: 'auto',
    };
    if (json.tls === 'tls') {
      outbound.tls = {
        enabled: true,
        server_name: json.sni || json.host || json.add,
        insecure: true,
      };
    }
    if (json.net === 'ws') {
      outbound.transport = {
        type: 'ws',
        path: json.path || '/',
        headers: json.host ? { Host: json.host } : {},
      };
    }
    return outbound;
  }

  // 2. VLESS
  if (uri.startsWith('vless://')) {
    const u = new URL(uri);
    const p = u.searchParams;
    const outbound = {
      type: 'vless',
      tag: 'proxy',
      server: u.hostname,
      server_port: parseInt(u.port || 443),
      uuid: u.username,
      flow: p.get('flow') || undefined,
    };
    if (p.get('security') === 'reality') {
      outbound.tls = {
        enabled: true,
        server_name: p.get('sni') || u.hostname,
        reality: {
          enabled: true,
          public_key: p.get('pbk'),
          short_id: p.get('sid') || '',
        },
        utls: { enabled: true, fingerprint: p.get('fp') || 'chrome' },
      };
    } else if (p.get('security') === 'tls') {
      outbound.tls = {
        enabled: true,
        server_name: p.get('sni') || u.hostname,
        insecure: true,
      };
    }
    if (p.get('type') === 'ws') {
      outbound.transport = {
        type: 'ws',
        path: p.get('path') || '/',
        headers: p.get('host') ? { Host: p.get('host') } : {},
      };
    }
    return outbound;
  }

  // 3. Hysteria 2
  if (uri.startsWith('hysteria2://') || uri.startsWith('hy2://')) {
    const cleanUri = uri.replace('hy2://', 'hysteria2://');
    const u = new URL(cleanUri);
    const p = u.searchParams;
    const outbound = {
      type: 'hysteria2',
      tag: 'proxy',
      server: u.hostname,
      server_port: parseInt(u.port || 443),
      password: u.username,
      tls: {
        enabled: true,
        server_name: p.get('sni') || u.hostname,
        insecure: p.get('insecure') === '1',
      },
    };
    if (p.get('obfs')) {
      outbound.obfs = { type: p.get('obfs'), password: p.get('obfs-password') || '' };
    }
    return outbound;
  }

  // 4. TUIC
  if (uri.startsWith('tuic://')) {
    const u = new URL(uri);
    const p = u.searchParams;
    return {
      type: 'tuic',
      tag: 'proxy',
      server: u.hostname,
      server_port: parseInt(u.port || 443),
      uuid: u.username,
      password: u.password || u.username,
      congestion_control: p.get('congestion_control') || 'bbr',
      tls: {
        enabled: true,
        server_name: p.get('sni') || u.hostname,
        insecure: p.get('allow_insecure') === '1',
        alpn: ['h3'],
      },
    };
  }

  // 5. Trojan
  if (uri.startsWith('trojan://')) {
    const u = new URL(uri);
    const p = u.searchParams;
    const outbound = {
      type: 'trojan',
      tag: 'proxy',
      server: u.hostname,
      server_port: parseInt(u.port || 443),
      password: u.username,
      tls: {
        enabled: true,
        server_name: p.get('sni') || u.hostname,
        insecure: p.get('allowInsecure') === '1',
      },
    };
    if (p.get('type') === 'ws') {
      outbound.transport = {
        type: 'ws',
        path: p.get('path') || '/',
        headers: p.get('host') ? { Host: p.get('host') } : {},
      };
    }
    return outbound;
  }

  throw new Error(`Unsupported node URI protocol: ${uri.slice(0, 15)}...`);
}

// 准备并启动代理客户端
async function setupProxyBridge() {
  if (!PROXY_NODE) return undefined;

  // 如果原本就是标准 HTTP/SOCKS5 代理，直接返回给 Playwright
  if (PROXY_NODE.startsWith('http://') || PROXY_NODE.startsWith('https://') || PROXY_NODE.startsWith('socks5://')) {
    const parsed = new URL(PROXY_NODE);
    const cfg = { server: `${parsed.protocol}//${parsed.hostname}:${parsed.port}` };
    if (parsed.username) cfg.username = decodeURIComponent(parsed.username);
    if (parsed.password) cfg.password = decodeURIComponent(parsed.password);
    return cfg;
  }

  console.log('[Proxy Bridge] Parsing multi-protocol node...');
  const outboundConfig = parseNodeToOutbound(PROXY_NODE);

  const binDir = path.join(__dirname, '.singbox');
  if (!fs.existsSync(binDir)) fs.mkdirSync(binDir, { recursive: true });

  const singboxPath = path.join(binDir, 'sing-box');
  if (!fs.existsSync(singboxPath)) {
    console.log('[Proxy Bridge] Downloading Sing-box core...');
    const arch = os.arch() === 'arm64' ? 'arm64' : 'amd64';
    const coreUrl = `https://github.com/SagerNet/sing-box/releases/download/v1.10.7/sing-box-1.10.7-linux-${arch}.tar.gz`;
    execSync(`curl -sL "${coreUrl}" | tar -xz -C "${binDir}" --strip-components=1`);
    fs.chmodSync(singboxPath, 0o775);
  }

  const singboxConfig = {
    log: { level: 'error' },
    inbounds: [
      {
        type: 'mixed',
        tag: 'mixed-in',
        listen: '127.0.0.1',
        listen_port: LOCAL_SOCKS_PORT,
      },
    ],
    outbounds: [outboundConfig, { type: 'direct', tag: 'direct' }],
    route: { final: 'proxy' },
  };

  const configPath = path.join(binDir, 'config.json');
  fs.writeFileSync(configPath, JSON.stringify(singboxConfig, null, 2));

  console.log(`[Proxy Bridge] Starting local SOCKS5 proxy on 127.0.0.1:${LOCAL_SOCKS_PORT}...`);
  singboxProcess = spawn(singboxPath, ['run', '-c', configPath], { stdio: 'inherit' });

  // 等待核心启动建立本地监听
  await new Promise((resolve) => setTimeout(resolve, 3000));

  return { server: `socks5://127.0.0.1:${LOCAL_SOCKS_PORT}` };
}

(async () => {
  if (!DISCORD_TOKEN) {
    console.error('Error: DISCORD_TOKEN secret is required.');
    process.exit(1);
  }

  let proxy = undefined;
  try {
    proxy = await setupProxyBridge();
    if (proxy) console.log(`[Playwright] Using proxy server: ${proxy.server}`);
  } catch (err) {
    console.error(`[Proxy Bridge Error] ${err.message}`);
    process.exit(1);
  }

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

    // 注入 Discord Token 免密登录
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

    await page.waitForTimeout(5000);

    console.log('[Step 2] Navigating to Lunafy Panel...');
    await page.goto('https://panel.lunafy.run/dashboard', { waitUntil: 'networkidle', timeout: 45000 });

    // 登录/OAuth 授权检查
    if (page.url().includes('/login') || (await page.locator('text=Discord').isVisible().catch(() => false))) {
      console.log('[Step 2.1] Clicking Discord OAuth Login button...');
      const discordBtn = page.locator('a[href*="discord"], button:has-text("Discord")').first();
      if (await discordBtn.isVisible()) {
        await discordBtn.click();
        await page.waitForTimeout(4000);

        const authorizeBtn = page.locator('button:has-text("Authorize"), button:has-text("授权")').first();
        if (await authorizeBtn.isVisible({ timeout: 10000 }).catch(() => false)) {
          console.log('[Step 2.2] Authorizing Discord application...');
          await authorizeBtn.click();
          await page.waitForNavigation({ waitUntil: 'networkidle', timeout: 30000 }).catch(() => {});
        }
      }
    }

    await page.goto('https://panel.lunafy.run/dashboard', { waitUntil: 'networkidle', timeout: 30000 });

    console.log('[Step 3] Extracting renewal and status information...');
    await page.waitForSelector('.lunafy-server-status, section[class*="lunafy-server-status"]', { timeout: 20000 });

    const statusText = await page.locator('section[class*="lunafy-server-status"] .fi-badge, section[class*="lunafy-server-status"] [class*="status__heading"]').innerText().catch(() => 'Unknown');
    const datesText = await page.locator('.lunafy-server-status__dates, [class*="status__dates"]').innerText().catch(() => 'Dates not found');
    const actionElement = page.locator('.lunafy-server-status__action, [class*="status__action"]');
    const actionText = (await actionElement.innerText().catch(() => 'Unavailable')).trim();

    console.log(`\n================= Server Status =================`);
    console.log(`[Status] Current Status: ${statusText.replace(/\n/g, ' ')}`);
    console.log(`[Schedule] ${datesText.replace(/\n/g, ' | ')}`);
    console.log(`[Action State] ${actionText}`);
    console.log(`=================================================\n`);

    let renewResult = 'No action needed';
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
    if (singboxProcess) {
      singboxProcess.kill('SIGTERM');
      console.log('[Proxy Bridge] Stopped local Sing-box proxy daemon.');
    }
  }
})();
