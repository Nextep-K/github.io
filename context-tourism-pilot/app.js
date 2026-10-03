import { CONFIG } from './config.js';
import { hasRemoteApi, chatWithRemote, syncJourneyToNotion } from './api-client.js';

const DB_NAME = 'context-tourism-pilot';
const DB_VERSION = 2;
const STORE_EVENTS = 'events';
const STORE_STATE = 'state';
const STORE_SYNC = 'sync_queue';

const INITIAL_STATE = {
  journeyId: null,
  journeyState: 'IDLE',
  origin: null,
  destination: null,
  transport: null,
  currentLocation: null,
  currentPlaceName: null,
  cumulativeSpend: 0,
  lastEventId: null,
  startedAt: null,
  arrivedAt: null,
  movementWatchActive: false,
  lastPersistedLocation: null,
  lastPersistedLocationAt: null
};

let state = { ...INITIAL_STATE };
let db;
let watchId = null;

const els = {
  messages: document.querySelector('#messages'),
  input: document.querySelector('#messageInput'),
  composer: document.querySelector('#composer'),
  journeyState: document.querySelector('#journeyState'),
  placeLabel: document.querySelector('#placeLabel'),
  destinationLabel: document.querySelector('#destinationLabel'),
  locationBtn: document.querySelector('#locationBtn'),
  timeline: document.querySelector('#timeline'),
  todaySummary: document.querySelector('#todaySummary'),
  insightPanel: document.querySelector('#insightPanel'),
  syncPanel: document.querySelector('#syncPanel'),
  adminView: document.querySelector('#adminView'),
  chatView: document.querySelector('#chatView')
};

function uid(prefix='evt') {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
}

