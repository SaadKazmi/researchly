from pathlib import Path

import pytest

from repo_checks import (
    check_agents,
    check_features,
    check_line_budget,
    main,
    parse_frontmatter,
)


def feature(**overrides):
    base = {
        "id": "F1.1",
        "title": "Projects and threads",
        "build_step": 3,
        "spec_ref": "§3 F1.1",
        "acceptance": "Projects hold threads.",
        "e2e": [],
        "passes": False,
    }
    base.update(overrides)
    return base


def test_valid_features_have_no_errors():
    assert check_features({"features": [feature()]}) == []


def test_empty_features_rejected():
    assert check_features({"features": []}) == ["features must be a non-empty list"]


def test_missing_key_rejected():
    f = feature()
    del f["acceptance"]
    errors = check_features({"features": [f]})
    assert len(errors) == 1 and errors[0].startswith("F1.1: keys must be")


def test_duplicate_id_rejected():
    errors = check_features({"features": [feature(), feature()]})
    assert "F1.1: duplicate id" in errors


def test_bad_id_rejected():
    assert "F1: bad id" in check_features({"features": [feature(id="F1")]})


def test_passes_requires_e2e():
    errors = check_features({"features": [feature(passes=True)]})
    assert "F1.1: passes=true requires at least one e2e spec" in errors


def test_passes_with_e2e_ok():
    data = {"features": [feature(passes=True, e2e=["e2e/specs/F1.1.spec.ts"])]}
    assert check_features(data) == []


def test_passes_must_be_bool():
    assert "F1.1: passes must be a boolean" in check_features({"features": [feature(passes="yes")]})


def test_build_step_range():
    assert "F1.1: build_step must be 1..10" in check_features({"features": [feature(build_step=11)]})


def test_parse_frontmatter_crlf_and_lf():
    text = "---\r\nname: architect\r\nmodel: opus\r\n---\r\nbody\r\n"
    assert parse_frontmatter(text) == {"name": "architect", "model": "opus"}
    assert parse_frontmatter("no frontmatter") == {}


def test_check_agents(tmp_path: Path):
    (tmp_path / "good.md").write_text(
        "---\nname: good\ndescription: does things\nmodel: sonnet\n---\nbody\n", encoding="utf-8"
    )
    (tmp_path / "bad.md").write_text("---\nname: bad\n---\nbody\n", encoding="utf-8")
    (tmp_path / "fable.md").write_text(
        "---\nname: fable\ndescription: x\nmodel: fable\n---\n", encoding="utf-8"
    )
    errors = check_agents(tmp_path)
    assert "bad.md: missing description" in errors
    assert "bad.md: missing model" in errors
    assert "fable.md: model must be one of haiku, inherit, opus, sonnet" in errors
    assert not any(e.startswith("good.md") for e in errors)


def test_line_budget(tmp_path: Path):
    p = tmp_path / "AGENTS.md"
    p.write_text("x\n" * 201, encoding="utf-8")
    assert check_line_budget(p) == ["AGENTS.md: 201 lines exceeds budget of 200"]
    p.write_text("x\n" * 200, encoding="utf-8")
    assert check_line_budget(p) == []


def test_main_on_minimal_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "features.json").write_text('{"features": []}', encoding="utf-8")
    assert main(tmp_path) == 1
    assert "features must be a non-empty list" in capsys.readouterr().out


def test_non_object_data_rejected():
    assert check_features([]) == ["features.json must be a JSON object"]
    assert check_features(None) == ["features.json must be a JSON object"]


def test_non_string_id_reported_not_raised():
    errors = check_features({"features": [feature(id=7)]})
    assert "<non-string id>: id must be a string" in errors


def test_build_step_bool_rejected():
    assert "F1.1: build_step must be 1..10" in check_features({"features": [feature(build_step=True)]})


def test_passes_with_blank_e2e_rejected():
    errors = check_features({"features": [feature(passes=True, e2e=[""])]})
    assert "F1.1: passes=true requires at least one e2e spec" in errors


def test_blank_e2e_entry_reported():
    errors = check_features({"features": [feature(e2e=["", "x"])]})
    assert "F1.1: e2e entries must be non-empty strings" in errors
