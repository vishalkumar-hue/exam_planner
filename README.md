# Exam Service Planner (Streamlit)

Pick an exam -> tick the services/items we give -> see cost, revenue, margin instantly -> save per-exam changes.

## Put the code on GitHub
Use a **private** repo (the data/ folder holds your exam list and rates).

    git init && git add . && git commit -m "Exam Service Planner"
    git branch -M main
    git remote add origin https://github.com/<you>/<repo>.git
    git push -u origin main

`.gitignore` already keeps out secrets, saved data and Excel files. Never commit `.streamlit/secrets.toml`
(copy `.streamlit/secrets.toml.example` and fill it only locally / in Streamlit Cloud Secrets).
Run tests with `pip install -r requirements-dev.txt && pytest`.

## Run on your computer
    pip install -r requirements.txt
    streamlit run app.py

## Deploy on Streamlit Cloud
1. Put this folder in a GitHub repo, create the app on share.streamlit.io with `app.py` as main file.
2. For saving that survives restarts, connect a Google Sheet (Streamlit Cloud local files are wiped on restart):
   - Create a Google service account, share your Google Sheet with its email (Editor).
   - App -> Settings -> Secrets:

        [gsheet]
        spreadsheet_key = "YOUR_SHEET_ID"      # or: spreadsheet_name = "Exam Planner"

        [gcp_service_account]
        # paste the whole service-account JSON here as key = "value" lines
        type = "service_account"
        project_id = "..."
        private_key_id = "..."
        private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
        client_email = "..."
        client_id = "..."
        token_uri = "https://oauth2.googleapis.com/token"

   - The app creates two tabs itself: `Exam_Service_Overrides` and `Exam_Settings`.
   - Without these secrets it saves to `data/saved/*.csv` (fine on your own computer).

## Data files (data/)
- `catalog.csv`  - 73 max-scenario items: service, type, item, qty, rate, total, scales_with
- `exams.csv`    - exam list (Raw Data). Use the sidebar uploader to load a fresh Raw Data sheet any time.
- `params.json`  - rates and dimensioning copied from the Excel Assumptions tab
- Re-create all three from the Excel report: `python tools/build_data.py "report.xlsx"`

## How the numbers are calculated
- Exam size: cameras / operators / guards use the same rule as the Excel Projection Report
  (candidates per shift-slot, 24:1 cameras + 3 per center, 70:1 operators, 4 guards per center; baggage guards = 6 per center).
- Item quantity for an exam = max-scenario quantity x (exam size / max size) for the item's "Scales with"
  (Cameras, Centers, Candidates, Operators, Guards, Fixed). Lump sums and unclear quantities scale with Candidates.
- Item cost = catalog total x same factor. If you change Qty or Rate, cost = Qty x Rate.
- Revenue = candidates x per-candidate rate per service (VOIP: centers x days x rate) unless you enter a revenue override.
- Status: Negative < 0%, Risky < 30%, else OK (same slabs as the Excel report).
- Days are not multiplied into catalog items (the catalog totals are treated as the full cost of the exam).

## Please verify
- Revenue rates in Assumptions are still the placeholder numbers (35 / 28 / 18 per candidate); BAGGAGEMGMT has no revenue rate.
- "Scales with" for each item was auto-detected from its quantity; correct any wrong ones in `data/catalog.csv`.
