"""PaddleOCR 배치 추론 가능성 측정 (프로젝트 코드는 수정하지 않음).

저장된 크롭 124개 × (base, retry) = 248장으로 세 방식을 비교한다.
  A. 현재 방식       — 파이프라인(검출+인식)을 한 장씩 호출
  B. 파이프라인 배치 — 248장을 리스트로 한 번에 predict
  C. 인식 전용 배치  — 검출 없이 인식 모델(TextRecognition)만 batch_size 장씩

출력: 방식별 시간, 그리고 크롭별 최종 텍스트(base·retry 중 선택 결과)가 A 와 같은지

실행 (OCR 이미지, bench 를 /work 로 마운트):
  docker run --rm -v "<repo>/bench:/work" -v paddlex-cache:/root/.paddlex \
    -e DEVICE=cpu marathon-ocr:cpu python /work/ocr_batch_test.py
"""
import json
import os
import sys
import time

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
sys.path.insert(0, "/app")

from src.georeferencing import ocr as O  # noqa: E402  (결과 해석·선택 로직은 서비스 코드 그대로 사용)

WORK = "/work"
DEVICE = os.environ.get("DEVICE", "cpu")
REC_BATCH = int(os.environ.get("REC_BATCH", "32"))


def load_pipeline():
    import paddle
    paddle.set_flags({"FLAGS_use_mkldnn": False})
    paddle.device.set_device(DEVICE)
    from paddleocr import PaddleOCR
    return PaddleOCR(lang="korean", device=DEVICE,
                     use_doc_orientation_classify=False,
                     use_doc_unwarping=False,
                     use_textline_orientation=False)


def page_to_raw(page):
    """파이프라인 결과 1장 → run_paddle_on_path 와 같은 형태"""
    items = O.extract_paddle_items(page)
    text = "\n".join(it["text"] for it in items if it["text"]).strip()
    score = sum(it["score"] for it in items) / len(items) if items else 0.0
    return {"text": text or None, "score": score, "items": items}


def final_texts(raw_by_path, records):
    """크롭별 base·retry 중 서비스와 같은 기준으로 고른 최종 텍스트"""
    out = []
    for rec in records:
        best = O.choose_best_paddle_result([raw_by_path[rec["paddle_base_path"]],
                                            raw_by_path[rec["paddle_retry_path"]]])
        out.append(((best.get("text") or "").strip(), round(float(best.get("score", 0.0)), 4)))
    return out


def compare(name, ref, got):
    same_text = sum(1 for a, b in zip(ref, got) if a[0] == b[0])
    same_both = sum(1 for a, b in zip(ref, got) if a == b)
    diffs = [(i, a[0], b[0]) for i, (a, b) in enumerate(zip(ref, got)) if a[0] != b[0]]
    print(f"[{name}] 텍스트 일치 {same_text}/{len(ref)}, 텍스트+점수 일치 {same_both}/{len(ref)}")
    for i, a, b in diffs[:15]:
        print(f"    #{i:03d}  A={a!r}  →  {name}={b!r}")
    return {"text_match": same_text, "exact_match": same_both, "total": len(ref), "diffs": diffs}


def main():
    records = json.load(open(f"{WORK}/prepared.json", encoding="utf-8"))
    paths = [p for r in records for p in (r["paddle_base_path"], r["paddle_retry_path"])]
    print(f"DEVICE={DEVICE}  크롭 {len(records)}개 × 2 = {len(paths)}장  REC_BATCH={REC_BATCH}")

    ocr = load_pipeline()
    ocr.predict(input=paths[0])                                   # 워밍업 (첫 호출 초기화 비용 제외)

    # A. 현재 방식
    t = time.perf_counter()
    raw_a = {p: O.run_paddle_on_path(p, ocr) for p in paths}
    t_a = time.perf_counter() - t
    ref = final_texts(raw_a, records)
    print(f"[A] 한 장씩 파이프라인     {t_a:7.2f}s  ({t_a / len(paths) * 1000:.0f} ms/장)")

    # B. 파이프라인에 리스트로
    t = time.perf_counter()
    pages = list(ocr.predict(input=paths))
    t_b = time.perf_counter() - t
    raw_b = {p: page_to_raw(pg) for p, pg in zip(paths, pages)}
    print(f"[B] 파이프라인 리스트 입력 {t_b:7.2f}s  ({t_b / len(paths) * 1000:.0f} ms/장)")
    cmp_b = compare("B", ref, final_texts(raw_b, records))

    # C. 인식 모델만 배치로 (검출 생략 — Hi-SAM 이 이미 글자 영역을 잘라 줬다는 전제)
    from paddleocr import TextRecognition
    try:
        rec_model = TextRecognition(model_name=os.environ.get("REC_MODEL", "korean_PP-OCRv5_mobile_rec"),
                                    device=DEVICE)
    except Exception as e:                                        # 모델 이름이 다르면 기본값으로
        print(f"    인식 모델 지정 실패({e}) → 기본 모델 사용")
        rec_model = TextRecognition(device=DEVICE)
    rec_model.predict(input=paths[0])                             # 워밍업
    t = time.perf_counter()
    res_c = list(rec_model.predict(input=paths, batch_size=REC_BATCH))
    t_c = time.perf_counter() - t
    raw_c = {}
    for p, r in zip(paths, res_c):
        text = (r.get("rec_text") if isinstance(r, dict) else r["rec_text"]) or ""
        score = float(r.get("rec_score") if isinstance(r, dict) else r["rec_score"])
        raw_c[p] = {"text": text or None, "score": score,
                    "items": [{"text": text, "score": score, "cx": 0, "cy": 0}] if text else []}
    print(f"[C] 인식 전용 배치({REC_BATCH})   {t_c:7.2f}s  ({t_c / len(paths) * 1000:.0f} ms/장)")
    cmp_c = compare("C", ref, final_texts(raw_c, records))

    out = {"device": DEVICE, "images": len(paths), "rec_batch": REC_BATCH,
           "seconds": {"A_loop": round(t_a, 2), "B_pipeline_list": round(t_b, 2), "C_rec_only_batch": round(t_c, 2)},
           "B_vs_A": cmp_b, "C_vs_A": cmp_c}
    with open(f"{WORK}/batch_test_{DEVICE}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"저장: bench/batch_test_{DEVICE}.json")


if __name__ == "__main__":
    main()
