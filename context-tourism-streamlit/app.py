import json
import re
import uuid
from datetime import datetime, timezone

import streamlit as st
from streamlit_js_eval import get_geolocation

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
div[data-testid="stMetric"]{background:#f5f5f2;padding:10px;border-radius:12px}
</style>
""", unsafe_allow_html=True)

ALLOWED_TYPES={"Intent","Deliberation","Exposure","Choice","Movement","Stay","Transaction"}
ALLOWED_STATES={"IDLE","PLANNING","MOVING","STAYING","ENDED"}

SYSTEM_PROMPT="""너는 1인용 관광 Context Pilot의 Traveler Agent이자 Observer다.
사용자의 여행을 자연스럽게 돕되, 대화에서 관찰 가능한 사실만 구조화한다.

관찰축: Intent → Deliberation → Exposure → Choice → Movement → Stay → Transaction

규칙:
1. 단일 행동을 성향으로 일반화하지 않는다.
2. 사용자 직접 진술과 추론을 구분한다.
3. 실제 정보를 제공하지 않았다면 Exposure를 만들지 않는다.
4. 현재 Journey의 시간·공간 맥락을 유지한다.
5. 필요한 정보가 빠졌으면 짧게 질문한다.
6. 반드시 아래 JSON 객체 하나만 출력한다. 코드펜스 금지.

{"assistant_text":"응답","events":[{"type":"Intent|Deliberation|Exposure|Choice|Movement|Stay|Transaction","subtype":"식별자","detail":"관찰 내용","explicit_or_inferred":"explicit|inferred","confidence":0.0}],"state_updates":{"state":"IDLE|PLANNING|MOVING|STAYING|ENDED|null","origin":null,"destination":null,"transport":null,"spend_delta":0}}
"""

def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def secret(name, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

def init_state():
    defaults={
        "messages":[{"role":"assistant","content":"여행을 시작해 보겠습니다. 지금 어디에 있고, 어디로 가실 예정인가요?"}],
        "events":[],
        "journey":{"id":str(uuid.uuid4()),"state":"IDLE","origin":None,"destination":None,"transport":None,"spend_krw":0,"started_at":None,"ended_at":None},
        "location":None,
        "last_location_key":None,
    }
    for k,v in defaults.items():
        if k not in st.session_state:
            st.session_state[k]=v

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

def interpret(text):
    key=secret("OPENAI_API_KEY")
    if not key or OpenAI is None:
        return fallback(text)
    payload={"journey":st.session_state.journey,"location":st.session_state.location,"recent_messages":st.session_state.messages[-8:],"user_message":text}
    try:
        client=OpenAI(api_key=key)
        response=client.responses.create(
            model=secret("OPENAI_MODEL","gpt-6-luna"),
            instructions=SYSTEM_PROMPT,
            input=json.dumps(payload,ensure_ascii=False),
        )
        raw=response.output_text.strip()
        raw=re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$","",raw)
        result=json.loads(raw)
        if not isinstance(result,dict) or "assistant_text" not in result:
            raise ValueError("invalid model output")
        return result
    except Exception as e:
        st.toast(f"LLM 호출 실패 — 규칙 기반 처리: {type(e).__name__}")
        return fallback(text)

def apply_result(result):
    j=st.session_state.journey
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

def sync_notion():
    token=secret("NOTION_TOKEN")
    journey_ds=secret("NOTION_JOURNEY_DATA_SOURCE_ID")
    report_ds=secret("NOTION_REPORT_DATA_SOURCE_ID")
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
        "State":{"select":{"name":j.get("state") or "ENDED"}},
        "Spend KRW":{"number":j.get("spend_krw") or 0},
        "Event Count":{"number":len(st.session_state.events)},
        "Summary":rich(summary),
    }
    if j.get("transport") in {"car","taxi","bus","walk","other"}:
        props["Transport"]={"select":{"name":j["transport"]}}
    notion.pages.create(parent={"data_source_id":journey_ds},properties=props)

    findings=" / ".join(f'{e["type"]}:{e["subtype"]}' for e in st.session_state.events[-20:])
    notion.pages.create(
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
update_location(get_geolocation())

st.title("Context Tourism Pilot")
st.caption("1인용 관광 Context 관찰·분석 실험 · Streamlit v0.3")

j=st.session_state.journey
a,b,c=st.columns(3)
a.metric("Journey",j["state"])
b.metric("목적지",j["destination"] or "—")
c.metric("지출",f'{j["spend_krw"]:,}원')

chat_tab,admin_tab=st.tabs(["대화","관리자"])

with chat_tab:
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
        result=interpret(prompt)
        apply_result(result)
        st.session_state.messages.append({"role":"assistant","content":result.get("assistant_text") or "확인했습니다."})
        st.rerun()

with admin_tab:
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
    notion_ready=all([secret("NOTION_TOKEN"),secret("NOTION_JOURNEY_DATA_SOURCE_ID"),secret("NOTION_REPORT_DATA_SOURCE_ID")])
    if notion_ready:
        if st.button("현재 Journey를 Notion에 저장",use_container_width=True):
            try:
                sync_notion()
                st.success("Journey DB와 Pilot Reports에 저장했습니다.")
            except Exception as e:
                st.error(f"Notion 저장 실패: {e}")
    else:
        st.warning("Notion Secrets 미설정. Streamlit Secrets에 연결값을 넣으면 활성화됩니다.")

    export={"journey":st.session_state.journey,"location":st.session_state.location,"events":st.session_state.events,"messages":st.session_state.messages}
    st.download_button("JSON 내보내기",data=json.dumps(export,ensure_ascii=False,indent=2),file_name=f'context-journey-{j["id"][:8]}.json',mime="application/json",use_container_width=True)

    if st.button("새 Journey 시작",use_container_width=True):
        for key in ["messages","events","journey","location","last_location_key"]:
            st.session_state.pop(key,None)
        st.rerun()
