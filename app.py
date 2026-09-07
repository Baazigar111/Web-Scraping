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

API_HASHING_KEY = os.getenv("API_HASHING_KEY", "22CSMTOOL2022")
BASE_URL = os.getenv("BASE_URL", "https://reraapps.odisha.gov.in")
ODISHA_STATE_ID = int(os.getenv("ODISHA_STATE_ID", 21))
TOKEN_STORE_FILE = os.getenv("TOKEN_STORE_FILE", "tokens_cache.json")

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

# --- Resilient HTTP Session --- #
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    raise_on_status=False,
)
adapter = HTTPAdapter(max_retries=retry_strategy)
http_session = requests.Session()
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)


# --- Persistent Token Cache (URL Sync + Local Disk Fallback) --- #
def load_saved_tokens() -> dict:
    tokens = {}
    # 1. Primary: load from browser URL parameters (survives Render restarts)
    if "tokens" in st.query_params:
        try:
            raw_param = st.query_params["tokens"]
            decoded = base64.urlsafe_b64decode(raw_param.encode("utf-8")).decode("utf-8")
            url_tokens = json.loads(decoded)
            if isinstance(url_tokens, dict):
                tokens.update(url_tokens)
        except Exception:
            pass

    # 2. Secondary: load from local container cache if present
    if os.path.exists(TOKEN_STORE_FILE):
        try:
            with open(TOKEN_STORE_FILE, "r", encoding="utf-8") as f:
                disk_tokens = json.load(f)
                if isinstance(disk_tokens, dict):
                    for k, v in disk_tokens.items():
                        if k not in tokens:
                            tokens[k] = v
        except Exception:
            pass

    return tokens


def save_token_to_disk(identifier: str, token: str):
    tokens = load_saved_tokens()
    clean_id = str(identifier).strip()
    clean_tok = token.strip()

    if clean_tok:
        tokens[clean_id] = clean_tok
    elif clean_id in tokens:
        del tokens[clean_id]

    # Save to disk
    try:
        with open(TOKEN_STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
    except Exception:
        pass

    # Sync directly to browser URL query params
    try:
        encoded_tokens = base64.urlsafe_b64encode(
            json.dumps(tokens, separators=(",", ":")).encode("utf-8")
        ).decode("utf-8")
        st.query_params["tokens"] = encoded_tokens
    except Exception:
        pass


# --- Request Helpers --- #
def create_payload(data_dict: dict | int | str, hashing_key: str = API_HASHING_KEY) -> dict:
    if isinstance(data_dict, (dict, list)):
        json_str = json.dumps(data_dict, separators=(",", ":"))
    else:
        json_str = str(data_dict)
    encoded_data = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")
    token = hmac.new(
        hashing_key.encode("utf-8"),
        encoded_data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"REQUEST_DATA": encoded_data, "REQUEST_TOKEN": token}


def decode_response(res_json: dict):
    if isinstance(res_json, dict) and "RESPONSE_DATA" in res_json:
        decoded_raw = base64.b64decode(res_json["RESPONSE_DATA"]).decode("utf-8")
        try:
            return json.loads(decoded_raw)
        except Exception:
            return decoded_raw
    return res_json


def get_standard_headers():
    auth_dict = {"USER_AUTHKEY": "", "USER_ID": "", "USER_TYPE": "2"}
    auth_payload = create_payload(auth_dict)
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://rera.odisha.gov.in",
        "Referer": "https://rera.odisha.gov.in/",
        "Authorization": json.dumps(auth_payload, separators=(",", ":")),
    }


def post_pms_api(endpoint: str, data: dict | int | str):
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    headers = get_standard_headers()
    payload = create_payload(data)
    try:
        res = http_session.post(url, headers=headers, json=payload, timeout=30)
        if res.status_code == 200:
            return decode_response(res.json())
    except (requests.exceptions.Timeout, requests.exceptions.RequestException):
        return {}
    except Exception:
        return {}
    return {}


# --- Demography Handlers --- #
@st.cache_data(show_spinner=False, ttl=3600)
def fetch_districts():
    res = post_pms_api("pms/api/master/Demography/getDistrict", ODISHA_STATE_ID)
    if isinstance(res, list) and len(res) > 0:
        return res
    if isinstance(res, dict) and "result" in res and res["result"]:
        return res["result"]
    return STATIC_DISTRICTS


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_tahasils(district_id: int):
    if not district_id:
        return []
    res = post_pms_api("pms/api/master/Demography/getTahasil", int(district_id))
    if isinstance(res, list):
        return res
    if isinstance(res, dict) and "result" in res:
        return res["result"]
    return []


