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
MAX_STEPS = 20     # 커맨드 한 줄에 들어갈 수 있는 최대 입력 수 / '>' 로 이을 수 있는 최대 명령 수 (장난 방지)
MAX_COMMAND_LENGTH = 40   # 명령 하나의 최대 글자 수
MAX_CHAT_LENGTH = 240     # '>' 로 이은 전체 채팅의 최대 글자 수
MAX_HOLD_FRAMES = 120     # '@' 뒤에 지정할 수 있는 최대 유지 프레임 (2초)
SEQUENCE_GAP_FRAMES = 1   # '>' 사이에 모든 키를 뗀 상태로 기다리는 프레임
BUTTON_GAP = 0.04  # 버튼을 차례로 누를 때(lk lp rp) 버튼 사이 간격(초)
CHAIN_HOLD = 0.1   # 6n23n6n23 처럼 이어지는 커맨드에서 ↘ 를 유지하는 시간(초). 안 나가면 늘리기
COMMAND_GAP = 0.1  # 서로 다른 채팅 명령 사이에 쉬는 시간(초). 명령끼리 섞이지 않게 함

# 연속백대시·웨이브 (ADR-0015): 사람이 직접 입력한 기록(인풋 디스플레이)을 프레임 단위로 옮긴 것.
#   (방향, 프레임 수) 목록. 방향은 숫자패드·캐릭터 기준(4=뒤, 6=앞, 1=뒷대각, 3=앞대각, n=중립). 60프레임 = 1초.

# 연속백대시: 4(4F) n(6F) → [4(9F) 1(4F) 4(2F) n(5F)] x BACKDASH_COUNT   (= 4414 414 414 ...)
#   백대시가 덜 나가면 BACKDASH_SET 의 n 프레임을, 한 번도 안 움직이면 첫 4 프레임을 늘리세요.
BACKDASH_COUNT = 5
BACKDASH_FIRST = [("4", 4), ("n", 6)]
BACKDASH_SET = [("4", 9), ("1", 4), ("4", 2), ("n", 5)]

# 웨이브: 6n23 뒤에 대시(6 n)가 섞인 6n236n 을 반복
#   [6(3F) n(2F) 2(1F) 3(2F) 6(3F) n(4F)] x WAVE_COUNT
#   앉아대시가 안 나가면 2(↓) 프레임을 2로, 이어지지 않으면 마지막 n 프레임을 늘리세요.
WAVE_COUNT = 3
WAVE_SET = [("6", 3), ("n", 2), ("2", 1), ("3", 2), ("6", 3), ("n", 4)]

# ---- 기본 스텝 (ADR-0012) ----
# 철권은 위(8)/아래(2)를 '짧게 톡' 치면 횡이동, '길게' 누르면 점프/앉기가 됩니다.
SIDESTEP_TAP = 0.034   # 횡이동 때 위/아래를 누르는 시간(초) ≈ 2프레임. 점프·앉기가 나가면 줄이고, 안 움직이면 늘리기
SIDEWALK_HOLD = 0.6    # 횡걷기: 횡이동 직후 같은 방향을 계속 누르는 시간(초)
WALK_HOLD = 0.6        # 앞걷기/뒷걷기 유지 시간(초)
JUMP_HOLD = 0.15       # 점프(7·8·9 단독) 누르는 시간(초). 너무 짧으면 점프 대신 횡이동이 됨

# ---- 기술 후딜레이 (ADR-0012) ----
# 기술이 끝나기 전에 다음 명령을 넣으면 입력이 씹히거나 엉뚱한 기술이 나갑니다.
# 명령을 실행한 뒤 아래 프레임만큼 기다렸다가 다음 명령을 넣습니다. (60프레임 = 1초)
RECOVERY_ENABLED = True
FPS = 60
RECOVERY_SCALE = 1.0   # 전체 후딜 배율 (1.0 = 그대로, 0.5 = 절반만 기다림)
RECOVERY_MAX = 1.5     # 후딜 최대(초). 아무리 긴 기술이어도 이보다 오래 기다리지 않음
RECOVERY_FRAMES = {
    "이동": 0,      # 방향 단독(이동·앉기)·걷기 — 누르는 시간 자체가 동작 시간
    "점프": 30,     # 점프 후 착지까지
    "스텝": 16,     # 횡이동 한 번
    "대시": 4,
    "백대시": 8,
    "펀치": 18,     # 1·2 단독
    "킥": 26,       # 3·4 단독
    "동시": 30,     # 1+2, 3+4, 1+2+3+4 같은 동시 버튼
    "커맨드": 6,    # 방향이 들어간 기술(↘+2, 236lp 등)이면 추가
    "하단": 6,      # 마지막 버튼을 1·2·3 방향과 함께 누르면 추가 (앉은 자세 기술은 후딜이 긴 편)
    "연속": 12,     # 버튼을 차례로 누른 경우 2타째부터 한 번마다 추가 (lklprp 등)
    "잡기": 40,     # 1+3, 2+4, '잡기' (헛잡기는 후딜이 큼)
    "기상": 20,     # 일어나기
}
# 특정 기술만 따로 정하기: 로그/오버레이에 보이는 이름 → 후딜 프레임 (위 규칙보다 우선)
#   예) "↘+RP": 30,   "LP+RP": 34,   "↓+RK": 28,   "초풍": 32,   "백대시": 6
MOVE_RECOVERY = {
}

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


