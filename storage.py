"""Saves per-exam overrides. Backend 'local' (CSV files) or 'gsheet' (Google Sheet) - picked automatically."""
from pathlib import Path
import pandas as pd
from core import OVR_COLS, SET_COLS

LOCAL = Path(__file__).parent / "data" / "saved"
TABLES = {"overrides": ("Exam_Service_Overrides", OVR_COLS), "settings": ("Exam_Settings", SET_COLS)}


class Store:
    def __init__(self, secrets=None):
        self.secrets = secrets
        self.kind = "local"
        self._sh = None
        try:
            if secrets is not None and "gsheet" in secrets and "gcp_service_account" in secrets:
                import gspread
                gc = gspread.service_account_from_dict(dict(secrets["gcp_service_account"]))
                g = secrets["gsheet"]
                self._sh = gc.open_by_key(g["spreadsheet_key"]) if "spreadsheet_key" in g else gc.open(g["spreadsheet_name"])
                self.kind = "gsheet"
        except Exception as e:           # fall back to local, but remember why
            self.error = f"Google Sheet not available ({e}); using local files."
            self._sh = None
            self.kind = "local"
        LOCAL.mkdir(parents=True, exist_ok=True)

    # -- helpers
    def _ws(self, name, cols):
        import gspread
        try:
            return self._sh.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = self._sh.add_worksheet(title=name, rows=1000, cols=len(cols))
            ws.update([cols])
            return ws

    def load(self, table):
        name, cols = TABLES[table]
        if self.kind == "gsheet":
            vals = self._ws(name, cols).get_all_values()
            df = pd.DataFrame(vals[1:], columns=vals[0]) if len(vals) > 1 else pd.DataFrame(columns=cols)
        else:
            f = LOCAL / f"{table}.csv"
            df = pd.read_csv(f, dtype=str, keep_default_na=False) if f.exists() else pd.DataFrame(columns=cols)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df[cols].astype(str).replace({"nan": ""})

    def save(self, table, df):
        name, cols = TABLES[table]
        df = df[cols].fillna("").astype(str)
        if self.kind == "gsheet":
            ws = self._ws(name, cols)
            ws.clear()
            ws.update([cols] + df.values.tolist())
        else:
            df.to_csv(LOCAL / f"{table}.csv", index=False)
