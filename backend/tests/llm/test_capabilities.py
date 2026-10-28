from researchly.llm.capabilities import load_table


def test_default_table_has_role_defaults_for_keyed_providers():
    table = load_table()
    for provider in ("anthropic", "openai", "gemini"):
        assert set(table.roles[provider]) == {"strong", "mid", "cheap"}
    assert table.roles["anthropic"]["cheap"] == "anthropic/claude-haiku-5-5"
