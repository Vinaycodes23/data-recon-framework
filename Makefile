PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: install seed run app api test lint bench clean

install:
	python3 -m venv .venv
	$(PIP) install -q -r requirements-dev.txt

seed:
	$(PY) scripts/seed_demo.py

run:
	$(PY) -m recon run suites/demo_migration.yaml

app:
	$(PY) -m streamlit run apps/streamlit_app.py

api:
	$(PY) apps/api.py

test:
	$(PY) -m pytest --cov=recon --cov-report=term-missing

lint:
	$(PY) -m ruff check .

bench:
	$(PY) scripts/benchmark.py

clean:
	rm -rf reports demo_data benchmark_data .pytest_cache .ruff_cache .coverage