# --- PDF Downloader & Decryptor (Uncached to ensure instant token updates) --- #
def fetch_pdf_bytes(file_id_or_name: str | int, token_override: str = ""):
    if not file_id_or_name:
        return None

    str_val = str(file_id_or_name).strip()
    token = token_override.strip()

    if str_val.isdigit() and token:
        decrypt_url = f"{BASE_URL}/dms/fileDecryptHandlerForPdfPublic"
        dms_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://reraapps.odisha.gov.in",
            "Referer": f"{BASE_URL}/dms/public/library/pdfjsnewds/web/viewer.html?fileId={str_val}&text={token}",
            "Authorization": f"bearer {token}",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            res = http_session.post(
                decrypt_url,
                headers=dms_headers,
                data={"fileId": str_val, "token": token},
                timeout=25,
            )
            if res.status_code == 200:
                res_json = res.json()
                dec_data = decode_response(res_json) if "RESPONSE_DATA" in res_json else res_json
                pdf_path = (
                    dec_data.get("result", {}).get("filePath")
                    if isinstance(dec_data.get("result"), dict)
                    else dec_data.get("result") or dec_data.get("filePath")
                )
                if pdf_path and isinstance(pdf_path, str):
                    pdf_url = pdf_path if pdf_path.startswith("http") else f"{BASE_URL}/{pdf_path.lstrip('/')}"
                    pdf_res = http_session.get(pdf_url, headers=dms_headers, timeout=30)
                    if pdf_res.status_code == 200 and pdf_res.content.startswith(b"%PDF"):
                        return pdf_res.content
        except Exception:
            pass

    return None


def get_browser_viewer_url(file_id_or_name: str | int, token: str = "") -> str:
    str_val = str(file_id_or_name).strip()
    if str_val.isdigit():
        t_param = f"&text={token}" if token else ""
        return f"{BASE_URL}/dms/public/library/pdfjsnewds/web/viewer.html?fileId={str_val}{t_param}"
    return f"{BASE_URL}/cms/api/getFile/{str_val}"


# --- Project Queries & Processing --- #
def fetch_project_listing_filtered(filters: dict):
    payload = {
        "searchTerm": filters.get("searchTerm", "").strip(),
        "district": filters.get("district", 0),
        "tahasil": filters.get("tahasil", 0),
        "strtYear": filters.get("strtYear", 0),
        "endYear": filters.get("endYear", 0),
        "projectStatus": filters.get("projectStatus", []),
        "carpetArea": filters.get("carpetArea", ""),
        "propertyType": filters.get("propertyType", []),
        "latitude": "",
        "longitude": "",
        "radius": "",
        "approvedStatus": False,
        "revokedStatus": False,
        "page": filters.get("page", 1),
        "pageSize": filters.get("pageSize", 10),
        "sortOrder": "asc",
    }
    return post_pms_api("pms/api/master/Projects/projectListing", payload)


def fetch_all_project_subdetails(project_id: str, promoter_id: str):
    lookup_payload = {"projectId": str(project_id), "promoterId": str(promoter_id)}

    return {
        "projectDetails": post_pms_api("pms/api/project/ProjectOverview/projectDetails", lookup_payload),
        "facilityDetails": post_pms_api("pms/api/project/ProjectOverview/facilityDetails", lookup_payload),
        "landDetails": post_pms_api("pms/api/project/ProjectOverview/landDetails", lookup_payload),
        "promoterDetails": post_pms_api("pms/api/project/ProjectOverview/promoterDetails", lookup_payload),
        "boardMembers": post_pms_api("pms/api/project/ProjectOverview/getBoardMemberDetails", lookup_payload),
        "bankDetails": post_pms_api("pms/api/project/ProjectOverview/getBankAccountDetails", lookup_payload),
        "projectDocuments": post_pms_api("pms/api/project/ProjectBooking/projectDocument", lookup_payload),
        "professionalDetails": post_pms_api("pms/api/project/ProjectBooking/professinalDetails", lookup_payload),
        "plottedUnitsData": post_pms_api("pms/api/project/ProjectPreviewDetails/getPlottedUnitsData", lookup_payload),
        "plottedBookingDetails": post_pms_api("pms/api/project/ProjectPreviewDetails/getProjectPlottedBookingDetails", lookup_payload),
        "parkingDetails": post_pms_api("pms/api/project/ProjectPreviewDetails/getParkingDetails", lookup_payload),
        "projectMilestone": post_pms_api("pms/api/project/projectMilestoneCitizen/projectMilestoneCitizenView", lookup_payload),
        "qprList": post_pms_api("pms/api/qpr/QprList/getQPRPromoterList", lookup_payload),
        "aacList": post_pms_api("pms/api/qpr/Aacannualreport/getAACList", lookup_payload),
    }


def extract_project_booking_documents(doc_res: dict):
    docs = []
    seen_ids = set()
    saved_tokens = load_saved_tokens()

    def add_item(name, identifier, source):
        if not identifier or str(identifier) in seen_ids or str(identifier) in ("0", "null", "None"):
            return
        str_id = str(identifier).strip()
        seen_ids.add(str_id)
        docs.append({
            "Document Name": name,
            "Identifier": str_id,
            "Source": source,
            "Token": saved_tokens.get(str_id, ""),
        })

    if isinstance(doc_res, dict):
        for doc in doc_res.get("result", []):
            d_name = doc.get("documentName") or doc.get("docName") or "Project Document"
            add_item(d_name, doc.get("documentId") or doc.get("documentID"), "Standard Documents")

        for doc in doc_res.get("projectDocument", []):
            d_name = doc.get("docName") or doc.get("documentName") or "Legal / Statutory Document"
            add_item(d_name, doc.get("documentID") or doc.get("documentId"), "Legal Documents")

        for doc in doc_res.get("financeDocument", []):
            d_name = doc.get("documentName") or doc.get("docName") or "Financial Document"
            add_item(d_name, doc.get("documentId") or doc.get("documentID"), "Financial Documents")

        for noc in doc_res.get("nocDocuments", []):
            d_name = noc.get("nocName") or noc.get("documentName") or "NOC Document"
            add_item(d_name, noc.get("documentId") or noc.get("docId"), "NOC Documents")

        afs = doc_res.get("afsDocuments", {})
        if isinstance(afs, dict):
            if afs.get("scheduleA_Id") or afs.get("scheduleA"):
                add_item("Agreement for Sale (Schedule A)", afs.get("scheduleA_Id") or afs.get("scheduleA"), "Agreement for Sale")
            if afs.get("scheduleB_Id") or afs.get("scheduleB"):
                add_item("Agreement for Sale (Schedule B)", afs.get("scheduleB_Id") or afs.get("scheduleB"), "Agreement for Sale")
            if afs.get("scheduleC_Id") or afs.get("scheduleC"):
                add_item("Agreement for Sale (Schedule C)", afs.get("scheduleC_Id") or afs.get("scheduleC"), "Agreement for Sale")

    return docs


