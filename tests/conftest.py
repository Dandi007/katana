import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# Ensure module cache is populated with correct path
from harness.schema import load_contract, ContractError
