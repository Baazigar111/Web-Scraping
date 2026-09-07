import base64
import hashlib
import hmac
import io
import json
import os
import re
import zipfile
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import streamlit as st

st.set_page_config(
    page_title="Odisha RERA Filter & Project Explorer",
    page_icon="🏢",
    layout="wide",
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_HASHING_KEY = os.getenv("API_HASHING_KEY")
BASE_URL = os.getenv("BASE_URL", "https://reraapps.odisha.gov.in").rstrip("/")
ODISHA_STATE_ID = int(os.getenv("ODISHA_STATE_ID", 21))
TOKEN_STORE_FILE = os.getenv("TOKEN_STORE_FILE", "tokens_cache.json")
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

STATIC_DISTRICTS = [
    {"id": 354, "name": "Ganjam"},
    {"id": 350, "name": "Angul"},
    {"id": 351, "name": "Balangir"},
    {"id": 352, "name": "Balasore"},
    {"id": 353, "name": "Bargarh"},
    {"id": 355, "name": "Bhadrak"},
    {"id": 356, "name": "Boudh"},
    {"id": 357, "name": "Cuttack"},
    {"id": 358, "name": "Deogarh"},
    {"id": 359, "name": "Dhenkanal"},
    {"id": 360, "name": "Gajapati"},
    {"id": 361, "name": "Jagatsinghapur"},
    {"id": 362, "name": "Jajapur"},
    {"id": 363, "name": "Jharsuguda"},
    {"id": 364, "name": "Kalahandi"},
    {"id": 365, "name": "Kandhamal"},
    {"id": 366, "name": "Kendrapara"},
    {"id": 367, "name": "Kendujhar"},
    {"id": 368, "name": "Khordha"},
    {"id": 369, "name": "Koraput"},
    {"id": 370, "name": "Malkangiri"},
    {"id": 371, "name": "Mayurbhanj"},
    {"id": 372, "name": "Nabarangpur"},
    {"id": 373, "name": "Nayagarh"},
    {"id": 374, "name": "Nuapada"},
    {"id": 375, "name": "Puri"},
    {"id": 376, "name": "Rayagada"},
    {"id": 377, "name": "Sambalpur"},
    {"id": 378, "name": "Sonepur"},
    {"id": 379, "name": "Sundargarh"},
]

# --- Internal Gateway Service (Conceals Signatures & Portals) --- #
class InternalGateway:
    def __init__(self):
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.mount("http://", HTTPAdapter(max_retries=retries))

    def _sign_payload(self, data) -> dict:
        if not API_HASHING_KEY:
            return {}
        json_str = json.dumps(data, separators=(",", ":")) if isinstance(data, (dict, list)) else str(data)
        encoded = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")
        token = hmac.new(
            API_HASHING_KEY.encode("utf-8"),
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {"REQUEST_DATA": encoded, "REQUEST_TOKEN": token}

    def _decode_response(self, res_json: dict):
        if isinstance(res_json, dict) and "RESPONSE_DATA" in res_json:
            decoded_raw = base64.b64decode(res_json["RESPONSE_DATA"]).decode("utf-8")
            try:
                return json.loads(decoded_raw)
            except Exception:
                return decoded_raw
        return res_json

    def _get_headers(self) -> dict:
        auth_meta = self._sign_payload({"USER_AUTHKEY": "", "USER_ID": "", "USER_TYPE": "2"})
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://rera.odisha.gov.in",
            "Referer": "https://rera.odisha.gov.in/",
            "Authorization": json.dumps(auth_meta, separators=(",", ":")),
        }

    def post(self, endpoint: str, data):
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        try:
            res = self.session.post(
                url,
                headers=self._get_headers(),
                json=self._sign_payload(data),
                timeout=30,
            )
            if res.status_code == 200:
                return self._decode_response(res.json())
        except Exception:
            pass
        return {}

    def fetch_pdf(self, file_id: str | int, token: str):
        clean_id = str(file_id).strip()
        clean_tok = token.strip()
        if not clean_id.isdigit() or not clean_tok:
            return None

        decrypt_url = f"{BASE_URL}/dms/fileDecryptHandlerForPdfPublic"
        dms_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/dms/public/library/pdfjsnewds/web/viewer.html?fileId={clean_id}&text={clean_tok}",
            "Authorization": f"bearer {clean_tok}",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            res = self.session.post(
                decrypt_url,
                headers=dms_headers,
                data={"fileId": clean_id, "token": clean_tok},
                timeout=25,
            )
            if res.status_code == 200:
                dec_data = self._decode_response(res.json())
                pdf_path = (
                    dec_data.get("result", {}).get("filePath")
                    if isinstance(dec_data.get("result"), dict)
                    else dec_data.get("result") or dec_data.get("filePath")
                )
                if pdf_path and isinstance(pdf_path, str):
                    pdf_url = pdf_path if pdf_path.startswith("http") else f"{BASE_URL}/{pdf_path.lstrip('/')}"
                    pdf_res = self.session.get(pdf_url, headers=dms_headers, timeout=30)
                    if pdf_res.status_code == 200 and pdf_res.content.startswith(b"%PDF"):
                        return pdf_res.content
        except Exception:
            pass
        return None

gateway = InternalGateway()


# --- Persistent Token Cache (Cloud Redis + Local Fallback) --- #
def load_saved_tokens() -> dict:
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            res = requests.get(
                f"{UPSTASH_URL}/get/rera_tokens",
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                timeout=5,
            )
            if res.status_code == 200:
                raw_val = res.json().get("result")
                if raw_val:
                    if isinstance(raw_val, str):
                        try:
                            return json.loads(raw_val)
                        except json.JSONDecodeError:
                            return {}
                    elif isinstance(raw_val, dict):
                        return raw_val
        except Exception:
            pass

    if os.path.exists(TOKEN_STORE_FILE):
        try:
            with open(TOKEN_STORE_FILE, "r", encoding="utf-8") as f:
                disk_tokens = json.load(f)
                if isinstance(disk_tokens, dict):
                    return disk_tokens
        except Exception:
            pass

    return {}


def save_token_to_disk(identifier: str, token: str):
    tokens = load_saved_tokens()
    clean_id = str(identifier).strip()
    clean_tok = token.strip()

    if clean_tok:
        tokens[clean_id] = clean_tok
    elif clean_id in tokens:
        del tokens[clean_id]

    try:
        with open(TOKEN_STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
    except Exception:
        pass

    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            requests.post(
                f"{UPSTASH_URL}/set/rera_tokens",
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                data=json.dumps(tokens),
                timeout=5,
            )
        except Exception:
            pass


# --- Clean Business Logic Endpoints (Internal Router) --- #
@st.cache_data(show_spinner=False, ttl=3600)
def api_get_districts():
    res = gateway.post("pms/api/master/Demography/getDistrict", ODISHA_STATE_ID)
    if isinstance(res, list) and len(res) > 0:
        return res
    if isinstance(res, dict) and res.get("result"):
        return res["result"]
    return STATIC_DISTRICTS


@st.cache_data(show_spinner=False, ttl=3600)
def api_get_tahasils(district_id: int):
    if not district_id:
        return []
    res = gateway.post("pms/api/master/Demography/getTahasil", int(district_id))
    if isinstance(res, list):
        return res
    if isinstance(res, dict) and "result" in res:
        return res["result"]
    return []


def api_search_projects(filters: dict):
    payload = {
        "searchTerm": filters.get("search_term", "").strip(),
        "district": filters.get("district", 0),
        "tahasil": filters.get("tahasil", 0),
        "strtYear": filters.get("start_year", 0),
        "endYear": filters.get("possession_year", 0),
        "carpetArea": filters.get("carpet_area", ""),
        "propertyType": filters.get("property_types", []),
        "projectStatus": filters.get("project_statuses", []),
        "latitude": "",
        "longitude": "",
        "radius": "",
        "approvedStatus": False,
        "revokedStatus": False,
        "page": filters.get("page", 1),
        "pageSize": filters.get("page_size", 10),
        "sortOrder": "asc",
    }
    return gateway.post("pms/api/master/Projects/projectListing", payload)


def api_get_all_subdetails(project_id: str, promoter_id: str):
    p_body = {"projectId": str(project_id), "promoterId": str(promoter_id)}
    return {
        "projectDetails": gateway.post("pms/api/project/ProjectOverview/projectDetails", p_body),
        "facilityDetails": gateway.post("pms/api/project/ProjectOverview/facilityDetails", p_body),
        "landDetails": gateway.post("pms/api/project/ProjectOverview/landDetails", p_body),
        "promoterDetails": gateway.post("pms/api/project/ProjectOverview/promoterDetails", p_body),
        "boardMembers": gateway.post("pms/api/project/ProjectOverview/getBoardMemberDetails", p_body),
        "bankDetails": gateway.post("pms/api/project/ProjectOverview/getBankAccountDetails", p_body),
        "projectDocuments": gateway.post("pms/api/project/ProjectBooking/projectDocument", p_body),
        "professionalDetails": gateway.post("pms/api/project/ProjectBooking/professinalDetails", p_body),
        "plottedUnitsData": gateway.post("pms/api/project/ProjectPreviewDetails/getPlottedUnitsData", p_body),
        "plottedBookingDetails": gateway.post("pms/api/project/ProjectPreviewDetails/getProjectPlottedBookingDetails", p_body),
        "parkingDetails": gateway.post("pms/api/project/ProjectPreviewDetails/getParkingDetails", p_body),
        "projectMilestone": gateway.post("pms/api/project/projectMilestoneCitizen/projectMilestoneCitizenView", p_body),
        "qprList": gateway.post("pms/api/qpr/QprList/getQPRPromoterList", p_body),
        "aacList": gateway.post("pms/api/qpr/Aacannualreport/getAACList", p_body),
    }


# --- Safe Table / JSON Rendering Helper --- #
def render_safe_table_or_json(raw_data, empty_msg="No records found."):
    if not raw_data:
        st.info(empty_msg)
        return

    if isinstance(raw_data, list):
        if len(raw_data) == 0:
            st.info(empty_msg)
            return
        try:
            st.dataframe(pd.DataFrame(raw_data), use_container_width=True)
            return
        except Exception:
            st.json(raw_data)
            return

    if isinstance(raw_data, dict):
        for key in ["result", "data", "unitList", "projectUnitList", "parkingList"]:
            sub_val = raw_data.get(key)
            if isinstance(sub_val, list):
                if len(sub_val) == 0:
                    st.info(empty_msg)
                    return
                try:
                    st.dataframe(pd.DataFrame(sub_val), use_container_width=True)
                    return
                except Exception:
                    pass
        st.json(raw_data)
        return

    st.info(empty_msg)


# --- Document Processing & Builders --- #
def extract_project_booking_documents(doc_res: dict):
    docs = []
    seen = set()
    saved = load_saved_tokens()

    def add_item(name, identifier, source):
        if not identifier or str(identifier) in seen or str(identifier) in ("0", "null", "None"):
            return
        str_id = str(identifier).strip()
        seen.add(str_id)
        docs.append({
            "Document Name": name,
            "Identifier": str_id,
            "Source": source,
            "Token": saved.get(str_id, ""),
        })

    if isinstance(doc_res, dict):
        for doc in doc_res.get("result", []):
            add_item(doc.get("documentName") or doc.get("docName") or "Project Document", doc.get("documentId"), "Standard Documents")
        for doc in doc_res.get("projectDocument", []):
            add_item(doc.get("docName") or doc.get("documentName") or "Legal Document", doc.get("documentID"), "Legal Documents")
        for doc in doc_res.get("financeDocument", []):
            add_item(doc.get("documentName") or doc.get("docName") or "Financial Document", doc.get("documentId"), "Financial Documents")
        for noc in doc_res.get("nocDocuments", []):
            add_item(noc.get("nocName") or noc.get("documentName") or "NOC Document", noc.get("documentId"), "NOC Documents")
        afs = doc_res.get("afsDocuments", {})
        if isinstance(afs, dict):
            for k, label in [("scheduleA_Id", "Schedule A"), ("scheduleB_Id", "Schedule B"), ("scheduleC_Id", "Schedule C")]:
                if afs.get(k):
                    add_item(f"Agreement for Sale ({label})", afs[k], "Agreement for Sale")
    return docs


def extract_overview_documents(prj_data: dict, p_info: dict):
    docs = []
    seen = set()
    saved = load_saved_tokens()

    def add_item(name, identifier, source):
        if not identifier or str(identifier) in seen or str(identifier) in ("0", "null", "None"):
            return
        str_id = str(identifier).strip()
        seen.add(str_id)
        docs.append({
            "Document Name": name,
            "Identifier": str_id,
            "Source": source,
            "Token": saved.get(str_id, ""),
        })

    if isinstance(prj_data, dict):
        cert_id = p_info.get("certificateCopyId") or prj_data.get("certificateCopyId")
        if cert_id:
            add_item("Registration Certificate", cert_id, "Project Details")
        for k, label in [
            ("buildingPlanId", "Approved Building Plan"),
            ("sitePlanId", "Approved Site Plan"),
            ("buildingDrawPlanId", "Building Drawing Plan"),
            ("nakhshaLocationId", "Nakhsha / Location Map"),
        ]:
            if prj_data.get(k):
                add_item(label, prj_data[k], "Layout Plans")
    return docs


def extract_all_documents(p_info: dict, sub_data: dict):
    docs = []
    seen = set()
    saved = load_saved_tokens()

    def add_doc(name, identifier, source):
        if not identifier or str(identifier) in seen or str(identifier) in ("0", "null", "None"):
            return
        str_id = str(identifier).strip()
        seen.add(str_id)
        docs.append({
            "Document Name": name,
            "Identifier": str_id,
            "Source": source,
            "Token": saved.get(str_id, ""),
        })

    prj_details = sub_data.get("projectDetails", {}).get("result", {})
    land_details = sub_data.get("landDetails", {}).get("result", [])
    doc_res = sub_data.get("projectDocuments", {})
    prom_details = sub_data.get("promoterDetails", {}).get("result", {})
    fac_list = sub_data.get("facilityDetails", {}).get("result", [])

    for ov in extract_overview_documents(prj_details, p_info):
        add_doc(ov["Document Name"], ov["Identifier"], ov["Source"])

    if isinstance(fac_list, list):
        for fac in fac_list:
            if fac.get("documentId") and fac["documentId"] != 0:
                add_doc(f"{fac.get('facilityName', 'Facility')} Document", fac["documentId"], "Facilities")

    for pd_item in extract_project_booking_documents(doc_res):
        add_doc(pd_item["Document Name"], pd_item["Identifier"], pd_item["Source"])

    if isinstance(land_details, list):
        for idx, plot in enumerate(land_details):
            p_no = plot.get("plotNo", f"Plot #{idx+1}")
            for k, label in [
                ("plotEcId", "Encumbrance Certificate"),
                ("plotRorId", "Record of Rights (ROR)"),
                ("saleDeedId", "Sale Deed"),
                ("poaId", "Power of Attorney (POA)"),
                ("shareAllocId", "Share Allocation"),
            ]:
                if plot.get(k) and plot[k] != 0:
                    add_doc(f"{label} - Plot {p_no}", plot[k], f"Plot {p_no}")
            for o in plot.get("owners", []):
                if o.get("fileId") and o["fileId"] != 0:
                    add_doc(f"Owner Share Document - {o.get('name', 'Owner')}", o["fileId"], f"Plot {p_no}")

    if isinstance(prom_details, dict):
        for k, label in [
            ("registrationCertId", "Promoter Registration Certificate"),
            ("gstCopyId", "Promoter GST Copy"),
            ("panCopyId", "Promoter PAN Copy"),
        ]:
            if prom_details.get(k):
                add_doc(label, prom_details[k], "Promoter Details")

    bank_resp = sub_data.get("bankDetails", {})
    if isinstance(bank_resp, dict):
        b_res = bank_resp.get("result", {})
        if isinstance(b_res, dict):
            if b_res.get("reraCancellChequeId"):
                add_doc("Bank Account Cheque / Passbook", b_res["reraCancellChequeId"], "Bank Account Details")
            if b_res.get("bankStatementDocId"):
                add_doc("Bank Statement", b_res["bankStatementDocId"], "Bank Account Details")
        f_query = bank_resp.get("fundSourceQuery", {})
        if isinstance(f_query, dict) and f_query.get("estimationCopyId"):
            add_doc("Project Estimate Copy", f_query["estimationCopyId"], "Financial Details")

    prof_data = sub_data.get("professionalDetails", {}).get("result", [])
    if isinstance(prof_data, list):
        for prof in prof_data:
            doc_id = prof.get("documentId") or prof.get("certificateDocId") or prof.get("docId")
            if doc_id:
                p_name = prof.get("represntativeName") or prof.get("name") or "Professional"
                add_doc(f"Certificate - {p_name}", doc_id, "Professionals")

    for key, field, prefix in [
        ("projectMilestone", "milestoneName", "Milestone Doc"),
        ("qprList", "quarter", "QPR Report"),
        ("aacList", "financialYear", "AAC Audit Report"),
    ]:
        items = sub_data.get(key, {}).get("result", [])
        if isinstance(items, list):
            for item in items:
                d_id = item.get("documentId") or item.get("docId") or item.get("fileId")
                if d_id:
                    add_doc(f"{prefix} - {item.get(field, '')}", d_id, key)

    return docs


def build_project_zip(docs_list: list):
    zip_buffer = io.BytesIO()
    downloaded_count = 0
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for doc in docs_list:
            token = doc.get("Token", "")
            if token:
                pdf_bytes = gateway.fetch_pdf(doc["Identifier"], token)
                if pdf_bytes:
                    clean_name = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', doc['Document Name'])}.pdf"
                    zip_file.writestr(clean_name, pdf_bytes)
                    downloaded_count += 1
    zip_buffer.seek(0)
    return zip_buffer.getvalue(), downloaded_count


def render_documents_manager(docs_list: list, unique_key_prefix: str):
    if not docs_list:
        st.info("No downloadable documents found.")
        return

    saved_tokens = load_saved_tokens()
    for doc in docs_list:
        ident = str(doc["Identifier"]).strip()
        doc["Token"] = saved_tokens.get(ident, "").strip()

    ready_count = sum(1 for d in docs_list if d.get("Token", "").strip())
    col_h1, col_h2 = st.columns([3, 1])
    with col_h1:
        st.markdown(f"**Total Documents:** {len(docs_list)} | **Tokens Active:** {ready_count}/{len(docs_list)}")
    with col_h2:
        if ready_count > 0:
            zip_bytes, count = build_project_zip(docs_list)
            if count > 0:
                st.download_button(
                    label=f"📦 Download Unlocked ({count}) as ZIP",
                    data=zip_bytes,
                    file_name=f"Project_{unique_key_prefix}_Documents.zip",
                    mime="application/zip",
                    key=f"{unique_key_prefix}_bulk_zip",
                    type="primary",
                )

    st.markdown("---")

    for idx, doc in enumerate(docs_list):
        doc_name = doc["Document Name"]
        identifier = str(doc["Identifier"]).strip()
        source = doc["Source"]
        state_key = f"tok_{unique_key_prefix}_{identifier}"
        current_token = doc.get("Token", "")

        col_name, col_token, col_dl = st.columns([5, 4, 3])
        with col_name:
            st.markdown(f"📄 **{doc_name}**")
            st.caption(f"ID: `{identifier}` | Source: *{source}*")

        with col_token:
            if state_key not in st.session_state:
                st.session_state[state_key] = current_token

            token_input = st.text_input(
                label=f"Token for {identifier}",
                key=state_key,
                placeholder="Paste token (&text=...)",
                label_visibility="collapsed",
            )
            if token_input.strip() != current_token:
                save_token_to_disk(identifier, token_input.strip())
                st.rerun()

        with col_dl:
            if current_token:
                pdf_data = gateway.fetch_pdf(identifier, current_token)
                if pdf_data:
                    clean_filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', doc_name)}.pdf"
                    st.download_button(
                        label="⬇️ Download PDF",
                        data=pdf_data,
                        file_name=clean_filename,
                        mime="application/pdf",
                        key=f"dl_{unique_key_prefix}_{identifier}_{idx}",
                    )
                else:
                    st.warning("⚠️ Invalid token")
            else:
                st.caption("🔒 Paste Token & Enter")

        st.divider()


# ==========================================
# SIDEBAR FILTERS
# ==========================================
with st.sidebar:
    st.markdown("### Filter")
    if st.button("Clear Filter", use_container_width=True):
        st.session_state["f_search"] = ""
        st.session_state["f_district"] = 0
        st.session_state["f_tahasil"] = 0
        st.session_state["f_start_yr"] = 0
        st.session_state["f_possession_yr"] = 0
        st.session_state["f_carpet_area"] = "All"
        st.session_state["f_ptypes"] = []
        st.session_state["f_pstatus"] = []
        st.rerun()

    st.markdown("---")
    st.markdown("##### 📍 Demography")

    districts = api_get_districts()
    dist_map = {0: "Select District"}
    for d in districts:
        dist_map[d.get("id")] = d.get("name")

    selected_district_id = st.selectbox("District", options=list(dist_map.keys()), format_func=lambda x: dist_map[x], key="f_district")
    tahasils = api_get_tahasils(selected_district_id) if selected_district_id else []
    tahasil_map = {0: "Tahasil"}
    for t in tahasils:
        tahasil_map[t.get("id")] = t.get("name")

    selected_tahasil_id = st.selectbox("Tahasil", options=list(tahasil_map.keys()), format_func=lambda x: tahasil_map[x], disabled=len(tahasils) == 0, key="f_tahasil")

    st.markdown("---")
    st.markdown("##### 📅 Year")
    c_y1, c_y2 = st.columns(2)
    with c_y1:
        sel_start_yr = st.selectbox("Start Year", options=[0] + list(range(2010, 2027)), format_func=lambda x: "Start Year" if x == 0 else str(x), key="f_start_yr")
    with c_y2:
        sel_poss_yr = st.selectbox("Possession Year", options=[0] + list(range(2026, 2037)), format_func=lambda x: "Possession Y" if x == 0 else str(x), key="f_possession_yr")

    st.markdown("---")
    st.markdown("##### 📐 Carpet Area")
    carpet_opts = {"All": "All", "0-100": "0-100 sqm", "100-200": "100-200 sqm", "200-300": "200-300 sqm", "300-500": "300-500 sqm", "500-": "More than 500 sqm"}
    sel_carpet = st.radio("Carpet Area", options=list(carpet_opts.keys()), format_func=lambda x: carpet_opts[x], key="f_carpet_area")

    st.markdown("---")
    st.markdown("##### 🏗️ Project Type")
    project_types_meta = [
        {"id": "1", "name": "Residential"},
        {"id": "2", "name": "Commercial"},
        {"id": "3", "name": "Plotted Scheme"},
        {"id": "4", "name": "Mixed"},
        {"id": "5", "name": "Affordable Housing"},
    ]
    selected_ptypes = [pt["id"] for pt in project_types_meta if st.checkbox(pt["name"], key=f"pt_{pt['id']}")]

    st.markdown("---")
    st.markdown("##### 📊 Project Status")
    project_statuses_meta = [{"id": "1", "name": "Completed"}, {"id": "2", "name": "On Going"}]
    selected_statuses = [ps["id"] for ps in project_statuses_meta if st.checkbox(ps["name"], key=f"ps_{ps['id']}")]

    page_size = st.number_input("Projects per Page", min_value=1, max_value=50, value=10)


# ==========================================
# MAIN PAGE: SEARCH & DETAILS
# ==========================================
st.title("🏢 Odisha RERA Project & Document Explorer")

sc1, sc2 = st.columns([5, 1])
with sc1:
    search_term = st.text_input("Search by Project/Promoter Name", placeholder="Search by Project/Promoter Name...", key="f_search", label_visibility="collapsed")
with sc2:
    st.button("Search", type="primary", use_container_width=True)

filter_payload = {
    "search_term": search_term.strip() if search_term else "",
    "district": selected_district_id,
    "tahasil": selected_tahasil_id,
    "start_year": sel_start_yr,
    "possession_year": sel_poss_yr,
    "carpet_area": "" if sel_carpet == "All" else sel_carpet,
    "property_types": selected_ptypes,
    "project_statuses": selected_statuses,
    "page": 1,
    "page_size": page_size,
}

with st.spinner("Fetching matching records..."):
    listing_response = api_search_projects(filter_payload)
    projects_found = listing_response.get("result", [])
    total_found = listing_response.get("total", 0)

st.write(f"### *Showing **{len(projects_found)}** Projects (Total in Portal: {total_found})*")

if not projects_found:
    st.warning("No projects found matching the selected filter criteria.")
else:
    for p in projects_found:
        p_id = str(p.get("intid"))
        pr_id = str(p.get("promoterId"))
        p_name = p.get("project_Name", "Unnamed Project")
        prom_name = p.get("promotor_Name", "----")

        with st.expander(f"📁 **{p_name}** | by {prom_name} (Reg No: `{p.get('reg_no', 'N/A')}`)"):
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                st.markdown(f"**Address:** {p.get('location', '--')}")
                st.markdown(f"**Started From:** {p.get('projectStartdate', '--')}")
            with c2:
                st.markdown(f"**Project ID:** `{p_id}` | **Promoter ID:** `{pr_id}`")
                st.markdown(f"**Possession by:** {p.get('projectEnddate', '--')}")
            with c3:
                st.markdown(f"**Units:** `{p.get('unit_count') or p.get('plot_unit_count') or '--'}`")

            sub_details = api_get_all_subdetails(p_id, pr_id)
            docs_extracted = extract_all_documents(p, sub_details)

            t_docs, t_proj_docs, t_json, t_overview, t_prom, t_bm, t_prof, t_units, t_ms, t_qpr, t_aac, t_bank, t_fin, t_land, t_fac = st.tabs(
                [
                    "📑 Downloadable Files",
                    "📁 Project Documents",
                    "📦 Raw JSON",
                    "ℹ️ Overview",
                    "🏢 Promoter",
                    "👥 Board Members",
                    "👷 Professionals",
                    "📊 Units Status",
                    "🎯 Milestones",
                    "📈 QPR Details",
                    "📑 AAC Details",
                    "🏦 Bank Details",
                    "💰 Financials",
                    "🏞️ Land Details",
                    "🏊 Facilities",
                ]
            )

            with t_docs:
                render_documents_manager(docs_extracted, unique_key_prefix=f"p_{p_id}")

            with t_proj_docs:
                pdocs = extract_project_booking_documents(sub_details.get("projectDocuments", {}))
                render_documents_manager(pdocs, unique_key_prefix=f"pdocs_{p_id}")

            with t_json:
                saved_toks = load_saved_tokens()
                for d in docs_extracted:
                    d["Token"] = saved_toks.get(d["Identifier"], "")
                payload_export = {"downloadableDocuments": docs_extracted, **sub_details}
                st.json(payload_export)
                st.download_button(
                    label="📥 Export JSON",
                    data=json.dumps(payload_export, indent=2),
                    file_name=f"project_{p_id}.json",
                    mime="application/json",
                    key=f"dl_json_{p_id}",
                )

            with t_overview:
                prj_res = sub_details.get("projectDetails", {}).get("result", {})
                ov_docs = extract_overview_documents(prj_res, p)
                if ov_docs:
                    render_documents_manager(ov_docs, unique_key_prefix=f"pdetails_{p_id}")
                    st.markdown("---")
                st.json(prj_res if prj_res else {})

            with t_prom:
                st.json(sub_details.get("promoterDetails", {}).get("result", {}))

            with t_bm:
                bm = sub_details.get("boardMembers", {}).get("result", [])
                render_safe_table_or_json(bm, "No board members registered.")

            with t_prof:
                profs = sub_details.get("professionalDetails", {}).get("result", [])
                render_safe_table_or_json(profs, "No professionals registered.")

            with t_units:
                raw_units = sub_details.get("plottedUnitsData", {})
                units = raw_units.get("result", raw_units) if isinstance(raw_units, dict) else raw_units
                render_safe_table_or_json(units, "No units inventory reported.")

            with t_ms:
                ms = sub_details.get("projectMilestone", {}).get("result", [])
                render_safe_table_or_json(ms, "No milestones reported.")

            with t_qpr:
                qpr = sub_details.get("qprList", {}).get("result", [])
                render_safe_table_or_json(qpr, "No QPR reports available.")

            with t_aac:
                aac = sub_details.get("aacList", {}).get("result", [])
                render_safe_table_or_json(aac, "No AAC audits available.")

            with t_bank:
                b_res = sub_details.get("bankDetails", {}).get("result", {})
                if b_res:
                    st.json(b_res)
                    if b_res.get("reraCancellChequeId"):
                        render_documents_manager([{
                            "Document Name": "Bank Cancelled Cheque",
                            "Identifier": str(b_res["reraCancellChequeId"]),
                            "Source": "Bank Account Details",
                            "Token": load_saved_tokens().get(str(b_res["reraCancellChequeId"]), "")
                        }], unique_key_prefix=f"bank_{p_id}")
                else:
                    st.info("No bank records available.")

            with t_fin:
                fund = sub_details.get("bankDetails", {}).get("fundSourceQuery", {})
                if fund:
                    f1, f2, f3 = st.columns(3)
                    with f1:
                        st.metric("Estimated Cost", f"₹ {fund.get('estematedCost', '0.00')} Lakhs")
                    with f2:
                        st.metric("Promoter Investment", f"₹ {fund.get('promoterInvestment', '0.00')} Lakhs")
                    with f3:
                        st.metric("Allottee Investment", f"₹ {fund.get('allotteeInvestment', '0.00')} Lakhs")
                    if fund.get("estimationCopyId"):
                        render_documents_manager([{
                            "Document Name": "Estimate Copy",
                            "Identifier": str(fund["estimationCopyId"]),
                            "Source": "Financial Details",
                            "Token": load_saved_tokens().get(str(fund["estimationCopyId"]), "")
                        }], unique_key_prefix=f"fin_{p_id}")
                else:
                    st.info("No financial data found.")

            with t_land:
                land = sub_details.get("landDetails", {}).get("result", [])
                render_safe_table_or_json(land, "No land records available.")

            with t_fac:
                fac = sub_details.get("facilityDetails", {}).get("result", [])
                render_safe_table_or_json(fac, "No facility records available.")