"""Mutation catalogue (Task 14 fills in tests and truth effects)."""

MUTATIONS = {"rename_classes", "reshuffle_layout", "pagination_style", "date_format", "api_path_move",
             "json_keys_rename", "html_to_json", "withdraw_one", "deadline_passed", "site_down", "add_login"}
REPAIRABLE = ["rename_classes", "reshuffle_layout", "pagination_style", "date_format", "api_path_move",
              "json_keys_rename"]


def apply(state, slug: str, name: str) -> None:
    if name not in MUTATIONS:
        raise ValueError(f"unknown mutation {name}")
    state.mutations.setdefault(slug, set()).add(name)
