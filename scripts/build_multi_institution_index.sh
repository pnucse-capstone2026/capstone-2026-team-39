#!/usr/bin/env bash
# Build the 7-institution BM25 service index from finished parser runs.
#
# Usage (from the repository root):
#   scripts/build_multi_institution_index.sh [output-index]
#
# Runs that do not exist yet are skipped with a warning, so the index can be
# built before every institution has been parsed and rebuilt later.
set -euo pipefail

OUTPUT="${1:-processed/index/multi-institution.sqlite}"

# institution | chunks produced by scripts/parse_pipeline.py
SOURCES=(
  "부산대학교|processed/runs/20260725-pnu-curated-cascade-v5/cascade/chunks.jsonl"
  "한국인터넷진흥원(KISA)|processed/runs/20260723-final-kisa/cascade/chunks.jsonl"
  "한국은행|processed/runs/20260724-final-bok-cascade-resume-v1/cascade/chunks.jsonl"
)
# Large corpora were parsed as parallel runs split by config/multi-institution-parse/*.jsonl.
for part in 1 2 3; do
  SOURCES+=("한국거래소·한국예탁결제원·한국해양과학기술원 p$part|processed/runs/20260929-krx-ksd-kiost-cascade-p$part/cascade/chunks.jsonl")
done
for part in 1 2 3 4 5 6; do
  SOURCES+=("금융감독원 p$part|processed/runs/20260929-fss-baseline-p$part/baseline/chunks.jsonl")
done

args=()
for source in "${SOURCES[@]}"; do
  name="${source%%|*}"
  path="${source#*|}"
  if [[ -f "$path" ]]; then
    args+=(--chunks "$path")
    echo "include: $name ($path)"
  else
    echo "skip (not parsed yet): $name ($path)" >&2
  fi
done

if [[ ${#args[@]} -eq 0 ]]; then
  echo "no parser runs found" >&2
  exit 1
fi

# Build into a temporary file so a running API server never sees a half-built index.
tmp="${OUTPUT}.building"
rm -f "$tmp"
python3 scripts/bm25_search.py build --allow-suspect --index "$tmp" "${args[@]}"
mv -f "$tmp" "$OUTPUT"
echo "built: $OUTPUT"
