"""Pure calculation logic (no Streamlit) so it can be tested and reused."""
import json, math, re
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"
CAT_SERVICES = ["CCTV", "Biometric", "Frisking", "BAGGAGEMGMT"]
ALL_SERVICES = CAT_SERVICES + ["VOIP"]
RAW_TO_SERVICE = {"CCTV": "CCTV", "BIO": "Biometric", "FRI": "Frisking", "VOIP": "VOIP"}
SCALES = ["Candidates", "Centers", "Cameras", "Operators", "Guards", "Fixed"]
OVR_COLS = ["request_id", "service", "type", "item", "include", "qty", "rate"]
SET_COLS = ["request_id", "services", "rev_override", "notes"]


CAT_HEADERS = {"service": "service", "type": "type", "item": "item", "quantity": "qty_fixed", "rates": "rate", "rate": "rate",
               "per candidate": "per_cand", "per camera/op/guard": "per_unit", "per center": "per_center", "per centre": "per_center",
               "rmk": "rmk", "logic": "logic"}      # "Total" column in the sheet is ignored (cost is calculated)


def normalize_catalog(df):
    """Catalog from Google Sheet tab / CSV. Headers: Service, Type, Item, Quantity, Rates, Total,
    Per Candidate, Per Camera/OP/Guard, Per Center, RMK, Logic."""
    df = df.copy()
    df.columns = [CAT_HEADERS.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    for c in ("service", "type", "item", "rmk", "logic"):
        df[c] = df[c].fillna("").astype(str).str.strip() if c in df.columns else ""
    for c in ("qty_fixed", "rate", "per_cand", "per_unit", "per_center"):
        df[c] = _to_number(df[c]) if c in df.columns else 0.0
    df = df[df.service.isin(CAT_SERVICES) & (df["item"] != "")]
    return df[["service", "type", "item", "qty_fixed", "rate", "per_cand", "per_unit", "per_center", "rmk", "logic"]].reset_index(drop=True)


def load_catalog():
    return normalize_catalog(pd.read_csv(DATA / "catalog.csv", dtype=str, keep_default_na=False))


def load_params():
    return json.loads((DATA / "params.json").read_text())


def load_exams(path=None):
    df = pd.read_csv(path or DATA / "exams.csv")
    return clean_exams(df)


def _parse_dates(series):
    """Exam dates in the sheet are day-month-year (26-12-2025). Never let pandas guess month-first."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    txt = series.astype(str).str.strip().str.replace(r"\s+.*$", "", regex=True)   # drop any time part
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%y", "%d.%m.%Y"):
        todo = out.isna()
        if not todo.any():
            break
        out[todo] = pd.to_datetime(txt[todo], format=fmt, errors="coerce")
    return out


def _to_number(series):
    txt = series.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(txt, errors="coerce").fillna(0)


def clean_exams(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c != "" for c in df.columns]]          # blank header columns
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]     # repeated headers: keep the first
    ren = {"Request ID": "request_id", "Exam Name": "exam_name", "Exam Date": "exam_date", "Services": "services",
           "Total Centers": "centers", "Total Candidates": "candidates", "Total Shifts": "shifts", "No of Days": "days"}
    df = df.rename(columns=ren)
    need = ["request_id", "exam_name", "exam_date", "services", "centers", "candidates", "shifts", "days"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"Missing columns: {miss}")
    df = df[need]
    df["request_id"] = df["request_id"].astype(str).str.strip()
    df = df[~df["request_id"].isin(["", "nan", "None"])]
    for c in ("centers", "candidates", "shifts", "days"):
        df[c] = _to_number(df[c])
    df["shifts"] = df["shifts"].where(df["shifts"] > 0, 1)
    df["days"] = df["days"].where(df["days"] > 0, 1)
    df["exam_date"] = _parse_dates(df["exam_date"])
    df["exam_name"] = df["exam_name"].fillna("").astype(str).str.strip()
    df["services"] = df["services"].fillna("").astype(str)
    df = df.drop_duplicates("request_id", keep="last")
    return df.reset_index(drop=True)


def data_checks(exams):
    """Rows in the exam list that probably need fixing in the Google Sheet."""
    e = exams.copy()
    out = {}
    out["Candidates or centers missing"] = e[(e.candidates <= 0) | (e.centers <= 0)]
    yr = e.exam_date.dt.year
    out["Date missing or looks wrong"] = e[e.exam_date.isna() | (yr < 2024) | (yr > 2030)]
    dup = e[e.exam_name != ""].duplicated(["exam_name", "exam_date", "candidates", "centers"], keep=False)
    out["Same exam under several Request IDs"] = e[dup].sort_values(["exam_name", "request_id"])
    out["No service in Raw Data"] = e[e.services.map(lambda t: len(services_from_raw(t)) == 0)]
    return {k: v[["request_id", "exam_name", "exam_date", "services", "centers", "candidates"]] for k, v in out.items() if len(v)}


def services_from_raw(text):
    out = []
    for tok in str(text).replace("/", ",").split(","):
        s = RAW_TO_SERVICE.get(tok.strip().upper())
        if s and s not in out:
            out.append(s)
    return [s for s in ALL_SERVICES if s in out]


def exam_units(cand, cent, P, shifts=1, days=1):
    """Equipment/manpower needed at one time = per shift-slot (same logic as the Excel Projection Report)."""
    d = P["dimensioning"]
    cand, cent = float(cand), float(cent)
    slot = cand / (max(float(shifts), 1) * max(float(days), 1))
    cam = math.ceil(slot / d["cand_per_camera"]) + d["extra_cam_per_center"] * cent if cand > 0 else 0
    cpo = d.get("cand_per_operator")          # blank in Assumptions -> operators stay manual (0)
    op = max(cent * d["min_op_per_center"], math.ceil(slot / cpo)) if (cand > 0 and cpo) else 0
    fg = max(cent, 1) * d["guards_per_center"] if cand > 0 else 0
    bg = math.ceil(cent * d["baggage_guards_per_center"]) if (cand > 0 and d.get("baggage_guards_per_center")) else 0
    return {"cand": cand, "cent": cent, "cam": cam, "op": op, "fg": fg, "bg": bg}


DAYS_RE = re.compile(r"(x|\*|\u00d7|per)\s*days?\b", re.I)    # write "x days" / "per day" in the Logic column to multiply by days
UNIT_KEY = {"CCTV": "cam", "Biometric": "op", "Frisking": "fg", "BAGGAGEMGMT": "bg"}   # what "Per Camera/OP/Guard" means per service


def build_items(exam, services, saved_ovr, catalog, P):
    """All catalog items for one exam with defaults + saved overrides applied.
    Qty = Quantity + PerCandidate x candidates + PerCamera/OP/Guard x units + PerCenter x centers   (x days if Logic says so)."""
    units = exam_units(exam["candidates"], exam["centers"], P, exam["shifts"], exam["days"])
    days = max(float(exam["days"]), 1)
    df = catalog.copy()
    qty, basis = [], []
    for _, r in df.iterrows():
        uk = UNIT_KEY[r.service]
        q = r.qty_fixed + r.per_cand * units["cand"] + r.per_unit * units[uk] + r.per_center * units["cent"]
        parts = ([f"{r.qty_fixed:g} fixed"] if r.qty_fixed else []) + ([f"{r.per_cand:g}/cand"] if r.per_cand else []) \
            + ([f"{r.per_unit:g}/{uk}"] if r.per_unit else []) + ([f"{r.per_center:g}/center"] if r.per_center else [])
        if q and DAYS_RE.search(r.logic):
            q *= days; parts.append("x days")
        qty.append(round(q, 2)); basis.append(" + ".join(parts) if parts else "Manual")
    df["def_qty"] = qty
    df["def_rate"] = df.rate.astype(float)
    df["basis"] = basis
    df["def_cost"] = df.def_qty * df.def_rate
    df["def_include"] = df.service.isin(services).astype(int)
    df["include"] = df.def_include
    df["qty_in"] = df.def_qty
    df["rate_in"] = df.def_rate
    if saved_ovr is not None and len(saved_ovr):
        key = df.service + "|" + df.type + "|" + df["item"]
        for _, o in saved_ovr.iterrows():
            m = key == f"{o['service']}|{o['type']}|{o['item']}"
            if not m.any():
                continue
            if pd.notna(o.get("include")) and o.get("include") != "":
                df.loc[m, "include"] = int(float(o["include"]))
            if pd.notna(o.get("qty")) and o.get("qty") != "":
                df.loc[m, "qty_in"] = float(o["qty"])
            if pd.notna(o.get("rate")) and o.get("rate") != "":
                df.loc[m, "rate_in"] = float(o["rate"])
    df = df.reset_index(drop=True)
    return finalize_items(df), units


def finalize_items(df):
    """Compute cost from (possibly edited) include / qty_in / rate_in columns."""
    df = df.copy()
    same = (np.abs(df.qty_in.astype(float) - df.def_qty) < 0.0051) & (np.abs(df.rate_in.astype(float) - df.def_rate) < 0.0051)
    raw = np.where(same, df.def_cost, df.qty_in.astype(float) * df.rate_in.astype(float))
    df["cost"] = np.where(df.include.astype(int) == 1, raw, 0.0)
    df["changed"] = (df.include.astype(int) != df.def_include) | ~same
    return df


def summarize(items, exam, services, rev_override, P, add_overhead=False):
    units = exam_units(exam["candidates"], exam["centers"], P, exam["shifts"], exam["days"])
    R = P["revenue"]
    cand, cent, days = float(exam["candidates"]), float(exam["centers"]), float(exam["days"])
    rev_parts = {}
    for s in ("CCTV", "Biometric", "Frisking", "BAGGAGEMGMT"):
        if s in services:
            rev_parts[s] = cand * R.get(f"{s}_per_cand", 0)
    voip_rev = voip_cost = 0.0
    if "VOIP" in services:
        voip_rev = cent * days * R["VOIP_per_center_day"]
        voip_cost = cent * days * P["voip_cost_per_center_day"]
        rev_parts["VOIP"] = voip_rev
    auto_rev = sum(rev_parts.values())
    revenue = float(rev_override) if rev_override and float(rev_override) > 0 else auto_rev
    cost_items = float(items.cost.sum())
    overhead = revenue * (P["overhead"]["logistics_pct"] + P["overhead"]["central_pct"]) if add_overhead else 0.0
    cost = cost_items + voip_cost + overhead
    margin = revenue - cost
    pct = margin / revenue if revenue else 0.0
    if revenue <= 0:
        status = "No revenue"
    elif pct < 0:
        status = "Negative"
    elif pct < P["margin_slabs"]["risky_below"]:
        status = "Risky"
    else:
        status = "OK"
    by_service = items.groupby("service").cost.sum().reindex(CAT_SERVICES).fillna(0)
    if voip_cost:
        by_service["VOIP"] = voip_cost
    if overhead:
        by_service["Overhead %"] = overhead
    return dict(units=units, auto_revenue=auto_rev, revenue=revenue, cost=cost, margin=margin, margin_pct=pct,
                status=status, by_service=by_service, by_type=items.groupby("type").cost.sum().sort_values(ascending=False),
                rev_parts=rev_parts, voip_cost=voip_cost)


def overrides_from_items(items, request_id):
    """Only rows that differ from default -> long-format override rows."""
    rows = []
    for _, r in items[items.changed].iterrows():
        inc = int(r.include) if int(r.include) != int(r.def_include) else ""
        qty = r.qty_in if abs(float(r.qty_in) - r.def_qty) >= 0.0051 else ""
        rate = r.rate_in if abs(float(r.rate_in) - r.def_rate) >= 0.0051 else ""
        rows.append([request_id, r.service, r.type, r["item"], inc, qty, rate])
    return pd.DataFrame(rows, columns=OVR_COLS)


def compute_all(exams, catalog, P, ovr, settings, add_overhead=False):
    out = []
    for _, e in exams.iterrows():
        rid = e.request_id
        st_row = settings[settings.request_id == rid]
        if len(st_row) and str(st_row.iloc[0].services).strip():
            svcs = [s for s in str(st_row.iloc[0].services).split("|") if s]
        else:
            svcs = services_from_raw(e.services)
        rev_o = float(st_row.iloc[0].rev_override) if len(st_row) and str(st_row.iloc[0].rev_override) not in ("", "nan") else 0
        items, _ = build_items(e, svcs, ovr[ovr.request_id == rid], catalog, P)
        s = summarize(items, e, svcs, rev_o, P, add_overhead)
        out.append(dict(request_id=rid, exam_name=e.exam_name, exam_date=e.exam_date, services=", ".join(svcs),
                        candidates=int(e.candidates), centers=int(e.centers), days=int(e.days),
                        revenue=round(s["revenue"]), cost=round(s["cost"]), margin=round(s["margin"]),
                        margin_pct=round(s["margin_pct"], 4), status=s["status"],
                        customised="Yes" if len(ovr[ovr.request_id == rid]) or len(st_row) else ""))
    return pd.DataFrame(out)
