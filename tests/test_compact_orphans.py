"""compact_orphans: which HF files no current local file stands for (pure parts only; no HF)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("compact_orphans", Path(__file__).resolve().parents[1] / "pipeline/scripts/tools/compact_orphans.py")
co = importlib.util.module_from_spec(spec)
spec.loader.exec_module(co)


def test_only_language_files_of_the_layer_that_are_not_local():
    hf = ["README.md", "manifest.json", "_index/GEN_lexemes.json", "m/mya/_fingerprints.txt",
          "m/mya/mya_JUD/JUD_old11111.json", "m/mya/mya_JUD/JUD_new22222.json", "m/mya/mya_JUD/JUD_old11111.meta.json"]
    local = {"m/mya/mya_JUD/JUD_new22222.json", "m/mya/mya_JUD/JUD_new22222.meta.json"}
    assert co.orphans(hf, local, "main") == ["m/mya/mya_JUD/JUD_old11111.json"]
    assert co.orphans(hf, local, "meta") == ["m/mya/mya_JUD/JUD_old11111.meta.json"]
    assert not co.is_language_file("_index/x/y/z.json") and not co.is_language_file("m/mya/_fingerprints.txt")


def test_old_five_hex_names_are_kept_apart():
    hf = ["a/aaa/AAAMLT/1CO_1e815.json", "a/aaa/AAAMLT/1CO_0abc1234.json", "a/aaa/AAAMLT/1CO_ffff0000.json"]
    local = {"a/aaa/AAAMLT/1CO_ffff0000.json"}
    assert co.orphans(hf, local, "main", "fp8") == ["a/aaa/AAAMLT/1CO_0abc1234.json"]
    assert co.orphans(hf, local, "main", "old5") == ["a/aaa/AAAMLT/1CO_1e815.json"]
