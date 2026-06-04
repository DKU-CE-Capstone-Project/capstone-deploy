# AI 에이전트 ↔ 클라우드 결합 — 발표·보고서 가이드

발표 자료(오픈소스SW분석·클라우드)는 CNCF 인프라 중심이라 AI 에이전트 깊이가 약했다.
이 문서는 **AI 에이전트적 측면을 클라우드 서사에 자연스럽게 섞는** 방법을 정리한다.

## 한 줄 메시지

> **"멀티 에이전트 워크로드를 CNCF로 운영·확장하고, MongoDB 벡터검색(RAG)으로 에이전트를 똑똑하게 만든다."**
> AI와 클라우드는 분리된 두 축이 아니라 **한 시스템의 두 얼굴**이다.

## 핵심 프레이밍 3가지

1. **에이전트 파이프라인 = Argo DAG (같은 그림, 두 관점)**
   수집 → 필터 → 요약 → 키워드/클러스터 → **리포트(+RAG 검색 도구)** → **검증(critic)**.
   이 6단계 멀티 에이전트 DAG가 곧 Argo Workflow의 DAG다. "AI 그림"과 "클라우드 그림"이 같은 그림.

2. **에이전트별 독립 확장 = KEDA 오토스케일**
   가장 무거운 LLM 리포트 에이전트를 속보 burst 시 KEDA가 **독립적으로 1→N 확장**.
   기존 오토스케일 데모(100s→10s, 9.7×)를 *"AI 에이전트 워크로드 자동 확장"*으로 재프레이밍.

3. **데이터·근거 계층 = MongoDB Atlas (관리형 클라우드 DB)**
   영속화 + **Atlas Vector Search**를 한 컴포넌트가 담당. 벡터검색이 RAG의 엔진이 되어
   에이전트 품질을 끌어올린다 → "클라우드 관리형 서비스로 AI를 강화"하는 스토리.

## 실제 구현된 에이전트적 요소 (어필 포인트)

| 요소 | 구현 | 에이전트 패턴 |
|---|---|---|
| **RAG 그라운딩** | 리포트 생성 전 `gemini-embedding-001`(768d)로 임베딩 → Atlas `$vectorSearch`로 유사 과거 뉴스 top-3 검색 → 프롬프트에 근거 주입 | 도구 사용(retrieval) |
| **검증(critic) 에이전트** | 생성된 리포트를 두 번째 LLM이 근거 대비 점검 → `{grounded, confidence, issues, invalid_tickers}` | 검증 루프 / LLM-as-judge |
| **6개 특화 에이전트** | 수집·필터·요약·키워드·리포트·전략 각자 단일 책임 | 역할 분리 |
| **Graceful degradation** | Gemini 429/벡터 불가 시 자동 fallback (서비스 무중단) | 신뢰성 |

> 솔직성: 현재는 결정형 파이프라인 + RAG·검증 루프. **완전 자율(ADK·동적 분기)은 향후 과제**로 명시 → 과장 없이 신뢰도↑.

## 추가/수정 슬라이드 (10분 발표)

1. **AI 에이전트 파이프라인** (신규): 6-에이전트 DAG 그림. 캡션 "이 DAG가 곧 Argo Workflow".
2. **RAG로 똑똑해진 리포트 에이전트** (신규): MongoDB Vector Search 구조 + 근거 주입 전/후 리포트 비교. 'AI 깊이' 슬라이드.
3. **검증 에이전트** (신규): critic이 환각·허위 종목 차단하는 예시 JSON.
4. **기존 '최종 아키텍처' 슬라이드 보강**: 데이터 계층에 `MongoDB Atlas (+Vector Search)` 추가, 리포트 워커 옆에 `RAG·critic` 표기.
5. **기존 '오토스케일 데모' 슬라이드 재프레이밍**: 제목을 "AI 에이전트 워크로드 자동 확장(KEDA)"으로.

## 보고서 반영
- §4 아키텍처: 데이터 계층 = MongoDB Atlas, 에이전트 계층에 RAG·critic 추가(1차 대비 변경: in-memory→Atlas 영속화, 벡터검색 도입).
- §5 CNCF 활용: "관리형 MongoDB Atlas + Vector Search로 RAG 근거 계층 구성".
- §6 구현결과: RAG 근거 주입 전/후 리포트 비교 스크린샷, critic 검증 JSON.
- §7 검증: 벡터검색 유사도 점수, critic confidence; 한계로 "완전 자율 에이전트(ADK) 미구현" 명시.

## 데모 시연 (3분 영상에 1컷 추가)
검색(nvidia) → 리포트 생성 시 로그 `[report] RAG grounded with 3 similar articles` → 리포트가 근거 반영 + `GET /reports/{id}`의 `verification.grounded=true` 노출. Atlas Browse Collections에서 `articles`에 `embedding`(768d) 저장 확인.
