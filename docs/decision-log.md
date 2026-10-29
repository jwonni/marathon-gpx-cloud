# 의사결정 · 성능 기록

포트폴리오/블로그용 원자료. 결정할 때마다, 측정할 때마다 바로 적는다.

## 측정 규칙

- 처리 시간은 **항상 같은 벤치마크 이미지**로 잰다 → **`마라톤코스4.webp`** (프로젝트 루트, 128KB)
- 측정 도구는 `bench/` 에 있다: `ocr_profile.py`(구간별 프로파일), `paddle_bench_sizes.py`(크롭 크기별 CPU/GPU), `prepared.json` + `crops/`(전처리 완료된 크롭 124개 × 2)
- 수치 옆에 조건(인스턴스 타입, 디바이스, 이미지 태그)을 같이 적는다. 조건 없는 숫자는 비교가 안 된다.
- 처리 시간·메모리는 `app.py` 미들웨어의 `[resource]` 로그 줄을 그대로 옮긴다.

## 성능 · 비용 지표

| 지표 | 기준값 (조건, 날짜) | 개선 후 (조건, 날짜) | 무엇을 바꿨나 |
|---|---|---|---|
| Docker 빌드 컨텍스트 | 1.8GB | 1.9MB | `.dockerignore`로 가중치·.git 제외 |
| Docker 빌드 시간 (캐시 없음) | 2500.5s (로컬 Docker Desktop, 가정 회선, 2026-09-15). 구성: pip 다운로드 약 1765s(휠 107개·3.40GB·평균 1.95MB/s) / 이미지 export 506s / pip 설치 158s / 기타 약 50s | | |
| 이미지 크기 | 11.4GB (`docker images`, torch 2.14.0+cu130, 2026-09-15) | | |
| Docker 빌드 시간 (2차, 캐시 비어 있음) | 1430s. 구성: torch 레이어 792s / 나머지 레이어 178s / export 457s. **다운로드 속도가 1차 1.95MB/s → 2차 4.5~4.8MB/s로 회선 조건이 달라 1차와 직접 비교 불가** | | |
| requirements.txt 수정 후 재빌드 시간 | 전체 재설치 1430~2500s (레이어 분리 전, 회선 속도에 따라 변동) | **248s** (2026-09-17, `requests` 1줄 추가). torch 레이어 CACHED / 의존성 레이어 110.6s(157개 중 155개 pip 캐시 재사용, 신규 다운로드 2개) / export 135.9s | torch 레이어 분리 + pip 캐시 마운트 |
| 지도 1장 전체 처리 시간 | **EC2 g4dn.xlarge(T4): 약 154초** (911x642 지도, 앱 1개 + PaddleOCR CPU, 2026-09-16) | **약 25.7초 (6.0배)** (같은 인스턴스·이미지, 앱 + OCR 서비스 GPU 분리, 2026-10-28) | PaddleOCR GPU 전환 + OCR 서비스 분리 (18번) |
| └ 구간별 (분리 후) | | predict 0.63s / postprocess 0.06s / auto_extract_path 0.21s / convert_gpx 0.01s / **run_ocr 23.67s** (Hi-SAM 6.30s + OCR 서비스 17.30s) / run_georeference 1.09s | |
| └ 구간별 (EC2 GPU) | predict 0.57s / postprocess 0.06s / auto_extract_path 0.16s / convert_gpx 0.01s / **run_ocr 151.85s** / run_georeference 1.01s | | |
| └ run_ocr 내부 | Hi-SAM(GPU) 6.17s (predict 5.72s, masks 116, polygon 131) + **PaddleOCR(CPU) 약 145s** = 전체의 94% | | |
| 모델 워밍업 시간 | 로컬 CPU 약 54s (segformer 20.9s + Hi-SAM 32.9s) | **EC2 GPU 17.0s** (segformer 4.4s + Hi-SAM 3.9s + PaddleOCR 8.7s) | CPU → T4 GPU |
| VRAM 최대 사용량 | — | 5261MB (run_ocr 중, T4 16GB 중) | |
| 월 인프라 비용 (상시 보관분) | 약 $0.41 (2026-09-15): ECR 이미지 v1 압축 3.47GiB ≈ $0.37 + S3 가중치 약 1.7GB ≈ $0.04. 컴퓨팅 리소스 없음 | | |
| ECR 첫 푸시 이미지 크기 (압축) | 3.47GiB (`v1`, 2026-09-15) | | |

## 결정 기록

형식: **상황 → 선택지 → 결정과 이유 → 결과(수치)**. 이유는 면접에서 말할 수 있게 본인 말로 다시 쓸 것.

