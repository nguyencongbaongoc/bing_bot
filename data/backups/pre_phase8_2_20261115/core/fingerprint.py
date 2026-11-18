"""
core/fingerprint.py - Deterministic Seeded Anti-Detect Fingerprinting
Cố định thông số phần cứng và tiêm nhiễu vi mô dựa trên Seed định danh.
"""
import hashlib
import random
from typing import Dict, Any


def generate_seed_hash(account_id: str = "primary_account") -> int:
    """Tạo số nguyên seed từ account_id để khởi tạo PRNG độc nhất."""
    return int(hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:8], 16)


def get_deterministic_hardware_profile(account_id: str = "primary_account") -> Dict[str, Any]:
    rng = random.Random(generate_seed_hash(account_id))
    
    # Tạo giá trị nhiễu cố định theo seed
    canvas_noise_r = rng.uniform(-0.0001, 0.0001)
    canvas_noise_g = rng.uniform(-0.0001, 0.0001)
    canvas_noise_b = rng.uniform(-0.0001, 0.0001)
    audio_noise = rng.uniform(0.00001, 0.00005)
    
    return {
        "hardware_concurrency": rng.choice([8, 12, 16]),
        "device_memory": rng.choice([8, 16]),
        "canvas_noise": (canvas_noise_r, canvas_noise_g, canvas_noise_b),
        "audio_noise": audio_noise,
    }


def inject_stealth_fingerprint(context, account_id: str = "primary_account"):
    """Tiêm script can thiệp DOM ở Isolated World trước khi trang nạp bất kỳ mã JS nào."""
    profile = get_deterministic_hardware_profile(account_id)
    r_noise, g_noise, b_noise = profile["canvas_noise"]
    a_noise = profile["audio_noise"]
    cpu_cores = profile["hardware_concurrency"]
    ram_gb = profile["device_memory"]

    stealth_js = f"""
    (() => {{
        // 1. Cố định Hardware Concurrency & Memory
        Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {cpu_cores} }});
        Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {ram_gb} }});

        // 2. Chặn cờ WebDriver & Automation
        delete Object.getPrototypeOf(navigator).webdriver;
        Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});

        // 3. Tiêm nhiễu Canvas deterministic (không làm đổi hình ảnh nhưng làm đổi Hash CRC32)
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type, ...args) {{
            const ctx = this.getContext('2d');
            if (ctx) {{
                const imgData = ctx.getImageData(0, 0, Math.min(this.width, 10), Math.min(this.height, 10));
                for (let i = 0; i < imgData.data.length; i += 4) {{
                    imgData.data[i] = Math.min(255, Math.max(0, imgData.data[i] + {r_noise}));
                    imgData.data[i+1] = Math.min(255, Math.max(0, imgData.data[i+1] + {g_noise}));
                    imgData.data[i+2] = Math.min(255, Math.max(0, imgData.data[i+2] + {b_noise}));
                }}
                ctx.putImageData(imgData, 0, 0);
            }}
            return origToDataURL.apply(this, [type, ...args]);
        }};

        // 4. Tiêm nhiễu AudioContext Fingerprint
        if (window.AudioBuffer) {{
            const origGetChannelData = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function(channel) {{
                const data = origGetChannelData.apply(this, [channel]);
                for (let i = 0; i < Math.min(data.length, 100); i++) {{
                    data[i] += {a_noise};
                }}
                return data;
            }};
        }}
    }})();
    """
    context.add_init_script(stealth_js)