const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { execSync, spawn } = require('child_process');
const YAML = require('yaml');

const TARGET_URL = process.env.SERVER_URL || 'https://control.heavencloud.in/server/d9063afd/overview';
const COOKIES_RAW = process.env.heavencookies || '[]';
const PROXY_CONFIG_RAW = (process.env.heaven_PROXY_CONFIG || '').trim();
const CONFIG_DIR = '/tmp/mihomo';

/**
 * 1. Cookies 清洗与标准化（适配 Playwright strict/lax/none 枚举）
 */
function sanitizeCookies(raw) {
  if (!raw) return [];
  let list = [];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) list = parsed;
    else if (parsed && typeof parsed === 'object') {
      list = Object.entries(parsed).map(([name, value]) => ({ name, value: String(value) }));
    }
  } catch (e) {
    list = String(raw).split(';').map(part => {
      const idx = part.indexOf('=');
      return idx > -1 ? { name: part.slice(0, idx).trim(), value: part.slice(idx + 1).trim() } : null;
    }).filter(Boolean);
  }

  const sanitized = [];
  for (const item of list) {
    if (!item || !item.name) continue;
    const clean = {
      name: String(item.name).trim(),
      value: String(item.value ?? '').trim(),
      domain: item.domain ? String(item.domain).trim() : 'control.heavencloud.in',
      path: item.path ? String(item.path).trim() : '/'
    };

    if (clean.domain.startsWith('http://') || clean.domain.startsWith('https://')) {
      try {
        clean.domain = new URL(clean.domain).hostname;
      } catch (_) {
        clean.domain = 'control.heavencloud.in';
      }
    }

    if (typeof item.secure === 'boolean') clean.secure = item.secure;
    if (typeof item.httpOnly === 'boolean') clean.httpOnly = item.httpOnly;
    if (typeof item.expires === 'number' && item.expires > 0) clean.expires = Math.floor(item.expires);

    // 纠正 sameSite
    if (item.sameSite) {
      const s = String(item.sameSite).toLowerCase().trim();
      if (s === 'lax') clean.sameSite = 'Lax';
      else if (s === 'strict') clean.sameSite = 'Strict';
      else if (s === 'none' || s === 'no_restriction') clean.sameSite = 'None';
    }

    sanitized.push(clean);
  }
  return sanitized;
}

/**
 * 2. 多协议解析器：支持 VMess / VLESS / Hysteria2 / Trojan / SS / 订阅
 */
