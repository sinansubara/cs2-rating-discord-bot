.PHONY: install run run-skip

PYTHON := python3
ifneq (,$(wildcard .venv/bin/python))
PYTHON := .venv/bin/python
endif

install:
	$(PYTHON) -m pip install -r requirements.txt

run: install
	$(PYTHON) bot.py

run-skip:
	$(PYTHON) bot.py
