import { CONFIG } from './config.js';

function endpoint(path) {
  if (!CONFIG.apiBaseUrl) return null;
  return `${CONFIG.apiBaseUrl.replace(/\/$/, '')}${path}`;
}

async function postJson(path, body) {
  const url = endpoint(path);
  if (!url) throw new Error('API_ENDPOINT_NOT_CONFIGURED');
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      ...(CONFIG.apiToken ? { 'x-app-token': CONFIG.apiToken } : {})
    },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    throw new Error(`API_${response.status}${detail ? `: ${detail}` : ''}`);
  }
  return response.json();
}

export function hasRemoteApi() {
  return Boolean(CONFIG.apiBaseUrl);
}

export async function chatWithRemote(payload) {
  return postJson('/api/chat', payload);
}

export async function syncJourneyToNotion(payload) {
  return postJson('/api/notion/sync', payload);
}