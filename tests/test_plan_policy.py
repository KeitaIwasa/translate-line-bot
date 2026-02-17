from src.domain.services.plan_policy import stop_translation_on_quota


def test_stop_translation_on_quota_free_only():
    assert stop_translation_on_quota("free") is True
    assert stop_translation_on_quota("standard") is False
    assert stop_translation_on_quota("pro") is False


def test_stop_translation_on_quota_unknown_treated_as_free():
    assert stop_translation_on_quota("unknown") is True
