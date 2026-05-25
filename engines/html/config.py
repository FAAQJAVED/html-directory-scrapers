"""
config.py
=========
Loads and validates the YAML configuration file for the HTML Directory Scraper.

Responsibilities:
  - Reading and parsing config.yaml via PyYAML
  - Validating that all required top-level keys are present
  - Injecting runtime secrets from environment variables so they never
    need to be stored in config.yaml

Supported environment variable overrides:
  SCRAPER_COOKIES → config["cookies_raw"]   (session cookie string)
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except ImportError as exc:
    raise ImportError("PyYAML is required. Install with: pip install pyyaml") from exc

log = logging.getLogger(__name__)

_REQUIRED_TOP_LEVEL = {"base_url", "list_path", "categories", "selectors"}


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """
    Load, validate, and return the configuration dictionary.

    Reads the YAML file at *config_path*, checks that every required
    top-level key is present, then applies any environment variable
    overrides before returning.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Validated configuration dictionary.

    Raises:
        FileNotFoundError: Config file does not exist at the given path.
        ValueError:        Required top-level keys are missing from the file.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Copy config.yaml.example → config.yaml and fill in your values."
        )

    with open(path, "r", encoding="utf-8") as f:
        config: Dict[str, Any] = yaml.safe_load(f)

    if config is None:
        raise ValueError(
            f"The configuration file at {path.absolute()} is empty.\n"
            "Ensure you have copied config.yaml.example to config.yaml and filled in your values."
        )

    if not isinstance(config, dict):
        raise ValueError(
            f"The configuration file at {path.absolute()} must be a YAML mapping (key: value pairs)."
        )

    missing = _REQUIRED_TOP_LEVEL - set(config.keys())
    if missing:
        raise ValueError(
            f"config.yaml is missing required top-level keys: {missing}\n"
            "Copy config.yaml.example → config.yaml and fill in your values."
        )

    _apply_env_overrides(config)
    log.info("Configuration loaded from: %s", path)
    return config


def _apply_env_overrides(config: Dict[str, Any]) -> None:
    """
    Inject environment variable values into the config dict in-place.

    Environment variables take precedence over values in config.yaml so
    that secrets never need to be committed to version control.

    Args:
        config: Mutable config dict (modified in-place).
    """
    cookies = os.environ.get("SCRAPER_COOKIES", "")
    if cookies:
        config["cookies_raw"] = cookies
        log.debug("SCRAPER_COOKIES injected from environment.")
