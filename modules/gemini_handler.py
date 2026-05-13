"""
Google Gemini AI handler for chat responses
"""
from google.genai import Client
import logging
from pathlib import Path

class GeminiHandler:
    def __init__(self, api_key):
        self.logger = logging.getLogger(__name__)
        try:
            self.client = Client(api_key=api_key)
            self.logger.info("Gemini AI initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize Gemini AI: {e}")
            raise
        self.system_prompt = self._load_system_prompt()

    def _load_system_prompt(self):
        prompt_path = Path(__file__).parent.parent / 'messages' / 'ai-prompt.txt'
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return "คุณคือผู้ช่วย Helpdesk อัตโนมัติ พูดจาสุภาพ"

    def get_response(self, user_message):
        full_prompt = f"{self.system_prompt}\n\nผู้ใช้: {user_message}\n\nผู้ช่วย:"
        model_candidates = [
            'models/gemini-2.5-flash',
            'models/gemini-2.0-flash'
        ]
        last_exception = None
        for model_name in model_candidates:
            try:
                self.logger.info(f"Trying Gemini model: {model_name}")
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=full_prompt
                )
                self.logger.info(f"Gemini model {model_name} succeeded")
                return response.text
            except Exception as e:
                last_exception = e
                self.logger.warning(f"Gemini model {model_name} failed: {e}")
                if 'NOT_FOUND' in str(e) or 'not found' in str(e).lower():
                    continue
                break
        self.logger.error(f"Failed to get AI response from all models: {last_exception}")
        if last_exception is not None and 'RESOURCE_EXHAUSTED' in str(last_exception):
            return "ขออภัย ระบบ AI ไม่สามารถตอบได้ เนื่องจากโควต้า Gemini หมดหรือยังไม่ได้เปิดใช้งาน API โปรดตรวจสอบบัญชีและบิลลิ่ง"
        return "ขออภัย ระบบมีปัญหา กรุณาลองใหม่"

    async def get_response_async(self, user_message):
        return self.get_response(user_message)