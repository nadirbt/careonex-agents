# Task runner for this repo. Run `just` to list recipes.
# Not installed? macOS: brew install just. Others: https://just.systems/man/en/packages.html

# List all recipes
default:
    @just --list

# Build every container image
build:
    docker compose build

# Note: run builds first because the source catalog is baked into the data image,
# so a catalog edit needs a rebuild.

# Run the whole pipeline: bucket -> raw -> text -> chunks -> knowledge base
run: build
    docker compose up --abort-on-container-exit --exit-code-from kb-sync data ingest extract chunk kb-sync

# Start the retrieval API on :8080 (keeps running)
serve: build
    docker compose up retrieve

# Voice smoke test: fixture WAV -> Nova -> tool call -> reply WAV
smoke: build
    docker compose run --rm voice

# Show bucket and knowledge base status
status:
    docker compose run --rm data status
    -docker compose run --rm kb-sync status

# Print the AWS identity the containers run as (ARN must contain AC215)
whoami:
    docker compose run --rm data whoami

# Offline unit tests for every service (moto / botocore stubs, no AWS calls)
test: test-data test-extract test-chunk test-kb test-retrieve test-voice

# Offline tests for the data service
test-data:
    uv run --directory services/data pytest -q

# Offline tests for the extract service
test-extract:
    uv run --directory services/extract pytest -q

# Offline tests for the chunk service
test-chunk:
    uv run --directory services/chunk pytest -q

# Offline tests for the kb-sync service
test-kb:
    uv run --directory services/kb-sync pytest -q

# Offline tests for the retrieve service
test-retrieve:
    uv run --directory services/retrieve pytest -q

# Offline tests for the voice service
test-voice:
    uv run --directory services/voice pytest -q

# Stop and remove the compose containers
clean:
    docker compose down --remove-orphans