### 1. 서비스 운영 형태: 프론트 상시 + 백엔드 수동 apply
- 상황: 월 예산 1~3만 원. EKS 컨트롤플레인만 상시 가동해도 시간당 약 $0.10 (월 약 $73)
- 선택지: EKS 상시 가동 / 방문자 "깨우기" 버튼 / 스케줄 가동 / 수동 apply
- 결정: 수동 apply. 깨우기 버튼은 외부인이 내 비용을 발생시킬 수 있는 경로라 기각
- 결과:

### 2. 진행 순서: 인프라 먼저 → 앱 먼저
- 상황: 원래 계획은 VPC → EKS → 앱 배포
- 결정: GPU EC2 한 대에서 컨테이너 먼저 검증 후 인프라 확장
- 이유: 컨테이너 안 CUDA 문제와 EKS 문제를 동시에 디버깅하지 않기 위해
- 결과:

### 3. 베이스 이미지: `python:3.12-slim` (CUDA 베이스 이미지 미사용)
- 선택지: `nvidia/cuda` 런타임 이미지 / 파이썬 slim 이미지
- 결정: slim. PyPI의 torch 휠이 CUDA 런타임(`nvidia-*` 패키지)을 자체 포함하므로 CUDA 베이스는 중복
- 결과: 이미지 11.4GB. torch 기본 휠이 CUDA 13.0 빌드(`2.14.0+cu130`)로 설치됨
- 레이어 구성 (`docker history`, 레이어 분리 후): torch 레이어 5.8GB / 나머지 의존성 1.72GB / apt 217MB / 코드 1.94MB. 합계 약 8GB로 `docker images` 표시값(11.4GB)과 다름 → 경량화 비교 기준은 `docker history` 합계 또는 ECR 압축 크기로 통일할 것
- 확인: 컴파일된 GPU 아키텍처 `sm_75 sm_80 sm_86 sm_90 sm_100 sm_120` → T4(sm_75) 지원 확인
- 후속 조건: 호스트 NVIDIA 드라이버가 CUDA 13을 지원해야 함 → EC2 AMI 선택 시 `nvidia-smi`로 확인

### 4. 모델 가중치를 이미지에서 분리
- 상황: 가중치 약 1.7GB (SegFormer 375MB + SAM ViT-L 1.25GB + Hi-SAM 140MB)
- 결정: 이미지에 넣지 않고 실행 시 마운트 (이후 S3에서 받기)
- 이유: 가중치가 바뀔 때마다 이미지 재빌드·재배포 방지, 스케일아웃 시 이미지 풀 시간 단축
- 결과: 빌드 컨텍스트 1.8GB → 1.9MB

### 5. GPU 인스턴스: g4dn.xlarge, 한도 4 vCPU 요청
- 이유: 서울 리전 최저가 GPU (T4 16GB). 신규 계정은 큰 한도 요청이 거절되기 쉬워 최소 단위로 요청
- 결과:

### 6. Terraform state 락: S3 네이티브 락 (DynamoDB 미사용)
- 상황: 흔한 레퍼런스는 S3 + DynamoDB 락
- 결정: Terraform 1.10+의 `use_lockfile = true`. 1.11부터 DynamoDB 방식은 deprecated
- 결과 (2026-10-29): 적용·검증 완료 → 20번

### 7. 의존성 누락·버전 미고정 — 컨테이너가 잡아낸 문제
- 발견: 컨테이너 첫 실행에서 `[warmup] segformer/unet 로드 실패: pip install transformers`. 로컬 `python app.py`에서는 정상 동작
- 원인
  - `transformers`를 함수 안에서 import (`segformer_unet_b2.py:199`) → 파일 상단만 봐서는 의존성이 안 보임
  - 로컬에는 개발 버전(`4.57.1.dev0`)이 설치돼 있었고 `requirements.txt`에는 없었음
  - torch도 하한만(`>=2.0.0`) 지정 → 로컬 `2.9.1+cpu`, 이미지 `2.14.0+cu130`로 서로 다름
- 조치
  - 이미지 안에서 전체 모듈 import 검사 + 함수 안 import 목록 점검으로 누락 전수 확인. 실제 누락은 `transformers` 1개 (`matplotlib`은 디버그 시각화 전용이라 제외)
  - 재빌드 전 임시 컨테이너에서 설치·모델 로드 먼저 검증 (4.57.6에서 SegFormerUNet 로드 15.1s)
  - `transformers>=4.57,<5`, `torch==2.14.0`, `torchvision==0.29.0` 고정
- 교훈: (본인 말로)

