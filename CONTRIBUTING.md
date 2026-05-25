# Contributing

## Development setup

```bash
git clone https://github.com/FAAQJAVED/html-directory-scrapers.git
cd html-directory-scrapers
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r engines/html/requirements.txt
pip install -r engines/wordpress/requirements.txt
pip install pytest pytest-cov flake8 black mypy
```

## Running the tests

```bash
# Both engines
pytest tests/ -v

# Single engine
pytest tests/html/ -v
pytest tests/wordpress/ -v

# With coverage
pytest tests/html/ --cov=engines/html --cov-report=term-missing
```

## Code style

This project uses [black](https://black.readthedocs.io/) for formatting:

```bash
black engines/ tests/
```

## Pull request checklist

- [ ] All existing tests still pass: `pytest tests/`
- [ ] New behaviour has a test
- [ ] New public functions have Args/Returns docstrings
- [ ] Type hints added on all new function signatures
- [ ] `black` formatting applied
- [ ] CHANGELOG.md updated under the `[Unreleased]` section

## Branch naming

`fix/<short-description>` for bug fixes  
`feat/<short-description>` for new features
