"""Enables ``python -m trishul_smi`` as an alternative to the
``trishul-smi`` console script entry point.
"""

from trishul_smi.cli.main import app

if __name__ == "__main__":
    app()
