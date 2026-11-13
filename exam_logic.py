"""Exam Service Planner - calculation logic + storage (no Streamlit here)."""
import io, json, math, re
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"
LOCAL = DATA / "saved"
CAT_SERVICES = ["CCTV", "Biometric", "Frisking", "BAGGAGEMGMT"]
ALL_SERVICES = CAT_SERVICES + ["VOIP"]
RAW_TO_SERVICE = {"CCTV": "CCTV", "BIO": "Biometric", "BIOMETRIC": "Biometric", "FRI": "Frisking", "FRISK": "Frisking", "FRISKING": "Frisking",
                  "VOIP": "VOIP", "BAG": "BAGGAGEMGMT", "BAGGAGE": "BAGGAGEMGMT", "BAGGAGEMGMT": "BAGGAGEMGMT", "BM": "BAGGAGEMGMT"}
OVR_COLS = ["request_id", "service", "type", "item", "include", "qty", "rate"]
SET_COLS = ["request_id", "services", "rev_override", "notes", "candidates", "centers", "shifts", "days", "cameras", "operators", "guards", "bag_guards"]
# manual-input columns in the settings table -> exam field / unit key (blank = automatic from Raw Data)
MAN_INPUTS = {"candidates": "candidates", "centers": "centers", "shifts": "shifts", "days": "days"}
MAN_UNITS = {"cameras": "cam", "operators": "op", "guards": "fg", "bag_guards": "bg"}
TABLES = {"overrides": ("Exam_Service_Overrides", OVR_COLS), "settings": ("Exam_Settings", SET_COLS)}

CAT_HEADERS = {"service": "service", "type": "type", "item": "item", "quantity": "qty_fixed", "rates": "rate", "rate": "rate",
               "per candidate": "per_cand", "per camera/op/guard": "per_unit", "per center": "per_center", "per centre": "per_center",
               "rmk": "rmk", "logic": "logic"}      # "Total" column in the sheet is ignored (cost is calculated)
DAYS_RE = re.compile(r"(x|\*|\u00d7|per)\s*days?\b", re.I)    # write "x days" / "per day" in the Logic column to multiply by days
UNIT_KEY = {"CCTV": "cam", "Biometric": "op", "Frisking": "fg", "BAGGAGEMGMT": "bg"}   # meaning of "Per Camera/OP/Guard" per service


# ======================= BUILT-IN DEFAULTS (so only app.py + exam_logic.py are needed) =======================
DEFAULT_PARAMS = {
  "dimensioning": {
    "cand_per_camera": 24,
    "extra_cam_per_center": 3,
    "cand_per_operator": 70,
    "min_op_per_center": 1,
    "guards_per_center": 4
  },
  "revenue": {
    "CCTV_per_cand": 35,
    "Biometric_per_cand": 28,
    "Frisking_per_cand": 18,
    "BAGGAGEMGMT_per_cand": 0,
    "VOIP_per_center_day": 1100
  },
  "voip_cost_per_center_day": 400,
  "overhead": {
    "logistics_pct": 0.08,
    "central_pct": 0.1
  },
  "margin_slabs": {
    "risky_below": 0.3
  }
}

