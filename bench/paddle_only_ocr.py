"""실험: Hi-SAM 없이 PaddleOCR(검출+인식)을 지도 전체에 한 번만 돌려 OCR 결과를 만든다 (프로젝트 코드는 수정하지 않음).

  지금:  지도 → Hi-SAM 글자 영역 → 크롭 → PaddleOCR 인식 → 후처리
  실험:  지도 → PaddleOCR 검출+인식 (지도 전체 1회)      → 후처리

후처리(신뢰도 필터·정렬·세로 병합)는 서비스 코드(src.georeferencing.ocr)를 그대로 쓴다.
결과 JSON 은 bench/georef_offline.py 에 넣어 지리좌표·정답 거리로 평가한다.

  docker run --rm -v "<repo>/src:/app/src" -v "<repo>/bench:/work" -v paddlex-cache:/root/.paddlex \
    marathon-gpx:local python /work/paddle_only_ocr.py <이미지> <출력.json> [det_limit_side_len]
"""
import json
import os
import sys
import time

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
sys.path.insert(0, "/app")

from src.config import Config  # noqa: E402
from src.georeferencing import ocr as O  # noqa: E402

DEVICE = os.environ.get("DEVICE", "cpu")


def main(image, out_path, limit_side=None):
    import paddle
    paddle.set_flags({"FLAGS_use_mkldnn": False})
    paddle.device.set_device(DEVICE)
    from paddleocr import PaddleOCR

    kw = {}
    if limit_side:                               # 검출 입력 크기 (작은 글씨용으로 키워 볼 때)
        kw = {"text_det_limit_side_len": int(limit_side), "text_det_limit_type": "max"}
    ocr = PaddleOCR(lang=Config.OCR_LANG, device=DEVICE,
                    use_doc_orientation_classify=False, use_doc_unwarping=False,
                    use_textline_orientation=False, **kw)
    ocr.predict(input=image)                      # 워밍업 (모델 초기화 비용 제외)

    t = time.perf_counter()
    page = ocr.predict(input=image)[0]
    elapsed = time.perf_counter() - t

    # 지도 전체 좌표의 글자 상자 → 서비스 run_ocr 의 크롭 결과와 같은 형식 (x, y = 상자 중심)
    raw = [{"text": it["text"], "x": it["cx"], "y": it["cy"], "confidence": round(it["score"], 6), "num_segments": 1}
           for it in O.extract_paddle_items(page) if it["text"]]
    filtered = [r for r in raw if r["confidence"] >= Config.MIN_CONFIDENCE]
    if Config.SORT_BY_COORDS:
        filtered = sorted(filtered, key=lambda r: (r["y"], r["x"]))
    merged = O.merge_lines_by_same_x(filtered)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    print(f"[paddle-only] {DEVICE} 지도 전체 1회: {elapsed:.2f}s, 검출 {len(raw)}개 → 신뢰도 필터 {len(filtered)}개 → 최종 {len(merged)}개"
          + (f" (det_limit_side_len={limit_side})" if limit_side else ""))
    print(f"  예: {[m['text'] for m in merged[:25]]}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
