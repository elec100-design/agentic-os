import base64
import json

import pytest

from app import config, db, media, worker
from app.providers import MEDIA


def _media_job(conn, kind, prompt="붉은 원", project=True):
    """미디어 잡 + (기본) 연결된 태스크를 만든다."""
    job_id = db.create_job(conn, prompt, MEDIA, model=kind)
    if project:
        pid = db.create_project(conn, "목표")
        tid = db.create_task(conn, pid, 1, "미디어", prompt, kind, MEDIA)
        db.update_task(conn, tid, job_id=job_id)
    return db.get_job(conn, job_id)


async def test_image_via_cli_writes_artifact(tmp_env, monkeypatch):
    conn = db.get_conn(config.DB_PATH)
    job = _media_job(conn, "image")

    async def fake_run_cli(cmd, job_id, timeout):
        # 에이전트가 요청받은 target 경로에 직접 저장했다고 가정
        target = cmd[-1].split("저장하세요: ")[1].split("\n")[0]
        from pathlib import Path
        Path(target).write_bytes(b"png-bytes")
        return 0, f"완료\n{target}\n", ""

    monkeypatch.setattr(media, "_run_cli", fake_run_cli)
    monkeypatch.setattr(media.shutil, "which", lambda b: f"/bin/{b}")
    await media.run_media_job(conn, job)

    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "done"
    artifact = fresh["output"].strip().splitlines()[-1]  # 마지막 줄 = 경로 계약
    assert artifact.endswith(".png")
    assert (config.ARTIFACTS_DIR / "1").exists()
    assert db.usage_counts(conn, "2000-01-01")[MEDIA]["ok"] == 1


async def test_image_recovers_from_scratch_path(tmp_env, tmp_path, monkeypatch):
    """에이전트가 다른 경로(agy 스크래치)에 저장해도 output의 경로를 찾아 복사한다."""
    conn = db.get_conn(config.DB_PATH)
    job = _media_job(conn, "image")
    scratch = tmp_path / "scratch.png"
    scratch.write_bytes(b"png")

    async def fake_run_cli(cmd, job_id, timeout):
        return 0, f"생성했습니다\n{scratch}\n", ""

    monkeypatch.setattr(media, "_run_cli", fake_run_cli)
    monkeypatch.setattr(media.shutil, "which",
                        lambda b: "/bin/agy" if b == "agy" else None)
    await media.run_media_job(conn, job)

    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "done"
    target = fresh["output"].strip().splitlines()[-1]
    from pathlib import Path
    assert Path(target).read_bytes() == b"png"


async def test_cli_fallback_to_second_binary(tmp_env, monkeypatch):
    """agy 실패 → grok으로 폴백."""
    conn = db.get_conn(config.DB_PATH)
    job = _media_job(conn, "image")
    calls = []

    async def fake_run_cli(cmd, job_id, timeout):
        calls.append(cmd[0])
        if cmd[0] == "agy":
            return 1, "", "boom"
        target = cmd[-1].split("저장하세요: ")[1].split("\n")[0]
        from pathlib import Path
        Path(target).write_bytes(b"png")
        return 0, f"{target}\n", ""

    monkeypatch.setattr(media, "_run_cli", fake_run_cli)
    monkeypatch.setattr(media.shutil, "which", lambda b: f"/bin/{b}")
    await media.run_media_job(conn, job)
    assert calls == ["agy", "grok"]
    assert db.get_job(conn, job["id"])["status"] == "done"


async def test_no_cli_fails_with_guidance(tmp_env, monkeypatch):
    conn = db.get_conn(config.DB_PATH)
    job = _media_job(conn, "image")
    monkeypatch.setattr(media.shutil, "which", lambda b: None)
    await media.run_media_job(conn, job)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert "설치되지 않음" in fresh["error"]
    assert db.usage_counts(conn, "2000-01-01")[MEDIA]["failed"] == 1


async def test_audio_without_key_fails_with_guidance(tmp_env, monkeypatch):
    conn = db.get_conn(config.DB_PATH)
    job = _media_job(conn, "audio")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    await media.run_media_job(conn, job)
    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "failed"
    assert "GEMINI_API_KEY" in fresh["error"]


async def test_audio_via_api_writes_wav(tmp_env, monkeypatch):
    conn = db.get_conn(config.DB_PATH)
    job = _media_job(conn, "audio", prompt="안녕하세요 나레이션")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    pcm = b"\x00\x01" * 100

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"candidates": [{"content": {"parts": [{
                "inlineData": {"data": base64.b64encode(pcm).decode(),
                               "mimeType": "audio/L16;rate=24000"}}]}}]}).encode()

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["key"] = req.headers.get("X-goog-api-key")
        return FakeResp()

    monkeypatch.setattr(media.urllib.request, "urlopen", fake_urlopen)
    await media.run_media_job(conn, job)

    fresh = db.get_job(conn, job["id"])
    assert fresh["status"] == "done"
    assert captured["key"] == "test-key"
    assert "key=" not in captured["url"]  # 키는 URL이 아니라 헤더로
    from pathlib import Path
    wav = Path(fresh["output"].strip().splitlines()[-1]).read_bytes()
    assert wav[:4] == b"RIFF" and pcm in wav


async def test_worker_dispatches_media_jobs(tmp_env, monkeypatch):
    """run_job이 provider=media 잡을 media.run_media_job으로 넘긴다."""
    conn = db.get_conn(config.DB_PATH)
    job_id = db.create_job(conn, "이미지", MEDIA, model="image")
    job = db.claim_next_job(conn)
    called = {}

    async def fake_media(conn_, job_):
        called["id"] = job_["id"]

    monkeypatch.setattr(media, "run_media_job", fake_media)
    await worker.run_job(conn, job, save=False)
    assert called["id"] == job_id
