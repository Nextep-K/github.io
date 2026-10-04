import json
import re
import uuid
from datetime import datetime, timezone

import streamlit as st
from streamlit_js_eval import get_geolocation, streamlit_js_eval

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from notion_client import Client as NotionClient
except Exception:
    NotionClient = None

st.set_page_config(page_title="Context Tourism Pilot", page_icon="🧭", layout="centered")

st.markdown("""
<style>
.block-container{max-width:760px;padding-top:1rem;padding-bottom:5rem}
div[data-testid="stMetric"]{background:#1b1f27;padding:10px;border-radius:12px;border:1px solid #303641}
div[data-testid="stMetric"] [data-testid="stMetricLabel"],
div[data-testid="stMetric"] [data-testid="stMetricValue"]{color:#f7f8fa!important}
</style>
""", unsafe_allow_html=True)

ALLOWED_TYPES={"Intent","Deliberation","Exposure","Choice","Movement","Stay","Transaction"}
ALLOWED_STATES={"IDLE","PLANNING","MOVING","STAYING","ENDED"}

TRAVELER_PROMPT="""너는 평창 여행 중 사용자를 돕는 지능형 여행 동반자다.

목표:
- 이전 대화와 현재 Journey 상태를 이어서 이해한다.
- 사용자가 이미 말한 목적지·교통수단·출발 여부·선호를 다시 묻지 않는다.
- 주차, 식당, 운영시간, 입장료, 행사, 교통, 실제 장소 추천처럼 외부·최신 정보가 필요한 질문에는 web_search를 스스로 사용한다.
- 검색이 필요하면 먼저 검색하고, 충분히 답할 수 있는데도 막연한 추가 질문으로 돌리지 않는다.
- 현재 위치 좌표와 목적지, 이동 상태를 함께 고려한다.
- 가능한 경우 구체적인 장소명·이유·현재 맥락에 맞는 선택지를 2~4개 정도 제시한다.
- 공식 기관·시설·신뢰도 높은 출처를 우선하고, 최신 여부가 중요한 정보는 검색 결과에 근거한다.
- 사용자가 운전 중일 수 있어 답은 간결하게 하되, '휴대폰을 조작하지 말라' 같은 상투적 안전문구를 매번 반복하지 않는다. 실제로 즉각적인 안전 문제가 있을 때만 필요한 경고를 한다.
- 확실하지 않은 사실을 지어내지 않는다.
- 꼭 필요한 경우가 아니면 질문만 되돌려 보내지 말고 우선 최선의 답을 준 뒤 필요하면 한 가지 보완 질문만 한다.
- 사용자가 '가는 중', '도착', '이제 어디 갈까'처럼 짧게 말해도 이전 대화와 Journey를 기준으로 해석한다.
- 사용자가 관광·문화·역사 설명을 요청하면 단순 사실 나열보다 장소의 맥락과 의미를 이해하기 쉽게 설명한다.

너의 응답은 사용자에게 보이는 실제 여행 안내 문장만 작성한다.
"""

OBSERVER_PROMPT="""너는 관광 행동 관찰기다. 사용자에게 답하지 말고, 한 턴의 사용자 발화와 실제 assistant 응답을 관찰하여 구조화 이벤트만 추출한다.

관찰축: Intent → Deliberation → Exposure → Choice → Movement → Stay → Transaction

규칙:
1. 사용자 직접 진술과 추론을 구분한다.
2. 단일 행동을 안정적 성향으로 일반화하지 않는다.
3. assistant가 실제 장소·정보·추천을 제공한 경우에만 Exposure를 만든다.
4. 사용자가 이미 선택했거나 결정한 것만 Choice로 만든다.
5. 현재 Journey 상태와 이전 Event를 참고하여 상태 변화를 기록한다.
6. state_updates의 변경 없는 문자열 값은 빈 문자열로 둔다.
7. assistant의 설명 자체는 Choice가 아니다.
"""

