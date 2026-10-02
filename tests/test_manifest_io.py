import json
import multiprocessing as mp

from lexeme_aligner.manifest_io import update_json


def _writer(path, key):
    for i in range(25):
        update_json(path, lambda d, k=f"{key}{i}": d.setdefault("languages", {}).__setitem__(k, {"n": i}))


def test_concurrent_writers_lose_no_entries(tmp_path):
    path = tmp_path / "manifest.json"
    procs = [mp.Process(target=_writer, args=(path, f"p{n}_")) for n in range(4)]
    [p.start() for p in procs]
    [p.join() for p in procs]
    assert all(p.exitcode == 0 for p in procs)
    assert len(json.loads(path.read_text())["languages"]) == 100


def test_atomic_replace_leaves_no_temp_files_and_sorted_output(tmp_path):
    path = tmp_path / "sub" / "manifest.json"
    update_json(path, lambda d: d.update(b=1, a=2), default={"schema": "x"})
    assert list(json.loads(path.read_text())) == ["a", "b", "schema"]
    assert [p.name for p in path.parent.iterdir() if p.suffix == ".tmp"] == []
