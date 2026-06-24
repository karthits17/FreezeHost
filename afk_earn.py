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
# 🐾 100% 还原原版的站点检测与登录逻辑
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
            if attempt < MAX_SITE_RETRIES:
                page.wait_for_timeout(RETRY_WAIT)
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
            login_visible = page.locator('span.text-lg:has-text("Login with Discord")').is_visible()
            if login_visible:
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
                '[class*="content"] [class*="scroll"]','[class*="listScroller"]',
                'div[class*="modal"] div[style*="overflow"]','div[class*="root"] div[style*="overflow"]'];
            let scrolled = false;
            for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                    const s = getComputedStyle(el);
                    if (el.scrollHeight > el.clientHeight &&
                        ['auto','scroll'].some(v => s.overflowY === v || s.overflow === v))
                        { el.scrollTop = el.scrollHeight; scrolled = true; }
                }
            }
            if (!scrolled) document.querySelectorAll('div').forEach(el => {
                if (el.scrollHeight > el.clientHeight + 10) {
                    const s = getComputedStyle(el);
                    if (['auto','scroll','hidden'].includes(s.overflowY)) el.scrollTop = el.scrollHeight;
                }
            });
            scrollTo(0, document.body.scrollHeight);
        }""")
        page.wait_for_timeout(800)

    for _ in range(10):
        if "discord.com" not in page.url: return
        for sel in ['button:has-text("Authorize")','button:has-text("授权")',
                    'button[type="submit"]','div[class*="footer"] button','button[class*="primary"]']:
            try:
                btn = page.locator(sel).last
                if not btn.is_visible(): continue
                text = btn.inner_text().strip()
                if any(k in text.lower() for k in ("取消","cancel","deny")): continue
                if "scroll" in text.lower():
                    page.evaluate("""() => {
                        document.querySelectorAll('div').forEach(el => {
                            if (el.scrollHeight > el.clientHeight + 5) el.scrollTop = el.scrollHeight;
                        }); scrollTo(0, document.body.scrollHeight);
                    }""")
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
# 🐾 史诗级融合：全自动挂机防冻防断线 JS 注入
# =========================================================================
AFK_JS_PAYLOAD = r"""
if (window.top === window.self) {
    window.addEventListener('load', function () {
        if (!window.location.href.includes('/earn')) return;
        
        console.log('[AFKv20] 注入成功，正在接管挂机逻辑');

        const CFG = { CHECK_INTERVAL: 1000, FORCE_REFRESH: 3600 * 1000, CLICK_DEBOUNCE: 3000 };
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
              <span style="font-weight:bold;color:#7eb3ff;">🤖 AFK v20</span>
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
              <div style="display:flex;justify-content:space-between;background:rgba(255,255,255,0.04);padding:5px;border-radius:8px;">
                <span style="color:#aaa;">🔇 防冻状态</span>
                <span id="afk-audio-status" style="color:#ffaa00;">等待</span>
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
                        playLoop(); document.getElementById('afk-audio-status').textContent = '已激活';
                        document.getElementById('afk-audio-status').style.color = '#00cc66';
                        document.removeEventListener('click', activate, true);
                    }).catch(e => console.warn(e));
                }
                if (ctx.state === 'running') { playLoop(); document.getElementById('afk-audio-status').textContent = '已激活'; document.getElementById('afk-audio-status').style.color = '#00cc66'; }
                else { document.addEventListener('click', activate, true); }
            } catch (err) {}
        })();

        function bypassAdblock() {
            if(typeof adblockerDetected !== 'undefined') adblockerDetected = false;
            var msg = document.getElementById('adblocker-message'); if(msg) msg.style.display = 'none';
            var btn = document.getElementById('start-afk-btn'); if(btn) { btn.disabled = false; btn.textContent = 'Start AFK Session'; }
        }

        function tryClick(el) {
            if (Date.now() - lastClickTime < CFG.CLICK_DEBOUNCE) return;
            lastClickTime = Date.now(); el.click();
        }

        function loop() {
            tickCount++;
            const remaining = CFG.FORCE_REFRESH - (Date.now() - startTime);
            bypassAdblock();
            
            if (remaining <= 0) { worker.postMessage('stop'); location.reload(); return; }
            
            const timerEl = document.getElementById('session-timer');
            const timerText = timerEl ? timerEl.innerText.trim() : '--:--';
            document.getElementById('afk-timer').textContent = timerText;
            
            if (timerText === '0:00' || timerText === '00:00') {
                const renewBtn = document.evaluate("//button[contains(.,'Start New Session')]", document, null, 9, null).singleNodeValue;
                if (renewBtn) tryClick(renewBtn);
                else setTimeout(() => location.reload(), 2000);
                return;
            }
            
            const startBtn = document.getElementById('start-afk-btn');
            if (startBtn && startBtn.offsetParent !== null) { tryClick(startBtn); return; }
        }
        worker.onmessage = () => loop(); worker.postMessage('start');
    });
}
"""


# =========================================================================
# 🐾 Python 启动控制大厅
# =========================================================================
def run_earn():
    if not DISCORD_TOKEN:
        log_info("跳过：未配置 FREEZEHOST_DISCORD_TOKEN")
        return

    with sync_playwright() as pw:
        log_info("启动浏览器 (完全使用 WARP 系统级代理，无视本地代理参数)")
        
        # 彻底移除 proxy 注入参数，仅附加静音播放突破限制，100% 贴合原版环境
        browser = pw.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"]
        )
        page = browser.new_page(viewport={"width": 1280, "height": 753})
        page.set_default_timeout(60_000)
        
        # 全局注入 AFK 脚本
        page.add_init_script(AFK_JS_PAYLOAD)

        try:
            # ── 1. 出口 IP 验证 (一字不差的原版) ──────────────────────
            log_info("验证出口 IP...")
            try:
                ip = json.loads(page.goto("https://api.ipify.org?format=json", wait_until="domcontentloaded").text()).get("ip", "?")
                log_info(f"出口 IP: {ip}")
            except Exception:
                log_warn("IP 验证超时")

            # ── 2. 检测站点宕机与首页 (一字不差的原版) ────────────────
            log_info("打开 FreezeHost 登录页")
            if not wait_for_site_ready(page):
                raise RuntimeError("站点宕机，无法连接")

            # ── 3. 登录与 Token 注入 (一字不差的原版) ────────────────
            page.click('span.text-lg:has-text("Login with Discord")', timeout=15000)
            confirm_btn = page.locator("button#confirm-login")
            confirm_btn.wait_for(state="visible")
            confirm_btn.click()
            log_info("已接受服务条款")

            page.wait_for_url(re.compile(r"discord\.com"), timeout=15000)
            log_info("已到达 Discord")

            page.evaluate("""(token) => {
                const f = document.createElement('iframe');
                f.style.display = 'none';
                document.body.appendChild(f);
                f.contentWindow.localStorage.setItem('token', '"'+token+'"');
                try { localStorage.setItem('token', '"'+token+'"'); } catch(e) {}
                document.body.removeChild(f);
            }""", DISCORD_TOKEN)
            log_info("Token 已注入")

            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            if re.search(r"discord\.com/login", page.url):
                raise RuntimeError("Token 登录失败")

            log_info("Token 注入成功")

            # ── 4. 处理 OAuth (一字不差的原版) ───────────────────────
            try:
                page.wait_for_url(re.compile(r"discord\.com/oauth2/authorize"), timeout=6000)
                page.wait_for_timeout(2000)
                if "discord.com" in page.url:
                    handle_oauth_page(page)
                if "discord.com" in page.url:
                    try:
                        page.wait_for_url(re.compile(r"free\.freezehost\.pro"), timeout=20000)
                    except PlaywrightTimeout:
                        raise RuntimeError("OAuth 未跳转")
            except PlaywrightTimeout:
                if "discord.com" in page.url:
                    raise RuntimeError("OAuth 超时")

            # ── 5. 进入 Dashboard 或 Earn ─────────────────────────
            try:
                page.wait_for_url(lambda u: "/callback" in u or "/dashboard" in u or "/earn" in u, timeout=10000)
            except PlaywrightTimeout:
                pass
            if "/callback" in page.url:
                page.wait_for_url(re.compile(r"/dashboard|/earn"), timeout=15000)

            log_info("✅ 登录成功！原版登录方法完美通过！")
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {ACCOUNT_INDEX} 已成功登录！开启极速挂机！")

            # ── 6. 进入挂机战场 ──────────────────────────────────
            page.goto(f"{BASE_URL}/earn", wait_until="networkidle")
            
            global_start = time.time()
            max_runtime_sec = MAX_RUNTIME * 60
            
            log_info("🤖 已激活 Web Worker + 静音防冻双引擎！Python 退居幕后仅负责守卫...")

            # 挂机死循环守护
            while time.time() - global_start < max_runtime_sec:
                try:
                    # 侦测并自动点碎可能意外弹出的 CF 盾牌
                    iframe = page.frame_locator('iframe[src^="https://challenges.cloudflare.com"]').first
                    if iframe:
                        cb = iframe.locator('input[type="checkbox"], .cb-lb')
                        if cb.is_visible(timeout=1000):
                            cb.click()
                            log_info("🛡️ 自动处理并点碎 Turnstile 验证框")
                except: pass
                
                # 检查是否因为掉线脱离了目标页面
                if not page.url.startswith("https://free.freezehost.pro"):
                    log_info("⚠️ URL 偏移，尝试拉回战场...")
                    page.goto(f"{BASE_URL}/earn", wait_until="networkidle")
                
                page.wait_for_timeout(10000)

            log_info(f"挂机任务圆满结束。")
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {ACCOUNT_INDEX} 挂机圆满结束\n⏱️ 共计稳定运行 {MAX_RUNTIME} 分钟！")

        except Exception as e:
            log_error(f"挂机异常崩溃: {e}")
            traceback.print_exc()
        finally:
            browser.close()

if __name__ == "__main__":
    run_earn()
