from tests.scripts_loader import load_script

HEADER = "| mutation | portal | noticed | repaired | quality before | quality after | seconds | LLM USD |"


def test_render_matrix_rows_and_header():
    rows = [
        {"mutation": "rename_classes", "portal": "ssr", "noticed": "healing", "repaired": "serving",
         "quality_before": 1.0, "quality_after": 0.97, "seconds": 41.2, "llm_usd": 0.0123},
        {"mutation": "json_keys_rename", "portal": "spa", "noticed": "healing", "repaired": "failed",
         "quality_before": 0.99, "quality_after": 0.0, "seconds": 120.0, "llm_usd": 0.05},
    ]
    out = load_script("repair_matrix").render_matrix(rows).splitlines()
    assert out[0] == HEADER
    data = [line for line in out[2:] if line.startswith("|")]
    assert len(data) == 2
    assert "| 0.97 |" in data[0] and "| $0.012 |" in data[0]
