"""
Korail (www.letskorail.com) DOM selectors.

⚠️ 코레일은 사이트 리뉴얼 시 셀렉터가 자주 바뀝니다.
   사이트 변경 발견 시 이 파일만 업데이트하면 됩니다.
   (전체 부킹 흐름은 booker.py 가 담당)
"""

# ── URL (2026 개편 신 사이트) ───────────────────────────────────────
HOME_URL = "https://www.korail.com/"
LOGIN_URL = "https://www.korail.com/login"          # ※ 실제 경로 미확인 — 본인 검증 필요
TICKET_MAIN_URL = "https://www.korail.com/ticket/main"
SEARCH_URL = "https://www.korail.com/ticket/search/general"

# ── 로그인 입력 (※ 실제 selector 미확인 — 새 사이트 HTML 받은 뒤 갱신) ──
ID_INPUT = "input[name='memberId'], input#memberId, input[type='text']"
PW_INPUT = "input[name='password'], input#password, input[type='password']"
LOGIN_BTN = "button[type='submit'], a.btn_login, button:has-text('로그인')"

# ── 검색 폼 — 새 사이트는 팝업 기반 ─────────────────────────────────
# 출발/도착역 트리거 (동일 class, 안의 <span class="blind"> 텍스트로 구분):
#   <a class="btn_pop btn_end btn_pop-open"><span class="blind">출발역 선택</span></a>
DEPT_OPEN_BTN = "a.btn_pop-open:has(span.blind:has-text('출발역'))"
ARRV_OPEN_BTN = "a.btn_pop-open:has(span.blind:has-text('도착역'))"

# 역 선택 팝업 — 정확한 HTML 미확인. 보통 검색 input + 역 목록 + 확인 버튼.
# 흔한 패턴들에 대응하도록 후보 넓게.
STATION_SEARCH_INPUT = (
    "input[type='search'], input[type='text'][placeholder*='역'], "
    "input[placeholder*='검색'], .layer_pop input[type='text']"
)
STATION_LIST_ITEM_TPL = (
    "li:has-text('{name}'), button:has-text('{name}'), a:has-text('{name}'), "
    ".layer_pop *:has-text('{name}'):not(:has(*))"
)
STATION_CONFIRM_BTN = (
    "button:has-text('확인'), a:has-text('확인'), "
    "button:has-text('선택'), a:has-text('선택'), "
    ".layer_pop .btn_ok, .layer_pop .btn_confirm"
)

# 출발일 트리거: <a href="#none" class="btn_pop btn_d-day" title="출발일"></a>
DATE_OPEN_BTN = "a.btn_pop.btn_d-day, a.btn_pop[title='출발일']"

# 캘린더 — <p class="date">2026. 04.</p> 포맷, td.disabled 는 클릭 불가.
DATE_PICKER = ".datepicker"
DATE_PICKER_MONTH_TXT = ".datepicker p.date"
DATE_PICKER_PREV = (
    ".datepicker .btn_prev, .datepicker .prev, "
    ".datepicker button[class*='prev'], .datepicker a[class*='prev']"
)
DATE_PICKER_NEXT = (
    ".datepicker .btn_next, .datepicker .next, "
    ".datepicker button[class*='next'], .datepicker a[class*='next']"
)
# 일자 셀 (활성): td:not(.disabled) > a > span.day. day 텍스트로 클릭.
DATE_DAY_TPL = ".datepicker td:not(.disabled) a:has(span.day:text-is('{day}'))"

# 시간 picker (Slick 캐러셀):
#  <div class="timeSelect">
#    <li><span class="disabled">00시</span></li>     ← 비활성
#    <li class="current"><a>21시</a></li>            ← 선택됨
#    <li><a>22시</a></li>                            ← 선택 가능
TIME_PICKER = ".timeSelect"
TIME_PICKER_PREV = ".timeSelect .slick-prev"
TIME_PICKER_NEXT = ".timeSelect .slick-next"
TIME_HOUR_TPL = ".timeSelect li a:text-is('{hour}시')"

# 열차 조회 버튼: <button type="button" class="btn_lookup">열차 조회</button>
SEARCH_BTN = "button.btn_lookup, button:has-text('열차 조회'), button:has-text('조회')"

# ── 옛 site 호환을 위한 alias — booker 의 기존 코드가 참조 중. ──────
DEPT_INPUT = DEPT_OPEN_BTN
ARRV_INPUT = ARRV_OPEN_BTN
DATE_INPUT = DATE_OPEN_BTN
TIME_SELECT = TIME_PICKER

# ── 결과 페이지 (새 사이트) ─────────────────────────────────────────
# https://www.korail.com/ticket/search/list
RESULT_PAGE_URL_HINT = "/ticket/search/list"
# 결과 행 — 새 사이트는 table 외에 li/div 기반일 수도 있어 후보 넓게.
RESULT_ROWS = (
    "table tbody tr, "
    "ul.train_list > li, ul.list > li, "
    "div.train_item, div[class*='train_row'], "
    "table#tbl_search tbody tr, table.tbl_l tbody tr"
)
# 가격이 있는 셀 (예전 호환 alias).
ROW_PRICE_CELL = "td:has-text('원'):not(:text-is('매진'))"
ROW_SOLD_OUT_CELL = "td:text-is('매진'), td:text-is('-')"

