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

// 发送 Telegram 纯文本通知
async function sendNotification(text) {
  if (!TG_BOT_TOKEN || !TG_CHAT_ID) return;
  try {
    await axios.post(`https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage`, {
      chat_id: TG_CHAT_ID,
      text: text,
      parse_mode: 'Markdown',
    });
    console.log('[通知] Telegram 文本通知已发送');
  } catch (err) {
    console.error('[通知] 发送 Telegram 消息失败:', err.message);
  }
}

// 解析多协议节点链接并生成 Sing-box 规范配置
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
      server_port: parseInt(json.port || 443),
      uuid: json.id,
      alter_id: parseInt(json.aid || 0),
    };

    // 重点：Sing-box 不接受 "auto"，留空或仅在指定加密算法时填入
    if (json.scy && json.scy !== 'auto') {
      outbound.security = json.scy;
    }

    // TLS 与 uTLS 指纹配置
    if (json.tls === 'tls') {
      outbound.tls = {
        enabled: true,
        server_name: json.sni || json.host || json.add,
        insecure: json.insecure === '1' || json.insecure === 1,
      };
      if (json.fp) {
        outbound.tls.utls = {
          enabled: true,
          fingerprint: json.fp,
        };
      }
    }

    // WebSocket 传输与 Early Data 处理
    if (json.net === 'ws') {
      let wsPath = json.path || '/';
      let maxEarlyData = 0;

      if (wsPath.includes('ed=')) {
        const edMatch = wsPath.match(/[?&]ed=(\d+)/);
        if (edMatch) maxEarlyData = parseInt(edMatch[1]);
        wsPath = wsPath.replace(/[?&]ed=\d+/, '').replace(/\?$/, '');
        if (!wsPath) wsPath = '/';
      }

      outbound.transport = {
        type: 'ws',
        path: wsPath,
        headers: (json.host || json.sni) ? { Host: json.host || json.sni } : {},
      };

      if (maxEarlyData > 0) {
        outbound.transport.max_early_data = maxEarlyData;
        outbound.transport.early_data_header_name = 'Sec-WebSocket-Protocol';
      }
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
      if (p.get('fp')) {
        outbound.tls.utls = { enabled: true, fingerprint: p.get('fp') };
      }
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

  throw new Error(`不支持的节点协议: ${uri.slice(0, 15)}...`);
}

// 节点连通性检测（双接口容灾与真实错误日志）
async function testProxyConnectivity(proxyUrl) {
  console.log('\n================ 节点连通性检测 ================');
  const startTime = Date.now();
  const proxyFlag = proxyUrl ? `-x socks5h://127.0.0.1:${LOCAL_SOCKS_PORT}` : '';

  // 优先测试 api.ip.sb，备选 cloudflare 接口
  const testEndpoints = [
    { url: 'https://api.ip.sb/geoip', isJson: true },
    { url: 'https://cloudflare.com/cdn-cgi/trace', isJson: false },
  ];

  for (const ep of testEndpoints) {
    try {
      const testCmd = `curl ${proxyFlag} -sS -m 20 "${ep.url}"`;
      const res = execSync(testCmd, { encoding: 'utf-8' });
      const latency = Date.now() - startTime;

      let ip = '未知', country = '未知', isp = '未知';

      if (ep.isJson) {
        const data = JSON.parse(res);
        ip = data.ip || '未知';
        country = data.country || data.country_code || '未知';
        isp = data.isp || data.organization || '未知';
      } else {
        const ipMatch = res.match(/ip=(.+)/);
        const locMatch = res.match(/loc=(.+)/);
        if (ipMatch) ip = ipMatch[1].trim();
        if (locMatch) country = locMatch[1].trim();
        isp = 'Cloudflare Edge';
      }

      const info = {
        status: '连接正常 (在线)',
        ip,
        country,
        isp,
        latency: `${latency}ms`,
      };

      console.log(`[节点状态] ${info.status}`);
      console.log(`[出口 IP] ${info.ip}`);
      console.log(`[归属地] ${info.country} (${info.isp})`);
      console.log(`[延迟] ${info.latency}`);
      console.log('================================================\n');
      return info;
    } catch (err) {
      // 当前接口尝试失败，自动轮询备用接口
    }
  }

  console.error('[节点状态] 连接失败: 节点离线或握手超时');
  console.log('================================================\n');
  return {
    status: '连接失败 (离线)',
    ip: 'N/A',
    country: 'N/A',
    isp: 'N/A',
    latency: '超时',
  };
}

