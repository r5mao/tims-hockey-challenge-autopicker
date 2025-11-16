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


def run():
    project_root = Path(__file__).parent.parent
    env_path = project_root / '.env'

    print('Launching browser. Please complete sign-in in the opened window.')
    tokens = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        def handle_response(response):
            try:
                url = response.url
                if 'cognito-idp.us-east-1.amazonaws.com' in url:
                    # Try to parse response JSON
                    try:
                        body = response.json()
                    except Exception:
                        return

                    # Look for tokens in response bodies
                    if isinstance(body, dict):
                        # Example: during auth flows, AuthenticationResult may be present
                        if 'AuthenticationResult' in body:
                            ar = body['AuthenticationResult']
                            if 'RefreshToken' in ar and not tokens.get('REFRESH_TOKEN'):
                                tokens['REFRESH_TOKEN'] = ar['RefreshToken']
                        # Some flows may echo client id in response payload; otherwise read from request
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
                                # not JSON, try simple parse
                                if 'ClientId' in post:
                                    # naive extraction
                                    idx = post.find('ClientId')
                                    tokens['CLIENT_ID'] = post[idx:idx+200]
                    except Exception:
                        pass
            except Exception:
                # Catch any unexpected errors in the response handler to avoid crashing
                pass

        page.on('response', handle_response)

        page.goto('https://www.timhortons.ca/account')

        # Let the user interact and sign in (enter OTP) in the opened browser window.
        input('After completing sign-in in the browser window, press Enter here to continue...')

        # Give network handlers a moment to process any final responses
        time.sleep(1.0)

        # Get user agent
        try:
            ua = page.evaluate('navigator.userAgent')
            tokens.setdefault('USER_AGENT', ua)
        except Exception:
            pass

        # Try to get USER_ID from localStorage (thLegacyCognitoId or similar)
        try:
            # Check a few possible keys commonly used
            keys = page.evaluate('Object.keys(window.localStorage)')
            uid = None
            for k in keys:
                if 'thLegacyCognitoId' in k or 'thLegacy' in k or 'cognito' in k.lower():
                    uid = page.evaluate(f"window.localStorage.getItem('{k}')")
                    break
            # Fallback: try explicit key
            if uid is None:
                uid = page.evaluate("window.localStorage.getItem('thLegacyCognitoId')")
            if uid:
                tokens.setdefault('USER_ID', uid)
        except Exception:
            pass

        # If CLIENT_ID was not captured via network, try to sniff it from loaded scripts
        if not tokens.get('CLIENT_ID'):
            try:
                scripts = page.query_selector_all('script')
                for s in scripts:
                    src = s.get_attribute('src')
                    if not src:
                        txt = s.inner_text()
                        if 'ClientId' in txt:
                            # try to find a JS object literal containing ClientId
                            idx = txt.find('ClientId')
                            snippet = txt[idx: idx + 200]
                            # crude extraction attempt
                            tokens['CLIENT_ID'] = snippet
                            break
            except Exception:
                pass

        browser.close()

    # Show what we captured
    print('\nCaptured values:')
    for k, v in tokens.items():
        print(f'  {k}: {str(v)[:80]}{"..." if len(str(v))>80 else ""}')

    if not tokens.get('REFRESH_TOKEN'):
        print('\nWarning: REFRESH_TOKEN not found. If it was not returned in the Cognito response,')
        print('complete the sign-in flow again and watch the network responses. You can also')
        print('paste the refresh token manually into the `.env` file.')

    # Write .env
    env_lines = []
    env_lines.append(f"USER_AGENT={tokens.get('USER_AGENT','')}")
    env_lines.append(f"CLIENT_ID={tokens.get('CLIENT_ID','')}")
    env_lines.append(f"REFRESH_TOKEN={tokens.get('REFRESH_TOKEN','')}")
    env_lines.append(f"USER_ID={tokens.get('USER_ID','')}")

    print(f"\nWriting {env_path} (overwrites if exists)")
    with open(env_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(env_lines))

    print('\nDone. You can now run the autopicker:')
    print('  pip install -r requirements.txt')
    print('  python autopicker/main.py --test True')


if __name__ == '__main__':
    run()
