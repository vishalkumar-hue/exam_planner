import io
import pandas as pd
import streamlit as st
from planner_core import (CAT_SERVICES, ALL_SERVICES, SCALES, OVR_COLS, SET_COLS, load_catalog, load_params, load_exams,
                  clean_exams, services_from_raw, build_items, finalize_items, summarize, overrides_from_items, compute_all, data_checks)
from storage import Store

st.set_page_config(page_title="Exam Service Planner", page_icon=":bar_chart:", layout="wide")
st.markdown("""<style>
.block-container{padding-top:1.5rem;max-width:1400px}
div[data-testid="stMetricValue"]{font-size:1.5rem}
</style>""", unsafe_allow_html=True)

fmt = lambda v: f"₹{v:,.0f}"
cr = lambda v: f"₹{v/1e7:,.2f} Cr" if abs(v) >= 1e7 else (f"₹{v/1e5:,.2f} L" if abs(v) >= 1e5 else f"₹{v:,.0f}")

# ---------------- data & storage ----------------
@st.cache_data
def _catalog(): return load_catalog()
@st.cache_data
def _params(): return load_params()
@st.cache_resource
def _store():
    try: sec = st.secrets
    except Exception: sec = None
    try:
        _ = "gsheet" in sec if sec is not None else None
    except Exception:
        sec = None
    return Store(sec)

catalog, P, store = _catalog(), _params(), _store()

RAW_TAB = "Raw Data"   # tab name in the Google Sheet that holds the exam list

@st.cache_data(ttl=300, show_spinner="Reading Raw Data from Google Sheet...")
def _exams_from_sheet(_store, tab):
    return clean_exams(_store.read_tab(tab))

def tables():
    if "tables" not in st.session_state:
        st.session_state.tables = {"overrides": store.load("overrides"), "settings": store.load("settings")}
    return st.session_state.tables["overrides"], st.session_state.tables["settings"]

# ---------------- sidebar ----------------
with st.sidebar:
    st.title("Exam Service Planner")
    st.caption("Storage: **Google Sheet**" if store.kind == "gsheet" else "Storage: **local files** (use Google Sheet on Streamlit Cloud, see README)")
    if getattr(store, "error", None): st.warning(store.error)
    if store.kind == "gsheet":
        if st.button("Refresh exam list from Google Sheet", width="stretch"):
            st.cache_data.clear()
            st.session_state.pop("tables", None)      # reload saved changes too
            st.rerun()
    up = st.file_uploader("Or load an exam list for this session only (Excel with 'Raw Data' sheet, or CSV)", type=["xlsx", "csv"])
    add_overhead = st.toggle("Add overhead % of revenue", value=False,
        help=f"Logistics {P['overhead']['logistics_pct']:.0%} + Central/CCR {P['overhead']['central_pct']:.0%} of revenue (from Excel Assumptions). Off by default because logistics/procurement are already items in the catalog.")
    with st.expander("Rates used (from Assumptions)"):
        st.write("**Revenue**"); st.json(P["revenue"], expanded=False)
        st.write("**Dimensioning**"); st.json(P["dimensioning"], expanded=False)
        st.write("**Max scenario units**"); st.json(P["max_units"], expanded=False)

exam_source = ""
try:
    if up is not None:
        raw = pd.read_csv(up) if up.name.endswith(".csv") else pd.read_excel(up, sheet_name="Raw Data")
        exams = clean_exams(raw); exam_source = f"uploaded file ({up.name})"
    elif store.kind == "gsheet":
        try:
            exams = _exams_from_sheet(store, RAW_TAB).copy(); exam_source = f"Google Sheet, tab '{RAW_TAB}'"
        except Exception as e:
            st.warning(f"Could not read tab '{RAW_TAB}' from the Google Sheet ({e}). Showing the backup list from data/exams.csv instead.")
            exams = load_exams(); exam_source = "backup file data/exams.csv"
    else:
        exams = load_exams(); exam_source = "backup file data/exams.csv"
except Exception as e:
    st.error(f"Could not read exam list: {e}"); st.stop()
st.sidebar.caption(f"Exam list: **{exam_source}** ({len(exams)} exams)")

ovr, sett = tables()
tab_plan, tab_all, tab_cat = st.tabs(["Exam Planner", "All Exams", "Service Catalog"])