def perform(events, step=None, hold_frames=None):
    """이벤트를 차례로 입력. 이벤트 = [방향 또는 None, 동시에 누를 버튼 집합].
    hold_frames: '@N' 으로 지정한 경우 마지막 방향/버튼을 N프레임 동안 유지."""
    step = STEP if step is None else step
    held = set()
    try:
        # 방향 하나 + 버튼 없음 → 이동/앉기/점프처럼 조금 길게 유지
        if len(events) == 1 and events[0][0] and not events[0][1]:
            d = events[0][0]
            hold = CROUCH_HOLD if d in "123" else (JUMP_HOLD if d in "789" else MOVE_HOLD)
            if hold_frames is not None:
                hold = hold_frames / FPS
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
                time.sleep(hold_frames / FPS if hold_frames is not None and i == last else TAP)
                held = _set_held(held, dk)  # 버튼만 떼고 방향은 유지
                if i < last:
                    time.sleep(BUTTON_GAP)
            else:
                held = _set_held(held, dk)
                hold = step * 2 if i == last else step
                # 6n23 n 6n23 처럼 이어지는 커맨드: ↘/↙ 를 조금 더 길게 눌러 앞 동작이 나오게 함
                if d in ("3", "1") and i + 2 <= last and seq[i + 1][0] in ("n", "5"):
                    hold = max(hold, CHAIN_HOLD)
                if hold_frames is not None and i == last:
                    hold = hold_frames / FPS
                time.sleep(hold)
    finally:
        _set_held(held, set())


def sidestep(d, walk=0.0):
    """횡이동: 위(8)/아래(2)를 짧게 톡. walk > 0 이면 이어서 같은 방향을 눌러 횡걷기."""
    held = set()
    try:
        held = _set_held(held, _dir_keys(d))
        time.sleep(SIDESTEP_TAP)
        held = _set_held(held, set())
        if walk:
            time.sleep(STEP)
            held = _set_held(held, _dir_keys(d))
            time.sleep(walk)
    finally:
        _set_held(held, set())


def hold_dir(d, seconds):
    """방향 하나를 정해진 시간 동안 누르기 (앞걷기/뒷걷기)."""
    held = set()
    try:
        held = _set_held(held, _dir_keys(d))
        time.sleep(seconds)
    finally:
        _set_held(held, set())


def press_steps(steps):
    """(방향, 유지 초) 목록을 차례로 입력. 연속백대시·웨이브처럼 단계마다 딜레이가 다른 입력용."""
    held = set()
    try:
        for d, seconds in steps:
            held = _set_held(held, _dir_keys(d))
            time.sleep(seconds)
    finally:
        _set_held(held, set())


def _wait_until(t):
    """t(perf_counter 시각)까지 정밀 대기. 대부분은 sleep, 마지막 2ms 는 바쁜 대기 → 1프레임 입력도 정확히."""
    while True:
        left = t - time.perf_counter()
        if left <= 0:
            return
        if left > 0.003:
            time.sleep(left - 0.002)


def press_frames(steps):
    """(방향, 프레임 수) 목록을 프레임 단위로 입력. 시작 시각 기준 누적이라 오차가 쌓이지 않음."""
    held = set()
    t = time.perf_counter()
    try:
        for d, frames in steps:
            held = _set_held(held, _dir_keys(d))
            t += frames / FPS
            _wait_until(t)
    finally:
        _set_held(held, set())


def backdash_steps(count):
    """연속백대시: BACKDASH_FIRST + BACKDASH_SET x count (프레임 목록)."""
    return list(BACKDASH_FIRST) + list(BACKDASH_SET) * count


def wave_steps(count):
    """웨이브: WAVE_SET x count (프레임 목록)."""
    return list(WAVE_SET) * count


