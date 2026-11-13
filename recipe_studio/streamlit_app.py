"""오늘의 식탁 · COOKRCP01 레시피 탐색기.

이 폴더에서 실행: python -m streamlit run streamlit_app.py
"""

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4

import altair as alt
import pandas as pd
import streamlit as st

DATA_PATH = Path(__file__).with_name("COOKRCP01.csv")
LIBRARY_PATH = Path(__file__).with_name("recipe_library.sqlite3")
NUTRIENTS = {
    "INFO_ENG": "열량 (kcal)",
    "INFO_NA": "나트륨 (mg)",
    "INFO_PRO": "단백질 (g)",
    "INFO_CAR": "탄수화물 (g)",
    "INFO_FAT": "지방 (g)",
}

st.set_page_config(page_title="오늘의 식탁", page_icon=":material/skillet:", layout="wide")


@contextmanager
def open_library():
    """내 레시피와 즐겨찾기를 앱 폴더에 저장합니다."""
    connection = sqlite3.connect(LIBRARY_PATH)
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS recipes (id TEXT PRIMARY KEY, content TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS favorites (id TEXT PRIMARY KEY)")
            yield connection
    finally:
        connection.close()


def favorite_button(recipe_id: str, favorites: set[str]) -> None:
    saved = recipe_id in favorites
    label = "즐겨찾기 해제" if saved else "즐겨찾기에 추가"
    if st.button(label, icon=":material/star:" if saved else ":material/star_border:"):
        with open_library() as db:
            if saved:
                db.execute("DELETE FROM favorites WHERE id = ?", (recipe_id,))
            else:
                db.execute("INSERT OR IGNORE INTO favorites VALUES (?)", (recipe_id,))
        st.rerun()


def save_personal_recipe(payload):
    """직접 입력과 기존 요리 앱의 JSON 양식을 같은 형태로 저장합니다."""
    if not isinstance(payload, dict):
        raise ValueError("레시피 한 개를 JSON 객체로 입력해 주세요.")
    name, ingredients, steps = (payload.get(key) for key in ("name", "ingredients", "steps"))
    if isinstance(ingredients, dict) and ingredients and all(
        key.strip() and type(amount) is int and amount > 0 for key, amount in ingredients.items()
    ):
        ingredients = [f"{key} {amount}팩" for key, amount in ingredients.items()]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("요리 이름은 1~80자로 입력하세요.")
    for lines in (ingredients, steps):
        if not isinstance(lines, list) or not 1 <= len(lines) <= 100 or any(
            not isinstance(line, str) or not 1 <= len(line.strip()) <= 1000 for line in lines
        ):
            raise ValueError("재료와 조리 순서는 각각 1~100줄로 입력하세요. 한 줄은 최대 1,000자입니다.")
    recipe = {"name": name.strip(), "ingredients": [line.strip() for line in ingredients],
              "steps": [line.strip() for line in steps]}
    recipe_id = f"mine-{uuid4().hex}"
    with open_library() as db:
        db.execute("INSERT INTO recipes VALUES (?, ?)", (recipe_id, json.dumps(recipe, ensure_ascii=False)))
    st.session_state["recipe_notice"] = f"‘{recipe['name']}’ 레시피를 저장했습니다."
    st.rerun()


def show_personal_recipe(recipe):
    st.header(recipe["name"])
    st.subheader("재료")
    for line in recipe["ingredients"]:
        st.text(line)
    st.subheader("조리 순서")
    for number, step in enumerate(recipe["steps"], 1):
        st.text(f"{number}. {step}")
    st.download_button(
        "레시피 JSON 다운로드", json.dumps(recipe, ensure_ascii=False, indent=2),
        "my_recipe.json", "application/json", on_click="ignore",
    )


@st.cache_data
def load_recipes(path: Path, modified_ns: int) -> pd.DataFrame:
    """원본을 읽고 검색용 텍스트와 영양 수치를 준비합니다."""
    # 위젯 조작으로 스크립트가 다시 실행되어도 파일이 그대로면 캐시를 사용합니다.
    data = pd.read_csv(path, encoding="utf-8-sig", dtype={"RCP_SEQ": "string"})
    data = data.dropna(subset=["RCP_SEQ", "RCP_NM"]).drop_duplicates("RCP_SEQ")
    for column in NUTRIENTS:
        # 빈 값과 변환할 수 없는 값은 결측으로 두어 통계에 0으로 합산되지 않게 합니다.
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["검색문"] = (
        data[["RCP_NM", "RCP_PARTS_DTLS", "HASH_TAG"]]
        .fillna("")
        .agg(" ".join, axis=1)
        .str.casefold()
    )
    return data.set_index("RCP_SEQ")


def image_url(value: object) -> str | None:
    """원본의 식품안전나라 이미지 주소를 HTTPS로 표시합니다."""
    if not isinstance(value, str):
        return None
    url = urlsplit(value.strip())
    if url.scheme in ("http", "https") and url.netloc in (
        "www.foodsafetykorea.go.kr", "foodsafetykorea.go.kr"
    ):
        return url._replace(scheme="https").geturl()
    return None


def show_recipe(row: pd.Series) -> None:
    """선택한 레시피의 사진, 재료, 영양 수치와 조리 순서를 보여줍니다."""
    st.header(str(row["RCP_NM"]))
    st.caption(f"{row['RCP_PAT2']} · {row['RCP_WAY2']}")
    photo, details = st.columns([1, 2], gap="large")
    with photo:
        url = image_url(row["ATT_FILE_NO_MAIN"])
        if url:
            st.image(url, width="stretch", caption=f"{row['RCP_NM']} 완성 사진")
        else:
            st.info("등록된 대표 사진이 없습니다.")
    with details:
        with st.container(horizontal=True):
            for column in ("INFO_ENG", "INFO_NA", "INFO_PRO"):
                value = row[column]
                st.metric(NUTRIENTS[column], f"{value:,.1f}" if pd.notna(value) else "—", border=True)
        st.caption("영양 수치는 원본 CSV에 기록된 기준입니다.")
        st.subheader("재료")
        # st.text는 재료의 줄바꿈을 유지하고 CSV 문자열을 코드로 해석하지 않습니다.
        ingredients = row["RCP_PARTS_DTLS"]
        if isinstance(ingredients, str) and ingredients.strip():
            st.text(ingredients.strip())
        else:
            st.caption("등록된 재료 정보가 없습니다.")
    st.subheader("조리 순서")
    show_images = st.toggle("단계별 사진 보기", key="show_step_images")
    for index in range(1, 21):
        step = row.get(f"MANUAL{index:02d}")
        if not isinstance(step, str) or not step.strip():
            continue
        with st.container(border=True):
            st.markdown(f"**{index}단계**")
            # 일부 원본 설명은 첫머리에 번호가 있으므로 화면 번호와 겹치지 않게 제거합니다.
            st.write(re.sub(r"^\s*\d+[.)]\s*", "", step.strip(), count=1))
            if show_images:
                url = image_url(row.get(f"MANUAL_IMG{index:02d}"))
                if url:
                    st.image(url, width=320, caption=f"조리 {index}단계")
    tip = row.get("RCP_NA_TIP")
    if isinstance(tip, str) and tip.strip():
        st.info(tip.strip(), title="나트륨을 줄이는 조리 팁")


app_mode = st.sidebar.radio("오늘의 식탁", ["레시피 찾기", "즐겨찾기", "내 레시피", "레시피 추가"])
st.title("오늘의 식탁")
try:
    with open_library() as db:
        favorites = {row[0] for row in db.execute("SELECT id FROM favorites")}
        personal = {key: json.loads(content) for key, content in db.execute(
            "SELECT id, content FROM recipes ORDER BY rowid DESC"
        )}
except sqlite3.Error:
    st.error("앱 폴더에서 레시피 저장소를 열 수 없습니다. 폴더의 쓰기 권한을 확인하세요.")
    st.stop()

if notice := st.session_state.pop("recipe_notice", None):
    st.success(notice)

if app_mode == "레시피 추가":
    st.header("레시피 추가")
    mode = st.radio("추가 방식", ["직접 작성", "JSON 업로드"], horizontal=True)
    try:
        if mode == "직접 작성":
            with st.form("recipe_editor"):
                name = st.text_input("요리 이름", max_chars=80)
                ingredients = st.text_area("재료와 분량", placeholder="두부 150g\n달걀 2개", max_chars=12000)
                steps = st.text_area("조리 순서", placeholder="한 줄에 한 단계씩 입력하세요.", max_chars=20000)
                if st.form_submit_button("내 레시피에 저장", type="primary"):
                    save_personal_recipe({
                        "name": name, "ingredients": [line for line in ingredients.splitlines() if line.strip()],
                        "steps": [line for line in steps.splitlines() if line.strip()],
                    })
        else:
            example = {"name": "두부 달걀볶음", "ingredients": ["두부 150g", "달걀 2개"],
                       "steps": ["두부를 잘라 굽습니다.", "달걀을 넣고 익을 때까지 볶습니다."]}
            st.download_button("JSON 양식 다운로드", json.dumps(example, ensure_ascii=False, indent=2),
                               "recipe_template.json", "application/json", on_click="ignore")
            uploaded = st.file_uploader("레시피 JSON 파일", type=["json"])
            if st.button("업로드한 레시피 저장", disabled=uploaded is None, type="primary"):
                if uploaded.size > 100_000:
                    raise ValueError("100KB 이하의 JSON 파일을 선택하세요.")
                try:
                    payload = json.loads(uploaded.getvalue().decode("utf-8-sig"))
                except (UnicodeError, json.JSONDecodeError) as error:
                    raise ValueError("UTF-8로 저장한 올바른 JSON 파일을 올려 주세요.") from error
                save_personal_recipe(payload)
    except ValueError as error:
        st.warning(str(error))
    except sqlite3.Error:
        st.error("레시피를 저장하지 못했습니다. 앱 폴더의 쓰기 권한을 확인하세요.")
    st.stop()

if app_mode == "내 레시피":
    st.header("내 레시피")
    if personal:
        keyword = st.text_input("내 레시피 검색", placeholder="요리 이름 또는 재료").strip().casefold()
        ids = [key for key, recipe in personal.items()
               if keyword in " ".join([recipe["name"], *recipe["ingredients"]]).casefold()]
        if ids:
            key = st.selectbox("저장한 레시피", ids, format_func=lambda key: personal[key]["name"])
            favorite_button(key, favorites)
            show_personal_recipe(personal[key])
        else:
            st.info("검색 조건에 맞는 내 레시피가 없습니다.")
    else:
        st.info("‘레시피 추가’에서 직접 작성하거나 JSON을 업로드해 보세요.")
    st.stop()

try:
    recipes = load_recipes(DATA_PATH, DATA_PATH.stat().st_mtime_ns)
except (OSError, UnicodeError, KeyError, pd.errors.ParserError, pd.errors.EmptyDataError):
    st.error("앱 파일과 같은 폴더의 COOKRCP01.csv를 읽을 수 없습니다.")
    st.stop()

if app_mode == "즐겨찾기":
    st.header("즐겨찾기")
    titles = {key: recipes.at[key, "RCP_NM"] for key in favorites if key in recipes.index}
    titles.update({key: f"{personal[key]['name']} · 내 레시피" for key in favorites if key in personal})
    if titles:
        key = st.selectbox("즐겨찾기 레시피", sorted(titles, key=titles.get), format_func=titles.__getitem__)
        favorite_button(key, favorites)
        if key in personal:
            show_personal_recipe(personal[key])
        else:
            show_recipe(recipes.loc[key])
    else:
        st.info("레시피 상세에서 별표 버튼을 눌러 저장하세요.")
    st.stop()

st.caption("재료로 메뉴를 찾고, 영양 수치와 조리 순서를 확인하세요.")

# 같은 검색 조건을 적용할 수 있도록 저장한 레시피도 목록에 합칩니다.
# 내 레시피에는 영양 정보가 없으므로 해당 열은 결측값으로 둡니다.
if personal:
    personal_rows = pd.DataFrame.from_dict(
        {
            key: {
                "RCP_NM": recipe["name"],
                "RCP_PAT2": "내 레시피",
                "검색문": " ".join([recipe["name"], *recipe["ingredients"]]).casefold(),
            }
            for key, recipe in personal.items()
        },
        orient="index",
    )
    recipes = pd.concat([recipes, personal_rows]).rename_axis("RCP_SEQ")

with st.sidebar:
    st.header("레시피 필터")
    query = st.text_input("메뉴명 또는 재료", placeholder="예: 두부 달걀")
    excluded = st.text_input("제외할 검색어", placeholder="예: 새우, 우유")
    categories = st.multiselect("메뉴 종류", sorted(recipes["RCP_PAT2"].dropna().unique()))
    methods = st.multiselect("조리 방법", sorted(recipes["RCP_WAY2"].dropna().unique()))
    with st.expander("영양 조건"):
        max_sodium = st.number_input("나트륨 상한 (mg)", min_value=0.0, value=None, step=50.0)
        max_energy = st.number_input("열량 상한 (kcal)", min_value=0.0, value=None, step=25.0)
        min_protein = st.number_input("단백질 하한 (g)", min_value=0.0, value=None, step=5.0)
    st.caption("재료 검색은 원문에 적힌 단어를 기준으로 합니다. 알레르기 성분은 재료와 제품 표기를 확인하세요.")

# 포함 검색어는 모두 있어야 하고, 제외 검색어는 하나라도 있으면 목록에서 뺍니다.
# regex=False로 설정하면 괄호 같은 문자도 입력 그대로 검색합니다.
matches = pd.Series(True, index=recipes.index)
for term in re.split(r"[,\s]+", query.casefold().strip()):
    if term:
        matches &= recipes["검색문"].str.contains(term, regex=False)
for term in re.split(r"[,\s]+", excluded.casefold().strip()):
    if term:
        matches &= ~recipes["검색문"].str.contains(term, regex=False)
if categories:
    matches &= recipes["RCP_PAT2"].isin(categories)
if methods:
    matches &= recipes["RCP_WAY2"].isin(methods)
if max_sodium is not None:
    matches &= recipes["INFO_NA"].le(max_sodium)
if max_energy is not None:
    matches &= recipes["INFO_ENG"].le(max_energy)
if min_protein is not None:
    matches &= recipes["INFO_PRO"].ge(min_protein)

filtered = recipes.loc[matches]
if filtered.empty:
    st.info("조건에 맞는 레시피가 없습니다. 검색어 또는 영양 조건을 조정해 보세요.")
    st.stop()

first, second, third = st.columns(3)
first.metric("검색 결과", f"{len(filtered):,} / {len(recipes):,}", border=True)
for card, field in ((second, "INFO_NA"), (third, "INFO_PRO")):
    median = filtered[field].median()
    card.metric(f"중앙값 · {NUTRIENTS[field]}", f"{median:,.1f}" if pd.notna(median) else "—", border=True)

view = st.segmented_control(
    "보기", ["레시피 찾기", "영양 비교"], default="레시피 찾기", required=True,
)
if view == "레시피 찾기":
    ordering = {
        "이름순": ("RCP_NM", True),
        "나트륨 낮은 순": ("INFO_NA", True),
        "열량 낮은 순": ("INFO_ENG", True),
        "단백질 높은 순": ("INFO_PRO", False),
    }
    sort_col, recipe_col = st.columns([1, 2], vertical_alignment="bottom")
    order = sort_col.selectbox("정렬", list(ordering))
    field, ascending = ordering[order]
    ordered = filtered.sort_values(field, ascending=ascending, kind="stable")
    # 선택값은 레시피 ID로 유지해 이름이 같은 메뉴도 각각 선택할 수 있습니다.
    options = {
        key: f"{row.RCP_NM} · {row.RCP_PAT2} (#{key})"
        for key, row in ordered.iterrows()
    }
    selected = recipe_col.selectbox("레시피", list(options), format_func=options.__getitem__)
    favorite_button(str(selected), favorites)
    if selected in personal:
        show_personal_recipe(personal[selected])
    else:
        show_recipe(ordered.loc[selected])
else:
    st.header("레시피 영양 비교")
    comparable = filtered.dropna(subset=list(NUTRIENTS), how="all")
    options = {
        key: f"{row.RCP_NM} (#{key})"
        for key, row in comparable.sort_values("RCP_NM").iterrows()
    }
    selected = st.multiselect(
        "비교할 메뉴 (최대 4개)", list(options), format_func=options.__getitem__,
        default=list(options)[:min(2, len(options))], max_selections=4,
    )
    if selected:
        comparison = comparable.loc[selected, list(NUTRIENTS)].rename(columns=NUTRIENTS)
        comparison.insert(0, "메뉴", [options[key] for key in selected])
        field = st.selectbox("차트에서 비교할 지표", list(NUTRIENTS.values()))
        # 단위가 다른 지표를 같은 축에 섞지 않고 선택한 한 지표만 막대로 비교합니다.
        chart = alt.Chart(comparison.dropna(subset=[field])).mark_bar(
            color="#256344", cornerRadiusEnd=5,
        ).encode(
            x=alt.X(field=field, type="quantitative"),
            y=alt.Y("메뉴:N", sort="-x"),
            tooltip=[alt.Tooltip("메뉴:N"), alt.Tooltip(field=field, type="quantitative", format=",.1f")],
        )
        st.altair_chart(chart.properties(height=220, title=f"메뉴별 {field} 비교"))
        st.dataframe(comparison, hide_index=True)
        st.caption("원본 CSV 기준 수치입니다. 빈칸은 해당 값이 기록되지 않은 경우입니다.")
    else:
        st.info("비교할 레시피를 선택하세요." if options else "검색 결과에 영양 수치가 있는 레시피가 없습니다.")
    export = filtered[["RCP_NM", "RCP_PAT2", "RCP_WAY2", *NUTRIENTS]].rename(
        columns={"RCP_NM": "메뉴", "RCP_PAT2": "메뉴 종류", "RCP_WAY2": "조리 방법", **NUTRIENTS}
    )
    st.download_button(
        "필터 결과 CSV 다운로드", export.to_csv(index_label="레시피 ID").encode("utf-8-sig"),
        "레시피_검색결과.csv", "text/csv", icon=":material/download:", on_click="ignore",
    )
