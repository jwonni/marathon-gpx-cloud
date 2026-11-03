"""컨테이너 장애 시나리오 측정 (로컬 docker compose, CPU)

    python bench/failure/run_failure_test.py <라벨>      # 예: baseline, after-fix

시나리오 — 앱이 Hi-SAM 을 끝내고 OCR 서비스에 요청을 넘긴 직후(처리 중) 장애를 주입한다
    1. OCR 죽음   docker kill  → 진행 중 요청이 몇 초 만에 어떤 응답을 받나 / 새 요청은?
    2. OCR 멈춤   docker pause → 응답 대기가 얼마나 길어지나
    3. 앱 죽음    docker kill  → 자동 복구되나 / 몇 초
    4. 둘 다 죽음 docker kill  → 자동 복구되나 / 첫 요청 성공까지 몇 초

결과: bench/failure/results/<라벨>.md
"""
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

LABEL = sys.argv[1] if len(sys.argv) > 1 else "baseline"
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "bench" / "failure" / "results" / f"{LABEL}.md"
IMG = ROOT / "마라톤코스4.webp"          # 벤치마크 지도 (저작권상 레포 미포함)
APP = "http://localhost:8010"
WAIT_LIMIT = 700                         # 한 요청을 기다리는 최대 시간. 앱의 OCR 대기(600s)보다 길게
RECOVER_LIMIT = 180                      # 자동 복구를 기다리는 최대 시간. 재시작 + 워밍업(CPU 약 1분)보다 길게


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def compose(*args, check=False):
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check)


def state(service):
    cid = compose("ps", "-aq", service).stdout.strip()
    if not cid:
        return "없음"
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", cid], capture_output=True, text=True)
    return r.stdout.strip() or "없음"


def health_ok():
    try:
        return requests.get(f"{APP}/api/health", timeout=2).status_code == 200
    except requests.RequestException:
        return False


def post_ocr(timeout):
    """run_ocr 요청 1건 → (HTTP 코드 또는 예외 이름, 걸린 시간, 본문 앞부분)"""
    t0 = time.monotonic()
    try:
        with open(IMG, "rb") as f:
            r = requests.post(f"{APP}/api/run_ocr", files={"file": ("bench.webp", f, "image/webp")}, timeout=timeout)
        return str(r.status_code), time.monotonic() - t0, r.text[:160]
    except requests.RequestException as e:
        return type(e).__name__, time.monotonic() - t0, str(e)[:160]


def fresh_start():
    log("스택 초기화")
    compose("down", "-t", "5")
    compose("up", "-d")
    t0 = time.monotonic()
    while time.monotonic() - t0 < RECOVER_LIMIT * 2:
        if health_ok():
            log(f"준비 완료까지 {time.monotonic() - t0:.1f}s")
            return
        time.sleep(2)
    log("준비 시간 초과")


def wait_hisam_done(since):
    t0 = time.monotonic()
    while time.monotonic() - t0 < 400:
        if "Hi-SAM 완료" in compose("logs", "app", "--since", since).stdout:
            return True
        time.sleep(1)
    return False


def auto_recover():
    t0 = time.monotonic()
    while time.monotonic() - t0 < RECOVER_LIMIT:
        if state("ocr") == "running" and state("app") == "running" and health_ok():
            return f"자동 복구됨 ({time.monotonic() - t0:.1f}s)"
        time.sleep(3)
    return f"자동 복구 안 됨 ({RECOVER_LIMIT}s 동안 ocr={state('ocr')}, app={state('app')})"


def clean(text):
    return " ".join(text.replace("|", "/").split())


def run_case(n, title, inject_args):
    fresh_start()
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {}
    th = threading.Thread(target=lambda: result.update(r=post_ocr(WAIT_LIMIT), end=time.monotonic()))
    th.start()
    log(f"[{n}] 요청 전송 → Hi-SAM 완료 대기")
    if not wait_hisam_done(since):
        log(f"[{n}] Hi-SAM 완료 로그를 못 찾음")
    time.sleep(3)                                   # OCR 서비스가 처리 중인 시점
    t_inject = time.monotonic()
    log(f"[{n}] 장애 주입: docker compose {' '.join(inject_args)}")
    compose(*inject_args)
    th.join()
    code, total, body = result["r"]
    log(f"[{n}] 응답: {code} ({total:.1f}s)")

    code2, total2, body2 = post_ocr(60)             # 장애가 남은 상태에서 새 요청
    recover = auto_recover()
    compose("unpause", "ocr")                       # 2번 시나리오 정리

    with open(OUT, "a", encoding="utf-8") as f:
        f.write(f"### {n}. {title}\n")
        f.write("| 항목 | 결과 |\n|---|---|\n")
        f.write(f"| 장애 주입 | `docker compose {' '.join(inject_args)}` |\n")
        f.write(f"| 진행 중 요청 | {code} — 요청 시작부터 {total:.1f}s, **장애 주입부터 {result['end'] - t_inject:.1f}s** |\n")
        f.write(f"| └ 본문 | `{clean(body)}` |\n")
        f.write(f"| 장애 후 새 요청 | {code2} — {total2:.1f}s |\n")
        f.write(f"| └ 본문 | `{clean(body2)}` |\n")
        f.write(f"| 자동 복구 | {recover} |\n\n")


def main():
    if not IMG.exists():
        sys.exit(f"벤치마크 이미지 없음: {IMG}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images = " ".join(compose("config", "--images").stdout.split())
    OUT.write_text(
        f"# 장애 시나리오 측정 — {LABEL}\n\n"
        f"- 일시: {datetime.now():%Y-%m-%d %H:%M}\n"
        f"- 환경: 로컬 docker compose (CPU), 이미지 {images}\n"
        f"- 요청: `POST /api/run_ocr` (벤치마크 지도 1장). 장애는 Hi-SAM 완료 3초 뒤(OCR 서비스 처리 중) 주입\n\n",
        encoding="utf-8",
    )
    run_case(1, "OCR 죽음 (처리 중)", ["kill", "ocr"])
    run_case(2, "OCR 멈춤 (처리 중)", ["pause", "ocr"])
    run_case(3, "앱 죽음 (처리 중)", ["kill", "app"])
    run_case(4, "둘 다 죽음 (처리 중)", ["kill"])
    compose("down", "-t", "5")
    log(f"완료 → {OUT}")


if __name__ == "__main__":
    main()
