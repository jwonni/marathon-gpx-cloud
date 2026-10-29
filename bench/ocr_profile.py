"""run_ocr 구간별 소요 시간 프로파일 (프로젝트 코드는 수정하지 않음).

사용:
  python ocr_profile.py profile <image>   # Hi-SAM + 전처리 + OCR 호출 + 후처리 구간별 측정
  python ocr_profile.py ocronly           # 저장된 크롭으로 OCR 호출만 재측정 (MKLDNN=1 이면 oneDNN 켬)
"""
import json
import os
import sys
import time

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

sys.path.insert(0, "/app")

if os.environ.get("STUB_TORCH") == "1":
    # paddlepaddle-gpu 를 설치하면 torch 가 깨진다 (CUDA 라이브러리 충돌, decision-log 16번).
    # src.config 가 torch 를 import 하므로, OCR 측정에만 쓰는 이 스크립트에서는 가짜 모듈로 대체한다.
    import types
    _torch = types.ModuleType("torch")
    _torch.device = lambda name: name
    _torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    sys.modules["torch"] = _torch

WORK = "/work"
CROPS_JSON = f"{WORK}/prepared.json"
USE_MKLDNN = os.environ.get("MKLDNN", "0") == "1"
DEVICE = os.environ.get("DEVICE", "cpu")


class T:
    def __init__(self):
        self.rows = []

    def __call__(self, label, fn, *a, **kw):
        s = time.perf_counter()
        out = fn(*a, **kw)
        self.rows.append((label, time.perf_counter() - s))
        return out

    def report(self, total_label="합계"):
        width = max(len(r[0]) for r in self.rows) + 2
        total = sum(t for _, t in self.rows)
        print("\n" + "=" * (width + 22))
        for label, t in self.rows:
            print(f"{label:<{width}} {t:8.2f}s  {t / total * 100:5.1f}%")
        print("-" * (width + 22))
        print(f"{total_label:<{width}} {total:8.2f}s")


def load_ocr():
    import paddle
    paddle.set_flags({"FLAGS_use_mkldnn": USE_MKLDNN})
    paddle.device.set_device(DEVICE)
    from paddleocr import PaddleOCR
    return PaddleOCR(
        lang="korean", device=DEVICE,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def cmd_profile(image_path):
    from src.georeferencing.text_detector import run_hisam_to_json
    from src.georeferencing import ocr as O
    from src.config import Config

    t = T()
    payload = t("1. Hi-SAM 글자영역 검출", run_hisam_to_json, image_path)
    print(f"   polygon {payload.get('num_words')}개")

    crop_records = t("2. 크롭 좌표 정리", O.build_crop_records_from_json, payload)
    prepared = t("3. 크롭 전처리+파일 저장", O.build_all_paddle_inputs, crop_records, f"{WORK}/crops")
    print(f"   크롭 {len(prepared)}개")

    ocr = t("4. PaddleOCR 모델 로드", load_ocr)

    base_s = retry_s = merge_s = 0.0
    raw = []
    for rec in prepared:
        s = time.perf_counter(); b = O.run_paddle_on_path(rec["paddle_base_path"], ocr);  base_s += time.perf_counter() - s
        s = time.perf_counter(); r = O.run_paddle_on_path(rec["paddle_retry_path"], ocr); retry_s += time.perf_counter() - s
        s = time.perf_counter()
        raw.append(O.merge_crop_result_to_text_xy(rec, O.choose_best_paddle_result([b, r])))
        merge_s += time.perf_counter() - s
    t.rows += [("5. OCR 기본 호출", base_s), ("6. OCR 재시도 호출", retry_s), ("7. 결과 병합", merge_s)]

    def post():
        f = [x for x in raw if isinstance(x.get("text"), str) and x["text"].strip()
             and float(x.get("confidence", 0) or 0) >= Config.MIN_CONFIDENCE]
        f = sorted(f, key=lambda r: (r["y"], r["x"]))
        return O.merge_lines_by_same_x(f)

    final = t("8. 필터·정렬·병합", post)
    t.report("run_ocr 전체")
    print(f"\n최종 텍스트 {len(final)}개 / 크롭 {len(prepared)}개")

    with open(CROPS_JSON, "w", encoding="utf-8") as fp:
        json.dump([{k: r[k] for k in ("paddle_base_path", "paddle_retry_path")} for r in prepared], fp)


def cmd_ocronly():
    from src.georeferencing import ocr as O
    if os.environ.get("STUB_TORCH") == "1":
        # 가짜 torch 는 src.config 로드용으로만 쓰고 치운다.
        # 남겨두면 PaddleOCR 이 find_spec("torch") 검사에서 ValueError(torch.__spec__ is None) 를 낸다.
        sys.modules.pop("torch", None)
    with open(CROPS_JSON, encoding="utf-8") as fp:
        prepared = json.load(fp)
    ocr = load_ocr()
    O.run_paddle_on_path(prepared[0]["paddle_base_path"], ocr)  # 워밍업
    base_s = retry_s = 0.0
    for rec in prepared:
        s = time.perf_counter(); O.run_paddle_on_path(rec["paddle_base_path"], ocr);  base_s += time.perf_counter() - s
        s = time.perf_counter(); O.run_paddle_on_path(rec["paddle_retry_path"], ocr); retry_s += time.perf_counter() - s
    n = len(prepared)
    print(f"\nDEVICE={DEVICE} MKLDNN={'ON' if USE_MKLDNN else 'OFF'}  크롭 {n}개")
    print(f"  기본 호출  : {base_s:6.2f}s  ({base_s / n * 1000:5.0f} ms/call)")
    print(f"  재시도 호출: {retry_s:6.2f}s  ({retry_s / n * 1000:5.0f} ms/call)")
    print(f"  OCR 호출 합계: {base_s + retry_s:6.2f}s")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "profile":
        cmd_profile(sys.argv[2])
    else:
        cmd_ocronly()
