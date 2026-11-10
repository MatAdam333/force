"""share/fonts.sh on WSL (SPEC §45): the symbols font goes into Windows for the user and a Windows Terminal fragment
adds a "Force (<distro>)" profile; Linux gets no font. Windows is simulated: cmd.exe, reg.exe and wslpath stand-ins
and a fake %LOCALAPPDATA% (the font file is already there, so nothing is downloaded)."""
import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _windows(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    lad = tmp_path / "c" / "Users" / "u" / "AppData" / "Local"
    (lad / "Microsoft" / "Windows" / "Fonts").mkdir(parents=True)
    (lad / "Microsoft" / "Windows" / "Fonts" / "SymbolsNerdFontMono-Regular.ttf").write_bytes(b"font")
    (bin_ / "cmd.exe").write_text("#!/bin/sh\nprintf 'C:\\\\Users\\\\u\\\\AppData\\\\Local\\r\\n'\n")
    (bin_ / "reg.exe").write_text(f"#!/bin/sh\necho \"$@\" >> '{tmp_path}/reg.log'\n")
    (bin_ / "wslpath").write_text(f"#!/bin/sh\n[ \"$1\" = -u ] && shift\necho \"$1\" | sed 's#^C:#{tmp_path}/c#; s#\\\\#/#g'\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": f"{bin_}:/usr/bin:/bin", "WSL_DISTRO_NAME": "Ubuntu-24.04"}
    return lad, home, env


def _fonts(env, *args):
    return subprocess.run(["bash", str(REPO / "share" / "fonts.sh"), *args], env=env, capture_output=True, text=True,
                          timeout=60)


def test_wsl_adds_a_force_profile_and_registers_the_font(tmp_path):
    lad, home, env = _windows(tmp_path)
    r = _fonts(env)
    assert r.returncode == 0, r.stdout + r.stderr
    frag = lad / "Microsoft" / "Windows Terminal" / "Fragments" / "Force" / "force-Ubuntu-24.04.json"
    prof = json.loads(frag.read_text())["profiles"][0]
    assert prof["name"] == "Force (Ubuntu-24.04)"
    assert prof["font"]["face"] == "Cascadia Mono, Symbols Nerd Font Mono"
    assert prof["commandline"] == f"wsl.exe -d Ubuntu-24.04 --cd ~ -- bash -lc 'exec {home}/.local/bin/force --glyphs rich'"
    reg = (tmp_path / "reg.log").read_text()
    assert "add HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts /v Symbols Nerd Font Mono (TrueType)" in reg
    assert not (home / ".local" / "share" / "fonts").exists()            # no Linux font: it never reaches the terminal


def test_wsl_uninstall_removes_profile_font_and_registration(tmp_path):
    lad, home, env = _windows(tmp_path)
    assert _fonts(env).returncode == 0
    assert _fonts(env, "--uninstall").returncode == 0
    assert not (lad / "Microsoft" / "Windows Terminal" / "Fragments" / "Force").exists()
    assert not (lad / "Microsoft" / "Windows" / "Fonts" / "SymbolsNerdFontMono-Regular.ttf").exists()
    assert "delete HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts" in (tmp_path / "reg.log").read_text()


def test_wsl_without_windows_interop_explains_and_exits_3(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "WSL_DISTRO_NAME": "Ubuntu-24.04"}
    r = _fonts(env)
    if os.path.exists("/mnt/c/Windows/System32/cmd.exe"):                # (a real WSL host has interop)
        return
    assert r.returncode == 3
    assert "force settings set icons on" in r.stdout
