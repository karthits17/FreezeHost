#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreezeHost 黄金双核流水线 (绝对物理隔离版)
第一阶段: 原汁原味 Playwright 登录+续费 (FreezeHost-main 逻辑)
第二阶段: 原汁原味 SeleniumBase UC 挂机 (freeze-afk 逻辑)
"""

import os
import re
import sys
import json
import time
import base64
import traceback
import platform
from datetime import datetime
from urllib.request import Request, urlopen
from pathlib import Path

# =====================================================================
# 全局环境变量映射
# =====================================================================
if "FREEZEHOST_DISCORD_TOKEN" in os.environ:
    os.environ["DISCORD_TOKEN"] = os.environ["FREEZEHOST_DISCORD_TOKEN"]

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID", "").strip()
INSTANCE_ID   = int(os.environ.get("ACCOUNT_INDEX", os.environ.get("INSTANCE_ID", "1")))
MAX_RUNTIME   = int(os.environ.get("MAX_RUNTIME", "300"))


# =====================================================================
# 🚀 阶段一：纯血 Playwright 续费 (源自 FreezeHost-main/renew.py)
# =====================================================================
def run_renew_phase():
    print("\n" + "="*60)
    print(f"🚀 [阶段一] 启动 Playwright 登录与续费 (账号 {INSTANCE_ID})")
    print("="*60)
    
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

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

    def log_info(msg: str):  print(f"[RENEW] [INFO] {_mask(msg)}", flush=True)
    def log_warn(msg: str):  print(f"[RENEW] [WARN] {_mask(msg)}", flush=True)
    def log_error(msg: str): print(f"[RENEW] [ERROR] {_mask(msg)}", flush=True)

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
        if not TG_CHAT_ID or not TG_BOT_TOKEN: return
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
                req = Request(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto", data=body_parts, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
            else:
                req = Request(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", data=json.dumps({"chat_id": TG_CHAT_ID, "text": caption}).encode(), headers={"Content-Type": "application/json"}, method="POST")
            urlopen(req, timeout=30)
        except Exception as e: log_warn(f"TG 推送异常: {e}")

    def take_screenshot(page, name: str) -> bytes | None:
        try:
            page.set_viewport_size({"width": VIEWPORT_W, "height": VIEWPORT_H})
            page.wait_for_timeout(500)
            path = SCREENSHOT_DIR / f"{name}.png"
            page.screenshot(path=str(path), full_page=False)
            return path.read_bytes()
        except: return None

    def merge_screenshots(browser, buffers: list) -> bytes | None:
        if not buffers: return None
        pg = browser.new_page(viewport={"width": VIEWPORT_W, "height": VIEWPORT_H})
        try:
            imgs = "".join(f'<img src="data:image/png;base64,{base64.b64encode(b).decode()}" style="width:100%;border-radius:8px;border:2px solid #202225;box-shadow:0 4px 6px rgba(0,0,0,.3);" />' for b in buffers)
            pg.set_content(f'<body style="margin:0;padding:15px;background:#2f3136;display:flex;flex-direction:column;gap:15px;">{imgs}</body>')
            pg.wait_for_timeout(500)
            return pg.screenshot(full_page=True)
        except: return None
        finally: pg.close()

    def check_site_down(page) -> bool:
        try:
            return page.evaluate("""() => {
                const body = document.body ? document.body.innerText : '';
                if (body.includes('CONNECTION TO THE MANAGEMENT SERVICES LOST')) return true;
                if (body.includes('Retrying in') && body.includes('Retry Now')) return true;
                if (document.querySelector('button:has-text("Retry Now")')) return true;
                return false;
            }""")
        except: return False

    def wait_for_site_ready(page) -> bool:
        for attempt in range(1, MAX_SITE_RETRIES + 1):
            log_info(f"加载 FreezeHost 首页 (尝试 {attempt}/{MAX_SITE_RETRIES})...")
            try: page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
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
                        retry_btn.click()
                        page.wait_for_timeout(10000)
                        if not check_site_down(page): return True
                except: pass
                if attempt < MAX_SITE_RETRIES: page.wait_for_timeout(RETRY_WAIT)
                continue
            try:
                if page.locator('span.text-lg:has-text("Login with Discord")').is_visible(): return True
            except: pass
            return True
        return False

    def handle_oauth_page(page):
        log_info("进入 OAuth 授权页处理")
        page.wait_for_timeout(2000)
        for _ in range(20):
            if "discord.com" not in page.url: return
            try:
                page.evaluate("""() => {
                    const sels = ['[class*="scroller"]','[class*="oauth2"]','[class*="permissionList"]','[class*="content"] [class*="scroll"]','[class*="listScroller"]'];
                    let scrolled = false;
                    for (const sel of sels) {
                        for (const el of document.querySelectorAll(sel)) {
                            if (el.scrollHeight > el.clientHeight) { el.scrollTop = el.scrollHeight; scrolled = true; }
                        }
                    }
                    if (!scrolled) scrollTo(0, document.body.scrollHeight);
                }""")
            except: pass
            page.wait_for_timeout(800)

        for _ in range(10):
            if "discord.com" not in page.url: return
            for sel in ['button:has-text("Authorize")','button:has-text("授权")','button[type="submit"]']:
                try:
                    btn = page.locator(sel).last
                    if not btn.is_visible(): continue
                    if btn.is_disabled(): continue
                    btn.click()
                    page.wait_for_timeout(2000)
                    if "discord.com" not in page.url: return
                    break
                except: continue
            page.wait_for_timeout(1500)

    def discover_server_ids(page) -> list[str]:
        for attempt in range(3):
            captured = set()
            def on_req(req):
                m = re.search(r"/api/server(?:resources|network|subdomain)\?id=([a-f0-9]+)", req.url, re.I)
                if m: captured.add(m.group(1))
            page.on("request", on_req)
            if attempt == 0:
                log_info("加载 Dashboard 发现服务器...")
                try: page.goto(f"{BASE_URL}/dashboard", wait_until="networkidle", timeout=30000)
                except: page.reload(wait_until="networkidle")
            else:
                log_info(f"第 {attempt+1} 次重试...")
                page.reload(wait_until="networkidle")
            page.wait_for_timeout(5000)
            try: page.remove_listener("request", on_req)
            except: pass
            
            try:
                js_ids = page.evaluate(r"""() => {
                    const ids = [];
                    if (typeof serverData !== 'undefined' && Array.isArray(serverData)) serverData.forEach(s => { if (s.identifier) ids.push(s.identifier); });
                    if (!ids.length) document.querySelectorAll('script:not([src])').forEach(sc => {
                        for (const m of sc.textContent.matchAll(/identifier:\s*["']([a-f0-9]{6,})["']/gi)) ids.push(m[1]);
                    });
                    return ids;
                }""")
            except: js_ids = []
            
            all_ids = set(js_ids or []) | captured
            for sid in sorted(all_ids): _register_sensitive(sid)
            if all_ids:
                log_info(f"发现 {len(all_ids)} 台服务器")
                return sorted(all_ids)
            log_warn(f"第 {attempt+1} 次未发现服务器")
            if attempt < 2: page.wait_for_timeout(3000)
        return []

    def process_server(page, server_id: str) -> dict:
        server_url = f"{BASE_URL}/server-console?id={server_id}"
        result = dict(server_id=server_id, status="unknown", before=None, after=None, emoji="❓", status_label="未知", detail="")
        log_info(f"[{server_id}] 开始处理")
        try:
            page.goto(server_url, wait_until="networkidle")
            page.wait_for_timeout(3000)
            status_text = page.evaluate("() => { const el = document.getElementById('renewal-status-console'); return el ? el.innerText.trim() : null; }")
            total_days = remaining_total_days(status_text)
            result["before"] = parse_remaining(status_text)

            if total_days is not None and total_days > 7:
                log_info(f"[{server_id}] 剩余 {total_days:.1f} 天，无需续期")
                result.update(status="cooldown", emoji="⏳", status_label="冷却期", detail=result["before"])
                return result

            # 🛠️ 唯一修改点：适配新版弹窗点击逻辑
            try:
                renew_btn = page.locator("button:has-text('Renew'), button:has-text('Extend'), button:has-text('연장'), button.bkrtgq").first
                if renew_btn.is_visible(timeout=5000): renew_btn.click()
            except: raise RuntimeError("未找到面板续期按钮")
            
            page.wait_for_timeout(2000)
            try:
                confirm_inst_btn = page.locator("button:has-text('RENEW INSTANCE'), button:has-text('Renew Instance')").first
                if confirm_inst_btn.is_visible(timeout=5000): confirm_inst_btn.click()
            except: pass
            page.wait_for_timeout(2000)

            for _ in range(15):
                try:
                    iframe = page.frame_locator('iframe[src^="https://challenges.cloudflare.com"]').first
                    if iframe:
                        cb = iframe.locator('input[type="checkbox"], .cb-lb')
                        if cb.is_visible(timeout=1000): cb.click()
                except: pass
                if "Cannot Afford Renewal" in page.inner_text("body"):
                    log_warn(f"[{server_id}] 余额不足")
                    result.update(status="broke", emoji="⚠️", status_label="余额不足", detail="金币不够续期")
                    return result
                page.wait_for_timeout(1000)

            page.wait_for_timeout(4000)
            page.evaluate("""() => { document.querySelectorAll('button').forEach(b => {
                if (b.innerText.includes('Next') || b.innerText.includes('닫기') || b.innerText.includes('Close') || b.innerText.includes('Confirm')) b.click();
            }); }""")
            page.wait_for_timeout(2000)

            # 校验天数是否真的增加
            page.goto(server_url, wait_until="networkidle")
            page.wait_for_timeout(3000)
            after_text = page.evaluate("() => { const el = document.getElementById('renewal-status-console'); return el ? el.innerText.trim() : null; }")
            after_days = remaining_total_days(after_text)
            result["after"] = parse_remaining(after_text)
            
            if after_days is not None and total_days is not None and after_days > (total_days + 1):
                log_info(f"[{server_id}] 续期成功！")
                result.update(status="renewed", emoji="✅", status_label="续期成功", detail=f"{result['before'] or '?'} → {result['after'] or '?'}")
            else:
                log_warn(f"[{server_id}] 续期失败！")
                result.update(status="failed", emoji="❌", status_label="续期失败", detail=f"时间未实质增加 ({result['before'] or '?'})")
        except Exception as e:
            log_warn(f"[{server_id}] 异常: {e}")
            result.update(status="error", emoji="❌", status_label="脚本异常", detail=str(e)[:80])
        return result

    # 🚀 严格还原 FreezeHost-main/renew.py 登录机制 (默认 headless=True)
    with sync_playwright() as pw:
        log_info("启动浏览器 (Playwright Headless 原版模式)")
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": VIEWPORT_W, "height": VIEWPORT_H})
        page.set_default_timeout(TIMEOUT)

        try:
            log_info("打开 FreezeHost 登录页")
            if not wait_for_site_ready(page):
                log_error("站点宕机无法连接")
                return

            try:
                page.click('span.text-lg:has-text("Login with Discord")', timeout=15_000)
                page.locator("button#confirm-login").wait_for(state="visible", timeout=5000)
                page.locator("button#confirm-login").click()
                log_info("已接受服务条款")
            except: pass

            try: page.wait_for_url(re.compile(r"discord\.com"), timeout=15000)
            except: pass
            log_info("已到达 Discord, 开始注入 Token...")

            page.evaluate("""(token) => {
                const f = document.createElement('iframe'); f.style.display = 'none'; document.body.appendChild(f);
                f.contentWindow.localStorage.setItem('token', '"'+token+'"');
                try { localStorage.setItem('token', '"'+token+'"'); } catch(e) {}
                document.body.removeChild(f);
            }""", DISCORD_TOKEN)
            
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            if re.search(r"discord\.com/login", page.url):
                raise RuntimeError("Token 登录失败/失效")

            log_info("Token 注入成功")
            try:
                page.wait_for_url(re.compile(r"discord\.com/oauth2/authorize"), timeout=6000)
                page.wait_for_timeout(2000)
                if "discord.com" in page.url: handle_oauth_page(page)
            except: pass

            try: page.wait_for_url(lambda u: "/callback" in u or "/dashboard" in u or "/earn" in u, timeout=20000)
            except: pass
            
            if "/callback" in page.url:
                try: page.wait_for_url(re.compile(r"/dashboard|/earn"), timeout=15000)
                except: pass

            log_info("✅ 登录成功！")

            server_ids = discover_server_ids(page)
            results = []
            if not server_ids:
                log_info("❌ 未发现任何服务器。")
                send_tg(f"🤖 <b>FreezeHost 续费报告</b>\n👤 账号 {INSTANCE_ID}\n⚠️ 未发现任何服务器")
            else:
                for sid in server_ids:
                    log_info("=" * 50)
                    res = process_server(page, sid)
                    results.append(res)

                lines = []
                for r in results:
                    s = f"服务器: {r['server_id']} | {r['emoji']}{r['status_label']}"
                    if r["detail"]: s += f" {r['detail']}"
                    lines.append(s)
                
                if lines:
                    send_tg(f"🤖 <b>FreezeHost 续费报告</b>\n👤 账号 {INSTANCE_ID}\n" + "\n".join(lines))
                log_info("续费探测结束。")

        except Exception as e:
            log_error(f"异常: {e}")
            send_tg(f"🤖 <b>FreezeHost 续费报警</b>\n👤 账号 {INSTANCE_ID}\n❌ 第一阶段崩溃: {e}")
        finally:
            browser.close()


# =====================================================================
# 🚀 阶段二：SeleniumBase 原版挂机赚币 (源自 freeze-afk-main/freeze_afk.py)
# =====================================================================
def run_afk_phase():
    print("\n" + "="*60)
    print(f"🚀 [阶段二] 启动 SeleniumBase UC 挂机赚币 (账号 {INSTANCE_ID})")
    print("="*60)

    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        from pyvirtualdisplay import Display
        disp = Display(visible=False, size=(1920, 1080))
        disp.start()
        os.environ["DISPLAY"] = disp.new_display_var

    from seleniumbase import SB

    SESSION_DURATION = 1200  # 原版写死的 20 分钟循环
    global_start = time.time()

    def log_afk(msg):
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [AFK] [I{INSTANCE_ID}] {msg}", flush=True)

    def wait_turnstile(sb, timeout=120):
        start = time.time()
        last_click = 0
        while time.time() - start < timeout:
            try:
                val = sb.execute_script("return document.querySelector('[name=cf-turnstile-response]')?.value || '';")
                if val and len(str(val)) > 20: return str(val)
            except: pass
            now = time.time()
            if now - last_click > 5:
                try:
                    sb.uc_gui_click_captcha()
                    last_click = now
                except: pass
            time.sleep(2)
        return None

    def login_via_discord_token(sb, token):
        log_afk("Opening FreezeHost...")
        sb.uc_open_with_reconnect("https://free.freezehost.pro", reconnect_time=5)
        time.sleep(5)
        try: sb.click("button#login-btn")
        except: sb.execute_script("document.getElementById('login-btn')?.click();")
        time.sleep(3)
        try:
            sb.wait_for_element_visible("button#confirm-login", timeout=5)
            sb.click("button#confirm-login")
            log_afk("Confirmed terms")
        except: log_afk("No terms dialog")
        time.sleep(2)

        if "discord.com" in sb.get_current_url():
            log_afk("Inject token...")
            sb.execute_script("""(function(){
                var token = "%s";
                var f = document.createElement("iframe"); f.style.display = "none"; document.body.appendChild(f);
                try { f.contentWindow.localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
                try { localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
                document.body.removeChild(f);
            })();""" % token)

            log_afk("Reload...")
            sb.driver.refresh()
            time.sleep(8)

            url = sb.get_current_url()
            if "discord.com/login" in url:
                log_afk("Token invalid!")
                return False

            if "discord.com/oauth2" in url:
                log_afk("Auto-authorize...")
                sb.execute_script("""(function(){
                    document.querySelectorAll("button").forEach(function(btn){
                        if(btn.textContent.toLowerCase().includes("authorize")) btn.click();
                    });
                })();""")
                time.sleep(5)

            for _ in range(20):
                url = sb.get_current_url()
                if url.startswith("https://free.freezehost.pro"): break
                time.sleep(2)

        url = sb.get_current_url()
        log_afk("Login URL: %s" % url)
        return url.startswith("https://free.freezehost.pro")

    def click_start_afk(sb):
        log_afk("Bypassing adblocker...")
        try:
            sb.execute_script("""
                if(typeof adblockerDetected !== 'undefined') adblockerDetected = false;
                var msg = document.getElementById('adblocker-message');
                if(msg) msg.style.display = 'none';
            """)
        except: pass

        # 🛠️ 唯一修改点：兼容长按验证
        try:
            sb.execute_script("""
                var btns = Array.from(document.querySelectorAll('button, a, div'));
                var btn = btns.find(b => b.innerText && (b.innerText.toUpperCase().includes('START EARNING') || b.innerText.toUpperCase().includes('HOLD TO START') || b.innerText.toUpperCase().includes('START AFK')));
                if(btn) {
                    btn.disabled = false;
                    var mdown = new MouseEvent('mousedown', {bubbles: true});
                    var mup = new MouseEvent('mouseup', {bubbles: true});
                    btn.dispatchEvent(mdown);
                    setTimeout(() => { btn.dispatchEvent(mup); btn.click(); }, 1200);
                }
            """)
            log_afk("Triggered Start AFK Event (Hold/Click)!")
            time.sleep(3)
            ws_state = sb.execute_script("return (typeof ws !== 'undefined' && ws) ? ws.readyState : -1;")
            log_afk("WebSocket state: %s" % ws_state)
            return True
        except Exception as e:
            log_afk("Click JS failed: %s" % str(e)[:80])

        for attempt in range(3):
            try:
                sb.wait_for_element_visible("#start-afk-btn", timeout=5)
                sb.click("#start-afk-btn")
                log_afk("Clicked Start AFK via CSS!")
                time.sleep(3)
                return True
            except: time.sleep(3)
        return False

    def run_earn_session(sb, session_num, token):
        log_afk("Loading /earn...")
        sb.uc_open_with_reconnect("https://free.freezehost.pro/earn", reconnect_time=6)
        time.sleep(15)

        url = sb.get_current_url()
        if not url.startswith("https://free.freezehost.pro"):
            log_afk("Session expired, re-login...")
            if not login_via_discord_token(sb, token): return False
            sb.uc_open_with_reconnect("https://free.freezehost.pro/earn", reconnect_time=6)
            time.sleep(15)

        log_afk("Waiting Turnstile...")
        token_val = wait_turnstile(sb, timeout=120)
        if not token_val:
            log_afk("Turnstile failed!")
            return False

        log_afk("Turnstile OK! Token: %s..." % token_val[:30])
        
        if not click_start_afk(sb):
            log_afk("WARNING: Start AFK button click failed!")

        log_afk("Earning for %ds..." % SESSION_DURATION)
        start = time.time()
        while time.time() - start < SESSION_DURATION:
            try:
                url = sb.get_current_url()
                if not url.startswith("https://free.freezehost.pro"):
                    log_afk("Expired during earning")
                    break
            except: break

            if MAX_RUNTIME > 0 and (time.time() - global_start) > MAX_RUNTIME * 60:
                log_afk("Max runtime reached!")
                return None
            time.sleep(30)

        log_afk("Session #%d done" % session_num)
        return True

    # 🚀 纯血 SeleniumBase UC 启动
    sb_options = {
        "uc": True,
        "test": True,
        "headed": True, # 这是必须的，否则无法赚币
        "chromium_arg": "--no-sandbox,--disable-dev-shm-usage,--disable-gpu,--window-size=1280,720",
    }

    with SB(**sb_options) as sb:
        if not login_via_discord_token(sb, DISCORD_TOKEN):
            log_afk("Login failed!")
            return
        log_afk("Login OK!")

        if TG_CHAT_ID and TG_BOT_TOKEN:
            try:
                req = Request(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", data=json.dumps({"chat_id": TG_CHAT_ID, "text": f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {INSTANCE_ID}\n✅ 登录成功，正式开启原版 SeleniumBase 赚币引擎！", "parse_mode": "HTML"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
                urlopen(req, timeout=10)
            except: pass

        session = 0
        while True:
            if MAX_RUNTIME > 0 and (time.time() - global_start) > MAX_RUNTIME * 60:
                log_afk("Max runtime reached!")
                break
            session += 1
            log_afk("")
            log_afk("=== Session #%d ===" % session)

            result = run_earn_session(sb, session, DISCORD_TOKEN)
            if result is None: break
            if not result: log_afk("Session failed, retrying...")
            time.sleep(5)
            
    log_afk("Done!")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print(f"[ERROR] 账号 {INSTANCE_ID} 未配置 DISCORD_TOKEN，跳过运行！")
        sys.exit(0)
        
    try:
        run_renew_phase()
    except Exception as e:
        print(f"续期阶段发生未捕获异常: {e}")
        traceback.print_exc()

    try:
        run_afk_phase()
    except Exception as e:
        print(f"AFK 阶段发生未捕获异常: {e}")
        traceback.print_exc()
