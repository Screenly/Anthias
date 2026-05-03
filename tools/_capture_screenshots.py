"""Throwaway helper to capture full-page screenshots of every Anthias route.

Used during the React-to-Django migration to produce before/after pairs.
Not part of the shipped tree — delete once the migration lands.

Usage:
    uv run python tools/_capture_screenshots.py before
    uv run python tools/_capture_screenshots.py after
"""

import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import WebDriverWait

ROUTES = [
    ('login', '/login/', None),
    ('splash', '/splash-page', None),
    ('home', '/', 'nav.navbar'),
    ('system-info', '/system-info', 'nav.navbar'),
    ('integrations', '/integrations', 'nav.navbar'),
    ('settings', '/settings', 'nav.navbar'),
]

VIEWPORTS = [
    ('desktop', 1366, 900),
    ('mobile', 414, 900),
]


def main(label: str) -> None:
    out = Path('/tmp/anthias-screenshots') / label
    out.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(options=options)

    try:
        for vp_name, w, h in VIEWPORTS:
            driver.set_window_size(w, h)
            for name, path, wait_selector in ROUTES:
                url = f'http://localhost:8000{path}'
                driver.get(url)
                if wait_selector:
                    try:
                        WebDriverWait(driver, 10).until(
                            ec.presence_of_element_located(
                                (By.CSS_SELECTOR, wait_selector),
                            )
                        )
                    except Exception as exc:
                        print(f'  WARN: {url} no {wait_selector}: {exc}')
                # Let async data render.
                time.sleep(2)
                # Resize to full content height for full-page capture.
                full_h = driver.execute_script(
                    'return Math.max('
                    'document.body.scrollHeight,'
                    'document.documentElement.scrollHeight)'
                )
                driver.set_window_size(w, max(h, full_h + 100))
                time.sleep(0.3)
                target = out / f'{name}_{vp_name}.png'
                driver.save_screenshot(str(target))
                print(f'  {target} ({w}x{full_h})')
    finally:
        driver.quit()


if __name__ == '__main__':
    label = sys.argv[1] if len(sys.argv) > 1 else 'before'
    main(label)