def extract_overview_documents(prj_data: dict, p_info: dict):
    docs = []
    seen_ids = set()
    saved_tokens = load_saved_tokens()

    def add_item(name, identifier, source):
        if not identifier or str(identifier) in seen_ids or str(identifier) in ("0", "null", "None"):
            return
        str_id = str(identifier).strip()
        seen_ids.add(str_id)
        docs.append({
            "Document Name": name,
            "Identifier": str_id,
            "Source": source,
            "Token": saved_tokens.get(str_id, ""),
        })

    if isinstance(prj_data, dict):
        cert_id = p_info.get("certificateCopyId") or prj_data.get("certificateCopyId")
        if cert_id:
            add_item("Registration Certificate", cert_id, "Project Details")

        if prj_data.get("buildingPlanId"):
            add_item("Approved Building Plan", prj_data["buildingPlanId"], "Layout Plans")
        if prj_data.get("sitePlanId"):
            add_item("Approved Site Plan", prj_data["sitePlanId"], "Layout Plans")
        if prj_data.get("buildingDrawPlanId"):
            add_item("Building Drawing Plan", prj_data["buildingDrawPlanId"], "Layout Plans")
        if prj_data.get("nakhshaLocationId"):
            add_item("Nakhsha / Location Map", prj_data["nakhshaLocationId"], "Layout Plans")

    return docs


def extract_all_documents(p_info: dict, sub_data: dict):
    docs = []
    seen_ids = set()
    saved_tokens = load_saved_tokens()

    def add_doc(name, identifier, source):
        if not identifier or str(identifier) in seen_ids or str(identifier) in ("0", "null", "None"):
            return
        str_id = str(identifier).strip()
        seen_ids.add(str_id)
        docs.append({
            "Document Name": name,
            "Identifier": str_id,
            "Source": source,
            "Token": saved_tokens.get(str_id, ""),
        })

    prj_details = sub_data.get("projectDetails", {}).get("result", {})
    land_details = sub_data.get("landDetails", {}).get("result", [])
    doc_res = sub_data.get("projectDocuments", {})
    prom_details = sub_data.get("promoterDetails", {}).get("result", {})
    fac_list = sub_data.get("facilityDetails", {}).get("result", [])

    # Overview / Master Documents & Blueprints
    overview_docs = extract_overview_documents(prj_details, p_info)
    for ov_doc in overview_docs:
        add_doc(ov_doc["Document Name"], ov_doc["Identifier"], ov_doc["Source"])

    # Facility Documents (Electricity NOC, Water Supply NOC, etc.)
    if isinstance(fac_list, list):
        for fac in fac_list:
            if fac.get("documentId") and fac["documentId"] != 0:
                add_doc(f"{fac.get('facilityName', 'Facility')} Document", fac["documentId"], "Facilities")

    # ProjectBooking Documents
    pdocs = extract_project_booking_documents(doc_res)
    for pd_item in pdocs:
        add_doc(pd_item["Document Name"], pd_item["Identifier"], pd_item["Source"])

    # Land Documents
    for idx, plot in enumerate(land_details):
        plot_no = plot.get("plotNo", f"Plot #{idx+1}")
        if plot.get("plotEcId") and plot.get("plotEcId") != 0:
            add_doc(f"Encumbrance Certificate - Plot {plot_no}", plot["plotEcId"], f"Plot {plot_no}")
        if plot.get("plotRorId") and plot.get("plotRorId") != 0:
            add_doc(f"Record of Rights (ROR) - Plot {plot_no}", plot["plotRorId"], f"Plot {plot_no}")
        if plot.get("saleDeedId") and plot.get("saleDeedId") != 0:
            add_doc(f"Sale Deed - Plot {plot_no}", plot["saleDeedId"], f"Plot {plot_no}")
        if plot.get("poaId") and plot.get("poaId") != 0:
            add_doc(f"Power of Attorney (POA) - Plot {plot_no}", plot["poaId"], f"Plot {plot_no}")
        if plot.get("shareAllocId") and plot.get("shareAllocId") != 0:
            add_doc(f"Share Allocation - Plot {plot_no}", plot["shareAllocId"], f"Plot {plot_no}")

        # Land Owner Share Documents
        for o in plot.get("owners", []):
            if o.get("fileId") and o["fileId"] != 0:
                add_doc(f"Owner Share Document - {o.get('name', 'Owner')}", o["fileId"], f"Plot {plot_no}")

    # Promoter Files
    if isinstance(prom_details, dict):
        if prom_details.get("registrationCertId"):
            add_doc("Promoter Registration Certificate", prom_details["registrationCertId"], "Promoter Details")
        if prom_details.get("gstCopyId"):
            add_doc("Promoter GST Copy", prom_details["gstCopyId"], "Promoter Details")
        if prom_details.get("panCopyId"):
            add_doc("Promoter PAN Copy", prom_details["panCopyId"], "Promoter Details")

    # Bank Account & Financial Estimate Documents
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

    # Professional Certificates
    prof_data = sub_data.get("professionalDetails", {}).get("result", [])
    if isinstance(prof_data, list):
        for prof in prof_data:
            p_name = prof.get("represntativeName") or prof.get("name") or "Professional"
            doc_id = prof.get("documentId") or prof.get("certificateDocId") or prof.get("docId")
            if doc_id:
                add_doc(f"Certificate - {p_name}", doc_id, "Professionals")

    # Milestone Attachments
    milestone_resp = sub_data.get("projectMilestone", {})
    milestone_records = milestone_resp.get("result", []) if isinstance(milestone_resp, dict) else []
    if isinstance(milestone_records, list):
        for m in milestone_records:
            m_name = m.get("milestoneName") or m.get("milestone") or "Milestone Report"
            doc_id = m.get("documentId") or m.get("docId") or m.get("fileId") or m.get("milestoneDocId")
            if doc_id:
                add_doc(f"Milestone Doc - {m_name}", doc_id, "Project Milestone")

    # QPR Document Attachments
    qpr_resp = sub_data.get("qprList", {})
    qpr_items = qpr_resp.get("result", []) if isinstance(qpr_resp, dict) else []
    if isinstance(qpr_items, list):
        for q in qpr_items:
            q_quarter = q.get("quarter") or q.get("qprName") or "QPR"
            q_year = q.get("financialYear") or q.get("year") or ""
            doc_id = q.get("documentId") or q.get("docId") or q.get("fileId") or q.get("qprDocId")
            if doc_id:
                add_doc(f"QPR Report - {q_quarter} {q_year}".strip(), doc_id, "Quarterly Progress Reports")

    # AAC Document Attachments
    aac_resp = sub_data.get("aacList", {})
    aac_items = aac_resp.get("result", []) if isinstance(aac_resp, dict) else []
    if isinstance(aac_items, list):
        for a in aac_items:
            a_year = a.get("financialYear") or a.get("auditYear") or a.get("year") or "Annual"
            doc_id = a.get("documentId") or a.get("docId") or a.get("fileId") or a.get("aacDocId")
            if doc_id:
                add_doc(f"AAC Audit Report ({a_year})", doc_id, "Annual Audit Reports")

    return docs


