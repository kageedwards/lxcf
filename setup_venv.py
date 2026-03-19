#!/usr/bin/env python3
"""Bootstrap a .venv inside this package and install dependencies.

Called by npm postinstall so the bridge always has its deps available,
regardless of the system Python environment.
"""

import subprocess
import sys
import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(PKG_DIR, ".venv")

def main():
    # Create venv if missing
    if not os.path.isdir(VENV_DIR):
        print(f"[lxcf] Creating venv in {VENV_DIR}")
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])

    # Resolve the venv pip
    if sys.platform == "win32":
        pip = os.path.join(VENV_DIR, "Scripts", "pip")
    else:
        pip = os.path.join(VENV_DIR, "bin", "pip")

    # Install the package itself (picks up deps from pyproject.toml)
    print("[lxcf] Installing lxcf + dependencies into .venv")
    subprocess.check_call([pip, "install", "--quiet", PKG_DIR])

if __name__ == "__main__":
    main()
