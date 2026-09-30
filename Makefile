.PHONY: check test

# Documentation, Python syntax and host-side regressions. This does not boot the
# guest or build the console runtime; see CONTRIBUTING.md.
PY_TOOLS := mun $(wildcard scripts/*.py vm/*.py os/builder/*.py tests/test_*.py tools/mun-card/mun_card/*.py examples/shape/*.py services/mun-cardd/*.py services/mun-launchd/*.py)

check:
	python3 scripts/check_foundation.py
	@if command -v cc >/dev/null 2>&1; then $(MAKE) -s -C examples/mun-collect test; else echo "no C compiler: MUN Collect save tests skipped"; fi
	python3 -c 'import ast, sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]; print("Python syntax OK:", *sys.argv[1:])' $(PY_TOOLS)
	$(MAKE) test

test:
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
