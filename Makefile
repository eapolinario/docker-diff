.PHONY: build clean

build:
	uv build .

clean:
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf __pycache__/
	rm -rf .pytest_cache/