#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -ne 2 ]]; then
  echo "Usage: analyze.sh INPUT.zip OUTPUT.json" >&2
  exit 2
fi
command -v timeout >/dev/null || { echo "Linux coreutils timeout is required" >&2; exit 2; }

archive_path=$1
report_path=$2
image=${NISHTYAK_ANALYZER_IMAGE:-nishtyak-analyzer:0.3.0}
if [[ ! -f "$archive_path" ]] || (( $(wc -c < "$archive_path") > 10485760 )); then
  echo "Input must be a ZIP file no larger than 10 MiB" >&2
  exit 2
fi
mkdir -p "$(dirname "$report_path")"
temporary_report=$(mktemp "${report_path}.tmp.XXXXXX")
job_name="nishtyak-analysis-$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"

cleanup() {
  timeout --signal=TERM --kill-after=2s 5s docker rm --force "$job_name" >/dev/null 2>&1 || true
  rm -f "$temporary_report"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if (
  ulimit -f 16384
  timeout --signal=TERM --kill-after=3s 35s docker run --rm --interactive --init --pull=never \
    --name "$job_name" --network none --read-only --user 65532:65532 \
    --cap-drop ALL --security-opt no-new-privileges \
    --cpus 1 --memory 512m --memory-swap 512m --pids-limit 32 --log-driver none \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m,uid=65532,gid=65532,mode=700 \
    "$image" --archive - --output - --memory-bytes 536870912 \
    --cpu-seconds 15 --max-report-bytes 8388608 \
    < "$archive_path" > "$temporary_report"
); then
  result=0
else
  result=$?
fi
if [[ -s "$temporary_report" ]]; then
  mv "$temporary_report" "$report_path"
fi
exit "$result"