# ── 새 사이트 결과 페이지 셀 패턴 ──────────────────────────────────
# 일반실/특실 좌석 예약 링크 — 가격(.txt_gr 또는 .txt_price) 표시:
#   <a><p class="txt_ch">일반실</p><p class="txt_price">15% 할인</p>
#      <p class="txt_gr">20,100원</p></a>
SEAT_AVAIL_LINK = "a:has(p.txt_gr), a:has(p.txt_price)"

# 입석+좌석 링크:
#   <a><div class="tck_etc_use">입석 + 좌석</div></a>
STANDING_AVAIL_LINK = "a:has(.tck_etc_use)"

# 셀 클릭 후 나타나는 '예매' 확정 버튼:
#   <button class="btn_bn-blue02 reservbtn">예매</button>
RESERVE_BTN = (
    "button.reservbtn, "
    "button.btn_bn-blue02:has-text('예매'), "
    "button:has-text('예매')"
)

# '더보기' (다음 시간대):
#   <a class="page_group"><span>더보기</span></a>
LOAD_MORE_BTN = "a.page_group, a:has(span:text-is('더보기'))"
ROW_TRAIN_NO = "td:nth-child(2)"
ROW_DEPT_TIME = "td:nth-child(3)"
ROW_ARRV_TIME = "td:nth-child(4)"
ROW_FIRST_CLASS_BTN = "td:nth-child(5) a"   # 특실 예매/예약하기
ROW_STD_CLASS_BTN = "td:nth-child(6) a"     # 일반실 예매/예약하기
ROW_SOLD_OUT_TXT = "td:nth-child(6)"         # '매진' 텍스트가 들어가는 셀
SOLD_OUT_KEYWORDS = ("매진", "예약대기", "Sold", "좌석없음")
# 예약 가능 표시 — 이 키워드가 셀에 있으면 매진이 아닌 것으로 본다.
AVAIL_KEYWORDS = ("예매", "예약", "예약하기", "Book")

# 좌석 선택 / 결제
# 좌석 선택 후 → 승객정보/예약확인 → 결제 페이지로 진행하는 "다음/예매하기" 류 버튼들.
# 코레일은 단계별로 다른 wording 을 쓰므로 후보를 넓게 둡니다.
PROCEED_BTN = (
    "input[name='proceed'], a#btn_proceed, "
    "a:has-text('다음'), button:has-text('다음'), "
    "a:has-text('예매하기'), button:has-text('예매하기'), "
    "a:has-text('예약하기'), button:has-text('예약하기'), "
    "a:has-text('진행'), button:has-text('진행'), "
    "input[type='submit'][value*='다음'], input[type='submit'][value*='예매']"
)
# 실제 결제 페이지로 진입하는 "결제하기" 버튼.
PAY_BTN = (
    "a#btn_pay, input[name='pay'], "
    "a:has-text('결제하기'), button:has-text('결제하기'), "
    "a:has-text('결제'), button:has-text('결제'), "
    "input[type='submit'][value*='결제']"
)
# 결제 페이지에 도달했음을 확인할 수 있는 표식 (CSS 셀렉터만).
# 한국어 텍스트 매칭은 booker.py 의 _payment_visible() 에서 별도 처리.
PAYMENT_PAGE_MARKER = (
    ".pay_method, #payMethodArea, [class*='payment'], [id*='payment'], "
    ".paymentArea, .pay_area, #pay_area, #payInfo"
)
# 페이지 안에 이 한국어 단어들이 보이면 결제 페이지로 간주.
PAYMENT_PAGE_TEXTS = ("결제수단", "결제정보", "신용카드", "간편결제", "카드결제")

# 좌석 배치도 (좌석 picker)
# 코레일은 보통 row*column grid: 1~20행 × A,B,(통로),C,D 열 형태.
# 사용 가능한 좌석은 button/area 로 표현되며, data-seat-no 또는 alt/title 에 좌석명 포함.
SEAT_BUTTONS = (
    "a[data-seat-no], area[data-seat-no], button[data-seat-no], "
    "a.seat, button.seat, "
    "img[alt*='좌석'][alt*='가능']"
)
SEAT_AVAILABLE_ATTR = "data-status"   # 'available' | 'taken'
SEAT_NAME_ATTR = "data-seat-no"       # 예: "3A", "12D"
SEAT_CONFIRM_BTN = (
    "input[name='seatConfirm'], a.btn_seat_confirm, "
    "a:has-text('좌석선택완료'), button:has-text('좌석선택완료')"
)
# 자동배정 — 좌석맵 진입 없이 바로 좌석 자동 할당으로 결제까지 진행.
# 좌석 picker 가 동작 안 할 때의 fallback 이자, 사실상 가장 빠르고 안정적인 경로.
AUTO_SEAT_BTN = (
    "a:has-text('자동배정'), button:has-text('자동배정'), "
    "a:has-text('자동 배정'), button:has-text('자동 배정'), "
    "input[type='button'][value*='자동배정'], input[type='submit'][value*='자동배정']"
)

# 임시 확보 후 보이는 페이지 (결제 대기, 10분 카운트다운)
HOLD_TIMER_TXT = ".hold_timer, #payment_timer, .timer"

# 캡차 (있을 경우)
CAPTCHA_IMG = "img[src*='captcha']"
CAPTCHA_INPUT = "input[name='captcha']"
