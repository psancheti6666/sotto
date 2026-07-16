#!/bin/bash
# Sotto one-command setup: installs everything needed and downloads both AI models.
# Supports macOS (Apple Silicon + Intel) and Linux (X11 + Wayland; apt or dnf).
# Usage: ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
step() { printf "\n\033[1;34m==>\033[0m \033[1m%s\033[0m\n" "$*"; }

OS="$(uname)"
ARCH="$(uname -m)"

find_python() {
  PY=""
  for cand in python3.13 python3.12 python3.11 python3.10; do
    command -v "$cand" >/dev/null && PY="$(command -v "$cand")" && return 0
  done
  return 1
}

wait_for_ollama() {
  for _ in $(seq 1 30); do
    curl -s --max-time 2 http://localhost:11434/api/version >/dev/null && return 0
    sleep 1
  done
  return 1
}

# =============================================================== macOS =======
if [[ "$OS" == "Darwin" ]]; then
  if ! command -v brew >/dev/null; then
    step "Homebrew (the standard macOS package manager) is not installed"
    read -r -p "Install Homebrew now? It will ask for your Mac password. [Y/n] " ans
    if [[ "$ans" =~ ^[Nn] ]]; then
      echo "Setup needs Homebrew — install it from https://brew.sh and re-run ./setup.sh"
      exit 1
    fi
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Make brew usable in THIS shell (the installer only updates future ones).
    if [[ "$ARCH" == "arm64" ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    else
      eval "$(/usr/local/bin/brew shellenv)"
    fi
    command -v brew >/dev/null || { echo "Homebrew install did not complete — re-run ./setup.sh"; exit 1; }
  fi

  step "Checking Python 3.10+"
  if ! find_python; then
    bold "Installing python@3.11 via Homebrew…"
    brew install python@3.11
    PY="$(brew --prefix)/bin/python3.11"
  fi
  echo "Using $PY"

  step "Checking Ollama (runs the text-cleaning model locally)"
  command -v ollama >/dev/null || { bold "Installing Ollama via Homebrew…"; brew install ollama; }
  if ! curl -s --max-time 2 http://localhost:11434/api/version >/dev/null; then
    bold "Starting the Ollama service…"
    brew services start ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)
    wait_for_ollama || { echo "Could not start Ollama — run 'ollama serve' manually and re-run setup."; exit 1; }
  fi

  step "Downloading the text-cleaning model (qwen3:4b-instruct, ~2.5 GB — one time)"
  ollama pull qwen3:4b-instruct

  step "Creating Python environment and installing dependencies"
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt

  if [[ "$ARCH" == "arm64" ]]; then
    step "Downloading the speech-recognition model (Parakeet via MLX, ~600 MB — one time)"
    .venv/bin/python -c "from parakeet_mlx import from_pretrained; from_pretrained('mlx-community/parakeet-tdt-0.6b-v3')" >/dev/null
  else
    step "Downloading the speech-recognition model (Parakeet via ONNX, ~2.4 GB — one time)"
    .venv/bin/python -c "import onnx_asr; onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3', providers=['CPUExecutionProvider'])" >/dev/null
    bold "Intel Mac note: everything runs on the CPU here. Speech recognition stays"
    bold "fast, but AI text cleanup may take several seconds per dictation. If it"
    bold "feels slow, set  ollama_model = \"llama3.2:3b\"  in ~/.sotto/config.toml."
  fi

  # ---- macOS Globe-key setting ------------------------------------------------
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
  exit 0
fi

# =============================================================== Linux =======
if [[ "$OS" == "Linux" ]]; then
  if command -v apt-get >/dev/null; then PKG="apt"
  elif command -v dnf >/dev/null; then PKG="dnf"
  else
    echo "Unsupported distro: need apt or dnf. Install the packages from README.md manually."
    exit 1
  fi

  SESSION="${XDG_SESSION_TYPE:-}"
  [[ -n "${WAYLAND_DISPLAY:-}" ]] && SESSION="wayland"
  echo "Detected: $PKG, ${SESSION:-unknown} session"

  step "Installing system packages (sudo required)"
  if [[ "$PKG" == "apt" ]]; then
    sudo apt-get update -qq
    sudo apt-get install -y python3 python3-venv python3-tk \
      portaudio19-dev libnotify-bin curl
    if [[ "$SESSION" == "wayland" ]]; then
      sudo apt-get install -y wl-clipboard wtype || true
      bold "GNOME Wayland note: wtype doesn't work on GNOME — install ydotool too:"
      bold "  sudo apt-get install ydotool   (and enable the ydotoold service)"
    else
      sudo apt-get install -y xdotool xclip
    fi
  else
    sudo dnf install -y python3 python3-tkinter portaudio-devel libnotify curl
    if [[ "$SESSION" == "wayland" ]]; then
      sudo dnf install -y wl-clipboard wtype || true
      bold "GNOME Wayland note: wtype doesn't work on GNOME — install ydotool too:"
      bold "  sudo dnf install ydotool   (and enable the ydotoold service)"
    else
      sudo dnf install -y xdotool xclip
    fi
  fi

  step "Checking Python 3.10+"
  find_python || PY="$(command -v python3)"
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "Python 3.10+ required (found $("$PY" --version)). Install a newer python3 and re-run."; exit 1; }
  echo "Using $PY"

  step "Checking Ollama (runs the text-cleaning model locally)"
  command -v ollama >/dev/null || {
    bold "Installing Ollama (official installer)…"
    curl -fsSL https://ollama.com/install.sh | sh
  }
  if ! curl -s --max-time 2 http://localhost:11434/api/version >/dev/null; then
    bold "Starting the Ollama service…"
    (sudo systemctl enable --now ollama >/dev/null 2>&1) || (nohup ollama serve >/dev/null 2>&1 &)
    wait_for_ollama || { echo "Could not start Ollama — run 'ollama serve' manually and re-run setup."; exit 1; }
  fi

  step "Downloading the text-cleaning model (qwen3:4b-instruct, ~2.5 GB — one time)"
  ollama pull qwen3:4b-instruct

  step "Creating Python environment and installing dependencies"
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt

  step "Downloading the speech-recognition model (Parakeet via ONNX, ~2.4 GB — one time)"
  .venv/bin/python -c "import onnx_asr; onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3', providers=['CPUExecutionProvider'])" >/dev/null

  step "Granting access to the hotkey (kernel input events)"
  if id -nG "$USER" | grep -qw input; then
    echo "Already in the 'input' group."
  else
    sudo usermod -aG input "$USER"
    bold "*** You were added to the 'input' group — LOG OUT AND BACK IN (or reboot)"
    bold "*** before running Sotto, or the hotkey will not be detected."
  fi

  step "Setup complete!"
  cat <<'EOF'

Start Sotto with:

    ./run.sh

Hold RIGHT CTRL and speak — release, and the cleaned text appears wherever
your cursor is. Double-tap Right Ctrl for hands-free mode; Escape cancels.

Notes for Linux:
  • If you were just added to the 'input' group, log out and back in first.
  • Without a GPU, AI text cleanup runs on the CPU and may take several
    seconds per dictation. If it feels slow, set
        ollama_model = "llama3.2:3b"
    in ~/.sotto/config.toml (faster, slightly lower quality).
  • Sound feedback uses the freedesktop sound theme (preinstalled on most
    desktops). No sounds is harmless.
EOF
  exit 0
fi

echo "Unsupported OS: $OS (Sotto supports macOS and Linux)."
exit 1