def recovery(label, events=None, kind=None):
    """명령 실행 후 기다릴 후딜레이(초). MOVE_RECOVERY > kind > 입력 모양으로 추정 순."""
    if not RECOVERY_ENABLED:
        return 0.0
    if label in MOVE_RECOVERY:
        frames = MOVE_RECOVERY[label]
    elif kind:
        frames = RECOVERY_FRAMES.get(kind, 0)
    else:
        events = events or []
        presses = [e for e in events if e[1]]
        if not presses:
            jump = len(events) == 1 and events[0][0] in ("7", "8", "9")
            frames = RECOVERY_FRAMES["점프" if jump else "이동"]
        else:
            d, btns = presses[-1]  # 마지막에 나가는 기술의 후딜이 다음 입력을 막음
            has_dir = any(e[0] not in (None, "n", "5") for e in events)
            if not has_dir and len(presses) == 1 and btns in ({"1", "3"}, {"2", "4"}):
                frames = RECOVERY_FRAMES["잡기"]
            elif len(btns) >= 2:
                frames = RECOVERY_FRAMES["동시"]
            elif btns <= {"1", "2"}:
                frames = RECOVERY_FRAMES["펀치"]
            else:
                frames = RECOVERY_FRAMES["킥"]
            if has_dir:
                frames += RECOVERY_FRAMES["커맨드"]
            if d in ("1", "2", "3"):
                frames += RECOVERY_FRAMES["하단"]
            frames += RECOVERY_FRAMES["연속"] * (len(presses) - 1)
    return min(frames / FPS * RECOVERY_SCALE, RECOVERY_MAX)


# 기본 스텝 명령 (띄어쓰기·대소문자 무시). 횡이동/횡걷기는 화면 안쪽=위, 바깥쪽=아래
SIDESTEP_UP = ("횡위", "위횡", "윗횡", "횡이동", "횡이동위", "횡", "스텝", "스텝위", "ss", "ssu")
SIDESTEP_DOWN = ("횡아래", "아래횡", "아랫횡", "횡이동아래", "스텝아래", "ssd")
SIDEWALK_UP = ("횡걷기", "횡걷기위", "위횡걷기", "횡보", "횡걸음")
SIDEWALK_DOWN = ("횡걷기아래", "아래횡걷기", "횡보아래", "횡걸음아래")
# 회전 방향 횡 (ADR-0018). 2P(오른쪽) 기준 반시계 = ↑(화면 안쪽), 시계 = ↓. 1P(왼쪽)이면 반대.
CCW_STEP = ("반시계횡신",)
CCW_WALK = ("반시계횡보", "반시계횡걸음")
CW_STEP = ("시계횡신",)
CW_WALK = ("시계횡보", "시계횡걸음")


def _rot_dir(clockwise):
    """시계/반시계 → 위(8)/아래(2). 실행하는 순간의 사이드 기준 (큐에서 기다리는 동안 사이드가 바뀌어도 맞게)."""
    if MY_SIDE == "오른쪽":
        return "2" if clockwise else "8"
    return "8" if clockwise else "2"
WALK_FWD = ("앞걷기", "전진")
WALK_BACK = ("뒷걷기", "뒤걷기", "후진")
CROUCH_DASH = ("앉아대시", "앉아대쉬", "ㅇㅇㄷㅅ")
WAKE_UP = ("일어나", "일어나기", "일어서", "기상")
EWGF = ("초풍", "ㅊㅍ")
WAVE = ("웨이브", "웨이브대시", "웨이브대쉬", "ㅇㅇㅂ")


BUTTON_NAMES = {"1": "LP", "2": "RP", "3": "LK", "4": "RK"}  # 표시 이름용 (숫자는 방향과 헷갈리므로)
_ARROWS = {"1": "↙", "2": "↓", "3": "↘", "4": "←", "5": "·", "n": "·",
           "6": "→", "7": "↖", "8": "↑", "9": "↗"}


def _label(events):
    out, prev_btn = "", False
    for d, b in events:
        piece = (_ARROWS[d] if d else "") + (("+" if d else "") + "+".join(BUTTON_NAMES[x] for x in sorted(b)) if b else "")
        if out and (prev_btn or d is None):
            out += ","
        out += piece
        prev_btn = bool(b)
    return out