BUILTIN_CATALOG_CSV = r'''Service,Type,Item,Quantity,Rates,Total,Per Candidate,Per Camera/OP/Guard,Per Center,RMK,Logic
CCTV,Operations,Camera Day-1,,,,,,,,
CCTV,Operations,Camera 30th Oct,,,,,,,,
CCTV,Operations,Camera_Manpower,,,,,,,,
CCTV,Operations,Internet,,,,,,,,
CCTV,Operations,UPS,,,,,,,,
CCTV,Operations,Monitor,,,,,,,,
CCTV,Tech,VMS_License,,,,,,,,
CCTV,Tech,VMS_Use,,,,,,,,
CCTV,Others,CCR,,,,,,,,
CCTV,Others,Backend,,,,,,,,
CCTV,Others,DR,,,,,,,,
CCTV,Others,CCR - Proctor,,,,,,,,
CCTV,Others,CCT - AI,,,,,,,,
CCTV,Others,Other Vendor Payment,,,,,,,,
CCTV,Others,Misc,,,,,,,,
CCTV,Procurement-Consumable,2TB HDD,,,,,,,,
CCTV,Procurement-Consumable,4TB HDD,,,,,,,,
CCTV,Operations,District Office Manpower for TV,,,,,,,,
CCTV,Operations,"District CCR TV Installation, Uninstallation, Internet, Reverse Logistics & Packaging (4TV each District)",,,,,,,,
CCTV,Logistics,Logistics,,,,,,,,
Biometric,Operations,Manpower,,,,,,,,
Biometric,Operations,Mock,,,,,,,,
Biometric,Operations,Training,,,,,,,,
Biometric,Procurement-Stock,Tablet,,,,,,,,
Biometric,Operations,Tablet,,,,,,,,
Biometric,Procurement-Consumable,Bar code,,,,,,,,
Biometric,Logistics,Logistics,,,,,,,,
Biometric,Logistics,Warehouse Mapower,,,,,,,,
Biometric,Logistics,Warehouse Operations,,,,,,,,
Biometric,Operations,FPS,,,,,,,,
Biometric,Operations,IRIS,,,,,,,,
Biometric,Operations,Backend,,,,,,,,
Biometric,Procurement-Consumable,ID Card,,,,,,,,
Biometric,Operations,Jacket,,,,,,,,
Biometric,Operations,OTG,,,,,,,,
Biometric,Operations,Hologram,,,,,,,,
Biometric,Operations,IRIS License,,,,,,,,
Biometric,Operations,Jio IoT Sims,,,,,,,,
Biometric,Operations,Airtel IoT Sims,,,,,,,,
Biometric,Procurement-Misc,Procurement-Misc,,,,,,,,
Biometric,Operations-Misc,Operations-Misc,,,,,,,,
Frisking,Operations,Guards,,,,,,,,
Frisking,Operations,Mock,,,,,,,,
Frisking,Operations,Training,,,,,,,,
Frisking,Operations,Encloser,,,,,,,,
Frisking,Operations,Crowd Managers,,,,,,,,
Frisking,Operations,HHMD,,,,,,,,
Frisking,Others,Backend,,,,,,,,
Frisking,Procurement-Stock,INVW Jackets,,,,,,,,
Frisking,Logistics,Logistics Rev Pickup City to Center,,,,,,,,
Frisking,Logistics,Logistics Rev Pickup Center to City,,,,,,,,
Frisking,Logistics,Logistics Rev for delhi,,,,,,,,
Frisking,Logistics,Logistics Forward from delhi,,,,,,,,
Frisking,Logistics,Warehouse Mapower,,,,,,,,
Frisking,Logistics,Warehouse Operations,,,,,,,,
Frisking,Procurement-Consumable,Id Cards,,,,,,,,
Frisking,Procurement-Consumable,Id Cards Pouch,,,,,,,,
Frisking,Procurement-Consumable,Lanyards,,,,,,,,
Frisking,Procurement-Misc,Procurement-Misc,,,,,,,,
Frisking,Operations-Misc,Operations-Misc,,,,,,,,
BAGGAGEMGMT,Operations,Guards,,,,,,,,
BAGGAGEMGMT,Operations,Mock,,,,,,,,
BAGGAGEMGMT,Operations,Training,,,,,,,,
BAGGAGEMGMT,Operations,Encloser,,,,,,,,
BAGGAGEMGMT,Procurement-Consumable,Id Cards,,,,,,,,
BAGGAGEMGMT,Procurement-Consumable,Id Cards Pouch,,,,,,,,
BAGGAGEMGMT,Procurement-Consumable,Lanyards,,,,,,,,
BAGGAGEMGMT,Procurement-Consumable,"8x8"" courier polythene",,,,,,,,
BAGGAGEMGMT,Procurement-Consumable,Marker,,,,,,,,
BAGGAGEMGMT,Procurement-Consumable,Sticker Label A4 White,,,,,,,,
BAGGAGEMGMT,Procurement-Consumable,Banner,,,,,,,,
BAGGAGEMGMT,Procurement-Misc,Procurement-Misc,,,,,,,,
BAGGAGEMGMT,Operations-Misc,Operations-Misc,,,,,,,,
'''

