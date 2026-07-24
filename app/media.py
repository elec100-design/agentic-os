"""미디어 생성 레이어 — 비전 보드의 image/video/audio 태스크를 처리한다.

2026-07-25 스파이크 실측 결과 (docs/PROVIDERS.md 부록 참조):
- 이미지: agy(`generate_image`)·grok(`image_gen`) 모두 CLI 구독 로그인으로 생성 가능
- 비디오: grok만 가능(`image_to_video`/`reference_to_video`)
- 오디오: 두 CLI 모두 불가 → Gemini API TTS 폴백 (GEMINI_API_KEY 필요)

경로 우선순위: CLI(구독, 추가 과금 없음) → API(옵트인 키). API 키는 인프로세스
HTTP 호출에서만 읽는다 — worker._clean_env가 자식 프로세스에서 키를 제거하는
기존 원칙(CLI는 항상 구독 로그인 사용)은 그대로 유지된다.

출력 계약: 성공한 미디어 잡의 output '마지막 줄'은 산출물의 절대 경로다.
orchestrator._sync_tasks가 이 줄을 task.artifact_path로 회수한다.

알려진 제약(v1): 워커는 provider 이름("media") 기준으로 직렬화하므로, media
잡이 내부에서 쓰는 agy/grok CLI가 같은 CLI의 텍스트 잡과 동시에 돌 수 있다.
두 CLI 모두 stateless 단발 실행이라 정합성 문제는 없고 사용량 계측이 겹칠 수
있는 정도다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import struct
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app import config, db
from app.providers import MEDIA  # noqa: F401  (worker가 media.MEDIA로 참조)

_EXT = {"image": "png", "video": "mp4", "audio": "wav"}
_KIND_KO = {"image": "이미지", "video": "비디오", "audio": "오디오"}
# 종류별로 시도할 CLI 순서 (스파이크로 검증된 능력만)
_CLI_ORDER = {"image": ("agy", "grok"), "video": ("grok",), "audio": ()}

_GEN_PROMPT = (
    "{prompt}\n\n"
    "위 내용의 {kind_ko}를 생성해서 정확히 다음 경로에 파일로 저장하세요: {target}\n"
    "다른 경로에 저장했다면 그 파일을 위 경로로 복사하세요. "
    "완료 후 저장된 파일의 절대 경로만 마지막 줄에 단독으로 출력하세요."
)


def _cli_command(binary, prompt, timeout):
    if binary == "agy":
        # 헤드리스 파일 쓰기에 권한 스킵이 필요하다 (스파이크 실측)
        return ["agy", "--dangerously-skip-permissions",
                "--print-timeout", f"{timeout}s", "-p", prompt]
    return ["grok", "--always-approve", "-p", prompt]


def artifact_target(conn, job, kind):
    """산출물 저장 경로 — data/artifacts/{project_id}/{seq}_{kind}_{job_id}.{ext}"""
    task = db.task_by_job(conn, job["id"])
    project_id = task["project_id"] if task else 0
    seq = task["seq"] if task else job["id"]
    d = config.ARTIFACTS_DIR / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{seq}_{kind}_{job['id']}.{_EXT[kind]}"


async def _run_cli(cmd, job_id, timeout):
    """CLI 1회 실행. (returncode, stdout, stderr). 취소를 위해 running_procs 등록."""
    from app import worker  # 순환 임포트 방지
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=worker._clean_env(),
    )
    worker.running_procs[job_id] = proc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "timeout"
    finally:
        worker.running_procs.pop(job_id, None)
    return (proc.returncode,
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"))


_PATH_RE = re.compile(r"(/[^\s'\"]+\.(?:png|jpg|jpeg|webp|mp4|mov|webm|wav|mp3))",
                      re.I)


def _recover_artifact(stdout, target):
    """에이전트가 다른 경로에 저장했으면(예: agy 스크래치) 찾아서 target으로 복사."""
    if target.is_file():
        return True
    for line in reversed(stdout.strip().splitlines()):
        m = _PATH_RE.search(line)
        if m and Path(m.group(1)).is_file():
            shutil.copyfile(m.group(1), target)
            return True
    return False


async def _generate_via_cli(conn, job, kind, target, timeout):
    """가용한 CLI를 순서대로 시도. (ok, log) 반환."""
    logs = []
    prompt = _GEN_PROMPT.format(prompt=job["prompt"], kind_ko=_KIND_KO[kind],
                                target=target)
    for binary in _CLI_ORDER[kind]:
        if not shutil.which(binary):
            logs.append(f"- {binary}: 설치되지 않음")
            continue
        rc, stdout, stderr = await _run_cli(
            _cli_command(binary, prompt, timeout), job["id"], timeout)
        if rc == 0 and _recover_artifact(stdout, target):
            logs.append(f"- ✅ {binary}: 생성 완료")
            return True, "\n".join(logs)
        reason = "timeout" if stderr == "timeout" else (
            "산출물 파일 없음" if rc == 0 else (stderr or f"exit {rc}")[-300:])
        logs.append(f"- ❌ {binary}: {reason}")
    return False, "\n".join(logs)


# --- Gemini API 폴백 (오디오/TTS 전용 — CLI가 오디오를 지원하지 않음) ---------

def _pcm_to_wav(pcm, rate=24000):
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE",
                         b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16,
                         b"data", len(pcm))
    return header + pcm


def _tts_api(prompt, target, api_key, timeout):
    """Gemini TTS REST 호출 → WAV 저장. 동기 함수(스레드에서 실행)."""
    model = config.MEDIA_TTS_MODEL
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": "Kore"}}},
        },
    }).encode()
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent",
        data=body,
        # 키는 URL이 아니라 헤더로 전달한다(로그·프록시 노출 방지)
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
    pcm = base64.b64decode(part["data"])
    rate_m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
    target.write_bytes(_pcm_to_wav(pcm, int(rate_m.group(1)) if rate_m else 24000))


async def _generate_audio(conn, job, target, timeout):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return False, ("오디오 생성은 Gemini API가 필요합니다 — aos.env에 "
                       "GEMINI_API_KEY를 추가하세요 (CLI는 오디오 생성 미지원)")
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_tts_api, job["prompt"], target, api_key, timeout),
            timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
            json.JSONDecodeError, asyncio.TimeoutError) as e:
        return False, f"Gemini TTS 실패 (model={config.MEDIA_TTS_MODEL}): {e!r}"
    return True, f"- ✅ Gemini TTS ({config.MEDIA_TTS_MODEL})"


async def run_media_job(conn, job):
    """미디어 잡 실행 — run_job/run_council과 같은 계약(상태 전이·usage_log 책임)."""
    start = datetime.now(timezone.utc)
    kind = job["model"] if job["model"] in _EXT else "image"
    timeout = job["timeout_sec"] or config.MEDIA_TIMEOUT_SEC
    target = artifact_target(conn, job, kind)

    if kind == "audio":
        ok, log = await _generate_audio(conn, job, target, timeout)
    else:
        ok, log = await _generate_via_cli(conn, job, kind, target, timeout)

    duration = (datetime.now(timezone.utc) - start).total_seconds()

    # 사용자가 취소한 작업은 결과를 덮어쓰지 않는다 (run_job과 동일)
    fresh = db.get_job(conn, job["id"])
    if fresh["status"] == "failed" and fresh["error"] == "cancelled":
        return

    if not ok:
        db.update_job(conn, job["id"], status="failed",
                      error=f"{_KIND_KO[kind]} 생성 실패:\n{log}",
                      finished_at=db.now_iso())
        db.log_usage(conn, MEDIA, duration, "failed", job["id"])
        return

    # 출력 계약: 마지막 줄 = 산출물 절대 경로
    db.update_job(conn, job["id"], status="done",
                  output=f"{log}\n{target}", finished_at=db.now_iso())
    db.log_usage(conn, MEDIA, duration, "ok", job["id"])
