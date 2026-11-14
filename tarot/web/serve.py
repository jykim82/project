#!/usr/bin/env python3
"""타로의 숲 정적 서버 — HTML은 항상 재검증(no-cache), 무거운 에셋은 캐시 허용."""
import http.server
import functools

WEB_ROOT = "/Users/jykim/taro-work/tarot/web"
PORT = 8080


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        path = self.path.split("?")[0]
        if path.endswith((".html", "/")):
            # 매 방문마다 서버와 재검증 → 배포 즉시 반영, 재방문 시 304로 가볍게
            self.send_header("Cache-Control", "no-cache")
        else:
            # 카드 이미지·폰트·오디오는 넉넉히 캐시 (내용 바뀌면 드묾)
            self.send_header("Cache-Control", "public, max-age=86400")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # 무기록 원칙: 접근 로그 남기지 않음


if __name__ == "__main__":
    handler = functools.partial(Handler, directory=WEB_ROOT)
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), handler).serve_forever()
