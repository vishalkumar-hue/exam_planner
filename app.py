import io
import pandas as pd
import altair as alt
import streamlit as st
from planner_core import (CAT_SERVICES, ALL_SERVICES, SCALES, OVR_COLS, SET_COLS, load_catalog, load_params, load_exams,
                  clean_exams, services_from_raw, build_items, finalize_items, summarize, overrides_from_items, compute_all, data_checks)
from storage import Store

st.set_page_config(page_title="Exam Service Planner", page_icon=":bar_chart:", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
.block-container{padding-top:3.5rem;padding-bottom:3rem;padding-left:2rem;padding-right:2rem;max-width:100%!important}
section[data-testid="stSidebar"],div[data-testid="stSidebarCollapsedControl"],div[data-testid="collapsedControl"]{display:none}
/* header banner */
.hero{background:linear-gradient(90deg,#1E3A8A,#2563EB);color:#fff;border-radius:12px;padding:.9rem 1.3rem;margin-bottom:1rem}
.hero h1{margin:0;padding:0;font-size:1.5rem;font-weight:700;color:#fff}
.hero p{margin:.15rem 0 0;color:#DBEAFE;font-size:.85rem}
/* blue everywhere (works even if config.toml is missing) */
span[data-baseweb="tag"]{background-color:#1D4ED8!important}
span[data-baseweb="tag"] *{color:#fff!important}
button[kind="primary"],button[data-testid="stBaseButton-primary"]{background-color:#1D4ED8!important;border-color:#1D4ED8!important;color:#fff!important}
/* tabs */
button[data-baseweb="tab"]{font-size:1rem;font-weight:600;padding:.6rem 1.1rem}
button[data-baseweb="tab"][aria-selected="true"],button[data-baseweb="tab"][aria-selected="true"] p{color:#1D4ED8!important}
div[data-baseweb="tab-highlight"]{background-color:#1D4ED8!important;height:3px}
/* KPI cards */
div[data-testid="stMetric"]{background:#F8FAFC;border:1px solid #E2E8F0;border-left:4px solid #1D4ED8;border-radius:10px;padding:.7rem .9rem;box-shadow:0 1px 2px rgba(15,23,42,.05)}
div[data-testid="stMetricLabel"] p{color:#64748B;font-size:.8rem;font-weight:600;text-transform:uppercase;letter-spacing:.03em}
div[data-testid="stMetricValue"]{font-size:1.45rem;font-weight:700;color:#0F172A}
/* headings & blocks */
h2,h3{color:#0F172A;font-weight:700}
div[data-testid="stExpander"]{border:1px solid #E2E8F0;border-radius:10px}
div[data-testid="stExpander"] summary p{font-weight:600}
div[data-testid="stDataFrame"]{border:1px solid #E2E8F0;border-radius:10px}
.badge{display:inline-block;padding:.35rem .9rem;border-radius:999px;font-weight:700;font-size:1.05rem}
.b-OK{background:#DCFCE7;color:#166534}.b-Risky{background:#FEF3C7;color:#92400E}
.b-Negative{background:#FEE2E2;color:#991B1B}.b-Norevenue{background:#E2E8F0;color:#475569}
.stat-lab{color:#64748B;font-size:.8rem;font-weight:600;text-transform:uppercase;letter-spacing:.03em;margin-bottom:.3rem}
</style>""", unsafe_allow_html=True)

STATUS_CLR = {"OK": "background-color:#DCFCE7;color:#166534;font-weight:600", "Risky": "background-color:#FEF3C7;color:#92400E;font-weight:600",
              "Negative": "background-color:#FEE2E2;color:#991B1B;font-weight:600", "No revenue": "background-color:#E2E8F0;color:#475569;font-weight:600"}

fmt = lambda v: f"₹{v:,.0f}"
cr = lambda v: f"₹{v/1e7:,.2f} Cr" if abs(v) >= 1e7 else (f"₹{v/1e5:,.2f} L" if abs(v) >= 1e5 else f"₹{v:,.0f}")

SVC_CLR = {"CCTV": "#1D4ED8", "Biometric": "#0891B2", "Frisking": "#7C3AED", "BAGGAGEMGMT": "#EA580C", "VOIP": "#16A34A", "Overhead %": "#64748B"}

def hbar(series, colors=None, gradient=("#93C5FD", "#1E3A8A")):
    """Horizontal bar chart with value labels. colors = {name: hex} or None for a blue gradient by value."""
    df = series.reset_index(); df.columns = ["name", "cost"]; df["label"] = df.cost.map(cr)
    top = float(df.cost.max()) * 1.3 or 1
    base = alt.Chart(df).encode(y=alt.Y("name:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220, labelFontSize=12, domain=False, ticks=False)),
                                x=alt.X("cost:Q", title=None, axis=None, scale=alt.Scale(domain=[0, top])))
    col = (alt.Color("name:N", legend=None, scale=alt.Scale(domain=list(colors), range=list(colors.values()))) if colors
           else alt.Color("cost:Q", legend=None, scale=alt.Scale(range=list(gradient))))
    bars = base.mark_bar(cornerRadiusEnd=5, size=22).encode(color=col, tooltip=[alt.Tooltip("name:N", title=""), alt.Tooltip("cost:Q", title="Cost (₹)", format=",.0f")])
    txt = base.mark_text(align="left", dx=6, fontSize=12, fontWeight="bold", color="#0F172A").encode(text="label:N")
    return (bars + txt).properties(height=max(38 * len(df), 80)).configure_view(strokeWidth=0).configure_axis(grid=False)

def rcm_chart(rev, cost, margin):
    df = pd.DataFrame({"name": ["Revenue", "Cost", "Margin"], "cost": [rev, cost, margin]})
    return hbar(df.set_index("name").cost, colors={"Revenue": "#1D4ED8", "Cost": "#F59E0B", "Margin": "#16A34A" if margin >= 0 else "#DC2626"})

def pct_clr(x):
    try: x = float(x)
    except Exception: return ""
    return "color:#166534;font-weight:600" if x >= 0.3 else ("color:#92400E;font-weight:600" if x >= 0 else "color:#991B1B;font-weight:600")

def num_clr(x):
    try: return "color:#166534" if float(x) >= 0 else "color:#991B1B"
    except Exception: return ""

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
st.markdown('<div class="hero"><h1>Exam Service Planner</h1><p>Plan services, cost and margin for every exam</p></div>', unsafe_allow_html=True)
cfg = st.expander("Settings & data source", expanded=False)
with cfg:
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
        r1, r2, r3 = st.columns(3)
        r1.write("**Revenue**"); r1.json(P["revenue"], expanded=True)
        r2.write("**Dimensioning**"); r2.json(P["dimensioning"], expanded=True)
        r3.write("**Max scenario units**"); r3.json(P["max_units"], expanded=True)

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
cfg.caption(f"Exam list: **{exam_source}** ({len(exams)} exams)")

ovr, sett = tables()
tab_plan, tab_all, tab_cat = st.tabs(["Exam Planner", "All Exams", "Service Catalog"])

# ---------------- Exam Planner ----------------
with tab_plan:
    exams["label"] = exams.request_id + " | " + exams.exam_name.str[:60] + " | " + exams.exam_date.dt.strftime("%d-%b-%y").fillna("no date")
    pick = st.selectbox("Select exam", exams.label, index=None, placeholder="Type Request ID or exam name...")
    if pick is None:
        st.info("Pick an exam above to see and change what we are giving in it.", icon=":material/arrow_upward:")
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
            k[4].markdown(f'<div class="stat-lab">Status</div><span class="badge b-{S["status"].replace(" ", "")}">{S["status"]}</span>', unsafe_allow_html=True)
            k2 = st.columns(4)
            cand = max(e.candidates, 1); cent = max(e.centers, 1)
            k2[0].metric("Cost / candidate", f"₹{S['cost']/cand:,.2f}"); k2[1].metric("Cost / center", fmt(S["cost"] / cent))
            k2[2].metric("Revenue / candidate", f"₹{S['revenue']/cand:,.2f}"); k2[3].metric("Auto revenue", cr(S["auto_revenue"]))
            a, b, c3 = st.columns(3)
            a.write("**Revenue vs Cost vs Margin**"); a.altair_chart(rcm_chart(S["revenue"], S["cost"], S["margin"]), width="stretch")
            bs = S["by_service"][S["by_service"] > 0]
            b.write("**Cost by service**")
            if len(bs): b.altair_chart(hbar(bs, SVC_CLR), width="stretch")
            bt = S["by_type"][S["by_type"] > 0]
            c3.write("**Cost by type**")
            if len(bt): c3.altair_chart(hbar(bt), width="stretch")

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
    if len(v):
        g1, g2 = st.columns([1, 2])
        sc = v.status.value_counts()
        g1.write("**Exams by status**")
        g1.altair_chart(alt.Chart(sc.rename_axis("status").reset_index(name="n")).mark_bar(cornerRadiusEnd=5, size=22).encode(
            y=alt.Y("status:N", sort="-x", title=None), x=alt.X("n:Q", title=None, axis=None),
            color=alt.Color("status:N", legend=None, scale=alt.Scale(domain=["OK", "Risky", "Negative", "No revenue"], range=["#16A34A", "#F59E0B", "#DC2626", "#94A3B8"])),
            tooltip=["status", "n"]).properties(height=max(38 * len(sc), 80)).configure_view(strokeWidth=0).configure_axis(grid=False), width="stretch")
        g2.write("**Top 10 exams by revenue (Revenue vs Cost)**")
        t10 = v.nlargest(10, "revenue")[["request_id", "revenue", "cost"]].melt("request_id", var_name="kind", value_name="amt")
        g2.altair_chart(alt.Chart(t10).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("request_id:N", sort=list(v.nlargest(10, "revenue").request_id), title=None, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("amt:Q", title=None, axis=alt.Axis(format="~s")), xOffset="kind:N",
            color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=["revenue", "cost"], range=["#1D4ED8", "#F59E0B"])),
            tooltip=["request_id", "kind", alt.Tooltip("amt:Q", format=",.0f")]).properties(height=220).configure_view(strokeWidth=0), width="stretch")
    st.dataframe(v.style.map(lambda x: STATUS_CLR.get(x, ""), subset=["status"]).map(num_clr, subset=["margin"]).map(pct_clr, subset=["margin_pct"]), width="stretch", hide_index=True, height=520, column_config={
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
