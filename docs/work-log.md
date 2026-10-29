# 작업 로그

진행 상황과 **다음에 이어서 할 일**을 적는다. 결정 근거와 측정값은 [decision-log.md](decision-log.md)에 있다.

---

## 2026-10-28 — OCR 동일성 검증 완료, 로컬 VM 인프라 트랙 시작

### 한 일
- 동일성 검증 실행 → 로컬 실행과 OCR 서비스 결과 **완전히 동일** (106개, 텍스트·좌표·신뢰도). 분리는 정확도 무관 → decision-log 17번

### 방향 추가
- 신입 인프라 공고(서버·Linux·Ansible·HA·모니터링·K8s·OpenStack)에 맞춰 **로컬 VM 트랙**을 추가
  - VirtualBox + Rocky Linux → Ansible(기존 `scripts/ec2-user-data.sh`를 playbook으로) → Nginx LB + 앱 2대 + OCR 서버 → Prometheus/Grafana/알람 → k3s + GitHub Actions → (여유 시) DevStack
  - AWS는 GPU 측정용으로 유지. 로컬은 비용 0, CPU로 동작 (compose로 전체 처리 확인됨)
  - PC: RAM 31GB, 16스레드, 디스크 여유 661GB

- 로컬 VM: VirtualBox 7.2 + Rocky Linux 9.8(`NEW-VM`, 호스트명 `rocky-1`, 사용자 `jw`, SSH 127.0.0.1:2222)
  - 막힌 것: 무인 설치가 ISO를 재구성해 `checksum error` → 원본 ISO로 수동 설치 / 비밀번호 불일치 → GRUB `rd.break`로 재설정 / `jw`가 wheel에 없음 → `usermod -aG wheel`
  - VM이 느림: VBox.log `AMD-V is not available` → NEM 모드. Docker Desktop(WSL2)과 VBS가 Hyper-V를 켜 두기 때문. Docker 때문에 당분간 유지
- **EC2 GPU 전체 재측정: 154초 → 25.7초 (6.0배)** → decision-log 18번
  - 처음 띄운 인스턴스는 AMI가 일반 Amazon Linux + 디스크 16GB여서 종료하고 재생성
- 포트폴리오 구성 결정: 이력서 1장 + PDF + GitHub. 프로젝트 3개(캡스톤 / Plainedu 운영 / 홈랩 VM). 캡스톤 문제 3개: ① 병목 진단→서비스 분리 ② 이미지·빌드 ③ 로컬-컨테이너 환경 차이

- 포트폴리오 아키텍처 그림 완성: `docs/portfolio/capstone-architecture.svg/.png`
- **Terraform 시작** (`infra/envs/dev/gpu-ec2/`)
  - 실행 권한: `Terraform-s3`에 인라인 정책 `terraform-marathon-ec2` + 관리형 정책 `marathon-s3-models-read` 생성 → decision-log 19번 (초안의 권한 상승 경로 발견·차단)
  - 코드: `versions.tf`(provider, 기본 태그) / `main.tf`(GPU AMI 검색) / `variables.tf`(내 IP, /32만 허용) / `security.tf`(SG + 22·8010 + egress) / `iam.tf`(역할·S3 읽기·프로파일) / `ec2.tf`(g4dn.xlarge, 100GiB gp3, IMDSv2, ssh 출력)
  - 내 IP는 `my.auto.tfvars`(git 제외)
  - 10-29 확인: EC2 apply→destroy 후 **SG·역할·인스턴스 프로파일 3개가 남음** (destroy 중단 추정, 무료 리소스). 인스턴스·볼륨은 삭제됨

  - 10-29: destroy 재실행으로 정리 완료. **state 버킷(`infra/bootstrap`) 생성 → gpu-ec2 backend S3 이전 → 락 동작 확인** → decision-log 20번

### 다음에 할 일
1. (선택) `gpu-ec2` 폴더의 옛 로컬 `terraform.tfstate`, `.backup` 삭제 — 이제 S3를 씀
2. 포트폴리오: 캡스톤 문제 ① 본문 작성
3. VM 트랙: Windows에서 `ssh -p 2222 jw@127.0.0.1` 접속 확인 → Ansible
4. 이후: 로컬 k3s → EKS 단기 apply/destroy
5. (보류) Hi-SAM 실패율 측정

---

## 2026-09-17 — PaddleOCR GPU 실측 + OCR 서비스 분리

### 한 일
1. **EC2 GPU로 OCR 실측** (g4dn.xlarge, 실제 크롭 124개)
   - CPU 4코어 80.17초 → GPU 12.75초 (**6.3배**) → decision-log 16번 (D)
   - 결론: PaddleOCR GPU 전환. 단 torch와 CUDA 라이브러리가 충돌하므로 **OCR을 별도 컨테이너로 분리**
