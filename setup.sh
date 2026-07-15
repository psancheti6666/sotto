#!/bin/bash
# Sotto one-command setup: installs everything needed and downloads both AI models.
# Usage: ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
step() { printf "\n\033[1;34m==>\033[0m \033[1m%s\033[0m\n" "$*"; }

# ---- preflight ---------------------------------------------------------------
[[ "$(uname)" == "Darwin" ]] || { echo "Sotto currently supports macOS only."; exit 1; }
[[ "$(uname -m)" == "arm64" ]] || { echo "Sotto requires Apple Silicon (M1 or newer)."; exit 1; }
command -v brew >/dev/null || {
  echo "Homebrew is required. Install it first:"
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1
}

# ---- Python 3.11+ ------------------------------------------------------------
step "Checking Python 3.11+"
PY=""
for cand in python3.13 python3.12 python3.11; do
  command -v "$cand" >/dev/null && PY="$(command -v "$cand")" && break
done
if [[ -z "$PY" ]]; then
  bold "Installing python@3.11 via Homebrew…"
  brew install python@3.11
  PY="$(brew --prefix)/bin/python3.11"
fi
echo "Using $PY"

# ---- Ollama (runs the text-cleaning model locally) ----------------------------
step "Checking Ollama"
command -v ollama >/dev/null || { bold "Installing Ollama via Homebrew…"; brew install ollama; }
if ! curl -s --max-time 2 http://localhost:11434/api/version >/dev/null; then
  bold "Starting the Ollama service…"
  brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
  for _ in $(seq 1 30); do
    curl -s --max-time 2 http://localhost:11434/api/version >/dev/null && break
    sleep 1
  done
fi
curl -s --max-time 2 http://localhost:11434/api/version >/dev/null \
  || { echo "Could not start Ollama — run 'ollama serve' manually and re-run setup."; exit 1; }

step "Downloading the text-cleaning model (qwen3:4b-instruct, ~2.5 GB — one time)"
ollama pull qwen3:4b-instruct

# ---- Python environment --------------------------------------------------------
step "Creating Python environment and installing dependencies"
"$PY" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

step "Downloading the speech-recognition model (Parakeet, ~600 MB — one time)"
.venv/bin/python -c "from parakeet_mlx import from_pretrained; from_pretrained('mlx-community/parakeet-tdt-0.6b-v3')" >/dev/null

# ---- macOS Globe-key setting ----------------------------------------------------
step "Configuring the fn (Globe) hotkey"
FN_SETTING="$(defaults read com.apple.HIToolbox AppleFnUsageType 2>/dev/null || echo missing)"
if [[ "$FN_SETTING" != "0" ]]; then
  read -r -p "Set the Globe key to 'Do Nothing' so fn can be the dictation hotkey? [Y/n] " ans
  if [[ ! "$ans" =~ ^[Nn] ]]; then
    defaults write com.apple.HIToolbox AppleFnUsageType -int 0
    echo "Done (revert anytime in System Settings → Keyboard → 'Press 🌐 key to')."
  else
    echo "Skipped — set hotkey = \"alt_r\" (or another key) in ~/.sotto/config.toml instead."
  fi
fi

# ---- done ----------------------------------------------------------------------
step "Setup complete!"
cat <<'EOF'

Start Sotto with:

    ./run.sh

On first launch, macOS will ask you to grant three permissions to your terminal
app (System Settings → Privacy & Security):

    • Microphone        — to hear you
    • Accessibility     — to type the text at your cursor
    • Input Monitoring  — to detect the dictation hotkey

Grant all three, then run ./run.sh again. Hold fn and speak — release, and the
cleaned text appears wherever your cursor is.
EOF
