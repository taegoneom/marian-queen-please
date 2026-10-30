"""OS CSPRNG and opt-in ANU Quantum Numbers. No background prefetch."""
import json
import os
import queue
import re
import secrets
import threading
from contextlib import contextmanager
import urllib.request
import urllib.error
import time
from .config import MIN_QRNG_REQUEST_INTERVAL


class RNGError(RuntimeError):
    pass


class RateLimitError(RNGError):
    pass


class CSPRNG:
    last_mode = "CSPRNG"
    last_warning = ""

    def randbelow(self, n):
        self.last_mode, self.last_warning = "CSPRNG", ""
        return secrets.randbelow(n)


class QuantumRNG:
    ENDPOINT = "https://api.quantumnumbers.anu.edu.au/?length=64&type=uint8"

    def __init__(self, timeout=5, fallback=False, fetch=None, provider="anu", min_interval=MIN_QRNG_REQUEST_INTERVAL):
        self.timeout, self.fallback = timeout, fallback
        self.provider = provider
        self.provider_name = {"anu": "ANU", "lfdr": "LfD / Quantis"}[provider]
        self.fetch = fetch or self._http
        self.last_mode, self.last_warning = f"QRNG ({self.provider_name})", ""
        self._batch_active=False
        self._batch_values=[]
        self.min_interval=max(MIN_QRNG_REQUEST_INTERVAL,min_interval)
        self._next_request_at=0.0
        self._request_lock=threading.Lock()

    @contextmanager
    def batch(self):
        """Share one fresh response within one explicitly observed ten-pull."""
        self._batch_active=True
        self._batch_values=[]
        try:
            yield
        finally:
            self._batch_active=False
            self._batch_values=[]

    def _take_batch(self,n):
        limit=(2**32//n)*n
        while self._batch_values:
            v=self._batch_values.pop(0)
            if v<limit:
                self.last_mode=f'QRNG ({self.provider_name})'
                return v%n
        raise RNGError('이번 모집의 QRNG 난수 블록을 소진했습니다.')

    def _http(self, timeout):
        headers = {"User-Agent": "NikkeObserver/1.0"}
        endpoint = "https://lfdr.de/qrng_api/qrng?length=64&format=HEX"
        if self.provider == "anu":
            key = os.environ.get("ANU_QRNG_API_KEY", "").strip()
            if not key:
                raise RNGError("ANU_QRNG_API_KEY 환경 변수에 API 키를 설정하세요.")
            endpoint = self.ENDPOINT
            headers["x-api-key"] = key
        req = urllib.request.Request(endpoint, headers=headers)
        # Never forward credentials to a redirected host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise RNGError("QRNG 리디렉션 거부")
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise RNGError("QRNG 응답 크기 오류")
            payload = json.loads(raw)
            return self.normalize_lfdr(payload) if self.provider == "lfdr" else payload

    @staticmethod
    def normalize_lfdr(payload):
        if not isinstance(payload, dict) or type(payload.get("length")) is not int or payload["length"] != 64:
            raise RNGError("LfD QRNG 응답 길이 오류")
        value = payload.get("qrn")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{128}", value):
            raise RNGError("LfD QRNG HEX 데이터 오류")
        return {"success": True, "type": "uint8", "data": list(bytes.fromhex(value))}

    def randbelow(self, n):
        if not 0 < n <= 2**32:
            raise RNGError("QRNG 추첨 범위 오류")
        self.last_warning = ""
        if self._batch_active and self._batch_values:
            return self._take_batch(n)
        delay=max(0,self._next_request_at-time.monotonic())
        if delay:
            time.sleep(delay)
        if not self._request_lock.acquire(blocking=False):
            raise RNGError('이전 QRNG 요청이 아직 종료되지 않았습니다. 잠시 후 재시작하세요.')
        self._next_request_at=time.monotonic()+self.min_interval
        mailbox = queue.Queue(maxsize=1)

        def request():
            try:
                mailbox.put((True, self.fetch(self.timeout)))
            except urllib.error.HTTPError as exc:
                mailbox.put((False, exc.code))
            except Exception:
                # Do not expose headers, keys, or response bodies in an error.
                mailbox.put((False, None))
            finally:
                self._request_lock.release()

        # Queue deadline bounds DNS + connect + read as a whole. Late responses
        # are discarded; a timed-out worker never draws or writes an outcome.
        threading.Thread(target=request, daemon=True).start()
        try:
            ok, payload = mailbox.get(timeout=self.timeout)
            if not ok:
                if payload in (403,429):
                    raise RateLimitError(f'QRNG HTTP {payload}: 제공자 접근 제한. 자동 재시도 없이 중지합니다.')
                raise RNGError("API 키·네트워크·요청 한도를 확인하세요.")
            if not isinstance(payload, dict) or payload.get("success") is not True or payload.get("type") != "uint8":
                raise RNGError("QRNG 응답 형식 오류")
            data = payload.get("data")
            if not isinstance(data, list) or len(data) != 64 or any(type(v) is not int or not 0 <= v <= 255 for v in data):
                raise RNGError("QRNG 데이터 오류")
            limit = (2**32 // n) * n
            if self._batch_active:
                self._batch_values=[int.from_bytes(bytes(data[i:i+4]),'big') for i in range(0,64,4)]
                return self._take_batch(n)
            for i in range(0, 64, 4):
                v = int.from_bytes(bytes(data[i:i + 4]), "big")
                if v < limit:
                    self.last_mode = f"QRNG ({self.provider_name})"
                    return v % n
            raise RNGError("QRNG rejection sampling 재시도 필요")
        except (queue.Empty, RNGError) as exc:
            if isinstance(exc,RateLimitError):raise
            why = "QRNG 요청 제한 시간 초과" if isinstance(exc, queue.Empty) else str(exc)
            if not self.fallback:
                raise RNGError(why) from None
            self.last_mode = "CSPRNG (QRNG fallback)"
            self.last_warning = why + " → 설정에 따라 OS CSPRNG로 전환했습니다."
            return secrets.randbelow(n)


def build(config):
    if config["rng_mode"] == "qrng":
        return QuantumRNG(config["qrng_timeout_seconds"], config["qrng_fallback"], provider=config["qrng_provider"], min_interval=config['qrng_min_request_interval_seconds'])
    return CSPRNG()
