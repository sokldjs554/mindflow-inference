# Render 배포 구성

이 구성은 가상 기록과 deterministic mock provider를 사용하는 포트폴리오 데모용입니다.
실제 진단·치료 서비스가 아니며 의료진 검토를 전제로 합니다. 외부 LLM API를 호출하는
provider 선택 설정은 없고, 기존 worker가 `MockLLMProvider` / `MockSTTProvider`를 생성합니다.

## 현재 배포 전제와 미완료 항목

공개 URL은 아직 생성·검증하지 않았습니다. 아래 조건을 해결하기 전에는 적용하지 않습니다.

- 기존 API는 직접 입력 transcript, 오디오, session title, reviewer/reason 등을 저장합니다.
  UI 안내와 redaction만으로 방문자가 입력하는 실제 개인정보 저장을 방지할 수 없습니다.
  공개 데모에서 고정 synthetic 입력만 받도록 제한하는 변경은 별도 범위 확인이 필요합니다.
- Render 연결 도구로 workspace/service 조회는 가능하지만 Blueprint 적용 및 background
  worker 생성 도구가 없습니다. 로컬 Render CLI와 `RENDER_API_KEY`도 없습니다.
  따라서 도구만으로 요청한 전체 구성을 실제 생성할 수 없었습니다.
- web과 worker는 최소 유료 compute(`0.5c-512mb`)입니다. worker에는 무료 플랜이 없고
  web pre-deploy도 유료 플랜이 필요합니다. 적용 전에 Dashboard의 실제 청구 금액을 확인합니다.
- 현재 workspace의 무료 PostgreSQL/Key Value 한도는 다른 프로젝트가 이미 사용 중입니다.
  기존 리소스를 변경하거나 공유하지 않고, DB `0.1c-256mb` / 디스크 1 GB와
  Key Value `256mb`의 최소 유료 구성을 명시했습니다. 네 리소스 모두 비용이 발생합니다.
  실제 생성은 하지 않았습니다. 무료 리소스는 종류별 workspace당 하나만 허용됩니다.

## 저장소에서 확인한 실행 방식

| 항목 | 기존 구현 / Render 적용 |
|---|---|
| API | `uvicorn app.main:app --host 0.0.0.0 --port <PORT> --no-access-log` |
| Worker | `python -m app.worker`, Redis Streams `workers` consumer group |
| PostgreSQL | `DATABASE_URL`, SQLAlchemy async engine / asyncpg |
| Redis | `REDIS_URL`, `Redis.from_url`, web/worker 동일 `STREAM_PREFIX` |
| Migration | `python -m alembic upgrade head`, `migrations/env.py`가 동일 설정 사용 |
| UI | `/` → `app/static/index.html`, `/static/*`, Docker package data에 포함 |
| WebSocket | 브라우저의 `location.host`와 HTTPS이면 `wss`, `/ws/jobs/{id}` |
| Health | `/ready`: 2초 안에 Alembic 테이블 SELECT와 Redis PING, 실패 시 503 |

`/ready`는 worker heartbeat나 Alembic head 일치 자체를 검사하지 않습니다.
실제 worker 처리와 WebSocket 이벤트는 샘플 실행으로 별도 확인해야 합니다.

## Blueprint와 시작 순서

네 리소스는 동일 Singapore region에 생성됩니다. 기존 Dockerfile을 재사용하므로
`buildCommand` 대신 `dockerfilePath` / `dockerContext`를 사용합니다.
이미지 빌드는 기존 `pip install --no-cache-dir .`이고, Render 시작 명령은
`dockerCommand`로만 덮어씁니다. Dockerfile과 docker-compose.yml의 로컬 명령은 유지됩니다.

1. Render PostgreSQL과 Key Value 생성, 내부 연결 문자열 runtime 주입.
2. Web pre-deploy: `python -m scripts.render_start migrate` → `alembic upgrade head`.
   실패하면 web 배포가 중단됩니다. worker에서는 migration을 중복 실행하지 않습니다.
