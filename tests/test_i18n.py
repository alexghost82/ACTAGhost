from acta.i18n import detect_language, is_rtl, respond_in_directive, t


def test_detect_russian():
    assert detect_language("Привет, как дела?") == "ru"


def test_detect_hebrew():
    assert detect_language("שלום, מה שלומך?") == "he"


def test_detect_english():
    assert detect_language("Hello, how are you?") == "en"


def test_detect_empty_uses_default():
    assert detect_language("", default="en") == "en"


def test_rtl_only_hebrew():
    assert is_rtl("he") is True
    assert is_rtl("ru") is False
    assert is_rtl("en") is False


def test_translations_present():
    for lang in ("ru", "he", "en"):
        assert t("how_i_got_here", lang)
        assert lang_name_in(respond_in_directive(lang))


def lang_name_in(text: str) -> bool:
    return any(name in text for name in ("Russian", "Hebrew", "English"))
