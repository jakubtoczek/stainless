"""Offer the app in Windows' "Open with" list for its documents (.json), never as the default: a
per-user ProgID (HKCU\\Software\\Classes, no admin) whose command runs this environment's pythonw on
the file, and the ProgID named under the extension's OpenWithProgids. Rewritten at each start so it
follows the folder; uninstall.bat removes it (the one registry entry the suite writes — House rules).
One copy in each app that opens documents, identical to the suite's (suite.py checks). Run it for its
self-check.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path


def command(exe: str, module: str, src: Path) -> str:
    """The shell's command line: pythonw running the package from its source folder on "%1"."""
    code = (f"import runpy,sys;sys.path.insert(0,r'{src}');sys.argv[0]='{module}';"
            f"runpy.run_module('{module}',run_name='__main__')")
    return f'"{exe}" -I -c "{code}" "%1"'    # -I: the document's folder never on sys.path


def register(progid: str, name: str, module: str, src: Path, icon: Path,
             exts: tuple[str, ...] = (".json",), root: str = r"Software\Classes") -> None:
    """`name` in Open with for `exts`: what the ProgID `progid` runs is `module` with the file.
    In a thread, never raising: the app starts whatever the registry says."""
    exe = Path(sys.executable)
    if sys.platform != "win32" or exe.name.lower() != "pythonw.exe":
        return                                   # run some other way (a test, a dev shell): nothing
    threading.Thread(target=_write, args=(progid, name, command(str(exe), module, src), icon, exts, root),
                     daemon=True).start()


def _write(progid, name, cmd, icon, exts, root) -> None:
    import winreg as w
    try:
        with w.CreateKey(w.HKEY_CURRENT_USER, rf"{root}\{progid}") as k:
            w.SetValueEx(k, "", 0, w.REG_SZ, f"{name} document")
        with w.CreateKey(w.HKEY_CURRENT_USER, rf"{root}\{progid}\DefaultIcon") as k:
            w.SetValueEx(k, "", 0, w.REG_SZ, f"{icon},0")
        with w.CreateKey(w.HKEY_CURRENT_USER, rf"{root}\{progid}\shell\open") as k:
            w.SetValueEx(k, "FriendlyAppName", 0, w.REG_SZ, name)      # what Open with shows
        with w.CreateKey(w.HKEY_CURRENT_USER, rf"{root}\{progid}\shell\open\command") as k:
            w.SetValueEx(k, "", 0, w.REG_SZ, cmd)
        for ext in exts:
            with w.CreateKey(w.HKEY_CURRENT_USER, rf"{root}\{ext}\OpenWithProgids") as k:
                w.SetValueEx(k, progid, 0, w.REG_SZ, "")
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED
    except OSError:
        pass


def unregister(progid: str, exts: tuple[str, ...] = (".json",), root: str = r"Software\Classes") -> None:
    """What register wrote, gone (uninstall.bat does the same with reg delete)."""
    import winreg as w
    for ext in exts:
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, rf"{root}\{ext}\OpenWithProgids", 0, w.KEY_SET_VALUE) as k:
                w.DeleteValue(k, progid)
        except OSError:
            pass
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", r"\DefaultIcon", ""):
        try:
            w.DeleteKey(w.HKEY_CURRENT_USER, rf"{root}\{progid}{sub}")
        except OSError:
            pass


if __name__ == "__main__":
    import winreg as w
    root = r"Software\LRBOpenWithSelfCheck"      # never the real Classes key
    cmd = command(r"C:\x\pythonw.exe", "app", Path(r"C:\x\src"))
    assert cmd.startswith('"C:\\x\\pythonw.exe" -I -c "import runpy') and cmd.endswith('" "%1"'), cmd
    _write("App.json", "App", cmd, Path(r"C:\x\app.ico"), (".json",), root)
    with w.OpenKey(w.HKEY_CURRENT_USER, rf"{root}\App.json\shell\open") as k:
        assert w.QueryValueEx(k, "FriendlyAppName")[0] == "App"
    with w.OpenKey(w.HKEY_CURRENT_USER, rf"{root}\.json\OpenWithProgids") as k:
        assert w.QueryValueEx(k, "App.json")[0] == ""
    unregister("App.json", (".json",), root)
    try:
        w.OpenKey(w.HKEY_CURRENT_USER, rf"{root}\App.json")
        raise AssertionError("left behind")
    except FileNotFoundError:
        pass
    for k in (rf"{root}\.json\OpenWithProgids", rf"{root}\.json", root):
        w.DeleteKey(w.HKEY_CURRENT_USER, k)
    print("openwith self-check ok")
