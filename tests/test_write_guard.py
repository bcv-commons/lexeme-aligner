"""write_guard: each case runs in a fresh interpreter (the guard cannot be uninstalled)."""
import subprocess
import sys
import textwrap

import pytest


def _run(tmp_path, body: str, allow=None):
    """Run `body` under the guard with `allow` (default tmp/out) as the allowed folder; `outside` is a folder it must not touch.
    The system temp folder is always allowed, so `outside` lives under tmp_path/outside and the test points TMPDIR elsewhere."""
    out, outside, tmp = tmp_path / "out", tmp_path / "outside", tmp_path / "systmp"
    outside.mkdir()
    tmp.mkdir()
    (outside / "existing.txt").write_text("keep")
    script = textwrap.dedent(f"""
        import os, sys, sqlite3
        from pathlib import Path
        OUT, OUTSIDE = Path({str(out)!r}), Path({str(outside)!r})
        from lexeme_aligner import write_guard
        write_guard.install([{str(allow or out)!r}])
        def refused(fn):
            try:
                fn()
            except PermissionError:
                return True
            return False
    """) + textwrap.dedent(body)
    env = {"PATH": "/usr/bin:/bin", "TMPDIR": str(tmp), "PYTHONPATH": ":".join(sys.path)}
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout, out, outside


def test_writes_inside_allowed_outside_refused_and_logged(tmp_path):
    stdout, out, outside = _run(tmp_path, """
        (OUT / "a" ).mkdir(); (OUT / "a" / "f.txt").write_text("x")
        os.replace(OUT / "a" / "f.txt", OUT / "g.txt")
        print(refused(lambda: (OUTSIDE / "new.txt").write_text("x")))
        print(refused(lambda: open(OUTSIDE / "existing.txt", "a")))
        print(refused(lambda: os.open(OUTSIDE / "x", os.O_WRONLY | os.O_CREAT)))
        print(refused(lambda: os.replace(OUT / "g.txt", OUTSIDE / "g.txt")))
        print(refused(lambda: os.remove(OUTSIDE / "existing.txt")))
        print(refused(lambda: (OUTSIDE / "d").mkdir()))
        print(refused(lambda: os.symlink(OUT / "g.txt", OUTSIDE / "l")))
        print(open(OUTSIDE / "existing.txt").read())          # reads are free
        os.makedirs(OUTSIDE, exist_ok=True)                      # an existing folder is not a write
        import tempfile; tempfile.NamedTemporaryFile(delete=True).close()
    """)
    lines = stdout.split()
    assert lines[:7] == ["True"] * 7 and lines[7] == "keep"
    assert (out / "g.txt").read_text() == "x"
    assert sorted(p.name for p in outside.iterdir()) == ["existing.txt"]
    log = (out / "guard.log").read_text().splitlines()
    assert len(log) == 7 and all(str(outside) in l for l in log)


def test_relative_paths_resolve_against_cwd(tmp_path):
    stdout, out, outside = _run(tmp_path, """
        os.chdir(OUTSIDE)
        print(refused(lambda: Path("publish/x.json").parent.mkdir(parents=True)))
        print(refused(lambda: Path("rel.txt").write_text("x")))
        os.chdir(OUT); Path("rel.txt").write_text("ok")
    """)
    assert stdout.split() == ["True", "True"]
    assert (out / "rel.txt").read_text() == "ok"


def test_symlink_out_of_allowed_folder_is_followed(tmp_path):
    stdout, out, outside = _run(tmp_path, """
        os.symlink(OUTSIDE, OUT / "link")
        print(refused(lambda: (OUT / "link" / "sneaky.txt").write_text("x")))
    """)
    assert stdout.split() == ["True"]
    assert not (outside / "sneaky.txt").exists()


def test_shutil_rmtree_inside_allowed(tmp_path):
    stdout, out, outside = _run(tmp_path, """
        import shutil
        (OUT / "t" / "u").mkdir(parents=True); (OUT / "t" / "u" / "f").write_text("x")
        shutil.rmtree(OUT / "t")
        print((OUT / "t").exists())
        print(refused(lambda: shutil.rmtree(OUTSIDE)))
    """)
    assert stdout.split() == ["False", "True"]
    assert (outside / "existing.txt").exists()


def test_sqlite_outside_is_read_only(tmp_path):
    db = tmp_path / "outside_db.sqlite"
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("create table t (x)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    stdout, out, outside = _run(tmp_path, f"""
        con = sqlite3.connect({str(db)!r})
        print(con.execute("select x from t").fetchone()[0])
        try:
            con.execute("insert into t values (2)"); con.commit(); print("wrote")
        except sqlite3.OperationalError:
            print("readonly")
        print(refused(lambda: sqlite3.connect(OUTSIDE / "new.db")))
        c2 = sqlite3.connect(OUT / "mine.db"); c2.execute("create table y (z)"); c2.commit()
        print(sqlite3.connect(":memory:").execute("select 1").fetchone()[0])
    """)
    assert stdout.split() == ["1", "readonly", "True", "1"]
    assert not (outside / "new.db").exists() and (out / "mine.db").exists()


def test_pyarrow_writers(tmp_path):
    pa = pytest.importorskip("pyarrow")
    stdout, out, outside = _run(tmp_path, """
        import pyarrow as pa, pyarrow.parquet as pq
        t = pa.table({"a": [1, 2]})
        pq.write_table(t, OUT / "ok.parquet")
        print(refused(lambda: pq.write_table(t, OUTSIDE / "no.parquet")))
        print(refused(lambda: pq.ParquetWriter(str(OUTSIDE / "no2.parquet"), t.schema)))
        w = pq.ParquetWriter(str(OUT / "ok2.parquet"), t.schema); w.write_table(t); w.close()
        print(isinstance(w, pq.ParquetWriter), pq.read_table(OUTSIDE.parent / "out" / "ok.parquet").num_rows)
    """)
    assert stdout.split() == ["True", "True", "True", "2"]
    assert not any(p.suffix == ".parquet" for p in outside.iterdir())


def test_main_runs_a_module_under_the_guard(tmp_path):
    out, outside = tmp_path / "out", tmp_path / "outside"
    outside.mkdir()
    mod, systmp = tmp_path / "mods", tmp_path / "systmp"
    mod.mkdir()
    systmp.mkdir()
    (mod / "stepmod.py").write_text(textwrap.dedent(f"""
        import sys
        from pathlib import Path
        Path({str(out)!r}, "ok.txt").write_text(" ".join(sys.argv[1:]))
        Path({str(outside)!r}, "no.txt").write_text("x")
    """))
    env = {"PATH": "/usr/bin:/bin", "TMPDIR": str(systmp), "PYTHONPATH": ":".join([str(mod), *sys.path])}
    r = subprocess.run([sys.executable, "-m", "lexeme_aligner.write_guard", "--allow", str(out), "--", "-m", "stepmod", "a", "b"],
                       capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "PermissionError" in r.stderr
    assert (out / "ok.txt").read_text() == "a b"
    assert not (outside / "no.txt").exists()
