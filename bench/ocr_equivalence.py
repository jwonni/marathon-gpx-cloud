"""OCR 서비스 분리가 결과를 바꾸는지 검증 — Hi-SAM 결과를 고정해 무작위성을 제거하고 비교한다.

앱 이미지(marathon-gpx:local) 안에서 실행:
  python ocr_equivalence.py hisam  <image> <hisam.json>            # Hi-SAM 1회 실행 후 고정
  python ocr_equivalence.py local  <hisam.json> <image> <out.json>   # 기존 방식: 앱 프로세스 안에서 run_ocr
  python ocr_equivalence.py remote <hisam.json> <image> <out.json>   # 분리 방식: OCR_SERVICE_URL 로 HTTP
  python ocr_equivalence.py diff   <a.json> <b.json>
"""
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")
sys.path.insert(0, "/app")


def _load(path):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _dump(obj, path):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=1, default=float)


def cmd_hisam(image, out):
    from src.georeferencing.text_detector import run_hisam_to_json
    t = time.perf_counter()
    payload = run_hisam_to_json(image)
    _dump(payload, out)
    print(f"[hisam] polygon {payload.get('num_words')}개, {time.perf_counter() - t:.1f}s")


def _run(mode, hisam_json, image, out):
    payload = _load(hisam_json)
    payload["image_path"] = image
    t = time.perf_counter()
    if mode == "local":
        from src.georeferencing.ocr import run_ocr
        results = run_ocr(payload, tempfile.mkdtemp())
    else:
        from src.georeferencing.ocr_client import run_ocr_remote
        results = run_ocr_remote(payload)
    _dump(results, out)
    print(f"[{mode}] 텍스트 {len(results)}개, {time.perf_counter() - t:.1f}s")


def cmd_diff(a_path, b_path):
    a, b = _load(a_path), _load(b_path)
    key = lambda r: (r.get("y"), r.get("x"), r.get("text"))
    a_s, b_s = sorted(a, key=key), sorted(b, key=key)

    print(f"[diff] A {len(a)}개 / B {len(b)}개")
    if len(a) != len(b):
        print("[diff] 개수부터 다름")

    mismatches = 0
    for ra, rb in zip(a_s, b_s):
        same_text = ra.get("text") == rb.get("text")
        same_xy = (ra.get("x"), ra.get("y")) == (rb.get("x"), rb.get("y"))
        conf_gap = abs(float(ra.get("confidence", 0)) - float(rb.get("confidence", 0)))
        if not (same_text and same_xy and conf_gap < 1e-4):
            mismatches += 1
            print(f"[diff] 다름: A={ra.get('text')!r}@({ra.get('x')},{ra.get('y')}) conf={ra.get('confidence')}"
                  f"  B={rb.get('text')!r}@({rb.get('x')},{rb.get('y')}) conf={rb.get('confidence')}")
    if mismatches == 0 and len(a) == len(b):
        print("[diff] 완전히 동일 (텍스트·좌표·신뢰도)")
    else:
        print(f"[diff] 불일치 {mismatches}건")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "hisam":
        cmd_hisam(*args)
    elif cmd in ("local", "remote"):
        _run(cmd, *args)
    else:
        cmd_diff(*args)
