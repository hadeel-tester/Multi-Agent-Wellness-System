# Source: Anses. 2025. Ciqual French food composition table. https://ciqual.anses.fr/

from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent.parent.parent
XLSX_PATH = ROOT / "knowledge_base" / "data" / "ciqual" / "Table Ciqual 2025_ENG_2025_11_03.xlsx"
CSV_OUT = Path(__file__).parent / "ciqual_cleaned.csv"


def clean_header(col: str) -> str:
    return col.replace("\n", " ").strip()


def find_column(df: pd.DataFrame, keywords: list[str], required: bool = True) -> str | None:
    """Find a column by case-insensitive substring matching. First keyword wins."""
    cols = df.columns.tolist()
    for kw in keywords:
        matches = [c for c in cols if kw.lower() in c.lower()]
        if matches:
            return matches[0]
    if required:
        raise ValueError(
            f"Could not find column matching any of: {keywords}\n"
            f"Available columns:\n" + "\n".join(f"  {c!r}" for c in cols)
        )
    return None


def parse_value(val) -> float:
    """
    Convert CIQUAL nutritional value strings to float.

    Rules:
      "-"           → NaN  (missing; do NOT treat as zero)
      "traces"      → 0.0
      "< 0,5" etc.  → numeric part after "<"
      "9,5"         → 9.5  (European decimal comma → period)
    """
    if pd.isna(val):
        return np.nan
    s = str(val).strip()
    if s in ("-", ""):
        return np.nan
    if s.lower() == "traces":
        return 0.0
    if s.startswith("<"):
        s = s[1:].strip()
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


def main():
    print(f"Reading: {XLSX_PATH}")
    df_raw = pd.read_excel(XLSX_PATH, sheet_name="food composition", dtype=str)

    # Clean all column headers (remove embedded newlines)
    df_raw.columns = [clean_header(c) for c in df_raw.columns]

    print(f"\nAll columns after header cleanup ({len(df_raw.columns)} total):")
    for i, c in enumerate(df_raw.columns):
        print(f"  [{i:>2}] {c!r}")

    # --- Locate target columns ---
    col_code = find_column(df_raw, ["alim_code"])
    col_name = find_column(df_raw, ["alim_nom_eng"])
    col_group = find_column(df_raw, ["alim_grp_nom_eng"])
    # Energy: EU Regulation 1169/2011 kcal version (not kJ) — must match kcal specifically
    col_kcal = find_column(df_raw, ["1169 2011 (kcal", "Regulation EU No 1169", "kcal 100g"])
    # Protein: N×Jones factor version (prefer over N×6.25)
    col_protein = find_column(df_raw, ["Protein (g", "protein (g"])
    col_carbs = find_column(df_raw, ["Carbohydrate (g", "carbohydrate (g"])
    col_fat = find_column(df_raw, ["Fat (g", "fat (g"])
    col_fiber = find_column(df_raw, ["Fibres (g", "fibres (g", "Fiber (g", "fiber (g"])

    print(f"\nMapped columns:")
    print(f"  alim_code         = {col_code!r}")
    print(f"  food_name_en      = {col_name!r}")
    print(f"  food_group        = {col_group!r}")
    print(f"  calories_per_100g = {col_kcal!r}")
    print(f"  protein_g         = {col_protein!r}")
    print(f"  carbs_g           = {col_carbs!r}")
    print(f"  fat_g             = {col_fat!r}")
    print(f"  fiber_g           = {col_fiber!r}")

    # --- Extract and rename ---
    df = df_raw[[col_code, col_name, col_group, col_kcal, col_protein, col_carbs, col_fat, col_fiber]].copy()
    df.columns = ["alim_code", "food_name_en", "food_group",
                  "calories_per_100g", "protein_g", "carbs_g", "fat_g", "fiber_g"]

    # Drop rows with no food name
    df = df.dropna(subset=["food_name_en"])
    df = df[df["food_name_en"].str.strip() != ""]

    # Parse nutritional values
    for col in ["calories_per_100g", "protein_g", "carbs_g", "fat_g", "fiber_g"]:
        df[col] = df[col].apply(parse_value)

    # Save
    df.to_csv(CSV_OUT, index=False)
    print(f"\nSaved {len(df):,} rows to: {CSV_OUT}")

    # Summary
    print(f"\nColumns: {list(df.columns)}")
    print(f"\nNaN counts per column:")
    print(df.isnull().sum().to_string())
    print(f"\nSample rows:")
    print(df.head(5).to_string())


if __name__ == "__main__":
    main()
