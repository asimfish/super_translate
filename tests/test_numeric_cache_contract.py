from pdf_zh_translator.pdf_layout import protect_text, restore_text


def test_structure_identifiers_remain_plain_without_unprotecting_body_numbers():
    source = "Theorem 2 follows Eq. 34 using Lemma 1 after 100 iterations."
    protected, mapping = protect_text(source)

    assert "Theorem 2" in protected
    assert "Eq. 34" in protected
    assert "Lemma 1" in protected
    assert "100" not in protected
    assert "100" in mapping.values()
    assert restore_text(protected, mapping) == (source, [])


def test_numeric_cache_alias_preserves_literals_and_existing_formula_markers():
    from pdf_zh_translator.translators import resolve_protected_cache_alias

    source = "Set value ⟦1⟧ in ⟦2⟧ and apply ⟦0⟧."
    mapping = {0: "x=1", 1: "2.4", 2: "2023"}
    cache = {"Set value 2.4 in 2023 and apply ⟦0⟧.": "在2023年设数值2.4并应用⟦0⟧。"}
    before = dict(cache)

    assert resolve_protected_cache_alias(source, mapping, cache) == "在⟦2⟧年设数值⟦1⟧并应用⟦0⟧。"
    assert cache == before


def test_numeric_cache_alias_rejects_missing_changed_or_duplicated_content():
    from pdf_zh_translator.translators import resolve_protected_cache_alias

    source = "Set value ⟦1⟧ in ⟦2⟧ and apply ⟦0⟧."
    mapping = {0: "x=1", 1: "2.4", 2: "2023"}
    legacy = "Set value 2.4 in 2023 and apply ⟦0⟧."
    for translation in (
        "设数值2.4并应用⟦0⟧。",
        "在2024年设数值2.4并应用⟦0⟧。",
        "在2023年设数值2.4并应用⟦0⟧⟦0⟧。",
        "在2023年设数值2.4和2.4并应用⟦0⟧。",
    ):
        assert resolve_protected_cache_alias(source, mapping, {legacy: translation}) is None


def test_numeric_cache_alias_does_not_change_math_placeholder_indices():
    from pdf_zh_translator.translators import resolve_protected_cache_alias

    source = "Set ⟦1⟧ and use ⟦0⟧ twice: ⟦0⟧."
    mapping = {0: "x=1", 1: "100"}
    cache = {"Set 100 and use ⟦0⟧ twice: ⟦0⟧.": "设100，使用⟦0⟧两次：⟦0⟧。"}
    assert resolve_protected_cache_alias(source, mapping, cache) == "设⟦1⟧，使用⟦0⟧两次：⟦0⟧。"


def test_numeric_formula_marker_is_not_downgraded_to_a_plain_literal():
    from pdf_zh_translator.translators import resolve_protected_cache_alias

    source = "Use ⟦0⟧ after ⟦1⟧ iterations."
    mapping = {0: "2", 1: "10"}
    cache = {"Use ⟦0⟧ after 10 iterations.": "迭代10次后使用⟦0⟧。"}
    assert resolve_protected_cache_alias(
        source, mapping, cache, immutable_indices=(0,)
    ) == "迭代⟦1⟧次后使用⟦0⟧。"


def test_cache_only_numeric_alias_never_writes_or_mutates_the_source_cache(tmp_path):
    import json

    from pdf_zh_translator.translators import CacheOnlyTranslator, cache_key

    cache_file = tmp_path / "immutable.jsonl"
    legacy = "Use ⟦0⟧ after 10 iterations."
    original = json.dumps({
        "key": cache_key(legacy), "source": legacy, "translation": "迭代10次后使用⟦0⟧。"
    }, ensure_ascii=False)
    cache_file.write_text(original + "\n", encoding="utf-8")
    translator = CacheOnlyTranslator(cache_file)
    original_records = dict(translator.cache)
    source = "Use ⟦0⟧ after ⟦1⟧ iterations."
    translator.register_protected_source(source, {0: "2", 1: "10"}, immutable_indices=(0,))

    assert translator.can_translate(source)
    assert translator.translate_batch([source]) == ["迭代⟦1⟧次后使用⟦0⟧。"]
    assert translator.cache == original_records
    assert cache_file.read_text(encoding="utf-8") == original + "\n"
    assert not cache_file.with_name(cache_file.name + ".missing.jsonl").exists()


def test_numeric_cache_alias_keeps_observed_identifier_punctuation():
    from pdf_zh_translator.translators import resolve_protected_cache_alias

    assert resolve_protected_cache_alias(
        "Uniform-MCF-⟦0⟧", {0: "500"}, {"Uniform-MCF-500": "均匀最小费用流-500"}
    ) == "均匀最小费用流-⟦0⟧"
    assert resolve_protected_cache_alias(
        "C.⟦0⟧ WITHOUT CONSTRAINTS", {0: "1"}, {"C.1 WITHOUT CONSTRAINTS": "C.1 无约束"}
    ) == "C.⟦0⟧ 无约束"


def test_numeric_cache_alias_still_blocks_removed_or_added_signs():
    from pdf_zh_translator.translators import resolve_protected_cache_alias

    for source_value, target_value in (("10", "-10"), ("-10", "10"), ("10", "+10")):
        assert resolve_protected_cache_alias(
            "Set ⟦0⟧.", {0: source_value}, {f"Set {source_value}.": f"设{target_value}。"}
        ) is None


def test_failed_fixture_replay_does_not_write_into_repository(tmp_path):
    from hashlib import sha256
    from pathlib import Path

    import pytest

    from pdf_zh_translator.translators import CacheOnlyTranslator, TranslationError

    source = Path(__file__).parent / "fixtures/otf_production_acceptance_cache.jsonl"
    diagnostic = source.with_name(source.name + ".missing.jsonl")
    before_source = sha256(source.read_bytes()).hexdigest()
    before_diagnostic = sha256(diagnostic.read_bytes()).hexdigest() if diagnostic.exists() else None
    translator = CacheOnlyTranslator(source)

    with pytest.raises(TranslationError, match="missing or invalid"):
        translator.translate_batch(["Unique uncached text for read-only fixture diagnostics."])

    assert translator.cache_file.parent == tmp_path
    assert translator.cache_file.with_name(translator.cache_file.name + ".missing.jsonl").is_file()
    assert sha256(source.read_bytes()).hexdigest() == before_source
    after_diagnostic = sha256(diagnostic.read_bytes()).hexdigest() if diagnostic.exists() else None
    assert after_diagnostic == before_diagnostic