### 8. Dockerfile 레이어 분리 + pip 캐시 마운트
- 상황: 패키지 1개 추가만으로 pip 레이어 전체가 무효화되어 2500s 재빌드
- 선택지: 그대로 감수 / 기존 레이어 뒤에 `pip install transformers` 한 줄 덧붙이기(빠르지만 requirements.txt와 실제 이미지가 어긋남) / 레이어 구조 개선
- 결정: torch를 `requirements-torch.txt`로 분리해 먼저 설치하고, pip 다운로드는 BuildKit 캐시 마운트로 보존. 어차피 재빌드가 불가피한 시점이라 구조 변경의 추가 비용이 없음
- 결과 (2026-09-17 측정, `requirements.txt`에 `requests` 1줄 추가): **재빌드 248s**
  - torch 레이어(5.8GB): CACHED
  - 의존성 레이어: 110.6s — 157개 패키지 중 155개를 pip 캐시에서 설치, 신규 다운로드 2개 → **다운로드 병목(9번)과 무관해짐**
  - export: 135.9s
  - 비교 기준: 레이어 분리 전에는 같은 수정이 전체 재설치 = 1430~2500s (회선 속도에 따라 변동). 조건이 다른 두 값이라 "N배"로 단정하지 않고 범위로 기록
- 남은 병목: 설치·export 모두 1.72GB 의존성 레이어 크기에 비례. 앱 이미지에서 paddle 계열을 빼면 더 줄어들 여지 있음 (OCR 서비스 분리 완료 후 검토)

### 9. 빌드 다운로드 병목 진단 — 도구 교체 전에 먼저 측정
- 상황: 1차 빌드 다운로드 평균 1.95MB/s(16Mbps). 원인 후보: ① 회선 ② Docker Desktop(WSL2) 네트워크 경로 ③ pip의 순차·단일 연결 다운로드
- 측정 (같은 PyPI 파일 `torch-2.14.0` 앞 100MB, 2026-09-15)
  - Windows 직접, 연결 1개: 7.29 → 재측정 10.60MB/s
  - Windows 직접, 연결 4개 동시: 11.06MB/s (합계)
  - 컨테이너 안, 연결 1개: 10.23MB/s
- 해석
  - 컨테이너 안팎 속도 동일 → ② 아님
  - 병렬 4개로도 거의 안 늘어남 → 단일 연결이 이미 회선 상한(약 11MB/s) 근처 → ③의 영향 작음. uv 등 병렬 다운로더로 교체해도 이득 작을 것
  - 같은 조건의 빌드에서 1.95 / 4.5 / 측정 시 7~11MB/s로 시간대별 변동이 큼 → ① 회선(와이파이·통신사) 변동이 주원인
- 결정: 로컬 빌드는 도구 교체 대신 "다시 받지 않기"(레이어 분리·캐시 마운트)로 대응. 근본 해결은 AWS 안에서 빌드 (ECR 푸시 단계에서 결정)
- 교훈: 조건이 다른 두 빌드 시간(2500s vs 1430s)을 비교하면 개선 효과를 잘못 주장하게 된다

### 10. 측정 도구가 꺼져 있던 문제 — PaddleOCR이 root 로거 레벨 변경
- 발견: 컨테이너에서 요청이 성공하는데 `[resource]` 로그가 하나도 안 찍힘. `[warmup] PaddleOCR loaded`, `all models ready`도 누락
- 원인: PaddleOCR(PaddleX) 로드 시 root 로거 레벨이 `INFO` → `WARNING`으로 바뀜 (로드 전후 레벨 출력으로 확인). 이후 앱의 INFO 로그가 전부 조용히 버려짐. 로컬 실행에서도 동일했을 것
- 선택지: PaddleOCR 로드 직후 root 레벨 복구 (라이브러리가 나중에 또 바꾸면 재발) / 앱 로거에 레벨 직접 지정
- 결정: 앱이 쓰는 로거(`src`, `resource`, `warmup` 등)에 `INFO` 레벨을 직접 지정. root 레벨을 누가 언제 바꾸든 영향 없음
- 검증: 재빌드 없이 수정한 `app.py`만 임시 컨테이너에 마운트 → PaddleOCR 로드 후에도 root는 `WARNING`인 채로 `[resource]` 정상 출력
- 교훈: 측정값이 "안 나오는 것"도 확인해야 한다. 도구가 조용히 꺼져 있으면 기준값 자체가 없다

### 11. 카카오 API 401 — `.env` 따옴표 해석 차이
- 발견: 컨테이너에서 지오레퍼런싱 실패 `유효 카카오 검색 결과 부족: 0개`. 로그상 모든 카카오 요청이 HTTP 401
- 원인: `.env`에 `KAKAO_API_KEY="..."`처럼 따옴표 사용. `python-dotenv`는 따옴표를 벗기지만 `docker --env-file`은 값의 일부로 취급 → 컨테이너 안 키 길이 34자(32자 + 따옴표 2개)
- 조치: `.env`에서 따옴표 제거, `.env.example`에 주의 문구 추가
- 교훈: 같은 설정 파일도 읽는 도구마다 문법이 다르다. 나중에 K8s Secret, SSM으로 옮길 때도 값에 따옴표가 섞이지 않게 주의
- 검증: 따옴표 제거 후 컨테이너에서 `run_georeference` 200 (2026-09-15)

