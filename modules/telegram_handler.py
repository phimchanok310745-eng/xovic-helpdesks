"""
Telegram bot handler for Helpdesk system
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ConversationHandler, CallbackQueryHandler, ContextTypes
)
import os
import logging
import re
from datetime import datetime

# Conversation states
SELECTING_ACTION, TICKET_INPUT, CONFIRM_CANCEL = range(3)

class TelegramHandler:
    """Handle all Telegram bot interactions"""
    
    def __init__(self, token, sheets_handler, gemini_handler, message_templates):
        self.token = token
        self.sheets = sheets_handler
        self.gemini = gemini_handler
        self.templates = message_templates
        self.team_chat_id = None
        self.logger = logging.getLogger(__name__)
        self.filters = self._load_filters()
        # ป้องกัน ticket ซ้ำ: เก็บประวัติ user + message + timestamp
        self.last_ticket = {}  # {user_id: {"message": str, "time": datetime}}
        self.duplicate_window = 300  # 5 นาที = 300 วินาที
    
    def _load_filters(self):
        return {
            'report': ['แจ้งปัญหา', 'รายงานปัญหา', 'แจ้ง', 'ช่วยด้วย', 'ปัญหา', 'ไม่ทำงาน', 'เสีย', 'report', 'new ticket', 'แจ้ง', 'ขอความช่วย', 'ต้องการความช่วย', 'มีปัญหา', 'help', 'problem', 'issue', 'error', 'ไม่ได้', 'ใช้งานไม่ได้'],
            'check': ['ตรวจสอบ', 'สถานะ', 'เช็ค', 'status', 'check', 'ติดตาม', 'ดูสถานะ', 'เช็คสถานะ', 'track'],
            'cancel': ['ยกเลิก', 'cancel', 'ไม่เอา', 'ขอคืน', 'ลบ', 'remove'],
            'ai': ['ถาม', 'help', 'ช่วย', 'สอบถาม', 'คำถาม', '?', 'what', 'how', 'why']
        }
    
    def _extract_ticket_id(self, text):
        if not text:
            return None
        # รองรับ: TKT-20260424-002, TKT20260424002, tkt-20260424-002, tkt20260424002, #TKT-xxx
        text_upper = text.upper()
        patterns = [
            r'#\s*(TKT-\d{8}-\d{3,4})',
            r'(TKT-\d{8}-\d{3,4})',
            r'(TKT\d{11,12})',
            r'#\s*(TKT\d{11,12})',
            r'#\s*(TKT-\d+-\d+)',
            r'(TKT-\d+-\d+)',
            r'#\s*(TKT\d+)',
            r'(TKT\d+)'
        ]
        for pattern in patterns:
            match = re.search(pattern, text_upper)
            if match:
                result = match.group(1).upper().lstrip('#')
                self.logger.info(f"Extracted ticket_id: {result} from pattern {pattern}")
                return result
        self.logger.info(f"No ticket_id found in text: {text}")
        return None

    def _extract_customer_identifier(self, text):
        if not text:
            return None
        match = re.search(r'\b(\d{5,20})\b', text)
        if match:
            return match.group(1)
        return None

    def _find_ticket_for_cancel(self, text):
        ticket_id = self._extract_ticket_id(text)
        if ticket_id:
            return self.sheets.search_ticket(ticket_id)
        customer_id = self._extract_customer_identifier(text)
        if customer_id:
            return self.sheets.search_ticket_by_customer(customer_id)
        return None

    def _detect_route(self, text):
        if not text:
            return 'D'
        text_lower = text.lower()
        ticket_id = self._extract_ticket_id(text)
        self.logger.info(f"_detect_route: text='{text}', extracted ticket_id='{ticket_id}'")
        
        if ticket_id:
            self.logger.info(f"_detect_route: found ticket_id, checking for cancel words")
            if any(word in text_lower for word in self.filters['cancel']):
                return 'C'
            return 'B'
        
        self.logger.info(f"_detect_route: no ticket_id, checking filters for: {text_lower[:30]}...")
        if any(word in text_lower for word in self.filters['report']):
            self.logger.info(f"_detect_route: matched report filter")
            return 'A'
        elif any(word in text_lower for word in self.filters['check']):
            self.logger.info(f"_detect_route: matched check filter")
            return 'B'
        elif any(word in text_lower for word in self.filters['cancel']):
            self.logger.info(f"_detect_route: matched cancel filter")
            return 'C'
        else:
            self.logger.info(f"_detect_route: no filter matched, default to AI (D)")
            return 'D'
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_msg = (
            "✨ ยินดีต้อนรับสู่ **XOVIC Helpdesk** ✨\n\n"
            "เราพร้อมช่วยเหลือคุณตลอด 24 ชม.\n"
            "พิมพ์ **แจ้งปัญหา** เพื่อเริ่มต้น\n"
            "พิมพ์ **ตรวจสอบ [Ticket ID]** เพื่อติดตามสถานะ\n"
            "พิมพ์ **ยกเลิก [Ticket ID]** หากต้องการยกเลิก\n\n"
            "หรือพิมพ์ข้อความสอบถามใดๆ ก็ได้เลยค่ะ 😊"
        )
        await update.message.reply_text(welcome_msg)
        return SELECTING_ACTION
    
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_msg = (
            "📋 คำสั่งที่ใช้งานได้:\n\n"
            "/start - เริ่มต้นใช้งาน\n"
            "/help - แสดงคำสั่งทั้งหมด\n"
            "/new - แจ้งปัญหาใหม่\n"
            "/status [เลขTicket] - ตรวจสอบสถานะ\n"
            "/cancel [เลขTicket] - ยกเลิก Ticket\n"
            "/stats - ดูสถิติประจำวัน\n"
            "/contact - ติดต่อเจ้าหน้าที่\n\n"
            "หรือพิมพ์ข้อความทั่วไปเพื่อแชทกับ AI"
        )
        await update.message.reply_text(help_msg)
    
    async def new_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("กรุณาพิมพ์รายละเอียดปัญหาที่คุณต้องการแจ้ง:")
        context.user_data['awaiting_report'] = True
        return SELECTING_ACTION
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages"""
        user = update.effective_user
        text = update.message.text
        chat = update.effective_chat
        
        self.logger.info(f"Incoming message from user={user.id} chat={chat.id} text={text}")
        try:
            # Check if we're awaiting a report
            if context.user_data.get('awaiting_report'):
                context.user_data['awaiting_report'] = False
                route = 'A'
            else:
                route = self._detect_route(text)
            
            # ถ้าเป็นกลุ่ม และ route == 'D' (ทั่วไป) และข้อความไม่ใช่ @บอท -> ไม่ตอบ
            if chat.type in ['group', 'supergroup'] and route == 'D':
                # ตรวจสอบว่า message มีการ mention บอทหรือไม่
                entities = update.message.entities or []
                is_mention = any(e.type == 'mention' for e in entities if e.type)
                # ตรวจสอบด้วยว่า @xovic_bot อยู่ในข้อความ
                if not is_mention and not text.startswith('/') and '@xovic_bot' not in text:
                    self.logger.info("Skip reply in group for non-command message")
                    return SELECTING_ACTION

            self.logger.info(f"User {user.id} - Route {route}: {text[:50]}...")

            if route == 'A':
                await self._handle_report(update, context, user, text)
            elif route == 'B':
                await self._handle_check(update, context, text)
            elif route == 'C':
                await self._handle_cancel_request(update, context, text)
            elif route == 'D':
                await self._handle_ai_chat(update, context, text)
            else:
                await update.message.reply_text("ไม่เข้าใจคำสั่ง กรุณาพิมพ์ใหม่")

        except Exception as e:
            self.logger.error(f"Exception in handle_message: {e}", exc_info=True)
            try:
                await update.message.reply_text("เกิดข้อผิดพลาดภายในระบบ กรุณาลองอีกครั้งหลังจากนี้")
            except Exception:
                pass

        return SELECTING_ACTION

    async def _handle_report(self, update, context, user, text):
        # ตรวจสอบ ticket ซ้ำ
        user_id_str = str(user.id)
        now = datetime.now()
        if user_id_str in self.last_ticket:
            last_info = self.last_ticket[user_id_str]
            time_diff = (now - last_info["time"]).total_seconds()
            # ถ้าข้อความเหมือนกันและภายใน 5 นาที
            if time_diff < self.duplicate_window and last_info["message"] == text:
                await update.message.reply_text(
                    "⚠️ คุณได้สร้าง Ticket ข้อความเดียวกันไปแล้ว\n"
                    "กรุณารอ 5 นาที หรือส่งข้อความใหม่ที่แตกต่างจากเดิม"
                )
                return SELECTING_ACTION
        
        # สร้าง ticket ใหม่
        ticket_id = self.sheets.add_ticket(
            user_id=user.id,
            username=user.full_name or user.username or str(user.id),
            message=text
        )
        
        # เก็บประวัติการสร้าง ticket
        self.last_ticket[user_id_str] = {"message": text, "time": now}
        self.logger.info(f"📨 team_chat_id = {self.team_chat_id}")
        if self.team_chat_id:
            try:
                keyboard = [
                    [
                        InlineKeyboardButton("👨‍💻 รับเรื่องนี้", callback_data=f"assign_{ticket_id}"),
                        InlineKeyboardButton("🟡 กำลังดำเนินการ", callback_data=f"status_inprogress_{ticket_id}")
                    ],
                    [
                        InlineKeyboardButton("🔵 แก้ไขเสร็จ", callback_data=f"status_resolved_{ticket_id}"),
                        InlineKeyboardButton("⚫ ปิดงาน", callback_data=f"status_closed_{ticket_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                team_msg = (
                    f"<b>📢 มี Ticket ใหม่!</b>\n"
                    f"Ticket: <b>{ticket_id}</b>\n"
                    f"📝 รายละเอียด: {text}\n"
                    f"👤 ผู้แจ้ง: {user.full_name}\n"
                    f"⏰ เวลา: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"👉 สถานะ: 🟢 รอดำเนินการ\n"
                    f"👨‍💻 ผู้รับเรื่อง: ยังไม่มี"
                )
                await context.bot.send_message(
                    chat_id=self.team_chat_id,
                    text=team_msg,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                self.logger.info(f"✅ ส่งข้อความไปยังกลุ่ม {self.team_chat_id} สำเร็จ")
            except Exception as e:
                self.logger.error(f"❌ ส่งข้อความไปกลุ่มล้มเหลว: {e}")
        else:
            self.logger.warning("⚠️ ไม่มี team_chat_id ตั้งค่าไว้")
        reply = (
            f"ขอบคุณที่แจ้งปัญหาค่ะ 🙏\n\n"
            f"✅ ระบบได้สร้าง Ticket ให้คุณแล้ว\n"
            f"🆔 Ticket ID: <b>{ticket_id}</b>\n\n"
            f"ทีมงานของเราจะติดต่อกลับโดยเร็วที่สุด\n"
            f"(โดยปกติภายใน 2-4 ชั่วโมงทำการ)\n\n"
            f"หากต้องการตรวจสอบสถานะ พิมพ์:\n"
            f"ตรวจสอบ {ticket_id}"
        )
        await update.message.reply_text(reply, parse_mode='HTML')
    
    async def _handle_check(self, update, context, text):
        """Handle ticket status check"""
        ticket_id = self._extract_ticket_id(text)
        self.logger.info(f"_handle_check: extracted ticket_id = {ticket_id}, original text = {text}")

        if ticket_id:
            ticket = self.sheets.search_ticket(ticket_id)
            self.logger.info(f"_handle_check: search result = {ticket}")

            if not ticket:
                await update.message.reply_text(f"❌ ไม่พบ Ticket {ticket_id}\n\nกรุณาตรวจสอบเลข Ticket อีกครั้ง")
                return SELECTING_ACTION

            data = ticket.get("data") if isinstance(ticket, dict) else ticket
            # ดึงข้อมูลด้วย key ที่ตรงกับที่ search_ticket สร้าง
            ticket_id_val = data.get('ticket_id', '')
            customer = data.get('customer_name', '')
            issue = data.get('issue_details', '')
            raw_status = data.get('status', 'open')
            assignee = data.get('assigned_to', '')
            created_time = data.get('created_at', '')
            updated_time = data.get('updated_at', '')

            status_map = {
                "open": "🟢 รับเรื่องแล้ว",
                "in_progress": "🟡 กำลังดำเนินการ",
                "resolved": "🔵 แก้ไขเสร็จสิ้น",
                "closed": "⚫ ปิดเรื่อง",
                "cancelled": "🔴 ยกเลิก",
                "แจ้งปัญหา": "🟢 รับเรื่องแล้ว",
                "กำลังดำเนินการ": "🟡 กำลังดำเนินการ",
                "แก้ไขเสร็จสิ้น": "🔵 แก้ไขเสร็จสิ้น",
                "ปิดเรื่อง": "⚫ ปิดเรื่อง",
                "ยกเลิก": "🔴 ยกเลิก"
            }
            display_status = status_map.get(raw_status, f"❓ {raw_status}")

            if not assignee:
                assignee = "⏳ ยังไม่มีผู้รับเรื่อง"

            status_msg = (
                f"📋 สถานะ Ticket {ticket_id_val}\n"
                f"{'='*30}\n"
                f"📌 รายละเอียด: {issue or '-'}\n"
                f"📊 สถานะ: {display_status}\n"
                f"👤 ผู้แจ้ง: {customer or '-'}\n"
                f"👨‍💻 ผู้รับผิดชอบ: {assignee}\n"
                f"📅 สร้างเมื่อ: {created_time or '-'}\n"
                f"🕒 อัปเดตล่าสุด: {updated_time or '-'}\n"
                f"📝 หมายเหตุ: -\n"
                f"{'='*30}"
            )
            await update.message.reply_text(status_msg)
        else:
            await update.message.reply_text("กรุณาระบุเลข Ticket")

        return SELECTING_ACTION
    
    async def _handle_cancel_request(self, update, context, text):
        ticket = self._find_ticket_for_cancel(text)
        if ticket:
            ticket_id = ticket['data'].get('ticket_id', '') if isinstance(ticket, dict) else ticket.get('data', {}).get('ticket_id', '')
            keyboard = [[
                InlineKeyboardButton("✅ ใช่, ยกเลิก", callback_data=f"confirm_cancel_{ticket_id}"),
                InlineKeyboardButton("❌ ไม่", callback_data="cancel_no")
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"คุณต้องการยกเลิก Ticket {ticket_id} ใช่หรือไม่?",
                reply_markup=reply_markup
            )
            context.user_data['cancel_ticket'] = ticket
            return CONFIRM_CANCEL

        await update.message.reply_text(
            "กรุณาระบุเลข Ticket หรือ ID ตัวเลขที่ต้องการยกเลิก\n"
            "ตัวอย่าง: ยกเลิก TKT-20260423-000 หรือ ยกเลิก 123456789"
        )
        return SELECTING_ACTION
    
    async def _handle_ai_chat(self, update, context, text):
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        response = await self.gemini.get_response_async(text)
        await update.message.reply_text(response)

    async def _set_ticket_status(self, update, context, ticket_id, status, note=None, assigned_to=None):
        ticket = self.sheets.search_ticket(ticket_id)
        if not ticket:
            await update.message.reply_text(f"❌ ไม่พบ Ticket {ticket_id}")
            return
        changed = self.sheets.update_ticket_status(
            ticket['row'], status, note or f"สถานะเปลี่ยนเป็น {status}",
            reply_chat_id=update.effective_chat.id if update.effective_chat else None,
            assigned_to=assigned_to
        )
        if changed:
            # เฉพาะสถานะที่ไม่ใช่ in_progress, resolved, closed เท่านั้นที่แจ้ง user
            if status not in ["in_progress", "resolved", "closed"]:
                await update.message.reply_text(f"✅ Ticket {ticket_id} ถูกตั้งสถานะเป็น {status} แล้ว")
            if self.team_chat_id:
                await context.bot.send_message(
                    chat_id=self.team_chat_id,
                    text=f"🔄 Ticket {ticket_id} สถานะเปลี่ยนเป็น {status}"
                )
        else:
            # แจ้ง error เฉพาะ user ที่ไม่ใช่ 3 สถานะนี้
            if status not in ["in_progress", "resolved", "closed"]:
                await update.message.reply_text("❌ ไม่สามารถอัปเดตสถานะได้ โปรดลองอีกครั้ง")

    async def in_progress(self, update, context):
        text = update.message.text or ''
        ticket_id = self._extract_ticket_id(text)
        if not ticket_id:
            await update.message.reply_text("กรุณาระบุหมายเลข Ticket ที่ต้องการเปลี่ยนสถานะ เช่น /inprogress TKT-20260320-000")
            return
        await self._set_ticket_status(update, context, ticket_id, 'in_progress', 'อัปเดตโดยเจ้าหน้าที่: กำลังดำเนินการ')

    async def resolved(self, update, context):
        text = update.message.text or ''
        ticket_id = self._extract_ticket_id(text)
        if not ticket_id:
            await update.message.reply_text("กรุณาระบุหมายเลข Ticket ที่ต้องการเปลี่ยนสถานะ เช่น /resolve TKT-20260320-000")
            return
        await self._set_ticket_status(update, context, ticket_id, 'resolved', 'อัปเดตโดยเจ้าหน้าที่: แก้ไขเสร็จสิ้น')

    async def closed(self, update, context):
        text = update.message.text or ''
        ticket_id = self._extract_ticket_id(text)
        if not ticket_id:
            await update.message.reply_text("กรุณาระบุหมายเลข Ticket ที่ต้องการปิด เช่น /close TKT-20260320-000")
            return
        await self._set_ticket_status(update, context, ticket_id, 'closed', 'อัปเดตโดยเจ้าหน้าที่: ปิดเรื่องแล้ว')

    async def assign(self, update, context):
        text = update.message.text or ''
        ticket_id = self._extract_ticket_id(text)
        if not ticket_id:
            await update.message.reply_text("กรุณาระบุหมายเลข Ticket และชื่อผู้รับผิดชอบ เช่น /assign TKT-20260320-000 นายเอ")
            return
        parts = text.split()
        assignee = None
        if len(parts) > 2:
            assignee = ' '.join(parts[2:]).strip()
        if not assignee:
            assignee = update.effective_user.full_name or update.effective_user.username or 'เจ้าหน้าที่'
        await self._set_ticket_status(
            update,
            context,
            ticket_id,
            'in_progress',
            note=f"Assigned to {assignee}",
            assigned_to=assignee
        )

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """คำสั่ง /stats แสดงสถิติประจำวัน"""
        stats = self.sheets.get_stats()
        if stats:
            msg = (
                f"📊 *สถิติ Ticket ประจำวัน*\n"
                f"📥 ทั้งหมด: {stats['total']}\n"
                f"🟢 เปิด: {stats['open']}\n"
                f"🟡 กำลังดำเนินการ: {stats['in_progress']}\n"
                f"🔵 แก้ไขเสร็จ: {stats['resolved']}\n"
                f"⚫ ปิดแล้ว: {stats['closed']}\n"
                f"🔴 ยกเลิก: {stats['cancelled']}"
            )
            await update.message.reply_text(msg, parse_mode='Markdown')
        else:
            await update.message.reply_text("ไม่พบข้อมูล")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        # รับเรื่อง
        if query.data.startswith('assign_'):
            ticket_id = query.data.replace('assign_', '')
            user = query.from_user
            assignee_name = user.full_name or user.username or str(user.id)
            ticket = self.sheets.search_ticket(ticket_id)
            if not ticket:
                await query.edit_message_text(f"❌ ไม่พบ Ticket {ticket_id}")
                return SELECTING_ACTION
            data = ticket.get('data') if isinstance(ticket, dict) else ticket
            current_assignee = data.get('assigned_to', '')
            if current_assignee:
                await query.edit_message_text(f"⚠️ Ticket {ticket_id} มีผู้รับเรื่องแล้ว: {current_assignee}")
                return SELECTING_ACTION
            success = self.sheets.update_assignee(ticket_id, assignee_name)
            if success:
                # อัปเดตข้อความเดิม ให้มีปุ่มสถานะ
                new_text = f"✅ {assignee_name} รับเรื่อง {ticket_id} เรียบร้อยแล้ว"
                new_keyboard = [
                    [
                        InlineKeyboardButton("🟡 กำลังดำเนินการ", callback_data=f"status_inprogress_{ticket_id}"),
                        InlineKeyboardButton("🔵 แก้ไขเสร็จ", callback_data=f"status_resolved_{ticket_id}"),
                        InlineKeyboardButton("⚫ ปิดงาน", callback_data=f"status_closed_{ticket_id}")
                    ]
                ]
                new_markup = InlineKeyboardMarkup(new_keyboard)
                await query.edit_message_text(new_text, reply_markup=new_markup)
                # แจ้งเตือนในกลุ่ม
                if self.team_chat_id:
                    await context.bot.send_message(
                        chat_id=self.team_chat_id,
                        text=f"👨‍💻 Ticket {ticket_id} ถูกรับเรื่องโดย {assignee_name}"
                    )
            else:
                await query.edit_message_text(f"❌ เกิดข้อผิดพลาด ไม่สามารถรับเรื่อง {ticket_id} ได้")
            context.user_data.clear()
            return SELECTING_ACTION

        # กำลังดำเนินการ
        elif query.data.startswith('status_inprogress_'):
            ticket_id = query.data.replace('status_inprogress_', '')
            user = query.from_user
            assignee_name = user.full_name or user.username or str(user.id)
            # กำหนดผู้รับเรื่องหากยังไม่มี
            self.sheets.update_assignee(ticket_id, assignee_name)
            self.sheets.update_ticket_status_by_id(ticket_id, 'in_progress', note='กำลังดำเนินการ')
            new_text = f"🟡 Ticket {ticket_id} กำลังดำเนินการโดย {assignee_name}"
            new_keyboard = [
                [
                    InlineKeyboardButton("🔵 แก้ไขเสร็จ", callback_data=f"status_resolved_{ticket_id}"),
                    InlineKeyboardButton("⚫ ปิดงาน", callback_data=f"status_closed_{ticket_id}")
                ]
            ]
            new_markup = InlineKeyboardMarkup(new_keyboard)
            await query.edit_message_text(new_text, reply_markup=new_markup)
            # แจ้งเตือน
            if self.team_chat_id:
                await context.bot.send_message(
                    chat_id=self.team_chat_id,
                    text=f"🟡 Ticket {ticket_id} กำลังดำเนินการโดย {assignee_name}"
                )
            # ไม่ต้องแจ้งลูกค้าโดยตรง
            return SELECTING_ACTION

        # แก้ไขเสร็จ
        elif query.data.startswith('status_resolved_'):
            ticket_id = query.data.replace('status_resolved_', '')
            self.sheets.update_ticket_status_by_id(ticket_id, 'resolved', note='แก้ไขเสร็จสิ้น')
            new_text = f"🔵 Ticket {ticket_id} แก้ไขเสร็จสิ้น"
            new_keyboard = [
                [
                    InlineKeyboardButton("⚫ ปิดงาน", callback_data=f"status_closed_{ticket_id}")
                ]
            ]
            new_markup = InlineKeyboardMarkup(new_keyboard)
            await query.edit_message_text(new_text, reply_markup=new_markup)
            # แจ้งเตือน
            if self.team_chat_id:
                await context.bot.send_message(
                    chat_id=self.team_chat_id,
                    text=f"🔵 Ticket {ticket_id} แก้ไขเสร็จสิ้น"
                )
            # ไม่ต้องแจ้งลูกค้าโดยตรง
            return SELECTING_ACTION

        # ปิดงาน
        elif query.data.startswith('status_closed_'):
            ticket_id = query.data.replace('status_closed_', '')
            self.sheets.update_ticket_status_by_id(ticket_id, 'closed', note='ปิดงาน')
            new_text = f"⚫ Ticket {ticket_id} ปิดงานเรียบร้อยแล้ว"
            await query.edit_message_text(new_text)
            # แจ้งเตือน
            if self.team_chat_id:
                await context.bot.send_message(
                    chat_id=self.team_chat_id,
                    text=f"⚫ Ticket {ticket_id} ปิดงานเรียบร้อยแล้ว"
                )
            # ไม่ต้องแจ้งลูกค้าโดยตรง
            return SELECTING_ACTION

        # จัดการปุ่มยกเลิก
        elif query.data.startswith('confirm_cancel_'):
            ticket_id = query.data.replace('confirm_cancel_', '')
            ticket = context.user_data.get('cancel_ticket')
            if ticket:
                self.sheets.update_ticket_status(
                    ticket['row'], 'cancelled', 'ยกเลิกโดยลูกค้า',
                    reply_chat_id=update.effective_chat.id if update.effective_chat else None
                )
                success_msg = (
                    f"✅ ยกเลิก Ticket <b>{ticket_id}</b> เรียบร้อยแล้ว\n\n"
                    f"หากต้องการแจ้งปัญหาใหม่ พิมพ์ \"แจ้งปัญหา\""
                )
                await query.edit_message_text(success_msg, parse_mode='HTML')
                if self.team_chat_id:
                    await context.bot.send_message(
                        chat_id=self.team_chat_id,
                        text=f"🔄 Ticket {ticket_id} ถูกยกเลิกโดยลูกค้า"
                    )
            else:
                await query.edit_message_text("❌ ไม่พบ Ticket นี้ในระบบ\nกรุณาตรวจสอบเลข Ticket อีกครั้ง")
            context.user_data.clear()
            return SELECTING_ACTION

        elif query.data == "cancel_no":
            await query.edit_message_text("✅ ยกเลิกรายการเรียบร้อย")
            context.user_data.clear()
            return SELECTING_ACTION

        context.user_data.clear()
        return SELECTING_ACTION
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.logger.error(f"Exception: {context.error}")
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "ขออภัย ระบบเกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง"
                )
        except:
            pass
    
    def run(self):
        app = Application.builder().token(self.token).build()
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', self.start),
                CommandHandler('help', self.help),
                CommandHandler('new', self.new_ticket),
                CommandHandler('inprogress', self.in_progress),
                CommandHandler('resolve', self.resolved),
                CommandHandler('close', self.closed),
                CommandHandler('assign', self.assign),
                CommandHandler('stats', self.stats),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
            ],
            states={
                SELECTING_ACTION: [
                    CommandHandler('start', self.start),
                    CommandHandler('help', self.help),
                    CommandHandler('new', self.new_ticket),
                    CommandHandler('inprogress', self.in_progress),
                    CommandHandler('resolve', self.resolved),
                    CommandHandler('close', self.closed),
                    CommandHandler('assign', self.assign),
                    CommandHandler('stats', self.stats),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
                ],
                TICKET_INPUT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
                ],
                CONFIRM_CANCEL: [
                    CallbackQueryHandler(self.handle_callback)
                ]
            },
            fallbacks=[CommandHandler('start', self.start)]
        )
        app.add_handler(conv_handler)
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        app.add_error_handler(self.error_handler)
        self.logger.info("🤖 Helpdesk Bot is running...")
        app.run_polling(allowed_updates=Update.ALL_TYPES) 