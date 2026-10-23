"""The deployed Vercel function's pins must stay in step with the package.

frontend/api/requirements.txt pins the core runtime dependencies to the tested
versions and installs rrip at an exact commit. These checks fail if a core
dependency stops being pinned, if a pin drops below the floor rrip declares in
pyproject.toml (which would let the function install a version the code does not
support), or if the rrip line slips back to @main. No install, no network.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "frontend" / "api" / "requirements.txt"


def _canonical(name: str) -> str:
    # Strip an extras marker and normalise, so "psycopg_pool" and "psycopg-pool"
    # and "uvicorn[standard]" compare as the same project.
    base = re.split(r"[\[<>=!~ ]", name, maxsplit=1)[0]
    return base.strip().lower().replace("_", "-")


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text))


def _pyproject_core_floors() -> dict[str, tuple[int, ...]]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floors: dict[str, tuple[int, ...]] = {}
    for dep in data["project"]["dependencies"]:
        m = re.search(r">=\s*([0-9][0-9.]*)", dep)
        floors[_canonical(dep)] = _version(m.group(1)) if m else (0,)
    return floors


def _active_requirement_lines() -> list[str]:
    """Requirement specifiers only -- inline and full-line comments stripped.

    The policy checks below must read what pip will actually install, not the
    file's prose. The explanatory header legitimately contains the word "@main"
    (describing the bug that pinning fixed), so a naive substring scan of the
    whole file reports a violation that does not exist.
    """
    lines: list[str] = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        spec = raw.split("#", 1)[0].strip()
        if spec:
            lines.append(spec)
    return lines


def _requirement_pins() -> dict[str, tuple[int, ...]]:
    pins: dict[str, tuple[int, ...]] = {}
    for line in _active_requirement_lines():
        if line.startswith("rrip ") or line.startswith("rrip@"):
            continue
        m = re.match(r"(.+?)==([0-9][0-9.]*)\s*$", line)
        if m:
            pins[_canonical(m.group(1))] = _version(m.group(2))
    return pins


def test_every_core_dependency_is_pinned_for_the_function() -> None:
    missing = set(_pyproject_core_floors()) - set(_requirement_pins())
    assert not missing, f"core deps not pinned in the function requirements: {sorted(missing)}"


def test_each_pin_satisfies_its_pyproject_floor() -> None:
    floors, pins = _pyproject_core_floors(), _requirement_pins()
    below = {n: (pins[n], floors[n]) for n in floors if n in pins and pins[n] < floors[n]}
    assert not below, f"a pin is below the floor rrip declares: {below}"


def test_rrip_is_installed_at_an_immutable_commit_not_a_branch() -> None:
    """The deployed rrip must be pinned to a full commit SHA, which is immutable.

    Checked against the ACTIVE requirement line, not the whole file: a branch
    ref (`@main`) or a short SHA is mutable or ambiguous, so neither is an
    immutable pin, and a comment that merely mentions `@main` is not a policy
    violation. A full 40-hex SHA is the only ref that names one unchangeable
    commit, which is what makes each Vercel build reproducible.
    """
    rrip = [ln for ln in _active_requirement_lines()
            if ln.startswith("rrip ") or ln.startswith("rrip@")]
    assert len(rrip) == 1, f"expected exactly one active rrip requirement, got {rrip}"
    line = rrip[0]

    ref = line.rsplit("@", 1)[1].strip() if "@" in line else ""
    assert re.fullmatch(r"[0-9a-fA-F]{40}", ref), (
        f"rrip must be pinned to a full 40-char commit SHA (an immutable ref); "
        f"the git ref is {ref!r} in {line!r}")
    assert ref.lower() != "main", "rrip must not track @main"