BUILTIN_EXAMS_CSV = r'''request_id,exam_name,exam_date,services,centers,candidates,shifts,days
VM3110,GNDU,2026-09-19,BIO,161,57317,2,4
LN3492,Group-2 Sub Group -1 Krishi Vistar Adhikari Recruitment Test - 2026,2026-09-17,"BIO, FRI, CCTV, VOIP",40,34950,3,2
NX1826,UKSSSC_ Stenographer Typing Test,2026-09-12,"CCTV, BIO",1,268,2,2
VY4189,UPSSSC Excise Constable,2026-09-20,"BIO, FRI, CCTV",73,32891,1,1
XP2161,UPSSSC Ayurvedic,2026-09-20,"BIO, FRI, CCTV",6,2678,1,1
RE6896,PT BD SHARMA UNIVERSITY,2026-09-13,"CCTV, FRI, BIO",86,23147,1,1
RO7141,Pt. BD Sharma,2026-09-13,"CCTV, BIO, FRI",87,23147,1,1
AY7744,GSSSB CRBT Exam,2026-09-16,VOIP,68,458275,2,14
XY1217,SSC Selection Post Phase 14 Exam 2026,2026-09-16,"FRI, CCTV, VOIP",211,72582,3,10
LR2864,UPPSC APGDC Mains Exam,2026-09-14,"FRI, BIO, CCTV",6,17113,1,12
OU5547,HPSC VERIFICATION,2026-09-16,BIO,1,12,1,1
TA8176,"Assistant Town Planner, 2026",2026-09-20,"BIO, FRI, CCTV, VOIP",4,1387,1,1
CS4948,Specialist Ayurveda Examination 2024,2026-09-20,"BIO, FRI, CCTV, VOIP",1,233,1,1
QW2570,SSC Combined Hindi Translator (CHT) 2026 Paper-I,2026-09-13,"CCTV, FRI",2,1834,1,1
XP5926,Junior X-ray Technician,2026-09-16,BIO,5,1727,1,1
EU6241,JKSSB- Barber/Dhobi/Jr. Dhobi,2026-09-15,BIO,2,306,1,1
AN5583,JKSSB- FMPHW/MMHW,2026-09-20,"BIO, CCTV",38,15393,1,1
XR8084,TNPSC CTSE 19 Sep 2026,2026-09-19,CCTV,6,18,1,2
BA6569,UKSSSC_ Multiple post Examination,2026-09-20,"BIO, CCTV",3,175,2,1
RV1385,RPSC SI EXAM,2026-09-20,BIO,666,217000,1,1
UK9243,GNDU- Multiple Vacancies,2026-09-19,"BIO, FRI, CCTV",161,57317,2,4
AT4819,Group-2 Sub Group-4 Combined Recruitment Test -2026,2026-09-22,"BIO, FRI, CCTV, VOIP",48,300000,2,15
GY2513,UKSSSC Graduate Level Verification,2026-09-22,BIO,1,668,2,14
KG6535,UKBSE_TET Examination,2026-09-29,BIO,4,1522,2,1
PR4775,DElEd- 2026,2026-10-03,CCTV,63,5461,1,14
UY4011,State Forest Service Mains Examination 2026,2026-09-27,"BIO, FRI, CCTV, VOIP",2,692,1,1
ZX4558,AIIMS NORCET-11 STAGE-2,2026-09-30,"BIO, CCTV, VOIP",30,12639,1,1
IO2949,SSC CGLE Examination 2026,2026-09-30,"CCTV, FRI, VOIP",221,2850000,3,18
OT7505,MPSC,2026-10-03,"BIO, CCTV, FRI",12,3489,1,9
GJ5246,SWASTHYA SHIKSHA ADHIKARI (SCREENING) EXAM- 2025,2026-10-04,"BIO, FRI, CCTV",286,124806,1,1
EU6803,"Assistant Professor Examination, 2025",2026-10-04,"BIO, FRI, CCTV, VOIP",33,24160,2,1
IM6171,UPPSC Veterinary Medical Officer (Screening),2026-10-03,"BIO, FRI, CCTV",6,2552,1,1
BI2941,MP Police ASI (Computer) & Head Constable (Computer) Recruitment 2026,2026-09-27,"BIO, CCTV",1,450,2,1
EG3727,UPPRPB Constable PET,2026-10-01,BIO,15,67724,1,7
QE7440,NITTTR,2026-10-11,BIO,185,104582,1,1
GA5193,GSSSB,2026-09-16,VOIP,69,0,3,14
PA7501,UIAMS,2026-10-03,BIO,4,5168,2,3
HX6816,JKSSB- Junior Health Inspector (FW),2026-10-06,BIO,4,1115,1,1
WE2639,JKSSB- FMPHW/MMHW,2026-10-11,"BIO, CCTV",38,15393,1,1
MP4788,UPPSC ATP Mains,2026-10-06,"CCTV, BIO, FRI",1,83,2,1
IV4893,UP PET,2026-10-25,"CCTV, FRI, BIO",1123,2883361,2,3
RN5922,UKSSSC_ Multiple Post examination,2026-10-11,"BIO, CCTV",19,5802,2,1
CE7670,MBBS 2nd Prof,2026-10-28,"CCTV, FRI",36,15791,1,6
BY9984,LUVAS,2026-10-11,BIO,9,5276,1,1
IX2997,RSSB Lab Assistant Verification,2026-09-24,BIO,1,2314,1,9
HP1457,UPESSC Special Teacher Eligibility Test (Advertisement No. - 03/2026),2026-11-03,"CCTV, BIO, FRI, VOIP",131,106547,2,1
IT4333,TNPSC Combined Civil Services Examination (Group II & IIA Services),2026-11-01,CCTV,18,0,1,2
BR3066,SSC JE Paper -1,2026-11-02,"FRI, CCTV, VOIP",195,650000,3,5
MF8373,AIIMS_INICET,2026-11-01,"BIO, CCTV, VOIP",217,76000,1,1
GI7242,AIIMS INI-SS,2026-10-24,"CCTV, BIO, VOIP",48,10150,1,1
FQ5383,GNDU VERIFICATION,2026-10-23,BIO,1,1800,1,6
ZT7125,RSSB LAB ASSISTANT VERIFICATION,2026-10-15,BIO,0,272,1,1
OK5153,TNPSC Combined Technical Services Examination (Diploma/ITI Level),2026-11-15,CCTV,18,0,1,2
GV4376,UPPSC Homeopathic Medical Officer,2026-10-24,"BIO, FRI, CCTV",22,9048,1,1
AF2787,UPPSC Homeopathic Medical Officer,2026-10-24,"BIO, FRI, CCTV",22,9048,1,1
XC7524,UPPSC Vetting Officer,2026-10-26,"BIO, CCTV, FRI",12,4969,1,1
TX7247,UPPSC Dental Surgeon,2026-10-28,"BIO, FRI, CCTV",34,14739,1,1
WH7234,GNDU,2026-10-25,BIO,411,149521,1,1
OM5877,Azamgarh University - Asst Professor,2026-10-27,"BIO, CCTV",4,3500,2,1
XP8512,JKSSB- CSSD Attendant/Nursing Aid G-III,2026-10-25,"BIO, CCTV",31,11297,1,1
MV2089,Shift 1: Psychologist & Line Man  Shift 2: Instructor,2026-10-25,"BIO, CCTV",2,505,2,1
RV6967,UPSSSC PET,2026-10-25,VOIP,594,261321,4,3
YZ7648,CSIR- VERIFICATION,2026-10-26,BIO,1,1,1,1
TA9466,Assistant District Public Prosecution Officer 2026,2026-11-01,"BIO, FRI, CCTV, VOIP",83,32784,1,1
'''