def parse(text):
    """채팅 → (표시용 이름, 실행 함수, 후딜레이 초). 명령이 아니면 None.
    '>' : 여러 명령을 차례로 실행 (예: 6n23rp>2lk)  /  '@N' : 마지막 입력을 N프레임 유지 (예: 3lk@5)"""
    raw = (text or "").strip().lower().replace(" ", "")
    if not raw or len(raw) > MAX_CHAT_LENGTH:
        return None

    # '>' 연결: 각 부분을 따로 해석해 차례로 실행. 하나라도 명령이 아니면 전체 무시.
    #   사이에는 모든 키를 뗀 상태로 SEQUENCE_GAP_FRAMES 만큼 쉼 (후딜 대기 없이 이어서 → 콤보 가능)
    if ">" in raw:
        parts = raw.split(">")
        if len(parts) > MAX_STEPS or any(not p for p in parts):
            return None
        commands = [parse(p) for p in parts]
        if any(c is None for c in commands):
            return None

        def sequence():
            for index, (_, action, _) in enumerate(commands):
                if index:
                    _wait_until(time.perf_counter() + SEQUENCE_GAP_FRAMES / FPS)
                action()

        return " > ".join(c[0] for c in commands), sequence, commands[-1][2]

    if len(raw) > MAX_COMMAND_LENGTH:
        return None

    # '@N': 마지막 방향/버튼을 N프레임 동안 유지한 뒤 해제
    hold_frames = None
    if "@" in raw:
        match = re.fullmatch(r"([^@]+)@([0-9]{1,3})", raw)
        if not match:
            return None
        raw, frame_text = match.groups()
        hold_frames = int(frame_text)
        if not 1 <= hold_frames <= MAX_HOLD_FRAMES:
            return None

    special = _special(raw)
    if special is not None:
        # 특수 명령은 자체 타이밍을 쓰므로 '@' 지정은 받지 않음
        return None if hold_frames is not None else special
    return _parse_basic(raw, hold_frames)


def _special(raw):
    """특수 명령(잡기·대시·기본 스텝·초풍·웨이브 등)이면 결과, 아니면 None."""
    if raw == "잡기":
        return "잡기", lambda: perform(_events([], ["1", "3"])), recovery("잡기", kind="잡기")
    if raw in ("대시", "ㄷㅅ"):
        return "대시", lambda: perform(_events(["6", "n", "6"])), recovery("대시", kind="대시")
    if raw in ("백대시", "백대쉬", "ㅂㄷㅅ"):
        return "백대시", lambda: perform(_events(["4", "n", "4"])), recovery("백대시", kind="백대시")
    if raw in ("연속백대시", "연속백대쉬", "ㅇㅅㅂㄷㅅ"):
        # 사람 입력 기록 그대로: 4(4F) n(6F) + [4(9F) 1(4F) 4(2F) n(5F)] x BACKDASH_COUNT
        name = f"연속백대시x{BACKDASH_COUNT}"
        steps = backdash_steps(BACKDASH_COUNT)
        return name, lambda: press_frames(steps), recovery(name, kind="이동")

    # 기본 스텝: 횡이동 / 횡걷기 / 앞·뒷걷기 / 앉아대시
    if raw in SIDESTEP_UP:
        return "횡이동↑", lambda: sidestep("8"), recovery("횡이동↑", kind="스텝")
    if raw in SIDESTEP_DOWN:
        return "횡이동↓", lambda: sidestep("2"), recovery("횡이동↓", kind="스텝")
    if raw in SIDEWALK_UP:
        return "횡걷기↑", lambda: sidestep("8", SIDEWALK_HOLD), recovery("횡걷기↑", kind="이동")
    if raw in SIDEWALK_DOWN:
        return "횡걷기↓", lambda: sidestep("2", SIDEWALK_HOLD), recovery("횡걷기↓", kind="이동")
    if raw in CCW_STEP:
        return "반시계횡신", lambda: sidestep(_rot_dir(False)), recovery("반시계횡신", kind="스텝")
    if raw in CCW_WALK:
        return "반시계횡보", lambda: sidestep(_rot_dir(False), SIDEWALK_HOLD), recovery("반시계횡보", kind="이동")
    if raw in CW_STEP:
        return "시계횡신", lambda: sidestep(_rot_dir(True)), recovery("시계횡신", kind="스텝")
    if raw in CW_WALK:
        return "시계횡보", lambda: sidestep(_rot_dir(True), SIDEWALK_HOLD), recovery("시계횡보", kind="이동")
    if raw in WALK_FWD:
        return "앞걷기", lambda: hold_dir("6", WALK_HOLD), recovery("앞걷기", kind="이동")
    if raw in WALK_BACK:
        return "뒷걷기", lambda: hold_dir("4", WALK_HOLD), recovery("뒷걷기", kind="이동")
    if raw in CROUCH_DASH:
        return "앉아대시", lambda: perform(_events(["6", "n", "3"])), recovery("앉아대시", kind="대시")
    if raw in WAKE_UP:
        # 다운 상태에서 8 = 일어나기. 짧게 눌러 서 있을 때 점프가 나가지 않게 함
        return "기상", lambda: press_steps([("8", TAP)]), recovery("기상", kind="기상")
    if raw in EWGF:
        ev = _events(["6", "n", "2", "3"], ["2"])  # 6n23rp
        return "초풍", lambda: perform(ev), recovery("초풍", ev)
    if raw in WAVE:
        name = f"웨이브x{WAVE_COUNT}"
        steps = wave_steps(WAVE_COUNT)
        return name, lambda: press_frames(steps), recovery(name, kind="대시")
    return None


