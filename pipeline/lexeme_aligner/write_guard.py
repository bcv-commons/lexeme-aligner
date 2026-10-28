"""Write guard for align-text (2026-10-28): a chain step may write only inside the folders it is given.

align-text runs our chain on someone else's machine and promises that nothing outside their output folder changes. Many module-level paths
are relative to the working directory (`Path("config/...")`, `Path("publish/...")`), so environment settings alone cannot keep that
promise. The guard enforces it in the step's own interpreter:

- an audit hook refuses (PermissionError) every write-like event whose target lies outside the allowed folders: `open` for writing,
  `os.rename`/`os.replace` (source and destination), `os.remove`, `os.rmdir`, `os.mkdir` (of a folder that does not exist yet),
  `shutil.rmtree`, `os.link`/`os.symlink`, `os.truncate`, `os.chmod`/`os.chown`/`os.utime`;
- `sqlite3.connect` to a database outside them is opened read-only (`mode=ro`); creating a new one there is refused;
- pyarrow writers (parquet `write_table`/`ParquetWriter`/`write_to_dataset`, `dataset.write_dataset`, `feather.write_feather`) write in
  C++ without an `open` event, so they are wrapped when pyarrow is imported.

Reads are never restricted. The system temp folder is always allowed (eflomal works there). Every refusal is appended to
`<first allowed folder>/guard.log` before it is raised, so a refused write shows up even when the caller swallows the error.

Run a step under the guard:

    python -m lexeme_aligner.write_guard --allow OUT [--allow DIR ...] -- -m lexeme_aligner.run_pilot --method eflomal ...

or set ALIGNER_GUARD_ALLOW=dir1:dir2 and call `install_from_env()`. Chain steps start no Python subprocesses (checked 2026-10-28), so
guarding the step's interpreter covers the step. Not covered: writes made by C code that never passes through Python (eflomal's own
binary writes only in the temp folder).
"""
from __future__ import annotations

import os
import sys
import tempfile

_ALLOWED: tuple[str, ...] = ()
_LOG: str | None = None
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
# event -> index of its dir_fd argument (None: the event has none), for events whose first argument is the path written
_PATH_EVENTS = {"os.remove": 1, "os.rmdir": 1, "shutil.rmtree": 1, "os.truncate": None, "os.chmod": 2, "os.chown": 3, "os.utime": 3}
_IN_REFUSE = False


def _real(p) -> str | None:
    if isinstance(p, int):                             # an open file descriptor
        try:
            return os.path.realpath(os.readlink(f"/proc/self/fd/{p}"))
        except OSError:
            pass
        try:
            import fcntl                               # macOS
            return os.path.realpath(fcntl.fcntl(p, fcntl.F_GETPATH, b"\0" * 1024).rstrip(b"\0").decode())
        except (ImportError, AttributeError, OSError, ValueError):
            return None
    try:
        return os.path.realpath(os.fsdecode(p))
    except (TypeError, ValueError):
        return None


def _at(p, dir_fd) -> str | None:
    """Absolute real path of `p`, resolved against `dir_fd` when one is given (shutil.rmtree's fd-based walk)."""
    if dir_fd is None or (isinstance(dir_fd, int) and dir_fd < 0) or isinstance(p, int) or os.path.isabs(os.fsdecode(p)):  # -1 = no dir_fd
        return _real(p)
    base = _real(dir_fd)
    return None if base is None else os.path.realpath(os.path.join(base, os.fsdecode(p)))


def allowed(path: str | None) -> bool:
    if path is None:
        return False
    return path == os.devnull or any(path == a or path.startswith(a + os.sep) for a in _ALLOWED)


def _refuse(event: str, path: str | None) -> None:
    global _IN_REFUSE
    msg = f"write guard: {event} outside the allowed folders: {path}"
    if _LOG and not _IN_REFUSE:
        _IN_REFUSE = True
        try:
            with _orig_open(_LOG, "a", encoding="utf-8") as f:
                f.write(f"{event}\t{path}\t{' '.join(sys.argv)[:300]}\n")
        except OSError:
            pass
        finally:
            _IN_REFUSE = False
    raise PermissionError(msg)


def _check(event: str, path: str | None) -> None:
    if not allowed(path):
        _refuse(event, path)


def _hook(event: str, args: tuple) -> None:
    try:
        _inspect(event, args)
    except PermissionError:
        raise
    except Exception:                                  # an argument shape we do not know: never break the step over it
        pass


def _arg(args: tuple, i: int | None):
    return args[i] if i is not None and len(args) > i else None


def _inspect(event: str, args: tuple) -> None:
    if event == "open":
        if not args or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        mode, flags = (args[1] if len(args) > 1 else None), (args[2] if len(args) > 2 else 0)
        writes = any(c in mode for c in "wax+") if isinstance(mode, str) else bool((flags or 0) & _WRITE_FLAGS)
        if writes:
            _check("open", _real(args[0]))
    elif event in ("os.rename", "os.replace"):
        _check(event, _at(args[0], _arg(args, 2)))
        _check(event, _at(args[1], _arg(args, 3)))
    elif event == "os.mkdir":
        p = _at(args[0], _arg(args, 2))
        if p is not None and not os.path.exists(p):
            _check(event, p)
    elif event == "os.link":
        _check(event, _at(args[1], _arg(args, 3)))
    elif event == "os.symlink":
        _check(event, _at(args[1], _arg(args, 2)))
    elif event in _PATH_EVENTS:
        if args and args[0] is not None:
            _check(event, _at(args[0], _arg(args, _PATH_EVENTS[event])))


