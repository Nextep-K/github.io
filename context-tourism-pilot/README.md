# Context Tourism Pilot PWA v0.2

개인용 Local-first 관광 Context 수집·분석 PWA.

## 구현됨
- GitHub 정적 호스팅용 순수 PWA
- IndexedDB 로컬 저장
- Journey 상태 관리
- Intent / Deliberation / Choice / Movement / Stay / Transaction
- foreground watchPosition 위치 기록
- 시간 + 공간 + previous_event + context snapshot 기반 Context Trajectory
- 관리자 Timeline / Insight
- Notion Sync Queue
- 원격 LLM/Notion용 API adapter

## 첫 테스트
1. 진부도서관에서 월정사 입구까지 갈 거야
2. 자가용
3. 지금 출발해
4. 주차 정보 찾아줘
5. 도착했어
6. 점심 3만8천 원 썼어
7. 오늘 여행 종료

## 원격 API
앱 자체에는 OpenAI/Notion 비밀키를 넣지 않는다.
안전한 서버리스 중계 API를 배포한 뒤 다음 두 값만 개인 폰의 localStorage에 저장한다.

```js
localStorage.setItem('contextPilot.apiBaseUrl','https://YOUR-WORKER.workers.dev');
localStorage.setItem('contextPilot.apiToken','YOUR_PRIVATE_APP_TOKEN');
location.reload();
```

PWA 위치 추적은 앱이 전경에서 실행 중인 동안을 기준으로 한다.