2. **OCR 서비스 분리 1단계** — OCR 전용 서비스·이미지 작성 → decision-log 17번
   - `ocr_service.py`, `Dockerfile.ocr`, `requirements-ocr.txt`
   - `src/config.py`: torch 선택적 import, `PADDLE_DEVICE`·`OCR_SERVICE_URL`·`OCR_SERVICE_TIMEOUT` 환경변수화
   - 로컬 CPU판 이미지 `marathon-ocr:cpu` 빌드, `/health` 정상 확인
3. **2단계** — 앱이 OCR을 HTTP로 호출
   - `src/georeferencing/ocr_client.py` 신규, `app.py` 호출 2곳 교체, `requirements.txt`에 `requests`
   - 앱 이미지 재빌드 **248초** (requirements 1줄 수정, torch 레이어 캐시) → decision-log 8번 결과 채움
4. **3단계** — `docker-compose.yml`로 두 컨테이너 실행 → 벤치마크 지도 **끝까지 처리 성공**
   - 단, 사용자가 **정확도가 낮게 나온 것 같다**고 보고함

### 정확도 저하 조사 (진행 중)
- 확인 완료: 처리 로직·설정값 변경 없음 / 앱 이미지와 OCR 이미지의 라이브러리 버전 동일 (실제 로드되는 cv2 모두 4.10.0)
- 유력 원인: **Hi-SAM 무작위성** (decision-log 12번) — 실행마다 글자 영역이 달라짐
- 또 다른 가능성: 비교 기준이 로컬 Windows 실행(paddleocr 3.5.0)이면 컨테이너(3.4.1)와 원래부터 버전이 다름
- **동일성 검증 테스트 1차 실행은 스크립트 버그로 실패** — 측정값 없음
  - 원인: `.ps1`을 BOM 없는 UTF-8로 저장 → Windows PowerShell 5.1이 CP949로 읽어 `마라톤코스4.webp` 경로와 한글 문자열이 깨짐, 따옴표가 먹혀 이후 명령까지 오작동
  - 조치: `bench/run_ocr_equivalence.ps1`을 UTF-8 with BOM으로 재저장, 구문 오류 0개·이미지 경로 확인, 잔여 컨테이너·네트워크·`bench/eq` 정리 완료

### 다음에 할 일 (순서대로)
1. **동일성 검증 실행** (CPU라 약 10분, **노트북 덮개 닫지 말 것** — 닫으면 절전으로 중단)
   ```powershell
   powershell -ExecutionPolicy Bypass -File bench\run_ocr_equivalence.ps1
   ```
   - 결과 파일: `bench/eq/hisam.json`(고정한 Hi-SAM 결과), `bench/eq/local.json`(기존 방식), `bench/eq/remote.json`(OCR 서비스)
   - 비교만 다시 하려면:
     ```powershell
     docker run --rm -v "${PWD}\bench:/work" marathon-gpx:local python /work/ocr_equivalence.py diff /work/eq/local.json /work/eq/remote.json
     ```
   - **완전히 같으면** → 분리는 정확도 무관. 보인 차이는 Hi-SAM 무작위성 → 5번으로
   - **다르면** → 어느 텍스트가 달라졌는지 추적
2. 사용자에게 확인: "정확도가 낮다"의 비교 기준이 로컬 Windows 실행인지 컨테이너인지
3. **GPU판 OCR 이미지 빌드** — 집 회선으로는 paddle GPU(약 5GB)가 느리므로 EC2에서 빌드
   - `docker build -f Dockerfile.ocr -t marathon-ocr:gpu .`
4. **EC2에서 두 컨테이너(GPU) 실행 → 앱 전체 재측정** (기준값: 지도 1장 154초)
   - GPU용 compose 설정은 아직 없음 (`--gpus` 지정, OCR은 `PADDLE_DEVICE=gpu`)
5. Hi-SAM 무작위성 실패율·결과 편차 측정 (GPU에서 같은 이미지 N회)

### 확인이 필요한 상태
- EC2 인스턴스(`54.180.78.66`, `i-042d8a8b9fb6a65ea`)를 **종료했는지** 콘솔에서 확인. 100GiB 볼륨이 남아 있지 않은지도 확인
- 오늘 변경 사항은 **git 커밋 전** (브랜치 `demo09`)

### 참고
- 벤치마크 이미지: `마라톤코스4.webp` / 측정 도구: `bench/`
- EC2 접속: 키 `C:\Users\roffj\Downloads\marathon-key.pem`, 사용자 `ec2-user` (Session Manager는 미해결, decision-log 13번)
- EC2 시작 시 루트 볼륨 **100GiB**, IAM 역할 `marathon-ec2-role`을 **시작 화면에서** 지정 (둘 다 놓쳐서 실패한 적 있음)
- 사용자 데이터 스크립트의 카카오 키는 `/opt/marathon/.env`로 들어감. Windows에서 복사하면 줄 끝 `\r` 제거 필요
