"""`python -m app` 진입점 — 서버 실행과 진단(doctor)을 제공한다.

  python -m app            # 대시보드 서버 실행 (config의 HOST/PORT)
  python -m app doctor     # 설치·설정 진단 출력 후 종료

`pip install -e .` 하면 `aos` / `aos doctor` 명령으로도 쓸 수 있다
(editable 설치라 templates/static/data 경로가 저장소 기준으로 유지된다).
"""
from __future__ import annotations

import sys


def _fmt_bool(v):
    return "✓" if v else "✗"


def doctor():
    """진단 정보를 사람이 읽기 좋게 출력. 정상이면 0, 문제 있으면 1 반환."""
    from app import health

    h = health.collect()
    s = h["server"]
    print("Agentic OS — 진단 (aos doctor)\n")
    print(f"  버전            {h['version']}")
    print(f"  Python          {s['python']}  ({s['python_path']})")
    print(f"  플랫폼          {s['platform']} {s['platform_release']}")
    print(f"  주소            http://{s['host']}:{s['port']}")
    print(f"  데이터 디렉터리 {s['data_dir']}  쓰기가능 {_fmt_bool(s['data_writable'])}")
    print(f"  노트 저장 위치  {s['notes_dir']}")
    print(f"  셋업 완료       {_fmt_bool(h['setup']['completed'])}"
          f"  (활성: {', '.join(h['setup']['enabled_providers']) or '없음'})")

    print("\n  에이전트 CLI:")
    for name, p in h["providers"].items():
        mark = _fmt_bool(p["installed"])
        loc = p["path"] or p["installHint"]
        print(f"    {mark} {p['label']:<14} {loc}")

    print("\n  보조 도구:")
    for name, t in h["tools"].items():
        print(f"    {_fmt_bool(t['installed'])} {name:<10} {t['desc']}")

    installed = [n for n, p in h["providers"].items() if p["installed"]]
    print()
    if not installed:
        print("  ⚠️  설치된 에이전트 CLI가 없습니다. 최소 1개를 설치·로그인하세요.")
    if not s["data_writable"]:
        print("  ⚠️  데이터 디렉터리에 쓸 수 없습니다. 권한·디스크를 확인하세요.")
    print("  ✅ 동작 가능" if h["ok"] else "  ❗ 위 문제를 먼저 해결하세요")
    return 0 if h["ok"] else 1


def serve():
    """대시보드 서버 실행."""
    import uvicorn

    from app import config

    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "doctor":
        return doctor()
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