### 12. [알려진 문제] Hi-SAM 결과의 무작위성 — 같은 이미지가 성공/실패를 오감
- 발견: 같은 컨테이너, 같은 지도로 1차 `run_ocr` 500 (`Hi-SAM 마스크 예측 실패`), 2차 200 → 지오레퍼런싱까지 성공
- 원인(추정): `auto_mask_generator.py:133`에서 글자 영역의 점 1000개를 `torch.randperm`으로 무작위 추출, 시드 고정 없음. 점수 기준(0.4)이나 NMS 기준(0.6)을 통과하는 마스크가 0개면 `None` 반환
- 상태: 미해결. GPU 환경으로 옮긴 뒤 같은 이미지 N회 반복 실행으로 **실패율을 먼저 측정**하고 대응 결정 (시드 고정 / 실패 시 재시도 / 점 개수·임계값 조정 등)
- 결과: 실패율 (측정 예정) → (개선 후)

### 13. [미해결] EC2 Session Manager 연결 실패
- 상황: g4dn.xlarge (Deep Learning Base OSS Nvidia Driver GPU AMI, Ubuntu 24.04), 인스턴스 프로파일 `marathon-ec2-role` 연결, 인바운드는 8010(내 IP)만
- 증상: Session Manager "연결되지 않음". 최신 오류 (10:44Z, 10:53Z 동일)
  `SSM Agent unable to acquire credentials: no valid credentials could be retrieved for ec2 identity. Default Host Management Err: ... AccessDeniedException: Systems Manager's instance management role is not configured for account`
- 해석: 앞부분(인스턴스 역할로 자격증명 획득 실패)이 실제 원인. 뒷부분(Default Host Management)은 역할 실패 시 시도하는 대체 수단의 실패라 원인 아님
- 시도: 역할에 `AmazonSSMManagedInstanceCore` 추가 → 재부팅 (총 2~3회) → 변화 없음
- 남은 가설: 신뢰 관계(`ec2.amazonaws.com`) 오류 / 인스턴스 메타데이터(IMDS) 설정 / 기본 VPC 라우팅(인터넷 경로) / AMI의 SSM 에이전트 상태
- 다음 진단: EC2 Instance Connect(보안 그룹 22번을 서울 서비스 대역 `13.209.1.56/29`에만 개방)로 접속해 `aws sts get-caller-identity`, SSM 엔드포인트 통신, 에이전트 로그 확인
- 결과: 인스턴스 종료 (2026-09-15), 다음 날 재시도

### 14. 이미지 크기 때문에 EC2 배포 실패 — `no space left on device`
- 발견 (2026-09-16): 사용자 데이터 스크립트가 `docker pull` 중 실패
  `docker: failed to register layer: open /usr/local/lib/python3.12/site-packages/triton/backends/nvidia/include/cuda_fp16.hpp: no space left on device`
- 원인: GPU AMI 기본 루트 볼륨 + 압축 해제된 이미지(8GB 이상) + 가중치 1.7GB 가 디스크 용량 초과
- 조치: EBS 볼륨 100GiB 로 확장 → `growpart` + 파일시스템 확장 → 재시도
- 의미: **이미지 크기(11.4GB / ECR 압축 3.47GiB)가 추상적인 수치가 아니라 실제 배포 실패의 원인**이었다.
  경량화(멀티스테이지, API/GPU 워커 이미지 분리, 불필요 패키지 제거)의 근거가 되는 구체적 사건
- 파생: 스케일 아웃 시 노드마다 이 용량이 필요 → 노드 디스크 비용과 이미지 pull 시간에 직접 영향

### 15. 진짜 병목은 GPU가 아니라 CPU OCR (2026-09-16 측정)
- 측정: EC2 g4dn.xlarge(T4)에서 지도 1장 약 154초. 이 중 `run_ocr` 151.85초, 그 안에서 Hi-SAM(GPU)은 6.17초뿐 → **PaddleOCR(CPU)이 약 145초로 전체의 94%**
- 구조: `ocr.py:380-384`가 크롭마다 PaddleOCR을 기본 1회 + 재시도 1회 = 총 262회(크롭 131개) **순차 호출**. 호출당 약 0.55초
- 조건: PaddleOCR은 torch와의 CUDA 충돌을 피하려고 CPU 고정(`config.py` `PADDLE_DEVICE="cpu"`). g4dn.xlarge는 vCPU가 4개뿐 (로컬 PC는 16개)
- 의미: GPU 인스턴스를 키워도 이 구간은 거의 안 빨라진다. "GPU 서빙 최적화"의 대상이 Hi-SAM이 아니라 OCR 파이프라인임
- 개선 후보 (비용 낮은 순): ① 기본 결과 신뢰도가 높으면 재시도 생략 (최대 2배) ② 크롭을 리스트로 묶어 배치 추론 ③ 크롭 처리 병렬화 ④ PaddleOCR을 GPU로 (paddlepaddle-gpu, torch와 VRAM 공유 검증 필요) ⑤ vCPU 많은 인스턴스
- 결과: (개선 후 측정 예정)

