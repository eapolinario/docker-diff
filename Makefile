.PHONY: build clean check-uv

BUILD_DIR := build

check-uv:
	@which uv > /dev/null 2>&1 || (echo "Error: uv is not installed. Please install uv first." && exit 1)

build: check-uv
	mkdir -p $(BUILD_DIR)
	cd $(BUILD_DIR) && UV_BUILD_CACHE=.build-cache uv build -o dist ..
	mkdir -p dist
	cp $(BUILD_DIR)/dist/*.whl dist/
	rm -rf $(BUILD_DIR)

clean:
	rm -rf dist/
	rm -rf build/
	rm -rf __pycache__/
	rm -rf .pytest_cache/