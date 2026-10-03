# Context Tourism Pilot — Streamlit v0.3

## Community Cloud coordinates

- Repository: `Nextep-K/github.io`
- Branch: `main`
- Main file path: `context-tourism-streamlit/app.py`

Community Cloud supports entrypoint files in subdirectories. The dependency file is placed next to the entrypoint.

## Current functions

- Chat-first tourism journey
- Browser geolocation on user permission
- Time + location attached to Events
- Observation Axis:
  Intent → Deliberation → Exposure → Choice → Movement → Stay → Transaction
- Admin Context Trajectory
- Conservative Rubric
- JSON export
- OpenAI Responses API when secret exists
- Rule fallback when OpenAI secret is absent
- Notion Journey DB + Pilot Reports sync when Notion secrets exist

## Streamlit Secrets

Do not commit actual secrets to GitHub.

```toml
OPENAI_API_KEY = "..."
OPENAI_MODEL = "gpt-5.6"

NOTION_TOKEN = "..."
NOTION_JOURNEY_DATA_SOURCE_ID = "..."
NOTION_INSIGHT_DATA_SOURCE_ID = "..."
NOTION_REPORT_DATA_SOURCE_ID = "..."
```

The current Streamlit pilot gets the user's current browser location. Continuous background GPS remains a later PWA/native concern.
