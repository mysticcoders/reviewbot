# Development commands for ReviewBot. Run `just` to list them.

# List the recipes.
default:
    @just --list

# Install the project and its development dependencies.
sync:
    uv sync

# Run the unit tests; extra arguments go to pytest.
test *args:
    uv run pytest {{args}}

# Check the code with ruff.
lint:
    uvx ruff check src tests

# Run the linter, then the unit tests.
check: lint test

# Run reviewbot from this checkout.
run *args:
    uv run reviewbot {{args}}

# Run the live Rex test; it needs the rex CLI and a running Rex session.
test-live:
    @test -n "${REX_SESSION:-}" || { echo "REX_SESSION is not set: run this inside a Rex session" >&2; exit 1; }
    uv run pytest tests/test_rex.py -k real -rs