### 16. OCR 병목의 실제 구성 — 프로파일링 결과 (2026-09-16)
벤치마크 이미지: `마라톤코스4.webp` (128KB). 프로젝트 코드는 수정하지 않고 별도 스크립트로 측정.

**(A) 구간별 프로파일 — 로컬 PC 컨테이너(CPU), 크롭 124개**

| 구간 | 시간 | 비중 |
|---|---|---|
| Hi-SAM 글자영역 검출 | 138.76s | 51.5% (EC2 GPU에서는 6.17s) |
| 크롭 좌표 정리 | 0.08s | 0.0% |
| 크롭 전처리 + 파일 저장 | 8.82s | 3.3% |
| PaddleOCR 모델 로드 | 12.13s | 4.5% (요청마다가 아니라 1회) |
| **OCR 기본 호출** | **46.14s** | 17.1% (372 ms/call) |
| **OCR 재시도 호출** | **63.71s** | 23.6% (514 ms/call) |
| 결과 병합·필터·정렬 | 0.01s | 0.0% |

- **가설 기각**: "전처리·디스크 IO가 88초"라고 추정했으나 실제 전처리는 8.82초. **OCR 호출 자체가 약 110초로 압도적**
- 재시도 호출은 정확도상 필요하다는 사용자 판단에 따라 제거 후보에서 제외

**(B) oneDNN(CPU 가속) 영향 — 같은 크롭 124개**

| 설정 | OCR 호출 합계 |
|---|---|
| `FLAGS_use_mkldnn=False` (현재 코드) | 115.02s |
| `FLAGS_use_mkldnn=True` | 114.56s |

→ 차이 없음. `ocr.py:164`의 Windows 회피용 설정은 리눅스 성능에 영향 없음. **후보에서 제외**

**(C) PaddleOCR GPU 전환 검증 (EC2 g4dn.xlarge)**

| 실험 | 결과 |
|---|---|
| torch + `paddlepaddle-gpu` 동시 설치 | ❌ torch 붕괴 (`libtorch_cuda.so: undefined symbol: ncclCommResume`) |
| torch 제거 후 paddle GPU 단독 | ✅ 정상 |
| 합성 크롭 288×96 | CPU 147ms → GPU 30ms (4.9배) |
| 합성 크롭 568×152 | CPU 286ms → GPU 55ms (5.2배) |
| 라이브러리 용량 | +4.9GB (site-packages 7.1G → 12G) |

- 원인: torch(cu13.0)와 paddle(cu12.6)이 각자의 `nvidia-*` CUDA 라이브러리를 같은 site-packages에 설치해 서로 덮어씀. `config.py`의 "torch와 CUDA 충돌 방지" 주석이 가리키던 문제
- **결론: 같은 이미지 안에서 paddle GPU + torch는 불가. OCR을 별도 컨테이너/서비스로 분리해야 함** → 로드맵의 "API 서버 / GPU 워커 분리"와 동일한 방향
**(D) 실측 — 같은 EC2 g4dn.xlarge, 같은 실제 크롭 124개 (2026-09-17)**

| | CPU (vCPU 4) | GPU (T4) | 배율 |
|---|---|---|---|
| 기본 호출 | 29.52s (238 ms/call) | 4.75s (38 ms/call) | 6.2배 |
| 재시도 호출 | 50.65s (408 ms/call) | 8.00s (65 ms/call) | 6.3배 |
| **OCR 호출 합계** | **80.17s** | **12.75s** | **6.3배 (67.4s 절감)** |

