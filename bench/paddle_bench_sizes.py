"""실제 크롭 크기(기본 288px / 재시도 568px)로 PaddleOCR CPU vs GPU 비교."""
import os
import time

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

import cv2
import numpy as np
import paddle
from paddleocr import PaddleOCR

print(f"paddle {paddle.__version__}  cuda={paddle.is_compiled_with_cuda()}")

# config.py 기준: 기본 = 긴 변 256 + 여백 16, 재시도 = 긴 변 512 + 여백 28, 최소 높이 64
CASES = {"base(288x96)": (288, 96, 0.9), "retry(568x152)": (568, 152, 1.6)}
N = 15

def make(w, h, scale):
    img = np.full((h, w, 3), 255, np.uint8)
    cv2.putText(img, "SUNCHEON 12KM", (10, int(h * 0.65)), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 2)
    return img

results = {}
for dev in ("cpu", "gpu"):
    try:
        ocr = PaddleOCR(
            lang="korean", device=dev,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        for name, (w, h, s) in CASES.items():
            img = make(w, h, s)
            ocr.predict(img)  # 워밍업
            start = time.perf_counter()
            for _ in range(N):
                ocr.predict(img)
            ms = (time.perf_counter() - start) / N * 1000
            results[(dev, name)] = ms
            print(f"RESULT {dev:3} {name:16} {ms:7.0f} ms/call")
    except Exception as exc:
        print(f"RESULT {dev}: FAILED {type(exc).__name__}: {str(exc)[:200]}")

# 크롭 131개 × (기본 1회 + 재시도 1회) 기준 추정
if len(results) == 4:
    for dev in ("cpu", "gpu"):
        total = sum(results[(dev, n)] for n in CASES) * 131 / 1000
        print(f"ESTIMATE {dev}: 크롭 131개 × (기본+재시도) = {total:.1f}s")
