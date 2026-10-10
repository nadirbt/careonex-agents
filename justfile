# Task runner for this repo. Run `just` to list recipes.
# Not installed? macOS: brew install just. Others: https://just.systems/man/en/packages.html

services := "data extract chunk kb-sync retrieve voice"

default:
    @just --list

# Build every container image
build:
    docker compose build

# The whole data pipeline, one command: bucket -> raw -> text -> chunks -> knowledge base
# Builds first: the source catalog is baked into the data image, so a catalog edit needs a rebuild.
run: build
    docker compose up --abort-on-container-exit --exit-code-from kb-sync data ingest extract chunk kb-sync

# Retrieval API on :8080 (keeps running)
serve: build
    docker compose up retrieve

# Voice smoke test against the running retrieve service (starts it if needed)
smoke: build
    docker compose run --rm voice

status:
    docker compose run --rm data status
    -docker compose run --rm kb-sync status

whoami:
    docker compose run --rm data whoami

# Offline unit tests for every service (moto / botocore stubs, no AWS calls)
test: test-data test-extract test-chunk test-kb test-retrieve test-voice
test-data:
    uv run --directory services/data pytest -q
test-extract:
    uv run --directory services/extract pytest -q
test-chunk:
    uv run --directory services/chunk pytest -q
test-kb:
    uv run --directory services/kb-sync pytest -q
test-retrieve:
    uv run --directory services/retrieve pytest -q
test-voice:
    uv run --directory services/voice pytest -q

clean:
    docker compose down --remove-orphans
