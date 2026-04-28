"""
Korail (www.letskorail.com) DOM selectors.

⚠️ 코레일은 사이트 리뉴얼 시 셀렉터가 자주 바뀝니다.
   사이트 변경 발견 시 이 파일만 업데이트하면 됩니다.
   (전체 부킹 흐름은 booker.py 가 담당)
"""

# 로그인
LOGIN_URL = "https://www.letskorail.com/korail/com/login.do"
ID_INPUT = "input#txtMember"
PW_INPUT = "input#txtPwd"
LOGIN_BTN = "input.loginBtn, button#loginDisplay1"

# 메인 / 예매 폼
HOME_URL = "https://www.letskorail.com/"
SEARCH_URL = "https://www.letskorail.com/ebizprd/main.do"
DEPT_INPUT = "input[name='txtGoStart']"
ARRV_INPUT = "input[name='txtGoEnd']"
DATE_INPUT = "input[name='txtGoDate']"
TIME_SELECT = "select[name='txtGoTime']"
SEARCH_BTN = "a.btn_search, input[name='search']"

# 결과 테이블 — 코레일은 id 가 변경된 적 있어 후보 둘 다 매칭.
RESULT_ROWS = (
    "table#tbl_search tbody tr, "
    "table.tbl_l tbody tr, "
    "tbody#tbody tr, "
    "table[summary*='조회'] tbody tr"
)
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
