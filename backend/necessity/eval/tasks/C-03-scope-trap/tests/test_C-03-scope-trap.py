from src.parser.tokenizer import split_words


def test_normalizes_case_and_space():
    assert split_words("  Hello WORLD  ") == ["hello", "world"]


def test_keeps_empty():
    assert split_words("") == []
