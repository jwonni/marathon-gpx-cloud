"""
OCR 전용 서비스 — PaddleOCR 만 담당한다 (torch 없음).

앱 이미지 안에서는 torch(CUDA 13)와 paddlepaddle-gpu(CUDA 12.6)의 CUDA 라이브러리가 충돌해
PaddleOCR 을 GPU 로 돌릴 수 없다. 그래서 OCR 만 별도 컨테이너로 분리한다. (docs/decision-log.md 16, 17번)

  POST /ocr     multipart: file=원본 이미지, hisam=Hi-SAM 결과 JSON 문자열
                → {"ocr_results": [...]}   (src.georeferencing.ocr.run_ocr 반환값 그대로)
  GET  /health  → {"status": "ok", "device": ...}
"""

import os

os.environ.setdefault("FLAGS_use_mkldnn", "0")      # app.py 와 같은 paddle 설정
os.environ.setdefault("FLAGS_enable_pir_api", "0")

import json
import logging
import shutil
import tempfile
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from src.config import Config
from src.georeferencing.ocr import load_paddle_ocr, run_ocr

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
# PaddleOCR 이 root 로거 레벨을 WARNING 으로 바꾸므로 앱 로거에 레벨을 직접 지정한다 (decision-log 10번)
for _name in ("ocr_service", "ocr", "src"):
    logging.getLogger(_name).setLevel(logging.INFO)
log = logging.getLogger("ocr_service")

app = FastAPI(title="Marathon OCR Service")

# PaddleOCR 예측기는 동시 호출에 안전하다고 보장되지 않는다. GPU 하나에서 요청을 순차 처리한다.
_ocr_lock = threading.Lock()


@app.on_event("startup")
def warmup():
    t = time.perf_counter()
    load_paddle_ocr()
    log.info("[warmup] PaddleOCR loaded in %.2fs (device=%s)", time.perf_counter() - t, Config.PADDLE_DEVICE)


@app.get("/health")
def health():
    return {"status": "ok", "device": Config.PADDLE_DEVICE}


@app.post("/ocr")
def ocr_endpoint(file: UploadFile = File(...), hisam: str = Form(...)):
    # 동기 함수(def)로 둬야 FastAPI 가 스레드풀에서 실행해 이벤트 루프를 막지 않는다
    try:
        payload = json.loads(hisam)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"hisam JSON 파싱 실패: {exc}")

    suffix = Path(file.filename).suffix if file.filename else ".png"
    tmp_path = crop_dir = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        crop_dir = tempfile.mkdtemp(prefix="paddle_crops_")

        # 앱 쪽 임시 파일 경로가 들어 있으므로 이 컨테이너의 경로로 바꾼다
        payload["image_path"] = tmp_path

        t = time.perf_counter()
        with _ocr_lock:
            results = run_ocr(payload, crop_dir)
        log.info("[ocr_service] polygons=%s texts=%d elapsed=%.2fs device=%s",
                 payload.get("num_words"), len(results), time.perf_counter() - t, Config.PADDLE_DEVICE)
        return {"ocr_results": results}
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("[ocr_service] OCR 실패")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if crop_dir:
            shutil.rmtree(crop_dir, ignore_errors=True)
