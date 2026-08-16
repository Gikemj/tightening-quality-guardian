# The package uses Python 3.10+ type syntax. Override, e.g. `make PYTHON=python3.11 all`.
PYTHON ?= python3.12

.PHONY: build demo evaluate evaluate-scenarios test serve all

# `make -j all` must never audit while generated artifacts are still changing.
.NOTPARALLEL: all

build:
	PYTHONPATH=src $(PYTHON) scripts/build_all.py

demo:
	PYTHONPATH=src $(PYTHON) scripts/generate_demo_data.py
	PYTHONPATH=src $(PYTHON) scripts/build_demo_assets.py

evaluate:
	PYTHONPATH=src $(PYTHON) scripts/evaluate.py

evaluate-scenarios:
	PYTHONPATH=src $(PYTHON) scripts/evaluate_scenarios.py

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v
	node --test tests/web-engine.test.mjs
	node --check docs/risk-engine.js
	node --check docs/app.js
	node --check docs/round2.js
	PYTHONPATH=src $(PYTHON) scripts/audit_repository.py

serve:
	$(PYTHON) -m http.server 8000 --bind 127.0.0.1 --directory docs

all:
	$(MAKE) build
	$(MAKE) test