function nowIso() { return new Date().toISOString(); }
function localTime(iso) { return new Date(iso).toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', hour12:false}); }

async function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const d = req.result;
      if (!d.objectStoreNames.contains(STORE_EVENTS)) d.createObjectStore(STORE_EVENTS, { keyPath: 'event_id' });
      if (!d.objectStoreNames.contains(STORE_STATE)) d.createObjectStore(STORE_STATE, { keyPath: 'key' });
      if (!d.objectStoreNames.contains(STORE_SYNC)) d.createObjectStore(STORE_SYNC, { keyPath: 'sync_id' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(store, mode='readonly') { return db.transaction(store, mode).objectStore(store); }

async function putState() {
  return new Promise((resolve, reject) => {
    const req = tx(STORE_STATE, 'readwrite').put({ key:'app_state', value:state });
    req.onsuccess = resolve;
    req.onerror = () => reject(req.error);
  });
}

async function loadState() {
  return new Promise((resolve) => {
    const req = tx(STORE_STATE).get('app_state');
    req.onsuccess = () => resolve(req.result?.value || null);
    req.onerror = () => resolve(null);
  });
}

async function getEvents() {
  return new Promise((resolve, reject) => {
    const req = tx(STORE_EVENTS).getAll();
    req.onsuccess = () => resolve(req.result.sort((a,b) => a.occurred_at.localeCompare(b.occurred_at)));
    req.onerror = () => reject(req.error);
  });
}

async function addSyncItem(item) {
  return new Promise((resolve, reject) => {
    const req = tx(STORE_SYNC, 'readwrite').put(item);
    req.onsuccess = resolve;
    req.onerror = () => reject(req.error);
  });
}

async function getSyncItems() {
  return new Promise((resolve, reject) => {
    const req = tx(STORE_SYNC).getAll();
    req.onsuccess = () => resolve(req.result.sort((a,b) => a.created_at.localeCompare(b.created_at)));
    req.onerror = () => reject(req.error);
  });
}

async function deleteSyncItem(syncId) {
  return new Promise((resolve, reject) => {
    const req = tx(STORE_SYNC, 'readwrite').delete(syncId);
    req.onsuccess = resolve;
    req.onerror = () => reject(req.error);
  });
}

function snapshot() {
  return {
    journey_state: state.journeyState,
    origin: state.origin,
    destination: state.destination,
    transport: state.transport,
    cumulative_spend: state.cumulativeSpend,
    current_place: state.currentPlaceName,
    current_location: state.currentLocation ? { ...state.currentLocation } : null
  };
}

async function addEvent(eventType, subtype, payload={}, opts={}) {
  const event = {
    event_id: uid(),
    journey_id: state.journeyId,
    segment_id: payload.segment_id || null,
    occurred_at: opts.occurred_at || nowIso(),
    recorded_at: nowIso(),
    event_type: eventType,
    event_subtype: subtype || null,
    latitude: state.currentLocation?.latitude ?? null,
    longitude: state.currentLocation?.longitude ?? null,
    location_accuracy: state.currentLocation?.accuracy ?? null,
    place_name: state.currentPlaceName,
    previous_event_id: state.lastEventId,
    parent_event_id: payload.parent_event_id || null,
    explicit_or_inferred: opts.inferred ? 'inferred' : 'explicit',
    source: opts.source || 'conversation',
    confidence: opts.confidence || 'user_confirmed',
    context_snapshot: snapshot(),
    payload
  };

  await new Promise((resolve, reject) => {
    const req = tx(STORE_EVENTS, 'readwrite').put(event);
    req.onsuccess = resolve;
    req.onerror = () => reject(req.error);
  });
  state.lastEventId = event.event_id;
  await putState();
  return event;
}

function addMessage(role, text) {
  const div = document.createElement('div');
  div.className = `message ${role}`;
  div.textContent = text;
  els.messages.appendChild(div);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function renderState() {
  els.journeyState.textContent = state.journeyState;
  els.placeLabel.textContent = state.currentPlaceName || (state.currentLocation ? '좌표 확인됨' : '미확인');
  els.destinationLabel.textContent = state.destination || '—';
}

async function ensureJourney() {
  if (state.journeyId && state.journeyState !== 'ENDED') return;
  state = { ...INITIAL_STATE, journeyId: uid('journey'), journeyState:'PLANNING', startedAt:nowIso() };
  await putState();
  await addEvent('Journey', 'START', {}, { source:'system' });
}

function geolocationOptions() {
  return {
    enableHighAccuracy: CONFIG.location.enableHighAccuracy,
    timeout: CONFIG.location.timeoutMs,
    maximumAge: CONFIG.location.maximumAgeMs
  };
}

async function updateLocationFromPosition(pos, {persist=false, subtype='LOCATION_UPDATE'}={}) {
  state.currentLocation = {
    latitude: pos.coords.latitude,
    longitude: pos.coords.longitude,
    accuracy: pos.coords.accuracy,
    speed: Number.isFinite(pos.coords.speed) ? pos.coords.speed : null,
    heading: Number.isFinite(pos.coords.heading) ? pos.coords.heading : null
  };
  state.currentPlaceName = state.currentPlaceName || '현재 위치';
  await putState();
  renderState();
  if (persist && state.journeyId) {
    await addEvent('Context', subtype, { ...state.currentLocation }, {
      source:'device_geolocation', inferred:true, confidence:'sensor_observed'
    });
    state.lastPersistedLocation = { ...state.currentLocation };
    state.lastPersistedLocationAt = nowIso();
    await putState();
  }
  return state.currentLocation;
}

async function captureLocation({silent=false, persist=true}={}) {
  if (!navigator.geolocation) {
    if (!silent) addMessage('assistant', '이 기기에서는 위치 기능을 사용할 수 없습니다.');
    return null;
  }
  return new Promise(resolve => {
    navigator.geolocation.getCurrentPosition(async pos => {
      const location = await updateLocationFromPosition(pos, {persist});
      if (!silent) addMessage('assistant', `현재 위치를 갱신했습니다. 정확도는 약 ${Math.round(pos.coords.accuracy)}m입니다.`);
      resolve(location);
    }, err => {
      if (!silent) addMessage('assistant', `위치 확인을 하지 못했습니다. (${err.message})`);
      resolve(null);
    }, geolocationOptions());
  });
}

function haversineM(a, b) {
  if (!a || !b) return Infinity;
  const R = 6371000;
  const toRad = d => d * Math.PI / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const h = Math.sin(dLat/2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon/2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function shouldPersistPosition(nextLocation) {
  if (!state.lastPersistedLocation || !state.lastPersistedLocationAt) return true;
  const elapsed = Date.now() - new Date(state.lastPersistedLocationAt).getTime();
  const distance = haversineM(state.lastPersistedLocation, nextLocation);
  return elapsed >= CONFIG.location.minPersistIntervalMs || distance >= CONFIG.location.minPersistDistanceM;
}

function startMovementWatch() {
  if (!navigator.geolocation || watchId !== null) return;
  state.movementWatchActive = true;
  watchId = navigator.geolocation.watchPosition(async pos => {
    const nextLocation = {
      latitude: pos.coords.latitude,
      longitude: pos.coords.longitude,
      accuracy: pos.coords.accuracy,
      speed: Number.isFinite(pos.coords.speed) ? pos.coords.speed : null,
      heading: Number.isFinite(pos.coords.heading) ? pos.coords.heading : null
    };
    const persist = shouldPersistPosition(nextLocation);
    await updateLocationFromPosition(pos, {persist, subtype:'MOVEMENT_LOCATION'});
  }, err => {
    console.warn('watchPosition error', err);
  }, geolocationOptions());
  putState().catch(console.error);
}

function stopMovementWatch() {
  if (watchId !== null && navigator.geolocation) navigator.geolocation.clearWatch(watchId);
  watchId = null;
  state.movementWatchActive = false;
  putState().catch(console.error);
}

function extractAmount(text) {
  const normalized = text.replace(/,/g,'');
  const m = normalized.match(/(\d+(?:\.\d+)?)\s*(만|천)?\s*원/);
  if (!m) return null;
  let amount = Number(m[1]);
  if (m[2] === '만') amount *= 10000;
  if (m[2] === '천') amount *= 1000;
  return Math.round(amount);
}

function inferRoute(text) {
  const compact = text.replace(/\s+/g, ' ').trim();
  const patterns = [
    /(.+?)에서\s+(.+?)(?:까지|으로)\s*(?:갈|가려|가고|이동|출발)/,
    /(.+?)에서\s+(.+?)까지$/,
    /출발지(?:는|가)?\s*(.+?)[, ]+목적지(?:는|가)?\s*(.+)$/
  ];
  for (const p of patterns) {
    const m = compact.match(p);
    if (m?.[1] && m?.[2]) return { origin:m[1].trim(), destination:m[2].trim() };
  }
  return null;
}

function inferDestination(text) {
  const patterns = [
    /(.+?)(?:에|으로|까지)\s*(?:갈|가려|가고|이동)/,
    /목적지(?:는|가)?\s*(.+)/
  ];
  for (const p of patterns) {
    const m = text.match(p);
    if (m?.[1]) return m[1].trim().replace(/["']/g,'');
  }
  return null;
}

async function maybeRemoteReply(text) {
  if (!hasRemoteApi()) return null;
  try {
    const recent = (await getEvents()).filter(e => e.journey_id === state.journeyId).slice(-20);
    const response = await chatWithRemote({
      message: text,
      journey_id: state.journeyId,
      context: snapshot(),
      recent_events: recent
    });
    if (response?.reply) {
      await addEvent('Exposure', 'AI_RESPONSE', {
        topic:'open_query',
        response_id:response.response_id || null,
        text:response.reply
      }, { source:'llm_api', inferred:true, confidence:'system_generated' });
      return response.reply;
    }
  } catch (error) {
    console.warn('Remote chat unavailable', error);
  }
  return null;
}

async function handleUserText(raw) {
  const text = raw.trim();
  if (!text) return;
  addMessage('user', text);
  await ensureJourney();
  await addEvent('Conversation', 'USER_MESSAGE', { text }, { source:'chat_ui' });

  const placeNow = text.match(/(?:지금|현재)\s+(.+?)(?:이야|야|에\s*있어|에\s*있다)$/);
  if (placeNow?.[1]) {
    state.currentPlaceName = placeNow[1].trim();
    state.origin = state.origin || state.currentPlaceName;
    await addEvent('Context', 'PLACE_CONFIRMED', { place: state.currentPlaceName });
    await putState(); renderState();
    addMessage('assistant', `현재 장소를 ${state.currentPlaceName}(으)로 기록했습니다. 어디로 가실 예정인가요?`);
    return;
  }

  const route = inferRoute(text);
  if (route) {
    state.origin = route.origin;
    state.currentPlaceName = route.origin;
    state.destination = route.destination;
    state.journeyState = 'PLANNING';
    await addEvent('Intent', 'DESTINATION_VISIT', route);
    await putState(); renderState();
    addMessage('assistant', `${route.origin}에서 ${route.destination}까지 이동하는 계획으로 기록했습니다. 어떤 교통수단으로 이동하시나요?`);
    return;
  }

  if (/(갈까|어떨까|고민|갈지|말지)/.test(text) && !/어디\s*갈까/.test(text)) {
    await addEvent('Deliberation', 'OPTION_CONSIDERATION', { text });
    const remote = await maybeRemoteReply(text);
    addMessage('assistant', remote || '이 내용을 선택 전 고민으로 현재 시간·장소 맥락에 연결해 기록했습니다.');
    return;
  }

  const destination = inferDestination(text);
  if (destination && !/어디\s*갈까/.test(text)) {
    state.destination = destination;
    state.journeyState = 'PLANNING';
    await addEvent('Intent', 'DESTINATION_VISIT', { destination });
    await putState(); renderState();
    addMessage('assistant', `${destination} 방문 의도로 기록했습니다. 어떤 교통수단으로 이동하시나요?`);
    return;
  }

  const transports = [
    [/자가용|차로|자동차/, 'car', '자가용'],
    [/택시/, 'taxi', '택시'],
    [/버스/, 'bus', '버스'],
    [/도보|걸어서/, 'walk', '도보']
  ];
  for (const [pattern, value, label] of transports) {
    if (pattern.test(text)) {
      state.transport = value;
      await addEvent('Choice', 'TRANSPORT', { transport:value });
      await putState(); renderState();
      addMessage('assistant', `${label}(으)로 기록했습니다. 출발할 때 “지금 출발해”라고 말씀해 주세요.`);
      return;
    }
  }

  if (/지금\s*출발|출발해|출발한다/.test(text)) {
    state.journeyState = 'MOVING';
    state.startedAt = nowIso();
    await captureLocation({silent:true, persist:true});
    await addEvent('Movement', 'START', { from:state.origin || state.currentPlaceName, to:state.destination, transport:state.transport });
    startMovementWatch();
    await putState(); renderState();
    addMessage('assistant', `${state.destination || '목적지'}로 이동을 시작한 것으로 기록했습니다. 앱이 열려 있는 동안 위치 변화를 Context Trajectory에 연결합니다. 이동 중 궁금한 것은 그냥 말씀하세요.`);
    return;
  }

  if (/주차/.test(text)) {
    await addEvent('Intent', 'PARKING_INFORMATION', { target:state.destination });
    const remote = await maybeRemoteReply(text);
    if (remote) {
      addMessage('assistant', remote);
    } else {
      await addEvent('System', 'INFO_SOURCE_NOT_CONNECTED', { target:state.destination, topic:'parking' }, { source:'system', inferred:true, confidence:'system_generated' });
      addMessage('assistant', `${state.destination || '목적지'} 주차 정보를 찾고 싶다는 의도로 기록했습니다. 실시간 정보 중계 API를 연결하면 여기서 바로 조회합니다.`);
    }
    return;
  }

  if (/도착했|도착이야|도착$/.test(text)) {
    await captureLocation({silent:true, persist:true});
    stopMovementWatch();
    state.journeyState = 'STAYING';
    state.arrivedAt = nowIso();
    if (state.destination) state.currentPlaceName = state.destination;
    await addEvent('Movement', 'END', { destination:state.destination });
    await addEvent('Stay', 'START', { place:state.destination });
    await putState(); renderState();
    addMessage('assistant', `${state.destination || '현재 장소'} 도착과 체류 시작을 기록했습니다. 여기서 하는 질문도 지금 장소와 시간에 연결됩니다.`);
    return;
  }

  if (/머무는 중|체류/.test(text)) {
    state.journeyState = 'STAYING';
    await addEvent('Stay', 'CONFIRMED', { place:state.currentPlaceName || state.destination });
    await putState(); renderState();
    addMessage('assistant', '현재 장소에 체류 중인 것으로 기록했습니다.');
    return;
  }

  const amount = extractAmount(text);
  if (amount) {
    state.cumulativeSpend += amount;
    await addEvent('Transaction', 'SELF_REPORTED', {
      amount,
      currency:'KRW',
      category:/점심|저녁|식사|밥|카페|커피/.test(text) ? 'food_beverage' : 'unspecified',
      original_text:text
    }, { source:'self_report' });
    await putState(); renderState();
    addMessage('assistant', `${amount.toLocaleString('ko-KR')}원 지출로 기록했습니다. 현재 누적 지출은 ${state.cumulativeSpend.toLocaleString('ko-KR')}원입니다.`);
    return;
  }

  if (/지출했어|돈\s*썼|결제했/.test(text)) {
    await addEvent('Intent', 'TRANSACTION_REPORT', {});
    addMessage('assistant', '무엇에 얼마를 쓰셨나요? 예: “점심 3만8천 원.”');
    return;
  }

  if (/이제\s*어디|다음\s*장소|어디\s*갈까/.test(text)) {
    await addEvent('Intent', 'NEXT_DESTINATION', { current_place:state.currentPlaceName });
    const remote = await maybeRemoteReply(text);
    addMessage('assistant', remote || '다음 장소 추천 의도로 기록했습니다. 외부 관광정보 API가 연결되면 현재 위치·시간·이전 방문·누적 지출·관심사를 함께 사용해 추천합니다.');
    return;
  }

  if (/여행\s*종료|오늘\s*여행\s*끝|끝낼게/.test(text)) {
    stopMovementWatch();
    state.journeyState = 'ENDED';
    await addEvent('Journey', 'END', { cumulative_spend:state.cumulativeSpend });
    await putState(); renderState();
    addMessage('assistant', '오늘 Journey를 종료했습니다. 관리자 화면에서 Context Trajectory와 초기 분석을 확인할 수 있습니다.');
    return;
  }

  await addEvent('Intent', 'OPEN_QUERY', { text });
  const remote = await maybeRemoteReply(text);
  addMessage('assistant', remote || '이 질문을 현재 시간·위치·Journey 맥락에 연결해 기록했습니다. 원격 LLM 중계 API가 연결되면 자유질문 답변도 함께 제공합니다.');
}

function eventSummary(e) {
  const p = e.payload || {};
  if (e.event_type === 'Intent') return p.destination || p.target || p.text || '의도 기록';
  if (e.event_type === 'Choice') return p.transport ? `교통수단: ${p.transport}` : '선택 기록';
  if (e.event_type === 'Movement') return `${p.from || ''}${p.to ? ' → '+p.to : ''}`.trim() || e.event_subtype;
  if (e.event_type === 'Transaction') return `${(p.amount||0).toLocaleString('ko-KR')}원 · ${p.category||'미분류'}`;
  if (e.event_type === 'Conversation') return p.text || '';
  if (e.event_type === 'Exposure') return p.target ? `${p.target} 관련 정보 노출` : p.text || '정보 노출';
  if (e.event_type === 'Stay') return p.place || e.place_name || '체류';
  if (e.event_type === 'Context') return e.latitude ? `위치 ${e.latitude.toFixed(5)}, ${e.longitude.toFixed(5)} · ±${Math.round(e.location_accuracy || 0)}m` : 'Context 갱신';
  return e.event_subtype || '';
}

function basicInsights(events) {
  const insights = [];
  const intents = events.filter(e => e.event_type==='Intent');
  const parking = intents.filter(e => e.event_subtype==='PARKING_INFORMATION');
  const txs = events.filter(e => e.event_type==='Transaction');
  const stays = events.filter(e => e.event_type==='Stay');
  const locations = events.filter(e => e.event_type==='Context' && e.event_subtype==='MOVEMENT_LOCATION');

  if (locations.length) insights.push({ title:'시공간 궤적 관측', body:`이동 중 위치 Event ${locations.length}건. 개별 좌표가 아니라 Journey의 시간순 Context Trajectory로 연결됨.` });
  if (parking.length) insights.push({ title:'이동 중 정보 요구 관측', body:`주차 관련 Intent ${parking.length}회. 현재는 단일 Journey 근거이므로 성향 판정은 ‘근거 부족’.` });
  if (txs.length) insights.push({ title:'소비 이벤트 관측', body:`자기보고 Transaction ${txs.length}건, 총 ${txs.reduce((s,e)=>s+(e.payload.amount||0),0).toLocaleString('ko-KR')}원. 반복 관측 전에는 소비성향으로 일반화하지 않음.` });
  if (stays.length) insights.push({ title:'체류 관측', body:`Stay 관련 Event ${stays.length}건. 이후 반복 Journey에서 장소 유형·체류시간을 비교해야 Pattern 판정 가능.` });
  if (!insights.length && events.length) insights.push({ title:'근거 부족', body:'Event는 수집되었으나 반복 패턴을 판정하기에는 아직 관측량이 부족합니다.' });
  return insights;
}

function escapeHtml(s='') { return String(s).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }

async function buildJourneyPayload() {
  const events = await getEvents();
  const journeyEvents = state.journeyId ? events.filter(e => e.journey_id === state.journeyId) : events;
  return {
    schema_version:'0.2',
    exported_at:nowIso(),
    state,
    insights:basicInsights(journeyEvents),
    events:journeyEvents
  };
}

async function renderAdmin() {
  const payload = await buildJourneyPayload();
  const journeyEvents = payload.events;
  const syncItems = await getSyncItems();

  els.todaySummary.innerHTML = `
    <div class="metric"><span>Journey 상태</span><strong>${escapeHtml(state.journeyState)}</strong></div>
    <div class="metric"><span>이벤트 수</span><strong>${journeyEvents.length}</strong></div>
    <div class="metric"><span>목적지</span><strong>${escapeHtml(state.destination || '—')}</strong></div>
    <div class="metric"><span>누적 지출</span><strong>${state.cumulativeSpend.toLocaleString('ko-KR')}원</strong></div>
  `;

  els.timeline.innerHTML = journeyEvents.length ? journeyEvents.map(e => `
    <div class="event">
      <time>${localTime(e.occurred_at)}</time>
      <div>
        <strong>${escapeHtml(e.event_type)}${e.event_subtype ? ' · '+escapeHtml(e.event_subtype):''}</strong>
        <p>${escapeHtml(eventSummary(e))}</p>
      </div>
    </div>`).join('') : '<div class="empty">아직 기록된 이벤트가 없습니다.</div>';

  els.insightPanel.innerHTML = payload.insights.map(i => `
    <div class="insight"><b>${escapeHtml(i.title)}</b><small>${escapeHtml(i.body)}</small></div>
  `).join('') || '<div class="empty">분석할 데이터가 부족합니다.</div>';

  els.syncPanel.innerHTML = `
    <div class="insight"><b>원격 API</b><small>${hasRemoteApi() ? '연결 주소 설정됨' : '미설정 — 로컬 기록은 정상 작동'}</small></div>
    <div class="insight"><b>Notion Queue</b><small>${syncItems.length}건 대기</small></div>
    <div class="insight"><b>PWA 위치 추적</b><small>앱이 전경에서 실행 중일 때만 연속 위치 기록</small></div>
  `;
}

async function exportJson() {
  const payload = await buildJourneyPayload();
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `context-trajectory-${Date.now()}.json`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function notionSync() {
  const payload = await buildJourneyPayload();
  if (!state.journeyId) {
    addMessage('assistant', '동기화할 Journey가 없습니다.');
    return;
  }

  const syncItem = {
    sync_id: uid('sync'),
    journey_id:state.journeyId,
    target:'notion',
    created_at:nowIso(),
    payload
  };

  if (!hasRemoteApi()) {
    await addSyncItem(syncItem);
    await renderAdmin();
    addMessage('assistant', 'Notion 동기화 데이터를 로컬 Queue에 저장했습니다. 안전한 중계 API를 연결하면 이 Queue를 전송합니다.');
    return;
  }

  try {
    const result = await syncJourneyToNotion(payload);
    await addEvent('System', 'NOTION_SYNCED', { page_id:result.page_id || null, url:result.url || null }, { source:'notion_api', inferred:true, confidence:'system_generated' });
    addMessage('assistant', '현재 Journey를 Notion에 동기화했습니다.');
  } catch (error) {
    await addSyncItem(syncItem);
    addMessage('assistant', 'Notion 전송에 실패해 로컬 Queue에 보관했습니다. 원본 Journey 데이터는 유지됩니다.');
  }
  await renderAdmin();
}

async function flushSyncQueue() {
  if (!hasRemoteApi()) return;
  const items = await getSyncItems();
  for (const item of items) {
    try {
      await syncJourneyToNotion(item.payload);
      await deleteSyncItem(item.sync_id);
    } catch {
      break;
    }
  }
}

function bindUi() {
  els.composer.addEventListener('submit', async e => {
    e.preventDefault();
    const text = els.input.value;
    els.input.value = '';
    await handleUserText(text);
  });
  document.querySelectorAll('[data-quick]').forEach(b => b.addEventListener('click', () => handleUserText(b.dataset.quick)));
  document.querySelectorAll('.nav-btn').forEach(b => b.addEventListener('click', async () => {
    document.querySelectorAll('.nav-btn').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
    document.querySelector('#'+b.dataset.view).classList.add('active');
    if (b.dataset.view==='adminView') await renderAdmin();
  }));
  els.locationBtn.addEventListener('click', () => captureLocation());
  document.querySelector('#refreshAdminBtn').addEventListener('click', renderAdmin);
  document.querySelector('#analyzeBtn').addEventListener('click', renderAdmin);
  document.querySelector('#exportBtn').addEventListener('click', exportJson);
  document.querySelector('#notionSyncBtn').addEventListener('click', notionSync);
}

async function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    try { await navigator.serviceWorker.register('./service-worker.js', {scope:'./'}); }
    catch (error) { console.warn('Service worker registration failed', error); }
  }
}

async function init() {
  db = await openDb();
  const saved = await loadState();
  if (saved) state = { ...INITIAL_STATE, ...saved, movementWatchActive:false };
  bindUi();
  renderState();
  await registerServiceWorker();
  await flushSyncQueue();

  if (state.journeyState === 'MOVING') startMovementWatch();

  addMessage('assistant', state.journeyId && state.journeyState !== 'ENDED'
    ? '이전 Journey 상태를 복원했습니다. 계속 말씀하세요.'
    : '개인용 Context Tourism Pilot입니다. 오늘 어디로 가실 예정인가요?');
}

init().catch(err => {
  console.error(err);
  addMessage('assistant', `초기화 오류: ${err.message}`);
});