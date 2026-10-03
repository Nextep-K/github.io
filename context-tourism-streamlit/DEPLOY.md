# Streamlit Community Cloud deployment

## GitHub coordinates
- Repository: Nextep-K/github.io
- Branch: main
- Main file path: context-tourism-streamlit/app.py
- Python: 3.12

## Deploy
1. Open https://share.streamlit.io/
2. Create app
3. Choose "Yup, I have an app"
4. Enter the three GitHub coordinates above.
5. Open Advanced settings.
6. Keep Python 3.12.
7. Paste Secrets from secrets.toml.example after replacing only the secret values.
8. Deploy.

## Notion access
The Notion databases already exist:
- Journey DB: 872c7dd7-b60c-44f5-a33e-f76a36a519ca
- Tourist Insight DB: 99c918d3-a363-41b7-989c-089d730e6e65
- Pilot Reports: 87d1d86a-c4d7-43e7-8b29-3ad1c0d13de2

A Notion internal integration used by Streamlit must be granted access to the parent page:
Context Tourism Pilot — 개발 기준·실험 로그

The ChatGPT Notion connection and a Streamlit NOTION_TOKEN are separate credentials.

## Without secrets
The app still deploys and runs with the rule-based fallback. OpenAI conversation intelligence and direct Notion sync remain disabled until their secrets are added.
