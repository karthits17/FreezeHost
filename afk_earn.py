#!/usr/bin/env python3

import os
import re
import json
import time
import traceback
from datetime import datetime
from urllib.request import Request, urlopen
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# 从环境变量读取
DISCORD_TOKEN = os.environ.get("FREEZEHOST_DISCORD_TOKEN", "").strip()
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID", "").strip()
ACCOUNT_INDEX = os.environ.get("ACCOUNT_INDEX", "1").strip()
MAX_RUNTIME   = int(os.environ.get("MAX_RUNTIME", "300"))  # 默认挂机 300 分钟

BASE_URL = "https://free.freezehost.pro"

def log_info(msg: str):
    """带时间戳和账号标识的日志"""
    ts = datetime.now().strftime("%H:%M:%S")
    safe_msg = msg.replace(DISCORD_TOKEN, "***") if DISCORD_TOKEN else msg
    print(f"[{ts}] [账号 {ACCOUNT_INDEX}] {safe_msg}", flush=True)

def send_tg(text: str):
    """发送 TG 通知"""
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

def handle_oauth_page(page):
    """稳健 OAuth 处理逻辑"""
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
        for sel in ['button:has-text("Authorize")', 'button:has-text("授权")', 'button[type="submit"]']:
            try:
                btn = page.locator(sel).last
                if not btn.is_visible(): continue
                text = btn.inner_text().strip()
                if any(k in text.lower() for k in ("取消", "cancel", "deny")): continue
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

def login(page):
    """Discord Token 登录逻辑"""
    log_info("打开 FreezeHost 登录页")
    page.goto(BASE_URL, wait_until="domcontentloaded")
    
    try:
        page.click('span.text-lg:has-text("Login with Discord")', timeout=15000)
        confirm_btn = page.locator("button#confirm-login")
        confirm_btn.wait_for(state="visible", timeout=5000)
        confirm_btn.click()
        log_info("已接受服务条款")
    except: pass

    page.wait_for_url(re.compile(r"discord\.com"), timeout=20000)
    log_info("已到达 Discord")

    page.evaluate("""(token) => {
        const f = document.createElement('iframe'); f.style.display = 'none'; document.body.appendChild(f);
        try { f.contentWindow.localStorage.setItem('token', '"'+token+'"'); } catch(e) {}
        try { localStorage.setItem('token', '"'+token+'"'); } catch(e) {}
        document.body.removeChild(f);
    }""", DISCORD_TOKEN)
    log_info("Token 已注入")

    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    
    if re.search(r"discord\.com/login", page.url):
        raise RuntimeError("Token 登录失败，请检查 Token 是否有效")

    log_info("Token 注入成功，等待 OAuth 回调...")

    try:
        page.wait_for_url(re.compile(r"discord\.com/oauth2/authorize"), timeout=10000)
        if "discord.com" in page.url: handle_oauth_page(page)
        if "discord.com" in page.url:
            page.wait_for_url(re.compile(r"free\.freezehost\.pro"), timeout=30000)
    except PlaywrightTimeout: pass

    # 🐾 修复点：大幅增加超时时间，并加入柔性判断
    try:
        page.wait_for_url(lambda u: "/callback" in u or "/dashboard" in u or "/earn" in u, timeout=45000)
    except PlaywrightTimeout:
        if "free.freezehost.pro" in page.url:
            log_info("⚠️ URL 精准匹配超时，但已处于主站，放行进入挂机模块")
        else:
            raise RuntimeError(f"登录回调跳转严重超时，当前 URL: {page.url}")

    log_info("登录流程完成！")
    return True


