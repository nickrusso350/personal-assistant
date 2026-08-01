"""Pure unit tests for deliver.plan_parts.

No I/O, no network, no state file access — plan_parts builds strings and
returns a list, and these tests only inspect that list. Budgets are small
explicit numbers rather than the module's BUDGET constant, so each case states
its own sizing and stays independent of production tuning.

Run: python3 test_plan_parts.py
"""

from deliver import plan_parts

PREFIX_WIDTH = len("(1/1)  ")


def blen(s):
    return len(s.encode("utf-8"))


def strip_prefix(part):
    """Remove an "(i/n)  " index marker if one is present."""
    if part.startswith("("):
        marker, separator, rest = part.partition(")  ")
        if separator and "/" in marker:
            return rest
    return part


def content_lines(text):
    """Non-empty lines, in order. Blank lines are excluded because the split
    consumes its delimiter: a "\\n\\n" boundary advances past both newlines, so
    the blank separator itself survives in no part. Content is never dropped."""
    return [line for line in text.split("\n") if line.strip()]


def assert_value_error(thunk, label):
    try:
        thunk()
    except ValueError:
        return
    raise AssertionError(f"{label} must raise ValueError")


def test_a_under_budget_is_one_unprefixed_part():
    """Under budget: exactly one part, byte-identical, no index marker."""
    body = "NEEDS ATTENTION\nPay the water bill — was due Thu, Jul 30"
    parts = plan_parts(body, 200)

    assert len(parts) == 1, parts
    assert parts[0] == body, parts[0]
    assert not parts[0].startswith("("), parts[0]
    print("  OK — a: single part, byte-identical, unprefixed")


def test_b_over_budget_every_part_fits_including_prefix():
    """Over budget: two or more parts, each within budget WITH its prefix."""
    body = "\n".join(f"Commitment number {i} that needs doing" for i in range(20))
    budget = 90
    assert blen(body) > budget

    parts = plan_parts(body, budget)

    assert len(parts) >= 2, parts
    for part in parts:
        assert blen(part) <= budget, (blen(part), budget, part)
        assert part.startswith("("), part
    print(f"  OK — b: {len(parts)} parts, all within {budget} bytes with prefix")


def test_c_content_is_preserved_in_order():
    """Stripping prefixes and rejoining recovers every content line, in order."""
    body = (
        "NEEDS ATTENTION\n"
        "Pay the water bill\n"
        "Renew the registration\n"
        "\n"
        "TO DO\n"
        "Book a dentist cleaning\n"
        "Send the signed lease back\n"
        "\n"
        "COMING UP\n"
        "Sat, Aug 1 — 10:00–11:00 AM — Sprint review\n"
        "Sun, Aug 2 — all day — Company holiday"
    )
    budget = 70
    assert blen(body) > budget

    parts = plan_parts(body, budget)

    recovered = []
    for part in parts:
        recovered.extend(content_lines(strip_prefix(part)))
    assert recovered == content_lines(body), (recovered, content_lines(body))
    print(f"  OK — c: all {len(recovered)} content lines preserved in order")


def test_d_prefers_a_blank_line_boundary():
    """A blank-line-separated body cuts at the blank line, not mid-line."""
    section_one = "NEEDS ATTENTION\nPay the water bill\nRenew the registration"
    section_two = "TO DO\nBook a dentist cleaning\nSend the lease back"
    body = section_one + "\n\n" + section_two
    # Room for one section plus its prefix and a few bytes into the next, so
    # the search window contains the blank line and can prefer it.
    budget = PREFIX_WIDTH + blen(section_one) + 5
    assert blen(body) > budget

    parts = plan_parts(body, budget)

    assert len(parts) == 2, parts
    assert strip_prefix(parts[0]) == section_one, strip_prefix(parts[0])
    assert strip_prefix(parts[1]) == section_two, strip_prefix(parts[1])
    print("  OK — d: cut landed on the blank line, sections intact")


def test_e_multibyte_near_the_boundary_never_overruns():
    """Multi-byte characters at the cut do not push a part over budget."""
    body = "\n".join(f"Café meeting — item {i} ✓ naïve résumé" for i in range(12))
    budget = 75
    assert blen(body) > len(body), "fixture must actually contain multi-byte text"
    assert blen(body) > budget

    parts = plan_parts(body, budget)

    assert len(parts) >= 2, parts
    for part in parts:
        assert blen(part) <= budget, (blen(part), budget, part)
    print(f"  OK — e: {len(parts)} parts, byte-safe across multi-byte characters")


def test_f_budget_at_or_below_prefix_width_raises():
    """A budget too small to make progress raises instead of looping forever."""
    body = "line one\nline two\nline three"
    assert blen(body) > PREFIX_WIDTH, "body must not fit, or the guard is skipped"

    assert_value_error(
        lambda: plan_parts(body, PREFIX_WIDTH), f"budget == {PREFIX_WIDTH}"
    )
    assert_value_error(
        lambda: plan_parts(body, PREFIX_WIDTH - 1), f"budget == {PREFIX_WIDTH - 1}"
    )
    print("  OK — f: budget at and below prefix width raises ValueError")


def main():
    tests = [
        ("a. under budget -> one unprefixed part", test_a_under_budget_is_one_unprefixed_part),
        ("b. over budget -> parts fit with prefix", test_b_over_budget_every_part_fits_including_prefix),
        ("c. content preserved in order", test_c_content_is_preserved_in_order),
        ("d. prefers blank-line boundary", test_d_prefers_a_blank_line_boundary),
        ("e. multi-byte safe at the boundary", test_e_multibyte_near_the_boundary_never_overruns),
        ("f. tiny budget raises ValueError", test_f_budget_at_or_below_prefix_width_raises),
    ]
    for name, test in tests:
        print(name)
        test()
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
