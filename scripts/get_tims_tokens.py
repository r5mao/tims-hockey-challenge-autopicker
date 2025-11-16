#!/usr/bin/env python3
"""
Playwright helper to capture Tim Hortons auth tokens and write a `.env` file.

Usage:
  1. pip install playwright
  2. python -m playwright install
  3. python scripts/get_tims_tokens.py

This opens a visible browser. Sign in on the opened page (enter email, paste OTP when you get it).
When sign-in finishes, return to the terminal and press Enter. The script listens for
Cognito network responses and extracts `REFRESH_TOKEN`, `CLIENT_ID`, `USER_ID`, and `USER_AGENT`.

Notes:
- The script runs headful so you can interact with the real login UI (OTP entry).
- It does not store Access/Id tokens long-term; it writes `REFRESH_TOKEN`, `CLIENT_ID`,
  `USER_AGENT`, and `USER_ID` to a `.env` file in repo root.
"""
import json
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright


def extract_tokens_from_response(response, tokens: dict) -> None:
    """Parse an intercepted network response and add any discovered tokens to `tokens`."""
    try:
        url = response.url
        # Cognito flow: look for AuthenticationResult with RefreshToken
        if 'cognito-idp.us-east-1.amazonaws.com' in url:
            try:
                body = response.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                ar = body.get('AuthenticationResult')
                if isinstance(ar, dict) and 'RefreshToken' in ar and not tokens.get('REFRESH_TOKEN'):
                    tokens['REFRESH_TOKEN'] = ar['RefreshToken']
        # Check request payload for ClientId
        try:
            req = response.request
            post = req.post_data
            if post and 'ClientId' in post and not tokens.get('CLIENT_ID'):
                try:
                    pdata = json.loads(post)
                    if 'ClientId' in pdata:
                        tokens['CLIENT_ID'] = pdata['ClientId']
                except Exception:
                    if 'ClientId' in post:
                        idx = post.find('ClientId')
                        tokens['CLIENT_ID'] = post[idx:idx+200]
        except Exception:
            pass

        # Tim Hortons GraphQL endpoint contains `thLegacyCognitoId` under data->me->thLegacyCognitoId
        if 'use1-prod-th-gateway.rbictg.com/graphql' in url and not tokens.get('USER_ID'):
            try:
                body = response.json()
                if isinstance(body, dict):
                    data = body.get('data')
                    if isinstance(data, dict):
                        me = data.get('me')
                        if isinstance(me, dict) and 'thLegacyCognitoId' in me:
                            tokens['USER_ID'] = me.get('thLegacyCognitoId')
            except Exception:
                pass
    except Exception:
        # swallow individual response parsing errors to avoid crashing the script
        pass


def sniff_client_id_from_scripts(page, tokens: dict) -> None:
    """Try to find a `ClientId` literal inside inline scripts on the page."""
    if tokens.get('CLIENT_ID'):
        return
    try:
        scripts = page.query_selector_all('script')
        for s in scripts:
            src = s.get_attribute('src')
            if not src:
                txt = s.inner_text()
                if 'ClientId' in txt:
                    idx = txt.find('ClientId')
                    snippet = txt[idx: idx + 200]
                    tokens['CLIENT_ID'] = snippet
                    break
    except Exception:
        pass


def get_user_id_from_localstorage(page, tokens: dict) -> None:
    """Read `thLegacyCognitoId` (or similar) from localStorage as a fallback."""
    if tokens.get('USER_ID'):
        return
    try:
        keys = page.evaluate('Object.keys(window.localStorage)')
        uid = None
        for k in keys:
            if 'thLegacyCognitoId' in k or 'thLegacy' in k or 'cognito' in k.lower():
                uid = page.evaluate(f"window.localStorage.getItem('{k}')")
                break
        if uid is None:
            uid = page.evaluate("window.localStorage.getItem('thLegacyCognitoId')")
        if uid:
            tokens['USER_ID'] = uid
    except Exception:
        pass


def write_env_file(path: Path, tokens: dict) -> None:
    """Write the captured tokens to `.env` (overwrites existing file)."""
    env_lines = []
    env_lines.append(f"USER_AGENT={tokens.get('USER_AGENT','')}")
    env_lines.append(f"CLIENT_ID={tokens.get('CLIENT_ID','')}")
    env_lines.append(f"REFRESH_TOKEN={tokens.get('REFRESH_TOKEN','')}")
    env_lines.append(f"USER_ID={tokens.get('USER_ID','')}")
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(env_lines))


def capture_tokens_with_playwright(env_path: Path) -> dict:
    """Orchestrate Playwright browser session to capture Tim Hortons tokens and return them."""
    tokens = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        # wire response handler to call extract_tokens_from_response with our tokens dict
        page.on('response', lambda response: extract_tokens_from_response(response, tokens))

        page.goto('https://www.timhortons.ca/account')
        input('After completing sign-in in the browser window, press Enter here to continue...')

        # Give network handlers a moment to process any final responses
        time.sleep(1.0)

        # get user agent
        try:
            ua = page.evaluate('navigator.userAgent')
            tokens.setdefault('USER_AGENT', ua)
        except Exception:
            pass

        # fallback strategies
        get_user_id_from_localstorage(page, tokens)
        sniff_client_id_from_scripts(page, tokens)

        browser.close()

    return tokens


def run():
    project_root = Path(__file__).parent.parent
    env_path = project_root / '.env'

    print('Launching browser. Please complete sign-in in the opened window.')
    tokens = capture_tokens_with_playwright(env_path)

    # Show what we captured
    print('\nCaptured values:')
    for k, v in tokens.items():
        print(f'  {k}: {str(v)[:80]}{"..." if len(str(v))>80 else ""}')

    if not tokens.get('REFRESH_TOKEN'):
        print('\nWarning: REFRESH_TOKEN not found. If it was not returned in the Cognito response,')
        print('complete the sign-in flow again and watch the network responses. You can also')
        print('paste the refresh token manually into the `.env` file.')

    print(f"\nWriting {env_path} (overwrites if exists)")
    write_env_file(env_path, tokens)

    print('\nDone. You can now run the autopicker:')
    print('  pip install -r requirements.txt')
    print('  python autopicker/main.py --test True')


if __name__ == '__main__':
    run()
