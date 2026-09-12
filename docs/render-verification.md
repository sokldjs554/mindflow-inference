# Render 구성 검증 기록

## PUBLIC DEMO MODE 검증 — 2026-09-13

공개 입력 제한을 구현하고 로컬 Docker 환경에서 검증했습니다. 실제 Render 배포,
공개 hostname/TLS 및 Live Demo URL은 아직 생성·검증하지 않았습니다.

| 검증 | 결과 |
|---|---|
| `python -m pytest -q` | **85 passed**, skip 없음, 기존 Alembic 경고 2건 |
| `ruff check app tests scripts` | 통과 |
| `ruff format --check app tests scripts` | 30 files already formatted |
| `mypy app scripts/render_start.py` | 16 source files 통과 |
| `docker compose config --quiet` | 통과 |
| `docker build -t mindflow-public-demo .` | 통과 |
| `python -m alembic check` | No new upgrade operations detected |
| Render 공식 JSON Schema 및 기존 구성 참조 검증 | 통과, 아래 기존 schema hash와 동일 |
| Render CLI/API Blueprint 서버 측 검증 | 미실행 |
| 서버 소유 Scenario A~E / 기존 JS fixture 일치 | 통과 |
| E 근거/Clinical Review/승인/audit/Provenance | 통과 |
| B STALE_EVIDENCE / 승인 409 / 거절 / note-v2 Replay·비교 | 통과 |
| 임의 title/transcript/append/correction/upload 및 변조 scenario body | REST 차단 및 DB 미저장 확인 |
| 임의 reviewer/reason | 고정 demo 값 저장 확인 |
| 기존 자유 입력 기록의 새 추론/Replay | 공개 모드에서 차단 확인 |
| PUBLIC_DEMO_MODE 기본 false / 환경변수 true | 통과, 기존 로컬 회귀 테스트 유지 |
| Headless Edge 실제 UI | 자유 입력 숨김/비활성화, E 승인, B stale 승인 비활성화·거절·Replay 후 승인, page error 0 |

별도 `mindflow_public_test` DB와 `mindflow-public-check` Redis namespace에서
`scripts.render_start migrate/web/worker`를 실행했습니다. web/worker 모두
`PUBLIC_DEMO_MODE=true`, 일반 `postgresql://` URL 및 web `PORT=10000`을 주입했습니다.
`/ready` 200, 기존 E/B 검증을 서버 Scenario endpoint로 실행하여 E WebSocket progress
6건, B/Replay 12건, 고정 demo reviewer와 Replay 후 승인을 확인했습니다.
검증 스크립트와 컨테이너는 로컬 전용이며 실제 Render 서비스가 아닙니다.

자동 삭제는 구현하지 않았습니다. 전용 DB 사용과 정기 초기화 절차는
[배포 문서](render-deployment.md#공개-데모-입력-정책과-데이터-보존)에 있습니다.

## 이전 배포 구성 검증

검증일: 2026-09-12. **실제 Render 배포 및 공개 URL 검증은 미완료**입니다.
모든 실행 데이터는 synthetic이며 외부 LLM API는 사용하지 않았습니다.

| 검증 | 결과 |
|---|---|
| `python -m pytest -q` | 76 passed, 20.74초, skip 없음 |
| `ruff check app tests scripts` | 통과 |
| `ruff format --check app tests scripts` | 28 files already formatted |
| `mypy app scripts/render_start.py` | 15개 파일 통과 |
| `python -m pip check` | 의존성 문제 없음 |
| `docker compose config --quiet` | 통과 |
| `docker compose build` | API / worker / migrate 이미지 모두 통과 |
| Compose 전체 실행 | migration 완료 후 API healthy / worker 실행 확인 |
| `python -m alembic check` | No new upgrade operations detected |
| 기존 `python -m scripts.demo` | Scenario A-D, B replay/비교 통과 |
| 기존 bounded load harness | 6 workflows / 36 HTTP / 6 WebSockets, 오류 0 |
| Render 공식 JSON Schema | 통과; 리소스 참조, 내부 접속, Docker 필드 별도 확인 |
| Render CLI/API 서버 측 Blueprint validation | 미확인: 해당 인증/검증 도구 없음 |
| `git diff --check` / 변경 검토 | 통과; UI/백엔드와 Dockerfile/Compose 변경 없음 |
| 기존 `docs/demo.mp4` | 변경 없음, README 영상 링크 유지 |

테스트 환경은 Docker Linux / Python 3.12.14 / PostgreSQL 16 / Redis 7.4입니다.
Node.js 22를 검증 이미지에 추가하여 기존 clinical presentation 테스트도 실행했습니다.
첫 실행의 Node.js 부재에 따른 13 skip은 최종 실행에서 모두 해소했습니다.
Alembic의 기존 `prepend_sys_path` / `path_separator` 관련 deprecation warning 2건이 있습니다.

로컬 6379 포트가 이미 사용 중이어서 ignored override 파일로 Redis/API/worker 공개 포트를
각각 16379/18000/19000으로 바꿔 실행했습니다. 원본 Compose 파일은 수정하지 않았습니다.
검증용 개발 이미지, 실행 스크립트, 원시 load 결과는 `.runtime` / `reports/local`에만 있습니다.

## Render 시작 경로의 로컬 통합 검증

별도 `mindflow_render_test` DB와 `mindflow-render-check` Redis namespace를 사용했습니다.
일반 `postgresql://` 연결 URL을 주입하여 asyncpg 변환을 실제 migration/API/worker에서 확인했습니다.

1. 빈 DB 상태에서 Render worker wrapper 시작: migration 대기, worker 시작 로그 없음.
2. Render migrate wrapper 실행으로 Alembic schema 생성.
3. 대기 중인 worker가 `worker_started`를 기록하고 소비 시작.
4. Render web wrapper에 `PORT=10000` 주입: 실제 `0.0.0.0:10000` 접속 성공.
5. `RENDER_EXTERNAL_URL`은 로컬 검증용 HTTPS origin으로 주입해 WebSocket Origin 허용 확인.
   실제 Render hostname 또는 HTTPS TLS 검증을 했다는 뜻은 아닙니다.
6. `/ready`: HTTP 200, `{"status":"ready","postgres":true,"redis":true}`.
7. UI 파일의 `lang="ko"`, 가상 데이터 안내, JS/CSS 제공, 반복 GET 200 확인.
8. UI에 정의된 Scenario E fixture로 실제 worker 처리, clinical support, evidence API,
   승인 및 audit 확인. WebSocket progress 6건 수신.
9. UI의 Scenario B fixture로 `STALE_EVIDENCE` 1건, 승인 409 `UNSAFE_APPROVAL`, 거절,
   note-v2 Replay/비교의 stale 0건, replay 승인과 audit 확인. WebSocket progress 총 12건 수신.

브라우저 화면 렌더링/클릭과 Render의 실제 WSS/TLS, 공개 URL 새로고침은 미확인입니다.
로컬 HTTP 반복 GET을 브라우저 새로고침 검증으로 간주하지 않습니다.

공식 schema SHA-256:
`f6cb3fbae8c598d41385069bf7084293b48b802f88e1bc98b1c4b9c24a15be47`.
Schema 통과는 Render 계정 한도, 비용, Git 연결 및 실제 배포 성공을 보장하지 않습니다.

실제 배포가 막힌 이유와 다음 적용 절차는 [배포 안내](render-deployment.md)에 있습니다.
