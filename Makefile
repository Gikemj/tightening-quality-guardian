.PHONY: demo evaluate test serve all

demo:
	PYTHONPATH=src python3 scripts/generate_demo_data.py
	PYTHONPATH=src python3 scripts/build_demo_assets.py

evaluate:
	PYTHONPATH=src python3 scripts/evaluate.py

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v
	node --test tests/web-engine.test.mjs
	python3 scripts/audit_repository.py

serve:
	python3 -m http.server 8000 --directory docs

all: demo evaluate test