// 启动代理桥接服务（捕获 Sing-box 真实输出）
async function setupProxyBridge() {
  if (!PROXY_NODE) return undefined;

  if (PROXY_NODE.startsWith('http://') || PROXY_NODE.startsWith('https://') || PROXY_NODE.startsWith('socks5://')) {
    const parsed = new URL(PROXY_NODE);
    const cfg = { server: `${parsed.protocol}//${parsed.hostname}:${parsed.port}` };
    if (parsed.username) cfg.username = decodeURIComponent(parsed.username);
    if (parsed.password) cfg.password = decodeURIComponent(parsed.password);
    return cfg;
  }

  console.log('[代理适配] 正在解析多协议节点配置...');
  const outboundConfig = parseNodeToOutbound(PROXY_NODE);

  const binDir = path.join(__dirname, '.singbox');
  if (!fs.existsSync(binDir)) fs.mkdirSync(binDir, { recursive: true });

  const singboxPath = path.join(binDir, 'sing-box');
  if (!fs.existsSync(singboxPath)) {
    console.log('[代理适配] 正在下载 Sing-box 内核...');
    const arch = os.arch() === 'arm64' ? 'arm64' : 'amd64';
    const coreUrl = `https://github.com/SagerNet/sing-box/releases/download/v1.10.7/sing-box-1.10.7-linux-${arch}.tar.gz`;
    execSync(`curl -sL "${coreUrl}" \vert{} tar -xz -C "${binDir}" --strip-components=1`);
    fs.chmodSync(singboxPath, 0o775);
  }

  const singboxConfig = {
    log: { level: 'warn' },
    dns: {
      servers: [
        { tag: 'dns-remote', address: 'https://1.1.1.1/dns-query', detour: 'direct' }
      ]
    },
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

  const logFilePath = path.join(binDir, 'singbox.log');
  const logFd = fs.openSync(logFilePath, 'w');

  console.log(`[代理适配] 本地代理通道已启动: 127.0.0.1:${LOCAL_SOCKS_PORT}`);
  singboxProcess = spawn(singboxPath, ['run', '-c', configPath], {
    stdio: ['ignore', logFd, logFd],
  });

  // 等待进程稳定并校验是否异常闪退
  await new Promise((resolve) => setTimeout(resolve, 3000));
  if (singboxProcess.exitCode !== null) {
    const logs = fs.readFileSync(logFilePath, 'utf-8');
    throw new Error(`Sing-box 内核退出 (代码 ${singboxProcess.exitCode}):\n${logs}`);
  }

  return { server: `socks5://127.0.0.1:${LOCAL_SOCKS_PORT}` };
}

// 自动识别并处理 Cloudflare 质询
async function handleCloudflareTurnstile(page) {
  try {
    const turnstileIframe = page.locator('iframe[src*="cloudflare.com"], iframe[src*="turnstile"]').first();
    if (await turnstileIframe.isVisible({ timeout: 3000 }).catch(() => false)) {
      console.log('[防护绕过] 检测到 Cloudflare 验证盾，正在尝试点击...');
      await page.waitForTimeout(2000);
      const frame = page.frameLocator('iframe[src*="cloudflare.com"], iframe[src*="turnstile"]').first();
      const checkbox = frame.locator('input[type="checkbox"], .ctp-checkbox-label, #challenge-stage').first();
      if (await checkbox.isVisible({ timeout: 4000 }).catch(() => false)) {
        await checkbox.click();
        await page.waitForTimeout(3000);
      }
    }
  } catch (e) {}
}

(async () => {
  if (!DISCORD_TOKEN) {
    console.error('错误: 缺少 DISCORD_TOKEN 变量');
    process.exit(1);
  }

  let proxy = undefined;
  let proxyInfo = null;

  try {
    proxy = await setupProxyBridge();
    proxyInfo = await testProxyConnectivity(proxy ? proxy.server : undefined);

    if (proxy && proxyInfo.status.includes('失败')) {
      throw new Error(`代理节点连接失败，节点离线或握手超时`);
    }
  } catch (err) {
    console.error(`[代理错误] ${err.message}`);
    await sendNotification(`❌ *Lunafy 续期任务失败*\n代理错误: \`${err.message}\``);
    process.exit(1);
  }

  const browser = await chromium.launch({
    headless: true,
    proxy: proxy,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled',
      '--disable-web-security',
    ],
  });

  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  });

  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  });

  const page = await context.newPage();

  try {
    // 步骤 1：Discord 免密登录
    console.log('[步骤 1] 正在初始化 Discord 登录态...');
    await page.goto('https://discord.com/login', { waitUntil: 'domcontentloaded', timeout: 45000 });

    await page.evaluate((token) => {
      const iframe = document.createElement('iframe');
      document.body.appendChild(iframe);
      iframe.contentWindow.localStorage.token = `"${token}"`;
    }, DISCORD_TOKEN);

    await page.goto('https://discord.com/channels/@me', { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(3000);
    console.log(`[步骤 1] Discord 登录成功，当前地址: ${page.url()}`);

    // 步骤 2：访问 Lunafy 首页
    console.log('[步骤 2] 正在打开 Lunafy 面板首页...');
    await page.goto('https://panel.lunafy.run/', { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(3000);

    await handleCloudflareTurnstile(page);

    // 检测 Discord 快捷登录按钮
    const discordLoginBtn = page.locator('a[href*="discord"], button:has-text("Discord"), a:has-text("Discord")').first();
    if (await discordLoginBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      console.log('[步骤 2.1] 发现 Discord 授权登录入口，正在点击...');
      await discordLoginBtn.click();
      await page.waitForTimeout(4000);
    }

    // 处理 Discord OAuth 授权
    if (page.url().includes('discord.com/oauth2') || page.url().includes('discord.com')) {
      console.log('[步骤 2.2] 正在确认 Discord OAuth 授权...');
      await page.waitForTimeout(2000);
      const authBtn = page.locator('button[type="submit"]:has-text("Authorize"), button:has-text("授权"), button:has-text("Authorize")').last();
      if (await authBtn.isVisible({ timeout: 10000 }).catch(() => false)) {
        await authBtn.click();
        await page.waitForTimeout(5000);
      }
    }

    await handleCloudflareTurnstile(page);

    // 步骤 3：获取服务器状态与时间
    console.log('[步骤 3] 正在获取服务器状态与续期时间...');
    const cardLocator = page.locator('section.lunafy-server-status, section[class*="lunafy-server-status"]').first();
    await cardLocator.waitFor({ state: 'visible', timeout: 35000 });

    const statusBadge = await page.locator('section[class*="lunafy-server-status"] .fi-badge, [class*="status__heading"]').innerText().catch(() => 'Active');
    const rawDatesText = await page.locator('.lunafy-server-status__dates, [class*="status__dates"]').innerText().catch(() => '');

    const nextRenewalMatch = rawDatesText.match(/Next renewal\s+([^\n\r|]+?)(?:\s*Server deleted|$)/i);
    const nextRenewalTime = nextRenewalMatch ? nextRenewalMatch[1].trim() : (rawDatesText || '未知');

    const actionElement = page.locator('.lunafy-server-status__action, [class*="status__action"]').first();
    const actionText = (await actionElement.innerText().catch(() => 'Unavailable')).trim();

    console.log(`\n================= 服务器状态详情 =================`);
    console.log(`[当前状态] ${statusBadge.replace(/Server Status:/gi, '').trim()}`);
    console.log(`[下次续期时间] ${nextRenewalTime}`);
    console.log(`[续期按钮状态] ${actionText}`);
    console.log(`==================================================\n`);

    // 步骤 4：判断并执行续期操作
    let needRenew = '否 (未到续期时间)';
    const renewBtn = actionElement.locator('button, a').first();
    const canRenew = (await renewBtn.isVisible().catch(() => false)) && !actionText.toLowerCase().includes('unavailable');

    if (canRenew) {
      console.log('[步骤 4] 满足续期条件，正在执行一键续期...');
      await renewBtn.click();
      await page.waitForTimeout(4000);
      needRenew = '是 (已成功执行续期)';
      console.log(`[执行结果] 续期请求已提交`);
    } else {
      console.log('[步骤 4] 当前无需续期，等待下次调度周期');
    }

    // 发送 Telegram 纯文本消息通知
    const summaryMessage = `📋 *Lunafy 续期状态通知*\n\n` +
      `⏰ *下次续期时间:* \`${nextRenewalTime}\`\n` +
      `🔄 *是否执行续期:* *${needRenew}*\n\n` +
      `🖥️ *服务器状态:* \`${statusBadge.replace(/Server Status:/gi, '').trim()}\`\n` +
      `🌐 *出站节点:* \`${proxyInfo.country} (${proxyInfo.isp})\``;

    await sendNotification(summaryMessage);

  } catch (error) {
    console.error('[执行异常]', error.message);
    await sendNotification(`❌ *Lunafy 续期任务失败*\n异常原因: \`${error.message}\``);
    process.exit(1);
  } finally {
    await browser.close();
    if (singboxProcess) {
      singboxProcess.kill('SIGTERM');
      console.log('[代理适配] 本地代理通道已关闭');
    }
  }
})();
