"""
Web application for customers without Telegram
"""
import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Request, Form, HTTPException, Query, Depends, status, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import httpx
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import csv
import json
import hashlib
from io import StringIO

# เพิ่มพาธเพื่อให้ import modules จากโปรเจกต์หลัก
sys.path.append(str(Path(__file__).parent.parent))
from modules.sheets_handler import SheetsHandlerOAuth
from modules.gemini_handler import GeminiHandler
from dotenv import load_dotenv

# โหลด .env จาก root project
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "your-very-secret-key-change-it")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_TEAM_CHAT_ID = os.getenv("TELEGRAM_TEAM_CHAT_ID")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([TELEGRAM_BOT_TOKEN, GOOGLE_SHEETS_ID, GEMINI_API_KEY]):
    raise RuntimeError("Missing required environment variables")

ROOT_DIR = Path(__file__).parent.parent

# Lazy initialization
class LazyHandler:
    def __init__(self, factory):
        self._factory = factory
        self._instance = None
    def _get_instance(self):
        if self._instance is None:
            try:
                self._instance = self._factory()
                print(f"Handler initialized: {type(self._instance).__name__}")
            except Exception as e:
                print(f"Warning: {e}")
                raise
        return self._instance
    def reload(self):
        """Force reload the instance"""
        print(f"Reloading {type(self._instance).__name__ if self._instance else 'handler'}...")
        self._instance = None
        return self._get_instance()
    def __getattr__(self, name):
        return getattr(self._get_instance(), name)

def create_sheets_handler():
    return SheetsHandlerOAuth(
        credentials_file=str(ROOT_DIR / "client_secret.json"),
        token_file=str(ROOT_DIR / "token.pickle"),
        sheet_id=GOOGLE_SHEETS_ID,
    )
def create_gemini_handler():
    return GeminiHandler(api_key=GEMINI_API_KEY)

sheets_handler = LazyHandler(create_sheets_handler)
gemini_handler = LazyHandler(create_gemini_handler)

app = FastAPI(title="Helpdesk Customer Portal")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
serializer = URLSafeTimedSerializer(SECRET_KEY)

def create_admin_session() -> str:
    exp_timestamp = int(datetime.now().timestamp()) + 8 * 3600
    return serializer.dumps({"admin": ADMIN_USERNAME, "exp": exp_timestamp})

def verify_admin_session(session_token: Optional[str] = Cookie(None)):
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        data = serializer.loads(session_token, max_age=8*3600)
        if data.get("admin") == ADMIN_USERNAME:
            exp = data.get("exp")
            if exp and isinstance(exp, (int, float)):
                if datetime.now().timestamp() > exp:
                    raise HTTPException(status_code=401, detail="Session expired")
            return True
    except (SignatureExpired, BadSignature):
        pass
    raise HTTPException(status_code=401, detail="Invalid session")

def read_html_file(filename: str) -> str:
    path = Path(__file__).parent / "templates" / filename
    if not path.exists():
        return f"<h1>File {filename} not found</h1>"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

async def notify_team(ticket_id: str, customer_name: str, message: str):
    if not TELEGRAM_TEAM_CHAT_ID:
        return
    text = (f"🆕 มี Ticket ใหม่จากเว็บไซต์\n"
            f"Ticket: <b>{ticket_id}</b>\n"
            f"👤 ผู้แจ้ง: {customer_name}\n"
            f"📝 รายละเอียด: {message[:200]}\n"
            f"🔗 เข้าดูใน Google Sheets")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json={"chat_id": TELEGRAM_TEAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=5.0)
            logger.info(f"Sent notification for {ticket_id}")
        except Exception as e:
            logger.error(f"Notify failed: {e}")

# แก้ไข get_tickets_by_email ให้รองรับ customer_id ที่เป็น int
def get_tickets_by_email(email: str) -> List[dict]:
    all_tickets = sheets_handler.get_all_tickets()
    if not all_tickets:
        return []
    email_clean = email.strip().lower()
    filtered = []
    for t in all_tickets:
        cust_id = t.get("customer_id", "")
        if isinstance(cust_id, (int, float)):
            cust_id = str(cust_id)
        else:
            cust_id = str(cust_id) if cust_id is not None else ""
        if cust_id.strip().lower() == email_clean:
            filtered.append({
                "ticket_id": t.get("ticket_id", ""),
                "status": t.get("status", ""),
                "created_at": t.get("created_at", ""),
                "issue_details": t.get("issue_details", "")[:100],
                "assigned_to": t.get("assigned_to", ""),
            })
    filtered.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return filtered

# ---------- Pydantic Models ----------
class CreateTicketRequest(BaseModel):
    name: str; email: str; message: str
class CancelTicketRequest(BaseModel):
    identifier: str
    ticket_id: Optional[str] = None

