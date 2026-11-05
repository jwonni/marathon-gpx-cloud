"""처리 중에도 앱이 다른 요청에 응답하는지 측정한다.

지도 1장을 /api/run_ocr 로 보내 놓고(백그라운드), 그동안 3초마다 /metrics 와 /api/health 의 응답 시간을 잰다.
Prometheus 수집 제한(10초)을 넘으면 모니터링은 앱을 '다운'으로 판단한다.

  python bench/responsiveness_test.py <이미지> [APP_URL]
"""
import sys
import threading
import time

import requests

image = sys.argv[1]
APP = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8010"
TIMEOUT = 10        # Prometheus 기본 scrape_timeout

done = {}


def heavy():
    t = time.perf_counter()
    with open(image, "rb") as f:
        r = requests.post(f"{APP}/api/run_ocr", files={"file": ("image.png", f, "image/png")}, timeout=1800)
    done.update(code=r.status_code, sec=time.perf_counter() - t)


th = threading.Thread(target=heavy)
th.start()
time.sleep(2)

rows = []
while th.is_alive():
    for path in ("/metrics", "/api/health"):
        t = time.perf_counter()
        try:
            code = requests.get(APP + path, timeout=TIMEOUT).status_code
        except requests.RequestException:
            code = "timeout"
        rows.append((path, code, time.perf_counter() - t))
    time.sleep(3)
th.join()

for path in ("/metrics", "/api/health"):
    r = [x for x in rows if x[0] == path]
    ok = [x for x in r if x[1] == 200]
    print(f"{path:<12} 시도 {len(r)}회, 성공 {len(ok)}회, 최대 {max(x[2] for x in r):.2f}s, "
          f"10초 초과(수집 실패) {sum(1 for x in r if x[1] != 200)}회")
print(f"처리 중이던 run_ocr: HTTP {done.get('code')} {done.get('sec', 0):.0f}s")