# ---------------- Exam Planner ----------------
with tab_plan:
    exams["label"] = exams.request_id + " | " + exams.exam_name.str[:60] + " | " + exams.exam_date.dt.strftime("%d-%b-%y").fillna("no date")
    pick = st.selectbox("Select exam", exams.label, index=None, placeholder="Type Request ID or exam name...")
    if pick is None:
        st.info("Pick an exam to see and change what we are giving in it.")
    else:
        e = exams[exams.label == pick].iloc[0]; rid = e.request_id
        s_row = sett[sett.request_id == rid]
        saved_svcs = [s for s in str(s_row.iloc[0].services).split("|") if s] if len(s_row) else []
        default_svcs = saved_svcs or services_from_raw(e.services)
        saved_rev = float(s_row.iloc[0].rev_override) if len(s_row) and str(s_row.iloc[0].rev_override) not in ("", "nan") else 0.0
        ver = st.session_state.get("ver", {}).get(rid, 0)

        if e.candidates <= 0 or e.centers <= 0:
            st.warning("Candidates or Centers are missing for this exam - numbers will be zero until they are filled in the Raw Data.")
        st.caption(f"Services in Raw Data: **{e.services or '-'}**")
        services = st.multiselect("Services we give in this exam", ALL_SERVICES, default=default_svcs, key=f"svc_{rid}_{ver}",
                                  help="CCTV / Biometric / Frisking / BAGGAGEMGMT use the catalog items. VOIP uses the simple per-center rate.")

        items, units = build_items(e, services, ovr[ovr.request_id == rid], catalog, P)
        c = st.columns(8)
        for col, (lab, val) in zip(c, [("Candidates", e.candidates), ("Centers", e.centers), ("Days", e.days), ("Shifts/day", e.shifts),
                                       ("Cameras", units["cam"]), ("Operators", units["op"]), ("Frisk guards", units["fg"]), ("Baggage guards", units["bg"])]):
            col.metric(lab, f"{val:,.0f}")

        result_box = st.container()
        st.subheader("What we give")
        st.caption("Tick/untick Include, or change Qty / Rate for this exam only. Qty defaults to the max-scenario quantity scaled to this exam's size.")
        edited = []
        for svc in [s for s in CAT_SERVICES if s in services]:
            sub = items[items.service == svc]
            with st.expander(f"{svc}  -  {cr(sub.cost.sum())}  ({int(sub.include.sum())} items)", expanded=True):
                view = sub[["include", "item", "type", "scales_with", "qty_in", "rate_in", "cost"]].copy()
                view["include"] = view.include.astype(bool)
                out = st.data_editor(
                    view, key=f"ed_{rid}_{svc}_{ver}", hide_index=True, width="stretch", height=min(35 * len(view) + 40, 520),
                    column_config={"include": st.column_config.CheckboxColumn("Include", width="small"),
                                   "item": st.column_config.TextColumn("Item", disabled=True),
                                   "type": st.column_config.TextColumn("Type", disabled=True),
                                   "scales_with": st.column_config.TextColumn("Scales with", disabled=True, width="small"),
                                   "qty_in": st.column_config.NumberColumn("Qty", format="%.2f", min_value=0),
                                   "rate_in": st.column_config.NumberColumn("Rate (₹)", format="%.2f", min_value=0),
                                   "cost": st.column_config.NumberColumn("Cost (₹)", format="%,.0f", disabled=True)})
                m = sub.copy()
                m["include"] = out.include.astype(int).values
                m["qty_in"] = out.qty_in.fillna(0).values
                m["rate_in"] = out.rate_in.fillna(0).values
                edited.append(m)
        items2 = pd.concat(edited + [items[~items.service.isin(services)].assign(include=0)]) if edited else items.assign(include=0)
        items2 = finalize_items(items2.reset_index(drop=True))

        rev_o = st.number_input("Revenue override (₹) - 0 = calculate from rates", min_value=0.0, value=saved_rev, step=10000.0, key=f"rev_{rid}_{ver}")
        S = summarize(items2, e, services, rev_o, P, add_overhead)
        if "BAGGAGEMGMT" in services and rev_o <= 0 and P["revenue"].get("BAGGAGEMGMT_per_cand", 0) == 0:
            st.warning("BAGGAGEMGMT has no revenue rate in Assumptions - enter the revenue override above or the margin will look too low.")

        with result_box:
            st.subheader("Result")
            k = st.columns(5)
            k[0].metric("Revenue", cr(S["revenue"])); k[1].metric("Cost", cr(S["cost"]))
            k[2].metric("Margin", cr(S["margin"])); k[3].metric("Margin %", f"{S['margin_pct']:.1%}")
            k[4].metric("Status", S["status"])
            k2 = st.columns(4)
            cand = max(e.candidates, 1); cent = max(e.centers, 1)
            k2[0].metric("Cost / candidate", f"₹{S['cost']/cand:,.2f}"); k2[1].metric("Cost / center", fmt(S["cost"] / cent))
            k2[2].metric("Revenue / candidate", f"₹{S['revenue']/cand:,.2f}"); k2[3].metric("Auto revenue", cr(S["auto_revenue"]))
            a, b = st.columns(2)
            a.write("**Cost by service**"); a.bar_chart(S["by_service"][S["by_service"] > 0], horizontal=True)
            b.write("**Cost by type**"); b.dataframe(S["by_type"][S["by_type"] > 0].rename("Cost (₹)").map(lambda v: f"{v:,.0f}"), width="stretch")

        sv, rs, nt = st.columns([1, 1, 3])
        note = nt.text_input("Note (optional)", value=str(s_row.iloc[0].notes) if len(s_row) else "", key=f"nt_{rid}_{ver}", label_visibility="collapsed", placeholder="Note for this exam (optional)")
        if sv.button("Save this exam", type="primary"):
            new_o = overrides_from_items(items2, rid)
            ovr2 = pd.concat([ovr[ovr.request_id != rid], new_o], ignore_index=True)
            auto = services_from_raw(e.services)
            keep = services != auto or rev_o > 0 or note.strip()
            sett2 = sett[sett.request_id != rid]
            if keep:
                sett2 = pd.concat([sett2, pd.DataFrame([[rid, "|".join(services), rev_o if rev_o > 0 else "", note]], columns=SET_COLS)], ignore_index=True)
            try:
                store.save("overrides", ovr2); store.save("settings", sett2)
                st.session_state.tables = {"overrides": ovr2.astype(str), "settings": sett2.astype(str)}
                st.success(f"Saved {len(new_o)} changed item(s) for {rid}.")
            except Exception as ex:
                st.error(f"Save failed: {ex}")
        if rs.button("Reset to default"):
            ovr2 = ovr[ovr.request_id != rid]; sett2 = sett[sett.request_id != rid]
            try:
                store.save("overrides", ovr2); store.save("settings", sett2)
                st.session_state.tables = {"overrides": ovr2, "settings": sett2}
                st.session_state.setdefault("ver", {})[rid] = ver + 1
                st.rerun()
            except Exception as ex:
                st.error(f"Reset failed: {ex}")

