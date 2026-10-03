export const CONFIG = {
  // GitHub Pages 정적 배포에서는 비워 둔다.
  // 안전한 서버리스 중계 API를 배포한 뒤 예: https://example.workers.dev
  apiBaseUrl: localStorage.getItem('contextPilot.apiBaseUrl') || '',
  apiToken: localStorage.getItem('contextPilot.apiToken') || '',
  location: {
    enableHighAccuracy: true,
    timeoutMs: 10000,
    maximumAgeMs: 15000,
    minPersistIntervalMs: 30000,
    minPersistDistanceM: 50
  }
};