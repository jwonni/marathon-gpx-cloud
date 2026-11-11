# marathon-gpx-cloud

마라톤 코스 지도 이미지에서 경로를 추출해 지리좌표(GPX)로 변환하는 AI 서비스입니다.
로컬에서만 동작하던 캡스톤 프로젝트를 **실서비스 운영을 목표로 AWS 클라우드에 옮기며 고도화하고 있는 개인 프로젝트**입니다.

| 구분 | 범위 | 기간 |
|------|------|------|
| 캡스톤 팀 프로젝트 | 경로 추출 모델, 후처리·그래프 단순화, 지리좌표 변환 파이프라인, 웹 UI | ~ 2026-05 (이 레포의 커밋 기록 기준 2026-05-12 ~ 05-20) |
| **개인 작업 (이 레포)** | 컨테이너화, 성능 병목 진단, OCR 서비스 분리·GPU 전환, 정확도 검증, Terraform 인프라, 모니터링·알람 | 2026-09 ~ |

- 원본 팀 레포: [jwonni/Capston-A-man-of-Gyeongsang-do](https://github.com/jwonni/Capston-A-man-of-Gyeongsang-do) — 팀 커밋 기록은 이 레포에 그대로 보존되어 있습니다
- 배경: 러닝 의류·커뮤니티 기업과 함께한 AX 산학 캡스톤. 담당자가 매번 20~30분씩 손으로 만들던 마라톤 코스 GPX를 지도 이미지로 자동 생성하는 것이 목표였습니다
- 팀 프로젝트에서 본인 담당: OCR 모델 선정, 인식 텍스트 정제, 카카오 API로 지명 좌표 확보, 신뢰할 수 있는 좌표만 골라 픽셀↔GPS 변환 행렬 계산 (지리좌표 변환 파이프라인)

## 핵심 성과

![분리 전후 아키텍처와 처리 시간](docs/portfolio/capstone-architecture.png)

| 지표 | 개선 전 | 개선 후 | 무엇을 바꿨나 |
|------|---------|---------|---------------|
| 지도 1장 전체 처리 시간 (EC2 g4dn.xlarge, T4) | 약 154초 | **약 15~17초 (약 9배)** | OCR 서비스 분리·GPU 전환, Hi-SAM FP16·점 수 조정, OCR 인식 전용 배치 |
| 결과 정확도 (송도 지도, 정답 위치와의 거리) | 출발 4.8km · 도착 5.6km | **316m · 210m** | 앵커 영역 필터 수정, 기록 없이 낮춰져 있던 Hi-SAM 설정 복원 |
| Hi-SAM 글자 검출 실패율 | 57% (7회 중 4회) | **이후 측정에서 실패 없음** | 〃 |
| GPU 메모리 최대 (앱) | 9.5GB | **5.6GB** | Hi-SAM FP16 |
| 처리 중 `/metrics`·`/api/health` 응답 | 10초 넘게 응답 없음 | **76/76 성공, 최대 0.1초** | 모니터링으로 이벤트 루프 블로킹 발견 → 스레드풀 실행 |
| 의존성 수정 후 이미지 재빌드 | 1,430~2,500초 | **248초** | torch 레이어 분리 + pip 캐시 마운트 |
| Docker 빌드 컨텍스트 | 1.8GB | **1.9MB** | 모델 가중치를 이미지에서 분리 |
| GPU 실험 환경 구성 | 콘솔 수작업 (AMI·디스크·IAM 실수 반복) | **`terraform apply` 1회** | Terraform 코드화 + S3 원격 state·락 |

- 같은 인스턴스 타입·같은 벤치마크 지도·같은 측정 로그(`[resource]`)로 비교했습니다
- 처리 시간은 **정확도가 확보된 설정 기준**입니다. 중간에 기록했던 25.7초는 Hi-SAM 설정이 낮춰져 결과가 수 km씩 틀리던 상태의 수치라 대체했습니다 (decision-log 18·24~27번)
- 정확도는 응답의 "성공" 여부가 아니라 **정답 위치와의 거리(m)**로 평가했습니다 (`bench/e2e_eval.py`)
- OCR 분리 전후 결과가 같은지 Hi-SAM 출력을 고정해 검증했습니다 (텍스트 106개, 글자·좌표·신뢰도 완전 일치)
- 모든 결정의 근거와 측정 조건은 [docs/decision-log.md](docs/decision-log.md)에 있습니다

## 주요 기능

1. **경로 마스크 추출** — SegFormer-UNet B2 / U-Net 모델로 마라톤 코스 이미지에서 경로 마스크 예측
2. **후처리** — 연결 성분 필터링 + 스켈레톤화
3. **그래프 단순화** — 스켈레톤을 5단계 파이프라인으로 의미론적 그래프 G′로 압축
4. **경로 추출** — G′에서 BFS 탐색 후 엣지 픽셀 복원 + 꺾임점 기반 키포인트 추출 (시작·끝점 자동 결정 지원)
5. **지리좌표 변환** — Hi-SAM 텍스트 감지 → PaddleOCR → 카카오 Local API → 호모그래피로 픽셀↔GPS 변환
6. **GPX 출력** — 변환된 GPS 좌표를 GPX 파일로 내보내기

개인 작업으로 추가된 기능:

7. **OCR 서비스 분리** — PaddleOCR을 별도 컨테이너(`ocr_service.py`)로 분리해 GPU로 실행. `OCR_SERVICE_URL`이 없으면 기존처럼 앱 안에서 실행
8. **요청별 자원 측정** — 모든 API 요청의 처리 시간·RAM·VRAM·CPU를 `[resource]` 로그로 기록
9. **인프라 코드화** — GPU EC2·보안 그룹·IAM 역할을 Terraform으로 생성/삭제, state는 S3에 보관
10. **모니터링·알람** — Prometheus 지표(처리 시간, 실패, GPU 메모리, 결과 품질), Grafana 대시보드, Alertmanager 알람

## 아키텍처

```
브라우저 ──▶ 앱 컨테이너 (FastAPI, :8010)                     OCR 컨테이너 (FastAPI, :8020)
            torch (CUDA 13)                                  paddle (CUDA 12.6)
            ├─ SegFormer-UNet  ── GPU                         └─ PaddleOCR 인식 ── GPU
            ├─ Hi-SAM          ── GPU (FP16)
            └─ ocr_client ──── POST /ocr (이미지 + 글자 영역) ──▶
                          ◀──── 인식 텍스트 + 좌표 ─────────────
                │
                ├─▶ 카카오 Local API (좌표 검색)
                └─▶ S3 (모델 가중치 1.7GB, 실행 시 다운로드)

   앱 /metrics ─┐
  OCR /metrics ─┴──▶ Prometheus (15초 수집, 알람 규칙 6개) ──▶ Alertmanager
                              └──▶ Grafana (대시보드 12패널)
```

**왜 컨테이너를 나눴나:** torch(CUDA 13)와 paddlepaddle-gpu(CUDA 12.6)를 한 이미지에 설치하면 각자의 CUDA 라이브러리가 서로를 덮어써 torch가 깨집니다. 그래서 PaddleOCR은 CPU로 고정할 수밖에 없었고, 이것이 전체 처리 시간의 94%를 차지하는 병목이었습니다. OCR을 torch 없는 별도 이미지로 분리해 GPU를 쓸 수 있게 했습니다. (decision-log 15~18번)

## 프로젝트 구조

```
├── app.py                              # FastAPI 서버 진입점 (+ [resource] 측정 미들웨어)
├── ocr_service.py                      # [개인] OCR 전용 서비스 (POST /ocr, GET /health)
├── requirements.txt                    # 앱 의존 패키지 (torch 제외)
├── requirements-torch.txt              # [개인] torch 버전 고정 — Docker 레이어 분리용
├── requirements-ocr.txt                # [개인] OCR 서비스 의존 패키지 (torch 없음)
├── Dockerfile                          # [개인] 앱 이미지 (python:3.12-slim + torch)
├── Dockerfile.ocr                      # [개인] OCR 이미지 (paddle GPU/CPU 빌드 인자)
├── docker-compose.yml                  # [개인] 로컬 검증용: 앱 + OCR 두 컨테이너 (CPU)
├── docker-compose.dev.yml              # [개인] 현재 코드를 컨테이너에 연결 (재빌드 없이 재시작만으로 반영)
├── docker-compose.monitoring.yml       # [개인] Prometheus + Alertmanager + Grafana
├── .dockerignore                       # [개인] 가중치·.git 제외 (빌드 컨텍스트 1.8GB → 1.9MB)
├── .env                                # 환경변수 (KAKAO_API_KEY 등, git 제외)
├── .env.example                        # 환경변수 예시
├── Hi-SAM/                             # Hi-SAM 텍스트 감지 (ymy-k/Hi-SAM, Apache-2.0, 일부 수정 포함)
│   └── pretrained_checkpoint/          # 체크포인트 (별도 다운로드, git 제외)
├── bench/                              # [개인] 성능·정확도·장애 측정 도구
│   ├── e2e_eval.py                     # 지도 1장을 끝까지 처리하고 정답 위치와의 거리(m) 측정
│   ├── georef_offline.py               # 저장된 OCR 결과로 지리좌표 단계만 재실행 (같은 입력 비교)
│   ├── georef_diagnose.py              # 지리좌표 단계별 추적 (어디서 다른 도시가 섞이나)
│   ├── ocr_profile.py                  # OCR 구간별 프로파일
│   ├── ocr_batch_test.py               # 검출+인식 / 인식 전용 배치 비교
│   ├── paddle_only_ocr.py              # Hi-SAM 없이 PaddleOCR만 쓰는 실험
│   ├── ocr_equivalence.py              # OCR 분리 전후 결과 비교
│   ├── responsiveness_test.py          # 처리 중에도 /metrics·health 가 응답하는지
│   ├── alert_test.py                   # 장애를 일으켜 알람이 울리는 시간 측정
│   └── failure/run_failure_test.py     # 컨테이너 장애 시나리오
├── monitoring/                         # [개인] Prometheus 설정·알람 규칙, Alertmanager, Grafana 대시보드(코드로 생성)
├── infra/                              # [개인] Terraform
│   ├── bootstrap/                      # state 저장용 S3 버킷
│   ├── envs/dev/gpu-ec2/               # GPU EC2 + 보안 그룹 + IAM 역할
│   └── iam/                            # Terraform 실행 권한 정책 (JSON)
├── docs/
│   ├── decision-log.md                 # [개인] 의사결정·성능 측정 기록
│   ├── work-log.md                     # [개인] 작업 로그
│   ├── portfolio/                      # [개인] 아키텍처 그림
│   ├── HISAM_CHECKPOINT_DOWNLOAD.md    # Hi-SAM 체크포인트 다운로드 가이드
│   └── SETUP_GEOREF.md                 # 지리좌표 변환 환경 설정 가이드
├── scripts/
│   ├── ec2-user-data.sh                # [개인] EC2 시작 시 자동 실행 스크립트
│   └── visualize_component_filter.py   # 연결 성분 필터 디버그 시각화
├── src/
│   ├── config.py                       # 전역 설정 (환경변수로 OCR 장치·서비스 주소, Hi-SAM FP16·점 수, OCR 모드 지정)
│   ├── metrics.py                      # [개인] Prometheus 지표 정의 (처리 시간, 실패, 결과 품질)
│   ├── georeferencing/
│   │   ├── anchor_builder.py           # 카카오 API 앵커 구축 + 밀집 영역 필터
│   │   ├── homography.py               # 픽셀↔GPS 호모그래피 계산
│   │   ├── ocr.py                      # PaddleOCR 기반 텍스트 인식 파이프라인
│   │   ├── ocr_client.py               # [개인] OCR 서비스 호출 / 로컬 실행 선택
│   │   └── text_detector.py            # Hi-SAM 텍스트 영역 감지 (모델 싱글톤 캐시)
│   ├── gpx_conversion/
│   │   └── gpx_converter.py            # 픽셀 경로 → GPX 변환
│   └── marathon_route_extraction/
│       ├── component_filter.py         # 연결 성분 필터링
│       ├── graph_simplifier.py         # 5단계 그래프 단순화 파이프라인
│       ├── path_extractor.py           # 스켈레톤 → 순서 있는 경로 추출
│       ├── postprocess.py              # 4단계 후처리 파이프라인
│       ├── segformer_unet_b2.py        # SegFormer-UNet B2 모델 / 추론
│       └── unet.py                     # U-Net 모델 / 추론
├── static/
│   └── index.html                      # 단계별 데모 UI
└── weights/                            # 경로 추출 모델 가중치 (별도 다운로드, git 제외)
    ├── segformer_unet_b2_best.pt
    └── unet_best.pt
```

`[개인]` 표시는 팀 프로젝트 이후 개인 작업으로 추가한 파일입니다.

## 설치 및 실행

실행 방법은 세 가지입니다. 공통 준비(1~3) 후 원하는 방식(A/B/C)을 고르세요.

| 방식 | 용도 | OCR 장치 |
|------|------|----------|
| A. 로컬 Python | 코드 수정·디버깅 | CPU (앱 안에서 실행) |
| B. Docker Compose | 컨테이너 2개 구성을 로컬에서 검증 | CPU (OCR 컨테이너) |
| C. AWS GPU (Terraform) | 성능 측정·실서비스 환경 | GPU (OCR 컨테이너) |

### 1. 경로 추출 모델 가중치 다운로드 (SegFormer-UNet B2)

U-Net과 SegFormer-UNet-B2 가중치는 아래 Google Drive에서 다운

- 다운로드 링크: https://drive.google.com/drive/folders/1TKDRnaR8HlrcM2B8wclnDiv_GN5hD2gW?usp=sharing

Drive에 저장된 파일명이 아래와 같다면:

- `model_best.pt`
- `model_last.pt`

프로젝트에서는 아래처럼 이름을 바꿔 `weights/` 폴더에 저장

- 각각 `model_best.pt` -> `segformer_unet_b2_best.pt`와 `unet_best.pt`으로 변경

### 2. Hi-SAM 체크포인트 다운로드

Hi-SAM 코드는 이 레포에 포함되어 있습니다 (입력 해상도 512 대응 수정 포함). 체크포인트만 받으면 됩니다.

```bash
curl -L -o Hi-SAM/pretrained_checkpoint/sam_vit_l_0b3195.pth https://dl.fbaipublicfiles.com/segment_anything/sam_vit_l_0b3195.pth
curl -L -o Hi-SAM/pretrained_checkpoint/hi_sam_l.pth https://huggingface.co/GoGiants1/Hi-SAM/resolve/main/hi_sam_l.pth
```

### 3. 환경변수 설정

```bash
cp .env.example .env
# .env 파일에 카카오 REST API 키 입력
```

```env
KAKAO_API_KEY=여기에_카카오_API_키_입력
```

> 값을 따옴표로 감싸지 마세요. `docker --env-file`은 따옴표를 값의 일부로 읽어 카카오 API가 401을 반환합니다. (decision-log 11번)

### A. 로컬 Python 실행

```bash
pip install -r requirements-torch.txt
pip install -r requirements.txt
pip install einops timm pycocotools absl-py python-Levenshtein tqdm   # Hi-SAM 의존 패키지 (Python 3.13 호환)
python app.py
```

브라우저에서 `http://localhost:8010` 접속. `OCR_SERVICE_URL`을 설정하지 않으면 OCR은 앱 프로세스 안에서 CPU로 실행됩니다.

### B. Docker Compose 실행 (로컬, CPU)

```bash
# 이미지 빌드
docker build -t marathon-gpx:local .
docker build -f Dockerfile.ocr -t marathon-ocr:cpu \
  --build-arg PADDLE_PKG=paddlepaddle==3.2.2 --build-arg PADDLE_INDEX=https://pypi.org/simple .

# OCR 모델 캐시 볼륨 (최초 1회)
docker volume create paddlex-cache

# 실행 — OCR 서비스가 준비(/health)된 뒤 앱이 뜬다
docker compose up
```

브라우저에서 `http://localhost:8010` 접속.

모니터링까지 함께 띄우려면 (코드 수정은 재빌드 없이 `restart app` 으로 반영):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.monitoring.yml up -d
```

| 화면 | 주소 |
|------|------|
| Grafana 대시보드 | `http://localhost:3000` (로그인 없이 보기 전용) |
| Prometheus (알람 규칙·상태) | `http://localhost:9090/alerts` |
| Alertmanager (발생한 알람) | `http://localhost:9093` |

### C. AWS GPU 실행 (Terraform)

사전 조건: AWS CLI 자격증명, Terraform 1.10 이상, EC2 키 페어. 버킷·계정 ID 등은 본인 환경에 맞게 바꿔야 합니다.

```bash
# (최초 1회) state 저장용 S3 버킷 생성
cd infra/bootstrap
terraform init && terraform apply

# GPU EC2 생성 — 내 IP만 SSH(22)·앱(8010) 허용
cd ../envs/dev/gpu-ec2
echo 'my_ip_cidr = "<내 공인 IP>/32"' > my.auto.tfvars
terraform init && terraform apply     # 마지막에 ssh 접속 명령이 출력됨
```

EC2 접속 후 (AMI에 NVIDIA 드라이버·Docker 포함):

```bash
# 가중치 받기 (인스턴스 역할로 S3 models/ 읽기만 허용)
aws s3 cp s3://<버킷>/models/weights/ /opt/marathon/weights/ --recursive
aws s3 cp s3://<버킷>/models/hisam/   /opt/marathon/hisam/   --recursive

# 이미지 빌드 (AWS 안에서 빌드: OCR 337초 + 앱 211초)
docker build -f Dockerfile.ocr -t marathon-ocr:gpu .
docker build -t marathon-gpx:split .

# 두 컨테이너 실행
docker network create marathon
docker run -d --name ocr --network marathon --gpus all -p 8020:8020 -e PADDLE_DEVICE=gpu \
  -v paddlex-cache:/root/.paddlex marathon-ocr:gpu
docker run -d --name app --network marathon --gpus all -p 8010:8010 \
  --env-file .env -e OCR_SERVICE_URL=http://ocr:8020 \
  -v /opt/marathon/weights:/app/weights \
  -v /opt/marathon/hisam:/app/Hi-SAM/pretrained_checkpoint \
  marathon-gpx:split

# (선택) 큐 작업자 — 로컬 작업자와 같은 큐를 나눠 처리. 자격증명은 인스턴스 역할(처리 권한만)
docker build -t marathon-worker:local worker
docker run -d --name worker --network marathon --stop-timeout 600 \
  -e APP_URL=http://app:8010 -e WORKER_NAME=ec2 marathon-worker:local

# GPU 지표 수집기 — 온프레미스 Prometheus 가 :9400 을 수집 (대상 등록은 terraform apply 가 자동으로)
docker run -d --name dcgm --gpus all --cap-add SYS_ADMIN -p 9400:9400 \
  nvcr.io/nvidia/k8s/dcgm-exporter:4.4.2-4.7.1-ubuntu22.04
```

**작업이 끝나면 반드시 삭제합니다** (g4dn.xlarge 시간당 약 $0.53):

```bash
terraform destroy    # "Destroy complete!" 확인. 디스크도 함께 삭제됨
```

## API 엔드포인트

### 앱 (`:8010`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/predict` | 이미지 업로드 → 경로 마스크 예측 |
| `POST` | `/api/postprocess` | 연결 성분 필터링 + 스켈레톤화 |
| `POST` | `/api/extract_path` | 시작점→끝점 순서 경로 추출 |
| `POST` | `/api/auto_extract_path` | 스켈레톤 형태로 시작·끝점을 자동 결정해 경로 추출 |
| `POST` | `/api/run_ocr` | Hi-SAM + PaddleOCR 실행 (OCR 서비스가 설정되면 서비스로 위임) |
| `POST` | `/api/run_georeference` | OCR 결과 → 카카오 API → 호모그래피 계산 |
| `POST` | `/api/georeference` | 위 두 단계를 한 번에 실행 |
| `POST` | `/api/convert_gpx` | 픽셀 경로 → GPX 파일 변환 |
| `GET`  | `/api/health` | 서버 상태 확인 |

### OCR 서비스 (`:8020`, 개인 작업)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/ocr` | multipart(이미지 + Hi-SAM 글자 영역 JSON) → 인식 텍스트 목록 |
| `GET`  | `/health` | PaddleOCR 로드 완료 여부 (compose healthcheck에 사용) |

## 경로 추출 파이프라인 (`/api/predict` → `/api/postprocess` → `/api/extract_path`)

```
마라톤 코스 이미지
  ↓ /api/predict
경로 추출 모델 (SegFormer-UNet B2 / UNet)
  ↓
경로 마스크
  ↓ /api/postprocess
후처리 (노이즈 제거 + fragment 연결 + 스켈레톤화)
  ↓
스켈레톤 이미지
  ↓ /api/extract_path  — graph_simplifier.py
그래프 단순화 5단계
  1. 1차 선형 압축    — degree-2 중간 노드 제거
  2. 노드 병합        — 유클리드 거리 ≤ τ 인 노드를 centroid로 통합
  3. 2차 선형 압축    — 병합 후 새로 생긴 degree-2 노드 재압축
  4. 컴포넌트 연결    — 분리 성분을 가장 가까운 노드쌍으로 반복 연결
  5. 핵심 노드 유지   — leaf(degree 1) + junction(degree ≥ 3)만 남은 G′
  ↓
단순화된 그래프 G′에서 BFS 경로 탐색 (start → end)
  ↓
엣지 픽셀 복원 + 꺾임점 추출 (각도 기반 키포인트)
  ↓
경로 픽셀 좌표 리스트 [(x, y), ...]
```

## 지리좌표 변환 파이프라인 (`/api/run_ocr` → `/api/run_georeference`)

```
이미지
  ↓ Hi-SAM (vit_l, 입력 1024, 점 1500개, GPU에서 FP16)  — text_detector.py   [앱 컨테이너]
텍스트 영역 polygon 목록
  ↓ POST /ocr  — ocr_client.py → ocr_service.py           [OCR 컨테이너, GPU]
  ↓ PaddleOCR 인식 모델만 32장씩 배치 (검출은 Hi-SAM 결과 사용)  — ocr.py
    (y, x) 정렬 → 동일 X 세로 병합
인식된 텍스트 + 픽셀 좌표  [{text, x, y, confidence}, ...]
  ↓ 카카오 Local API (병렬 검색 + 밀집 영역 필터)  — anchor_builder.py
    기호·키워드 블랙리스트 필터 → ThreadPoolExecutor 병렬 검색
    검색 결과가 가장 촘촘한 곳을 중심으로 반경 15km 밖은 이상치 → 이상치는 영역 bbox 안에서 재검색 → 앵커 확정
앵커 (픽셀 좌표 ↔ GPS 좌표) 쌍
  ↓ 호모그래피 + 재투영 오차 기반 이상치 제거  — homography.py
    1차 정제 (≤ 7.0px) → Hough Circle 마커 오프셋 보정
    → 2차 정제 (≤ 7.0px) → findHomography(픽셀/GPS 정규화)
픽셀↔GPS 변환 행렬  HomographyTransform {H, H_inv}
```

## 인프라 (Terraform, 개인 작업)

| 리소스 | 파일 | 설정 |
|--------|------|------|
| GPU AMI | `main.tf` | `Deep Learning Base OSS Nvidia Driver GPU AMI (Amazon Linux 2023)` 최신본을 이름으로 검색 (드라이버 없는 AMI 선택 실수 방지) |
| 보안 그룹 | `security.tf` | SSH(22)·앱(8010)을 변수 `my_ip_cidr`에만 허용. `/32`가 아니면 plan 단계에서 거부 |
| IAM 역할 | `iam.tf` | EC2만 사용 가능, 권한은 S3 `models/*` 읽기 하나 |
| EC2 | `ec2.tf` | g4dn.xlarge, 루트 100GiB gp3(삭제 시 함께 삭제), IMDSv2 강제 |
| 원격 state | `backend.tf`, `bootstrap/` | S3 버킷(버전 관리·암호화·공개 차단·삭제 방지) + `use_lockfile` 네이티브 락 |
| 실행 권한 | `iam/terraform-s3-policy.json` | 서울 리전만, `marathon-*` 역할만, 허용 인스턴스 타입 외 거부. 초안의 권한 상승 경로를 리뷰에서 발견해 차단 (decision-log 19번) |

## 모니터링 (Prometheus · Grafana · Alertmanager, 개인 작업)

앱과 OCR 서비스가 `/metrics`로 숫자를 공개하고, Prometheus가 15초마다 읽어 갑니다(pull 방식). 기록은 메모리에서만 일어나 요청 처리에 영향을 주지 않습니다.

| 지표 | 내용 |
|------|------|
| `marathon_stage_seconds` | Hi-SAM / OCR 단계별 처리 시간 |
| `marathon_stage_failures_total` | 어느 단계에서 실패했는지 |
| `marathon_request_vram_peak_bytes` | API별 GPU 메모리 최대 사용량 |
| `marathon_ocr_service_queue_wait_seconds` | OCR 요청 대기 시간 (커지면 OCR 컨테이너를 늘릴 신호) |
| `marathon_georef_anchors`, `…_reprojection_error_px`, `marathon_gpx_route_km` | 결과 품질 (앵커 수, 재투영 오차, GPX 경로 길이) |
| `marathon_georef_suspicious_total` | 품질 기준 미달 결과 수 (앵커 8개 미만, 오차 7px 초과, 경로 100km 초과) |

| 알람 | 조건 | 검증 결과 (`bench/alert_test.py`) |
|------|------|------------------------------------|
| ServiceDown | 앱·OCR이 1분 넘게 응답 없음 | OCR 정지 → 81초에 발생, 재시작 → 43초에 해제 |
| GeorefQualityLow | 최근 10분 사이 품질 기준 미달 결과 발생 | 품질 기준 미달 결과 주입 → 22초에 발생 |
| StageFailures / OcrServiceFailures | 단계별·OCR 실패 발생 | |
| GpuMemoryHigh | GPU 메모리 14GB 초과 (T4 16GB) | |
| OcrQueueBacklog | OCR 대기 p95 10초 초과 | |

모니터링을 붙이면서 찾은 버그 두 가지 (decision-log 29번):
- **이벤트 루프 블로킹**: `async def` 엔드포인트 안에서 수 분짜리 추론을 직접 실행해, 처리 중에는 `/metrics`와 다른 요청까지 멈췄습니다. 무거운 엔드포인트를 스레드풀에서 실행하도록 바꿨습니다.
- **첫 번째 실패에 알람이 안 울림**: 라벨별 카운터가 처음 발생 순간 1로 생겨나 Prometheus가 증가를 보지 못했습니다. 가능한 라벨을 시작 시 0으로 만들어 두었습니다.

## 측정 도구 (`bench/`, 개인 작업)

| 도구 | 용도 |
|------|------|
| `[resource]` 로그 (`app.py`) | 요청별 처리 시간·RAM·VRAM·CPU |
| `e2e_eval.py` | 브라우저와 같은 순서로 지도 1장을 처리하고 **정답 위치와의 거리(m)** 측정 |
| `georef_offline.py`, `georef_diagnose.py` | 같은 OCR 결과로 지리좌표 단계만 재실행, 단계별로 어디서 결과가 틀어지는지 추적 |
| `ocr_profile.py`, `ocr_batch_test.py` | OCR 구간별 시간 분해, 검출+인식 / 인식 전용 비교 |
| `ocr_equivalence.py`, `run_ocr_equivalence.ps1` | Hi-SAM 결과를 고정한 뒤 OCR 분리 전후 출력 비교 |
| `responsiveness_test.py`, `alert_test.py` | 처리 중 응답성, 장애 주입 시 알람 발생·해제 시간 |

벤치마크 지도 이미지는 대회 측 저작물이라 레포에 포함하지 않았습니다.

## 유틸리티 (디버깅)

연결 성분 필터 결과 시각화:

```bash
python scripts/visualize_component_filter.py --input-mask sample_route.png --min-component-area 130
```

## 로드맵

- [x] 컨테이너화 (레이어 분리, 가중치 분리)
- [x] 성능 병목 진단 → OCR 서비스 분리 + GPU 전환
- [x] 정답 위치 기준 정확도 검증 → 앵커 영역 필터·Hi-SAM 설정 수정
- [x] Hi-SAM FP16·점 수 조정, OCR 인식 전용 배치 (정확도 유지, 154s → 약 17s)
- [x] Terraform으로 GPU EC2 코드화 + S3 원격 state·락
- [x] Prometheus/Grafana/Alertmanager 모니터링, 장애 주입으로 알람 검증
- [ ] 변환 행렬 검증 강화 (오차 기준·앵커 수·경로 길이를 못 맞추면 실패로 응답)
- [ ] 알람 전달 채널 (Slack·Discord), GPU 지표 (DCGM exporter)
- [ ] 이미지를 ECR에 두고 user_data로 자동 기동 (준비 시간 약 11분 → 수 분)
- [ ] 도메인 + HTTPS 연결
- [ ] 큐 기반 비동기 처리 + GPU Scale-to-Zero, k6 부하 테스트
- [ ] Kubernetes (로컬 k3s → EKS)

## 참고 사항

- GPU가 없으면 CPU로 자동 전환됨 (Hi-SAM 추론은 CPU에서 수 분이 걸리고, FP16은 GPU에서만 적용)
- Hi-SAM FP16·점 수, OCR 인식 전용 모드는 환경변수 `HISAM_FP16`, `HISAM_TOTAL_POINTS`, `OCR_REC_ONLY`로 바꿀 수 있음
- 앱 이미지 안에서는 PaddleOCR을 CPU로 고정 (torch와 CUDA 라이브러리 충돌). GPU OCR은 OCR 컨테이너(`PADDLE_DEVICE=gpu`)에서만 사용
- Windows 환경에서는 PaddlePaddle `3.0 이상 3.3 미만` 버전 사용 권장 (oneDNN 버그 회피)
- Hi-SAM은 [ymy-k/Hi-SAM](https://github.com/ymy-k/Hi-SAM) (Apache-2.0) 코드를 포함하며, 수정한 파일에는 상단에 수정 표시가 있습니다
