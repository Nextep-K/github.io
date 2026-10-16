"""Exercise the actual Streamlit UI in a local headless Chromium browser.

Does not call OpenAI or Notion: the pilot's built-in rule fallback is enough to
validate that submitted commands and answers render in reverse chronological order.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


APP = Path(__file__).resolve().parents[1] / "app.py"
BASE = "http://127.0.0.1:8591/"


def wait_for_server(timeout=45):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            with urlopen(BASE+"_stcore/health",timeout=2) as response:
                if response.status==200:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.5)
    raise RuntimeError("Local Streamlit did not become healthy")


def run():
    from playwright.sync_api import sync_playwright, expect

    server=subprocess.Popen([
        sys.executable,"-m","streamlit","run",str(APP),
        "--server.address","127.0.0.1","--server.port","8591",
        "--server.headless","true","--browser.gatherUsageStats","false",
    ], stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        wait_for_server()
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True,args=["--no-sandbox"])
            try:
                context=browser.new_context(viewport={"width":390,"height":844},
                                            locale="ko-KR",timezone_id="Asia/Seoul")
                page=context.new_page()
                response=page.goto(BASE,wait_until="domcontentloaded",timeout=45000)
                if response is None or response.status!=200:
                    raise AssertionError("Local browser could not load the app")
                command=page.locator("textarea[placeholder='여행 중 궁금한 것을 말하세요']")
                command.wait_for(state="visible",timeout=45000)
                expect(page.get_by_text("음성명령 입력",exact=True)).to_be_visible()
                expect(page.get_by_text("음성 안내",exact=True)).to_be_visible()

                command.fill("월정사에 갈래")
                command.press("Enter")
                page.get_by_text("월정사로 가시는군요",exact=False).first.wait_for(timeout=30000)

                command.fill("지금 출발")
                command.press("Enter")
                page.get_by_text("출발로 기록했습니다",exact=False).first.wait_for(timeout=30000)

                cards=page.locator('[data-testid="stChatMessage"]')
                expect(cards).to_have_count(5,timeout=20000)
                texts=[cards.nth(i).inner_text() for i in range(5)]
                assert "출발로 기록했습니다" in texts[0],texts
                assert "지금 출발" in texts[1],texts
                assert "월정사로 가시는군요" in texts[2],texts
                assert "월정사에 갈래" in texts[3],texts
                assert "여행을 시작해 보겠습니다" in texts[4],texts

                inline_parent=page.locator(".st-key-journey_command_bar")
                assert inline_parent.count()==1
                assert inline_parent.evaluate("(el)=>getComputedStyle(el).position")=="sticky"
                command_y=command.bounding_box()["y"]
                first_reply_y=cards.first.bounding_box()["y"]
                assert command_y<first_reply_y,(command_y,first_reply_y)

                # Verify the replay button starts speech inside its own click
                # listener (without waiting for a Streamlit server rerun).
                replay_frames=[fr for fr in page.frames
                               if fr.locator("#voice-replay").count()==1]
                assert len(replay_frames)==1, "Expected exactly one replay iframe"
                replay_frame=replay_frames[0]
                replay_frame.evaluate("""() => {
                    window.__replayCalls=[];
                    Object.defineProperty(window,"speechSynthesis",{
                        configurable:true,
                        value:{
                            cancel:()=>{},
                            getVoices:()=>[],
                            speak:(utterance)=>window.__replayCalls.push(utterance.text)
                        }
                    });
                    Object.defineProperty(window,"SpeechSynthesisUtterance",{
                        configurable:true,
                        value:class { constructor(text){this.text=text;} }
                    });
                }""")
                replay_frame.locator("#voice-replay").click()
                spoken=replay_frame.evaluate("() => window.__replayCalls")
                assert len(spoken)==1 and "출발로 기록했습니다" in spoken[0],spoken
                print("PASS: replay begins speech in direct browser click handler")
                print("PASS: real 390px Chromium: input first; latest assistant reply,")
                print("      latest user, previous assistant, previous user, greeting last")
                print("PASS: original voice-input and TTS controls remain visible")
                print("PASS: chat input rendered inline inside the sticky command container")
            except Exception:
                print("Browser state:",page.url,repr(page.locator("body").inner_text()[:800]),flush=True)
                raise
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            stdout,_=server.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill()
            stdout,_=server.communicate()
        print("Local Streamlit output (last lines):",stdout[-1200:],flush=True)


if __name__=="__main__":
    run()
