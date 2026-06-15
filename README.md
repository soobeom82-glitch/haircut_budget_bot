# Haircut Calendar Bot

텔레그램 채팅방에 `이발 3만`, `염색 4만`, `충전 30만`처럼 입력하면:

- 메시지 시각을 기준으로 구글 캘린더에 일정을 생성하고
- Redis에 저장된 현재 잔액을 읽어 예치금을 계산한 뒤
- 새 잔액을 반영한 제목으로 저장합니다.

예시 제목:

- `이발 (3만) 잔액 330,000원`
- `염색 (4만) 잔액 290,000원`
- `서하 은호 이발 (3만) 잔액 260,000원` (`EVENT_PREFIX=서하 은호`)

## 지원 형식

- `충전 30만`
- `염색 4만`
- `이발 3만`
- `염색 4만 : 메모`
- `이발 3` (`DEFAULT_AMOUNT_UNIT=man`이면 3만으로 처리)

`충전`, `입금`, `예치금`이 포함된 항목은 플러스로 계산하고, 나머지는 마이너스로 계산합니다.

## 동작 방식

1. 텔레그램 웹훅이 메시지를 받습니다.
2. 메시지에서 항목명과 금액을 파싱합니다.
3. Redis에 저장된 현재 잔액을 읽습니다.
4. 값이 없으면 `INITIAL_BALANCE_WON`을 시작 금액으로 씁니다.
5. 새 잔액을 계산합니다.
6. 메시지가 발생한 시각으로 새 캘린더 이벤트를 만듭니다.
7. 텔레그램에 처리 결과를 답장합니다.

## 설정

### 1. 텔레그램 봇 만들기

1. 텔레그램에서 `@BotFather`를 열고 `/newbot`으로 봇을 만듭니다.
2. 발급받은 토큰을 `TELEGRAM_BOT_TOKEN`에 넣습니다.
3. 그룹 채팅에서 일반 메시지를 읽게 하려면 `@BotFather`의 `/setprivacy`에서 `Disable`로 바꿉니다.
4. 봇을 사용할 채팅방에 초대합니다.

### 2. 구글 캘린더 서비스 계정 준비

1. Google Cloud Console에서 새 프로젝트를 만듭니다.
2. `Google Calendar API`를 활성화합니다.
3. `서비스 계정`을 만들고 JSON 키를 다운로드합니다.
4. 연결할 구글 캘린더의 `설정 및 공유`에서 서비스 계정 이메일에 `일정 변경` 권한을 부여합니다.
5. 다운로드한 JSON 파일 경로를 `GOOGLE_SERVICE_ACCOUNT_FILE`에 넣습니다.

이 봇은 서비스 계정으로 캘린더를 읽고 씁니다. 캘린더는 거래 이력 저장용이고, 현재 잔액 원장은 Redis에 저장합니다.

### 3. 환경 변수

`.env.example`를 참고해 `.env` 파일을 만듭니다.

주요 항목:

- `GOOGLE_CALENDAR_ID`: `primary` 또는 특정 캘린더 ID
- `EVENT_PREFIX`: 제목 앞에 항상 붙일 텍스트. 예: `서하 은호`
- `INITIAL_BALANCE_WON`: Redis에 아직 잔액이 없을 때 시작 금액
- `TELEGRAM_ALLOWED_CHAT_IDS`: 허용할 채팅방 ID. 비워두면 모든 채팅 허용
- `KV_REST_API_URL`, `KV_REST_API_TOKEN`: Upstash Redis 연결 정보

## 실행

```bash
python3 main.py
```

정상 실행되면 `/health`에서 `ok`를 돌려줍니다.

## Vercel 배포

이 저장소는 Vercel Python Functions 구조도 같이 포함합니다.

- `/telegram/webhook` -> `api/telegram/webhook.py`
- `/health` -> `api/health.py`

즉, Vercel에 배포한 뒤 텔레그램 웹훅 URL은 아래처럼 쓰면 됩니다.

```text
https://<your-vercel-domain>/telegram/webhook
```

### Vercel 프로젝트에 넣어야 할 환경 변수

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_SECRET_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `GOOGLE_CALENDAR_ID`
- `GOOGLE_SERVICE_ACCOUNT_FILE` 대신 `GOOGLE_SERVICE_ACCOUNT_JSON` 권장
- `KV_REST_API_URL`
- `KV_REST_API_TOKEN`
- `CALENDAR_TIMEZONE`
- `EVENT_PREFIX`
- `INITIAL_BALANCE_WON`

서비스 계정 JSON은 파일 업로드보다 `GOOGLE_SERVICE_ACCOUNT_JSON`에 JSON 문자열 전체를 넣는 방식이 Vercel에서는 더 편합니다.

### 추천 저장소

Vercel 기준 무료 DB로는 `Upstash Redis`를 추천합니다.

- 잔액처럼 작은 상태값 저장에 충분합니다.
- Vercel Marketplace로 연결하면 `KV_REST_API_URL`, `KV_REST_API_TOKEN`이 자동으로 들어옵니다.
- 이 프로젝트는 별도 SDK 없이 REST 방식으로 바로 붙도록 구현돼 있습니다.

### 주의 사항

- Vercel의 로컬 파일시스템은 영구 저장소가 아닙니다.
- 그래서 중복 방지는 캘린더 이벤트 설명의 `update_id=...`를 기준으로 한 번 더 확인합니다.
- `.data` 로그 파일은 로컬 실행용에 가깝고, Vercel에서는 `/tmp` 임시 저장소를 사용합니다.

## 웹훅 등록

서버가 외부에서 접근 가능한 URL에 떠 있어야 합니다. 예를 들어 `https://example.com/telegram/webhook` 으로 노출되었다면:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -d "url=https://example.com/telegram/webhook" \
  -d "secret_token=<YOUR_SECRET_TOKEN>"
```

웹훅 확인:

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

## 텔레그램 명령

- `/help`: 사용법 보기
- `/balance`: 현재 잔액 조회
- `/history`: 최근 이력 5건 조회
- `/history 10`: 최근 이력 최대 10건 조회
- `/setbalance 36만`: 현재 잔액 강제 설정
- `/chatid`: 현재 채팅방 ID 확인

## 메모

- 처리된 업데이트는 `.data/processed_updates.json`에 저장해서 중복 등록을 막습니다.
- 처리 내역은 `.data/ledger.jsonl`에 남습니다.
- 일정 설명에는 원문 메시지, 증감액, 잔액, 텔레그램 메시지 ID가 함께 기록됩니다.
- `DEFAULT_EVENT_DURATION_MINUTES`로 기본 일정 길이를 바꿀 수 있습니다.
