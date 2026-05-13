"""
Google Sheets handler for ticket management (OAuth version)
"""
import gspread
import os
import pickle
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from datetime import datetime
import json
import logging

class SheetsHandlerOAuth:
    def __init__(self, credentials_file, token_file, sheet_id):
        self.sheet_id = sheet_id
        self.logger = logging.getLogger(__name__)
        self.client = self._authenticate(credentials_file, token_file)
        self.worksheet = self.client.open_by_key(sheet_id).sheet1
        self.headers = self._load_headers()

    def _authenticate(self, credentials_file, token_file):
        SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = None
        if os.path.exists(token_file):
            with open(token_file, 'rb') as token:
                creds = pickle.load(token)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_file, 'wb') as token:
                pickle.dump(creds, token)
        return gspread.authorize(creds)

    def _load_headers(self):
        try:
            with open('sheets/tickets-headers.json', 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except FileNotFoundError:
            return [
                "ticket_id", "customer_id", "customer_name", "issue_details",
                "status", "priority", "created_at", "updated_at",
                "reply_chat_id", "last_message", "assigned_to"
            ]

    def add_ticket(self, user_id, username, message):
        try:
            today = datetime.now()
            date_prefix = today.strftime('%Y%m%d')
            existing_records = self.get_all_tickets()
            same_day_count = sum(1 for rec in existing_records
                                 if isinstance(rec.get('created_at'), str)
                                 and rec.get('created_at').startswith(today.strftime('%Y-%m-%d')))
            ticket_index = same_day_count + 1
            ticket_id = f"TKT-{date_prefix}-{ticket_index:03d}"
            created_at = today.strftime('%Y-%m-%d %H:%M:%S')
            headers = [h.lower().strip() for h in self.worksheet.row_values(1)]
            row = []
            for h in headers:
                if h == 'ticket_id':
                    row.append(ticket_id)
                elif h in ('created_at', 'timestamp'):
                    row.append(created_at)
                elif h in ('customer_id', 'user_id'):
                    row.append(str(user_id))
                elif h in ('customer_name', 'username'):
                    row.append(username)
                elif h in ('issue_details', 'issue', 'message', 'detail'):
                    row.append(message)
                elif h == 'status':
                    row.append('open')
                elif h == 'priority':
                    row.append('ปานกลาง')
                elif h == 'updated_at':
                    row.append(created_at)
                elif h in ('reply_chat_id', 'last_message', 'assigned_to'):
                    row.append('')
                else:
                    row.append('')
            self.worksheet.append_row(row)
            # เพิ่มข้อความ initial ลง last_message
            headers = [h.lower().strip() for h in self.worksheet.row_values(1)]
            last_row = len(self.worksheet.get_all_values())
            for i, h in enumerate(headers):
                if h in ('last_message', 'last_msg', 'หมายเหตุ', 'notes', 'remark'):
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    msg = f"[{timestamp}] สร้าง Ticket โดยลูกค้า"
                    self.worksheet.update_cell(last_row, i+1, msg)
                    break
            self.logger.info(f"Created ticket: {ticket_id}")
            return ticket_id
        except Exception as e:
            self.logger.error(f"Failed to add ticket: {e}")
            raise

    def search_ticket(self, ticket_id):
        try:
            # Normalize ticket_id: remove -, ลบ #, รองรับตัวพิมพ์เล็ก/ใหญ่
            original_ticket_id = str(ticket_id)
            raw_ticket_id = str(ticket_id).strip().upper().replace('-', '').replace(' ', '').lstrip('#')
            self.logger.info(f"Searching for ticket_id: '{original_ticket_id}' -> normalized: '{raw_ticket_id}'")
            rows = self.worksheet.get_all_values()
            if not rows or len(rows) < 2:
                self.logger.info("No rows or insufficient data in sheet")
                return None
            
            # หา column index จาก header row
            headers = [str(h).strip().lower() for h in rows[0]]
            idx_ticket = headers.index('ticket_id') if 'ticket_id' in headers else 0
            self.logger.info(f"Headers: {headers}")
            self.logger.info(f"Ticket column index: {idx_ticket}")
            
            for idx, row in enumerate(rows[1:], start=2):
                if not row or len(row) <= idx_ticket:
                    continue
                cell_id = str(row[idx_ticket]).strip().upper().lstrip('#')
                cell_id_no_dash = cell_id.replace('-', '').replace(' ', '')
                self.logger.info(f"Row {idx}: cell='{cell_id}' -> normalized='{cell_id_no_dash}' vs searching='{raw_ticket_id}'")
                if cell_id_no_dash == raw_ticket_id:
                    self.logger.info(f"Match found at row {idx}")
                    data = {
                        'ticket_id': row[idx_ticket] if idx_ticket < len(row) else '',
                        'customer_id': row[1] if len(row) > 1 else '',
                        'customer_name': row[2] if len(row) > 2 else '',
                        'issue_details': row[3] if len(row) > 3 else '',
                        'status': row[4] if len(row) > 4 else 'open',
                        'assigned_to': row[10] if len(row) > 10 else '',
                        'created_at': row[6] if len(row) > 6 else '',
                        'updated_at': row[7] if len(row) > 7 else '',
                        'user_id': row[1] if len(row) > 1 else ''
                    }
                    if not data.get('status'):
                        data['status'] = 'open'
                    if not data.get('assigned_to'):
                        data['assigned_to'] = ''
                    self.logger.info(f"Found ticket: {data.get('ticket_id')}")
                    return {'row': idx, 'data': data}
            self.logger.info(f"Ticket not found: {raw_ticket_id}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to search ticket: {e}")
            return None

    def _normalize_customer_id(self, customer_id):
        if customer_id is None:
            return ''
        if isinstance(customer_id, float) and customer_id.is_integer():
            customer_id = int(customer_id)
        return str(customer_id).strip().lower()

    def search_ticket_by_customer(self, customer_id):
        raw_customer = self._normalize_customer_id(customer_id)
        if not raw_customer:
            return None
        try:
            rows = self.worksheet.get_all_values()
            if not rows or len(rows) < 2:
                return None
            headers = [str(h).strip().lower() for h in rows[0]]
            idx_customer = headers.index('customer_id') if 'customer_id' in headers else 1
            idx_ticket = headers.index('ticket_id') if 'ticket_id' in headers else 0
            idx_created = headers.index('created_at') if 'created_at' in headers else None
            matched = []
            for idx, row in enumerate(rows[1:], start=2):
                if idx_customer < len(row):
                    current = self._normalize_customer_id(row[idx_customer])
                    if current == raw_customer:
                        ticket_data = {
                            'ticket_id': row[idx_ticket] if idx_ticket < len(row) else '',
                            'customer_name': row[headers.index('customer_name')] if 'customer_name' in headers and headers.index('customer_name') < len(row) else '',
                            'issue_details': row[headers.index('issue_details')] if 'issue_details' in headers and headers.index('issue_details') < len(row) else '',
                            'status': row[headers.index('status')] if 'status' in headers and headers.index('status') < len(row) else 'open',
                            'assigned_to': row[headers.index('assigned_to')] if 'assigned_to' in headers and headers.index('assigned_to') < len(row) else '',
                            'created_at': row[idx_created] if idx_created is not None and idx_created < len(row) else '',
                            'updated_at': row[headers.index('updated_at')] if 'updated_at' in headers and headers.index('updated_at') < len(row) else '',
                            'customer_id': row[idx_customer],
                            'row': idx
                        }
                        matched.append(ticket_data)
            if not matched:
                return None
            if idx_created is not None:
                matched.sort(key=lambda x: x.get('created_at', ''), reverse=True)
            else:
                matched.sort(key=lambda x: x['row'], reverse=True)
            latest = matched[0]
            return {'row': latest['row'], 'data': latest}
        except Exception as e:
            self.logger.error(f"Failed to search ticket by customer: {e}")
            return None

    def update_ticket_status(self, row, status, note='', reply_chat_id=None, assigned_to=None):
        try:
            headers = [h.lower().strip() for h in self.worksheet.row_values(1)]
            # อัปเดตสถานะ
            for i, h in enumerate(headers):
                if h in ('status', 'สถานะ'):
                    self.worksheet.update_cell(row, i+1, status)
                    break
            if assigned_to is not None:
                for i, h in enumerate(headers):
                    if h == 'assigned_to':
                        self.worksheet.update_cell(row, i+1, assigned_to)
                        break
            # updated_at
            for i, h in enumerate(headers):
                if h == 'updated_at':
                    self.worksheet.update_cell(row, i+1, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                    break
            if reply_chat_id is not None:
                for i, h in enumerate(headers):
                    if h == 'reply_chat_id':
                        self.worksheet.update_cell(row, i+1, str(reply_chat_id))
                        break

            # ===== เริ่มส่วนที่แก้ไข: บันทึกข้อความการเปลี่ยนสถานะลง last_message เสมอ =====
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if note:
                # ถ้ามี note ให้ใช้ note ที่ผู้ใช้กรอก (แต่ก็สามารถเพิ่ม prefix บอกการเปลี่ยนสถานะด้วย)
                log_msg = f"[{timestamp}] {note}"
            else:
                # ถ้าไม่มี note ให้สร้างข้อความอัตโนมัติ
                # แปลง status ให้เป็นภาษาไทยสวยงาม (ไม่บังคับ)
                status_th = {
                    'open': 'เปิดเรื่อง',
                    'in_progress': 'กำลังดำเนินการ',
                    'resolved': 'แก้ไขเสร็จสิ้น',
                    'closed': 'ปิดงาน',
                    'cancelled': 'ยกเลิก'
                }.get(status, status)
                log_msg = f"[{timestamp}]{status_th}"

            # หาคอลัมน์ที่เก็บข้อความล่าสุด (ลองหลายชื่อ)
            updated = False
            for i, h in enumerate(headers):
                if h in ('last_message', 'last_msg', 'หมายเหตุ', 'notes', 'remark'):
                    current = self.worksheet.cell(row, i+1).value or ''
                    # ต่อข้อความใหม่ไว้ด้านบน (แสดงล่าสุดก่อน)
                    new_content = log_msg + "\n" + current
                    self.worksheet.update_cell(row, i+1, new_content)
                    updated = True
                    break
            if not updated:
                # ถ้าไม่พบคอลัมน์ที่ตรง ให้พยายามใช้คอลัมน์ที่ 10 (index 9) เพราะเป็น last_message ตาม header ตัวอย่าง
                # หรืออาจเพิ่มคอลัมน์ใหม่
                self.logger.warning(f"No suitable column found for last_message, using column 10")
                col = 10  # คอลัมน์ J (1-based)
                current = self.worksheet.cell(row, col).value or ''
                new_content = log_msg + "\n" + current
                self.worksheet.update_cell(row, col, new_content)
            # ===== จบส่วนที่แก้ไข =====

            self.logger.info(f"Updated ticket at row {row} status={status}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to update ticket status: {e}")
            return False

    def update_assignee(self, ticket_id, assignee_name):
        try:
            ticket = self.search_ticket(ticket_id)
            if not ticket:
                self.logger.error(f"Ticket {ticket_id} not found")
                return False
            row = ticket['row']
            headers = [h.lower().strip() for h in self.worksheet.row_values(1)]
            assign_col = None
            for i, h in enumerate(headers):
                if h == 'assigned_to':
                    assign_col = i + 1
                    break
            if not assign_col:
                assign_col = len(headers) + 1
                self.worksheet.update_cell(1, assign_col, 'assigned_to')
            self.worksheet.update_cell(row, assign_col, assignee_name)
            self.logger.info(f"Updated assignee for ticket {ticket_id} to {assignee_name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to update assignee: {e}")
            return False

    def update_ticket_status_by_id(self, ticket_id, status, note=''):
        ticket = self.search_ticket(ticket_id)
        if not ticket:
            return False
        return self.update_ticket_status(ticket['row'], status, note)

    def get_stats(self):
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            records = self.get_all_tickets()
            stats = {
                'total': 0, 'open': 0, 'in_progress': 0,
                'resolved': 0, 'closed': 0, 'cancelled': 0
            }
            for rec in records:
                created = rec.get('created_at', '')
                if created and created.startswith(today):
                    stats['total'] += 1
                    status = rec.get('status', '').lower()
                    if status in stats:
                        stats[status] += 1
            return stats
        except Exception as e:
            self.logger.error(f"Failed to get stats: {e}")
            return None

    def get_all_tickets(self, status=None):
        """ดึง Ticket ทั้งหมด โดยใช้ get_all_values เพื่อป้องกันปัญหา header"""
        try:
            rows = self.worksheet.get_all_values()
            if not rows or len(rows) < 2:
                return []
            headers = [str(h).strip() for h in rows[0]]
            records = []
            for row in rows[1:]:
                record = {}
                for i, header in enumerate(headers):
                    if i < len(row):
                        record[header] = row[i]
                    else:
                        record[header] = ''
                records.append(record)
            return records
        except Exception as e:
            self.logger.error(f"Failed to get tickets: {e}")
            return []