# ---------------- All Exams ----------------
with tab_all:
    res = compute_all(exams, catalog, P, ovr, sett, add_overhead)
    checks = data_checks(exams)
    if checks:
        with st.expander(f"Data checks - {sum(len(v) for v in checks.values())} rows may need fixing in the Google Sheet"):
            for title, d in checks.items():
                st.write(f"**{title}** ({len(d)})")
                st.dataframe(d, hide_index=True, width="stretch", height=min(35 * len(d) + 40, 300),
                             column_config={"exam_date": st.column_config.DateColumn("Date", format="DD-MMM-YYYY")})
    f1, f2 = st.columns([2, 2])
    stat = f1.multiselect("Status", sorted(res.status.unique()), default=sorted(res.status.unique()))
    q = f2.text_input("Search (Request ID / exam name)")
    v = res[res.status.isin(stat)]
    if q: v = v[v.request_id.str.contains(q, case=False) | v.exam_name.str.contains(q, case=False)]
    m = st.columns(4)
    m[0].metric("Exams", len(v)); m[1].metric("Revenue", cr(v.revenue.sum())); m[2].metric("Cost", cr(v.cost.sum()))
    m[3].metric("Margin %", f"{(v.margin.sum() / v.revenue.sum() if v.revenue.sum() else 0):.1%}")
    st.dataframe(v, width="stretch", hide_index=True, height=520, column_config={
        "exam_date": st.column_config.DateColumn("Date", format="DD-MMM-YY"),
        "revenue": st.column_config.NumberColumn("Revenue (₹)", format="%,d"), "cost": st.column_config.NumberColumn("Cost (₹)", format="%,d"),
        "margin": st.column_config.NumberColumn("Margin (₹)", format="%,d"), "margin_pct": st.column_config.NumberColumn("Margin %", format="percent")})
    buf = io.BytesIO(); v.to_excel(buf, index=False, sheet_name="Exams"); 
    d1, d2 = st.columns(2)
    d1.download_button("Download Excel", buf.getvalue(), "exam_projection.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    d2.download_button("Download saved overrides (CSV)", ovr.to_csv(index=False), "exam_service_overrides.csv", "text/csv")

# ---------------- Catalog ----------------
with tab_cat:
    st.caption("Max-scenario services and rates (data/catalog.csv). 'Scales with' says which exam size drives the quantity: an exam with half the candidates pays half of a Candidate-scaled item.")
    fs = st.multiselect("Service", CAT_SERVICES, default=CAT_SERVICES, key="catsvc")
    cv = catalog[catalog.service.isin(fs)].rename(columns={"qty": "max_qty", "rate": "rate", "total": "max_total"})
    st.dataframe(cv, width="stretch", hide_index=True, height=560,
                 column_config={"max_qty": st.column_config.NumberColumn(format="%,d"), "rate": st.column_config.NumberColumn(format="%,d"), "max_total": st.column_config.NumberColumn(format="%,d")})
    st.write("Total at max scenario:", fmt(catalog.total.sum()))
