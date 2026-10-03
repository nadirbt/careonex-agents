.PHONY: build run status ingest shell test test-data clean

# Build every container image
build:
	docker compose build

# The whole pipeline, one command: ensure buckets, then ingest the catalog
run:
	docker compose up --abort-on-container-exit --exit-code-from ingest data ingest

status:
	docker compose run --rm data status

ingest:
	docker compose run --rm data ingest

whoami:
	docker compose run --rm data whoami

# Interactive shell inside the data container (same creds mount)
shell:
	docker compose run --rm --entrypoint bash data

# Offline unit tests for every service (moto, no AWS calls)
test: test-data
test-data:
	cd services/data && uv run pytest -q

clean:
	docker compose down --remove-orphans
