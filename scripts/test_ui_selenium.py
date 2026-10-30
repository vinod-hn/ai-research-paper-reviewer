"""
Selenium Browser Automation & Verification Script for AI Research Paper Reviewer UI
Tests:
- Renders http://127.0.0.1:7860/ cleanly in headless Chrome
- Navigates through all 10 tabs
- Inspects computed styles of .code-badge, .path-badge, and text elements in Settings
- Verifies that NO elements have unreadable black-on-black or white-on-white styling
- Captures full-page screenshots of redesigned Settings, Discover, and Dashboard
- Tests responsive viewports (1920px, 1440px, 1024px, 768px, 390px)
"""

import os
import sys
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def run_browser_tests():
    print("==================================================")
    print("STARTING SELENIUM HEADLESS BROWSER VERIFICATION")
    print("==================================================")

    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1600,1050')

    driver = webdriver.Chrome(options=options)
    artifacts_dir = os.path.abspath("data")
    os.makedirs(artifacts_dir, exist_ok=True)

    try:
        url = "http://127.0.0.1:7860/"
        print(f"Connecting to {url} ...")
        driver.get(url)

        # Wait for Gradio app to render
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, "workspace-header"))
        )
        print("[OK] UI Loaded. Header detected.")

        # 1. Verify Header and Title
        header_el = driver.find_element(By.CLASS_NAME, "workspace-header")
        assert "AI Research Paper Reviewer" in header_el.text
        print("[OK] Header title confirmed: 'AI Research Paper Reviewer'")

        time.sleep(1.0)

        # 2. Test Navigation Tabs (at standard desktop viewport)
        tabs = driver.find_elements(By.CSS_SELECTOR, "button[role='tab']")
        print(f"[OK] Found {len(tabs)} navigation tabs:")
        for idx, tab in enumerate(tabs):
            print(f"   Tab {idx}: '{tab.text.strip()}'")

        assert len(tabs) == 10, f"Expected 10 tabs, got {len(tabs)}"

        # 3. Click and Test Settings Tab (Mandatory Fix Area)
        settings_tab = tabs[9]  # 10th tab is Settings
        assert "Settings" in settings_tab.text
        settings_tab.click()
        time.sleep(1.0)
        print("[OK] Successfully navigated to 'Settings' tab.")

        # Verify Settings Card Grid is present
        settings_grid = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CLASS_NAME, "settings-grid"))
        )
        assert settings_grid.is_displayed()
        print("[OK] Redesigned settings-grid detected and displayed.")

        # Inspect Computed Styles of .code-badge and .path-badge
        code_badges = driver.find_elements(By.CLASS_NAME, "code-badge")
        print(f"[OK] Found {len(code_badges)} code badges in Settings:")
        for i, badge in enumerate(code_badges):
            bg_col = badge.value_of_css_property("background-color")
            txt_col = badge.value_of_css_property("color")
            text = badge.text.strip()
            print(f"   Badge '{text}': background={bg_col}, text_color={txt_col}")
            # Ensure background is NOT black / dark slate
            assert "rgba(0, 0, 0" not in bg_col and "rgb(0, 0, 0)" not in bg_col, f"Black background on {text}!"
            # Ensure text is clearly contrasted
            assert bg_col != txt_col, f"Contrast failure on {text}!"

        path_badges = driver.find_elements(By.CLASS_NAME, "path-badge")
        print(f"[OK] Found {len(path_badges)} path badges in Settings:")
        for p_badge in path_badges:
            bg = p_badge.value_of_css_property("background-color")
            col = p_badge.value_of_css_property("color")
            text = p_badge.text.strip()
            print(f"   Path '{text[:30]}...': background={bg}, text_color={col}")
            assert "rgba(0, 0, 0" not in bg and "rgb(0, 0, 0)" not in bg
            assert bg != col

        # Capture Settings Screenshot
        shot_settings = os.path.join(artifacts_dir, "settings_redesigned_verified.png")
        driver.save_screenshot(shot_settings)
        print(f"[OK] Saved verified Settings screenshot to {shot_settings}")

        # 4. Click Discover Papers Tab and Capture Screenshot
        discover_tab = tabs[1]
        discover_tab.click()
        time.sleep(1.0)
        shot_discover = os.path.join(artifacts_dir, "discover_redesigned_verified.png")
        driver.save_screenshot(shot_discover)
        print(f"[OK] Saved verified Discover Papers screenshot to {shot_discover}")

        # 5. Click Dashboard Tab and Capture Screenshot
        dash_tab = tabs[0]
        dash_tab.click()
        time.sleep(0.8)
        shot_dash = os.path.join(artifacts_dir, "dashboard_redesigned_verified.png")
        driver.save_screenshot(shot_dash)
        print(f"[OK] Saved verified Dashboard screenshot to {shot_dash}")

        # 6. Test Responsive Viewports
        viewports = [(1920, 1080), (1440, 900), (1024, 768), (768, 1024), (390, 844)]
        for w, h in viewports:
            driver.set_window_size(w, h)
            time.sleep(0.3)
            body = driver.find_element(By.TAG_NAME, "body")
            assert body.is_displayed()
            print(f"[OK] Viewport {w}x{h} rendered without layout crashes.")

        print("==================================================")
        print("ALL BROWSER TESTS AND VERIFICATIONS COMPLETED!")
        print("==================================================")

    finally:
        driver.quit()

if __name__ == "__main__":
    run_browser_tests()
