"""
"대형주/우량주" 유니버스(코스피 시가총액 상위 N개)를 산정하는 모듈.

애초 계획은 무료 라이브러리 pykrx로 KRX 사이트에서 직접 긁어오는 것이었는데,
실제로 붙여보니 KRX가 정책을 바꿔서 데이터포털 로그인 없이는 다 막혀있었다
(별도 KRX_ID/KRX_PW 계정이 필요해짐). 그래서 대신 **한국투자증권이 API 키 없이
공개로 제공하는 코스피 종목마스터 파일**(kospi_code.mst)을 쓴다 — 이미 쓰는
KIS 계정 하나로 통일되고, 로그인 절차도 따로 필요 없다.

이 파일은 KIS가 자사 Open API 예제(kis_kospi_code_mst.py)에서 공식적으로
안내하는 고정폭 텍스트 포맷이고, 종목별 시가총액·우선주 여부·관리종목 여부 등을
담고 있다. 하루 12시간 캐시해서, 추천/급락 두 기능이 그날의 유니버스를 공유한다.
"""

from __future__ import annotations

import urllib.request
import zipfile
from dataclasses import dataclass
from io import BytesIO

import pandas as pd
import streamlit as st

from app import config

MASTER_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"

# kospi_code.mst 뒤쪽 228자 고정폭 파트의 필드 스펙 (KIS 공식 예제 기준, 순서 고정)
_PART2_FIELD_WIDTHS = [
    2, 1, 4, 4, 4, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 9, 5, 5, 1, 1, 1, 2, 1, 1,
    1, 2, 2, 2, 3, 1, 3, 12, 12, 8,
    15, 21, 2, 7, 1, 1, 1, 1, 1, 9,
    9, 9, 5, 9, 8, 9, 3, 1, 1, 1,
]
_PART2_COLUMNS = [
    "그룹코드", "시가총액규모", "지수업종대분류", "지수업종중분류", "지수업종소분류",
    "제조업", "저유동성", "지배구조지수종목", "KOSPI200섹터업종", "KOSPI100",
    "KOSPI50", "KRX", "ETP", "ELW발행", "KRX100",
    "KRX자동차", "KRX반도체", "KRX바이오", "KRX은행", "SPAC",
    "KRX에너지화학", "KRX철강", "단기과열", "KRX미디어통신", "KRX건설",
    "Non1", "KRX증권", "KRX선박", "KRX섹터_보험", "KRX섹터_운송",
    "SRI", "기준가", "매매수량단위", "시간외수량단위", "거래정지",
    "정리매매", "관리종목", "시장경고", "경고예고", "불성실공시",
    "우회상장", "락구분", "액면변경", "증자구분", "증거금비율",
    "신용가능", "신용기간", "전일거래량", "액면가", "상장일자",
    "상장주수", "자본금", "결산월", "공모가", "우선주",
    "공매도과열", "이상급등", "KRX300", "KOSPI", "매출액",
    "영업이익", "경상이익", "당기순이익", "ROE", "기준년월",
    "시가총액", "그룹사코드", "회사신용한도초과", "담보대출가능", "대주가능",
]
assert len(_PART2_FIELD_WIDTHS) == len(_PART2_COLUMNS)


@dataclass
class UniverseStock:
    ticker: str
    name: str
    market_cap_rank: int


class UniverseError(RuntimeError):
    pass


def _download_master_bytes() -> bytes:
    # KIS 공식 예제는 ssl._create_unverified_context()로 인증서 검증을 꺼버리는데
    # (중간자 공격에 취약해짐), 직접 테스트해보니 정상 검증으로도 문제없이 받아져서
    # 검증을 켜둔 채로 쓴다.
    req = urllib.request.Request(MASTER_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            zip_bytes = resp.read()
    except Exception as e:  # noqa: BLE001
        raise UniverseError(f"종목마스터 파일 다운로드 실패: {e}") from e
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        inner_name = zf.namelist()[0]
        return zf.read(inner_name)


def _parse_master(raw_bytes: bytes) -> pd.DataFrame:
    text = raw_bytes.decode("cp949")
    rows1 = []
    rows2 = []
    tail_len = sum(_PART2_FIELD_WIDTHS)  # == 228
    for line in text.splitlines():
        if len(line) <= tail_len:
            continue
        head, tail = line[:-tail_len], line[-tail_len:]
        rows1.append(
            {
                "단축코드": head[0:9].strip(),
                "표준코드": head[9:21].strip(),
                "한글명": head[21:].strip(),
            }
        )
        rec = {}
        pos = 0
        for name, width in zip(_PART2_COLUMNS, _PART2_FIELD_WIDTHS):
            rec[name] = tail[pos : pos + width].strip()
            pos += width
        rows2.append(rec)
    return pd.concat([pd.DataFrame(rows1), pd.DataFrame(rows2)], axis=1)


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _get_master_df() -> pd.DataFrame:
    """종목마스터 전체(약 2,580개 코스피 종목)를 파싱해서 캐시. 하루 12시간 캐시라
    유니버스 산정과 종목명 조회가 같은 다운로드 결과를 공유한다."""
    df = _parse_master(_download_master_bytes())
    df["시가총액_num"] = pd.to_numeric(df["시가총액"], errors="coerce")
    return df


def get_top_market_cap_universe(top_n: int = config.TOP_N) -> list[UniverseStock]:
    """코스피 시가총액 상위 top_n 종목을 순위와 함께 반환.

    우선주/SPAC/관리종목/거래정지/정리매매 종목은 "우량주 매매"에 부적합해서 제외한다.
    """
    df = _get_master_df()
    df = df.dropna(subset=["시가총액_num"])
    df = df[
        (df["우선주"] == "0")
        & (df["SPAC"] != "Y")
        & (df["관리종목"] != "Y")
        & (df["거래정지"] != "Y")
        & (df["정리매매"] != "Y")
    ]
    df = df.sort_values("시가총액_num", ascending=False).head(top_n)

    return [
        UniverseStock(ticker=row["단축코드"], name=row["한글명"], market_cap_rank=rank)
        for rank, (_, row) in enumerate(df.iterrows(), start=1)
    ]


def get_stock_name(ticker: str) -> str | None:
    """종목코드로 한글 종목명을 찾는다. KIS 현재가 조회 API는 종목명을 안 주기 때문에
    (업종명만 줌), "직접 종목 추가" 같은 유니버스 밖 종목의 이름을 채울 때 쓴다."""
    df = _get_master_df()
    match = df[df["단축코드"] == ticker]
    if match.empty:
        return None
    return str(match.iloc[0]["한글명"])
