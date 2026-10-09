"""한/영 전환 실수 되돌리기 — 쿠팡 서브 ID 'hot' 이 'ㅗㅐㅅ' 로 들어간 일 (10/10)."""
from dealbot.app import _ascii_sub_id
from dealbot.keyboard import from_korean_keyboard, is_ascii


def test_jamo_and_syllables_back_to_english_keys():
    assert from_korean_keyboard("ㅗㅐㅅ") == "hot"
    assert from_korean_keyboard("핫") == "gkt"          # 완성형도 자모로 풀어서 (ㅎ ㅏ ㅅ)
    assert from_korean_keyboard("ㅗㅐㅅ_ㅇㄷ미") == "hot_deal"
    assert from_korean_keyboard("blog01") == "blog01"


def test_sub_id_is_fixed_only_when_not_ascii():
    assert _ascii_sub_id("ㅗㅐㅅ") == "hot"
    assert _ascii_sub_id("hot") == "hot" and _ascii_sub_id(None) is None
    assert is_ascii("hot") and not is_ascii("ㅗㅐㅅ")
