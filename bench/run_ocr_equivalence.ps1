# OCR 서비스 분리 전후 결과 동일성 검증 (로컬, CPU)
#   1) Hi-SAM 1회 실행 → 결과 고정
#   2) 고정 입력으로 기존 방식(앱 안 OCR) 실행
#   3) 같은 입력으로 OCR 서비스(HTTP) 실행
#   4) 두 결과 비교
# 필요 이미지: marathon-gpx:local, marathon-ocr:cpu
# 주의: 이 파일은 반드시 "UTF-8 with BOM"으로 저장할 것.
#       Windows PowerShell 5.1은 BOM 없는 UTF-8을 CP949로 읽어 한글 경로·문자열이 깨진다 (2026-09-17 실제 실패)
$repo  = Split-Path -Parent $PSScriptRoot
$bench = $PSScriptRoot
$app   = "marathon-gpx:local"
$eq    = Join-Path $bench "eq"

New-Item -ItemType Directory -Force $eq | Out-Null
Copy-Item (Join-Path $repo "마라톤코스4.webp") (Join-Path $eq "bench.webp") -Force

$mounts = @(
  "-v", "${bench}:/work",
  "-v", "$repo\weights:/app/weights",
  "-v", "$repo\Hi-SAM\pretrained_checkpoint:/app/Hi-SAM/pretrained_checkpoint",
  "-v", "paddlex-cache:/root/.paddlex"
)

Write-Host "=== [$(Get-Date -Format T)] 1) Hi-SAM 결과 고정 ==="
docker run --rm @mounts $app python /work/ocr_equivalence.py hisam /work/eq/bench.webp /work/eq/hisam.json 2>&1 | Select-String '^\['

Write-Host "=== [$(Get-Date -Format T)] 2) 기존 방식: 앱 안에서 OCR ==="
docker run --rm @mounts $app python /work/ocr_equivalence.py local /work/eq/hisam.json /work/eq/bench.webp /work/eq/local.json 2>&1 | Select-String '^\['

Write-Host "=== [$(Get-Date -Format T)] 3) 분리 방식: OCR 서비스 ==="
docker network create ocr-eq | Out-Null
docker run -d --name ocr-eq --network ocr-eq -e PADDLE_DEVICE=cpu -v paddlex-cache:/root/.paddlex marathon-ocr:cpu | Out-Null
for ($i = 0; $i -lt 60; $i++) {
  docker exec ocr-eq python -c "import urllib.request; urllib.request.urlopen('http://localhost:8020/health')" 2>$null
  if ($LASTEXITCODE -eq 0) { break }
  Start-Sleep -Seconds 5
}
docker run --rm @mounts --network ocr-eq -e OCR_SERVICE_URL=http://ocr-eq:8020 $app python /work/ocr_equivalence.py remote /work/eq/hisam.json /work/eq/bench.webp /work/eq/remote.json 2>&1 | Select-String '^\['
docker rm -f ocr-eq | Out-Null
docker network rm ocr-eq | Out-Null

Write-Host "=== [$(Get-Date -Format T)] 4) 비교 ==="
docker run --rm -v "${bench}:/work" $app python /work/ocr_equivalence.py diff /work/eq/local.json /work/eq/remote.json 2>&1 | Select-String '^\['
