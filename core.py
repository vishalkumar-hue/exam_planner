"""Pure calculation logic (no Streamlit) so it can be tested and reused."""
import json, math
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


def load_catalog():
    return pd.read_csv(DATA / "catalog.csv")


def load_params():
    return json.loads((DATA / "params.json").read_text())


def load_exams(path=None):
    df = pd.read_csv(path or DATA / "exams.csv")
    return clean_exams(df)


def clean_exams(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    ren = {"Request ID": "request_id", "Exam Name": "exam_name", "Exam Date": "exam_date", "Services": "services",
           "Total Centers": "centers", "Total Candidates": "candidates", "Total Shifts": "shifts", "No of Days": "days"}
    df = df.rename(columns=ren)
    need = ["request_id", "exam_name", "exam_date", "services", "centers", "candidates", "shifts", "days"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise ValueError(f"Missing columns: {miss}")
    df = df[need].dropna(subset=["request_id"])
    for c in ("centers", "candidates", "shifts", "days"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["shifts"] = df["shifts"].where(df["shifts"] > 0, 1)
    df["days"] = df["days"].where(df["days"] > 0, 1)
    df["exam_date"] = pd.to_datetime(df["exam_date"], errors="coerce")
    df["services"] = df["services"].fillna("").astype(str)
    df["request_id"] = df["request_id"].astype(str).str.strip()
    return df.reset_index(drop=True)


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
    op = max(cent * d["min_op_per_center"], math.ceil(slot / d["cand_per_operator"])) if cand > 0 else 0
    fg = max(cent, 1) * d["guards_per_center"] if cand > 0 else 0
    bg = math.ceil(cent * d["baggage_guards_per_center"]) if cand > 0 else 0
    return {"cand": cand, "cent": cent, "cam": cam, "op": op, "fg": fg, "bg": bg}


def _factor(scales_with, service, units, mx):
    if scales_with == "Fixed":
        return 1.0
    key = {"Candidates": "cand", "Centers": "cent", "Cameras": "cam", "Operators": "op"}.get(scales_with)
    if scales_with == "Guards":
        key = "bg" if service == "BAGGAGEMGMT" else "fg"
    if key is None or not mx.get(key):
        return 0.0
    return float(units[key]) / float(mx[key])


def build_items(exam, services, saved_ovr, catalog, P):
    """All catalog items for one exam with defaults + saved overrides applied."""
    units = exam_units(exam["candidates"], exam["centers"], P, exam["shifts"], exam["days"])
    mx = P["max_units"]
    df = catalog.copy()
    df["factor"] = [_factor(s, sv, units, mx) for s, sv in zip(df.scales_with, df.service)]
    df["def_qty"] = (df.qty * df.factor).round(2)
    df["def_rate"] = df.rate.astype(float)
    df["def_cost"] = df.total * df.factor
    df["def_include"] = ((df.total > 0) & df.service.isin(services)).astype(int)
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
