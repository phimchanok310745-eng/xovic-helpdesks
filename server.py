"""
XOVIC Helpdesk - FastAPI Web Server
เชื่อม HTML templates กับ Google Sheets และ Gemini AI
"""
import os
import json
import hashlib
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Pydantic Models (request body)
# ─────────────────────────────────────────────

class TicketCreate(BaseModel):
    name: str
    email: str
    message: str

class TicketStatusUpdate(BaseModel):
    status: str
    note: str = ""

class CancelRequest(BaseModel):
    identifier: str
    ticket_id: Optional[str] = None

class ChatRequest(BaseModel):
    message: str

class AssignRequest(BaseModel):
    assignee: str

class AdminLogin(BaseModel):
    username: str = ""
    password: str = ""


# ─────────────────────────────────────────────
# Session helper (simple cookie-based)
# ─────────────────────────────────────────────

ADMIN_COOKIE = "xovic_admin_session"
ADMINS_FILE = Path("admins.json")


def _load_admins() -> dict:
    try:
        with open(ADMINS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_admins(data: dict):
    with open(ADMINS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _verify_admin(request: Request) -> bool:
    return request.cookies.get(ADMIN_COOKIE) == "authenticated"


def require_admin(request: Request):
    if not _verify_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ─────────────────────────────────────────────
# App Factory
# ─────────────────────────────────────────────

def create_app(sheets_handler, gemini_handler) -> FastAPI:
    app = FastAPI(title="XOVIC Helpdesk", version="1.0.0")

    templates = Jinja2Templates(directory="web_app/templates")

    # Mount static files ถ้ามีโฟลเดอร์ static/
    static_path = Path("static")
    if static_path.exists():
        app.mount("/static", StaticFiles(directory="static"), name="static")

    # ── helper ──────────────────────────────────
    def _html(name: str, request: Request, **ctx):
        return templates.TemplateResponse(request=request, name=name, context=ctx)

    def _ticket_response(ticket_data: dict) -> dict:
        """Normalize ticket dict สำหรับส่ง JSON กลับ"""
        return {
            "ticket_id":     ticket_data.get("ticket_id", ""),
            "customer_id":   ticket_data.get("customer_id", ""),
            "customer_name": ticket_data.get("customer_name", ""),
            "issue_details": ticket_data.get("issue_details", ""),
            "status":        ticket_data.get("status", "open"),
            "priority":      ticket_data.get("priority", "ปานกลาง"),
            "created_at":    ticket_data.get("created_at", ""),
            "updated_at":    ticket_data.get("updated_at", ""),
            "assigned_to":   ticket_data.get("assigned_to", ""),
            "last_message":  ticket_data.get("last_message", ""),
        }

    # ────────────────────────────────────────────
    # PUBLIC PAGES
    # ────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return _html("index.html", request)

    @app.get("/report", response_class=HTMLResponse)
    async def report_page(request: Request):
        return _html("report.html", request)

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(request: Request):
        return _html("status.html", request)

    @app.get("/cancel", response_class=HTMLResponse)
    async def cancel_page(request: Request):
        return _html("cancel.html", request)

    @app.get("/my-tickets", response_class=HTMLResponse)
    async def my_tickets_page(request: Request):
        return _html("my_tickets.html", request)

    @app.get("/chat", response_class=HTMLResponse)
    async def chat_page(request: Request):
        return _html("chat.html", request)

    # ────────────────────────────────────────────
    # PUBLIC API — Tickets
    # ────────────────────────────────────────────

    @app.post("/api/tickets")
    async def create_ticket(body: TicketCreate):
        """สร้าง Ticket ใหม่จากหน้า report.html"""
        try:
            ticket_id = sheets_handler.add_ticket(
                user_id=body.email,
                username=body.name,
                message=body.message,
            )
            return {"ticket_id": ticket_id, "status": "open"}
        except Exception as e:
            logger.error(f"create_ticket error: {e}")
            raise HTTPException(status_code=500, detail="ไม่สามารถสร้าง Ticket ได้")

    @app.get("/api/tickets/{ticket_id}")
    async def get_ticket(ticket_id: str):
        """ดึงข้อมูล Ticket ตาม ID"""
        result = sheets_handler.search_ticket(ticket_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"ไม่พบ Ticket {ticket_id}")
        return _ticket_response(result["data"])

    @app.get("/api/tickets")
    async def get_tickets_by_email(email: str):
        """ดึง Ticket ทั้งหมดของ email"""
        all_tickets = sheets_handler.get_all_tickets()
        matched = [
            _ticket_response(t)
            for t in all_tickets
            if t.get("customer_id", "").lower() == email.lower()
        ]
        return {"tickets": matched}

    @app.post("/api/tickets/cancel")
    async def cancel_ticket(body: CancelRequest):
        """ยกเลิก Ticket"""
        ticket = None
        if body.ticket_id:
            ticket = sheets_handler.search_ticket(body.ticket_id)
        if not ticket:
            ticket = sheets_handler.search_ticket_by_customer(body.identifier)
        if not ticket:
            raise HTTPException(status_code=404, detail="ไม่พบ Ticket ที่ต้องการยกเลิก")

        tid = ticket["data"].get("ticket_id", "")
        sheets_handler.update_ticket_status(ticket["row"], "cancelled", "ยกเลิกโดยลูกค้า (Web)")
        return {"message": f"ยกเลิก Ticket {tid} เรียบร้อยแล้ว"}

    # ────────────────────────────────────────────
    # PUBLIC API — AI Chat
    # ────────────────────────────────────────────

    @app.post("/api/chat")
    async def chat(body: ChatRequest):
        """แชทกับ Gemini AI"""
        try:
            response = await gemini_handler.get_response_async(body.message)
            return {"response": response}
        except Exception as e:
            logger.error(f"chat error: {e}")
            raise HTTPException(status_code=500, detail="AI ไม่สามารถตอบได้ขณะนี้")

    # ────────────────────────────────────────────
    # ADMIN PAGES
    # ────────────────────────────────────────────

    @app.get("/admin/login", response_class=HTMLResponse)
    async def admin_login_page(request: Request):
        return _html("login.html", request)

    @app.post("/admin/login")
    async def admin_login(request: Request):
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")
        admins = _load_admins()
        hashed = _hash_password(password)
        valid = any(
            a.get("username") == username and a.get("password") == hashed
            for a in admins.values()
        )
        if not valid:
            return _html("login.html", request, error="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
        response = RedirectResponse(url="/admin", status_code=302)
        response.set_cookie(ADMIN_COOKIE, "authenticated", httponly=True, samesite="lax")
        return response

    @app.get("/admin/logout")
    async def admin_logout():
        response = RedirectResponse(url="/admin/login", status_code=302)
        response.delete_cookie(ADMIN_COOKIE)
        return response

    @app.get("/admin/register", response_class=HTMLResponse)
    async def admin_register_page(request: Request):
        return _html("register.html", request)

    @app.post("/admin/register")
    async def admin_register(request: Request):
        form = await request.form()
        email = form.get("email", "")
        password = form.get("password", "")
        confirm = form.get("confirm_password", "")
        username = form.get("username", "")
        if password != confirm:
            return _html("register.html", request, error="รหัสผ่านไม่ตรงกัน")
        admins = _load_admins()
        # ตรวจสอบ username ซ้ำ
        if any(a.get("username") == username for a in admins.values()):
            return _html("register.html", request, error="Username นี้มีอยู่แล้ว")
        new_id = str(len(admins) + 1)
        admins[new_id] = {
            "username": username,
            "email": email,
            "password": _hash_password(password),
            "created_at": datetime.now().isoformat(),
        }
        _save_admins(admins)
        response = RedirectResponse(url="/admin/login", status_code=302)
        return response

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_dashboard(
        request: Request,
        filter: Optional[str] = None,
        date: Optional[str] = None          # เปลี่ยนจาก date_param เป็น date
    ):
        if not _verify_admin(request):
            return RedirectResponse(url="/admin/login", status_code=302)
        
        init_filter = ""
        init_date = ""
        
        if filter == "not_completed":
            init_filter = "open,in_progress,resolved"
        elif filter == "completed":
            init_filter = "closed"
        
        # ใช้ date (ไม่ใช่ date_param)
        if date == "today":
            init_date = datetime.now().date().isoformat()
        elif date:
            init_date = date
        
        logger.info(f"Admin dashboard: filter={filter}, date={date}, init_filter={init_filter}, init_date={init_date}")
        
        return _html("admin.html", request, init_filter=init_filter, init_date=init_date)
    # routes ตัวช่วย redirect ไปยัง /admin พร้อม query string
    @app.get("/admin/not-completed")
    async def admin_not_completed():
        return RedirectResponse(url="/admin?filter=not_completed", status_code=302)

    @app.get("/admin/completed")
    async def admin_completed():
        return RedirectResponse(url="/admin?filter=completed", status_code=302)

    @app.get("/admin/today")
    async def admin_today():
        return RedirectResponse(url="/admin?date=today", status_code=302)

    # ────────────────────────────────────────────
    # ADMIN API — Tickets
    # ────────────────────────────────────────────

    @app.get("/admin/api/tickets")
    async def admin_list_tickets(
        request: Request,
        status: Optional[str] = None,
        date: Optional[str] = None,
    ):
        require_admin(request)
        all_tickets = sheets_handler.get_all_tickets()
        result = []
        for t in all_tickets:
            # filter by status (comma-separated)
            if status:
                allowed = [s.strip() for s in status.split(",")]
                if t.get("status", "") not in allowed:
                    continue
            # filter by date
            if date:
                created = t.get("created_at", "")
                if not created.startswith(date):
                    continue
            result.append(_ticket_response(t))
        return {"tickets": result}

    @app.put("/admin/api/tickets/{ticket_id}/status")
    async def admin_update_status(
        ticket_id: str,
        body: TicketStatusUpdate,
        request: Request,
    ):
        require_admin(request)
        ticket = sheets_handler.search_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="ไม่พบ Ticket")
        ok = sheets_handler.update_ticket_status(ticket["row"], body.status, body.note)
        if not ok:
            raise HTTPException(status_code=500, detail="อัปเดตไม่สำเร็จ")
        return {"message": f"อัปเดต Ticket {ticket_id} เป็น {body.status} แล้ว"}

    @app.post("/admin/api/tickets/{ticket_id}/assign")
    async def admin_assign_ticket(
        ticket_id: str,
        body: AssignRequest,
        request: Request,
    ):
        require_admin(request)
        ok = sheets_handler.update_assignee(ticket_id, body.assignee)
        if not ok:
            raise HTTPException(status_code=500, detail="มอบหมายไม่สำเร็จ")
        return {"message": f"มอบหมาย Ticket {ticket_id} ให้ {body.assignee} แล้ว"}

    @app.get("/admin/api/tickets/export")
    async def admin_export_tickets(
        request: Request,
        period: str = "all",
        format: str = "csv",
        status: Optional[str] = None,
        date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        require_admin(request)
        all_tickets = sheets_handler.get_all_tickets()
        today_str = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now()

        filtered = []
        for t in all_tickets:
            created = t.get("created_at", "")

            # filter by period
            if period == "today":
                if not created.startswith(today_str):
                    continue
            elif period == "this_month":
                if not created.startswith(now.strftime("%Y-%m")):
                    continue
            elif period == "this_year":
                if not created.startswith(str(now.year)):
                    continue
            elif period == "custom" and start_date and end_date:
                d = created[:10]
                if not (start_date <= d <= end_date):
                    continue

            # filter by status
            if status:
                allowed = [s.strip() for s in status.split(",")]
                if t.get("status", "") not in allowed:
                    continue

            # filter by specific date
            if date and not created.startswith(date):
                continue

            filtered.append(t)

        headers = ["ticket_id", "customer_id", "customer_name", "issue_details",
                   "status", "priority", "created_at", "updated_at",
                   "assigned_to", "last_message"]

        if format == "txt":
            lines = ["\t".join(headers)]
            for t in filtered:
                lines.append("\t".join(str(t.get(h, "")) for h in headers))
            content = "\n".join(lines)
            filename = f"tickets_{today_str}.txt"
            media_type = "text/plain"
        else:
            lines = [",".join(headers)]
            for t in filtered:
                row = []
                for h in headers:
                    val = str(t.get(h, "")).replace('"', '""')
                    row.append(f'"{val}"')
                lines.append(",".join(row))
            content = "\n".join(lines)
            filename = f"tickets_{today_str}.csv"
            media_type = "text/csv"

        return Response(
            content=content.encode("utf-8-sig"),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return app