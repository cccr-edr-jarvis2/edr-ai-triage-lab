# 런북: 우리가 겪은 장애와 찾는 법

> 02 문서 8장의 **'좋은 런북'** 형식으로 씁니다. 새벽 당번이 이것만 보고 해결할 수 있게, **실제로 친 명령과 실제 출력**을 남깁니다.

## 공통 1차 확인

```bash
kubectl -n soc get pods,svc,ingress,pvc,jobs
kubectl -n soc get events --sort-by=.lastTimestamp | tail -20
```

## 장애 기록

### 1일차 실습 — 진단 진행 중

- **출제:** 📘 문서 담당 · **진단:** 🔍 기록 담당 · **복구까지 걸린 시간:** 20분
- **시작 전 확인:** API `/readyz`가 정상 응답했고 의존 서비스 연결이 정상이었다.
- **진단자가 발견한 증상:** `/readyz`에서 opensearch 연결 상태가 false로 응답했다.
- **확인 명령과 출력:**

1. api 컨테이너 로그 확인
   `docker compose logs`로 api 컨테이너의 로그를 확인했다. 확인 결과, opensearch 주소로 http://localhost:9200로의 연결을 시도 중이고 거부되고 있음을 발견했다.

```
api-1         | urllib3.exceptions.NewConnectionError: HTTPConnection(host='localhost', port=9200): Failed to establish a new connection: [Errno 111] Connection refused
api-1         | 2026-11-18 02:00:30,243 opensearch HEAD http://localhost:9200/ [status:N/A request:0.000s]
```

2. docker-compose.yaml 설정 확인
   연결이 실패하는 원인으로 먼저 주소 설정이 잘못되어 있는지를 확인하고자 했다.

```
    # Opensearch 서비스 설정
    ports:
      - "127.0.0.1:9200:9200"

    # api 컨테이너 설정
    environment:
      OPENSEARCH_URL: http://localhost:9200
```

opensearch의 포트와 api 컨테이너의 연결 포트 자체는 9200으로 동일했다.
다만 localhost로 설정했을 경우, 다른 컨테이너 주소가 아니라 해당 컨테이너 내에서 서비스 포트로 접근하게 된다.
이를 서비스명인 opensearch:9200으로 변경하고 다시 컨테이너를 실행하였다.

- **진단 후 나눔:** 오래 걸린 지점, 다른 접근법, 다음에 처음 칠 명령을 진단 후 기록
  - 진단자: 컨테이너는 모두 실행 중이어서 서비스 간 연결에도 문제가 없는 줄 알았다.
    컨테이너가 실행 중인 것과 앱이 다른 서비스에 연결되는 것은 별개라는 점을 이해하는 데 시간이 걸렸다.
  - 출제자: 컨테이너 상태를 확인한 뒤 API의 준비 상태와 로그를 순서대로 확인할 것 같다.
  - 다음에 같은 증상을 보면 처음 칠 명령: `docker compose ps`

- **고장 예상표와 비교:** 기존 예상표에는 부모 기록 누락과 다른 PC의
  동일 PID 문제를 적었다. 이번 앱–OpenSearch 연결 설정 문제는
  예상하지 못했다. 다음에는 컨테이너 간 주소 설정도 확인 항목에 추가한다.

### [단계-번호] 제목 (출제: 🔍/📘 · 진단: 🔍/📘 · 복구까지 걸린 시간: \_\_\_\_)

- **증상:**
- **확인 명령과 출력:**
- **원인:**
- **조치:**
- **진단 후 나눔** (깨 보기 규칙 5~6번)
  - 진단자: 가장 오래 걸린 지점과 이유
  - 출제자: 나라면 어디부터 봤을지
  - 다음에 같은 증상을 보면 처음 칠 명령:
- **재발 방지:**
- **고장 예상표와 비교:** 02 문서 9-2에서 예상했던 고장인가? 증상과 '처음 확인할 곳'은 맞았나?
