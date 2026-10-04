.PHONY: build run serve smoke status test test-data test-extract test-chunk test-kb test-retrieve test-voice clean

SERVICES := data extract chunk kb-sync retrieve voice

# Build every container image
build:
	docker compose build

# The whole data pipeline, one command: bucket -> raw -> text -> chunks -> knowledge base
run:
	docker compose up --abort-on-container-exit --exit-code-from kb-sync data ingest extract chunk kb-sync

# Retrieval API on :8080 (keeps running)
serve:
	docker compose up retrieve

# Voice smoke test against the running retrieve service (starts it if needed)
smoke:
	docker compose run --rm voice

status:
	docker compose run --rm data status
	docker compose run --rm kb-sync status || true

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
