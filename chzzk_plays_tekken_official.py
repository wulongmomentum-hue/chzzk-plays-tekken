"""
치지직 '공식 API'로 채팅을 받아 철권 조종하기 (Chzzk Plays Tekken - Official)

[처음 한 번만] 치지직 개발자 센터 준비
  1) https://developers.chzzk.naver.com 에서 애플리케이션 등록
  2) 로그인 리디렉션 URL:  http://localhost:8080/callback   (정확히 이대로 입력)
  3) API Scope: '채팅 메시지 조회' 체크 (승인이 필요하면 승인 후 사용 가능)
  4) 발급된 Client ID / Client Secret 을 아래 [설정]에 붙여넣기

[실행]
  pip install chzzkpy pydirectinput
  python chzzk_plays_tekken_official.py
  → 브라우저가 열리면 치지직 로그인 + '동의' 클릭 → "연결 완료" 문구가 뜨면 성공
  → 철권 창을 클릭해 활성화. 끝낼 때는 이 창에서 Ctrl+C
  (인증 토큰은 하루 유효하니 방송마다 한 번씩 동의하면 됩니다)
"""

import asyncio
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

import sys
import traceback


def _pause_and_exit(code=1):
    input("\n엔터를 누르면 창이 닫힙니다...")
    sys.exit(code)


try:
    import pydirectinput
    from chzzkpy import Client, Message, UserPermission
except ImportError as e:
    print("❌ 필요한 프로그램이 설치되지 않았습니다:", e)
    print("   명령 프롬프트(cmd)를 열고 아래를 입력한 뒤 다시 실행하세요:")
    print("   py -m pip install chzzkpy pydirectinput")
    _pause_and_exit()

# ======================= [설정] =======================

CLIENT_ID = "여기에_Client_ID"
CLIENT_SECRET = "여기에_Client_Secret"
REDIRECT_PORT = 8080
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"

# 모드: "혼돈" = 채팅 오는 대로 바로 실행 / "민주주의" = 일정 시간 모아서 1등 명령만 실행
MODE = "혼돈"
VOTE_SECONDS = 3          # 민주주의 모드에서 투표 모으는 시간(초)

USER_COOLDOWN = 1.0       # 한 사람이 다시 명령할 수 있기까지 걸리는 시간(초) - 도배 방지
MAX_QUEUE = 10            # 혼돈 모드에서 밀려 있을 수 있는 최대 명령 수 (넘치면 버림 → 딜레이 방지)

# 내 캐릭터 위치: 화면 오른쪽(2P)이면 "오른쪽", 왼쪽(1P)이면 "왼쪽" (앞/뒤 방향 결정)
MY_SIDE = "오른쪽"
SIDE_CHANGE_COOLDOWN = 3.0   # 사이드변경 명령 후 다음 변경까지 기다리는 시간(초) - 장난 방지

# 철권 키보드 배치 (게임 설정 > 컨트롤과 똑같이 맞추세요)
KEY_UP = "w"
KEY_DOWN = "s"
KEY_LEFT = "a"
KEY_RIGHT = "d"
KEY_1 = "u"   # 왼손 (LP)
KEY_2 = "i"   # 오른손 (RP)
KEY_3 = "j"   # 왼발 (LK)
KEY_4 = "k"   # 오른발 (RK)

TAP = 0.06         # 버튼 한 번 누르는 시간(초)
STEP = 0.034       # 커맨드(6n23 등)에서 방향 하나를 유지하는 시간(초) ≈ 2프레임
MOVE_HOLD = 0.35   # 방향 하나만 칠 때 누르고 있는 시간(초)
CROUCH_HOLD = 0.5  # 앉기 계열(1, 2, 3 방향 단독) 유지 시간(초)
MAX_STEPS = 20     # 커맨드 한 줄에 들어갈 수 있는 최대 입력 수 (장난 방지)
BUTTON_GAP = 0.04  # 버튼을 차례로 누를 때(lk lp rp) 버튼 사이 간격(초)
CHAIN_HOLD = 0.1   # 6n23n6n23 처럼 이어지는 커맨드에서 ↘ 를 유지하는 시간(초). 안 나가면 늘리기
COMMAND_GAP = 0.1  # 서로 다른 채팅 명령 사이에 쉬는 시간(초). 명령끼리 섞이지 않게 함