_orig_open = open


def _guard_sqlite() -> None:
    import sqlite3
    if getattr(sqlite3.connect, "_write_guard", False):
        return
    orig = sqlite3.connect

    def connect(database, *a, **k):
        db = os.fsdecode(database) if isinstance(database, (str, bytes, os.PathLike)) else database
        if isinstance(db, str) and db != ":memory:" and not db.startswith("file::memory:"):
            uri = k.get("uri", False)
            path = db[5:].split("?", 1)[0] if uri and db.startswith("file:") else db
            real = _real(path)
            if not allowed(real):
                if not os.path.exists(real or path):
                    _refuse("sqlite3.connect (new database)", real)
                if not (uri and "mode=ro" in db):
                    db, k = f"file:{real}?mode=ro", {**k, "uri": True}
        return orig(db, *a, **k)

    connect._write_guard = True
    sqlite3.connect = connect


def _wrap_writer(mod, name: str, pos: int, kw: str) -> None:
    orig = getattr(mod, name, None)
    if orig is None or getattr(orig, "_write_guard", False):
        return

    def wrapped(*a, **k):
        target = k.get(kw, a[pos] if len(a) > pos else None)
        if isinstance(target, (str, bytes, os.PathLike)):
            _check(f"pyarrow {name}", _real(target))
        return orig(*a, **k)

    wrapped._write_guard = True
    setattr(mod, name, wrapped)


def _guard_pyarrow(name: str, mod) -> None:
    if name == "pyarrow.parquet":
        _wrap_writer(mod, "write_table", 1, "where")
        _wrap_writer(mod, "write_to_dataset", 1, "root_path")
        orig = getattr(mod, "ParquetWriter", None)
        if orig is not None and not getattr(orig, "_write_guard", False):
            class ParquetWriter(orig):                 # a subclass, so isinstance checks and attributes keep working
                _write_guard = True

                def __init__(self, where, *a, **k):
                    if isinstance(where, (str, bytes, os.PathLike)):
                        _check("pyarrow ParquetWriter", _real(where))
                    super().__init__(where, *a, **k)
            mod.ParquetWriter = ParquetWriter
    elif name == "pyarrow.dataset":
        _wrap_writer(mod, "write_dataset", 1, "base_dir")
    elif name == "pyarrow.feather":
        _wrap_writer(mod, "write_feather", 1, "dest")


_PYARROW = ("pyarrow.parquet", "pyarrow.dataset", "pyarrow.feather")


class _PostImport:
    """Meta-path finder that patches the pyarrow writer modules right after they are imported (importing pyarrow eagerly in every
    step would cost startup time and import it where it is not used)."""

    def find_spec(self, name, path=None, target=None):
        if name not in _PYARROW:
            return None
        sys.meta_path.remove(self)
        try:
            import importlib.util
            spec = importlib.util.find_spec(name)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None:
            return spec
        loader = spec.loader
        exec_module = loader.exec_module

        def patched(module, _exec=exec_module, _name=name):
            _exec(module)
            _guard_pyarrow(_name, module)
        loader.exec_module = patched
        return spec


def install(allow: list[str | os.PathLike], log: str | os.PathLike | None = None) -> None:
    """Allow writes only inside `allow` (+ the system temp folder) for the rest of this interpreter's life. Cannot be undone."""
    global _ALLOWED, _LOG
    if _ALLOWED:
        raise RuntimeError("write guard already installed")
    dirs = [os.path.realpath(os.fsdecode(a)) for a in allow if a]
    if not dirs:
        raise ValueError("write guard needs at least one allowed folder")
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    _ALLOWED = tuple(dict.fromkeys([*dirs, os.path.realpath(tempfile.gettempdir())]))
    _LOG = os.path.join(dirs[0], "guard.log") if log is None else os.path.realpath(os.fsdecode(log))
    sys.dont_write_bytecode = True                     # __pycache__ beside our modules is outside the allowed folders
    _guard_sqlite()
    for name in _PYARROW:
        if name in sys.modules:
            _guard_pyarrow(name, sys.modules[name])
    sys.meta_path.insert(0, _PostImport())
    sys.addaudithook(_hook)


def install_from_env() -> bool:
    raw = os.environ.get("ALIGNER_GUARD_ALLOW")
    if not raw:
        return False
    install(raw.split(os.pathsep))
    return True


def main(argv: list[str] | None = None) -> int:
    import argparse
    import runpy
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        raise SystemExit("usage: python -m lexeme_aligner.write_guard --allow DIR [--allow DIR ...] -- (-m MODULE | SCRIPT) [ARGS ...]")
    cut = argv.index("--")
    ap = argparse.ArgumentParser(prog="write_guard")
    ap.add_argument("--allow", action="append", required=True, help="folder writes are allowed in (repeatable)")
    args = ap.parse_args(argv[:cut])
    rest = argv[cut + 1:]
    if not rest:
        raise SystemExit("write_guard: nothing to run after --")
    install(args.allow)
    if rest[0] == "-m":
        if len(rest) < 2:
            raise SystemExit("write_guard: -m needs a module name")
        sys.argv = [rest[1], *rest[2:]]
        runpy.run_module(rest[1], run_name="__main__", alter_sys=True)
    else:
        sys.argv = rest
        runpy.run_path(rest[0], run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
