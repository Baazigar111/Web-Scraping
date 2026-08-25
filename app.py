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
import streamlit as st

st.set_page_config(
    page_title="Odisha RERA Filter & Project Explorer",
    page_icon="🏢",
    layout="wide",
)
from dotenv import load_dotenv

# Load local .env file if it exists
load_dotenv()

API_HASHING_KEY = os.getenv("API_HASHING_KEY")
BASE_URL = os.getenv("BASE_URL")
ODISHA_STATE_ID = int(os.getenv("ODISHA_STATE_ID"))
TOKEN_STORE_FILE = os.getenv("TOKEN_STORE_FILE")

# Static Fallbacks matching the portal
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


# --- Token Cache Helpers --- #
def load_saved_tokens() -> dict:
    if os.path.exists(TOKEN_STORE_FILE):
        try:
            with open(TOKEN_STORE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_token_to_disk(identifier: str, token: str):
    tokens = load_saved_tokens()
    tokens[str(identifier).strip()] = token.strip()
    with open(TOKEN_STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)


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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
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
        res = requests.post(url, headers=headers, json=payload, timeout=15)
        if res.status_code == 200:
            return decode_response(res.json())
    except Exception as e:
        st.error(f"Error accessing {endpoint}: {e}")
    return {}


# --- Demography Handlers --- #
@st.cache_data(show_spinner=False)
def fetch_districts():
    res = post_pms_api("pms/api/master/Demography/getDistrict", ODISHA_STATE_ID)
    if isinstance(res, list) and len(res) > 0:
        return res
    if isinstance(res, dict) and "result" in res:
        return res["result"]
    return STATIC_DISTRICTS


@st.cache_data(show_spinner=False)
def fetch_tahasils(district_id: int):
    if not district_id:
        return []
    res = post_pms_api("pms/api/master/Demography/getTahasil", int(district_id))
    if isinstance(res, list):
        return res
    if isinstance(res, dict) and "result" in res:
        return res["result"]
    return []


# --- PDF Downloader & Decryptor --- #
@st.cache_data(show_spinner=False)
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
            res = requests.post(decrypt_url, headers=dms_headers, data={"fileId": str_val, "token": token}, timeout=12)
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
                    pdf_res = requests.get(pdf_url, headers=dms_headers, timeout=20)
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
        "searchTerm": filters.get("searchTerm", ""),
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
        "projectDocuments": post_pms_api("pms/api/project/ProjectBooking/projectDocument", lookup_payload),
        "promoterDetails": post_pms_api("pms/api/project/ProjectOverview/promoterDetails", lookup_payload),
    }


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

    cert_id = p_info.get("certificateCopyId") or prj_details.get("certificateCopyId")
    if cert_id:
        add_doc("Registration Certificate", cert_id, "Project Master")

    for doc in doc_res.get("result", []):
        add_doc(doc.get("documentName", "Project Document"), doc.get("documentId"), "Project Documents")

    for doc in doc_res.get("financeDocument", []):
        add_doc(doc.get("documentName", "Financial Document"), doc.get("documentId"), "Financial Documents")

    for doc in doc_res.get("projectDocument", []):
        add_doc(doc.get("docName", "Legal Document"), doc.get("documentID"), "Legal Documents")

    afs = doc_res.get("afsDocuments", {})
    if afs:
        add_doc("Agreement for Sale (Schedule A)", afs.get("scheduleA_Id") or afs.get("scheduleA"), "Agreement for Sale")
        add_doc("Agreement for Sale (Schedule B)", afs.get("scheduleB_Id") or afs.get("scheduleB"), "Agreement for Sale")
        add_doc("Agreement for Sale (Schedule C)", afs.get("scheduleC_Id") or afs.get("scheduleC"), "Agreement for Sale")

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

    if prom_details.get("registrationCertId"):
        add_doc("Promoter Registration Certificate", prom_details["registrationCertId"], "Promoter Details")
    if prom_details.get("gstCopyId"):
        add_doc("Promoter GST Copy", prom_details["gstCopyId"], "Promoter Details")
    if prom_details.get("panCopyId"):
        add_doc("Promoter PAN Copy", prom_details["panCopyId"], "Promoter Details")

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
    ready_count = sum(1 for d in docs_list if d.get("Token", "").strip())

    col_h1, col_h2 = st.columns([3, 1])
    with col_h1:
        st.markdown(f"**Total Documents:** {len(docs_list)} | **Tokens Active:** {ready_count}/{len(docs_list)}")
    with col_h2:
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
        persisted_token = saved_tokens.get(identifier, doc.get("Token", ""))

        col_name, col_token, col_link, col_dl = st.columns([4, 3, 2, 2])

        with col_name:
            st.markdown(f"📄 **{doc_name}**")
            st.caption(f"ID: `{identifier}` | Source: *{source}*")

        with col_token:
            token_input = st.text_input(
                label=f"Token for {identifier}",
                value=persisted_token,
                placeholder="Paste token (&text=...)",
                key=f"tok_{unique_key_prefix}_{identifier}_{idx}",
                label_visibility="collapsed",
            )
            if token_input != persisted_token:
                save_token_to_disk(identifier, token_input)
                doc["Token"] = token_input.strip()
                st.rerun()

        with col_link:
            viewer_url = get_browser_viewer_url(identifier, token=persisted_token)
            st.markdown(f"[🔗 Open in Viewer]({viewer_url})")

        with col_dl:
            if persisted_token:
                pdf_data = fetch_pdf_bytes(identifier, token_override=persisted_token)
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
                    st.error("Invalid token")
            else:
                st.caption("🔒 Paste Token")
        st.divider()


# ==========================================
# SIDEBAR FILTERS (EXACT PORTAL STRUCTURE)
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

# Search Bar
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

# Build Query Payload
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

            # Load project sub-details
            sub_details = fetch_all_project_subdetails(p_id, pr_id)
            docs_extracted = extract_all_documents(p, sub_details)

            t_docs, t_json, t_overview, t_land, t_fac = st.tabs(
                [
                    "📑 Downloadable Documents",
                    "📦 Raw JSON (With Tokens)",
                    "ℹ️ Project Details",
                    "🏞️ Land Details",
                    "🏊 Facilities",
                ]
            )

            with t_docs:
                render_documents_manager(docs_extracted, unique_key_prefix=f"p_{p_id}")

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
                st.json(sub_details.get("projectDetails", {}).get("result", {}))

            with t_land:
                st.dataframe(pd.DataFrame(sub_details.get("landDetails", {}).get("result", [])), use_container_width=True)

            with t_fac:
                st.json(sub_details.get("facilityDetails", {}).get("result", []))