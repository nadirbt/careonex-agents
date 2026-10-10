# This project uses `just`, not make. This stub only points people at it.
.DEFAULT_GOAL := use-just
.PHONY: use-just
use-just %:
	@echo "This repo uses just instead of make."
	@command -v just >/dev/null 2>&1 || echo "Install it first: brew install just  (other platforms: https://just.systems/man/en/packages.html)"
	@echo "Then run: just $(if $(filter-out use-just,$(MAKECMDGOALS)),$(filter-out use-just,$(MAKECMDGOALS)),   # lists all commands)"
	@exit 1
