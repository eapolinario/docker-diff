.PHONY: build clean check-uv integration-test check-gh release release-dry-run

BUILD_DIR := build
TEST_VENV := .venv-integration-test

check-uv:
	@which uv > /dev/null 2>&1 || (echo "Error: uv is not installed. Please install uv first." && exit 1)

build: check-uv
	mkdir -p $(BUILD_DIR)
	cd $(BUILD_DIR) && UV_BUILD_CACHE=.build-cache uv build -o dist ..
	mkdir -p dist
	cp $(BUILD_DIR)/dist/*.whl dist/
	rm -rf $(BUILD_DIR)

integration-test: clean build check-uv
	@echo "Running integration test..."
	rm -rf $(TEST_VENV)
	uv venv $(TEST_VENV)
	. $(TEST_VENV)/bin/activate && uv pip install dist/*.whl
	. $(TEST_VENV)/bin/activate && docker-diff list images
	@echo "Integration test passed!"
	rm -rf $(TEST_VENV)

clean:
	rm -rf dist/
	rm -rf build/
	rm -rf __pycache__/
	rm -rf .pytest_cache/
	rm -rf $(TEST_VENV)

# Ensure GitHub CLI is available and authenticated
check-gh:
	@which gh > /dev/null 2>&1 || (echo "Error: gh (GitHub CLI) is not installed. Install https://cli.github.com/" && exit 1)
	@gh auth status > /dev/null 2>&1 || (echo "Error: gh is not authenticated. Run: gh auth login" && exit 1)

# Create a GitHub release and tag using gh.
# Usage:
#   - Explicit version: make release VERSION=0.0.7
#   - Bump by type:      make release BUMP=patch|minor|major  (default: patch)
# Optional:
#   - DRY_RUN=1 to only print computed version/tag without creating a release
release:
	@if [ "$(DRY_RUN)" != "1" ]; then $(MAKE) check-gh; fi
	@# Ensure working tree is clean (skip in DRY_RUN)
	@if [ "$(DRY_RUN)" != "1" ]; then git diff --quiet || (echo "Error: Uncommitted changes present." && exit 1); fi
	@if [ "$(DRY_RUN)" != "1" ]; then git diff --cached --quiet || (echo "Error: Staged but uncommitted changes present." && exit 1); fi
	@
	@# Determine VERSION if not provided, using BUMP (defaults to patch)
	@if [ -z "$(VERSION)" ]; then \
		LAST_TAG=$$(gh release view --json tagName --jq '.tagName' --latest 2>/dev/null || true); \
		if [ -z "$$LAST_TAG" ]; then LAST_TAG=$$(git tag -l 'v*' --sort=-v:refname | head -n1); fi; \
		BASE_VER=$${LAST_TAG#v}; \
		if [ -z "$$BASE_VER" ]; then BASE_VER=0.0.0; fi; \
		MAJOR=$$(echo "$$BASE_VER" | awk -F. '{print $$1+0}'); \
		MINOR=$$(echo "$$BASE_VER" | awk -F. '{print $$2+0}'); \
		PATCH=$$(echo "$$BASE_VER" | awk -F. '{print $$3+0}'); \
		BUMP_LOWER=$$(echo "$(BUMP)" | tr '[:upper:]' '[:lower:]'); \
		if [ -z "$$BUMP_LOWER" ]; then BUMP_LOWER=patch; fi; \
		case "$$BUMP_LOWER" in \
		  major) MAJOR=$$((MAJOR+1)); MINOR=0; PATCH=0 ;; \
		  minor) MINOR=$$((MINOR+1)); PATCH=0 ;; \
		  patch) PATCH=$$((PATCH+1)) ;; \
		  *) echo "Invalid BUMP: $(BUMP). Use patch|minor|major"; exit 1 ;; \
		esac; \
		VERSION=$${MAJOR}.$${MINOR}.$${PATCH}; \
		echo "Computed next version from $${LAST_TAG:-none} with BUMP=$$BUMP_LOWER => $$VERSION"; \
	fi; \
	TAG=v$$VERSION; \
	echo "Release tag: $$TAG"; \
	echo "Commit: $$(git rev-parse --short HEAD)"; \
	if [ "$(DRY_RUN)" = "1" ]; then \
		echo "[DRY RUN] Would create GitHub release $$TAG"; \
	else \
		echo "Creating GitHub release $$TAG"; \
		gh release create "$$TAG" --title "$$TAG" --generate-notes --latest --target "$(shell git rev-parse HEAD)"; \
	fi

# Dry run helper: computes the next version/tag without creating a release
release-dry-run:
	@$(MAKE) --no-print-directory release DRY_RUN=1 BUMP=$(BUMP) VERSION=$(VERSION)
