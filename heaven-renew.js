const fs = require('fs');
const { chromium } = require('playwright');

const TARGET_URL = process.env.SERVER_URL || 'https://control.heavencloud.in/server/d9063afd/overview';
const COOKIES_RAW = process.env.heavencookies || '[]';
const PROXY_PORT = process.env.LOCAL_PROXY_PORT || '';

// 兼容解析 JSON 数组格式与分号拼接键值对格式的 Cookies
function parseCookies(raw) {
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return parsed.map((item) => {
        const cookie = {
          name: item.name,
          value: item.value,
          path: item.path || '/'
        };
        if (item.domain) {
          cookie.domain = item.domain;
        } else {
          cookie.url = 'https://control.heavencloud.in';
        }
        if (item.secure !== undefined) cookie.secure = item.secure;
        if (item.sameSite) cookie.sameSite = item.sameSite;
        return cookie;
      });
    }
  } catch (e) {
    // 文本格式继续向下解析
  }

  const cookies = [];
  const parts = raw.split(';');
  for (const part of parts) {
    const idx = part.indexOf('=');
    if (idx !== -1) {
      const name = part.slice(0, idx).trim();
      const value = part.slice(idx + 1).trim();
      if (name && value) {
        cookies.push({
          name,
          value,
          domain: 'control.heavencloud.in',
          path: '/',
          secure: true,
          sameSite: 'Lax'
        });
      }
    }
  }
  return cookies;
}

(async () => {
  console.log('[*] 启动 HeavenCloud 续期自动化流程');

  const cookies = parseCookies(COOKIES_RAW);
  if (!cookies || cookies.length === 0) {
    console.error('[!] 错误: 未解析到有效的 heavencookies，请检查 Secrets 配置。');
    process.exit(1);
  }

  const launchOptions = {
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  };

  if (PROXY_PORT) {
    launchOptions.proxy = { server: `http://127.0.0.1:${PROXY_PORT}` };
    console.log(`[*] 已接入本地网络代理: 127.0.0.1:${PROXY_PORT}`);
  }

  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 900 }
  });

  // 注入 Cookies
  await context.addCookies(cookies);
  const page = await context.newPage();

  console.log(`[*] 访问目标服务器页面: ${TARGET_URL}`);
  try {
    await page.goto(TARGET_URL, { waitUntil: 'networkidle', timeout: 60000 });
  } catch (err) {
    console.log(`[*] 页面加载超时或部分资源挂起，继续后续处理: ${err.message}`);
  }

  await page.waitForTimeout(3000);

  // 检查是否跳转回登录页
  if (page.url().includes('/auth/login')) {
    console.error('[!] 登录失效: 页面跳转至登录页，请在 Secrets 中重新设置 heavencookies。');
    await page.screenshot({ path: 'login_failed.png' });
    await browser.close();
    process.exit(1);
  }

  console.log('[+] Cookies 登录有效，正在查找续期倒计时按钮...');

  // 定位续期按钮：支持 title/aria-label 包含 renew 或按钮文本带天数（如 6d 22h）
  let renewBtn = page.locator('button[title*="renew" i], button[aria-label*="Renews" i], button:has-text("d ")').first();
  let count = await renewBtn.count();

  // 若在控制台首页，尝试查找 Manage 按钮跳转进入概览
  if (count === 0) {
    console.log('[*] 未直接发现续期按钮，检测是否存在 Manage 按钮...');
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
    console.log(`[*] 找到续期模块 [${btnText}]，触发点击...`);
    await renewBtn.click();

    await page.waitForTimeout(2500);

    // 抓取绿色 Toast 提示文本 (Too early to renew this one yet.)
    const toast = page.locator('div[role="status"] p, div.shadow-panel p').first();
    if (await toast.count() > 0 && await toast.isVisible()) {
      const msg = (await toast.innerText()).trim();
      console.log(`[+] 捕获到系统提示: "${msg}"`);
    } else {
      console.log('[*] 点击已执行，未抓取到显式提示框，请核验截图。');
    }
  } else {
    console.log('[!] 未定位到续期倒计时按钮，请检查截图。');
  }

  // 保存现场截图
  await page.screenshot({ path: 'renew_result.png' });

  // 提取当前最新会话 Cookies 并保存
  const latestCookies = await context.cookies();
  fs.writeFileSync('new_cookies.json', JSON.stringify(latestCookies, null, 2), 'utf-8');
  console.log('[+] 最新 Cookies 已成功写入 new_cookies.json。');

  await browser.close();
})();
