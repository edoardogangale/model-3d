"""Il motore locale (TRELLIS.2 via comando) usa un finto worker: verifica il cablaggio, non la qualità del modello."""
import sys, os
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _views(tmp: Path):
    (tmp / "views").mkdir(parents=True, exist_ok=True)
    (tmp / "views" / "a.png").write_bytes(b"png")
    return [{"id": "a", "role": "front", "file": "views/a.png", "enabled": True},
            {"id": "b", "role": "back", "file": "views/b.png", "enabled": True}]


def test_local_engine_runs_command_and_reports_single_view(tmp_path, monkeypatch):
    from server.app import config
    from server.app.engines.local_command import LocalCommandEngine
    fake = tmp_path / "fake_worker.py"
    fake.write_text("import sys,pathlib\nargs=sys.argv[1:]\nout=args[args.index('--out')+1]\npathlib.Path(out).write_bytes(b'glTF'+b'0'*8)\nprint('fatto')\n")
    monkeypatch.setattr(config, "LOCAL_CMD", f"{sys.executable} {fake} --image {{image}} --out {{out}}")
    eng = LocalCommandEngine()
    assert eng.availability()["available"] is True
    jd = tmp_path / "job"; (jd / "raw").mkdir(parents=True)
    res = eng.run(jd, _views(jd), {}, lambda m: None, lambda: False)
    assert (jd / "raw" / "model.glb").read_bytes()[:4] == b"glTF"
    assert res["usage"]["used"] == ["a"] and res["usage"]["unused"] == ["b"]
    assert "singola" in " ".join(res["usage"]["notes"]).lower() or "sola vista" in " ".join(res["usage"]["notes"]).lower()


def test_local_engine_reports_failure(tmp_path, monkeypatch):
    from server.app import config
    from server.app.engines.local_command import LocalCommandEngine
    from server.app.engines.base import EngineError
    monkeypatch.setattr(config, "LOCAL_CMD", f"{sys.executable} -c 'import sys; sys.exit(3)'")
    jd = tmp_path / "job"; (jd / "raw").mkdir(parents=True)
    with pytest.raises(EngineError, match="codice 3"):
        LocalCommandEngine().run(jd, _views(jd), {}, lambda m: None, lambda: False)


def test_local_engine_unavailable_without_command(monkeypatch):
    from server.app import config
    from server.app.engines.local_command import LocalCommandEngine
    monkeypatch.setattr(config, "LOCAL_CMD", "")
    av = LocalCommandEngine().availability()
    assert av["available"] is False and av["missing"]
