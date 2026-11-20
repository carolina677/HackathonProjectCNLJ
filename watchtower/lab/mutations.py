"""Mutation catalogue: the ways a real portal changes under us. Ground truth follows each one."""

MUTATIONS = {"rename_classes", "reshuffle_layout", "pagination_style", "date_format", "api_path_move",
             "json_keys_rename", "html_to_json", "withdraw_one", "deadline_passed", "site_down", "add_login"}
# What the "Break it" button may pick: changes a good system can repair on its own.
REPAIRABLE = ["rename_classes", "reshuffle_layout", "pagination_style", "date_format", "api_path_move",
              "json_keys_rename"]


def apply(state, slug: str, name: str) -> None:
    if name not in MUTATIONS:
        raise ValueError(f"unknown mutation {name}")
    from .app import tenders

    state.mutations.setdefault(slug, set()).add(name)
    if name == "withdraw_one":
        taken = state.withdrawn.setdefault(slug, set())
        for t in tenders(slug):
            if t.id not in taken:
                taken.add(t.id)
                break
    elif name == "deadline_passed":
        taken = state.passed.setdefault(slug, set())
        for t in reversed(tenders(slug)):
            if t.id not in taken:
                taken.add(t.id)
                break
