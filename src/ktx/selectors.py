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

# 결과 테이블
RESULT_ROWS = "table#tbl_search tbody tr"
ROW_TRAIN_NO = "td:nth-child(2)"
ROW_DEPT_TIME = "td:nth-child(3)"
ROW_ARRV_TIME = "td:nth-child(4)"
ROW_FIRST_CLASS_BTN = "td:nth-child(5) a"   # 특실 예매
ROW_STD_CLASS_BTN = "td:nth-child(6) a"     # 일반실 예매
ROW_SOLD_OUT_TXT = "td:nth-child(6)"         # '매진' 텍스트가 들어가는 셀
SOLD_OUT_KEYWORDS = ("매진", "예약대기", "Sold")

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
# 결제 페이지에 도달했음을 확인할 수 있는 표식 (제목 / 결제수단 영역 / 신용카드 등).
PAYMENT_PAGE_MARKER = (
    "text=결제수단, text=신용카드, text=간편결제, "
    ".pay_method, #payMethodArea, [class*='payment']"
)

# 좌석 배치도 (좌석 picker)
# 코레일은 보통 row*column grid: 1~20행 × A,B,(통로),C,D 열 형태.
# 사용 가능한 좌석은 button/area 로 표현되며, data-seat-no 또는 alt/title 에 좌석명 포함.
SEAT_BUTTONS = "a[data-seat-no], area[data-seat-no], button[data-seat-no], a.seat, button.seat"
SEAT_AVAILABLE_ATTR = "data-status"   # 'available' | 'taken'
SEAT_NAME_ATTR = "data-seat-no"       # 예: "3A", "12D"
SEAT_CONFIRM_BTN = "input[name='seatConfirm'], a.btn_seat_confirm"

# 임시 확보 후 보이는 페이지 (결제 대기, 10분 카운트다운)
HOLD_TIMER_TXT = ".hold_timer, #payment_timer, .timer"

# 캡차 (있을 경우)
CAPTCHA_IMG = "img[src*='captcha']"
CAPTCHA_INPUT = "input[name='captcha']"