# 연속백대시: 4 4 1 n (4 1 n) 반복. 안 움직이면 BACKDASH_STEP 을 0.05 → 0.07 처럼 늘려 보세요.
BACKDASH_COUNT = 5     # 백대시 횟수
BACKDASH_STEP = 0.05   # 연속백대시에서 방향 하나를 유지하는 시간(초) ≈ 3프레임

# OBS 오버레이 (overlay.html 이 같은 폴더에 있어야 함)
#   원컴: OVERLAY_HOST = "127.0.0.1"  → OBS URL  http://127.0.0.1:8765
#   투컴: OVERLAY_HOST = "0.0.0.0"    → 송출 PC OBS URL  http://(게임PC IP):8765  (봇 실행 시 주소가 표시됨)
OVERLAY_ENABLED = True
OVERLAY_HOST = "0.0.0.0"
OVERLAY_PORT = 8765

# ======================================================

import re

pydirectinput.PAUSE = 0  # 입력 사이 기본 딜레이 제거

BUTTON_KEYS = {"1": KEY_1, "2": KEY_2, "3": KEY_3, "4": KEY_4}

# 버튼 영문 표기 (대소문자 무관)
BUTTON_WORDS = {"lp": "1", "rp": "2", "lk": "3", "rk": "4", "ap": "12", "ak": "34"}

# 한글 방향 표현 → 숫자패드 방향 (긴 단어부터 검사함)
#   7 8 9      윗뒤대각  위  윗앞대각
#   4 n 6  =   뒤       중립  앞
#   1 2 3      뒷대각   아래  앞대각
DIR_WORDS = {
    "윗앞대각": "9", "앞윗대각": "9", "윗대각": "9", "앞점프": "9", "앞위": "9",
    "윗뒤대각": "7", "윗뒷대각": "7", "뒤윗대각": "7", "뒷점프": "7", "뒤점프": "7", "뒤위": "7",
    "앞대각": "3", "앞아래": "3", "아랫앞대각": "3",
    "뒷대각": "1", "뒤대각": "1", "뒤아래": "1", "아랫뒤대각": "1",
    "앞으로": "6", "뒤로": "4",
    "점프": "8", "위": "8",
    "앉기": "2", "앉아": "2", "아래": "2",
    "중립": "n",
    "앞": "6", "뒤": "4",
}
_DIR_WORDS_SORTED = sorted(DIR_WORDS, key=len, reverse=True)


def _dir_keys(d):
    """숫자패드 방향 → 눌러야 할 키 집합."""
    fwd = KEY_RIGHT if MY_SIDE == "왼쪽" else KEY_LEFT
    back = KEY_LEFT if MY_SIDE == "왼쪽" else KEY_RIGHT
    table = {
        "1": {KEY_DOWN, back}, "2": {KEY_DOWN}, "3": {KEY_DOWN, fwd},
        "4": {back}, "5": set(), "n": set(), "6": {fwd},
        "7": {KEY_UP, back}, "8": {KEY_UP}, "9": {KEY_UP, fwd},
    }
    return table[d]


def _set_held(held, target):
    for k in held - target:
        pydirectinput.keyUp(k)
    for k in target - held:
        pydirectinput.keyDown(k)
    return set(target)


# ---------------- OBS 오버레이 서버 ----------------
import json
import os

_subscribers = set()


def emit(event):
    """오버레이로 이벤트 전송 (연결된 OBS 화면이 없으면 아무 일도 안 함)."""
    data = json.dumps(event, ensure_ascii=False)
    for q in list(_subscribers):
        try:
            q.put_nowait(data)
        except Exception:
            pass


def _status():
    return {"type": "status", "side": MY_SIDE, "mode": MODE}