- 측정 방법: 앱 이미지에서 torch 제거 후 `paddlepaddle-gpu==3.2.2`(cu126) 설치, `bench/run_gpu_ocr_bench.sh` + `bench/ocr_profile.py ocronly`. src.config 의 torch import 는 측정 스크립트에서만 가짜 모듈로 대체
- 합성 크롭 기준 추정(약 5배)보다 실제 효과가 더 큼
- 참고: 같은 CPU 측정이 로컬 Docker Desktop(16코어) 115s보다 EC2 4코어가 80s로 더 빠름 → Docker Desktop VM 자원 제한 영향으로 보임. **CPU 수치는 반드시 같은 기계끼리 비교할 것**
- 확인 필요: 앱 전체 측정에서 `run_ocr`은 151.85s였는데 (Hi-SAM 6.17s + OCR 호출 약 85s(크롭 131개 환산) + 전처리)로 설명되지 않는 약 40~50s가 남음. 같은 프로세스 안 torch와의 CPU 경합 등 가능성. **OCR 분리 후 앱 전체를 다시 재서 확인**
- 설치 비용: `paddlepaddle-gpu` 다운로드·설치에 4~6분 (중국 CDN). 실행 시점 설치는 불가 → OCR 전용 이미지에 빌드 시점에 넣어야 함
- **결정: PaddleOCR GPU 전환 진행. 방법은 OCR 전용 컨테이너 분리 (torch와 CUDA 라이브러리 충돌 때문)**

### 17. OCR 서비스 분리 설계 (2026-09-17)
- 자르는 지점: `run_ocr(Hi-SAM 결과, 크롭 폴더)` 함수 경계
  - 선택지: ① PaddleOCR 호출(`run_paddle_on_path`)만 원격화 — 크롭 248장을 네트워크로 주고받음 ② `run_ocr` 통째로 원격화 — 원본 이미지 + Hi-SAM JSON 만 전송
  - 결정: ②. 요청당 왕복 1회, 크롭 전처리~후처리가 OCR 쪽에 함께 있어 앱 변경은 호출 두 곳(`app.py` `/api/georeference`, `/api/run_ocr`)뿐
- 전환 방식: 환경변수 `OCR_SERVICE_URL`. 비어 있으면 기존처럼 앱 프로세스 안에서 실행 → 로컬 `python app.py` 개발 흐름 유지
- 구성
  - `ocr_service.py`: `POST /ocr`(multipart: 이미지 + Hi-SAM JSON) → `{"ocr_results": [...]}`, `GET /health`
  - `Dockerfile.ocr`: torch 없음. paddle 은 빌드 인자로 GPU판(기본, cu126) / CPU판(로컬 검증) 선택
  - `requirements-ocr.txt`: paddleocr 버전 범위는 앱 이미지와 동일하게 유지 (정확도 비교 조건 통일)
  - `src/config.py`: torch 를 선택적 import 로 변경, `PADDLE_DEVICE`·`OCR_SERVICE_URL`을 환경변수로
- 동시성: PaddleOCR 예측기의 동시 호출 안전성이 보장되지 않아 서비스 안에서 lock 으로 순차 처리. 처리량 확장은 컨테이너 수로 해결 (이후 KEDA 대상)
- 검증: 기존 앱 이미지에 수정 파일을 마운트해 ① torch 를 막은 상태에서 `ocr_service` import·`/health` 정상 ② torch 있는 상태에서 `app` import 정상 (`OCR_SERVICE_URL` 미설정 → 기존 동작)
- 앱 쪽 변경 (2단계)
  - `src/georeferencing/ocr_client.py` 신규: `run_ocr_auto()` 가 `OCR_SERVICE_URL` 유무로 원격/로컬 선택. 응답 대기 기본 600초(`OCR_SERVICE_TIMEOUT`)
  - `app.py`: `run_ocr` 호출 2곳을 `run_ocr_auto` 로 교체, OCR 서비스 사용 시 워밍업에서 PaddleOCR 로드 생략
  - `requirements.txt`: `requests` 명시 (이미지에는 간접 설치돼 있었지만 7번 교훈에 따라 명시)
- 검증 (2단계): 로컬 OCR 서비스 컨테이너(CPU판)에 수정한 앱 코드로 요청 → HTTP 200, 빈 입력에 `[]` 응답. multipart 업로드·JSON 전달·응답 파싱 경로 확인
- 정확도 동일성 검증 (2026-10-28): 분리 후 "정확도가 낮아진 것 같다"는 의심을 확인하려고 Hi-SAM 결과를 하나로 고정(`bench/eq/hisam.json`)한 뒤 같은 입력을 ① 앱 안 로컬 실행 ② OCR 서비스 HTTP 호출로 처리 (`bench/run_ocr_equivalence.ps1`, CPU)
  - 결과: 텍스트 106개 / 106개, 텍스트·좌표·신뢰도 **완전히 동일** (결과 JSON 바이트 단위 일치)
  - 결론: 서비스 분리는 정확도에 영향 없음. 체감한 차이는 12번 Hi-SAM 무작위성에서 온 것으로 판단
  - 교훈: 무작위 단계(Hi-SAM)를 고정하지 않으면 전후 비교 자체가 성립하지 않음. 비교 실험은 무작위 입력을 먼저 고정할 것