class ChatRequest(BaseModel):
    message: str
class ChatResponse(BaseModel):
    response: str

# ---------- User Pages ----------
@app.get("/", response_class=HTMLResponse)
async def home(): return HTMLResponse(content=read_html_file("index.html"))
@app.get("/report", response_class=HTMLResponse)
async def report_page(): return HTMLResponse(content=read_html_file("report.html"))
@app.get("/status", response_class=HTMLResponse)
async def status_page(): return HTMLResponse(content=read_html_file("status.html"))
@app.get("/cancel", response_class=HTMLResponse)
async def cancel_page(): return HTMLResponse(content=read_html_file("cancel.html"))
@app.get("/chat", response_class=HTMLResponse)
async def chat_page(): return HTMLResponse(content=read_html_file("chat.html"))
@app.get("/my-tickets", response_class=HTMLResponse)
async def my_tickets_page(): return HTMLResponse(content=read_html_file("my_tickets.html"))

# ---------- Admin Auth ----------
@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, error: str = "", registered: str = ""):
    content = read_html_file("login.html")
    if error:
        alert = '<script>alert("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"); window.history.replaceState({}, document.title, window.location.pathname);</script>'
        content = content.replace('</body>', f'{alert}</body>')
    if registered:
        alert = '<script>alert("ลงทะเบียนสำเร็จ! กรุณาเข้าสู่ระบบ"); window.history.replaceState({}, document.title, window.location.pathname);</script>'
        content = content.replace('</body>', f'{alert}</body>')
    return HTMLResponse(content=content)

@app.post("/admin/login")
async def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    # Check old admin from environment
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = create_admin_session()
        resp = RedirectResponse(url="/admin", status_code=303)
        resp.set_cookie("session_token", token, httponly=True, max_age=8*3600)
        return resp
    
    # Check new admins from database
    admins = load_admins()
    hashed_password = hashlib.sha256(password.encode()).hexdigest()
    
    for admin_id, admin in admins.items():
        if admin.get("username") == username and admin.get("password") == hashed_password:
            token = create_admin_session()
            resp = RedirectResponse(url="/admin", status_code=303)
            resp.set_cookie("session_token", token, httponly=True, max_age=8*3600)
            return resp
    
    return RedirectResponse(url="/admin/login?error=1", status_code=303)

@app.get("/admin/logout")
async def admin_logout():
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie("session_token")
    return resp

# ---------- Admin Registration ----------
ADMIN_DB = Path(__file__).parent.parent / "credentials" / "admins.json"

def load_admins() -> dict:
    if ADMIN_DB.exists():
        with open(ADMIN_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_admins(admins: dict):
    ADMIN_DB.parent.mkdir(parents=True, exist_ok=True)
    with open(ADMIN_DB, "w", encoding="utf-8") as f:
        json.dump(admins, f, ensure_ascii=False, indent=2)

@app.get("/admin/register", response_class=HTMLResponse)
async def admin_register_page(request: Request):
    return HTMLResponse(content=read_html_file("register.html"))

@app.post("/admin/register")
async def admin_register(request: Request, email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...), username: str = Form(...)):
    if password != confirm_password:
        return HTMLResponse(content=read_html_file("register.html") + '<script>alert("รหัสผ่านไม่ตรงกัน");</script>')
    
    if len(password) < 6:
        return HTMLResponse(content=read_html_file("register.html") + '<script>alert("รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร");</script>')
    
    admins = load_admins()
    
    # Check if email already exists
    for admin in admins.values():
        if admin.get("email") == email:
            return HTMLResponse(content=read_html_file("register.html") + '<script>alert("อีเมลนี้มีผู้ใช้งานแล้ว");</script>')
    
    # Create new admin
    hashed_password = hashlib.sha256(password.encode()).hexdigest()
    
    admin_id = str(len(admins) + 1)
    admins[admin_id] = {
        "username": username,
        "email": email,
        "password": hashed_password,
        "created_at": datetime.now().isoformat()
    }
    
    save_admins(admins)
    
    return RedirectResponse(url="/admin/login?registered=1", status_code=303)

