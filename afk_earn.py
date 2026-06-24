#!/usr/bin/env python3

import os
import re
import json
import time
import traceback
from datetime import datetime
from urllib.request import Request, urlopen
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

DISCORD_TOKEN = os.environ.get("FREEZEHOST_DISCORD_TOKEN", "").strip()
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID", "").strip()
ACCOUNT_INDEX = os.environ.get("ACCOUNT_INDEX", "1").strip()
MAX_RUNTIME   = int(os.environ.get("MAX_RUNTIME", "300"))  

BASE_URL   = "https://free.freezehost.pro"
MAX_SITE_RETRIES = 3
RETRY_WAIT = 30000

def log_info(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    safe_msg = msg.replace(DISCORD_TOKEN, "***") if DISCORD_TOKEN else msg
    print(f"[{ts}] [账号 {ACCOUNT_INDEX}] {safe_msg}", flush=True)

def log_warn(msg: str):
    log_info(f"⚠️ {msg}")

def send_tg(text: str):
    if not TG_CHAT_ID or not TG_BOT_TOKEN: return
    try:
        req = Request(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=10)
    except Exception as e:
        log_info(f"TG 推送异常: {e}")

# =========================================================================
# 🐾 100% 还原原版的站点检测与稳健登录逻辑
# =========================================================================
def check_site_down(page) -> bool:
    try:
        return page.evaluate("""() => {
            const body = document.body ? document.body.innerText : '';
            if (body.includes('CONNECTION TO THE MANAGEMENT SERVICES LOST')) return true;
            if (body.includes('Retrying in') && body.includes('Retry Now')) return true;
            if (document.querySelector('button:has-text("Retry Now")')) return true;
            return false;
        }""")
    except Exception:
        return False

def wait_for_site_ready(page) -> bool:
    for attempt in range(1, MAX_SITE_RETRIES + 1):
        log_info(f"加载 FreezeHost 首页 (尝试 {attempt}/{MAX_SITE_RETRIES})...")
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeout:
            log_warn(f"首页加载超时 (尝试 {attempt})")
            if attempt < MAX_SITE_RETRIES: page.wait_for_timeout(RETRY_WAIT)
            continue

        page.wait_for_timeout(3000)

        if check_site_down(page):
            log_warn(f"FreezeHost 后端服务不可用 (尝试 {attempt})")
            try:
                retry_btn = page.locator('button:has-text("Retry Now")')
                if retry_btn.is_visible():
                    log_info("点击页面 Retry Now 按钮...")
                    retry_btn.click()
                    page.wait_for_timeout(10000)
                    if not check_site_down(page):
                        log_info("站点恢复正常")
                        return True
            except Exception: pass
            if attempt < MAX_SITE_RETRIES:
                log_info(f"等待 {RETRY_WAIT // 1000} 秒后重试...")
                page.wait_for_timeout(RETRY_WAIT)
            continue

        try:
            if page.locator('span.text-lg:has-text("Login with Discord")').is_visible():
                log_info("首页加载正常，登录按钮可见")
                return True
        except Exception: pass
        log_info("首页已加载（未检测到宕机页面）")
        return True
    return False

def handle_oauth_page(page):
    log_info("进入 OAuth 授权页处理")
    page.wait_for_timeout(2000)
    for _ in range(20):
        if "discord.com" not in page.url: return
        btn_text = ""
        try:
            for sel in ['button[type="submit"]', 'div[class*="footer"] button', 'button[class*="primary"]']:
                btn = page.locator(sel).last
                if btn.is_visible():
                    btn_text = btn.inner_text().strip().lower()
                    break
        except Exception: pass
        if "authorize" in btn_text and "scroll" not in btn_text: break
        page.evaluate("""() => {
            const sels = ['[class*="scroller"]','[class*="oauth2"]','[class*="permissionList"]',
                '[class*="content"] [class*="scroll"]','[class*="listScroller"]'];
            let scrolled = false;
            for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                    if (el.scrollHeight > el.clientHeight) { el.scrollTop = el.scrollHeight; scrolled = true; }
                }
            }
            if (!scrolled) scrollTo(0, document.body.scrollHeight);
        }""")
        page.wait_for_timeout(800)

    for _ in range(10):
        if "discord.com" not in page.url: return
        for sel in ['button:has-text("Authorize")','button:has-text("授权")', 'button[type="submit"]']:
            try:
                btn = page.locator(sel).last
                if not btn.is_visible(): continue
                text = btn.inner_text().strip()
                if any(k in text.lower() for k in ("取消","cancel","deny")): continue
                if "scroll" in text.lower():
                    page.evaluate("scrollTo(0, document.body.scrollHeight);")
                    page.wait_for_timeout(1000)
                    break
                if btn.is_disabled():
                    page.wait_for_timeout(1000)
                    break
                btn.click()
                page.wait_for_timeout(2000)
                if "discord.com" not in page.url: return
                break
            except Exception: continue
        page.wait_for_timeout(1500)


# =========================================================================
# 🐾 全自动挂机防冻防断线 JS 引擎 (适配 HOLD TO START)
# =========================================================================
AFK_JS_PAYLOAD = r"""
if (window.top === window.self) {
    window.addEventListener('load', function () {
        if (!window.location.href.includes('/earn')) return;
        console.log('[AFKv20] 注入成功，正在接管挂机逻辑');

        const CFG = { CHECK_INTERVAL: 1000, FORCE_REFRESH: 3600 * 1000, CLICK_DEBOUNCE: 5000 };
        const workerCode = `
            let iv = null;
            self.onmessage = function(e) {
                if (e.data === 'start') { if (!iv) iv = setInterval(() => self.postMessage('tick'), 1000); }
                else if (e.data === 'stop') { clearInterval(iv); iv = null; }
            };
        `;
        const worker = new Worker(URL.createObjectURL(new Blob([workerCode], { type: 'application/javascript' })));
        const startTime = Date.now();
        let lastClickTime = 0, tickCount = 0;

        const panel = document.createElement('div');
        panel.id = 'afk-panel';
        panel.style.cssText = [
            'position:fixed','bottom:20px','right:20px','z-index:2147483647','width:280px',
            'background:linear-gradient(145deg,#0f0f1a,#1a1a2e)','border:1px solid rgba(100,100,255,0.3)',
            'border-radius:14px','box-shadow:0 8px 32px rgba(0,0,0,0.6)','font-family:monospace',
            'font-size:12px','color:#e0e0e0','overflow:hidden','user-select:none',
        ].join(';');

        panel.innerHTML = `
            <div id="afk-header" style="background:rgba(255,255,255,0.05);padding:10px;border-bottom:1px solid rgba(255,255,255,0.07);">
              <span style="font-weight:bold;color:#7eb3ff;">🤖 挂机引擎 v21</span>
              <span id="afk-uptime" style="float:right;color:#aaa;">0分0秒</span>
            </div>
            <div style="padding:10px;">
              <div id="afk-status-row" style="padding:8px;background:rgba(255,255,255,0.04);border-radius:8px;border-left:3px solid #888;margin-bottom:8px;">
                <div id="afk-status-title" style="font-weight:bold;color:#fff;">初始化中...</div>
              </div>
              <div style="display:flex;gap:8px;margin-bottom:8px;">
                <div style="flex:1;background:rgba(255,255,255,0.04);border-radius:8px;text-align:center;padding:5px;">
                  <div style="color:#aaa;font-size:10px;">SESSION</div>
                  <div id="afk-timer" style="color:#7eb3ff;font-size:14px;font-weight:bold;">--:--</div>
                </div>
              </div>
            </div>`;
        document.body.appendChild(panel);

        (function startSilentAudio() {
            try {
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                const ctx = new AudioCtx();
                const buffer = ctx.createBuffer(1, ctx.sampleRate * 0.5, ctx.sampleRate);
                const gain = ctx.createGain(); gain.gain.value = 0; gain.connect(ctx.destination);
                function playLoop() {
                    const src = ctx.createBufferSource(); src.buffer = buffer; src.connect(gain);
                    src.onended = playLoop; src.start();
                }
                function activate() {
                    ctx.resume().then(() => {
                        playLoop(); document.removeEventListener('click', activate, true);
                    }).catch(e => console.warn(e));
                }
                if (ctx.state === 'running') { playLoop(); }
                else { document.addEventListener('click', activate, true); }
            } catch (err) {}
        })();

        function bypassAdblock() {
            if(typeof adblockerDetected !== 'undefined') adblockerDetected = false;
            var msg = document.getElementById('adblocker-message'); if(msg) msg.style.display = 'none';
        }

        // 🐾 修复点：模拟物理长按 (HOLD TO START)
        function tryHoldClick(el) {
            if (Date.now() - lastClickTime < CFG.CLICK_DEBOUNCE) return;
            lastClickTime = Date.now(); 
            el.disabled = false;
            
            console.log("执行长按操作...");
            const mousedown = new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window});
            const mouseup = new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window});
            
            el.dispatchEvent(mousedown);
            setTimeout(() => {
                el.dispatchEvent(mouseup);
                el.click();
            }, 800); // 模拟按住 800 毫秒
        }

        function findStartButton() {
            const btns = Array.from(document.querySelectorAll('button'));
            return btns.find(b => {
                const t = b.innerText.toUpperCase();
                return t.includes('HOLD TO START') || t.includes('START NEW SESSION') || t.includes('START AFK');
            });
        }

        function loop() {
            tickCount++;
            const remaining = CFG.FORCE_REFRESH - (Date.now() - startTime);
            bypassAdblock();
            
            if (remaining <= 0) { worker.postMessage('stop'); location.reload(); return; }
            
            const timerEl = document.getElementById('session-timer');
            const timerText = timerEl ? timerEl.innerText.trim() : '--:--';
            document.getElementById('afk-timer').textContent = timerText;
            
            const bodyText = document.body.innerText;
            if (bodyText.includes('You are now earning coins') || bodyText.includes('1 coin will be added')) {
                document.getElementById('afk-status-title').textContent = '💰 稳定获取金币中';
                return;
            }

            if (timerText === '0:00' || timerText === '00:00') {
                document.getElementById('afk-status-title').textContent = '🔄 Session 续期中';
                const btn = findStartButton();
                if (btn) tryHoldClick(btn);
                else setTimeout(() => location.reload(), 2000);
                return;
            }
            
            const startBtn = findStartButton();
            if (startBtn && startBtn.offsetParent !== null) { 
                document.getElementById('afk-status-title').textContent = '🚀 点击开始赚币';
                tryHoldClick(startBtn); 
                return; 
            }
            document.getElementById('afk-status-title').textContent = '⏳ 等待操作/加载';
        }
        worker.onmessage = () => loop(); worker.postMessage('start');
    });
}
"""

def run_pipeline():
    if not DISCORD_TOKEN:
        log_info("跳过：未配置 FREEZEHOST_DISCORD_TOKEN")
        return

    with sync_playwright() as pw:
        log_info("🚀 启动浏览器 (Headed 模式 + Stealth 防检测)")
        
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-blink-features=AutomationControlled" # 隐藏自动化特征
            ]
        )
        # 伪装 user_agent
        context = browser.new_context(
            viewport={"width": 1280, "height": 753},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # 🐾 修复点：彻底抹除 Webdriver 指纹，防止 headless_detected 封禁！
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        context.add_init_script(AFK_JS_PAYLOAD)
        
        page = context.new_page()
        page.set_default_timeout(60_000)

        try:
            # ── 1. 稳健登录 ──────────────────────
            log_info("打开 FreezeHost 登录页...")
            page.goto(BASE_URL, wait_until="domcontentloaded")
            
            try:
                page.click('span.text-lg:has-text("Login with Discord")', timeout=15000)
                page.evaluate("document.querySelector('button#confirm-login')?.click();")
                log_info("已接受服务条款 (通过底层JS点击)")
            except Exception as e:
                log_info(f"点击条款时出现波动: {e}")

            # 🐾 修复点：加长登录跳转容忍度，防止偶尔的网络波动导致 20 秒崩溃
            try:
                page.wait_for_url(re.compile(r"discord\.com"), timeout=40000)
                log_info("已到达 Discord, 开始注入 Token...")
            except PlaywrightTimeout:
                log_warn("Discord 跳转超时，但尝试继续执行...")

            page.evaluate("""(token) => {
                const f = document.createElement('iframe'); f.style.display = 'none'; document.body.appendChild(f);
                f.contentWindow.localStorage.setItem('token', '"'+token+'"');
                try { localStorage.setItem('token', '"'+token+'"'); } catch(e) {}
                document.body.removeChild(f);
            }""", DISCORD_TOKEN)
            
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            if re.search(r"discord\.com/login", page.url):
                raise RuntimeError("Token 已失效，请更换 Token！")

            log_info("Token 注入成功，处理 OAuth 跳转...")
            try:
                page.wait_for_url(re.compile(r"discord\.com/oauth2/authorize"), timeout=15000)
                if "discord.com" in page.url: handle_oauth_page(page)
            except PlaywrightTimeout: pass

            try:
                page.wait_for_url(re.compile(r"/callback|/dashboard|/earn"), timeout=45000)
            except PlaywrightTimeout:
                if "free.freezehost.pro" in page.url:
                    log_warn("跳回超时，但已处于主站，强制放行")
                else:
                    raise RuntimeError("OAuth 彻底卡死在 Discord，无法返回")

            if "/callback" in page.url:
                page.wait_for_url(re.compile(r"/dashboard|/earn"), timeout=15000)

            log_info("✅ 登录成功！")

            # ── 2. 发现服务器并续费 ──────────────────────
            log_info("进入 Dashboard 获取服务器列表...")
            page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            
            server_links = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href^="/server/"]'))
                     .map(a => a.getAttribute('href'))
                     .filter(href => href.split('/').length >= 3);
            }""")
            server_links = list(set(server_links))
            
            if not server_links:
                log_info("❌ 未发现任何服务器，跳过续费步骤。")
            else:
                log_info(f"✅ 发现 {len(server_links)} 台服务器，开始依次检查续费...")
                
                for link in server_links:
                    server_id = link.split('/')[-1]
                    url = f"{BASE_URL}{link}"
                    log_info(f"[{server_id}] 打开控制台...")
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                    
                    clicked = page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const target = btns.find(b => b.innerText.includes('Extend') || b.innerText.includes('Renew') || b.innerText.includes('연장'));
                        if (target && !target.disabled) { target.click(); return true; }
                        const bkrtgq = document.querySelector('button.bkrtgq');
                        if (bkrtgq && !bkrtgq.disabled) { bkrtgq.click(); return true; }
                        return false;
                    }""")
                    
                    if not clicked:
                        log_info(f"[{server_id}] ⏭️ 未找到可用的续期按钮 (可能冷却中或界面未加载)")
                        continue
                        
                    log_info(f"[{server_id}] 🖱️ 已点击续期，探测 CF 盾牌...")
                    # 简单过盾
                    try:
                        for _ in range(10):
                            iframe = page.frame_locator('iframe[src^="https://challenges.cloudflare.com"]').first
                            if iframe:
                                cb = iframe.locator('input[type="checkbox"], .cb-lb')
                                if cb.is_visible(timeout=1000): cb.click()
                            page.wait_for_timeout(2000)
                    except: pass
                    
                    page.wait_for_timeout(3000)
                    page.evaluate("""() => {
                        document.querySelectorAll('button').forEach(b => {
                            if (b.innerText.includes('Next') || b.innerText.includes('닫기') || b.innerText.includes('Close') || b.innerText.includes('Confirm')) {
                                b.click();
                            }
                        });
                    }""")
                    log_info(f"[{server_id}] ✅ 续期操作执行完毕")
                    page.wait_for_timeout(2000)

            # ── 3. 进入挂机战场 ──────────────────────────────────
            log_info("🚀 续期完毕，跳转 /earn 页面开启挂机印钞模式！")
            page.goto(f"{BASE_URL}/earn", wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {ACCOUNT_INDEX}\n✅ 续期探测完成，正式开启挂机赚币模式！")
            
            global_start = time.time()
            max_runtime_sec = MAX_RUNTIME * 60
            loop_counter = 0

            # 挂机死循环守护
            while time.time() - global_start < max_runtime_sec:
                loop_counter += 1
                try:
                    iframe = page.frame_locator('iframe[src^="https://challenges.cloudflare.com"]').first
                    if iframe:
                        cb = iframe.locator('input[type="checkbox"], .cb-lb')
                        if cb.is_visible(timeout=1000):
                            cb.click()
                            log_info("🛡️ 自动点碎 Turnstile 验证框")
                except: pass
                
                if "/earn" not in page.url:
                    log_info(f"⚠️ URL 偏移 (当前: {page.url})，尝试拉回战场...")
                    page.goto(f"{BASE_URL}/earn", wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                
                if loop_counter % 6 == 0:
                    try:
                        ui_status = page.evaluate("() => document.getElementById('afk-status-title')?.innerText || '等待注入'")
                        ui_timer  = page.evaluate("() => document.getElementById('afk-timer')?.innerText || '--:--'")
                        log_info(f"📊 网页探针回传 | 状态: {ui_status} | 倒计时: {ui_timer}")
                    except: pass
                
                # 双重保险：如果在探针里发现还没点下去，Python 亲自下场模拟点击
                try:
                    page.evaluate("""() => {
                        const startBtn = document.getElementById('start-afk-btn');
                        if(startBtn && startBtn.innerText.includes('HOLD TO START')){
                            const mousedown = new MouseEvent('mousedown', {bubbles: true});
                            const mouseup = new MouseEvent('mouseup', {bubbles: true});
                            startBtn.dispatchEvent(mousedown);
                            setTimeout(() => { startBtn.dispatchEvent(mouseup); startBtn.click(); }, 800);
                        }
                    }""")
                except: pass

                page.wait_for_timeout(10000)

            log_info(f"挂机任务圆满结束。")
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {ACCOUNT_INDEX} 挂机圆满结束\n⏱️ 共计稳定运行 {MAX_RUNTIME} 分钟！")

        except Exception as e:
            log_info(f"❌ 全局异常崩溃: {e}")
            traceback.print_exc()
        finally:
            context.close()
            browser.close()

if __name__ == "__main__":
    run_pipeline()
