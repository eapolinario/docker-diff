.PHONY: build clean check-uv integration-test

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