# หน้า Admin พร้อมรองรับ filter จาก query string
@app.get("/admin")
async def admin_dashboard(request: Request, session_token: Optional[str] = Cookie(None),
                          filter: str = None, date: str = None):
    try:
        if session_token:
            data = serializer.loads(session_token, max_age=8*3600)
            if data.get("admin") == ADMIN_USERNAME:
                content = read_html_file("admin.html")
                # ฝังค่า filter/date ลงใน HTML เพื่อให้ JavaScript รู้
                filter_html = ""
                if filter == 'not_completed':
                    filter_html = '<input type="hidden" id="initFilter" value="open,in_progress,resolved">'
                elif filter == 'completed':
                    filter_html = '<input type="hidden" id="initFilter" value="closed">'
                if date == 'today':
                    filter_html += f'<input type="hidden" id="initDate" value="{datetime.now().strftime("%Y-%m-%d")}">'
                elif date and date != 'today':
                    filter_html += f'<input type="hidden" id="initDate" value="{date}">'
                # แทรก hidden input ก่อน </body>
                content = content.replace('<!-- FILTER_PLACEHOLDER -->', filter_html)
                return HTMLResponse(content=content)
    except (SignatureExpired, BadSignature):
        pass
    return RedirectResponse(url="/admin/login", status_code=303)

# แยกหน้าเฉพาะสำหรับ filter (เพื่อให้ URL สวยงาม)
@app.get("/admin/not-completed")
async def admin_not_completed(): return RedirectResponse(url="/admin?filter=not_completed", status_code=303)
@app.get("/admin/completed")
async def admin_completed(): return RedirectResponse(url="/admin?filter=completed", status_code=303)
@app.get("/admin/today")
async def admin_today(): return RedirectResponse(url="/admin?date=today", status_code=303)

# ---------- Admin API ----------
@app.get("/admin/api/tickets")
async def admin_get_all_tickets(_ = Depends(verify_admin_session),
                                status: str = None, date: str = None):
    all_tickets = sheets_handler.get_all_tickets()
    if status:
        status_list = [s.strip() for s in status.split(',')]
        all_tickets = [t for t in all_tickets if t.get('status') in status_list]
    if date:
        all_tickets = [t for t in all_tickets if (t.get('created_at') or '').startswith(date)]
    return {"tickets": all_tickets}

@app.put("/admin/api/tickets/{ticket_id}/status")
async def admin_update_status(ticket_id: str, status_data: dict, _ = Depends(verify_admin_session)):
    result = sheets_handler.search_ticket(ticket_id)
    if not result:
        raise HTTPException(404, "Ticket not found")
    row = result.get("row") if isinstance(result, dict) else result.get("row")
    new_status = status_data.get("status")
    note = status_data.get("note", "")
    success = sheets_handler.update_ticket_status(row, new_status, note)
    if not success:
        raise HTTPException(500, "Update failed")
    return {"success": True}

@app.post("/admin/api/tickets/{ticket_id}/assign")
async def admin_assign_ticket(ticket_id: str, assign_data: dict, _ = Depends(verify_admin_session)):
    assignee = assign_data.get("assignee")
    if not assignee:
        raise HTTPException(400, "Missing assignee")
    success = sheets_handler.update_assignee(ticket_id, assignee)
    if not success:
        raise HTTPException(404, "Ticket not found")
    return {"success": True}

@app.get("/admin/api/tickets/export")
async def admin_export_tickets(_ = Depends(verify_admin_session),
                               period: str = "all", format: str = "csv",
                               start_date: str = None, end_date: str = None,
                               status: str = None, date: str = None):
    all_tickets = sheets_handler.get_all_tickets()
    filtered = all_tickets[:]
    if status:
        sset = set(status.split(","))
        filtered = [t for t in filtered if t.get("status") in sset]
    if date:
        filtered = [t for t in filtered if (t.get("created_at") or "").startswith(date)]
    elif period == "today":
        today = datetime.now().strftime("%Y-%m-%d")
        filtered = [t for t in filtered if (t.get("created_at") or "").startswith(today)]
    elif period == "this_month":
        month = datetime.now().strftime("%Y-%m")
        filtered = [t for t in filtered if (t.get("created_at") or "").startswith(month)]
    elif period == "this_year":
        year = datetime.now().strftime("%Y")
        filtered = [t for t in filtered if (t.get("created_at") or "").startswith(year)]
    elif period == "custom" and start_date and end_date:
        filtered = [t for t in filtered if start_date <= (t.get("created_at") or "")[:10] <= end_date]
    fieldnames = ["ticket_id", "customer_name", "customer_id", "issue_details", "status", "assigned_to", "created_at", "updated_at", "last_message"]
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for t in filtered:
        row = {f: t.get(f, "") for f in fieldnames}
        writer.writerow(row)
    output.seek(0)
    ext = "csv" if format == "csv" else "txt"
    filename = f"tickets_export_{period}.{ext}"
    return StreamingResponse(iter([output.getvalue().encode('utf-8-sig')]),
                             media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})