# =========================================================================
# 🐾 史诗级融合：将油猴防冻脚本转换为全局注入脚本
# =========================================================================
AFK_JS_PAYLOAD = r"""
if (window.top === window.self) {
    window.addEventListener('load', function () {
        if (!window.location.href.includes('/earn')) return;
        
        console.log('[AFKv20] 注入成功，正在接管挂机逻辑');

        const CFG = {
            CHECK_INTERVAL: 1000,
            FORCE_REFRESH:  3600 * 1000,
            CLICK_DEBOUNCE: 3000,
        };

        const workerCode = `
            let iv = null;
            self.onmessage = function(e) {
                if (e.data === 'start') { if (!iv) iv = setInterval(() => self.postMessage('tick'), 1000); }
                else if (e.data === 'stop') { clearInterval(iv); iv = null; }
            };
        `;
        const worker = new Worker(URL.createObjectURL(new Blob([workerCode], { type: 'application/javascript' })));

        const startTime = Date.now();
        let lastClickTime = 0;
        let tickCount = 0;

        const panel = document.createElement('div');
        panel.id = 'afk-panel';
        panel.style.cssText = [
            'position:fixed','bottom:20px','right:20px','z-index:2147483647',
            'width:280px',
            'background:linear-gradient(145deg,#0f0f1a,#1a1a2e)',
            'border:1px solid rgba(100,100,255,0.3)',
            'border-radius:14px',
            'box-shadow:0 8px 32px rgba(0,0,0,0.6),0 0 0 1px rgba(255,255,255,0.05)',
            "font-family:'Consolas','Monaco',monospace",
            'font-size:12px','color:#e0e0e0','overflow:hidden','user-select:none',
        ].join(';');

        panel.innerHTML = [
            '<div id="afk-header" style="background:rgba(255,255,255,0.05);padding:10px 14px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,0.07);cursor:move;">',
            '  <span style="font-size:13px;font-weight:bold;color:#7eb3ff;">🤖 FreezeHost AFK v20</span>',
            '  <span id="afk-uptime" style="font-size:11px;background:rgba(255,255,255,0.08);padding:2px 8px;border-radius:10px;color:#aaa;">0分0秒</span>',
            '</div>',
            '<div style="padding:12px 14px;display:flex;flex-direction:column;gap:8px;">',
            '  <div id="afk-status-row" style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:rgba(255,255,255,0.04);border-radius:8px;border-left:3px solid #888;">',
            '    <span id="afk-dot" style="width:8px;height:8px;border-radius:50%;background:#888;flex-shrink:0;box-shadow:0 0 6px #888;"></span>',
            '    <div style="flex:1;min-width:0;">',
            '      <div id="afk-status-title" style="font-weight:bold;font-size:12px;color:#fff;">初始化中...</div>',
            '      <div id="afk-status-detail" style="font-size:11px;color:#999;margin-top:1px;">加载页面元素...</div>',
            '    </div>',
            '  </div>',
            '  <div style="display:flex;gap:8px;">',
            '    <div style="flex:1;padding:7px 10px;background:rgba(255,255,255,0.04);border-radius:8px;text-align:center;">',
            '      <div style="color:#aaa;font-size:10px;margin-bottom:2px;">SESSION 倒计时</div>',
            '      <div id="afk-timer" style="color:#7eb3ff;font-size:14px;font-weight:bold;">--:--</div>',
            '    </div>',
            '    <div style="flex:1;padding:7px 10px;background:rgba(255,255,255,0.04);border-radius:8px;text-align:center;">',
            '      <div style="color:#aaa;font-size:10px;margin-bottom:2px;">刷新倒计时</div>',
            '      <div id="afk-refresh" style="color:#ffd700;font-size:14px;font-weight:bold;">--:--</div>',
            '    </div>',
            '  </div>',
            '  <div style="padding:7px 10px;background:rgba(255,255,255,0.04);border-radius:8px;display:flex;justify-content:space-between;align-items:center;">',
            '    <span style="color:#aaa;font-size:11px;">🖥️ 标签页</span>',
            '    <span id="afk-bg-status" style="font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(0,200,100,0.15);color:#00cc66;">前台运行</span>',
            '  </div>',
            '  <div style="padding:7px 10px;background:rgba(255,255,255,0.04);border-radius:8px;display:flex;justify-content:space-between;">',
            '    <span style="color:#aaa;font-size:11px;">⚡ Worker 心跳</span>',
            '    <span id="afk-tick" style="color:#7eb3ff;font-size:11px;">0 次</span>',
            '  </div>',
            '  <div style="padding:7px 10px;background:rgba(255,255,255,0.04);border-radius:8px;display:flex;justify-content:space-between;align-items:center;">',
            '    <span style="color:#aaa;font-size:11px;">🔇 静音防冻</span>',
            '    <span id="afk-audio-status" style="font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(255,170,0,0.15);color:#ffaa00;">等待激活</span>',
            '  </div>',
            '</div>',
        ].join('');

        document.body.appendChild(panel);

        (function startSilentAudio() {
            try {
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (!AudioCtx) throw new Error('不支持 AudioContext');
                const ctx    = new AudioCtx();
                const buffer = ctx.createBuffer(1, ctx.sampleRate * 0.5, ctx.sampleRate);
                const gain   = ctx.createGain();
                gain.gain.value = 0;
                gain.connect(ctx.destination);

                function playLoop() {
                    const src = ctx.createBufferSource();
                    src.buffer  = buffer;
                    src.connect(gain);
                    src.onended = playLoop;
                    src.start();
                }

                function setUI(active) {
                    const el = document.getElementById('afk-audio-status');
                    if (!el) return;
                    el.textContent       = active ? '静音音频已激活' : '等待激活';
                    el.style.color       = active ? '#00cc66' : '#ffaa00';
                    el.style.background  = active ? 'rgba(0,200,100,0.15)' : 'rgba(255,170,0,0.15)';
                }

                function activate() {
                    ctx.resume().then(() => {
                        playLoop(); setUI(true);
                        document.removeEventListener('click',   activate, true);
                        document.removeEventListener('keydown', activate, true);
                    }).catch(err => console.warn('[AFKv20] resume 失败:', err));
                }

                if (ctx.state === 'running') {
                    playLoop(); setUI(true);
                } else {
                    document.addEventListener('click',   activate, true);
                    document.addEventListener('keydown', activate, true);
                }
            } catch (err) {
                console.warn('[AFKv20] 静音音频不可用:', err);
            }
        })();

        function formatMs(ms) {
            if (ms <= 0) return '0分0秒';
            const s = Math.floor(ms / 1000);
            return Math.floor(s / 60) + '分' + (s % 60) + '秒';
        }
        function pad2(n) { return String(n).padStart(2, '0'); }
        function formatCountdown(ms) {
            if (ms <= 0) return '00:00';
            const s = Math.floor(ms / 1000);
            return pad2(Math.floor(s / 60)) + ':' + pad2(s % 60);
        }

        const STATUS = {
            running: { color:'#00ccff', title:'💰 稳定挂机中'    },
            renew:   { color:'#ff66ff', title:'🔄 Session 续期'  },
            start:   { color:'#00ff88', title:'🚀 准备重连'},
            stuck:   { color:'#ff4444', title:'⚠️ 倒计时卡死'    },
            refresh: { color:'#ff8800', title:'♻️ 定时强制刷新'  },
            loading: { color:'#888888', title:'⏳ 页面加载中'          },
        };

        function updateUI(key, detail, timerText) {
            const s = STATUS[key] || STATUS.loading;
            const row = document.getElementById('afk-status-row');
            const dot = document.getElementById('afk-dot');
            row.style.borderLeftColor = s.color;
            dot.style.background  = s.color;
            dot.style.boxShadow   = '0 0 6px ' + s.color;
            document.getElementById('afk-status-title').textContent  = s.title;
            document.getElementById('afk-status-detail').textContent = detail;
            document.getElementById('afk-timer').textContent  = timerText || '--:--';
            document.getElementById('afk-tick').textContent   = tickCount + ' 次';
            const elapsed   = Date.now() - startTime;
            const remaining = CFG.FORCE_REFRESH - elapsed;
            document.getElementById('afk-uptime').textContent  = formatMs(elapsed);
            document.getElementById('afk-refresh').textContent = formatCountdown(remaining);
            const bgEl  = document.getElementById('afk-bg-status');
            const hidden = document.hidden;
            bgEl.textContent      = hidden ? '后台运行' : '前台运行';
            bgEl.style.color      = hidden ? '#8888ff' : '#00cc66';
            bgEl.style.background = hidden ? 'rgba(100,100,255,0.15)' : 'rgba(0,200,100,0.15)';
        }

        function tryClick(el, name) {
            if (Date.now() - lastClickTime < CFG.CLICK_DEBOUNCE) return false;
            lastClickTime = Date.now();
            const opts = { bubbles:true, cancelable:true, view:window };
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.click();
            return true;
        }

        function getRenewBtn() {
            return document.evaluate(
                "//button[contains(.,'Start New Session')]",
                document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
            ).singleNodeValue;
        }

        // 强行干掉防广告弹窗
        function bypassAdblock() {
            if(typeof adblockerDetected !== 'undefined') adblockerDetected = false;
            var msg = document.getElementById('adblocker-message');
            if(msg) msg.style.display = 'none';
            var btn = document.getElementById('start-afk-btn');
            if(btn) { btn.disabled = false; btn.textContent = 'Start AFK Session'; }
        }

        function loop() {
            tickCount++;
            const remaining = CFG.FORCE_REFRESH - (Date.now() - startTime);

            bypassAdblock(); // 每秒检查去广告

            if (remaining <= 0) {
                updateUI('refresh', '运行满1小时，执行刷新...', '--:--');
                worker.postMessage('stop');
                setTimeout(() => location.reload(), 500);
                return;
            }

            const bodyText  = document.body.innerText;
            const startBtn  = document.getElementById('start-afk-btn');
            const timerEl   = document.getElementById('session-timer');
            const timerText = timerEl ? timerEl.innerText.trim() : '';

            if (timerText === '0:00' || timerText === '00:00') {
                updateUI('stuck', '检测到 0:00 卡死，尝试续期...', timerText);
                const btn = getRenewBtn();
                if (btn) tryClick(btn, 'Start New Session (stuck)');
                else setTimeout(() => location.reload(), 2000);
                return;
            }

            const renewBtn = getRenewBtn();
            if (renewBtn && renewBtn.offsetParent !== null) {
                updateUI('renew', 'Session 结束，点击续期', timerText);
                tryClick(renewBtn, 'Start New Session');
                return;
            }

            if (bodyText.includes('You are now earning coins') || bodyText.includes('1 coin will be added')) {
                updateUI('running', '正在获取金币，倒计时: ' + timerText, timerText);
                return;
            }

            if (startBtn) {
                updateUI('start', '检测到空闲，点击开始', timerText);
                tryClick(startBtn, 'Start AFK Session');
                return;
            }

            updateUI('loading', '等待元素加载...', timerText);
        }

        worker.onmessage = () => loop();
        worker.postMessage('start');
        loop();
    });
}
"""

