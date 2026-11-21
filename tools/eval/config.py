"""Seeds, sizes, split ratios, family quotas, mixes, profiles and paths for the eval corpus (P1 plan 3.1, 3.3, 6.12).

GT-side module: no import from ``app``. All randomness elsewhere derives from ``seed_for``.
"""

from __future__ import annotations

import hashlib
import pathlib

DEVTEST_SEED = 20261119
HELDOUT_SEED = 61700529
GENERATOR_VERSION = "1"

SPLIT_SIZES = {"dev": 240, "test": 80, "heldout": 80}
DEVTEST_TOTAL = SPLIT_SIZES["dev"] + SPLIT_SIZES["test"]
DEV_FRACTION_OF_DEVTEST = 0.75

TEMPLATE_FAMILIES = ("IN-A", "IN-B", "IN-C", "IN-D", "IN-E", "US-A", "UK-A", "GEN-CSV")
GENERIC_FAMILIES = tuple(f"G{i:02d}" for i in range(1, 11))
SHARED_FAMILIES = TEMPLATE_FAMILIES + GENERIC_FAMILIES
HELDOUT_ONLY_FAMILIES = ("HX-1", "HX-2", "HX-3")  # designed in T08; ids provisional
HELDOUT_REPORTS_PER_ONLY_FAMILY = 8

PURPOSES = ("plan", "patient", "scenario", "values", "layout", "names", "scan", "photo", "rot90")

# scenario classes, shares of each split (section 3.1)
SCENARIO_SHARES = {"normal": 0.30, "mild": 0.25, "severe": 0.20, "critical": 0.25}

# input demographic completeness (section 3.3)
DEMOGRAPHIC_SHARES = {"both": 0.82, "no_sex": 0.06, "no_age": 0.06, "neither": 0.06}
# true age groups among age-known reports
AGE_GROUP_SHARES = {"paediatric": 0.13, "adult": 0.63, "elderly": 0.24}
AGE_RANGES = {"paediatric": (2, 17), "adult": (18, 64), "elderly": (65, 95)}
AGE_UNKNOWN_RANGE = (18, 80)  # true age of age-unknown reports (adults)

PAGE_SHARES = {1: 0.45, 2: 0.40, 3: 0.15}
ROT90_FRACTION = 0.05

# panels a paediatric report may contain
PAEDIATRIC_PANELS = ("CBC", "ELEC", "KFT", "LFT", "THYROID", "URINE", "SEROLOGY", "DIABETES")

# Fictional name lists: no deny-list tokens (section 3.9)
FIRST_NAMES_F = ("Anaya", "Meera", "Kavya", "Sunita", "Priya", "Neha", "Farida", "Ishita", "Lakshmi", "Rhea",
                 "Eleanor", "Margaret", "Priscilla", "Harriet", "Tamsin")
FIRST_NAMES_M = ("Arjun", "Rohan", "Vikram", "Imran", "Sandeep", "Karthik", "Devraj", "Nikhil", "Omar", "Tarun",
                 "Gerald", "Humphrey", "Desmond", "Leonard", "Rupert")
SURNAMES = ("Verma", "Nair", "Iyer", "Rao", "Menon", "Kulkarni", "Bose", "Sen", "Das", "Mehta", "Shah", "Patel",
            "Singh", "Khan", "Reddy", "Gupta", "Joshi", "Pillai", "Chopra", "Bhatt", "Hargreaves", "Whitlock",
            "Ellery", "Pemberton", "Calloway", "Thornton")

# Profiles (section 6.12): fractions of reports per rendering
PROFILES = {
    "ci": {"splits": ("test",), "native": 1.0, "csv": 1.0, "scan": 0.25, "photo": 0.25, "rot90": 1.0},
    "dev": {"splits": ("dev",), "native": 1.0, "csv": 1.0, "scan": 0.25, "photo": 0.25, "rot90": 1.0},
    "full": {"splits": ("dev", "test", "heldout"), "native": 1.0, "csv": 1.0, "scan": 1.0, "photo": 1.0, "rot90": 1.0},
    "heldout": {"splits": ("heldout",), "native": 1.0, "csv": 1.0, "scan": 1.0, "photo": 1.0, "rot90": 1.0},
    "smoke": {"splits": ("test",), "native": 1.0, "csv": 1.0, "scan": 0.0, "photo": 0.0, "rot90": 0.0, "first": 6},
}

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval"
CORPUS_DIR = EVAL_DIR / "corpus"


def master_seed(split: str) -> int:
    return HELDOUT_SEED if split == "heldout" else DEVTEST_SEED


def seed_for(master: int, split: str, index: int, purpose: str) -> int:
    """Per-purpose seed: first 8 bytes of SHA-256('vaidya-eval|master|split|index|purpose') as an unsigned int."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown seed purpose {purpose!r}")
    digest = hashlib.sha256(f"vaidya-eval|{master}|{split}|{index}|{purpose}".encode()).digest()
    return int.from_bytes(digest[:8], "big")
