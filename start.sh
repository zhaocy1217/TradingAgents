#!/usr/bin/env bash
# Checkout dev + pull, then bootstrap conda, tradingagents env (Python 3.13), deps, web UI.
# Optional: MINICONDA_INSTALL_DIR (default: $HOME/miniconda3), CONDA_ENV_NAME (default: tradingagents).

set -euo pipefail

run_as_root() {
	if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
		"$@"
	else
		sudo "$@"
	fi
}

ensure_git() {
	if command -v git >/dev/null 2>&1; then
		return 0
	fi
	echo "Git not found; installing..."
	if command -v apt-get >/dev/null 2>&1; then
		run_as_root apt-get update -qq
		run_as_root apt-get install -y git
	elif command -v dnf >/dev/null 2>&1; then
		run_as_root dnf install -y git
	elif command -v yum >/dev/null 2>&1; then
		run_as_root yum install -y git
	elif command -v zypper >/dev/null 2>&1; then
		run_as_root zypper install -y git
	elif command -v pacman >/dev/null 2>&1; then
		run_as_root pacman -Sy --noconfirm git
	elif command -v apk >/dev/null 2>&1; then
		run_as_root apk add --no-cache git
	else
		echo "Could not install git: no supported package manager found." >&2
		exit 1
	fi
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
ensure_git
git checkout dev
git pull

INSTALL_DIR="${MINICONDA_INSTALL_DIR:-$HOME/miniconda3}"
ENV_NAME="${CONDA_ENV_NAME:-tradingagents}"
# Must match tradingagents.web.app:run (uvicorn port).
PORT=8000
HEALTH_URL="http://127.0.0.1:${PORT}/healthz"

install_miniconda() {
	echo "Installing Miniconda to ${INSTALL_DIR} ..."
	local tmp arch mc_arch
	tmp="$(mktemp)"
	arch="$(uname -m)"
	case "$arch" in
	x86_64) mc_arch=Linux-x86_64 ;;
	aarch64 | arm64) mc_arch=Linux-aarch64 ;;
	*)
		echo "Unsupported architecture: ${arch}" >&2
		exit 1
		;;
	esac
	curl -fsSL "https://repo.anaconda.com/miniconda/Miniconda3-latest-${mc_arch}.sh" -o "$tmp"
	bash "$tmp" -b -p "$INSTALL_DIR"
	rm -f "$tmp"
}

init_conda() {
	if [[ -f "${INSTALL_DIR}/etc/profile.d/conda.sh" ]]; then
		# shellcheck source=/dev/null
		source "${INSTALL_DIR}/etc/profile.d/conda.sh"
	elif command -v conda >/dev/null 2>&1; then
		# shellcheck disable=SC2046
		eval "$(conda shell.bash hook)"
	else
		install_miniconda
		# shellcheck source=/dev/null
		source "${INSTALL_DIR}/etc/profile.d/conda.sh"
	fi
}

conda_env_exists() {
	conda env list | awk 'NF && $1 !~ /^#/ {print $1}' | grep -Fqx "$1"
}

stop_listeners_on_port() {
	local p="$1"
	if command -v fuser >/dev/null 2>&1; then
		fuser -k "${p}/tcp" 2>/dev/null || true
		return
	fi
	if command -v lsof >/dev/null 2>&1; then
		local pids
		pids="$(lsof -t -i:"${p}" -sTCP:LISTEN 2>/dev/null || true)"
		if [[ -n "${pids}" ]]; then
			kill ${pids} 2>/dev/null || true
		fi
		return
	fi
	echo "Warning: neither fuser nor lsof found; trying pkill for uvicorn on port ${p}" >&2
	pkill -f "[u]vicorn.*:${p}" 2>/dev/null || true
}

init_conda

if ! conda_env_exists "$ENV_NAME"; then
	conda create -n "$ENV_NAME" python=3.13 -y
fi

conda activate "$ENV_NAME"

cd "$ROOT"
python -m pip install -U pip
pip install .
pip install ".[web]"

if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
	echo "Server already responding on port ${PORT}; stopping it before restart..."
	stop_listeners_on_port "$PORT"
	sleep 1
	if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
		echo "Still reachable on ${HEALTH_URL}; aborting" >&2
		exit 1
	fi
fi

exec tradingagents-web
