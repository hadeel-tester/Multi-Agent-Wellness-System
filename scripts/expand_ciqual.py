"""
One-time script to expand ciqual_cleaned.csv with 6 micronutrient columns
from the original CIQUAL 2025 Excel file.

Run from the project root:
    python scripts/expand_ciqual.py
"""

import pandas as pd
from pathlib import Path

EXCEL_PATH = Path("knowledge_base/data/ciqual/Table Ciqual 2025_ENG_2025_11_03.xlsx")
CSV_PATH = Path("data/ciqual/ciqual_cleaned.csv")

# Exact column names in the Excel file mapped to our target names
COLUMN_MAP = {
    "Iron (mg\n100g)": "iron_mg",
    "Calcium\n(mg\n100g)": "calcium_mg",
    "Vitamin\nC (mg\n100g)": "vitamin_c_mg",
    "Vitamin\nD (\ufffdg\n100g)": "vitamin_d_ug",
    "Sodium\n(mg\n100g)": "sodium_mg",
    "Magnesium\n(mg\n100g)": "magnesium_mg",
}


def main():
    print("Reading original CIQUAL Excel file...")
    xl_df = pd.read_excel(EXCEL_PATH, sheet_name="food composition")

    # Resolve actual column names — the µg symbol may be encoded differently
    actual_map = {}
    for target_col, new_name in COLUMN_MAP.items():
        matched = [c for c in xl_df.columns if new_name.split("_")[0] in c.lower().replace("µ", "\ufffd")]
        # Direct match first
        if target_col in xl_df.columns:
            actual_map[target_col] = new_name
        else:
            # Fuzzy: find by keyword
            keyword = new_name.split("_")[0]  # e.g. "iron", "calcium", "vitamin", "sodium", "magnesium"
            candidates = [c for c in xl_df.columns if keyword in c.lower()]
            if new_name == "vitamin_c_mg":
                candidates = [c for c in xl_df.columns if "vitamin" in c.lower() and "c " in c.lower()]
            elif new_name == "vitamin_d_ug":
                candidates = [c for c in xl_df.columns if "vitamin" in c.lower() and "\nd " in c.lower() and "d2" not in c.lower() and "d3" not in c.lower()]
            if candidates:
                actual_map[candidates[0]] = new_name
                print(f"  Mapped '{candidates[0]}' -> {new_name}")
            else:
                print(f"  WARNING: could not find column for {new_name}")

    micro_cols = ["alim_code"] + list(actual_map.keys())
    micro_df = xl_df[micro_cols].copy()
    micro_df = micro_df.rename(columns=actual_map)

    # CIQUAL uses European decimal commas ("1,3") and "-" for trace/unknown — normalise both
    for col in actual_map.values():
        micro_df[col] = (
            micro_df[col]
            .astype(str)
            .str.replace(",", ".", regex=False)
            .str.strip()
        )
        micro_df[col] = pd.to_numeric(micro_df[col], errors="coerce")

    print(f"\nReading cleaned CSV ({CSV_PATH})...")
    cleaned_df = pd.read_csv(CSV_PATH)
    print(f"  Rows in cleaned CSV: {len(cleaned_df)}")

    # Drop any existing micronutrient columns to avoid duplicates on re-run
    existing_micro = [c for c in actual_map.values() if c in cleaned_df.columns]
    if existing_micro:
        cleaned_df = cleaned_df.drop(columns=existing_micro)
        print(f"  Dropped existing columns: {existing_micro}")

    expanded_df = cleaned_df.merge(micro_df, on="alim_code", how="left")

    print(f"\nSummary:")
    print(f"  Total rows: {len(expanded_df)}")
    for col in actual_map.values():
        nan_count = expanded_df[col].isna().sum()
        print(f"  {col}: {nan_count} NaN ({nan_count / len(expanded_df) * 100:.1f}%)")

    expanded_df.to_csv(CSV_PATH, index=False)
    print(f"\nExpanded CSV written to {CSV_PATH}")


if __name__ == "__main__":
    main()
