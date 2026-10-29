"""
OCR 실행 경로 선택.

- Config.OCR_SERVICE_URL 이 설정되면: OCR 전용 서비스(ocr_service.py)에 HTTP 로 요청
- 비어 있으면: 기존처럼 같은 프로세스에서 run_ocr 실행 (로컬 `python app.py` 개발 흐름 유지)

반환 형식은 두 경로 모두 src.georeferencing.ocr.run_ocr 와 같다.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from src.config import Config

log = logging.getLogger("ocr")


def run_ocr_auto(hisam_payload: dict, crop_dir: str) -> list[dict]:
    if not Config.OCR_SERVICE_URL:
        from src.georeferencing.ocr import run_ocr
        return run_ocr(hisam_payload, crop_dir)
    return run_ocr_remote(hisam_payload)


def run_ocr_remote(hisam_payload: dict) -> list[dict]:
    """원본 이미지 + Hi-SAM 결과를 OCR 서비스로 보내고 텍스트 목록을 받는다."""
    import requests

    url = Config.OCR_SERVICE_URL.rstrip("/") + "/ocr"
    image_path = hisam_payload["image_path"]

    t = time.perf_counter()
    with open(image_path, "rb") as fp:
        resp = requests.post(
            url,
            files={"file": (Path(image_path).name, fp)},
            data={"hisam": json.dumps(hisam_payload, ensure_ascii=False)},
            # 연결 5초, 응답 대기는 CPU 로 돌 때도 버티도록 길게
            timeout=(5, Config.OCR_SERVICE_TIMEOUT),
        )
    if resp.status_code != 200:
        raise RuntimeError(f"OCR 서비스 오류 HTTP {resp.status_code}: {resp.text[:300]}")

    results = resp.json()["ocr_results"]
    log.info("[ocr_client] 원격 OCR 완료: %d개 텍스트, %.2fs (%s)",
             len(results), time.perf_counter() - t, url)
    return results
