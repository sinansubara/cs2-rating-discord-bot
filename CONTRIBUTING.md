# Contributing

Thanks for contributing!

## Development setup

1. Install Python 3.13 (see .python-version).
2. Create a virtual environment:

```bash
python -m venv .venv
```

3. Activate it:

```bash
# Windows
.\.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

4. Install dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

## Lint

```bash
ruff check .
```

## Run

```bash
python bot.py
```

You can also use the helper scripts or Makefile targets described in the README.
