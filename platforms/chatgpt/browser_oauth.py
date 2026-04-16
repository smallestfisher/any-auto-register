"""ChatGPT OAuth 浏览器流程。"""
import re
import time

from curl_cffi import requests as curl_requests

from core.base_sms import create_phone_callbacks
from core.oauth_browser import (
    OAuthBrowser,
    browser_login_method_text,
    finalize_oauth_email,
    oauth_provider_label,
)
from platforms.chatgpt.oauth import OAuthManager


def _build_proxies(proxy: str | None) -> dict | None:
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _fetch_profile(access_token: str, proxy: str | None = None) -> dict:
    if not access_token:
        return {}
    try:
        response = curl_requests.get(
            "https://chatgpt.com/backend-api/me",
            headers={
                "authorization": f"Bearer {access_token}",
                "accept": "application/json",
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            },
            proxies=_build_proxies(proxy),
            timeout=20,
            impersonate="chrome124",
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        return {}
    return {}


def _find_visible(page, selectors: list[str]):
    for selector in selectors:
        try:
            node = page.query_selector(selector)
        except Exception:
            node = None
        if not node:
            continue
        try:
            if node.is_visible():
                return selector, node
        except Exception:
            continue
    return None, None


def _click_continue(page) -> bool:
    selectors = [
        'button[type="submit"]',
        'button:has-text("Continue")',
        'button:has-text("Next")',
        'button:has-text("Verify")',
        'button:has-text("Submit")',
    ]
    selector, node = _find_visible(page, selectors)
    if node:
        try:
            node.click()
            return True
        except Exception:
            pass
    try:
        page.keyboard.press("Enter")
        return True
    except Exception:
        return False


def _fill_sms_code(page, code: str) -> bool:
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not digits:
        return False
    grouped_selector, grouped_node = _find_visible(page, [
        'input[autocomplete="one-time-code"]',
        'input[inputmode="numeric"]',
        'input[name*="code"]',
    ])
    if grouped_node:
        try:
            grouped_node.click()
            grouped_node.fill(digits)
            return True
        except Exception:
            pass
    single_nodes = []
    try:
        single_nodes = page.query_selector_all('input[maxlength="1"]')
    except Exception:
        single_nodes = []
    if single_nodes and len(single_nodes) >= len(digits):
        for idx, digit in enumerate(digits):
            try:
                single_nodes[idx].click()
                single_nodes[idx].fill(digit)
            except Exception:
                try:
                    page.keyboard.press(digit)
                except Exception:
                    return False
        return True
    try:
        for digit in digits:
            page.keyboard.press(digit)
            time.sleep(0.05)
        return True
    except Exception:
        return False


def _handle_phone_verification(browser: OAuthBrowser, *, proxy: str | None, extra: dict | None, log_fn=print) -> bool:
    config = dict(extra or {})
    provider_key = str(config.get("sms_provider") or ("sms_activate" if config.get("sms_activate_api_key") else "")).strip()
    if not provider_key:
        raise RuntimeError("OAuth 流程遇到手机号验证，但未配置接码服务。请提供 sms_activate_api_key。")

    phone_callback, cleanup = create_phone_callbacks(
        provider_key,
        config,
        service="chatgpt",
        country=str(config.get("sms_country") or config.get("sms_activate_country") or "").strip(),
        log_fn=log_fn,
    )

    try:
        deadline = time.time() + 45
        phone_page = None
        tel_node = None
        while time.time() < deadline:
            for page in browser.pages():
                current_url = (page.url or "").lower()
                selector, node = _find_visible(page, ['input[type="tel"]', 'input[autocomplete="tel"]', 'input[inputmode="tel"]'])
                if node or "add-phone" in current_url:
                    phone_page = page
                    tel_node = node
                    break
            if phone_page:
                break
            time.sleep(0.5)
        if not phone_page:
            raise RuntimeError("未找到手机号验证页面")
        if tel_node is None:
            _, tel_node = _find_visible(phone_page, ['input[type="tel"]', 'input[autocomplete="tel"]', 'input[inputmode="tel"]'])
        if tel_node is None:
            raise RuntimeError("未找到手机号输入框")

        phone_number = phone_callback()
        if not phone_number:
            raise RuntimeError("接码服务未返回手机号")
        masked = f"{str(phone_number)[:4]}****"
        log_fn(f"检测到 ChatGPT OAuth 手机验证，填写手机号: {masked}")
        tel_node.click()
        try:
            tel_node.fill("")
        except Exception:
            pass
        tel_node.fill(str(phone_number).strip())
        time.sleep(0.5)
        _click_continue(phone_page)

        otp_deadline = time.time() + 60
        otp_ready = False
        while time.time() < otp_deadline:
            _, otp_node = _find_visible(phone_page, [
                'input[autocomplete="one-time-code"]',
                'input[inputmode="numeric"]',
                'input[maxlength="1"]',
                'input[name*="code"]',
            ])
            if otp_node:
                otp_ready = True
                break
            time.sleep(0.5)
        if not otp_ready:
            raise RuntimeError("未出现短信验证码输入框")

        sms_code = phone_callback()
        if not sms_code:
            raise RuntimeError("未获取到短信验证码")
        log_fn("收到手机验证码，开始填写")
        if not _fill_sms_code(phone_page, sms_code):
            raise RuntimeError("填写短信验证码失败")
        time.sleep(0.5)
        _click_continue(phone_page)
        time.sleep(2)
        return True
    finally:
        cleanup()


def register_with_browser_oauth(
    *,
    proxy: str | None = None,
    oauth_provider: str = "",
    email_hint: str = "",
    timeout: int = 300,
    log_fn=print,
    headless: bool = False,
    chrome_user_data_dir: str = "",
    chrome_cdp_url: str = "",
    extra: dict | None = None,
) -> dict:
    method_text = browser_login_method_text(oauth_provider)
    manager = OAuthManager(proxy_url=proxy)
    oauth_start = manager.start_oauth()

    with OAuthBrowser(
        proxy=proxy,
        headless=headless,
        chrome_user_data_dir=chrome_user_data_dir,
        chrome_cdp_url=chrome_cdp_url,
        log_fn=log_fn,
    ) as browser:
        browser.goto(oauth_start.auth_url)
        time.sleep(2)
        if oauth_provider:
            browser.try_click_provider(oauth_provider)

        if chrome_user_data_dir or chrome_cdp_url:
            browser.auto_select_google_account()
        else:
            log_fn(f"请在浏览器中完成登录/授权，可使用 {method_text}，最长等待 {timeout} 秒")
            if email_hint:
                log_fn(f"请确认最终登录账号邮箱为: {email_hint}")

        deadline = time.time() + timeout
        callback_url = ""
        phone_handled = False
        while time.time() < deadline:
            for page in browser.pages():
                current_url = (page.url or "").strip()
                if current_url.startswith(oauth_start.redirect_uri) and "code=" in current_url:
                    callback_url = current_url
                    break
            if callback_url:
                break

            if not phone_handled:
                need_phone = False
                for page in browser.pages():
                    current_url = (page.url or "").lower()
                    if "add-phone" in current_url:
                        need_phone = True
                        break
                    _, tel_node = _find_visible(page, ['input[type="tel"]', 'input[autocomplete="tel"]', 'input[inputmode="tel"]'])
                    if tel_node:
                        need_phone = True
                        break
                if need_phone:
                    _handle_phone_verification(browser, proxy=proxy, extra=extra, log_fn=log_fn)
                    phone_handled = True
                    continue

            time.sleep(1)

        if not callback_url:
            raise RuntimeError(f"ChatGPT 浏览器登录未在 {timeout} 秒内完成")

        token_info = manager.handle_callback(
            callback_url=callback_url,
            expected_state=oauth_start.state,
            code_verifier=oauth_start.code_verifier,
        )
        time.sleep(2)
        profile = _fetch_profile(token_info.get("access_token", ""), proxy=proxy)
        resolved_email = finalize_oauth_email(
            token_info.get("email") or profile.get("email", ""),
            email_hint,
            "ChatGPT",
        )
        return {
            "email": resolved_email,
            "account_id": token_info.get("account_id", ""),
            "access_token": token_info.get("access_token", ""),
            "refresh_token": token_info.get("refresh_token", ""),
            "id_token": token_info.get("id_token", ""),
            "session_token": browser.cookie_value(
                "__Secure-next-auth.session-token",
                domain_substrings=("chatgpt.com", "openai.com"),
            ),
            "cookies": browser.cookie_header(domain_substrings=("chatgpt.com", "openai.com")),
            "workspace_id": "",
            "profile": profile,
        }


# Backward-compat alias
register_with_manual_oauth = register_with_browser_oauth
