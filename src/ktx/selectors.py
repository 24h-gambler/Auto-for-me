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
PROCEED_BTN = "input[name='proceed'], a#btn_proceed"
PAY_BTN = "a#btn_pay, input[name='pay']"

# 캡차 (있을 경우)
CAPTCHA_IMG = "img[src*='captcha']"
CAPTCHA_INPUT = "input[name='captcha']"
