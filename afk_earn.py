#!/usr/bin/env python3

import os
import re
import sys
import json
import time
import traceback
from datetime import datetime
from urllib.request import Request, urlopen
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# 从环境变量读取矩阵分配的 Token
DISCORD_TOKEN = os.environ.get("FREEZEHOST_DISCORD_TOKEN", "").strip()
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID", "").strip()
ACCOUNT_INDEX = os.environ.get("ACCOUNT_INDEX", "1").strip()
MAX_RUNTIME   = int(os.environ.get("MAX_RUNTIME", "300"))  # 默认挂机 300 分钟 (5小时)

BASE_URL   = "https://free.freezehost.pro"

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
    """从 FreezeHost 移植的稳健 OAuth 处理逻辑"""
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

def wait_turnstile(page, timeout=120):
    """适配 Playwright 的 Turnstile 等待逻辑"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # 尝试点击可见的验证框
            iframe = page.frame_locator('iframe[src^="https://challenges.cloudflare.com"]').first
            if iframe:
                cb = iframe.locator('input[type="checkbox"], .cb-lb')
                if cb.is_visible(): cb.click()
        except: pass

        try:
            val = page.evaluate("() => document.querySelector('[name=cf-turnstile-response]')?.value || ''")
            if val and len(str(val)) > 20: return str(val)
        except: pass
        page.wait_for_timeout(2000)
    return None

def login(page):
    """FreezeHost 原版 Discord Token 登录逻辑"""
    log_info("打开 FreezeHost 登录页")
    page.goto(BASE_URL, wait_until="domcontentloaded")
    
    try:
        page.click('span.text-lg:has-text("Login with Discord")', timeout=15000)
        confirm_btn = page.locator("button#confirm-login")
        confirm_btn.wait_for(state="visible", timeout=5000)
        confirm_btn.click()
        log_info("已接受服务条款")
    except: pass

    page.wait_for_url(re.compile(r"discord\.com"), timeout=15000)
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

    log_info("Token 注入成功")

    try:
        page.wait_for_url(re.compile(r"discord\.com/oauth2/authorize"), timeout=6000)
        if "discord.com" in page.url: handle_oauth_page(page)
        if "discord.com" in page.url:
            page.wait_for_url(re.compile(r"free\.freezehost\.pro"), timeout=20000)
    except PlaywrightTimeout: pass

    page.wait_for_url(lambda u: "/callback" in u or "/dashboard" in u or "/earn" in u, timeout=15000)
    log_info("登录流程完成！")
    return True

def start_afk_session(page):
    """原版 AFK 脚本的去广告与开启挂机逻辑"""
    log_info("绕过广告屏蔽检测...")
    try:
        page.evaluate("""() => {
            if(typeof adblockerDetected !== 'undefined') adblockerDetected = false;
            var msg = document.getElementById('adblocker-message');
            if(msg) msg.style.display = 'none';
            var btn = document.getElementById('start-afk-btn');
            if(btn){ btn.disabled = false; btn.textContent = 'Start AFK Session'; }
        }""")
    except: pass

    for attempt in range(3):
        try:
            page.locator("#start-afk-btn").wait_for(state="visible", timeout=5000)
            page.locator("#start-afk-btn").click()
            log_info("已成功点击 Start AFK 按钮！")
            page.wait_for_timeout(3000)
            return True
        except Exception as e:
            try:
                page.evaluate("document.getElementById('start-afk-btn')?.click();")
                log_info("已通过 JS 强制点击 Start AFK 按钮！")
                page.wait_for_timeout(3000)
                return True
            except: pass
    return False

def run_earn():
    if not DISCORD_TOKEN:
        log_info("跳过：未配置 FREEZEHOST_DISCORD_TOKEN")
        return

    with sync_playwright() as pw:
        # 使用原版的极速 Headless 模式
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.set_default_timeout(60000)

        try:
            # 1. 执行验证与登录
            login(page)
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {ACCOUNT_INDEX} 已成功登录，准备开始挂机！")
            
            global_start = time.time()
            max_runtime_sec = MAX_RUNTIME * 60
            session_count = 0

            # 2. 挂机大循环 (每轮 20 分钟)
            while time.time() - global_start < max_runtime_sec:
                session_count += 1
                log_info(f"=== 开始第 {session_count} 轮 AFK Session ===")
                
                page.goto(f"{BASE_URL}/earn", wait_until="networkidle")
                page.wait_for_timeout(5000)
                
                if not page.url.startswith("https://free.freezehost.pro"):
                    log_info("Session 过期，尝试重新登录...")
                    login(page)
                    page.goto(f"{BASE_URL}/earn", wait_until="networkidle")
                    page.wait_for_timeout(5000)

                log_info("等待 Turnstile 验证...")
                token_val = wait_turnstile(page, timeout=120)
                if not token_val:
                    log_info("Turnstile 验证失败或超时，尝试刷新页面...")
                    continue

                log_info(f"Turnstile 验证通过！Token 长度: {len(token_val)}")
                
                # 点击开启挂机
                start_afk_session(page)

                log_info("进入 20 分钟循环挂机状态 (每 60 秒获得 1 币)...")
                session_start = time.time()
                
                while time.time() - session_start < 1200: # 20 分钟
                    if time.time() - global_start >= max_runtime_sec:
                        log_info("已达到最大运行时间设定！")
                        break
                        
                    page.wait_for_timeout(30000) # 每 30 秒检查一次
                    
                    try:
                        if "/earn" not in page.url:
                            log_info("URL 发生偏移，提前结束本轮 Session")
                            break
                    except: break

            log_info(f"挂机任务全部结束。共完成 {session_count} 轮。")
            send_tg(f"🤖 <b>FreezeHost AFK</b>\n👤 账号 {ACCOUNT_INDEX} 挂机结束\n⏱️ 共计运行 {MAX_RUNTIME} 分钟，约收益 {MAX_RUNTIME} 币！")

        except Exception as e:
            log_info(f"挂机异常崩溃: {e}")
            traceback.print_exc()
        finally:
            browser.close()

if __name__ == "__main__":
    run()