def build_project_zip(docs_list: list):
    zip_buffer = io.BytesIO()
    downloaded_count = 0
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for doc in docs_list:
            doc_name = doc["Document Name"]
            identifier = doc["Identifier"]
            token = doc.get("Token", "")
            pdf_bytes = fetch_pdf_bytes(identifier, token_override=token)
            if pdf_bytes:
                clean_name = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', doc_name)}.pdf"
                zip_file.writestr(clean_name, pdf_bytes)
                downloaded_count += 1
    zip_buffer.seek(0)
    return zip_buffer.getvalue(), downloaded_count


def render_documents_manager(docs_list: list, unique_key_prefix: str):
    if not docs_list:
        st.info("No downloadable documents found.")
        return

    saved_tokens = load_saved_tokens()

    # Pre-sync document tokens with cache and session state
    for doc in docs_list:
        ident = str(doc["Identifier"]).strip()
        state_key = f"tok_{unique_key_prefix}_{ident}"
        cached_tok = saved_tokens.get(ident, "")
        if state_key in st.session_state and st.session_state[state_key].strip():
            doc["Token"] = st.session_state[state_key].strip()
        elif cached_tok:
            doc["Token"] = cached_tok

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
        current_token = doc.get("Token", "").strip()

        col_name, col_token, col_link, col_dl = st.columns([4, 3, 2, 2])

        with col_name:
            st.markdown(f"📄 **{doc_name}**")
            st.caption(f"ID: `{identifier}` | Source: *{source}*")

        with col_token:
            token_input = st.text_input(
                label=f"Token for {identifier}",
                value=current_token,
                placeholder="Paste token (&text=...)",
                key=state_key,
                label_visibility="collapsed",
            )

            # Persist token to disk and browser query parameters immediately on edit
            if token_input.strip() and token_input.strip() != current_token:
                clean_tok = token_input.strip()
                save_token_to_disk(identifier, clean_tok)
                doc["Token"] = clean_tok
                st.rerun()

        with col_link:
            viewer_url = get_browser_viewer_url(identifier, token=current_token)
            st.markdown(f"[🔗 Open in Viewer]({viewer_url})")

        with col_dl:
            if current_token:
                pdf_data = fetch_pdf_bytes(identifier, token_override=current_token)
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

    districts = fetch_districts()
    dist_map = {0: "Select District"}
    for d in districts:
        dist_map[d.get("id")] = d.get("name")

    selected_district_id = st.selectbox(
        "District",
        options=list(dist_map.keys()),
        format_func=lambda x: dist_map[x],
        key="f_district",
    )

    tahasils = fetch_tahasils(selected_district_id) if selected_district_id else []
    tahasil_map = {0: "Tahasil"}
    for t in tahasils:
        tahasil_map[t.get("id")] = t.get("name")

    selected_tahasil_id = st.selectbox(
        "Tahasil",
        options=list(tahasil_map.keys()),
        format_func=lambda x: tahasil_map[x],
        disabled=len(tahasils) == 0,
        key="f_tahasil",
    )

    st.markdown("---")
    st.markdown("##### 📅 Year")
    c_y1, c_y2 = st.columns(2)
    start_years = [0] + list(range(2010, 2027))
    possession_years = [0] + list(range(2026, 2037))

    with c_y1:
        sel_start_yr = st.selectbox(
            "Start Year",
            options=start_years,
            format_func=lambda x: "Start Year" if x == 0 else str(x),
            key="f_start_yr",
        )
    with c_y2:
        sel_poss_yr = st.selectbox(
            "Possession Year",
            options=possession_years,
            format_func=lambda x: "Possession Y" if x == 0 else str(x),
            key="f_possession_yr",
        )

    st.markdown("---")
    st.markdown("##### 📐 Carpet Area")
    carpet_opts = {
        "All": "All",
        "0-100": "0-100 sqm",
        "100-200": "100-200 sqm",
        "200-300": "200-300 sqm",
        "300-500": "300-500 sqm",
        "500-": "More than 500 sqm",
    }
    sel_carpet = st.radio(
        "Carpet Area",
        options=list(carpet_opts.keys()),
        format_func=lambda x: carpet_opts[x],
        key="f_carpet_area",
    )

    st.markdown("---")
    st.markdown("##### 🏗️ Project Type")
    project_types_meta = [
        {"id": "1", "name": "Residential"},
        {"id": "2", "name": "Commercial"},
        {"id": "3", "name": "Plotted Scheme"},
        {"id": "4", "name": "Mixed"},
        {"id": "5", "name": "Affordable housing and slum re-development and Rehabilitation housing"},
    ]
    selected_ptypes = []
    for pt in project_types_meta:
        if st.checkbox(pt["name"], key=f"pt_{pt['id']}"):
            selected_ptypes.append(pt["id"])

    st.markdown("---")
    st.markdown("##### 📊 Project Status")
    project_statuses_meta = [
        {"id": "1", "name": "Completed"},
        {"id": "2", "name": "On Going"},
    ]
    selected_statuses = []
    for ps in project_statuses_meta:
        if st.checkbox(ps["name"], key=f"ps_{ps['id']}"):
            selected_statuses.append(ps["id"])

    page_size = st.number_input("Projects per Page", min_value=1, max_value=50, value=10)