async function parseProxyToMihomo(rawInput) {
  let text = rawInput.replace(/^["']|["']$/g, '').trim();
  if (!text) return null;

  // 1. 如果是 HTTP/HTTPS 订阅链接
  if (text.startsWith('http://') || text.startsWith('https://')) {
    console.log('[*] 检测到订阅链接，正在请求获取节点配置...');
    const res = await fetch(text, { headers: { 'User-Agent': 'ClashMeta; Mihomo' } });
    text = await res.text();
    text = text.trim();
    if (!text.includes('proxies:') && !text.includes('server:') && /^[A-Za-z0-9+/=\r\n]+$/.test(text)) {
      text = Buffer.from(text, 'base64').toString('utf-8');
    }
  }

  // 2. 如果包含 Clash YAML 格式
  if (text.includes('proxies:')) {
    try {
      const parsed = YAML.parse(text);
      if (parsed && Array.isArray(parsed.proxies) && parsed.proxies.length > 0) {
        return parsed;
      }
    } catch (e) {}
  }

  const lines = text.split(/[\r\n]+/).map(l => l.trim()).filter(Boolean);
  const proxies = [];

  for (const line of lines) {
    try {
      // 解析 VMess 协议 (Base64 JSON)
      if (line.startsWith('vmess://')) {
        const b64 = line.slice(8).trim();
        const vJson = JSON.parse(Buffer.from(b64, 'base64').toString('utf-8'));
        const node = {
          name: vJson.ps || `vmess-${vJson.add}`,
          type: 'vmess',
          server: vJson.add,
          port: parseInt(vJson.port || '443', 10),
          uuid: vJson.id,
          alterId: parseInt(vJson.aid || '0', 10),
          cipher: vJson.scy || 'auto',
          udp: true,
          tls: vJson.tls === 'tls',
          'skip-cert-verify': vJson.insecure === '1'
        };
        if (vJson.sni || vJson.host) {
          node.servername = vJson.sni || vJson.host;
        }
        if (vJson.fp) {
          node['client-fingerprint'] = vJson.fp;
        }
        if (vJson.alpn) {
          const arr = vJson.alpn.split(',').map(s => s.trim()).filter(Boolean);
          if (arr.length > 0) node.alpn = arr;
        }
        const net = (vJson.net || 'tcp').toLowerCase();
        node.network = net;
        if (net === 'ws') {
          node['ws-opts'] = {
            path: vJson.path || '/',
            headers: { Host: vJson.host || vJson.sni || vJson.add }
          };
        } else if (net === 'grpc') {
          node['grpc-opts'] = { 'grpc-service-name': vJson.path || '' };
        }
        proxies.push(node);
      }
      // 解析 VLESS 协议
      else if (line.startsWith('vless://')) {
        const u = new URL(line);
        const p = u.searchParams;
        const node = {
          name: decodeURIComponent(u.hash ? u.hash.slice(1) : '') || `vless-${u.hostname}`,
          type: 'vless',
          server: u.hostname,
          port: parseInt(u.port || '443', 10),
          uuid: u.username,
          cipher: 'auto',
          udp: true,
          tls: ['tls', 'reality'].includes(p.get('security'))
        };
        if (p.get('sni')) node.servername = p.get('sni');
        if (p.get('flow')) node.flow = p.get('flow');
        if (p.get('fp')) node['client-fingerprint'] = p.get('fp');
        if (p.get('security') === 'reality') {
          node['reality-opts'] = {
            'public-key': p.get('pbk') || '',
            'short-id': p.get('sid') || ''
          };
        }
        const net = p.get('type') || 'tcp';
        node.network = net;
        if (net === 'ws') {
          node['ws-opts'] = {
            path: decodeURIComponent(p.get('path') || '/'),
            headers: { Host: p.get('host') || p.get('sni') || u.hostname }
          };
        }
        proxies.push(node);
      }
      // 解析 Hysteria2 协议
      else if (line.startsWith('hysteria2://') || line.startsWith('hy2://')) {
        const u = new URL(line);
        const p = u.searchParams;
        proxies.push({
          name: decodeURIComponent(u.hash ? u.hash.slice(1) : '') || `hy2-${u.hostname}`,
          type: 'hysteria2',
          server: u.hostname,
          port: parseInt(u.port || '443', 10),
          password: decodeURIComponent(u.username || u.password || ''),
          sni: p.get('sni') || u.hostname,
          'skip-cert-verify': p.get('insecure') === '1'
        });
      }
      // 解析 Trojan 协议
      else if (line.startsWith('trojan://')) {
        const u = new URL(line);
        const p = u.searchParams;
        proxies.push({
          name: decodeURIComponent(u.hash ? u.hash.slice(1) : '') || `trojan-${u.hostname}`,
          type: 'trojan',
          server: u.hostname,
          port: parseInt(u.port || '443', 10),
          password: decodeURIComponent(u.username || u.password || ''),
          sni: p.get('sni') || u.hostname,
          udp: true,
          'skip-cert-verify': p.get('allowInsecure') === '1'
        });
      }
    } catch (err) {
      console.log(`[*] 解析节点单行失败 [${line.slice(0, 20)}...]: ${err.message}`);
    }
  }

  // 尝试直接解析为 Clash proxies
  if (proxies.length === 0 && (text.includes('- name:') || text.includes('type:'))) {
    try {
      const parsed = YAML.parse(`proxies:\n${text}`);
      if (parsed && Array.isArray(parsed.proxies)) return parsed;
    } catch (_) {}
  }

  if (proxies.length === 0) return null;

  return {
    'mixed-port': 7890,
    'allow-lan': false,
    'mode': 'rule',
    'log-level': 'warning',
    proxies,
    'proxy-groups': [
      {
        name: 'AUTO_PROXY',
        type: 'select',
        proxies: proxies.map(p => p.name)
      }
    ],
    rules: ['MATCH,AUTO_PROXY']
  };
}

/**
 * 3. 环境清理
 */
function cleanEnv() {
  console.log('[*] 正在执行环境清理（关闭残留 Mihomo、Chrome 与临时目录）...');
  try { execSync('pkill -9 -f mihomo || true'); } catch (_) {}
  try { execSync('pkill -9 -f chrome || true'); } catch (_) {}
  try { execSync('pkill -9 -f playwright || true'); } catch (_) {}
  try { fs.rmSync(CONFIG_DIR, { recursive: true, force: true }); } catch (_) {}
  try { fs.unlinkSync('new_cookies.json'); } catch (_) {}
  console.log('[+] 清理完成。');
}

/**
 * 4. 代理启动与验证
 */
async function setupProxy() {
  cleanEnv();
  if (!PROXY_CONFIG_RAW) {
    console.log('[*] 未检测到 heaven_PROXY_CONFIG，无需部署代理。');
    return;
  }

  console.log('[*] 检测到 heaven_PROXY_CONFIG，正在解析多协议配置...');
  const mihomoConfig = await parseProxyToMihomo(PROXY_CONFIG_RAW);

  if (!mihomoConfig || !mihomoConfig.proxies || mihomoConfig.proxies.length === 0) {
    console.error('[!] 错误: 无法解析输入的代理配置，请检查协议链接或 YAML 格式！');
    process.exit(1);
  }

  console.log(`[+] 成功解析到 ${mihomoConfig.proxies.length} 个代理节点，首选节点: [${mihomoConfig.proxies[0].name}]`);
  fs.mkdirSync(CONFIG_DIR, { recursive: true });

  // 下载 Mihomo 核心
  console.log('[*] 正在拉取 Mihomo 内核...');
  execSync('curl -sL "https://github.com/MetaCubeX/mihomo/releases/download/v1.18.7/mihomo-linux-amd64-v1.18.7.gz" | gzip -d > /tmp/mihomo/mihomo');
  execSync('chmod +x /tmp/mihomo/mihomo');

  // 生成 config.yaml
  fs.writeFileSync(path.join(CONFIG_DIR, 'config.yaml'), YAML.stringify(mihomoConfig), 'utf-8');

  // 启动后台代理
  console.log('[*] 正在启动 Mihomo 后台代理 (127.0.0.1:7890)...');
  const logFile = fs.openSync(path.join(CONFIG_DIR, 'mihomo.log'), 'a');
  const p = spawn('/tmp/mihomo/mihomo', ['-d', CONFIG_DIR], {
    detached: true,
    stdio: ['ignore', logFile, logFile]
  });
  p.unref();

  // 等待启动并做连通性测试
  await new Promise(r => setTimeout(r, 4000));
  try {
    execSync('curl -s -I -x http://127.0.0.1:7890 https://cp.cloudflare.com/generate_204 --connect-timeout 8 > /dev/null');
    console.log('[+] 本地代理 127.0.0.1:7890 启动成功，网络连接正常！');
  } catch (e) {
    console.log('[!] 提示: 代理已启动并监听，日志如下:');
    try {
      console.log(fs.readFileSync(path.join(CONFIG_DIR, 'mihomo.log'), 'utf-8'));
    } catch (_) {}
  }
}

/**
 * 5. 主续期任务
 */
async function runRenew() {
  const cookies = sanitizeCookies(COOKIES_RAW);
  if (!cookies || cookies.length === 0) {
    console.error('[!] 错误: 未检测到有效 heavencookies，请检查 Secrets 配置。');
    process.exit(1);
  }
  console.log(`[+] 成功解析并载入 ${cookies.length} 个 Cookies。`);

  const launchOptions = {
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  };

  if (PROXY_CONFIG_RAW) {
    launchOptions.proxy = { server: 'http://127.0.0.1:7890' };
    console.log('[*] 已挂载本地代理: 127.0.0.1:7890');
  }

  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 900 }
  });

  await context.addCookies(cookies);
  const page = await context.newPage();

  console.log(`[*] 正在进入目标页面: ${TARGET_URL}`);
  try {
    await page.goto(TARGET_URL, { waitUntil: 'networkidle', timeout: 60000 });
  } catch (e) {
    console.log(`[*] 页面网络响应耗时较长，继续后续步骤: ${e.message}`);
  }

  await page.waitForTimeout(3000);

  if (page.url().includes('/auth/login')) {
    console.error('[!] Cookies 已失效（重定向到了登录页面），请更新 heavencookies。');
    await page.screenshot({ path: 'login_failed.png' });
    await browser.close();
    process.exit(1);
  }

  console.log('[+] 登录有效，正在寻找续期倒计时按钮...');

  let renewBtn = page.locator('button[title*="renew" i], button[aria-label*="Renews" i], button:has-text("d ")').first();
  let count = await renewBtn.count();

  if (count === 0) {
    console.log('[*] 未直接找到续期按钮，尝试寻找 Manage 入口...');
    const manageBtn = page.locator('a[href*="/overview"], button:has-text("Manage")').first();
    if (await manageBtn.count() > 0) {
      await manageBtn.click();
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.waitForTimeout(3000);
      renewBtn = page.locator('button[title*="renew" i], button[aria-label*="Renews" i], button:has-text("d ")').first();
      count = await renewBtn.count();
    }
  }

  if (count > 0) {
    const btnText = (await renewBtn.innerText()).replace(/\n/g, ' ').trim();
    console.log(`[*] 找到续期模块 [${btnText}]，执行点击...`);
    await renewBtn.click();

    await page.waitForTimeout(2500);

    const toast = page.locator('div[role="status"] p, div.shadow-panel p').first();
    if (await toast.count() > 0 && await toast.isVisible()) {
      const msg = (await toast.innerText()).trim();
      console.log(`[+] 成功捕获反馈提示: "${msg}"`);
    } else {
      console.log('[*] 已点击，未抓取到显式提示文本，可通过截图核验。');
    }
  } else {
    console.log('[!] 未定位到续期按钮，请查看截图排查页面状态。');
  }

  await page.screenshot({ path: 'renew_result.png' });

  // 提取最新 Cookies
  const latestCookies = await context.cookies();
  fs.writeFileSync('new_cookies.json', JSON.stringify(latestCookies, null, 2), 'utf-8');
  console.log('[+] 当前最新会话 Cookies 已导出至 new_cookies.json');

  await browser.close();
}

// 命令行分流
(async () => {
  const arg = process.argv[2];
  if (arg === '--setup-proxy') {
    await setupProxy();
  } else if (arg === '--clean') {
    cleanEnv();
  } else {
    await runRenew();
  }
})();