# ======================= DATA LOADING / CLEANING =======================
def _to_number(series):
    txt = series.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(txt, errors="coerce").fillna(0)


def _parse_dates(series):
    """Exam dates are day-month-year (26-12-2025). Never let pandas guess month-first."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    txt = series.astype(str).str.strip().str.replace(r"\s+.*$", "", regex=True)   # drop any time part
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%y", "%d.%m.%Y", "%d-%b-%y", "%d-%b-%Y"):
        todo = out.isna()
        if not todo.any():
            break
        out[todo] = pd.to_datetime(txt[todo], format=fmt, errors="coerce")
    return out


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
    """data/catalog.csv if present, else the built-in item list (Qty/Rate are filled in the app or the Google Sheet)."""
    f = DATA / "catalog.csv"
    src = f if f.exists() else io.StringIO(BUILTIN_CATALOG_CSV)
    return normalize_catalog(pd.read_csv(src, dtype=str, keep_default_na=False))


def load_params():
    """Rates are built into this file (DEFAULT_PARAMS). A data/params.json, if present, overrides them."""
    f = DATA / "params.json"
    return json.loads(f.read_text()) if f.exists() else json.loads(json.dumps(DEFAULT_PARAMS))


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


SHEET_ID = "1N7Lx3qCe96eFlDWG-qilCCzCusiJ0rP4xUQxgmgu52w"   # Google Sheet that holds the 'Raw Data' tab


def read_public_tab(tab, sheet_id=SHEET_ID):
    """Read one tab of a Google Sheet through its CSV link (no service account needed).
    Works only if the sheet is shared as 'Anyone with the link -> Viewer'."""
    from urllib.parse import quote
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={quote(tab)}"
    df = pd.read_csv(url, dtype=str, keep_default_na=False)
    if "Request ID" not in [str(c).strip() for c in df.columns]:
        raise RuntimeError("Sheet did not open as CSV - share it as 'Anyone with the link: Viewer', and check the tab name")
    return df


def load_exams(path=None):
    """Given path, else data/exams.csv if present, else the built-in sample exam list."""
    f = DATA / "exams.csv"
    return clean_exams(pd.read_csv(path or (f if f.exists() else io.StringIO(BUILTIN_EXAMS_CSV))))


def services_from_raw(text):
    out = []
    for tok in str(text).replace("/", ",").split(","):
        s = RAW_TO_SERVICE.get(tok.strip().upper())
        if s and s not in out:
            out.append(s)
    return [s for s in ALL_SERVICES if s in out]


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


# ======================= CALCULATIONS =======================
def exam_units(cand, cent, P, shifts=1, days=1, manual=None):
    """Equipment/manpower needed at one time = per shift-slot (same logic as the Excel Projection Report).
    manual = {"cam":.., "op":.., "fg":.., "bg":..} replaces the automatic number for that unit."""
    d = P["dimensioning"]
    cand, cent = float(cand), float(cent)
    slot = cand / (max(float(shifts), 1) * max(float(days), 1))
    cam = math.ceil(slot / d["cand_per_camera"]) + d["extra_cam_per_center"] * cent if cand > 0 else 0
    cpo = d.get("cand_per_operator") or 70     # Excel Assumptions: 1 operator per 70 candidates
    op = max(cent * d["min_op_per_center"], math.ceil(slot / cpo)) if (cand > 0 and cpo) else 0
    fg = max(cent, 1) * d["guards_per_center"] if cand > 0 else 0
    bg = math.ceil(cent * d["baggage_guards_per_center"]) if (cand > 0 and d.get("baggage_guards_per_center")) else 0
    u = {"cand": cand, "cent": cent, "cam": cam, "op": op, "fg": fg, "bg": bg}
    u.update(manual or {})
    return u


def manual_units(exam):
    """Manual cameras / operators / guards typed for this exam (NaN or missing = automatic)."""
    out = {}
    for k in ("cam", "op", "fg", "bg"):
        v = exam.get(f"man_{k}")
        if v is not None and v == v:
            out[k] = float(v)
    return out


def _num(v):
    try:
        if v is None or str(v).strip() in ("", "nan", "None"):
            return None
        return float(v)
    except Exception:
        return None


def effective_exam(exam, srow=None):
    """Raw Data row + this exam's manual overrides (from the settings table). Blank = automatic from Raw Data."""
    e = exam.copy()
    for col, fld in MAN_INPUTS.items():
        v = _num(srow.get(col)) if srow is not None else None
        if v is not None and v >= 0:
            e[fld] = max(v, 1) if fld in ("shifts", "days") else v
    for col, k in MAN_UNITS.items():
        v = _num(srow.get(col)) if srow is not None else None
        e[f"man_{k}"] = v if (v is not None and v >= 0) else np.nan
    return e


