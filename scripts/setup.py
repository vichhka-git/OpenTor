#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OpenTor Contributors
"""
OpenTor — Interactive First-Run Setup Wizard
=============================================
Walks through .env configuration, Tor setup, dependency checks,
and prints a quick-start guide.

Usage:
    python3 setup.py

Requires: Python 3.x, standard library only.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys

# ── ANSI color codes ───────────────────────────────────────────────
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
RESET = "\033[0m"

# ── Paths ──────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OPENTOR_ROOT = os.path.dirname(SCRIPT_DIR)
ENV_PATH = os.path.join(OPENTOR_ROOT, ".env")
ENV_EXAMPLE_PATH = os.path.join(OPENTOR_ROOT, ".env.example")

# ── Helper functions ───────────────────────────────────────────────


def _p(msg: str, color: str = "") -> None:
    """Print a message with optional ANSI color."""
    print(f"{color}{msg}{RESET}")


def _ask(prompt: str, default: str = "") -> str:
    """Prompt for user input with an optional default value."""
    if default:
        val = input(f"  {prompt} [{default}]: ").strip()
        return val if val else default
    return input(f"  {prompt}: ").strip()


def _yes(prompt: str) -> bool:
    """Ask a yes/no question; default is Yes."""
    val = input(f"  {prompt} [Y/n]: ").strip().lower()
    return val != "n"


def _patch_env(key: str, value: str) -> None:
    """Update or append ``key=value`` in the .env file.

    If the key already exists (exact line match) it is replaced in place.
    Otherwise the pair is appended to the end of the file.
    """
    if not os.path.exists(ENV_PATH):
        _p(f"  {RED}Error: {ENV_PATH} does not exist{RESET}", RED)
        sys.exit(1)

    lines: list[str] = []
    with open(ENV_PATH, "r") as fh:
        lines = fh.readlines()

    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Match ``KEY=...`` (no leading whitespace, no comment prefix)
        if stripped.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w") as fh:
        fh.writelines(lines)

    _p(f"  {GREEN}✓ Set {key}={value}{RESET}", GREEN)


def _mod_available(name: str) -> bool:
    """Return True if the Python module *name* is importable."""
    try:
        if name == "bs4":
            import bs4  # noqa: F401
        elif name == "dotenv":
            import dotenv  # noqa: F401
        elif name == "mcp":
            import mcp  # noqa: F401
        else:
            __import__(name)
        return True
    except ImportError:
        return False


def _pip_install(pkg_spec: str) -> bool:
    """Install a package via ``pip install <pkg_spec>``.

    Uses ``sys.executable -m pip`` to respect the active interpreter.
    Returns True on success.
    """
    cmd = [sys.executable, "-m", "pip", "install", pkg_spec]
    _p(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        _p(f"  {GREEN}✓ Installed {pkg_spec}{RESET}", GREEN)
        return True
    else:
        _p(f"  {RED}✗ Failed to install {pkg_spec}{RESET}", RED)
        for line in result.stderr.strip().splitlines():
            _p(f"    {line}")
        return False


# ── Banner ─────────────────────────────────────────────────────────


def _banner() -> None:
    _p("")
    _p(f"{BOLD}{CYAN}╔══════════════════════════════════════════════╗{RESET}")
    _p(f"{BOLD}{CYAN}║           OpenTor — Setup Wizard             ║{RESET}")
    _p(f"{BOLD}{CYAN}║       Dark Web OSINT Toolkit — v1.0          ║{RESET}")
    _p(f"{BOLD}{CYAN}╚══════════════════════════════════════════════╝{RESET}")
    _p("")


# ── Step 1 — Environment Configuration ────────────────────────────


def _step1_env() -> None:
    _p(f"\n{BOLD}═══ Step 1: Environment Configuration ═══{RESET}\n")

    if os.path.exists(ENV_PATH):
        _p("  .env already exists.", YELLOW)
        if not _yes("  Would you like to re-configure?"):
            _p("  Skipping .env configuration.", YELLOW)
            return
    else:
        _p("  No .env found. Copying from .env.example ...")
        if not os.path.exists(ENV_EXAMPLE_PATH):
            _p(f"  {RED}Error: .env.example not found at{RESET}", RED)
            _p(f"        {ENV_EXAMPLE_PATH}")
            sys.exit(1)
        shutil.copy2(ENV_EXAMPLE_PATH, ENV_PATH)
        os.chmod(ENV_PATH, 0o600)
        _p(f"  {GREEN}✓ Created {ENV_PATH} (permissions 600){RESET}", GREEN)

    _p("\n  ── LLM Provider ──")
    _p("  Choose your LLM provider:")
    _p(f"    {CYAN}opencode{RESET}   — Orchestrator's own LLM (no key needed, default)")
    _p(f"    {CYAN}openai{RESET}     — OpenAI API (requires sk-... key)")
    _p(f"    {CYAN}anthropic{RESET}  — Anthropic API (requires sk-ant-... key)")
    _p(f"    {CYAN}gemini{RESET}     — Google Gemini API (requires AIza... key)")
    _p(f"    {CYAN}ollama{RESET}     — Local Ollama (no key needed)")
    _p(f"    {CYAN}llamacpp{RESET}   — Local llama.cpp (no key needed)")

    provider = _ask("Provider", default="opencode").strip().lower()

    valid = {"opencode", "openai", "anthropic", "gemini", "ollama", "llamacpp"}
    if provider not in valid:
        _p(f"  {RED}Unknown provider '{provider}'. Falling back to 'opencode'.{RESET}", RED)
        provider = "opencode"

    _patch_env("LLM_PROVIDER", provider)

    if provider == "openai":
        key = _ask("Enter your OpenAI API key (sk-...)")
        _patch_env("OPENAI_API_KEY", key)
    elif provider == "anthropic":
        key = _ask("Enter your Anthropic API key (sk-ant-...)")
        _patch_env("ANTHROPIC_API_KEY", key)
    elif provider == "gemini":
        key = _ask("Enter your Gemini API key (AIza...)")
        _patch_env("GEMINI_API_KEY", key)

    _p(f"\n  {GREEN}✓ LLM provider set to: {provider}{RESET}", GREEN)


# ── Step 2 — Tor Setup ────────────────────────────────────────────


def _step2_tor() -> None:
    _p(f"\n{BOLD}═══ Step 2: Tor Setup ═══{RESET}\n")

    # ── tor binary in PATH ──
    tor_path = shutil.which("tor")
    if tor_path:
        _p(f"  {GREEN}✓ tor binary found:{RESET} {tor_path}", GREEN)
    else:
        _p(f"  {RED}✗ tor binary not found in PATH{RESET}", RED)
        _p("    Install Tor:")
        _p("      • Debian/Ubuntu:  sudo apt install tor")
        _p("      • macOS:          brew install tor")
        _p("      • Source:         https://www.torproject.org/download/")

    # ── SOCKS port 9050 ──
    socks_ok = False
    try:
        with socket.create_connection(("127.0.0.1", 9050), timeout=2):
            socks_ok = True
        _p(f"  {GREEN}✓ SOCKS port 9050 is listening{RESET}", GREEN)
    except OSError:
        _p(f"  {YELLOW}⚠ SOCKS port 9050 not reachable (Tor may not be running){RESET}", YELLOW)

    # ── ControlPort 9051 ──
    ctrl_ok = False
    try:
        with socket.create_connection(("127.0.0.1", 9051), timeout=2):
            ctrl_ok = True
        _p(f"  {GREEN}✓ ControlPort 9051 is reachable{RESET}", GREEN)
    except OSError:
        _p(f"  {YELLOW}⚠ ControlPort 9051 not reachable{RESET}", YELLOW)

    # ── Offer custom torrc if ControlPort is down ──
    if not ctrl_ok:
        _p("\n  ── ControlPort not reachable ──")
        if _yes("  Would you like to create a custom Tor configuration?"):
            tor_data_dir = "/tmp/tor_data"
            torrc_path = os.path.join(tor_data_dir, "torrc")
            os.makedirs(tor_data_dir, mode=0o700, exist_ok=True)

            torrc_content = (
                "# OpenTor custom torrc\n"
                "SocksPort 9050\n"
                "ControlPort 9051\n"
                f"DataDirectory {tor_data_dir}\n"
                "CookieAuthentication 1\n"
                "SafeSocks 1\n"
            )
            with open(torrc_path, "w") as fh:
                fh.write(torrc_content)
            os.chmod(torrc_path, 0o644)
            _p(f"  {GREEN}✓ Created custom torrc:{RESET} {torrc_path}", GREEN)

            _patch_env("TOR_DATA_DIR", tor_data_dir)

            _p(f"\n  {BOLD}Start Tor with this config:{RESET}")
            _p(f"    tor -f {torrc_path}")
            _p("")
            _p("  Or run in the background:")
            _p(f"    tor -f {torrc_path} &")
            _p("")

    # ── Stem authentication test ──
    _p("\n  ── Stem Authentication Test ──")
    try:
        from stem.control import Controller

        try:
            with Controller.from_port(address="127.0.0.1", port=9051) as ctrl:
                ctrl.authenticate()
            _p(f"  {GREEN}✓ Stem authentication successful (cookie auth){RESET}", GREEN)
        except Exception as exc:
            msg = str(exc)
            if any(kw in msg.lower() for kw in ("refused", "111", "connection refused")):
                _p(f"  {YELLOW}⚠ ControlPort 9051 not available — cannot test stem auth{RESET}", YELLOW)
            elif "authentication" in msg.lower():
                _p(f"  {YELLOW}⚠ Stem auth failed: {msg}{RESET}", YELLOW)
                _p("  Possible fixes:")
                _p("    • Ensure CookieAuthentication 1 is in torrc")
                _p("    • Verify TOR_DATA_DIR in .env points to the right directory")
                _p("    • Set TOR_CONTROL_PASSWORD in .env if using password auth")
            else:
                _p(f"  {YELLOW}⚠ {msg}{RESET}", YELLOW)
    except ImportError:
        _p(f"  {YELLOW}⚠ Stem not installed (will be addressed in Step 3){RESET}", YELLOW)


# ── Step 3 — Python Dependencies ──────────────────────────────────


def _step3_deps() -> str:
    """Check Python dependencies and present an interactive install menu.

    Returns:
        ``'venv'``, ``'break-system'``, or ``'skip'`` reflecting the
        user's installation choice (or ``'skip'`` if everything was
        already installed).
    """
    _p(f"\n{BOLD}═══ Step 3: Python Dependencies ═══{RESET}\n")

    required: dict[str, str] = {
        "requests": "requests[socks]",
        "bs4": "beautifulsoup4",
        "dotenv": "python-dotenv",
        "stem": "stem",
    }

    optional: dict[str, str] = {
        "mcp": "mcp",
    }

    missing_required: list[str] = []
    missing_optional: list[str] = []

    for mod_name, pkg_name in required.items():
        if _mod_available(mod_name):
            _p(f"  {GREEN}✓ {pkg_name} is installed{RESET}", GREEN)
        else:
            _p(f"  {RED}✗ {pkg_name} is missing{RESET}", RED)
            missing_required.append(pkg_name)

    for mod_name, pkg_name in optional.items():
        if _mod_available(mod_name):
            _p(f"  {GREEN}✓ {pkg_name} is installed (optional){RESET}", GREEN)
        else:
            _p(f"  {YELLOW}⚠ {pkg_name} is missing (optional){RESET}", YELLOW)
            missing_optional.append(pkg_name)

    all_missing = missing_required + missing_optional

    if not missing_required and not missing_optional:
        _p(f"\n  {GREEN}✓ All dependencies are satisfied!{RESET}", GREEN)
        return "skip"

    # ── Interactive install menu ──
    _p(f"\n  Python packages needed: {', '.join(all_missing)}")
    _p("  Choose installation method:")
    _p("    1) Virtual environment (recommended)")
    _p("       → Creates .venv/ in the OpenTor directory")
    _p("       → Isolated, won't affect system Python")
    _p("    2) pip3 install --break-system-packages")
    _p("       → Installs globally (may affect other Python projects)")
    _p("    3) Skip — I'll install manually")

    choice = _ask("Choice", default="1").strip()

    if choice == "1":
        # ── Option 1: Virtual environment ──
        venv_path = os.path.join(OPENTOR_ROOT, ".venv")
        _p(f"\n  {BOLD}Creating virtual environment...{RESET}")
        _p(f"    python3 -m venv {venv_path}")

        result = subprocess.run(
            [sys.executable, "-m", "venv", venv_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            _p(f"  {RED}✗ Failed to create virtual environment{RESET}", RED)
            for line in result.stderr.strip().splitlines():
                _p(f"    {line}")
            return "skip"

        _p(f"  {GREEN}✓ Virtual environment created at {venv_path}{RESET}", GREEN)

        # Install from requirements.txt if it exists, otherwise install
        # individual packages.
        pip_bin = os.path.join(venv_path, "bin", "pip")
        req_path = os.path.join(OPENTOR_ROOT, "requirements.txt")

        _p(f"\n  {BOLD}Installing dependencies...{RESET}")
        if os.path.exists(req_path):
            install_cmd = [pip_bin, "install", "-r", req_path]
            _p(f"    {' '.join(install_cmd)}")
            result = subprocess.run(install_cmd, capture_output=True, text=True)
        else:
            install_cmd = [pip_bin, "install"] + all_missing
            _p(f"    {' '.join(install_cmd)}")
            result = subprocess.run(install_cmd, capture_output=True, text=True)

        if result.returncode == 0:
            _p(f"  {GREEN}✓ Dependencies installed in virtual environment{RESET}", GREEN)
        else:
            _p(f"  {RED}✗ Failed to install some dependencies{RESET}", RED)
            for line in result.stderr.strip().splitlines():
                _p(f"    {line}")

        _p(f"\n  {BOLD}Activate the virtual environment:{RESET}")
        _p(f"    source {venv_path}/bin/activate")

        return "venv"

    elif choice == "2":
        # ── Option 2: --break-system-packages ──
        _p(f"\n  {BOLD}Installing with --break-system-packages...{RESET}")
        install_cmd = ["pip3", "install", "--break-system-packages"] + all_missing
        _p(f"    {' '.join(install_cmd)}")

        result = subprocess.run(install_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            _p(f"  {GREEN}✓ Dependencies installed globally{RESET}", GREEN)
        else:
            _p(f"  {RED}✗ Failed to install some dependencies{RESET}", RED)
            for line in result.stderr.strip().splitlines():
                _p(f"    {line}")

        return "break-system"

    else:
        # ── Option 3: Skip — manual install ──
        _p(f"\n  {BOLD}Install manually:{RESET}")
        _p(f"    pip install {' '.join(all_missing)}")
        return "skip"


# ── Step 4 — Summary & Quick-Start Guide ──────────────────────────


def _step4_summary(install_method: str = "") -> None:
    """Print the setup summary and quick-start guide.

    *install_method* is one of ``'venv'``, ``'break-system'``, or
    ``'skip'`` as returned by ``_step3_deps()``.
    """
    _p(f"\n{BOLD}═══ Step 4: Setup Complete — Quick-Start Guide ═══{RESET}\n")

    _p(f"{GREEN}OpenTor is configured and ready to use.{RESET}", GREEN)

    # ── Virtual-environment reminder ──
    if install_method == "venv":
        _p(f"\n{BOLD}⚡ Activate the virtual environment before using OpenTor:{RESET}")
        _p(f"    source {os.path.join(OPENTOR_ROOT, '.venv', 'bin', 'activate')}")
        _p("")
        _p(f"  Quick verification:")
        _p(f"    source .venv/bin/activate && python3 scripts/opentor.py check")
        _p("")

    _p(f"\n{BOLD}1. Start Tor{RESET}")
    _p("   Make sure Tor is running before using OpenTor:")
    _p("     tor -f /tmp/tor_data/torrc   (custom config created above)")
    _p("     sudo systemctl start tor     (system Tor on Linux)")
    _p("     tor                           (direct launch)")

    _p(f"\n{BOLD}2. Verify Tor connectivity{RESET}")
    _p("   Test that the Tor transport layer works:")
    _p("     python3 scripts/torcore.py")
    _p("   (torcore.py is imported automatically by osint.py)")

    _p(f"\n{BOLD}3. Search the dark web{RESET}")
    _p("   Run a search from the command line using osint.py:")
    _p('     python3 -c "from scripts import osint; import json;')
    _p('       r = osint.search_darkweb(\'example query\');')
    _p('       print(json.dumps(r, indent=2))"')

    _p(f"\n{BOLD}4. List available search engines{RESET}")
    _p("   See all configured .onion search engines:")
    _p('     python3 -c "from scripts import engines;')
    _p("       print('\\n'.join(engines.ENGINE_NAMES))\"")

    _p(f"\n{BOLD}5. Explore further{RESET}")
    _p("   • Read the architecture guide:   cat CORE_ENGINE.md")
    _p("   • See usage examples:            cat EXAMPLES.md")
    _p("   • Check engine health:           python3 -c \"from scripts import engines;")
    _p("       print(engines.check_engines())\"")
    _p("   • Try different analysis modes:  python3 -c \"from scripts import engines;")
    _p("       print(engines.mode_engines('threat_intel'))\"")
    _p("")

    _p(f"{BOLD}{GREEN}Happy hunting!{RESET}")


# ── Main ───────────────────────────────────────────────────────────


def main() -> None:
    """Run the full interactive setup wizard."""
    _banner()
    _p(f"{BOLD}OpenTor root:{RESET} {OPENTOR_ROOT}")
    _p(f"{BOLD}Scripts dir:{RESET}  {SCRIPT_DIR}")
    _p("")

    if not _yes("Begin OpenTor setup?"):
        _p("Setup cancelled.", YELLOW)
        sys.exit(0)

    _step1_env()
    _step2_tor()
    install_method = _step3_deps()
    _step4_summary(install_method)


if __name__ == "__main__":
    main()
