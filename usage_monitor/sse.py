"""서버→브라우저 SSE. 변경이 생기면 스냅샷 전체를 보낸다(화면 하나, 수 KB라 변경분 계산보다 단순하다)."""
import asyncio
import json


class Hub:
    def __init__(self):
        self._queues = set()

    def publish(self, event, data):
        msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
        for q in list(self._queues):
            if q.qsize() > 20:      # 읽지 않는 클라이언트는 밀린 것을 버린다
                while not q.empty():
                    q.get_nowait()
            q.put_nowait(msg)

    async def stream(self, first_message):
        q = asyncio.Queue()
        self._queues.add(q)
        try:
            yield first_message
            while True:
                yield await q.get()
        finally:
            self._queues.discard(q)

    @property
    def clients(self):
        return len(self._queues)