def build_items(exam, services, saved_ovr, catalog, P):
    """All catalog items for one exam with defaults + saved overrides applied.
    Qty = Quantity + PerCandidate x candidates + PerCamera/OP/Guard x units + PerCenter x centers   (x days if Logic says so)."""
    units = exam_units(exam["candidates"], exam["centers"], P, exam["shifts"], exam["days"], manual_units(exam))
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
    units = exam_units(exam["candidates"], exam["centers"], P, exam["shifts"], exam["days"], manual_units(exam))
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
    for _, e0 in exams.iterrows():
        rid = e0.request_id
        st_row = settings[settings.request_id == rid]
        e = effective_exam(e0, st_row.iloc[0] if len(st_row) else None)
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


# ======================= STORAGE =======================
class Store:
    """Saves per-exam overrides. Backend 'local' (CSV files) or 'gsheet' (Google Sheet) - picked automatically."""

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

    def read_tab(self, name):
        """Read a whole tab as text (first row = headers). Only works when the Google Sheet is connected."""
        if self.kind != "gsheet":
            raise RuntimeError("Google Sheet is not connected")
        vals = self._sh.worksheet(name).get_all_values()
        if len(vals) < 2:
            return pd.DataFrame(columns=vals[0] if vals else [])
        return pd.DataFrame(vals[1:], columns=vals[0])

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