def run_earn():
    if not DISCORD_TOKEN:
        log_info("跳过：未配置 FREEZEHOST_DISCORD_TOKEN")
        return

    proxy_arg = []
    proxy_address = os.getenv('PROXY', '127.0.0.1:10808')
    if proxy_address:
        proxy_url = proxy_address if "://" in proxy_address else f"socks5://{proxy_address}"
        proxy_arg = [f'--proxy-server={proxy_url}']

    with sync_playwright() as pw:
        # 🐾 核心：强行破除音频权限，让 JS 里的静音防冻直接无感运行！
        args = [
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--autoplay-policy=no-user-gesture-required" 
        ] + proxy_arg

        browser = pw.chromium.launch(headless=True, args=args)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.set_default_timeout(60000)
        
        # 🐾 核心：永久注入防冻神级脚本，即使刷新也不会丢失
        page.add_init_script(AFK_JS_PAYLOAD)

        try:
            # 1. 登录验证
            login(page)
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {ACCOUNT_INDEX} 已成功登录，启动防冻挂机引擎！")
            
            # 2. 进入 Earn 页面，JS脚本会自动接管点击和循环
            page.goto(f"{BASE_URL}/earn", wait_until="networkidle")
            
            global_start = time.time()
            max_runtime_sec = MAX_RUNTIME * 60
            
            log_info("已激活 Web Worker + 静音防冻 JS！Python 退居幕后仅负责监控...")

            # 3. Python 化身保安：仅负责驱逐 Turnstile 盾牌和看时间
            while time.time() - global_start < max_runtime_sec:
                try:
                    # 侦测是否突然弹出了盾牌（比如 JS 定时刷新后）
                    iframe = page.frame_locator('iframe[src^="https://challenges.cloudflare.com"]').first
                    if iframe:
                        cb = iframe.locator('input[type="checkbox"], .cb-lb')
                        if cb.is_visible(timeout=1000):
                            cb.click()
                            log_info("🛡️ 自动处理并点碎 Turnstile 验证框")
                except:
                    pass
                
                # 防跑飞机制
                if not page.url.startswith("https://free.freezehost.pro"):
                    log_info("⚠️ URL 被意外重定向，尝试拉回...")
                    page.goto(f"{BASE_URL}/earn", wait_until="networkidle")
                
                # 睡 10 秒看一次，把所有繁重的点击工作都交给 JS 脚本！
                page.wait_for_timeout(10000)

            log_info(f"挂机任务圆满结束。")
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {ACCOUNT_INDEX} 挂机圆满结束\n⏱️ 共计稳定运行 {MAX_RUNTIME} 分钟！")

        except Exception as e:
            log_info(f"挂机异常崩溃: {e}")
            traceback.print_exc()
        finally:
            browser.close()

if __name__ == "__main__":
    run_earn()
