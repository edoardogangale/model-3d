"""Test dell'intera catena sul manichino sintetico (verifica rig/esportazione/validazione, NON la ricostruzione da foto)."""
import sys, time, json
from pathlib import Path
import numpy as np, pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.app import skeleton as sk, rigging as rg, meshprep as mp
from server.app.engines import fixture
from server.app.glbio import GLB
from server.app import validate as val


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    os.environ["MODEL3D_DATA"] = str(tmp_path_factory.mktemp("data"))
    for m in [m for m in sys.modules if m.startswith("server.app")]: del sys.modules[m]
    from fastapi.testclient import TestClient
    from server.app.main import app
    return TestClient(app)


def wait(c, jid, stage, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get(f"/api/jobs/{jid}").json()
        sts = [v["state"] for v in j["stages"].values()]
        if j["stages"][stage]["state"] == "done" or any(x in ("error", "cancelled", "interrupted") for x in sts): return j
        time.sleep(0.3)
    raise TimeoutError(stage)


def test_joint_estimation_accuracy():
    m = fixture.build(rotate_y_deg=180)
    m, rep = mp.clean(m)
    assert rep["removed_floor_slabs"] == 1 and rep["removed_fragments"] == 1
    assert mp.facing_estimate(m.v)[1] == "-Z"
    m = mp.rotate_y(m, 180); m, _ = mp.place(m, 1.67)
    J = rg.symmetrize(rg.estimate_joints(m.v)[0]); T = fixture.truth_joints(); s = 1.67 / 1.705
    errs = {k: np.linalg.norm(J[k] - T[k] * s) for k in sk.NAMES}
    assert max(errs.values()) < 0.07, errs


def test_weights_are_valid_and_pose_conversion_consistent():
    m = fixture.build(); m, _ = mp.clean(m); m, _ = mp.place(m, 1.67); m, _ = mp.decimate(m, 20000)
    J = sk.joints_array(rg.symmetrize(rg.estimate_joints(m.v)[0]))
    ji, w = rg.compute_weights(m.v, m.f, J)
    assert np.allclose(w.sum(1), 1, atol=1e-5) and (w.sum(1) > 0.99).all() and ji.shape[1] == 4
    v2, J2, info = rg.convert_pose(m.v, ji, w.astype(float), J, target="A")
    assert info["converted"] and abs(v2[:, 1].min()) < 1e-6
    # la mano sinistra deve ora stare più in basso-fuori con angolo ~40° dalla verticale
    d = J2[sk.IDX["LeftHand"]] - J2[sk.IDX["LeftArm"]]
    ang = np.degrees(np.arccos(-d[1] / np.linalg.norm(d)))
    assert abs(ang - 40) < 1.0
    # i vertici del braccio si sono mossi, quelli della testa no
    moved = np.linalg.norm(v2 - m.v, axis=1)
    assert moved[m.v[:, 1] > 1.6].max() < 0.02 and moved.max() > 0.1
    assert rg.edge_stretch(m.v, v2, m.f)["max"] < 4


def test_full_api_flow_and_gates(client):
    c = client
    caps = c.get("/api/capabilities").json()
    ids = {e["id"]: e for e in caps["engines"]}
    assert ids["fixture"]["reconstruction"] is False
    assert ids["meshy"]["available"] is False and ids["meshy"]["uploads_photos"] is True   # nessuna chiave → non disponibile
    jid = c.post("/api/jobs", json={"name": "Babu"}).json()["id"]
    # un motore remoto richiede conferma e chiave
    r = c.post(f"/api/jobs/{jid}/run", json={"start": "reconstruct", "end": "export", "engine": "meshy"})
    assert r.status_code == 409
    r = c.post(f"/api/jobs/{jid}/run", json={"start": "reconstruct", "end": "export", "engine": "fixture"})
    assert r.status_code == 200
    j = wait(c, jid, "export")
    assert [j["stages"][s]["state"] for s in ("reconstruct", "prepare", "rig", "export")] == ["done"] * 4, j["stages"]
    rep = c.get(f"/api/jobs/{jid}/file/validation.json").json()
    assert rep["verdict"] == "draft" and not rep["ready"]
    structural = [x for x in rep["checks"] if x["category"] == "structural" and not x["ok"] and x["severity"] == "error"]
    assert not structural, structural
    # download "avatar pronto" bloccato, bozza consentita e chiaramente nominata
    assert c.get(f"/api/jobs/{jid}/download").status_code == 409
    d = c.get(f"/api/jobs/{jid}/download-draft")
    assert d.status_code == 200 and "BOZZA" in d.headers["content-disposition"]
    open(Path(__import__("os").environ["MODEL3D_DATA"]) / "draft.glb", "wb").write(d.content)
    assert d.content[:4] == b"glTF"


def test_exported_glb_is_a_real_skinned_mesh(client):
    p = Path(__import__("os").environ["MODEL3D_DATA"]) / "draft.glb"
    g = GLB(p); j = g.json
    assert not [i for i in j["images"] if "uri" in i] and not [b for b in j["buffers"] if "uri" in b]
    assert j["skins"] and len(j["skins"][0]["joints"]) == len(sk.NAMES)
    r = val.validate_glb(p, provenance={"reconstruction": True})
    assert r["ready"], r["reasons"]            # con provenienza reale dichiarata, una struttura valida diventa "ready"
    assert r["stats"]["triangles"] <= 30000 and r["stats"]["textures"][0][0] <= 2048


def test_validator_rejects_static_and_broken_files(tmp_path):
    from server.app.glbio import write_static_glb
    from PIL import Image
    m = fixture.build(floor=False, fragment=False)
    q = tmp_path / "static.glb"
    write_static_glb(q, m.v, m.normals(), m.uv, m.f, m.tex)
    r = val.validate_glb(q, provenance={"reconstruction": True}, node_validator=False)
    assert not r["ready"] and any("STATICO" in x["message"] for x in r["checks"])
    (tmp_path / "bad.glb").write_bytes(b"not a glb")
    assert not val.validate_glb(tmp_path / "bad.glb")["ready"]


def test_validator_detects_corrupted_weights(client, tmp_path):
    import struct
    p = Path(__import__("os").environ["MODEL3D_DATA"]) / "draft.glb"
    raw = bytearray(p.read_bytes()); g = GLB(p)
    acc = g.json["accessors"][g.json["meshes"][0]["primitives"][0]["attributes"]["WEIGHTS_0"]]
    bv = g.json["bufferViews"][acc["bufferView"]]
    # posizione del BIN nel file: 12 + 8 + jsonlen + 8
    jl = struct.unpack("<I", raw[12:16])[0]; binoff = 12 + 8 + jl + 8
    off = binoff + bv["byteOffset"]
    raw[off:off + 16] = struct.pack("<4f", 0, 0, 0, 0)      # primo vertice senza pesi
    bad = tmp_path / "bad_w.glb"; bad.write_bytes(raw)
    r = val.validate_glb(bad, provenance={"reconstruction": True}, node_validator=False)
    assert not r["ready"] and any(x["id"] in ("no_unweighted", "weights_norm") and not x["ok"] for x in r["checks"])


def test_token_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL3D_DATA", str(tmp_path)); monkeypatch.setenv("MODEL3D_TOKEN", "segreto")
    for m in [m for m in sys.modules if m.startswith("server.app")]: del sys.modules[m]
    from fastapi.testclient import TestClient
    from server.app.main import app
    c = TestClient(app)
    assert c.get("/api/jobs").status_code == 401
    assert c.post("/api/login", json={"token": "sbagliato"}).status_code == 401
    assert c.post("/api/login", json={"token": "segreto"}).status_code == 200
    assert c.get("/api/jobs").status_code == 200