async def start_overlay():
    if not OVERLAY_ENABLED:
        return
    try:
        from aiohttp import web
    except ImportError:
        print("⚠️ 오버레이를 켜려면 aiohttp 가 필요합니다 (py -m pip install aiohttp)")
        return
    here = os.path.dirname(os.path.abspath(__file__))

    async def index(request):
        path = os.path.join(here, "overlay.html")
        if not os.path.exists(path):
            return web.Response(text="overlay.html 파일을 봇과 같은 폴더에 넣어 주세요.")
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})

    async def events(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(request)
        q = asyncio.Queue(maxsize=200)
        _subscribers.add(q)
        try:
            await resp.write(f"data: {json.dumps(_status(), ensure_ascii=False)}\n\n".encode())
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    await resp.write(f"data: {data}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            _subscribers.discard(q)
        return resp

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/events", events)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, OVERLAY_HOST, OVERLAY_PORT).start()
        print(f"🖥️ OBS 오버레이 켜짐 (같은 PC의 OBS):   http://127.0.0.1:{OVERLAY_PORT}")
        if OVERLAY_HOST != "127.0.0.1":
            import socket
            try:
                sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sk.connect(("8.8.8.8", 80))  # 실제로 전송하지 않음, 내 PC의 공유기 내부 IP 확인용
                lan_ip = sk.getsockname()[0]
                sk.close()
                print(f"🖥️ 송출 PC의 OBS에서는 이 주소:        http://{lan_ip}:{OVERLAY_PORT}")
            except OSError:
                print("   송출 PC에서는 http://(이 PC의 IP):%d  (cmd 에서 ipconfig 로 IPv4 주소 확인)" % OVERLAY_PORT)
    except OSError:
        print(f"⚠️ {OVERLAY_PORT} 포트를 쓸 수 없어 오버레이를 못 켰어요. 봇이 이미 켜져 있는지 확인하세요.")


_last_side_change = 0.0
SIDE_TOGGLE = ("사이드변경", "자리변경", "ㅅㅇㄷㅂㄱ")
SIDE_LEFT = ("왼쪽사이드", "사이드왼쪽", "1p사이드")
SIDE_RIGHT = ("오른쪽사이드", "사이드오른쪽", "2p사이드")


def handle_side(text, user):
    """사이드 명령이면 처리하고 True. 큐/투표를 거치지 않고 즉시 적용."""
    global MY_SIDE, _last_side_change
    raw = (text or "").strip().lower().replace(" ", "")
    if raw not in SIDE_TOGGLE + SIDE_LEFT + SIDE_RIGHT:
        return False
    now = time.monotonic()
    if now - _last_side_change < SIDE_CHANGE_COOLDOWN:
        return True  # 쿨타임 중이면 무시
    if raw in SIDE_TOGGLE:
        new_side = "왼쪽" if MY_SIDE == "오른쪽" else "오른쪽"
    else:
        new_side = "왼쪽" if raw in SIDE_LEFT else "오른쪽"
    if new_side != MY_SIDE:
        MY_SIDE = new_side
        _last_side_change = now
        print(f"🔄 {user}: 사이드 변경 → 내 캐릭터 {MY_SIDE} ({'1P' if MY_SIDE == '왼쪽' else '2P'})")
        emit({"type": "side", "user": user, "side": MY_SIDE})
    return True


def _events(dirs, btns=()):
    """방향 목록 + (마지막 방향과 동시에 누를) 버튼 → 이벤트 목록."""
    evs = [[d, set()] for d in dirs]
    if btns:
        if evs:
            evs[-1][1].update(btns)
        else:
            evs.append([None, set(btns)])
    return evs


def perform(events, step=None):
    """이벤트를 차례로 입력. 이벤트 = [방향 또는 None, 동시에 누를 버튼 집합]."""
    step = STEP if step is None else step
    held = set()
    try:
        # 방향 하나 + 버튼 없음 → 이동/앉기/점프처럼 조금 길게 유지
        if len(events) == 1 and events[0][0] and not events[0][1]:
            d = events[0][0]
            hold = CROUCH_HOLD if d in "123" else (TAP if d in "789" else MOVE_HOLD)
            held = _set_held(held, _dir_keys(d))
            time.sleep(hold)
            return

        # 같은 방향이 연속이면 사이에 중립을 넣어야 두 번 입력됨 (예: 44 → 4 n 4)
        seq = []
        for d, b in events:
            if seq and d and d == seq[-1][0] and d not in "n5" and not seq[-1][1]:
                seq.append(["n", set()])
            seq.append([d, b])

        last = len(seq) - 1
        for i, (d, b) in enumerate(seq):
            dk = _dir_keys(d) if d else set()
            if b:
                held = _set_held(held, dk | {BUTTON_KEYS[x] for x in b})
                time.sleep(TAP)
                held = _set_held(held, dk)  # 버튼만 떼고 방향은 유지
                if i < last:
                    time.sleep(BUTTON_GAP)
            else:
                held = _set_held(held, dk)
                hold = step * 2 if i == last else step
                # 6n23 n 6n23 처럼 이어지는 커맨드: ↘/↙ 를 조금 더 길게 눌러 앞 동작이 나오게 함
                if d in ("3", "1") and i + 2 <= last and seq[i + 1][0] in ("n", "5"):
                    hold = max(hold, CHAIN_HOLD)
                time.sleep(hold)
    finally:
        _set_held(held, set())


_ARROWS = {"1": "↙", "2": "↓", "3": "↘", "4": "←", "5": "·", "n": "·",
           "6": "→", "7": "↖", "8": "↑", "9": "↗"}


def _label(events):
    out, prev_btn = "", False
    for d, b in events:
        piece = (_ARROWS[d] if d else "") + (("+" if d else "") + "+".join(sorted(b)) if b else "")
        if out and (prev_btn or d is None):
            out += ","
        out += piece
        prev_btn = bool(b)
    return out


def parse(text):
    """채팅 → (표시용 이름, 실행 함수). 명령이 아니면 None."""
    raw = (text or "").strip().lower().replace(" ", "")
    if not raw or len(raw) > 40:
        return None

    # 특수 명령
    if raw == "잡기":
        return "잡기", lambda: perform(_events([], ["1", "3"]))
    if raw in ("대시", "ㄷㅅ"):
        return "대시", lambda: perform(_events(["6", "n", "6"]))
    if raw in ("백대시", "백대쉬", "ㅂㄷㅅ"):
        return "백대시", lambda: perform(_events(["4", "n", "4"]))
    if raw in ("연속백대시", "연속백대쉬", "ㅇㅅㅂㄷㅅ"):
        # 4414 414 414 414 414  (414 사이의 4 4 는 자동으로 중립을 끼워 두 번 입력)
        seq = ["4", "4", "1", "4"] + ["4", "1", "4"] * (BACKDASH_COUNT - 1)
        return f"연속백대시x{BACKDASH_COUNT}", lambda: perform(_events(seq), BACKDASH_STEP)

    # ① 버튼만: 1 / 2 / 1+2 / 3+4 / 1+2+3+4 ...  (1~4 숫자를 + 로만 연결) → 동시 입력
    if re.fullmatch(r"[1-4](\+[1-4])*", raw):
        btns = sorted(set(raw.split("+")))
        return "+".join(btns), lambda: perform(_events([], btns))

    # ② 토큰 분해: 한글 방향 / 영문 버튼 / 숫자 / n / +
    tokens, i, used_korean = [], 0, False
    while i < len(raw):
        for w in _DIR_WORDS_SORTED:
            if raw.startswith(w, i):
                tokens.append(("dir", DIR_WORDS[w]))
                i += len(w)
                used_korean = True
                break
        else:
            for w, b in BUTTON_WORDS.items():
                if raw.startswith(w, i):
                    for k, c in enumerate(b):  # ap = 1+2 (동시)
                        if k:
                            tokens.append(("plus", "+"))
                        tokens.append(("btn", c))
                    i += len(w)
                    break
            else:
                c = raw[i]
                if c in "123456789n":
                    tokens.append(("num", c))
                elif c == "+":
                    tokens.append(("plus", c))
                else:
                    return None  # 모르는 글자가 섞이면 명령이 아님
                i += 1

    # ③ 이벤트 만들기
    #    + 로 이은 것 = 동시 / 붙여 쓴 버튼 = 차례로 / 방향 바로 뒤 버튼 = 그 방향과 동시
    events, prev = [], None
    for kind, v in tokens:
        if kind == "plus":
            if not events or prev == "plus":
                return None
            prev = "plus"
            continue
        if kind == "num":
            # + 뒤 숫자, 또는 한글 방향을 쓴 명령의 숫자 → 버튼 / 그 외 → 숫자패드 방향
            kind = "btn" if (prev == "plus" or used_korean) else "dir"
            if kind == "btn" and v not in BUTTON_KEYS:
                return None
        if kind == "dir":
            if prev == "plus":
                return None
            events.append([v, set()])
        else:
            if prev == "plus" or (prev == "dir" and not events[-1][1]):
                events[-1][1].add(v)
            else:
                events.append([None, {v}])
        prev = kind
    if prev == "plus" or not events or len(events) > MAX_STEPS:
        return None

    label = _label(events)
    return label, lambda: perform(events)


queue: asyncio.Queue = asyncio.Queue()
votes: Counter = Counter()
vote_actions: dict = {}
last_used: dict = {}


# ---------------- OAuth: 브라우저 동의 → 인증 코드 받기 ----------------

def get_authorization_code():
    state = secrets.token_urlsafe(12)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                result["code"] = qs["code"][0]
                result["state"] = qs.get("state", [""])[0]
                body = "연결 완료! 이 창은 닫아도 됩니다.".encode("utf-8")
            else:
                body = "인증 코드가 없습니다. 다시 시도해 주세요.".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", REDIRECT_PORT), Handler)
    url = "https://chzzk.naver.com/account-interlock?" + urllib.parse.urlencode(
        {"clientId": CLIENT_ID, "redirectUri": REDIRECT_URI, "state": state}
    )
    print("🌐 브라우저에서 치지직 동의 화면을 엽니다. 안 열리면 아래 주소를 직접 여세요:")
    print(url)
    webbrowser.open(url)

    while "code" not in result:
        server.handle_request()
    server.server_close()

    if result["state"] != state:
        raise RuntimeError("state 값이 일치하지 않습니다. 다시 실행해 주세요.")
    return result["code"], result["state"]


# ---------------- 채팅 처리 ----------------

client = Client(CLIENT_ID, CLIENT_SECRET)


@client.event
async def on_chat(message: Message):
    _profile = getattr(message, "profile", None)
    if handle_side(message.content, getattr(_profile, "nickname", None) or "익명"):
        return
    parsed = parse(message.content or "")
    if parsed is None:
        return
    cmd, action = parsed

    profile = getattr(message, "profile", None)
    user = getattr(profile, "nickname", None) or "익명"
    now = time.monotonic()
    if now - last_used.get(user, 0) < USER_COOLDOWN:
        return
    last_used[user] = now

    if MODE == "민주주의":
        votes[cmd] += 1
        vote_actions[cmd] = action
        emit({"type": "votes", "top": votes.most_common(3), "total": sum(votes.values())})
    elif queue.qsize() < MAX_QUEUE:
        await queue.put((user, cmd, action))


async def chaos_worker():
    while True:
        user, cmd, action = await queue.get()
        print(f"🎮 {user}: {cmd}")
        emit({"type": "cmd", "user": user, "cmd": cmd})
        await asyncio.to_thread(action)
        await asyncio.sleep(COMMAND_GAP)


async def democracy_worker():
    while True:
        emit({"type": "round", "seconds": VOTE_SECONDS})
        await asyncio.sleep(VOTE_SECONDS)
        if not votes:
            continue
        cmd, count = votes.most_common(1)[0]
        total = sum(votes.values())
        action = vote_actions[cmd]
        votes.clear()
        vote_actions.clear()
        print(f"🗳️ 결과: '{cmd}' ({count}/{total}표) 실행!")
        emit({"type": "result", "cmd": cmd, "count": count, "total": total})
        await asyncio.to_thread(action)


async def main():
    await start_overlay()
    code, state = await asyncio.to_thread(get_authorization_code)
    user_client = await client.generate_user_client(code, state)
    asyncio.create_task(democracy_worker() if MODE == "민주주의" else chaos_worker())
    print(f"✅ 채팅 연결 중... 모드: {MODE}  |  철권 창을 클릭해 활성화해 두세요. (종료: Ctrl+C)")
    await user_client.connect(UserPermission(chat=True))


if __name__ == "__main__":
    print("치지직(공식 API) 채팅 → 철권 조종 봇 시작")
    print(f"캐릭터 위치: {MY_SIDE} (채팅 '사이드변경'으로 전환)  |  예) 1, 1+2, lklprp, 앞대각4, 3rk, 6n23rp, 236lp, 연속백대시")
    if "여기에" in CLIENT_ID + CLIENT_SECRET:
        print("❌ 파일 위쪽 [설정]의 CLIENT_ID / CLIENT_SECRET 을 아직 안 채우셨어요.")
        print("   이 파일을 메모장으로 열어 '여기에_...' 부분을 바꾼 뒤 저장하고 다시 실행하세요.")
        _pause_and_exit()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n종료했습니다.")
    except Exception:
        print("\n❌ 에러가 발생했습니다. 아래 내용을 그대로 복사해서 보내주세요:\n")
        traceback.print_exc()
    _pause_and_exit(0)
