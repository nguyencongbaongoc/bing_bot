import json
import urllib.request
import urllib.error
import subprocess
import time
from typing import List, Optional
from core.tracker import _logger as logger

OLLAMA_API_BASE = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:3b"


def ensure_ollama_service() -> bool:
    """Kiểm tra dịch vụ Ollama, nếu chưa chạy thì thử khởi động ngầm."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_API_BASE}/api/tags", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        try:
            logger.info("[AI Solver] Đang khởi động tiến trình Ollama ngầm...")
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            )
            time.sleep(3)
            return True
        except Exception as e:
            logger.warning(f"[AI Solver] Không thể tự khởi động Ollama: {e}")
            return False


def ask_local_ai(question: str, options: List[str], model: str = DEFAULT_MODEL) -> Optional[str]:
    """Gửi câu hỏi và options tới Ollama, trích xuất đáp án chính xác nhất."""
    if not ensure_ollama_service():
        return None

    options_formatted = "\n".join([f"- {opt}" for opt in options])
    prompt = f"""You are a multiple-choice quiz solver. Pick the correct answer based on facts.
Question: {question}
Options:
{options_formatted}

Respond ONLY with the exact text of the correct option from the list above. No reasoning, no extra words."""

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 30
        }
    }

    try:
        req = urllib.request.Request(
            f"{OLLAMA_API_BASE}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            answer = res_data.get("response", "").strip()

            for opt in options:
                if opt.lower() in answer.lower() or answer.lower() in opt.lower():
                    return opt
            return options[0]
    except Exception as e:
        logger.warning(f"[AI Solver] Lỗi truy vấn LLM: {e}")
        return None


def unload_model_from_vram(model: str = "qwen2.5:3b"):
    """Gửi tín hiệu dọn VRAM tới Ollama, tự động bỏ qua nếu Ollama không hoạt động."""
    import urllib.request
    import json
    from core.tracker import logger
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            logger.info("[✓] Đã giải phóng model khỏi VRAM thành công.")
    except Exception:
        # Bắt ConnectionRefusedError, URLError, Timeout,... khi Ollama không bật
        pass


def solve_quiz_with_ai(tab):
    """
    Trích xuất câu hỏi và đáp án từ tab Quiz, hỏi AI và click đáp án đúng.
    Trả về True nếu xử lý thành công.
    """
    from core.tracker import _log_info as _log_info, _log_warning as _log_warning
    from core.humanizer import human_delay

    try:
        # Selectors cho câu hỏi quiz
        question_selectors = [
            '#rqQuestion',
            '.rqQuestion',
            '[data-bi-id="quiz-question"]',
            'div[class*="question"]',
            'h2, h3',
        ]

        question_text = ""
        for sel in question_selectors:
            try:
                q_elem = tab.locator(sel).first
                if q_elem.is_visible(timeout=1000):
                    question_text = q_elem.inner_text(timeout=1000).strip()
                    if question_text:
                        break
            except Exception:
                continue

        if not question_text:
            return False

        # Selectors cho các option
        option_selectors = [
            '.rqOption:not([disabled])',
            '.wk_ans_choice:not([disabled])',
            'div[role="button"][class*="answer"]:not([disabled])',
            'label[class*="option"]',
            'div[data-bi-area="quiz-option"]',
        ]

        options = []
        option_elements = []
        for sel in option_selectors:
            try:
                elements = tab.locator(sel).all()
                for el in elements:
                    try:
                        if el.is_visible(timeout=500):
                            text = el.inner_text(timeout=500).strip()
                            if text and text not in options:
                                options.append(text)
                                option_elements.append(el)
                    except Exception:
                        continue
                if options:
                    break
            except Exception:
                continue

        if not options:
            return False

        _log_info(f"[AI Solver] Câu hỏi: {question_text[:80]}...")
        _log_info(f"[AI Solver] Các đáp án: {options}")

        ai_answer = ask_local_ai(question_text, options)
        if ai_answer:
            _log_info(f"[AI Solver] AI gợi ý: {ai_answer}")
            # Click vào element chứa đáp án đúng
            for idx, opt_text in enumerate(options):
                if opt_text.lower() == ai_answer.lower() or ai_answer.lower() in opt_text.lower():
                    try:
                        option_elements[idx].click(force=True, timeout=2000)
                        human_delay(mean=1.5, std_dev=0.3, min_val=1.0)
                        return True
                    except Exception:
                        pass

    except Exception as e:
        _log_warning(f"[AI Solver] Lỗi khi giải quiz: {e}")

    return False