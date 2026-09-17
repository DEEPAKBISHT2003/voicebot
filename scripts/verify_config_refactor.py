import os
import sys
import subprocess

python_exe = sys.executable

def test_config():
    print("==================================================")
    print("RUNNING CONFIG REFACTOR VERIFICATION")
    print("==================================================")

    # 1. Test BOT_ROLE=observer
    env_observer = os.environ.copy()
    env_observer["BOT_ROLE"] = "observer"
    env_observer["BOT_DISPLAY_NAME"] = "Appzlogic Moderator"
    p1 = subprocess.run(
        [python_exe, "-c", "from services.browser.src.config import config, BrowserConfig, BOT_ROLE, BOT_DISPLAY_NAME; print(f'BOT_ROLE={BOT_ROLE} | BOT_DISPLAY_NAME={BOT_DISPLAY_NAME} | WS_URL={BrowserConfig.get_ws_url(\"sess-123\")}')"],
        capture_output=True, text=True, env=env_observer
    )
    print("\n[Test 1: Runtime BOT_ROLE=observer]")
    print(p1.stdout.strip())
    assert "BOT_ROLE=observer" in p1.stdout
    assert "/api/ws/copilot/sess-123?mode=audio_stream" in p1.stdout
    print("RESULT: PASS [OK]")

    # 2. Test BOT_ROLE=interviewer
    env_interviewer = os.environ.copy()
    env_interviewer["BOT_ROLE"] = "interviewer"
    env_interviewer["BOT_DISPLAY_NAME"] = "Mia - Appz Interviewer"
    p2 = subprocess.run(
        [python_exe, "-c", "from services.browser.src.config import config, BrowserConfig, BOT_ROLE, BOT_DISPLAY_NAME; print(f'BOT_ROLE={BOT_ROLE} | BOT_DISPLAY_NAME={BOT_DISPLAY_NAME} | WS_URL={BrowserConfig.get_ws_url(\"sess-456\")}')"],
        capture_output=True, text=True, env=env_interviewer
    )
    print("\n[Test 2: Runtime BOT_ROLE=interviewer]")
    print(p2.stdout.strip())
    assert "BOT_ROLE=interviewer" in p2.stdout
    assert "/api/ws/interview/sess-456" in p2.stdout
    print("RESULT: PASS [OK]")

    # 3. Test Fallback when no runtime BOT_ROLE is set (reads .env fallback)
    env_clean = os.environ.copy()
    env_clean.pop("BOT_ROLE", None)
    env_clean.pop("BOT_DISPLAY_NAME", None)
    p3 = subprocess.run(
        [python_exe, "-c", "from services.browser.src.config import config, BrowserConfig, BOT_ROLE; print(f'BOT_ROLE={BOT_ROLE} | WS_URL={BrowserConfig.get_ws_url(\"sess-789\")}')"],
        capture_output=True, text=True, env=env_clean
    )
    print("\n[Test 3: No runtime BOT_ROLE (.env fallback)]")
    print(p3.stdout.strip())
    assert ("BOT_ROLE=interviewer" in p3.stdout or "BOT_ROLE=observer" in p3.stdout)
    print("RESULT: PASS [OK]")

    # 4. Test teams_bot.py direct script import & execution compatibility
    p4 = subprocess.run(
        [python_exe, "-c", "import services.browser.src.pipeline.teams_bot as tb; print(f'teams_bot loaded successfully | tb.BOT_ROLE={tb.BOT_ROLE} | tb.USE_SHARED_NAMESPACE={tb.USE_SHARED_NAMESPACE}')"],
        capture_output=True, text=True, env=env_observer
    )
    print("\n[Test 4: teams_bot.py module import & configuration binding]")
    print(p4.stdout.strip())
    assert "teams_bot loaded successfully" in p4.stdout
    print("RESULT: PASS [OK]")

    # 5. Test UNIFIED_BROWSER_AUDIO_JS loading and %WS_URL% interpolation
    p5 = subprocess.run(
        [python_exe, "-c", "import services.browser.src.pipeline.teams_bot as tb; assert len(tb.UNIFIED_BROWSER_AUDIO_JS) > 20000; assert '%WS_URL%' in tb.UNIFIED_BROWSER_AUDIO_JS; formatted = tb.UNIFIED_BROWSER_AUDIO_JS.replace('%WS_URL%', 'ws://localhost:8000/test'); assert 'ws://localhost:8000/test' in formatted; print(f'JS length={len(tb.UNIFIED_BROWSER_AUDIO_JS)} chars | interpolation OK')"],
        capture_output=True, text=True, env=env_observer
    )
    print("\n[Test 5: audio_bridge.js loaded and validated in teams_bot]")
    print(p5.stdout.strip())
    assert "interpolation OK" in p5.stdout
    print("RESULT: PASS [OK]")

    # 6. Test TeamsBrowser and TeamsMeeting package structure
    p6 = subprocess.run(
        [python_exe, "-c", "from services.browser.src.teams import TeamsBrowser, TeamsMeeting; from services.browser.src.teams.meeting import is_really_in_meeting, run_in_meeting_diagnostics; print('TeamsBrowser and TeamsMeeting imported successfully')"],
        capture_output=True, text=True, env=env_observer
    )
    print("\n[Test 6: teams package (TeamsBrowser, TeamsMeeting) import check]")
    print(p6.stdout.strip())
    assert "TeamsBrowser and TeamsMeeting imported successfully" in p6.stdout
    print("RESULT: PASS [OK]")

    # 7. Test AudioBridge package structure and methods
    p7 = subprocess.run(
        [python_exe, "-c", "from services.browser.src.audio import AudioBridge, UNIFIED_BROWSER_AUDIO_JS, periodic_injector, start_localhost_proxy; bridge = AudioBridge('ws://localhost:8000/api/ws/copilot/test?mode=audio_stream'); js = bridge.get_formatted_js(); assert 'ws://localhost:8000/api/ws/copilot/test?mode=audio_stream' in js; print('AudioBridge initialized and formatted JS successfully')"],
        capture_output=True, text=True, env=env_observer
    )
    print("\n[Test 7: audio package (AudioBridge) check]")
    print(p7.stdout.strip())
    assert "AudioBridge initialized and formatted JS successfully" in p7.stdout
    print("RESULT: PASS [OK]")

    print("\n==================================================")
    print("ALL VERIFICATION CHECKS PASSED [OK]")
    print("==================================================")

if __name__ == "__main__":
    test_config()
