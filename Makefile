.PHONY: build clean check-uv integration-test check-gh release

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

# Create a GitHub release and tag using gh. Usage: make release VERSION=0.0.3
release: check-gh
	@if [ -z "$(VERSION)" ]; then echo "Usage: make release VERSION=0.0.3" && exit 1; fi
	@# Ensure working tree is clean (no unstaged or staged changes)
	@git diff --quiet || (echo "Error: Uncommitted changes present." && exit 1)
	@git diff --cached --quiet || (echo "Error: Staged but uncommitted changes present." && exit 1)
	@TAG="v$(VERSION)"; \
		echo "Creating GitHub release $$TAG at $$(git rev-parse --short HEAD)"; \
		gh release create "$$TAG" --title "$$TAG" --generate-notes --latest --target "$(shell git rev-parse HEAD)"