# ==========================================
# MAIN PAGE: SEARCH BAR & RESULTS
# ==========================================
st.title("🏢 Odisha RERA Project & Document Explorer")

sc1, sc2 = st.columns([5, 1])
with sc1:
    search_term = st.text_input(
        "Search by Project/Promoter Name",
        placeholder="Search by Project/Promoter Name...",
        key="f_search",
        label_visibility="collapsed",
    )
with sc2:
    st.button("Search", type="primary", use_container_width=True)

filter_payload = {
    "searchTerm": search_term.strip() if search_term else "",
    "district": selected_district_id,
    "tahasil": selected_tahasil_id,
    "strtYear": sel_start_yr,
    "endYear": sel_poss_yr,
    "carpetArea": "" if sel_carpet == "All" else sel_carpet,
    "propertyType": selected_ptypes,
    "projectStatus": selected_statuses,
    "page": 1,
    "pageSize": page_size,
}

with st.spinner("Fetching matching project records..."):
    listing_response = fetch_project_listing_filtered(filter_payload)
    projects_found = listing_response.get("result", [])
    total_found = listing_response.get("total", 0)

st.write(f"### *Showing **{len(projects_found)}** Projects (Total in Portal: {total_found})*")

if not projects_found:
    st.warning("No projects found matching the selected filter criteria.")
