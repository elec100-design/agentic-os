import ast
import collections
from pathlib import Path

from fastapi.testclient import TestClient

from app import i18n


def _client(tmp_env):
    from app.main import app
    return TestClient(app)


def test_resolve_lang_priority():
    assert i18n.resolve_lang(cookie="ko") == "ko"
    assert i18n.resolve_lang(cookie="en") == "en"
    assert i18n.resolve_lang(cookie=None, accept_language="ko-KR,ko;q=0.9") == "ko"
    assert i18n.resolve_lang(cookie=None, accept_language="en-US,en;q=0.9") == "en"
    assert i18n.resolve_lang(cookie="zzz", accept_language=None) == "en"  # 기본 영어
    # 쿠키가 Accept-Language보다 우선
    assert i18n.resolve_lang(cookie="en", accept_language="ko") == "en"


def test_t_translates_and_falls_back():
    i18n.set_lang("en")
    assert i18n.t("메모리") == "Memory"
    assert i18n.t("존재하지 않는 문자열") == "존재하지 않는 문자열"  # 폴백=원문
    i18n.set_lang("ko")
    assert i18n.t("메모리") == "메모리"


def test_index_default_english(tmp_env):
    from app import settings
    settings.save(["claude", "hermes"])
    with _client(tmp_env) as client:
        r = client.get("/")           # 쿠키·Accept-Language 없음 → 영어 기본
        assert r.status_code == 200
        assert "What can I help with?" in r.text
        assert 'lang="en"' in r.text
        assert "무엇을 도와드릴까요?" not in r.text


def test_index_korean_via_cookie(tmp_env):
    from app import settings
    settings.save(["claude", "hermes"])
    with _client(tmp_env) as client:
        client.cookies.set("aos-lang", "ko")
        r = client.get("/")
        assert "무엇을 도와드릴까요?" in r.text
        assert 'lang="ko"' in r.text


def test_lang_route_sets_cookie(tmp_env):
    with _client(tmp_env) as client:
        r = client.get("/lang/ko", headers={"referer": "http://x/"},
                       follow_redirects=False)
        assert r.status_code == 303
        assert "aos-lang=ko" in r.headers.get("set-cookie", "")
        # 잘못된 코드는 기본(en)으로
        r2 = client.get("/lang/zz", follow_redirects=False)
        assert "aos-lang=en" in r2.headers.get("set-cookie", "")


def test_en_catalog_has_no_duplicate_keys():
    # 파이썬 dict 리터럴은 중복 키를 조용히 덮어써서 뒤에 나온 값만 남는다.
    # i18n.EN 자체를 봐서는 중복을 알 수 없으므로 소스를 파싱해 확인한다.
    src = Path(i18n.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    en_dict = next(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "EN" for t in node.targets)
    )
    keys = [k.value for k in en_dict.keys]
    dups = [k for k, c in collections.Counter(keys).items() if c > 1]
    assert dups == []


def test_accept_language_korean(tmp_env):
    from app import settings
    settings.save(["claude", "hermes"])
    with _client(tmp_env) as client:
        r = client.get("/", headers={"accept-language": "ko-KR,ko;q=0.9"})
        assert "무엇을 도와드릴까요?" in r.text
