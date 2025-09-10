# docker-diff Agent Guidelines

## Build/Lint/Test Commands
- **Build:** `make build` (uses uv build system)
- **Clean:** `make clean` (removes dist/, build/, cache dirs)
- **Integration Test:** `make integration-test` (builds wheel, tests in clean venv)
- **Install:** `uv add --group dev <package>` (add dev dependencies)
- **Run CLI:** `python -m docker_diff_pkg.cli <command>` or `docker-diff <command>` (after install)

## Architecture & Structure
- **Package:** `docker_diff_pkg/` - Python package for Docker image comparison
- **Main module:** `docker_diff_pkg/cli.py` - CLI tool implementation
- **Database:** SQLite database (`docker_images.db`) with schema defined in `docker_diff_pkg/schema.sql`
- **Core class:** `DockerImageDB` - Handles all database operations and Docker image scanning
- **Database tables:** images, files, comparisons, comparison_images, file_differences, comparison_stats
- **Views:** unique_files, common_files, image_sizes for common queries
- **CLI commands:** scan, compare, list, summary, unique

## Code Style & Conventions
- **Python 3.10+** with type hints (`typing` module)
- **Database:** SQLite3 with proper foreign keys and indexes
- **Error handling:** subprocess.SubprocessError for Docker commands
- **Naming:** snake_case for functions/variables, PascalCase for classes
- **Imports:** stdlib first, then third-party (currently no external deps)
- **CLI:** argparse with subcommands, each has dedicated `_cmd_*` function
- **DB pattern:** Context managers (`with sqlite3.connect()`) for all DB operations
