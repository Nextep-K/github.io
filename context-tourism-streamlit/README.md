# Context Tourism Pilot — Streamlit v0.9.1

## Deployment coordinates
- Repository: `Nextep-K/github.io`
- Branch: `main` (changes only go live after merging)
- Main file path: `context-tourism-streamlit/app.py`
- Python: 3.12
- Streamlit Community Cloud: select the repository, branch, and subdirectory entrypoint above.

## Pilot scope
- Chat-first journey with optional browser geolocation on user permission
- Observer axes: Intent → Deliberation → Exposure → Choice → Movement → Stay → Transaction
- Separate Traveler response and Observer event extraction
- User interaction occurrence time, recorded time, and associated location
- Meal, rest, fatigue, and toilet cues as *situational signals* (not stable traits)
- Optional on-demand web search for current travel information
- **Voice command input mode defaults to ON each time the app session starts**; users can turn it OFF with the UI toggle or the spoken command "음성 입력 꺼". It can be re-enabled using the UI or text command "음성 입력 켜".
- Input mode and voice read-aloud output are separate preferences. Voice input ON does **not** change the voice read-aloud setting.
- Voice recording/transcription and optional browser TTS
- Browser-local active Journey and history, resume and JSON export
- Notion Journey, Pilot Raw Log, and Pilot Reports checkpoints when configured
- Full raw messages are split into Notion-compatible rich-text chunks instead of truncated.

## Voice input behavior

"Voice command input ON" means the microphone **recording control is available**, not that a microphone is continuously recording or listening.

- Streamlit `st.audio_input` starts recording when the user presses the microphone control and submits after the recording is finished.
- The user must grant browser microphone permission where applicable; this app cannot bypass that prompt or force the microphone on through Streamlit.
- Voice input OFF disables `st.audio_input`. Typed chat stays available.
- Continuous, hands-free listening would require a separate consent-based browser microphone component/PWA and device testing, not merely changing this default flag.

## Important limitations
- Successful Python CI is **not** proof that a Streamlit deployment is live or mobile-capable.
- Browser permissions, iPhone Safari audio playback, and actual web search/Notion authentication require device-level validation.
- Streamlit Cloud may redirect non-browser HTTP clients through an authentication flow repeatedly even when browsers work; the public probe is indicative only.
- Location is sampled when the app runs, not continuous background GPS.
- LocalStorage is device/browser specific and not encrypted by this application. Avoid shared devices for personal journeys.
- `APP_PASSCODE` currently protects the administrator and history UI, **not ordinary chat access**. Restrict the Streamlit deployment's Sharing settings if using paid API credentials.
- Tourist Insight DB population, trajectory-based preference learning, and validated life-rhythm inference are future features.

## Streamlit Secrets (example only; never commit real credentials)
```toml
APP_PASSCODE = "CHOOSE_A_LONG_PRIVATE_PASSCODE"
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
TRAVELER_MODEL = "gpt-6.1-sol"
OBSERVER_MODEL = "gpt-6-luna"
OPENAI_MODEL = "gpt-6-luna"
NOTION_TOKEN = "YOUR_NOTION_INTERNAL_INTEGRATION_TOKEN"
NOTION_JOURNEY_DATA_SOURCE_ID = "..."
NOTION_REPORT_DATA_SOURCE_ID = "..."
NOTION_RAW_LOG_DATA_SOURCE_ID = "..."
```
If secrets are absent, related optional features remain inactive or use rule-based responses.

## Checks
```bash
python -m py_compile context-tourism-streamlit/app.py
python -m unittest discover -s context-tourism-streamlit/tests -p "test_*.py" -v
python context-tourism-streamlit/tests/probe_deployment.py
```

The last command is a non-browser accessibility indicator, not an authenticated UI test.
