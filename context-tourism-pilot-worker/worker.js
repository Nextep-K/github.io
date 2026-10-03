const NOTION_VERSION = '2026-03-11';

function json(data, status=200, headers={}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type':'application/json; charset=utf-8',
      'access-control-allow-origin':'*',
      'access-control-allow-headers':'content-type,x-app-token',
      'access-control-allow-methods':'POST,OPTIONS',
      ...headers
    }
  });
}

function extractResponseText(data) {
  for (const item of data.output || []) {
    if (item.type !== 'message') continue;
    for (const part of item.content || []) {
      if (part.type === 'output_text' && part.text) return part.text;
    }
  }
  return '';
}

function compactContext(body) {
  const context = body.context || {};
  const events = (body.recent_events || []).slice(-20).map(e => ({
    at:e.occurred_at,
    type:e.event_type,
    subtype:e.event_subtype,
    place:e.place_name,
    payload:e.payload
  }));
  return { context, events };
}

async function openAiChat(requestBody, env) {
  if (!env.OPENAI_API_KEY) return json({error:'OPENAI_API_KEY_NOT_SET'}, 503);
  const travelContext = compactContext(requestBody);
  const prompt = [
    '너는 개인용 관광 동반자다.',
    '사용자의 현재 시간·공간·Journey 맥락을 이어서 답한다.',
    '확인되지 않은 실시간 정보는 단정하지 않는다.',
    '사용자에게 연구 설문처럼 말하지 않는다.',
    '짧고 실용적으로 한국어로 답한다.',
    `현재 맥락: ${JSON.stringify(travelContext)}`,
    `사용자: ${requestBody.message || ''}`
  ].join('\n');

  const response = await fetch('https://api.openai.com/v1/responses', {
    method:'POST',
    headers:{
      'authorization':`Bearer ${env.OPENAI_API_KEY}`,
      'content-type':'application/json'
    },
    body:JSON.stringify({
      model:env.OPENAI_MODEL || 'gpt-6-luna',
      input:prompt,
      store:false
    })
  });
  const data = await response.json();
  if (!response.ok) return json({error:'OPENAI_ERROR', detail:data}, response.status);
  return json({reply:extractResponseText(data), response_id:data.id || null});
}

function journeySummary(payload) {
  const state = payload.state || {};
  const insights = payload.insights || [];
  const eventCount = Array.isArray(payload.events) ? payload.events.length : 0;
  return {
    title:`Context Journey · ${new Date().toISOString().slice(0,10)}`,
    lines:[
      `Journey ID: ${state.journeyId || '-'}`,
      `Origin: ${state.origin || '-'}`,
      `Destination: ${state.destination || '-'}`,
      `Transport: ${state.transport || '-'}`,
      `State: ${state.journeyState || '-'}`,
      `Spend: ${state.cumulativeSpend || 0} KRW`,
      `Events: ${eventCount}`,
      ...insights.map(i => `${i.title}: ${i.body}`)
    ]
  };
}

async function syncNotion(payload, env) {
  if (!env.NOTION_TOKEN || !env.NOTION_PARENT_PAGE_ID) {
    return json({error:'NOTION_ENV_NOT_SET'}, 503);
  }
  const summary = journeySummary(payload);
  const children = summary.lines.slice(0,90).map(line => ({
    object:'block',
    type:'paragraph',
    paragraph:{rich_text:[{type:'text', text:{content:String(line).slice(0,1900)}}]}
  }));
  children.push({
    object:'block',
    type:'code',
    code:{
      language:'json',
      rich_text:[{type:'text', text:{content:JSON.stringify(payload).slice(0,1900)}}]
    }
  });

  const response = await fetch('https://api.notion.com/v1/pages', {
    method:'POST',
    headers:{
      'authorization':`Bearer ${env.NOTION_TOKEN}`,
      'content-type':'application/json',
      'notion-version':NOTION_VERSION
    },
    body:JSON.stringify({
      parent:{page_id:env.NOTION_PARENT_PAGE_ID},
      properties:{title:{type:'title', title:[{type:'text', text:{content:summary.title}}]}},
      children
    })
  });
  const data = await response.json();
  if (!response.ok) return json({error:'NOTION_ERROR', detail:data}, response.status);
  return json({page_id:data.id, url:data.url});
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return json({ok:true});
    if (request.method !== 'POST') return json({error:'METHOD_NOT_ALLOWED'}, 405);
    if (env.APP_TOKEN && request.headers.get('x-app-token') !== env.APP_TOKEN) {
      return json({error:'UNAUTHORIZED'}, 401);
    }

    const url = new URL(request.url);
    let body;
    try { body = await request.json(); }
    catch { return json({error:'INVALID_JSON'}, 400); }

    if (url.pathname === '/api/chat') return openAiChat(body, env);
    if (url.pathname === '/api/notion/sync') return syncNotion(body, env);
    return json({error:'NOT_FOUND'}, 404);
  }
};