### 18. 분리 후 GPU 전체 재측정 — 154초 → 25.7초 (2026-10-28)
- 조건: EC2 g4dn.xlarge (T4), AMI Deep Learning Base OSS Nvidia Driver GPU (Amazon Linux 2023, 드라이버 595.91.07), 루트 100GiB. 9/16 기준값과 같은 인스턴스 타입·벤치마크 이미지(`마라톤코스4.webp`, 911x642)·측정 방법(브라우저로 전체 진행, `[resource]` 로그)
- 구성: 컨테이너 2개, 같은 docker 네트워크. `app`(`marathon-gpx:split`, torch GPU) → HTTP → `ocr`(`marathon-ocr:gpu`, paddle GPU). compose 플러그인이 AMI에 없어 `docker run` 2회로 실행
- 빌드 (EC2에서, 캐시 없음): OCR 이미지 337s(7.26GB) / 앱 이미지 211s(7.72GB). 가중치 S3 다운로드 1.7GB 8s. 집 회선 빌드(1430~2500s)보다 크게 짧음 → 9번 "근본 해결은 AWS 안에서 빌드"를 뒷받침
- 워밍업: 앱 11.32s (segformer 5.84s + Hi-SAM 5.46s, PaddleOCR 로드 생략) / OCR 서비스 PaddleOCR GPU 로드 16.56s. 두 컨테이너가 병렬로 뜸
- 결과
  - 전체 약 153.7s → **약 25.7s (6.0배)**
  - `run_ocr` 151.85s → **23.67s (6.4배)** = Hi-SAM 6.30s + OCR 서비스 17.30s (+ HTTP 왕복 약 0.07s)
  - OCR 결과 텍스트 106개 (17번 동일성 검증과 같은 개수)
- 16번 (D)의 미해결 의문 해소: 분리 전 `run_ocr`에서 설명되지 않던 40~50s가 분리 후에는 사라짐 (6.30 + 17.30 ≈ 23.67). 같은 프로세스 안 torch와 PaddleOCR의 CPU 경합이라는 가설과 일치. 단 직접 증명한 것은 아니므로 "추정"
- 남은 병목: OCR 서비스 17.30s 중 순수 OCR 호출은 약 12.75s(16번 D), 나머지는 크롭 전처리 등. Hi-SAM 6.30s
- 비용: 약 3~4시간 사용, g4dn.xlarge 시간당 약 $0.53


### 19. Terraform 실행 권한 — 최소 권한 + 권한 상승 경로 차단 (2026-10-28)
- 상황: 로컬 자격증명(`Terraform-s3`)에 EC2 조회 권한조차 없어 Terraform으로 EC2·SG·IAM 역할을 만들 수 없음
- 결정: `Terraform-s3`에 인라인 정책 `terraform-marathon-ec2` 부여 (`infra/iam/terraform-s3-policy.json`)
  - EC2 전체 허용, 단 **서울 리전만** (`aws:RequestedRegion`)
  - RunInstances는 **g4dn.xlarge, t3.micro/small/medium 외 거부** (키 유출 시 고가 인스턴스 채굴 방지)
  - IAM은 이름이 **`marathon-*`인 역할·인스턴스 프로파일만** 관리, 붙일 수 있는 정책은 **허용 목록 3개**(ECR 읽기, SSM, `marathon-s3-models-read`), PassRole은 EC2에게만
- 초안의 결함 (리뷰에서 발견): `iam:PutRolePolicy`를 허용했었음 → `marathon-x` 역할에 "모든 작업 허용" 인라인 정책 작성 → 그 역할로 EC2 실행 → SSH 접속 → **계정 관리자 권한 획득** 경로가 열려 있었음
  - 조치: `PutRolePolicy`·`UpdateAssumeRolePolicy` 제거. S3 읽기는 관리자가 미리 만든 관리형 정책 `marathon-s3-models-read`(`models/*` 읽기만)로 대체하고 그 ARN만 허용
  - 교훈: "역할 생성 + 정책 작성 + PassRole + 인스턴스 실행"이 한 주체에 모이면 권한 상승이 된다. 각각은 무해해 보여도 조합을 봐야 한다
- 검증 (CLI)
  - 서울 `DescribeInstances` 성공 / us-east-1 `UnauthorizedOperation`
  - `run-instances --dry-run`: `p3.2xlarge` 거부 / `t3.micro` `DryRunOperation`(허용)
  - `marathon-ec2-role`, `marathon-s3-models-read` 조회 성공
