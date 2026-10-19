"""Install an optional per-user Linux desktop entry for the extracted package."""
import os
from pathlib import Path
import sys


def desktop_exec(executable: Path) -> str:
    value = str(executable.resolve())
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("Unsupported control character in installation path")
    # Desktop Entry Specification: percent is a field-code escape; reserved
    # characters are quoted, then backslashes escaped at the string layer.
    value = value.replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, "\\" + character)
    # GLib checks the first executable before expanding %% field codes.
    # A fixed env executable permits literal percent in the wallet path;
    # no shell expansion is involved.
    return '/usr/bin/env "' + value.replace("\\", "\\\\") + '"'


def install_desktop_entry(executable: Path) -> Path:
    if not sys.platform.startswith("linux") or not getattr(sys, "frozen", False):
        raise RuntimeError("Run the packaged Linux wallet with --install-desktop")
    base = Path(os.environ.get("XDG_DATA_HOME", ""))
    if not base.is_absolute():
        base = Path.home() / ".local" / "share"
    applications = base / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    target = applications / "bitcoin-k-wallet.desktop"
    text = ("[Desktop Entry]\nType=Application\nName=BTC-K Wallet\n"
            "Comment=BTC-K encrypted desktop wallet\n"
            f"Exec={desktop_exec(executable)}\nTerminal=false\n"
            "Icon=wallet\nCategories=Office;Finance;\nStartupNotify=true\n")
    # Replace the launcher itself instead of following a pre-existing symlink.
    temporary = applications / f".bitcoin-k-wallet-{os.getpid()}.desktop"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
        temporary.chmod(0o644)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
