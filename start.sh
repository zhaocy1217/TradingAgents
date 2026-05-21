#!/usr/bin/env bash
# Bootstrap conda env, install deps, then start Streamlit UI.
# Optional:
#   MINICONDA_INSTALL_DIR (default: $HOME/miniconda3)
#   CONDA_ENV_PATH        (default: $ROOT/.conda-env)
#   PORT                  (default: 8501)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_PATH="${ROOT}/tradingagents/ui/streamlit_app.py"
INSTALL_DIR="${MINICONDA_INSTALL_DIR:-$HOME/miniconda3}"
ENV_PATH="${CONDA_ENV_PATH:-$ROOT/.conda-env}"
PORT="${PORT:-8501}"

if [[ ! -f "${APP_PATH}" ]]; then
	echo "Streamlit app not found at ${APP_PATH}" >&2
	exit 1
fi

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
	fi
}

cd "$ROOT"
init_conda

if [[ ! -x "${ENV_PATH}/bin/python" ]]; then
	conda create --prefix "$ENV_PATH" python=3.13 -y
fi

conda activate "$ENV_PATH"

python -m pip install -U pip
pip install .

if ! python -m pip show streamlit >/dev/null 2>&1; then
	python -m pip install streamlit
fi

if command -v curl >/dev/null 2>&1 && curl -sf "http://127.0.0.1:${PORT}" >/dev/null 2>&1; then
	echo "Detected existing listener on port ${PORT}; attempting to stop it..."
	stop_listeners_on_port "$PORT"
	sleep 1
fi

python -m streamlit run "${APP_PATH}" --server.address 0.0.0.0 --server.port "${PORT}"

