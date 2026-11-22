"""Build / start / stop: the control worker (Docker replaced by a fake) and the gateway that guards it."""
import asyncio
import json

import httpx
import pytest
from control_worker import main as ctl
from fastapi.testclient import TestClient
from localai_gateway import main as gw


@pytest.fixture
def control(monkeypatch):
    monkeypatch.setattr(ctl, "PROJECT_DIR", "/srv/project")
    ran = []

    async def runner(cmd):
        ran.append(cmd)
        yield {"type": "log", "line": f"running {' '.join(cmd[-2:])}"}
        yield {"type": "exit", "code": 1 if "fail" in ctl.app.state.mode else 0}

    async def query(cmd, timeout=None):
        if cmd[:2] == ["docker", "ps"]:
            return 0, ["gateway", "search", "transcriber"]
        return (0, ["sha256:x"]) if cmd[-1].split("/")[1].split(":")[0] in ("transcriber", "reader") else (1, [])

    ctl.app.state.runner, ctl.app.state.query, ctl.app.state.mode = runner, query, ""
    ctl.app.state.ran = ran
    ctl.app.state.lock = asyncio.Lock()
    return ctl.app


def ndjson(r):
    return [json.loads(x) for x in r.text.splitlines() if x.strip()]


def test_the_commands_are_fixed_lists_without_a_shell(monkeypatch):
    monkeypatch.setattr(ctl, "PROJECT_DIR", "/p")
    base = ["docker", "compose", "--project-directory", "/p", "--profile"]
    assert ctl.command(ctl.Service.images, ctl.Action.build) == base + ["image", "build", "image"]
    assert ctl.command(ctl.Service.handwriting, ctl.Action.start) == base + [
        "handwriting", "up", "-d", "--no-deps", "--no-build", "reader"]
    assert ctl.command(ctl.Service.transcribe, ctl.Action.stop) == base + ["transcribe", "stop", "transcriber"]


def test_introduced_error_only_known_services_and_actions(control):
    c = TestClient(control)
    for path in ("/services/gateway/stop", "/services/ollama/start", "/services/control/stop",
                 "/services/documents/exec", "/services/documents;rm -rf/start", "/services/../build"):
        assert c.post(path).status_code in (404, 422), path
    assert control.state.ran == []


def test_an_action_streams_its_log_and_result(control):
    events = ndjson(TestClient(control).post("/services/images/build"))
    assert events[0] == {"type": "log", "line": "running build image"}
    assert events[-1]["type"] == "result" and events[-1]["service"] == "images"


def test_a_failed_start_says_to_build_first(control):
    control.state.mode = "fail"
    events = ndjson(TestClient(control).post("/services/voice/start"))
    assert events[-1]["type"] == "error" and "build it first" in events[-1]["message"]


def test_one_action_at_a_time(control):
    async def scenario():
        await control.state.lock.acquire()
    asyncio.run(scenario())
    assert TestClient(control).post("/services/images/stop").status_code == 409


def test_services_state(control):
    s = {x["name"]: x for x in TestClient(control).get("/services").json()["services"]}
    assert s["transcribe"]["built"] and s["transcribe"]["running"]
    assert s["handwriting"]["built"] and not s["handwriting"]["running"]
    assert not s["images"]["built"] and set(s) == {"documents", "transcribe", "speakers", "images", "handwriting",
                                                   "voice"}


def test_without_the_project_folder_nothing_runs(control, monkeypatch):
    monkeypatch.setattr(ctl, "PROJECT_DIR", "")
    assert TestClient(control).post("/services/images/build").status_code == 503


# ------------------------------------------------------------------ the gateway guards it

def gateway(fake, search_worker, control, tmp_path, **env):
    settings = gw.Settings({"OLLAMA_HOST": "http://fake-ollama", "DATA_DIR": str(tmp_path / "data"),
                            "UPLOADS_DIR": str(tmp_path / "uploads"), "WORKERS": "search=http://search:8000", **env})
    return gw.create_app(settings, ollama_transport=fake.transport, worker_transports={
        "search": httpx.ASGITransport(app=search_worker), "control": httpx.ASGITransport(app=control)})


def test_admin_on_this_machine(fake, search_worker, control, tmp_path):
    with TestClient(gateway(fake, search_worker, control, tmp_path)) as c:
        assert c.get("/v1/admin/services").json()["services"][0]["name"] == "documents"
        r = c.post("/v1/admin/services/images/build?wait=true")
        assert r.status_code == 200 and r.json()["result"]["action"] == "build"
        job = c.post("/v1/admin/services/images/stop").json()["id"]
        with c.stream("GET", f"/v1/jobs/{job}/events") as s:
            assert "event: log" in "".join(s.iter_text())


def test_introduced_error_open_to_the_network_without_a_key(fake, search_worker, control, tmp_path):
    with TestClient(gateway(fake, search_worker, control, tmp_path, BIND_ADDR="0.0.0.0")) as c:
        assert c.get("/v1/admin/services").status_code == 403
        assert c.post("/v1/admin/services/images/build").status_code == 403
    assert control.state.ran == []


def test_open_to_the_network_with_a_key(fake, search_worker, control, tmp_path):
    app = gateway(fake, search_worker, control, tmp_path, BIND_ADDR="0.0.0.0", LOCAL_AI_API_KEY="k3y")
    with TestClient(app) as c:
        assert c.get("/v1/admin/services").status_code == 401
        assert c.get("/v1/admin/services", headers={"X-API-Key": "k3y"}).status_code == 200


def test_gateway_rejects_unknown_names_before_reaching_control(fake, search_worker, control, tmp_path):
    with TestClient(gateway(fake, search_worker, control, tmp_path)) as c:
        assert c.post("/v1/admin/services/gateway/stop").status_code == 422
        assert c.post("/v1/admin/services/images/rm").status_code == 422
    assert control.state.ran == []


def test_control_off(fake, search_worker, control, tmp_path):
    def down(request):
        raise httpx.ConnectError("no control")
    settings = gw.Settings({"OLLAMA_HOST": "http://fake-ollama", "DATA_DIR": str(tmp_path / "d"),
                            "WORKERS": "search=http://search:8000"})
    app = gw.create_app(settings, ollama_transport=fake.transport, worker_transports={
        "search": httpx.ASGITransport(app=search_worker), "control": httpx.MockTransport(down)})
    with TestClient(app) as c:
        r = c.get("/v1/admin/services")
    assert r.status_code == 503 and "--profile control" in r.json()["detail"]


def test_commands_run_in_the_project_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(ctl, "PROJECT_DIR", str(tmp_path))

    async def collect():
        return [ev async for ev in ctl.stream(["pwd"])]
    events = asyncio.run(collect())
    assert events[0] == {"type": "log", "line": str(tmp_path)} and events[-1] == {"type": "exit", "code": 0}