# ---------- User API ----------
@app.post("/api/tickets")
async def create_ticket(req: CreateTicketRequest):
    if not req.name or not req.email or not req.message:
        raise HTTPException(400, "กรุณากรอกข้อมูลให้ครบถ้วน")
    try:
        ticket_id = sheets_handler.add_ticket(user_id=req.email, username=req.name, message=req.message)
        await notify_team(ticket_id, req.name, req.message)
        return {"success": True, "ticket_id": ticket_id}
    except Exception as e:
        logger.error(f"Create ticket failed: {e}")
        raise HTTPException(500, "ไม่สามารถสร้าง Ticket ได้")

@app.get("/api/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    result = sheets_handler.search_ticket(ticket_id)
    if not result:
        raise HTTPException(404, "ไม่พบ Ticket นี้")
    data = result.get("data") if isinstance(result, dict) else result
    return {
        "ticket_id": data.get("ticket_id", ""),
        "customer_name": data.get("customer_name", ""),
        "issue_details": data.get("issue_details", ""),
        "status": data.get("status", ""),
        "assigned_to": data.get("assigned_to", ""),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
        "last_message": data.get("last_message", ""),
    }

def _normalize_identifier(value: str) -> str:
    if value is None:
        return ''
    if isinstance(value, (int, float)):
        value = str(int(value))
    return str(value).strip().lower()

@app.post("/api/tickets/{ticket_id}/cancel")
async def cancel_ticket(ticket_id: str, req: CancelTicketRequest):
    result = sheets_handler.search_ticket(ticket_id)
    if not result and ticket_id.isdigit():
        result = sheets_handler.search_ticket_by_customer(ticket_id)
    if not result:
        raise HTTPException(404, "ไม่พบ Ticket นี้")
    data = result.get("data") if isinstance(result, dict) else result
    customer_id = _normalize_identifier(data.get("customer_id", ""))
    identifier = _normalize_identifier(req.identifier)
    if customer_id != identifier:
        raise HTTPException(403, "อีเมลหรือรหัสไม่ตรงกับผู้แจ้ง Ticket นี้")
    row = result.get("row") if isinstance(result, dict) else result.get("row")
    success = sheets_handler.update_ticket_status(row, "cancelled", "ยกเลิกโดยลูกค้า")
    if not success:
        raise HTTPException(500, "ไม่สามารถยกเลิก Ticket ได้")
    return {"success": True, "message": f"ยกเลิก Ticket {ticket_id} เรียบร้อยแล้ว"}

@app.post("/api/tickets/cancel")
async def cancel_ticket_by_identifier(req: CancelTicketRequest):
    if not req.identifier:
        raise HTTPException(400, "กรุณากรอกอีเมลหรือ ID ที่ใช้แจ้งปัญหา")
    result = None
    if req.ticket_id:
        result = sheets_handler.search_ticket(req.ticket_id)
        if not result and req.ticket_id.isdigit():
            result = sheets_handler.search_ticket_by_customer(req.ticket_id)
    if not result:
        result = sheets_handler.search_ticket_by_customer(req.identifier)
    if not result:
        raise HTTPException(404, "ไม่พบ Ticket นี้")
    data = result.get("data") if isinstance(result, dict) else result
    customer_id = _normalize_identifier(data.get("customer_id", ""))
    identifier = _normalize_identifier(req.identifier)
    if customer_id != identifier:
        raise HTTPException(403, "อีเมลหรือรหัสไม่ตรงกับผู้แจ้ง Ticket นี้")
    row = result.get("row") if isinstance(result, dict) else result.get("row")
    ticket_id_found = data.get("ticket_id", req.ticket_id or "")
    success = sheets_handler.update_ticket_status(row, "cancelled", "ยกเลิกโดยลูกค้า")
    if not success:
        raise HTTPException(500, "ไม่สามารถยกเลิก Ticket ได้")
    return {"success": True, "message": f"ยกเลิก Ticket {ticket_id_found} เรียบร้อยแล้ว"}

@app.get("/api/tickets")
async def list_tickets(email: str = Query(...)):
    if not email:
        raise HTTPException(400, "กรุณาระบุอีเมล")
    tickets = get_tickets_by_email(email)
    return {"tickets": tickets}

@app.post("/api/chat", response_model=ChatResponse)
async def chat_with_ai(req: ChatRequest):
    if not req.message:
        raise HTTPException(400, "กรุณาพิมพ์ข้อความ")
    try:
        # Force reload Gemini handler to get latest code changes during development
        gemini_handler.reload()
        response = gemini_handler.get_response(req.message)
        return ChatResponse(response=response)
    except Exception as e:
        logger.error(f"AI error: {e}")
        raise HTTPException(500, "AI ไม่สามารถตอบกลับได้ในขณะนี้")

if __name__ == "__main__":
    try:
        import uvicorn
        print("Starting Xovic Helpdesk Web App...")
        print(f"Server will run on http://localhost:8000")
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except Exception as e:
        print(f"Error starting server: {e}")
        import traceback
        traceback.print_exc()