OUTPUT_SCHEMA={
    "type":"object",
    "additionalProperties":False,
    "properties":{
        "assistant_text":{"type":"string"},
        "events":{
            "type":"array",
            "items":{
                "type":"object",
                "additionalProperties":False,
                "properties":{
                    "type":{"type":"string","enum":["Intent","Deliberation","Exposure","Choice","Movement","Stay","Transaction"]},
                    "subtype":{"type":"string"},
                    "detail":{"type":"string"},
                    "explicit_or_inferred":{"type":"string","enum":["explicit","inferred"]},
                    "confidence":{"type":"number","minimum":0,"maximum":1}
                },
                "required":["type","subtype","detail","explicit_or_inferred","confidence"]
            }
        },
        "state_updates":{
            "type":"object",
            "additionalProperties":False,
            "properties":{
                "state":{"type":"string","enum":["","IDLE","PLANNING","MOVING","STAYING","ENDED"]},
                "origin":{"type":"string"},
                "destination":{"type":"string"},
                "transport":{"type":"string"},
                "spend_delta":{"type":"integer","minimum":0}
            },
            "required":["state","origin","destination","transport","spend_delta"]
        }
    },
    "required":["assistant_text","events","state_updates"]
}

def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def secret(name, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

BROWSER_STATE_KEY="contextTourismPilot.v04"
LEGACY_BROWSER_STATE_KEY="contextTourismPilot.v03"
DEFAULT_RAW_LOG_DS_ID="d48e3ba6-0769-4a95-bf70-a27d9eabbf37"

def blank_journey():
    return {
        "id":str(uuid.uuid4()),
        "state":"IDLE",
        "origin":None,
        "destination":None,
        "transport":None,
        "spend_krw":0,
        "created_at":now_iso(),
        "started_at":None,
        "ended_at":None,
        "notion_synced_at":None,
        "notion_journey_page_id":None,
        "notion_report_page_id":None,
    }

def init_state():
    defaults={
        "messages":[{"role":"assistant","content":"여행을 시작해 보겠습니다. 지금 어디에 있고, 어디로 가실 예정인가요?"}],
        "events":[],
        "turns":[],
        "journey":blank_journey(),
        "location":None,
        "last_location_key":None,
        "browser_store_loaded":False,
        "saved_active":None,
        "journey_history":[],
        "selected_history_id":None,
    }
    for k,v in defaults.items():
        if k not in st.session_state:
            st.session_state[k]=v

def state_payload():
    return {
        "messages":st.session_state.messages,
        "events":st.session_state.events,
        "turns":st.session_state.turns,
        "journey":st.session_state.journey,
        "location":st.session_state.location,
        "last_location_key":st.session_state.last_location_key,
    }

def payload_has_activity(payload):
    if not payload or not isinstance(payload,dict):
        return False
    j=payload.get("journey") or {}
    return bool(
        payload.get("events")
        or len(payload.get("messages") or [])>1
        or j.get("destination")
        or j.get("origin")
        or j.get("state") not in (None,"IDLE")
    )

def normalize_saved_payload(payload):
    if not isinstance(payload,dict):
        return None
    j=payload.get("journey")
    if not isinstance(j,dict):
        return None
    j.setdefault("created_at", j.get("started_at") or now_iso())
    j.setdefault("notion_synced_at",None)
    j.setdefault("notion_journey_page_id",None)
    j.setdefault("notion_report_page_id",None)
    payload.setdefault("messages",[])
    payload.setdefault("events",[])
    payload.setdefault("turns",[])
    payload.setdefault("location",None)
    payload.setdefault("last_location_key",None)
    return payload

def load_browser_store():
    if st.session_state.browser_store_loaded:
        return
    raw=streamlit_js_eval(
        js_expressions=f'localStorage.getItem("{BROWSER_STATE_KEY}") || localStorage.getItem("{LEGACY_BROWSER_STATE_KEY}") || "__EMPTY__"',
        want_output=True,
        key="LOAD_BROWSER_STORE",
    )
    if raw is None:
        return
    st.session_state.browser_store_loaded=True
    if raw=="__EMPTY__":
        return
    try:
        saved=json.loads(raw)
        if not isinstance(saved,dict):
            return
        # v0.4 store
        if "history" in saved or "active" in saved:
            active=normalize_saved_payload(saved.get("active"))
            history=[]
            for item in saved.get("history") or []:
                item=normalize_saved_payload(item)
                if item:
                    history.append(item)
            st.session_state.saved_active=active if payload_has_activity(active) else None
            st.session_state.journey_history=history
        # v0.3 legacy single-session payload: keep as resumable, do not auto-restore.
        elif "journey" in saved:
            legacy=normalize_saved_payload(saved)
            if legacy and payload_has_activity(legacy):
                if (legacy.get("journey") or {}).get("state")=="ENDED":
                    st.session_state.journey_history=[legacy]
                else:
                    st.session_state.saved_active=legacy
    except Exception:
        pass

def upsert_history(payload):
    payload=normalize_saved_payload(payload)
    if not payload:
        return
    jid=(payload.get("journey") or {}).get("id")
    if not jid:
        return
    history=[h for h in st.session_state.journey_history if (h.get("journey") or {}).get("id")!=jid]
    history.append(payload)
    history.sort(key=lambda x:(x.get("journey") or {}).get("created_at") or "",reverse=True)
    st.session_state.journey_history=history[:100]

def persist_browser_store():
    current=state_payload()
    j=current.get("journey") or {}
    active=st.session_state.saved_active

    if payload_has_activity(current):
        current_id=j.get("id")
        active_id=((active or {}).get("journey") or {}).get("id")
        # Starting another Journey must never destroy an unfinished saved Journey.
        if active and active_id and current_id and active_id!=current_id:
            upsert_history(active)
        if j.get("state")=="ENDED":
            upsert_history(current)
            active=None
        else:
            active=current
    st.session_state.saved_active=active

    store={
        "version":4,
        "saved_at":now_iso(),
        "active":active,
        "history":st.session_state.journey_history,
    }
    raw=json.dumps(store,ensure_ascii=False,separators=(",",":"))
    expr=f'localStorage.setItem("{BROWSER_STATE_KEY}", {json.dumps(raw)}); localStorage.removeItem("{LEGACY_BROWSER_STATE_KEY}")'
    streamlit_js_eval(js_expressions=expr,want_output=False,key="SAVE_BROWSER_STORE")

def resume_payload(payload):
    payload=normalize_saved_payload(payload)
    if not payload:
        return
    jid=(payload.get("journey") or {}).get("id")
    st.session_state.messages=payload.get("messages") or [{"role":"assistant","content":"이어서 진행하겠습니다."}]
    st.session_state.events=payload.get("events") or []
    st.session_state.turns=payload.get("turns") or []
    st.session_state.journey=payload.get("journey") or blank_journey()
    st.session_state.location=payload.get("location")
    st.session_state.last_location_key=payload.get("last_location_key")
    st.session_state.saved_active=None
    if jid:
        st.session_state.journey_history=[
            h for h in st.session_state.journey_history
            if ((h.get("journey") or {}).get("id")!=jid)
        ]
    st.session_state.selected_history_id=None

def reset_current_journey():
    st.session_state.messages=[{"role":"assistant","content":"여행을 시작해 보겠습니다. 지금 어디에 있고, 어디로 가실 예정인가요?"}]
    st.session_state.events=[]
    st.session_state.turns=[]
    st.session_state.journey=blank_journey()
    st.session_state.location=None
    st.session_state.last_location_key=None
    st.session_state.selected_history_id=None

def journey_time_label(payload):
    j=payload.get("journey") or {}
    events=payload.get("events") or []
    start=j.get("created_at") or j.get("started_at") or (events[0].get("occurred_at") if events else None)
    end=j.get("ended_at") or (events[-1].get("occurred_at") if events else None)
    try:
        s=datetime.fromisoformat(start) if start else None
        e=datetime.fromisoformat(end) if end else None
        if s and e and s.date()==e.date():
            return f"{s:%Y-%m-%d %H:%M}–{e:%H:%M}"
        if s:
            return f"{s:%Y-%m-%d %H:%M}"
    except Exception:
        pass
    return "시간 미상"

def journey_route_label(payload):
    j=payload.get("journey") or {}
    origin=j.get("origin") or "현재 위치"
    destination=j.get("destination") or "목적지 미정"
    return f"{origin} → {destination}"

def snapshot():
    j=st.session_state.journey
    return {"captured_at":now_iso(),"journey_state":j["state"],"origin":j["origin"],"destination":j["destination"],"transport":j["transport"],"spend_krw":j["spend_krw"],"location":st.session_state.location}

def add_event(event_type, subtype="", detail="", explicit_or_inferred="explicit", confidence=1.0):
    if event_type not in ALLOWED_TYPES:
        return
    prev=st.session_state.events[-1]["event_id"] if st.session_state.events else None
    st.session_state.events.append({
        "event_id":str(uuid.uuid4()),
        "journey_id":st.session_state.journey["id"],
        "occurred_at":now_iso(),
        "recorded_at":now_iso(),
        "type":event_type,
        "subtype":subtype,
        "detail":detail,
        "explicit_or_inferred":explicit_or_inferred if explicit_or_inferred in {"explicit","inferred"} else "inferred",
        "confidence":max(0.0,min(float(confidence or 0),1.0)),
        "location":st.session_state.location,
        "previous_event_id":prev,
        "context":snapshot(),
    })

def normalize_location(raw):
    if not raw or not isinstance(raw,dict) or "error" in raw:
        return None
    c=raw.get("coords",raw)
    if c.get("latitude") is None or c.get("longitude") is None:
        return None
    return {"latitude":float(c["latitude"]),"longitude":float(c["longitude"]),"accuracy":c.get("accuracy"),"timestamp":raw.get("timestamp") or now_iso()}

def update_location(raw):
    loc=normalize_location(raw)
    if not loc:
        return
    st.session_state.location=loc
    key=f'{loc["latitude"]:.5f},{loc["longitude"]:.5f}'
    if key!=st.session_state.last_location_key:
        st.session_state.last_location_key=key
        if st.session_state.journey["state"]=="MOVING":
            add_event("Movement","location_update",key,"explicit",1.0)

def parse_money(text):
    t=text.replace(",","")
    m=re.search(r"(\d+(?:\.\d+)?)\s*만\s*원",t)
    if m:
        return int(float(m.group(1))*10000)
    m=re.search(r"(\d{3,})\s*원",t)
    return int(m.group(1)) if m else 0

def fallback(text):
    j=st.session_state.journey
    events=[]
    updates={"state":None,"origin":None,"destination":None,"transport":None,"spend_delta":0}
    reply="알겠습니다."

    m=re.search(r"(.{2,30}?)(?:까지|에)\s*(?:갈|가려|가고|가자)",text)
    if m:
        dest=m.group(1).strip()
        updates["destination"]=dest
        updates["state"]="PLANNING"
        events.append({"type":"Intent","subtype":"destination","detail":dest,"explicit_or_inferred":"explicit","confidence":1.0})
        reply=f"{dest}로 가시는군요. 어떤 교통수단으로 이동하시나요?"

    for k,v in {"자가용":"car","택시":"taxi","버스":"bus","도보":"walk","걸어":"walk"}.items():
        if k in text:
            updates["transport"]=v
            events.append({"type":"Choice","subtype":"transport","detail":v,"explicit_or_inferred":"explicit","confidence":1.0})
            reply="확인했습니다. 지금 바로 출발하시나요?"
            break

    if "출발" in text:
        updates["state"]="MOVING"
        events.append({"type":"Movement","subtype":"start","detail":f'{j.get("origin") or "현재 위치"} → {j.get("destination") or "목적지"}',"explicit_or_inferred":"explicit","confidence":1.0})
        reply="출발로 기록했습니다. 이동 중 궁금한 것이 있으면 그대로 말씀하세요."

    if "주차" in text and any(k in text for k in ["정보","찾","어디","가능"]):
        events.append({"type":"Intent","subtype":"parking_information","detail":text,"explicit_or_inferred":"explicit","confidence":1.0})
        reply="주차 정보를 알고 싶다는 의도로 기록했습니다. 실시간 주차정보원은 아직 연결 전입니다."

    if "도착" in text:
        updates["state"]="STAYING"
        events += [
            {"type":"Movement","subtype":"end","detail":j.get("destination") or "도착","explicit_or_inferred":"explicit","confidence":1.0},
            {"type":"Stay","subtype":"start","detail":j.get("destination") or "현재 장소","explicit_or_inferred":"explicit","confidence":1.0},
        ]
        reply="도착으로 기록했습니다. 여기서 무엇을 하실지 편하게 말씀하세요."

    amount=parse_money(text)
    if amount:
        updates["spend_delta"]=amount
        events.append({"type":"Transaction","subtype":"self_report","detail":f"{amount:,} KRW","explicit_or_inferred":"explicit","confidence":1.0})
        reply=f"{amount:,}원 지출로 기록했습니다."

    if any(k in text for k in ["여행 종료","오늘 끝","여행 끝"]):
        updates["state"]="ENDED"
        reply="오늘 Journey를 종료했습니다. 관리자 탭에서 Context Trajectory를 확인할 수 있습니다."

    if not events and j["state"]=="MOVING":
        events.append({"type":"Intent","subtype":"free_question","detail":text,"explicit_or_inferred":"explicit","confidence":0.9})

    return {"assistant_text":reply,"events":events,"state_updates":updates}

def traveler_reply(text):
    key=secret("OPENAI_API_KEY")
    if not key or OpenAI is None:
        return fallback(text)["assistant_text"]

    context={
        "journey":st.session_state.journey,
        "location":st.session_state.location,
        "conversation":st.session_state.messages[-30:],
        "recent_events":st.session_state.events[-30:],
        "user_message":text,
    }
    client=OpenAI(api_key=key)
    primary_model=secret("TRAVELER_MODEL","gpt-6.1-sol")
    try:
        response=client.responses.create(
            model=primary_model,
            instructions=TRAVELER_PROMPT,
            tools=[
                {
                    "type":"web_search",
                    "search_context_size":"medium",
                    "user_location":{"type":"approximate","country":"KR"},
                }
            ],
            tool_choice="auto",
            input=json.dumps(context,ensure_ascii=False),
        )
        answer=response.output_text.strip()
        if not answer:
            raise ValueError("empty traveler response")
        return answer
    except Exception as e:
        # If the higher-capability traveler model is unavailable to this API project,
        # retry with the low-cost model already configured for the pilot.
        try:
            response=client.responses.create(
                model=secret("OPENAI_MODEL","gpt-6-luna"),
                instructions=TRAVELER_PROMPT,
                tools=[
                    {
                        "type":"web_search",
                        "search_context_size":"medium",
                        "user_location":{"type":"approximate","country":"KR"},
                    }
                ],
                tool_choice="auto",
                input=json.dumps(context,ensure_ascii=False),
            )
            answer=response.output_text.strip()
            if answer:
                return answer
        except Exception:
            pass
        st.toast(f"AI 안내 호출 실패 — 규칙 기반 응답: {type(e).__name__}")
        return fallback(text)["assistant_text"]

def observe_turn(text, assistant_text):
    key=secret("OPENAI_API_KEY")
    if not key or OpenAI is None:
        return fallback(text)

    payload={
        "journey":st.session_state.journey,
        "location":st.session_state.location,
        "recent_events":st.session_state.events[-30:],
        "user_message":text,
        "assistant_message":assistant_text,
    }
    try:
        client=OpenAI(api_key=key)
        response=client.responses.create(
            model=secret("OBSERVER_MODEL",secret("OPENAI_MODEL","gpt-6-luna")),
            instructions=OBSERVER_PROMPT,
            input=json.dumps(payload,ensure_ascii=False),
            text={
                "format":{
                    "type":"json_schema",
                    "name":"tourism_context_result",
                    "schema":OUTPUT_SCHEMA,
                    "strict":True
                }
            },
        )
        result=json.loads(response.output_text.strip())
        if not isinstance(result,dict) or "events" not in result:
            raise ValueError("invalid observer output")
        return result
    except Exception as e:
        st.toast(f"Event 추출 실패 — 규칙 기반 보정: {type(e).__name__}")
        return fallback(text)

def apply_result(result):
    j=st.session_state.journey
    j.setdefault("created_at",now_iso())
    j.setdefault("notion_synced_at",None)
    j.setdefault("notion_journey_page_id",None)
    j.setdefault("notion_report_page_id",None)
    u=result.get("state_updates") or {}
    state=u.get("state")
    if state in ALLOWED_STATES:
        if state=="MOVING" and not j["started_at"]:
            j["started_at"]=now_iso()
        if state=="ENDED":
            j["ended_at"]=now_iso()
        j["state"]=state
    for k in ("origin","destination","transport"):
        if u.get(k):
            j[k]=u[k]
    try:
        delta=int(u.get("spend_delta") or 0)
    except Exception:
        delta=0
    if delta>0:
        j["spend_krw"]+=delta
    for e in result.get("events") or []:
        if isinstance(e,dict):
            add_event(e.get("type"),e.get("subtype",""),e.get("detail",""),e.get("explicit_or_inferred","inferred"),e.get("confidence",0.5))

def rich(value):
    return {"rich_text":[{"type":"text","text":{"content":str(value)[:1900]}}]}

def title(value):
    return {"title":[{"type":"text","text":{"content":str(value)[:1900]}}]}

def sync_notion_checkpoint(finalize=False):
    token=secret("NOTION_TOKEN")
    journey_ds=secret("NOTION_JOURNEY_DATA_SOURCE_ID")
    report_ds=secret("NOTION_REPORT_DATA_SOURCE_ID")
    raw_log_ds=secret("NOTION_RAW_LOG_DATA_SOURCE_ID",DEFAULT_RAW_LOG_DS_ID)
    if not token or not journey_ds or not report_ds or NotionClient is None:
        raise RuntimeError("Notion Secrets가 설정되지 않았습니다.")

    notion=NotionClient(auth=token)
    j=st.session_state.journey
    d=datetime.now().date().isoformat()
    summary=f'{j.get("origin") or "현재 위치"} → {j.get("destination") or "미정"} / {j.get("transport") or "미정"} / {len(st.session_state.events)} events'

    props={
        "Journey":title(f"Journey {d} · {j['id'][:8]}"),
        "Date":{"date":{"start":d}},
        "Origin":rich(j.get("origin") or ""),
        "Destination":rich(j.get("destination") or ""),
        "Spend KRW":{"number":j.get("spend_krw") or 0},
        "Event Count":{"number":len(st.session_state.events)},
        "Summary":rich(summary),
    }
    if j.get("state") in {"PLANNING","MOVING","STAYING","ENDED"}:
        props["State"]={"select":{"name":j["state"]}}
    if j.get("transport") in {"car","taxi","bus","walk","other"}:
        props["Transport"]={"select":{"name":j["transport"]}}

    if j.get("notion_journey_page_id"):
        notion.pages.update(page_id=j["notion_journey_page_id"],properties=props)
    else:
        page=notion.pages.create(parent={"data_source_id":journey_ds},properties=props)
        j["notion_journey_page_id"]=page.get("id")

    # Save every conversation turn as raw evidence. Successfully saved turns are
    # marked locally so a rerun does not create duplicate rows.
    if raw_log_ds:
        for turn in st.session_state.turns:
            if turn.get("notion_synced"):
                continue
            loc=turn.get("location") or {}
            state=(turn.get("journey") or {}).get("state") or "IDLE"
            destination=(turn.get("journey") or {}).get("destination") or ""
            occurred=turn.get("occurred_at") or now_iso()
            event_json=json.dumps(turn.get("events") or [],ensure_ascii=False,separators=(",",":"))
            raw_props={
                "Log":title(f"Turn {occurred[0:19]} · {j['id'][:8]}"),
                "Journey ID":rich(j["id"]),
                "Occurred At":{"date":{"start":occurred}},
                "User Message":rich(turn.get("user_message") or ""),
                "Assistant Message":rich(turn.get("assistant_message") or ""),
                "Events JSON":rich(event_json),
                "State":{"select":{"name":state if state in ALLOWED_STATES else "IDLE"}},
                "Destination":rich(destination),
            }
            if loc.get("latitude") is not None:
                raw_props["Latitude"]={"number":float(loc["latitude"])}
            if loc.get("longitude") is not None:
                raw_props["Longitude"]={"number":float(loc["longitude"])}
            raw_page=notion.pages.create(parent={"data_source_id":raw_log_ds},properties=raw_props)
            turn["notion_synced"]=True
            turn["notion_page_id"]=raw_page.get("id")

    if finalize and not j.get("notion_report_page_id"):
        findings=" / ".join(f'{e["type"]}:{e["subtype"]}' for e in st.session_state.events[-20:])
        report=notion.pages.create(
            parent={"data_source_id":report_ds},
            properties={
                "Report":title(f"Session Report · {d} · {j['id'][:8]}"),
                "Date":{"date":{"start":d}},
                "Type":{"select":{"name":"Session"}},
                "Journey ID":rich(j["id"]),
                "Summary":rich(summary),
                "Findings":rich(findings or "No structured events"),
                "Next Action":rich("실제 사용 후 Event 추출·시공간 연결 오류 검토"),
            },
        )
        j["notion_report_page_id"]=report.get("id")

    j["notion_synced_at"]=now_iso()

def auto_sync_notion_checkpoint():
    ready=all([
        secret("NOTION_TOKEN"),
        secret("NOTION_JOURNEY_DATA_SOURCE_ID"),
        secret("NOTION_REPORT_DATA_SOURCE_ID"),
    ])
    if not ready or not payload_has_activity(state_payload()):
        return
    try:
        sync_notion_checkpoint(finalize=st.session_state.journey.get("state")=="ENDED")
    except Exception as e:
        # Browser storage remains the fallback; the next successful turn can retry.
        st.toast(f"Notion 자동저장 실패 — 기기에는 보관됨: {type(e).__name__}")

def rubric():
    counts={}
    for e in st.session_state.events:
        key=(e["type"],e["subtype"],e["detail"])
        counts[key]=counts.get(key,0)+1
    repeated=[(k,v) for k,v in counts.items() if v>=3]
    if not repeated:
        return "근거 부족","현재 세션만으로 안정적 성향을 판정하지 않습니다."
    return "반복 패턴","; ".join(f"{k[0]}/{k[1]} ×{v}" for k,v in repeated[:5])

init_state()
load_browser_store()

def admin_access_gate(key_suffix):
    app_passcode=secret("APP_PASSCODE")
    if st.session_state.get("admin_authenticated"):
        return True
    if not app_passcode:
        st.warning("관리자 비밀번호가 설정되지 않았습니다.")
        return False
    st.caption("개인 기록 보호를 위해 관리자 영역만 잠겨 있습니다.")
    entered=st.text_input("관리자 비밀번호",type="password",key=f"admin_passcode_{key_suffix}")
    if st.button("관리자 열기",use_container_width=True,key=f"admin_open_{key_suffix}"):
        if entered==app_passcode:
            st.session_state.admin_authenticated=True
            st.rerun()
        else:
            st.error("비밀번호가 맞지 않습니다.")
    return False

update_location(get_geolocation())

st.title("Context Tourism Pilot")
st.caption("1인용 관광 Context 관찰·분석 실험 · Streamlit v0.6")

j=st.session_state.journey
a,b,c=st.columns(3)
a.metric("Journey",j["state"])
b.metric("목적지",j["destination"] or "—")
c.metric("지출",f'{j["spend_krw"]:,}원')
ai_ready=bool(secret("OPENAI_API_KEY"))
notion_ready=all([secret("NOTION_TOKEN"),secret("NOTION_JOURNEY_DATA_SOURCE_ID"),secret("NOTION_REPORT_DATA_SOURCE_ID")])
st.caption(f'AI: {"연결됨" if ai_ready else "규칙 모드"} · 웹검색: {"자동" if ai_ready else "꺼짐"} · Notion: {"자동저장" if notion_ready else "대기"} · Journey 저장: 자동')
if st.session_state.get("admin_authenticated"):
    if st.button("관리자 잠금",key="lock_admin"):
        st.session_state.admin_authenticated=False
        st.rerun()

chat_tab,history_tab,admin_tab=st.tabs(["대화","지난 Journey","관리자"])

with chat_tab:
    if st.session_state.saved_active and not payload_has_activity(state_payload()):
        st.info("중간에 멈춘 Journey가 저장되어 있습니다. '지난 Journey' 탭에서 이어갈 수 있습니다.")
    if st.session_state.location:
        loc=st.session_state.location
        st.caption(f'현재 위치: {loc["latitude"]:.5f}, {loc["longitude"]:.5f} · 정확도 {loc.get("accuracy") or "—"}m')
    else:
        st.caption("위치 권한을 허용하면 현재 위치를 Journey Context에 연결합니다.")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt=st.chat_input("여행 중 궁금한 것을 말하세요")
    if prompt:
        st.session_state.messages.append({"role":"user","content":prompt})
        assistant_text=traveler_reply(prompt)
        result=observe_turn(prompt,assistant_text)
        apply_result(result)
        st.session_state.messages.append({"role":"assistant","content":assistant_text or "확인했습니다."})
        st.session_state.turns.append({
            "turn_id":str(uuid.uuid4()),
            "occurred_at":now_iso(),
            "user_message":prompt,
            "assistant_message":assistant_text or "확인했습니다.",
            "events":result.get("events") or [],
            "journey":dict(st.session_state.journey),
            "location":dict(st.session_state.location) if st.session_state.location else None,
            "notion_synced":False,
        })
        auto_sync_notion_checkpoint()
        persist_browser_store()
        st.rerun()

with history_tab:
    if not admin_access_gate("history"):
        st.info("비밀번호를 입력하면 지난 Journey 기록을 볼 수 있습니다.")
    else:
        st.subheader("지난 Journey")

        items=[]
        if st.session_state.saved_active:
            items.append(("active",st.session_state.saved_active))
        for h in st.session_state.journey_history:
            items.append(("history",h))

        if not items:
            st.info("저장된 Journey가 아직 없습니다.")
        else:
            seen=set()
            for kind,payload in items:
                jh=payload.get("journey") or {}
                jid=jh.get("id")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                st.markdown(f"**{journey_time_label(payload)}**  \n{journey_route_label(payload)}")
                cols=st.columns([1,1,4])
                if cols[0].button("보기",key=f"view_{jid}"):
                    st.session_state.selected_history_id=jid
                if jh.get("state")!="ENDED":
                    if cols[1].button("이어가기",key=f"resume_{jid}"):
                        resume_payload(payload)
                        persist_browser_store()
                        st.rerun()
                st.divider()

            selected=None
            sid=st.session_state.selected_history_id
            if sid:
                for _,payload in items:
                    if (payload.get("journey") or {}).get("id")==sid:
                        selected=payload
                        break

            if selected:
                sj=selected.get("journey") or {}
                st.subheader("Journey 상세")
                st.caption(f'{journey_time_label(selected)} · {journey_route_label(selected)}')
                st.write(f'상태: **{sj.get("state") or "—"}**')
                events=selected.get("events") or []
                if events:
                    labels=[]
                    for idx,e in enumerate(events):
                        t=(e.get("occurred_at") or "")[11:19] or "시간"
                        labels.append(f'{idx+1}. {t} · {e.get("type","Event")} · {e.get("subtype","")}')
                    pick=st.selectbox("이벤트 시간 선택",options=list(range(len(events))),format_func=lambda i:labels[i],key=f"event_pick_{sid}")
                    ev=events[pick]
                    st.write(ev.get("detail") or "")
                    with st.expander("이 시점의 맥락 보기"):
                        st.json(ev.get("context") or {})
                else:
                    st.caption("저장된 Event가 없습니다.")

with admin_tab:
    if not admin_access_gate("admin"):
        st.info("비밀번호를 입력하면 관리자 분석 화면을 볼 수 있습니다.")
    else:
        st.subheader("Context Trajectory")
        if st.session_state.events:
            rows=[]
            for e in st.session_state.events:
                loc=e.get("location") or {}
                rows.append({"time":e["occurred_at"][11:19],"type":e["type"],"subtype":e["subtype"],"detail":e["detail"],"lat":loc.get("latitude"),"lon":loc.get("longitude"),"source":e["explicit_or_inferred"],"confidence":e["confidence"]})
            st.dataframe(rows,use_container_width=True,hide_index=True)
        else:
            st.info("아직 구조화된 Event가 없습니다.")

        status,evidence=rubric()
        st.subheader("Rubric")
        st.write(f"**{status}**")
        st.caption(evidence)

        st.subheader("Notion")
        if notion_ready:
            if st.button("현재 Journey를 Notion에 동기화",use_container_width=True):
                try:
                    sync_notion_checkpoint(finalize=st.session_state.journey.get("state")=="ENDED")
                    persist_browser_store()
                    st.success("Journey와 Raw Log를 Notion에 동기화했습니다.")
                except Exception as e:
                    st.error(f"Notion 저장 실패: {e}")
        else:
            st.warning("Notion Secrets 미설정. Streamlit Secrets에 연결값을 넣으면 활성화됩니다.")

        export={"journey":st.session_state.journey,"location":st.session_state.location,"events":st.session_state.events,"messages":st.session_state.messages,"turns":st.session_state.turns}
        st.download_button("JSON 내보내기",data=json.dumps(export,ensure_ascii=False,indent=2),file_name=f'context-journey-{j["id"][:8]}.json',mime="application/json",use_container_width=True)

        persist_browser_store()

        if st.button("새 Journey 시작",use_container_width=True):
            current=state_payload()
            if payload_has_activity(current):
                if (current.get("journey") or {}).get("state")=="ENDED":
                    upsert_history(current)
                else:
                    st.session_state.saved_active=current
            reset_current_journey()
            persist_browser_store()
            st.rerun()
