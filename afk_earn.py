#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreezeHost AFK & Renew - 黄金流水线 (Playwright 单核全自动驱动)
严格遵循: 登录 -> 发现服务器 -> 续费(适配最新弹窗UI) -> AFK赚币(强力防卡死纠偏)
"""

import os
import re
import sys
import json
import time
import base64
import traceback
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# =====================================================================
# 全局环境变量共享映射区
# =====================================================================
if "FREEZEHOST_DISCORD_TOKEN" in os.environ:
    os.environ["DISCORD_TOKEN"] = os.environ["FREEZEHOST_DISCORD_TOKEN"]

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID", "").strip()
INSTANCE_ID   = int(os.environ.get("ACCOUNT_INDEX", os.environ.get("INSTANCE_ID", "1")))
MAX_RUNTIME   = int(os.environ.get("MAX_RUNTIME", "300"))

TIMEOUT        = 60_000
MAX_SITE_RETRIES = 3
RETRY_WAIT     = 30_000
SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

BASE_URL   = "https://free.freezehost.pro"
VIEWPORT_W = 1280
VIEWPORT_H = 753

_SENSITIVE_VALUES = set()
_SERVER_INDEX = {}

# =====================================================================
# 工具与日志函数
# =====================================================================
def _register_sensitive(*values):
    for v in values:
        if v and len(v) > 2: _SENSITIVE_VALUES.add(v)

def _server_label(server_id: str) -> str:
    if server_id not in _SERVER_INDEX:
        _SERVER_INDEX[server_id] = len(_SERVER_INDEX) + 1
    return f"服务器#{_SERVER_INDEX[server_id]}"

def _mask(text: str) -> str:
    if DISCORD_TOKEN: text = text.replace(DISCORD_TOKEN, "***")
    if TG_BOT_TOKEN: text = text.replace(TG_BOT_TOKEN, "***")
    if TG_CHAT_ID: text = text.replace(TG_CHAT_ID, "***")
    for val in _SENSITIVE_VALUES:
        if val in text: text = text.replace(val, "***")
    for sid, idx in _SERVER_INDEX.items():
        if sid in text: text = text.replace(sid, f"服务器#{idx}")
    text = re.sub(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.)\d{1,3}\b", r"\1xx", text)
    text = re.sub(r"connect\.sid=[^;\s]+", "connect.sid=***", text)
    return text

def log_info(msg: str):  print(f"[{datetime.now().strftime('%H:%M:%S')}] [账号 {INSTANCE_ID}] [INFO] {_mask(msg)}", flush=True)
def log_warn(msg: str):  print(f"[{datetime.now().strftime('%H:%M:%S')}] [账号 {INSTANCE_ID}] [WARN] {_mask(msg)}", flush=True)
def log_error(msg: str): print(f"[{datetime.now().strftime('%H:%M:%S')}] [账号 {INSTANCE_ID}] [ERROR] {_mask(msg)}", flush=True)

def parse_remaining(text: str) -> str | None:
    if not text: return None
    d = re.search(r"(\d+(?:\.\d+)?)\s*day", text, re.I)
    h = re.search(r"(\d+(?:\.\d+)?)\s*hour", text, re.I)
    days_raw  = float(d.group(1)) if d else 0.0
    hours_raw = float(h.group(1)) if h else 0.0
    extra_hours = (days_raw - int(days_raw)) * 24
    total_hours = hours_raw + extra_hours
    final_days  = int(days_raw)
    final_hours = int(total_hours)
    final_mins  = int(round((total_hours - final_hours) * 60))
    parts = []
    if final_days > 0: parts.append(f"{final_days}天")
    if final_hours > 0 or final_days > 0: parts.append(f"{final_hours}时")
    parts.append(f"{final_mins}分")
    return "".join(parts) if parts else None

def remaining_total_days(text: str) -> float | None:
    if not text: return None
    d = re.search(r"(\d+(?:\.\d+)?)\s*day", text, re.I)
    h = re.search(r"(\d+(?:\.\d+)?)\s*hour", text, re.I)
    days  = float(d.group(1)) if d else 0.0
    hours = float(h.group(1)) if h else 0.0
    return days + hours / 24.0

def send_tg(caption: str, image_bytes: bytes | None = None):
    if not TG_CHAT_ID or not TG_BOT_TOKEN:
        log_warn("TG 未配置，跳过推送")
        return
    try:
        if image_bytes:
            boundary = f"----Boundary{abs(hash(caption))}"
            body_parts = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
                f"{TG_CHAT_ID}\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="caption"\r\n\r\n'
                f"{caption}\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="s.png"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()
            req = Request(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto",
                data=body_parts,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
        else:
            req = Request(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                data=json.dumps({"chat_id": TG_CHAT_ID, "text": caption}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        with urlopen(req, timeout=30) as resp:
            log_info("TG 推送成功" if resp.status == 200 else f"TG 推送失败: HTTP {resp.status}")
    except Exception as e:
        log_warn(f"TG 推送异常: {e}")

def take_screenshot(page, name: str) -> bytes | None:
    try:
        page.set_viewport_size({"width": VIEWPORT_W, "height": VIEWPORT_H})
        page.wait_for_timeout(500)
        path = SCREENSHOT_DIR / f"{name}.png"
        page.screenshot(path=str(path), full_page=False)
        return path.read_bytes()
    except Exception as e:
        log_warn(f"截图失败: {e}")
        return None

def merge_screenshots(browser, buffers: list) -> bytes | None:
    if not buffers: return None
    pg = browser.new_page(viewport={"width": VIEWPORT_W, "height": VIEWPORT_H})
    try:
        imgs = "".join(
            f'<img src="data:image/png;base64,{base64.b64encode(b).decode()}" '
            f'style="width:100%;border-radius:8px;border:2px solid #202225;'
            f'box-shadow:0 4px 6px rgba(0,0,0,.3);" />'
            for b in buffers
        )
        pg.set_content(
            f'<body style="margin:0;padding:15px;background:#2f3136;'
            f'display:flex;flex-direction:column;gap:15px;">{imgs}</body>'
        )
        pg.wait_for_timeout(500)
        return pg.screenshot(full_page=True)
    except Exception as e:
        return None
    finally:
        pg.close()

# =========================================================================
# AFK 挂机防冻引擎注入 (完美适配 START EARNING 与 HOLD TO START)
# =========================================================================
AFK_JS_PAYLOAD = r"""
if (window.top === window.self) {
    window.addEventListener('load', function () {
        if (!window.location.href.includes('/earn') && !window.location.href.includes('/dashboard')) return;
        console.log('[AFKv21] 注入成功，正在接管挂机逻辑');

        const CFG = { CHECK_INTERVAL: 1000, FORCE_REFRESH: 3600 * 1000, CLICK_DEBOUNCE: 6000 };
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

        // 🚀 核心突破：支持普通点击与长按解锁 (1200ms)
        function tryHoldClick(el) {
            if (Date.now() - lastClickTime < CFG.CLICK_DEBOUNCE) return;
            lastClickTime = Date.now(); 
            if(el.disabled) el.disabled = false;
            
            console.log('[AFKv21] 执行物理长按/点击:', el.innerText);
            
            const mousedown = new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window});
            const mouseup = new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window});
            
            el.dispatchEvent(mousedown);
            setTimeout(() => {
                el.dispatchEvent(mouseup);
                el.click();
            }, 1200); // 长按超过1秒，完美突破 HOLD TO START
        }

        function findStartButton() {
            const btns = Array.from(document.querySelectorAll('button, a, div'));
            return btns.find(b => {
                if (!b.innerText) return false;
                const t = b.innerText.toUpperCase();
                return t.includes('START EARNING') || 
                       t.includes('HOLD TO START') || 
                       t.includes('START NEW SESSION') || 
                       t.includes('START AFK');
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

# =========================================================================
# 第一部分：稳健登录 (源自 FreezeHost-main)
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
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
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
    log_info("进入 OAuth 授权页处理...")
    try:
        page.wait_for_timeout(2000)
    except: pass
    
    for _ in range(20):
        if "discord.com" not in page.url: return
        try:
            page.evaluate("""() => {
                document.querySelectorAll('div').forEach(el => {
                    if (el.scrollHeight > el.clientHeight) el.scrollTop = el.scrollHeight;
                });
                scrollTo(0, document.body.scrollHeight);
            }""")
        except Exception as e:
            if "Execution context was destroyed" in str(e) or "Target" in str(e):
                log_info("检测到页面正在自动跳转，中断 OAuth 滚动操作...")
                return
        try:
            page.wait_for_timeout(800)
        except: pass

    for _ in range(10):
        if "discord.com" not in page.url: return
        for sel in ['button:has-text("Authorize")', 'button:has-text("授权")', 'button[type="submit"]']:
            try:
                btn = page.locator(sel).last
                if not btn.is_visible(): continue
                text = btn.inner_text().strip()
                if any(k in text.lower() for k in ("取消","cancel","deny")): continue
                if btn.is_disabled(): continue
                btn.click()
                page.wait_for_timeout(2000)
                if "discord.com" not in page.url: return
                break
            except Exception as e:
                if "Execution context was destroyed" in str(e) or "Target" in str(e):
                    log_info("检测到页面正在自动跳转，中断 OAuth 点击操作...")
                    return
                continue
        try:
            page.wait_for_timeout(1500)
        except: pass

# =========================================================================
# 第二部分：发现服务器并续费 (包含完整新版弹窗处理)
# =========================================================================
def discover_server_ids(page) -> list[str]:
    for attempt in range(3):
        captured: set[str] = set()

        def on_req(req):
            m = re.search(r"/api/server(?:resources|network|subdomain)\?id=([a-f0-9]+)", req.url, re.I)
            if m: captured.add(m.group(1))

        page.on("request", on_req)
        if attempt == 0:
            log_info("加载 Dashboard 发现服务器...")
            try:
                page.goto(f"{BASE_URL}/dashboard", wait_until="networkidle", timeout=30000)
            except Exception as e:
                log_warn(f"跳往Dashboard受阻: {e}，尝试刷新...")
                page.reload(wait_until="networkidle")
        else:
            log_info(f"第 {attempt+1} 次重试发现服务器...")
            page.reload(wait_until="networkidle")

        page.wait_for_timeout(5000)
        page.remove_listener("request", on_req)

        js_ids = page.evaluate(r"""() => {
            const ids = [];
            if (typeof serverData !== 'undefined' && Array.isArray(serverData))
                serverData.forEach(s => { if (s.identifier) ids.push(s.identifier); });
            if (!ids.length) document.querySelectorAll('script:not([src])').forEach(sc => {
                for (const m of sc.textContent.matchAll(/identifier:\s*["']([a-f0-9]{6,})["']/gi))
                    ids.push(m[1]);
            });
            return ids;
        }""")

        all_ids = set(js_ids or []) | (captured if not js_ids else set())
        for sid in sorted(all_ids):
            _server_label(sid)
            _register_sensitive(sid)

        if all_ids:
            log_info(f"发现 {len(all_ids)} 台服务器")
            return sorted(all_ids)

        log_warn(f"第 {attempt+1} 次未发现服务器")
        if attempt < 2:
            page.wait_for_timeout(3000)

    return []

def process_server(page, server_id: str) -> dict:
    tag = _server_label(server_id)
    server_url = f"{BASE_URL}/server-console?id={server_id}"
    result = dict(server_id=server_id, status="unknown", before=None, after=None, emoji="❓", status_label="未知", detail="")

    log_info(f"[{server_id}] 开始处理续期")
    try:
        page.goto(server_url, wait_until="networkidle")
        page.wait_for_timeout(3000)

        status_text = page.evaluate("""() => {
            const el = document.getElementById('renewal-status-console');
            return el ? el.innerText.trim() : null;
        }""")
        log_info(f"[{server_id}] 续期状态: {status_text or '(空)'}")

        remaining_before = parse_remaining(status_text)
        total_days = remaining_total_days(status_text)
        result["before"] = remaining_before

        if total_days is not None and total_days > 7:
            log_info(f"[{server_id}] 剩余 {total_days:.1f} 天，无需续期")
            result.update(status="cooldown", emoji="⏳", status_label="冷却期", detail=remaining_before or f"{total_days:.1f}天")
            return result

        # 🚀 1. 寻找面板主续期按钮
        log_info(f"[{server_id}] 寻找主面板续期入口...")
        try:
            renew_btn = page.locator("button:has-text('Renew'), button:has-text('Extend'), button:has-text('연장'), button.bkrtgq").first
            if renew_btn.is_visible(timeout=5000):
                renew_btn.click()
                log_info(f"[{server_id}] 已点击面板续期，等待 RENEWAL SYSTEM 弹窗...")
            else:
                raise Exception("未找到面板续期按钮")
        except Exception as e:
            raise RuntimeError(f"面板续期按钮定位失败: {e}")

        page.wait_for_timeout(2000)

        # 🚀 2. 寻找 RENEWAL SYSTEM 弹窗中的黄底按钮: RENEW INSTANCE
        try:
            confirm_inst_btn = page.locator("button:has-text('RENEW INSTANCE'), button:has-text('Renew Instance')").first
            if confirm_inst_btn.is_visible(timeout=5000):
                confirm_inst_btn.click()
                log_info(f"[{server_id}] 已确认 RENEW INSTANCE，触发安全验证...")
            else:
                log_warn(f"[{server_id}] 未发现 RENEW INSTANCE 二次确认弹窗，可能直接进入安全验证...")
        except: pass

        page.wait_for_timeout(2000)

        # 🚀 3. 点碎 Security Verification 内的 Turnstile 盾牌
        log_info(f"[{server_id}] 探测安全验证 (Turnstile)...")
        for _ in range(15):
            try:
                iframe = page.frame_locator('iframe[src^="https://challenges.cloudflare.com"]').first
                if iframe:
                    cb = iframe.locator('input[type="checkbox"], .cb-lb')
                    if cb.is_visible(timeout=1000): 
                        cb.click()
                        log_info(f"[{server_id}] 已点击 Turnstile 验证码框")
            except: pass
            
            # 探测是否有钱不够的报错 Toast
            body_text = page.inner_text("body")
            if "Cannot Afford Renewal" in body_text:
                log_warn(f"[{server_id}] 余额不足: Cannot Afford Renewal")
                result.update(status="broke", emoji="⚠️", status_label="余额不足", detail="金币不够续期")
                return result
                
            page.wait_for_timeout(1000)

        page.wait_for_timeout(4000)

        # 🚀 4. 回到控制台强制刷新校验天数
        log_info(f"[{server_id}] 验证流程结束，重新读取剩余天数校验结果...")
        page.goto(server_url, wait_until="networkidle")
        page.wait_for_timeout(3000)
        
        after_text = page.evaluate("""() => {
            const el = document.getElementById('renewal-status-console');
            return el ? el.innerText.trim() : null;
        }""")
        result["after"] = parse_remaining(after_text)
        after_days = remaining_total_days(after_text)
        
        # 严格判断: 天数真的增加了，才算成功！
        if after_days is not None and total_days is not None and after_days > (total_days + 1):
            log_info(f"[{server_id}] 校验通过！天数已增加。")
            result.update(status="renewed", emoji="✅", status_label="续期成功",
                          detail=f"{result['before'] or '?'} → {result['after'] or '?'}")
        else:
            log_warn(f"[{server_id}] 校验失败！天数未增加。可能金币不足或接口异常。")
            result.update(status="failed", emoji="❌", status_label="续期失败",
                          detail=f"时间未实质增加 ({result['before'] or '?'})")

    except Exception as e:
        log_error(f"[{server_id}] 异常: {e}")
        result.update(status="error", emoji="❌", status_label="脚本异常", detail=str(e)[:80])

    return result

# =========================================================================
# 第三部分：主控中心
# =========================================================================
def run_pipeline():
    if not DISCORD_TOKEN:
        log_error("缺少 FREEZEHOST_DISCORD_TOKEN")
        return

    with sync_playwright() as pw:
        log_info("🚀 启动浏览器 (Headed + Stealth 模式)")
        
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-blink-features=AutomationControlled" 
            ]
        )
        context = browser.new_context(
            viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # 抹除特征并注入 AFK 引擎
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        context.add_init_script(AFK_JS_PAYLOAD)
        
        page = context.new_page()
        page.set_default_timeout(60_000)

        try:
            # ── 1. 稳健登录 ──────────────────────
            log_info("打开 FreezeHost 登录页...")
            if not wait_for_site_ready(page):
                raise RuntimeError("站点宕机，无法连接")
            
            try:
                page.click('span.text-lg:has-text("Login with Discord")', timeout=15000)
                page.evaluate("document.querySelector('button#confirm-login')?.click();")
                log_info("已接受服务条款 (通过底层JS点击)")
            except Exception as e:
                log_info(f"点击条款时出现波动: {e}")

            try:
                page.wait_for_url(re.compile(r"discord\.com"), timeout=30000)
                log_info("已到达 Discord, 开始注入 Token...")
            except PlaywrightTimeout:
                log_warn("Discord 跳转迟缓，强制尝试注入...")

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
                page.wait_for_url(re.compile(r"discord\.com/oauth2/authorize"), timeout=10000)
                if "discord.com" in page.url: handle_oauth_page(page)
            except PlaywrightTimeout: pass

            log_info("等待浏览器回调完成...")
            try:
                page.wait_for_url(re.compile(r"/dashboard|/earn"), timeout=45000)
                page.wait_for_load_state("networkidle", timeout=15000) 
            except PlaywrightTimeout:
                log_warn("回调加载超时，如果已在主站范围，则放行。")

            log_info("✅ 登录成功！")

            # ── 2. 发现服务器并续费 ──────────────────────
            server_ids = discover_server_ids(page)
            results, screenshots = [], []
            if not server_ids:
                log_info("❌ 未发现任何服务器，跳过续费步骤。")
            else:
                for sid in server_ids:
                    log_info("=" * 50)
                    res = process_server(page, sid)
                    results.append(res)
                    buf = take_screenshot(page, f"server-{_SERVER_INDEX.get(sid, 0)}")
                    if buf: screenshots.append(buf)

            # ── 合并截图与推送 ──────────────────────────
            final_img = (screenshots[0] if len(screenshots) == 1 else merge_screenshots(browser, screenshots) if screenshots else None)
            lines = []
            for r in results:
                s = f"服务器: {r['server_id']} | {r['emoji']}{r['status_label']}"
                if r["detail"]: s += f" {r['detail']}"
                lines.append(s)
            
            if lines:
                msg = f"🤖 <b>FreezeHost 续费报告</b>\n👤 账号 {INSTANCE_ID}\n" + "\n".join(lines)
                send_tg(msg, final_img)
            log_info("续费探测结束。")

            # ── 3. 进入挂机战场 ──────────────────────────────────
            log_info("🚀 跳转 /earn 页面开启挂机印钞模式！")
            try:
                page.goto(f"{BASE_URL}/earn", wait_until="domcontentloaded")
            except Exception as e:
                log_warn(f"跳转 /earn 遇到波动 ({e})，稍后将自动纠偏...")

            page.wait_for_timeout(5000)
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {INSTANCE_ID}\n✅ 探测完成，正式开启挂机赚币模式！")
            
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
                
                # 🚀 终极掉线防撞车纠偏 (剥离了导致挂掉的 else 分支)
                curr_url = page.url
                if "/earn" not in curr_url:
                    log_info(f"⚠️ URL 发生偏移 (当前: {curr_url})")
                    
                    # 如果掉线跳到了 Discord，自动重新授权
                    if "discord.com" in curr_url:
                        log_info("检测到 Discord 授权，尝试重新走授权流程...")
                        handle_oauth_page(page)
                        try: page.wait_for_url(re.compile(r"/submitlogin|/callback|/dashboard|/earn"), timeout=15000)
                        except: pass
                        curr_url = page.url

                    # 如果停在回调处理页，给它最多 25 秒的缓冲时间
                    if "submitlogin" in curr_url or "/callback" in curr_url or "login" in curr_url:
                        log_info("系统正在处理登录回调，耐心等待跳转...")
                        try:
                            page.wait_for_url(re.compile(r"/dashboard|/earn"), timeout=25000)
                        except:
                            log_warn("后端回调响应超时！")

                    # 无论上面经历了什么，只要最终还没到 /earn，强行拉回去！
                    curr_url = page.url
                    if "/earn" not in curr_url:
                        log_info("强制拉回挂机战场 /earn...")
                        try:
                            page.goto(f"{BASE_URL}/earn", wait_until="domcontentloaded")
                        except Exception as e:
                            log_warn(f"强制跳转 /earn 遇到波动: {e}")
                
                # 播报状态
                if loop_counter % 6 == 0:
                    try:
                        ui_status = page.evaluate("() => document.getElementById('afk-status-title')?.innerText || '等待注入'")
                        ui_timer  = page.evaluate("() => document.getElementById('afk-timer')?.innerText || '--:--'")
                        log_info(f"📊 网页探针回传 | 状态: {ui_status} | 倒计时: {ui_timer}")
                    except: pass
                
                page.wait_for_timeout(10000)

            log_info(f"挂机任务圆满结束。")
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {INSTANCE_ID} 挂机圆满结束\n⏱️ 共计稳定运行 {MAX_RUNTIME} 分钟！")

        except Exception as e:
            log_error(f"❌ 全局异常崩溃: {e}")
            traceback.print_exc()
        finally:
            context.close()
            browser.close()

if __name__ == "__main__":
    run_pipeline()
