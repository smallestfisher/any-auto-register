#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
  :
else
  echo "未找到可用虚拟环境。请先创建 .venv 或手动激活 Python 环境。" >&2
  exit 1
fi

MODE="${1:-help}"
shift || true

case "$MODE" in
  web)
    exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
    ;;
  cli)
    exec python3 cli.py "$@"
    ;;
  serve)
    exec python3 cli.py serve "$@"
    ;;
  help|-h|--help)
    cat <<'USAGE'
Usage:
  ./scripts/start.sh web [uvicorn args...]
  ./scripts/start.sh serve
  ./scripts/start.sh cli <subcommand...>

Examples:
  ./scripts/start.sh web
  ./scripts/start.sh serve
  ./scripts/start.sh cli platforms list
  ./scripts/start.sh cli register create --wait
USAGE
    ;;
  *)
    echo "未知模式: $MODE" >&2
    echo "使用 ./scripts/start.sh help 查看用法。" >&2
    exit 1
    ;;
esac