- 시행착오: 관리형 정책을 사용자 인라인 정책으로 만들다 **사용자당 인라인 합계 2,048자 한도** 초과 → IAM → 정책에서 독립 관리형으로 생성
- 남은 위험: 서울 리전 안에서 SG 전체 공개·스냅샷 공개 공유는 가능. 장기 액세스 키 사용. 보조 수단으로 AWS Budgets 알림 권장
- 정리: `marathon-sg`의 미사용 80번(`0.0.0.0/0`) 규칙 삭제 확인

### 20. Terraform 코드화 + remote state(S3) + 네이티브 락 (2026-10-28~29)
- 상황: 10-28 GPU EC2를 콘솔로 만들며 실수 반복 — 일반 Amazon Linux AMI 선택(드라이버 없음), 루트 16GB, IP 옮겨 적다 오타, SG에 미사용 80번(`0.0.0.0/0`) 잔존, 종료 후 볼륨 잔존 여부를 따로 확인해야 함
- 결정: `infra/envs/dev/gpu-ec2/`에 Terraform으로 코드화. 한 번에 쓰지 않고 data(AMI) → SG → IAM → EC2 순으로 단계별 작성, 단계마다 apply/destroy로 확인
  - AMI: `data "aws_ami"`가 이름 규칙(`Deep Learning Base OSS Nvidia Driver GPU AMI (Amazon Linux 2023) *`)으로 최신본 검색
  - SG: 22·8010을 변수 `my_ip_cidr`에만. 변수 검증으로 `/32`가 아니면 plan 단계에서 거부. 아웃바운드는 Terraform이 기본 규칙을 안 만들어서 명시
  - IAM: `marathon-tf-ec2-role` + 관리형 `marathon-s3-models-read`만 (수동 역할에 있던 ECR·SSM은 이번 용도에 불필요해 제외)
  - EC2: g4dn.xlarge, 100GiB gp3, `delete_on_termination`, IMDSv2 강제(SSRF로 역할 자격증명 유출 차단), `ssh` 접속 명령을 output으로
  - 내 IP는 `my.auto.tfvars`(git 제외)
- 시행착오: 10-28 destroy가 중간에 멈춰 SG·IAM 역할·인스턴스 프로파일 3개가 남음(인스턴스·볼륨은 삭제됨). 다음 날 state에 남은 항목으로 발견 → destroy 재실행. 교훈: `Destroy complete!` 확인 습관, state가 "무엇이 살아 있나"의 근거가 됨
- remote state
  - 버킷: `infra/bootstrap/`(이 폴더만 로컬 state)으로 `marathon-gpx-tfstate-507138241931` 생성. 버전 관리, SSE-S3(KMS는 월 $1이라 제외), 퍼블릭 액세스 차단 4종, 이전 버전 90일 후 삭제, `prevent_destroy`
  - `gpu-ec2`에 `backend "s3"`(`key = dev/gpu-ec2/terraform.tfstate`, `use_lockfile = true`) → `terraform init -migrate-state`
  - 참고: state가 비어 있으면 S3 객체는 첫 apply 때 생성됨
- 락 검증: 터미널 A에서 `terraform apply`를 확인 프롬프트에서 대기시키고, 터미널 B에서 `terraform plan` → `Error acquiring the state lock` 확인. A에서 `no`로 취소해 비용 없음
- 비용: state 버킷 월 1원 미만

### 21. 커밋 기록에 남아 있던 카카오 API 키 — 재발급으로 무효화 (2026-10-29)
- 상황: 팀 레포를 개인 레포(`marathon-gpx-cloud`)로 옮기기 전, push할 기록 전체를 키 패턴으로 스캔
- 발견: 2026-05 커밋 2개(`d2fe18c`, `5679972`)의 `src/config.py`에 카카오 REST API 키가 하드코딩된 채 커밋돼 있었음. 이후 코드에서는 제거됐지만 git 기록에 남음. 팀 레포가 공개라 **약 5개월간 노출 상태**였고, 현재 `.env` 키와 동일
- 선택지: ① 기록 재작성(history rewrite)으로 키 제거 — 새 레포에서만 지워지고 팀 레포·포크·캐시에는 남음, 팀원 커밋 해시도 바뀜 ② 키 재발급으로 무효화
- 결정: ② 재발급. 이미 공개된 비밀은 지워서 회수할 수 없으므로 **무효화가 근본 대응**. 무효화 후에는 기록에 남은 값이 쓸모없어져 재작성이 불필요
- 검증: 옛 키로 카카오 로컬 API 호출 → 401 / 새 키 → 200. 새 키는 git 기록 어디에도 없음
- 재발 방지: 키는 `.env`(git 제외)로만 주입 (이미 적용). 레포를 공개하거나 옮기기 전 기록 전체 스캔을 절차로 둔다 (도입 후보: gitleaks pre-commit / CI)