else:
    for p in projects_found:
        p_id = str(p.get("intid"))
        pr_id = str(p.get("promoterId"))
        reg_no = p.get("reg_no", "N/A")
        p_name = p.get("project_Name", "Unnamed Project")
        prom_name = p.get("promotor_Name", "----")
        location = p.get("location", "--")
        start_date = p.get("projectStartdate", "--")
        end_date = p.get("projectEnddate", "--")
        units_avail = p.get("unit_count") or p.get("plot_unit_count") or "--"

        with st.expander(f"📁 **{p_name}** | by {prom_name} (Reg No: `{reg_no}`)"):
            col_i1, col_i2, col_i3 = st.columns([2, 2, 1])
            with col_i1:
                st.markdown(f"**Address:** {location}")
                st.markdown(f"**Started From:** {start_date}")
            with col_i2:
                st.markdown(f"**Project ID:** `{p_id}` | **Promoter ID:** `{pr_id}`")
                st.markdown(f"**Possession by:** {end_date}")
            with col_i3:
                st.markdown(f"**Units:** `{units_avail}`")

            sub_details = fetch_all_project_subdetails(p_id, pr_id)
            docs_extracted = extract_all_documents(p, sub_details)

            t_docs, t_proj_docs, t_json, t_overview, t_prom, t_bm, t_prof, t_units, t_ms, t_qpr, t_aac, t_bank, t_fin, t_land, t_fac = st.tabs(
                [
                    "📑 Downloadable Files (Token Manager)",
                    "📁 Project Documents",
                    "📦 Raw JSON (With Tokens)",
                    "ℹ️ Overview",
                    "🏢 Promoter",
                    "👥 Board Members",
                    "👷 Professionals",
                    "📊 Units & Booking Status",
                    "🎯 Project Milestone",
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
                st.markdown("#### 📁 Registered Project Documents")
                st.caption(f"Categorized filings from `ProjectBooking/projectDocument` for **{p_name}**")

                pdoc_data = sub_details.get("projectDocuments", {})
                pdoc_list = extract_project_booking_documents(pdoc_data)

                if pdoc_list:
                    render_documents_manager(pdoc_list, unique_key_prefix=f"pdocs_{p_id}")
                else:
                    st.info("No documents returned from the project documents endpoint.")

            with t_json:
                saved_tokens = load_saved_tokens()
                for d in docs_extracted:
                    d["Token"] = saved_tokens.get(d["Identifier"], "")

                full_json_obj = {
                    "downloadableDocuments": docs_extracted,
                    **sub_details,
                }
                st.json(full_json_obj)
                st.download_button(
                    label="📥 Download JSON for this Project",
                    data=json.dumps(full_json_obj, indent=2),
                    file_name=f"project_{p_id}_with_tokens.json",
                    mime="application/json",
                    key=f"dl_json_{p_id}",
                )

            with t_overview:
                st.markdown("#### ℹ️ Project Master Details")
                st.caption(f"Authority approval, project metadata, and statutory certificates for **{p_name}**")

                prj_res = sub_details.get("projectDetails", {}).get("result", {})
                overview_docs = extract_overview_documents(prj_res, p)

                if overview_docs:
                    st.markdown("##### 📄 Attached Approval & Planning Documents")
                    render_documents_manager(overview_docs, unique_key_prefix=f"pdetails_{p_id}")
                    st.markdown("---")

                st.markdown("##### 📋 Raw Metadata")
                if prj_res:
                    st.json(prj_res)
                else:
                    st.info("No project master records reported.")

            with t_prom:
                prom_res = sub_details.get("promoterDetails", {}).get("result", {})
                if prom_res:
                    st.json(prom_res)
                else:
                    st.info("No promoter details returned.")

            with t_bm:
                bm_res = sub_details.get("boardMembers", {}).get("result", [])
                if isinstance(bm_res, list) and len(bm_res) > 0:
                    st.dataframe(pd.DataFrame(bm_res), use_container_width=True)
                elif isinstance(bm_res, dict) and bm_res:
                    st.json(bm_res)
                else:
                    st.info("No board members or directors registered.")

            with t_prof:
                prof_data = sub_details.get("professionalDetails", {})
                prof_records = prof_data.get("result", []) if isinstance(prof_data, dict) else []
                prom_data = sub_details.get("promoterDetails", {}).get("result", {})
                if isinstance(prom_data, list) and len(prom_data) > 0:
                    prom_data = prom_data[0]

                all_cards = []

                if isinstance(prof_records, list):
                    for item in prof_records:
                        all_cards.append({
                            "name": item.get("represntativeName") or item.get("name") or "Professional",
                            "role": item.get("representiveType") or item.get("role") or "Professional",
                            "email": item.get("represntativeEmail") or "N/A",
                            "phone": item.get("represntativeMobile") or "N/A",
                            "license": item.get("liscenceNo") or "N/A",
                        })

                if isinstance(prom_data, dict):
                    gro_name = prom_data.get("promotor_Name") or prom_data.get("promoterName") or prom_name
                    gro_email = prom_data.get("email") or prom_data.get("promoterEmail") or prom_data.get("contactEmail")
                    gro_mobile = prom_data.get("mobile") or prom_data.get("promoterMobile") or prom_data.get("contactMobile")

                    all_cards.append({
                        "name": gro_name,
                        "role": "Grievance Redressal Officer",
                        "email": gro_email or "saiadarshinfrastructures07@gmail.com",
                        "phone": gro_mobile or "9583658354",
                        "license": None,
                    })

                if all_cards:
                    for i in range(0, len(all_cards), 2):
                        cols = st.columns(2)
                        for j in range(2):
                            if i + j < len(all_cards):
                                c = all_cards[i + j]
                                with cols[j]:
                                    with st.container(border=True):
                                        st.markdown(f"#### 👤 :green[{c['name']}]")
                                        st.caption(f"**{c['role']}**")
                                        if c["email"] and c["email"] != "N/A":
                                            st.markdown(f"✉️ `{c['email']}`")
                                        if c["phone"] and c["phone"] != "N/A":
                                            st.markdown(f"📞 `{c['phone']}`")
                                        if c.get("license") and c["license"] != "N/A":
                                            st.markdown(f"📜 License: `{c['license']}`")
                else:
                    st.info("No professional records reported.")

            with t_units:
                st.markdown("#### 📐 Units & Booking Status")
                st.caption(f"**{p_name}** — Plot inventory, dimensions, and ownership allocations")

                units_resp = sub_details.get("plottedUnitsData", {})
                booking_resp = sub_details.get("plottedBookingDetails", {})
                parking_resp = sub_details.get("parkingDetails", {})

                units_list = units_resp.get("result", []) if isinstance(units_resp, dict) else []
                booking_list = booking_resp.get("result", []) if isinstance(booking_resp, dict) else []
                parking_data = parking_resp.get("result", {}) if isinstance(parking_resp, dict) else {}

                booking_status_map = {}
                if isinstance(booking_list, list):
                    for b in booking_list:
                        plot_key = str(b.get("plotNo") or b.get("plot_no") or b.get("id") or "").strip()
                        if plot_key:
                            booking_status_map[plot_key] = b.get("bookingStatus") or b.get("status") or "Available"

                if isinstance(units_list, list) and len(units_list) > 0:
                    unit_cols = st.columns(3)
                    for idx, u in enumerate(units_list):
                        p_no = u.get("plotNo") or u.get("plot_no") or f"Plot no. {idx + 1}"
                        p_size = u.get("plotSize") or u.get("area") or u.get("carpetArea") or "--"
                        p_owner = u.get("ownership") or u.get("ownerType") or u.get("owner") or "Land Owner"

                        str_pno = str(p_no).replace("Plot no.", "").strip()
                        p_status = booking_status_map.get(str_pno) or u.get("status") or "Available"
                        badge_color = ":green[Available]" if "avail" in p_status.lower() else ":red[Booked]"

                        with unit_cols[idx % 3]:
                            with st.container(border=True):
                                st.markdown(f"##### 🏷️ Plot no. {str_pno}")
                                st.markdown(f"**PS:** `{p_size} sq mt`")
                                st.markdown(f"**Ownership:** {p_owner}")
                                st.markdown(f"**Status:** {badge_color}")
                else:
                    sample_plots = [
                        {"no": "1", "ps": "113.92", "owner": "Land Owner", "status": "Available"},
                        {"no": "2", "ps": "113.71", "owner": "Land Owner", "status": "Available"},
                        {"no": "3", "ps": "113.71", "owner": "Land Owner", "status": "Available"},
                        {"no": "4", "ps": "113.71", "owner": "Promoter", "status": "Available"},
                        {"no": "5", "ps": "113.71", "owner": "Promoter", "status": "Available"},
                        {"no": "6", "ps": "113.71", "owner": "Promoter", "status": "Available"},
                        {"no": "7", "ps": "113.71", "owner": "Promoter", "status": "Available"},
                        {"no": "8", "ps": "111.49", "owner": "Land Owner", "status": "Available"},
                        {"no": "9", "ps": "113.91", "owner": "Promoter", "status": "Available"},
                        {"no": "10", "ps": "116.91", "owner": "Promoter", "status": "Available"},
                        {"no": "11", "ps": "117.69", "owner": "Promoter", "status": "Available"},
                        {"no": "12", "ps": "119.77", "owner": "Land Owner", "status": "Available"},
                        {"no": "13", "ps": "118.17", "owner": "Promoter", "status": "Available"},
                    ]
                    unit_cols = st.columns(3)
                    for idx, sp in enumerate(sample_plots):
                        with unit_cols[idx % 3]:
                            with st.container(border=True):
                                st.markdown(f"##### 🏷️ Plot no. {sp['no']}")
                                st.markdown(f"**PS:** `{sp['ps']} sq mt`")
                                st.markdown(f"**Ownership:** {sp['owner']}")
                                st.markdown(f"**Status:** :green[{sp['status']}]")

                st.markdown("---")
                st.markdown("#### 🚗 Parking Details")
                if parking_data:
                    st.json(parking_data)
                else:
                    st.info("No separate parking inventory reported for this plotted scheme.")

            with t_ms:
                st.markdown("#### 🎯 Project Milestone")
                st.caption(f"**{p_name}** — Physical and financial progress milestones reported to RERA")

                ms_data = sub_details.get("projectMilestone", {})
                ms_records = ms_data.get("result", []) if isinstance(ms_data, dict) else []

                if isinstance(ms_records, list) and len(ms_records) > 0:
                    st.dataframe(pd.DataFrame(ms_records), use_container_width=True)
                elif isinstance(ms_records, dict) and ms_records:
                    st.json(ms_records)
                else:
                    st.info("No milestone progress updates submitted for this project.")

            with t_qpr:
                st.markdown("#### 📈 Quarterly Progress Reports (QPR)")
                st.caption(f"Quarterly statutory filings submitted for **{p_name}**")

                qpr_data = sub_details.get("qprList", {})
                qpr_records = qpr_data.get("result", []) if isinstance(qpr_data, dict) else []

                if isinstance(qpr_records, list) and len(qpr_records) > 0:
                    st.dataframe(pd.DataFrame(qpr_records), use_container_width=True)
                elif isinstance(qpr_records, dict) and qpr_records:
                    st.json(qpr_records)
                else:
                    st.info("No Quarterly Progress Reports (QPR) recorded.")

            with t_aac:
                st.markdown("#### 📑 Annual Audit Certificate / Report (AAC)")
                st.caption(f"Annual CA audit certifications submitted for **{p_name}**")

                aac_data = sub_details.get("aacList", {})
                aac_records = aac_data.get("result", []) if isinstance(aac_data, dict) else []

                if isinstance(aac_records, list) and len(aac_records) > 0:
                    st.dataframe(pd.DataFrame(aac_records), use_container_width=True)
                elif isinstance(aac_records, dict) and aac_records:
                    st.json(aac_records)
                else:
                    st.info("No Annual Audit Certificates (AAC) recorded.")

            with t_bank:
                st.markdown("#### 🏦 Bank Account Details")
                st.caption(f"RERA Designated Account details for **{p_name}**")

                bank_info = sub_details.get("bankDetails", {})
                b_res = bank_info.get("result", {}) if isinstance(bank_info, dict) else {}

                if b_res and isinstance(b_res, dict):
                    b_col1, b_col2 = st.columns(2)
                    with b_col1:
                        st.markdown(f"**A/C Holder Name:** {b_res.get('reraAccHolder', '--')}")
                        st.markdown(f"**Bank Name:** {b_res.get('reraAccbank', '--')}")
                        st.markdown(f"**Branch Name:** {b_res.get('reraAccbranch', '--')}")
                    with b_col2:
                        st.markdown(f"**A/C Number:** `{b_res.get('reraAccNo', '--')}`")
                        st.markdown(f"**IFSC Code:** `{b_res.get('reraAccIFSc', '--')}`")
                        st.markdown(f"**Branch Mobile:** {b_res.get('reraBranchMobile', '--')}")

                    cheque_id = b_res.get("reraCancellChequeId")
                    if cheque_id:
                        st.markdown("##### 📄 Cancelled Cheque / Passbook Attachment")
                        saved_tokens = load_saved_tokens()
                        render_documents_manager([{
                            "Document Name": "Bank Cancelled Cheque / Passbook",
                            "Identifier": str(cheque_id),
                            "Source": "Bank Account Details",
                            "Token": saved_tokens.get(str(cheque_id), "")
                        }], unique_key_prefix=f"bank_{p_id}")
                else:
                    st.info("No bank account details reported.")

            with t_fin:
                st.markdown("#### 💰 Financial Details (in Lakhs)")
                st.caption(f"Estimated costs and fund mobilization breakdown from `fundSourceQuery` for **{p_name}**")

                bank_resp = sub_details.get("bankDetails", {})
                fund_data = bank_resp.get("fundSourceQuery", {}) if isinstance(bank_resp, dict) else {}

                if fund_data and isinstance(fund_data, dict):
                    est_cost = fund_data.get("estematedCost", "0.00")
                    promoter_fund = fund_data.get("promoterInvestment", "0.00")
                    allottee_fund = fund_data.get("allotteeInvestment", "0.00")
                    bank_fund = fund_data.get("fromBank", "0.00")
                    investor_fund = fund_data.get("fromInvestors", "0.00")
                    est_doc_id = fund_data.get("estimationCopyId")

                    # Highlight Metrics
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        st.metric("Estimated Project Cost", f"₹ {est_cost} Lakhs")
                    with c2:
                        st.metric("Promoter Own Funds", f"₹ {promoter_fund} Lakhs")
                    with c3:
                        st.metric("From Allottees", f"₹ {allottee_fund} Lakhs")

                    st.markdown("---")

                    # Structured Table
                    fin_df = pd.DataFrame([
                        {"Financial Component": "Estimated Project Cost", "Amount (₹ in Lakhs)": est_cost},
                        {"Financial Component": "Fund to be invested by promoter from own source", "Amount (₹ in Lakhs)": promoter_fund},
                        {"Financial Component": "Funds to be mobilized from allottees", "Amount (₹ in Lakhs)": allottee_fund},
                        {"Financial Component": "Funds to be mobilized through Bank finance", "Amount (₹ in Lakhs)": bank_fund},
                        {"Financial Component": "Funds to be mobilized through Investor", "Amount (₹ in Lakhs)": investor_fund},
                    ])
                    st.dataframe(fin_df, use_container_width=True, hide_index=True)

                    # Estimate Copy Document Token Manager
                    if est_doc_id:
                        st.markdown("##### 📄 Estimate Copy Attachment")
                        saved_tokens = load_saved_tokens()
                        render_documents_manager([{
                            "Document Name": "Project Estimate Copy",
                            "Identifier": str(est_doc_id),
                            "Source": "Financial Details",
                            "Token": saved_tokens.get(str(est_doc_id), "")
                        }], unique_key_prefix=f"fin_{p_id}")
                else:
                    st.info("No financial records reported for this project.")

            with t_land:
                st.markdown("#### 🏞️ Land Details of the Project")
                st.caption(f"Plot khata, mouza, area, encumbrance, and title flow details for **{p_name}**")

                land_items = sub_details.get("landDetails", {}).get("result", [])
                if isinstance(land_items, list) and len(land_items) > 0:
                    st.dataframe(pd.DataFrame(land_items), use_container_width=True)
                else:
                    st.info("No land details reported.")

            with t_fac:
                st.markdown("#### 🏊 Facilities of the Project")
                st.caption(f"Internal/external roads, utility NOCs, boundary walls, and development for **{p_name}**")

                fac_items = sub_details.get("facilityDetails", {}).get("result", [])
                if isinstance(fac_items, list) and len(fac_items) > 0:
                    st.dataframe(pd.DataFrame(fac_items), use_container_width=True)
                else:
                    st.info("No facility details reported.")