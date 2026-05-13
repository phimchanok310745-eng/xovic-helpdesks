"""
Message templates for bot responses
"""
import logging

class MessageTemplates:
    """Manage message templates"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.templates = {}
        self._load_templates()
    
    def _load_templates(self):
        """Load all templates from files"""
        template_files = {
            'team_notification': 'messages/team-notification.txt',
            'customer_reply': 'messages/customer-reply.txt',
            'ticket_status': 'messages/ticket-status.txt',
            'cancel_success': 'messages/cancel-success.txt',
            'cancel_notfound': 'messages/cancel-notfound.txt'
        }
        
        for key, path in template_files.items():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self.templates[key] = f.read()
            except FileNotFoundError:
                self.templates[key] = self._get_default_template(key)
    
    def _get_default_template(self, key):
        """Get default template if file not found"""
        defaults = {
            'team_notification': '🔔 มี Ticket ใหม่ #{ticket_id}\nจาก: {user}\nข้อความ: {message}',
            'customer_reply': '✅ รับเรื่องของคุณเรียบร้อยแล้ว\n\nหมายเลข Ticket: #{ticket_id}\nสถานะ: เปิดเรื่อง (Open)\n\nเจ้าหน้าที่จะติดต่อกลับโดยเร็ว',
            'ticket_status': '📋 สถานะ Ticket {ticket_id}\n\n📌 สถานะ: {status}\n⏰ สร้างเมื่อ: {created}\n📝 หมายเหตุ: {notes}',
            'cancel_success': '✅ ยกเลิก Ticket {ticket_id} เรียบร้อยแล้ว',
            'cancel_notfound': '❌ ไม่พบ Ticket {ticket_id}'
        }
        return defaults.get(key, '')
    
    def get_team_notification(self):
        return self.templates.get('team_notification', '')
    
    def get_customer_reply(self):
        return self.templates.get('customer_reply', '')
    
    def get_ticket_status(self):
        return self.templates.get('ticket_status', '')
    
    def get_cancel_success(self):
        return self.templates.get('cancel_success', '')
    
    def get_cancel_notfound(self):
        return self.templates.get('cancel_notfound', '')