3. Web: `python -m scripts.render_start web` → `0.0.0.0:$PORT`로 API 실행.
4. Worker: `python -m scripts.render_start worker` → 해당 코드의 Alembic heads와 DB
   revision이 일치할 때까지 최대 300초 기다린 후 실제 worker 실행. 시간 초과 시 실패합니다.

Render 전용 스크립트는 `postgresql://` 또는 `postgres://`를
`postgresql+asyncpg://`로 변환하며 credentials 및 URL의 나머지는 보존합니다.
필수 DB/Redis 변수가 없거나 loopback 주소이면 시작을 거절합니다.
Web에서는 Render 기본 변수 `RENDER_EXTERNAL_URL`로 기존 `CORS_ORIGINS`를 JSON 배열로
설정하므로 실제 할당된 URL만 WebSocket origin으로 허용합니다. `PORT`도 Render 값을 사용합니다.
Blueprint의 문자열 보간이나 예상 공개 hostname에 의존하지 않습니다.

Web/worker 자동 배포는 꺼져 있습니다. 최초 Blueprint 적용 후 코드 업데이트 시에는
web migration 및 배포 성공을 확인하고 worker를 같은 commit으로 배포합니다.
두 서비스를 걸친 migration 순서를 Blueprint 자체가 보장한다고 가정하지 않습니다.

## 공개 값과 secrets

| 변수 | 주입 방식 / 성격 |
|---|---|
| `DATABASE_URL` | `fromDatabase.connectionString`, 비밀값, 저장소/로그에 출력 금지 |
| `REDIS_URL` | `fromService.connectionString`, 내부 연결 정보, 저장소/로그에 출력 금지 |
| `API_KEY` | 현재 구성은 빈 값: 설치·키 입력 없는 공개 데모를 위한 기존 동작 |
| `STREAM_PREFIX` | 공개 값 `mindflow`, web/worker 동일 |
| `CORS_ORIGINS` | 실행 시 실제 `RENDER_EXTERNAL_URL`에서 계산하는 공개 origin |
| `PORT`, `RENDER_EXTERNAL_URL` | Render 기본 제공 변수, 임의 환경변수 추가 없음 |

DB/Redis의 `ipAllowList: []`는 외부 접속을 차단합니다. worker metrics는 기존 loopback
기본값을 유지합니다. `.env`는 커밋하거나 이미지에 포함하지 않습니다. 기존 로컬 loopback
기본값과 Compose의 공개 로컬 데모 암호는 Render에서 사용되지 않습니다.

## 적용 및 공개 검증

위 미완료 조건을 해결하고 commit/push한 다음
[Render Blueprint 생성](https://dashboard.render.com/blueprint/new?repo=https://github.com/sokldjs554/mindflow-inference)을
열어 리소스와 비용을 검토한 뒤 적용합니다. 이 링크는 Live Demo URL이 아닙니다.
CLI를 사용하는 환경에서는 `render blueprints validate render.yaml`도 실행합니다.

공개 URL에서 한글 UI, Scenario E, 원문/근거, Clinical Review Support,
승인/거절과 audit, Scenario B의 `STALE_EVIDENCE` 승인 차단, note-v2 Replay/비교,
WebSocket progress, `/ready`, worker 로그 및 새로고침을 직접 확인합니다.
확인 완료 후에만 README 상단에 실제 Live Demo URL을 추가합니다. `docs/demo.mp4`는 유지합니다.

## 공식 규격

- [Blueprint reference 및 plan ID](https://render.com/docs/blueprint-spec)
- [공식 JSON schema](https://render.com/schema/render.yaml.json)
- [Pre-deploy와 Docker 시작 명령](https://render.com/docs/deploys#pre-deploy-command)
- [Render 기본 환경변수](https://render.com/docs/environment-variables)
- [무료 리소스 제한](https://render.com/docs/free)

검증 결과는 이 작업의 최종 보고와 `docs/render-verification.md`를 참고합니다.
