import base64
import hashlib
import hmac
import json
import os
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel

load_dotenv()

app = FastAPI(
    title="Odisha RERA Gateway",
    description="Clean abstraction layer concealing upstream government endpoints and signatures.",
    version="1.0.0",
)

API_HASHING_KEY = os.getenv("API_HASHING_KEY")
BASE_URL = os.getenv("BASE_URL", "https://reraapps.odisha.gov.in").rstrip("/")
ODISHA_STATE_ID = int(os.getenv("ODISHA_STATE_ID", 21))

if not API_HASHING_KEY:
    raise RuntimeError("API_HASHING_KEY must be configured in your .env file.")


def _create_signed_payload(data) -> dict:
    if isinstance(data, (dict, list)):
        json_str = json.dumps(data, separators=(",", ":"))
    else:
        json_str = str(data)
    encoded = base64.b64encode(json_str.encode("utf-8")).decode("utf-8")
    token = hmac.new(
        API_HASHING_KEY.encode("utf-8"),
        encoded.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"REQUEST_DATA": encoded, "REQUEST_TOKEN": token}


def _decode_response(res_json: dict):
    if isinstance(res_json, dict) and "RESPONSE_DATA" in res_json:
        decoded_raw = base64.b64decode(res_json["RESPONSE_DATA"]).decode("utf-8")
        try:
            return json.loads(decoded_raw)
        except Exception:
            return decoded_raw
    return res_json


def _forward_post(endpoint: str, data) -> dict:
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    auth_meta = _create_signed_payload({"USER_AUTHKEY": "", "USER_ID": "", "USER_TYPE": "2"})
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://rera.odisha.gov.in",
        "Referer": "https://rera.odisha.gov.in/",
        "Authorization": json.dumps(auth_meta, separators=(",", ":")),
    }
    try:
        res = requests.post(url, headers=headers, json=_create_signed_payload(data), timeout=30)
        if res.status_code == 200:
            return _decode_response(res.json())
        raise HTTPException(status_code=res.status_code, detail="Upstream server error")
    except requests.exceptions.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed connecting to RERA portal: {exc}")


class ProjectSearchQuery(BaseModel):
    search_term: str = ""
    district: int = 0
    tahasil: int = 0
    start_year: int = 0
    possession_year: int = 0
    carpet_area: str = ""
    property_types: list[str] = []
    project_statuses: list[str] = []
    page: int = 1
    page_size: int = 10


# --- Demography ---

@app.get("/api/v1/demography/districts")
def get_districts():
    return _forward_post("pms/api/master/Demography/getDistrict", ODISHA_STATE_ID)


@app.get("/api/v1/demography/tahasils/{district_id}")
def get_tahasils(district_id: int):
    return _forward_post("pms/api/master/Demography/getTahasil", district_id)


# --- Search & Master Projects ---

@app.post("/api/v1/projects/search")
def search_projects(params: ProjectSearchQuery):
    payload = {
        "searchTerm": params.search_term.strip(),
        "district": params.district,
        "tahasil": params.tahasil,
        "strtYear": params.start_year,
        "endYear": params.possession_year,
        "carpetArea": params.carpet_area,
        "propertyType": params.property_types,
        "projectStatus": params.project_statuses,
        "latitude": "",
        "longitude": "",
        "radius": "",
        "approvedStatus": False,
        "revokedStatus": False,
        "page": params.page,
        "pageSize": params.page_size,
        "sortOrder": "asc",
    }
    return _forward_post("pms/api/master/Projects/projectListing", payload)


# --- Sub-detail Endpoints ---

@app.get("/api/v1/projects/{project_id}/details")
def get_project_details(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectOverview/projectDetails", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/facilities")
def get_facility_details(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectOverview/facilityDetails", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/land")
def get_land_details(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectOverview/landDetails", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/promoter")
def get_promoter_details(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectOverview/promoterDetails", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/board-members")
def get_board_members(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectOverview/getBoardMemberDetails", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/bank-and-financials")
def get_bank_and_financials(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectOverview/getBankAccountDetails", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/documents")
def get_project_documents(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectBooking/projectDocument", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/professionals")
def get_professional_details(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectBooking/professinalDetails", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/plotted-units")
def get_plotted_units(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectPreviewDetails/getPlottedUnitsData", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/bookings")
def get_plotted_bookings(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectPreviewDetails/getProjectPlottedBookingDetails", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/parking")
def get_parking_details(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/ProjectPreviewDetails/getParkingDetails", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/milestones")
def get_milestones(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/project/projectMilestoneCitizen/projectMilestoneCitizenView", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/qpr")
def get_qpr_reports(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/qpr/QprList/getQPRPromoterList", {"projectId": project_id, "promoterId": promoter_id})


@app.get("/api/v1/projects/{project_id}/aac")
def get_aac_reports(project_id: str, promoter_id: str = Query(...)):
    return _forward_post("pms/api/qpr/Aacannualreport/getAACList", {"projectId": project_id, "promoterId": promoter_id})


# --- Document Decryption & Streaming ---

@app.get("/api/v1/documents/{file_id}/download")
def download_pdf(file_id: str, token: str = Query(...)):
    clean_id = file_id.strip()
    clean_tok = token.strip()
    if not clean_id.isdigit():
        raise HTTPException(status_code=400, detail="Invalid Document Identifier")

    decrypt_url = f"{BASE_URL}/dms/fileDecryptHandlerForPdfPublic"
    dms_headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/dms/public/library/pdfjsnewds/web/viewer.html?fileId={clean_id}&text={clean_tok}",
        "Authorization": f"bearer {clean_tok}",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        res = requests.post(decrypt_url, headers=dms_headers, data={"fileId": clean_id, "token": clean_tok}, timeout=25)
        if res.status_code == 200:
            dec_data = _decode_response(res.json())
            pdf_path = (
                dec_data.get("result", {}).get("filePath")
                if isinstance(dec_data.get("result"), dict)
                else dec_data.get("result") or dec_data.get("filePath")
            )
            if pdf_path and isinstance(pdf_path, str):
                pdf_url = pdf_path if pdf_path.startswith("http") else f"{BASE_URL}/{pdf_path.lstrip('/')}"
                pdf_res = requests.get(pdf_url, headers=dms_headers, timeout=30)
                if pdf_res.status_code == 200 and pdf_res.content.startswith(b"%PDF"):
                    return Response(content=pdf_res.content, media_type="application/pdf")
        raise HTTPException(status_code=401, detail="Invalid token or decryption failed")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))