def _parse_basic(raw, hold_frames=None):
    """일반 명령(숫자패드·한글 방향·버튼). hold_frames = '@N' 유지 프레임."""
    # 숫자는 항상 숫자패드 방향. 공격은 lp / rp / lk / rk / ap(lp+rp) / ak(lk+rk) 로만 입력 (ADR-0017)

    # ② 토큰 분해: 한글 방향 / 영문 버튼 / 숫자 / n / +
    tokens, i = [], 0
    while i < len(raw):
        for w in _DIR_WORDS_SORTED:
            if raw.startswith(w, i):
                tokens.append(("dir", DIR_WORDS[w]))
                i += len(w)
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
            kind = "dir"  # 숫자는 항상 방향 (6+lp 는 되지만 6+1 은 명령이 아님)
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

    label = _label(events) + (f"@{hold_frames}" if hold_frames is not None else "")
    return label, lambda: perform(events, hold_frames=hold_frames), recovery(label, events)


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
    cmd, action, delay = parsed

    profile = getattr(message, "profile", None)
    user = getattr(profile, "nickname", None) or "익명"
    now = time.monotonic()
    if now - last_used.get(user, 0) < USER_COOLDOWN:
        return
    last_used[user] = now

    if MODE == "민주주의":
        votes[cmd] += 1
        vote_actions[cmd] = (action, delay)
        emit({"type": "votes", "top": votes.most_common(3), "total": sum(votes.values())})
    elif queue.qsize() < MAX_QUEUE:
        await queue.put((user, cmd, action, delay))


async def chaos_worker():
    while True:
        user, cmd, action, delay = await queue.get()
        print(f"🎮 {user}: {cmd}" + (f"  (후딜 {delay:.2f}초)" if delay else ""))
        emit({"type": "cmd", "user": user, "cmd": cmd, "recovery": round(delay, 2)})
        await asyncio.to_thread(action)
        # 기술 후딜레이가 끝날 때까지 다음 명령을 넣지 않음 (최소 COMMAND_GAP)
        await asyncio.sleep(max(COMMAND_GAP, delay))


async def democracy_worker():
    while True:
        emit({"type": "round", "seconds": VOTE_SECONDS})
        await asyncio.sleep(VOTE_SECONDS)
        if not votes:
            continue
        cmd, count = votes.most_common(1)[0]
        total = sum(votes.values())
        action, delay = vote_actions[cmd]
        votes.clear()
        vote_actions.clear()
        print(f"🗳️ 결과: '{cmd}' ({count}/{total}표) 실행!")
        emit({"type": "result", "cmd": cmd, "count": count, "total": total})
        await asyncio.to_thread(action)
        await asyncio.sleep(delay)  # 후딜 동안 다음 라운드 결과가 겹치지 않게 함


async def main():
    await start_overlay()
    code, state = await asyncio.to_thread(get_authorization_code)
    user_client = await client.generate_user_client(code, state)
    asyncio.create_task(democracy_worker() if MODE == "민주주의" else chaos_worker())
    print(f"✅ 채팅 연결 중... 모드: {MODE}  |  철권 창을 클릭해 활성화해 두세요. (종료: Ctrl+C)")
    await user_client.connect(UserPermission(chat=True))


if __name__ == "__main__":
    print("치지직(공식 API) 채팅 → 철권 조종 봇 시작")
    print(f"캐릭터 위치: {MY_SIDE} (채팅 '사이드변경'으로 전환)  |  예) lp, ap, lklprp, 앞대각rk, 3rk, 6n23rp, 236lp, 연속백대시, 횡위, 횡아래, 횡걷기, 기상, 초풍, 웨이브, 3lk@5, 6n23rp>2lk")
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
