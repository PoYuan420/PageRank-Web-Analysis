import streamlit as st
import requests
from bs4 import BeautifulSoup
import networkx as nx
import pandas as pd
import plotly.express as px
from urllib.parse import urljoin, urlparse, unquote
import time
from pyvis.network import Network
import streamlit.components.v1 as components
from opencc import OpenCC
from concurrent.futures import ThreadPoolExecutor
import hashlib
import random
import re

# 初始化繁體中文轉換器
cc = OpenCC('s2twp')  # 簡體中文轉台灣正體


# --- 1. 高效並行爬蟲核心邏輯 (網頁分析) ---
def fetch_single_url(args):
    url, headers, max_links = args
    links = set()
    try:
        response = requests.get(url, headers=headers, timeout=3)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            for a in soup.find_all("a", href=True):
                full_url = urljoin(url, a["href"])
                if urlparse(full_url).scheme in ["http", "https"]:
                    links.add(full_url)
                if len(links) >= max_links:
                    break
    except:
        pass
    return url, links


def crawl_web_parallel(start_url, max_per_layer=20, max_layers=2):
    G = nx.DiGraph()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    current_layer_urls = {start_url}
    visited_urls = set()

    estimated_max = sum([max_per_layer**i for i in range(1, max_layers + 1)])
    if estimated_max > 500:
        st.warning(f"⚠️ 警告：當前設定最大可能爬取 {estimated_max} 個節點，耗時較長，請耐心等候。")

    status_text = st.empty()
    progress_bar = st.progress(0)

    for layer in range(max_layers):
        status_text.write(f"🕸️ 正在分析第 {layer + 1} 層網頁 (當前層節點數: {len(current_layer_urls)})...")

        urls_to_crawl = [u for u in current_layer_urls if u not in visited_urls]
        if not urls_to_crawl:
            break

        tasks = [(url, headers, max_per_layer) for url in urls_to_crawl]
        next_layer_urls = set()

        with ThreadPoolExecutor(max_workers=15) as executor:
            results = executor.map(fetch_single_url, tasks)

            for i, (parent_url, child_links) in enumerate(results):
                visited_urls.add(parent_url)
                for link in child_links:
                    G.add_edge(parent_url, link)
                    next_layer_urls.add(link)
                progress_bar.progress(min((i + 1) / len(tasks), 1.0))

        current_layer_urls = next_layer_urls
        time.sleep(0.1)

    status_text.write("✅ 網路圖譜建構完成！")
    return G


# --- 2. 通用評分條組件 ---
def draw_influence_bar(current_val, df, target_col="權重值"):
    max_val = df[target_col].max()
    score = (current_val / max_val) * 100 if max_val > 0 else 0
    score = min(round(score, 1), 100.0)

    level_map = [
        (20, ("極弱", "#9ca3af")),
        (40, ("弱", "#fbbf24")),
        (60, ("一般", "#60a5fa")),
        (80, ("強", "#8b5cf6")),
        (101, ("極強", "#ef4444"))
    ]

    label, color = "一般", "#60a5fa"
    for limit, info in level_map:
        if score <= limit:
            label, color = info
            break

    bar_html = f"""
    <div style="font-family: sans-serif; margin: 20px 0;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 5px;">
            <span style="font-weight: bold; color: {color};">影響力評級：{label}</span>
            <span style="font-weight: bold;">{score} / 100</span>
        </div>
        <div style="width: 100%; background-color: #e5e7eb; border-radius: 10px; height: 25px; position: relative;">
            <div style="width: {score}%; background-color: {color}; height: 100%; border-radius: 10px;"></div>
        </div>
    </div>
    """
    components.html(bar_html, height=70)


def get_short_label(url):
    if not url.startswith("http"):
        return url
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if path_parts:
        return unquote(cc.convert(path_parts[-1]))
    return unquote(cc.convert(parsed.netloc))


# --- 3. 方向A：真實公開資料抓取層 ---
def fetch_real_ig_public_data(username):
    """
    嘗試抓取 Instagram 帳號公開頁面的 meta 標籤資訊。
    這只能取得「公開、未登入狀態下可見」的基本摘要資料，
    若 IG 端阻擋（常見），則回傳 None，由上層改用模擬估算。
    """
    result = {
        "fetch_success": False,
        "raw_followers": None,
        "raw_following": None,
        "raw_posts": None,
        "bio_length": 0,
        "has_avatar": None,
        "page_title": None,
        "source_note": "",
    }

    # 行動版 User-Agent 池，模擬一般手機瀏覽器（降低被識別為爬蟲的機率）
    mobile_uas = [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    ]

    common_headers = {
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "User-Agent": random.choice(mobile_uas),
    }

    # --- 方法一：IG 網頁前端內部使用的 web_profile_info 公開 API ---
    # 這個端點是 instagram.com 網頁版前端本身用來載入個人頁資料的請求，
    # 帶上 x-ig-app-id（IG 官方網頁固定使用的公開 App ID）成功率較高。
    api_url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    api_headers = {
        **common_headers,
        "x-ig-app-id": "936619743392459",
        "Accept": "*/*",
        "Referer": f"https://www.instagram.com/{username}/",
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.get(api_url, headers=api_headers, timeout=6)

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    user = data.get("data", {}).get("user")
                    if user:
                        result["raw_followers"] = user.get("edge_followed_by", {}).get("count")
                        result["raw_following"] = user.get("edge_follow", {}).get("count")
                        result["raw_posts"] = user.get("edge_owner_to_timeline_media", {}).get("count")
                        bio = user.get("biography", "") or ""
                        result["bio_length"] = len(bio)
                        avatar_url = user.get("profile_pic_url_hd") or user.get("profile_pic_url", "")
                        result["has_avatar"] = bool(avatar_url) and "44884218_345707102882519_2446069589734326272_n" not in avatar_url
                        result["page_title"] = user.get("full_name") or username
                        result["fetch_success"] = True
                        result["source_note"] = "成功透過 Instagram 網頁版內部 API（web_profile_info）取得真實公開數據。"
                        return result
                    else:
                        result["source_note"] = "帳號可能不存在或為私人帳號（API 回應中無使用者資料）。"
                        return result
                except ValueError:
                    pass  # 回傳非 JSON，往下走 fallback

            elif resp.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(1.5 * (attempt + 1) + random.uniform(0.2, 0.8))
                    api_headers["User-Agent"] = random.choice(mobile_uas)
                    continue
                else:
                    result["source_note"] = "IG 因請求過於頻繁回傳 HTTP 429（多次重試仍失敗），可能為雲端共用 IP 已遭限流。"
            else:
                result["source_note"] = f"web_profile_info API 回應 HTTP {resp.status_code}，嘗試改用網頁 meta 標籤備援方案。"
                break

        except Exception as e:
            result["source_note"] = f"連線發生例外狀況：{e}"
            break

    # --- 方法二（備援）：直接抓網頁 og:description ---
    url = f"https://www.instagram.com/{username}/"
    fallback_headers = {
        **common_headers,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp = requests.get(url, headers=fallback_headers, timeout=6)
        if resp.status_code != 200:
            note = f"備援方案亦無法存取頁面（HTTP {resp.status_code}），可能為私人帳號、不存在或遭到限流。"
            result["source_note"] = (result["source_note"] + " " + note) if result["source_note"] else note
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # og:description 範例文字: "1.2M Followers, 350 Following, 120 Posts - See Instagram photos and videos from XXX"
        og_desc_tag = soup.find("meta", property="og:description")
        og_title_tag = soup.find("meta", property="og:title")
        og_image_tag = soup.find("meta", property="og:image")

        if og_title_tag:
            result["page_title"] = og_title_tag.get("content", "")

        if og_image_tag:
            img_url = og_image_tag.get("content", "")
            # 預設大頭貼網址通常含 default 字樣，或回傳的是通用佔位圖
            result["has_avatar"] = bool(img_url) and "44884218_345707102882519_2446069589734326272_n" not in img_url

        if og_desc_tag:
            desc = og_desc_tag.get("content", "")

            def parse_count(text):
                text = text.strip().upper()
                multiplier = 1
                if text.endswith("K"):
                    multiplier = 1_000
                    text = text[:-1]
                elif text.endswith("M"):
                    multiplier = 1_000_000
                    text = text[:-1]
                elif text.endswith("B"):
                    multiplier = 1_000_000_000
                    text = text[:-1]
                try:
                    return int(float(text.replace(",", "")) * multiplier)
                except:
                    return None

            m = re.search(r"([\d.,]+[KMB]?)\s*Followers,\s*([\d.,]+[KMB]?)\s*Following,\s*([\d.,]+[KMB]?)\s*Posts", desc)
            if m:
                result["raw_followers"] = parse_count(m.group(1))
                result["raw_following"] = parse_count(m.group(2))
                result["raw_posts"] = parse_count(m.group(3))
                result["fetch_success"] = True

            # 簡介通常接在 "Posts - " 之後
            bio_part = desc.split(" - ", 1)
            if len(bio_part) > 1:
                result["bio_length"] = len(bio_part[1])

        if not result["fetch_success"]:
            note = "備援方案已連線成功，但 IG 並未在頁面中回傳粉絲數摘要（常見於需登入才能檢視的帳號）。"
            result["source_note"] = (result["source_note"] + " " + note) if result["source_note"] else note
        else:
            result["source_note"] = "web_profile_info API 失敗，改用網頁 meta 標籤備援方案成功解析出基本統計數據。"

    except Exception as e:
        result["source_note"] = f"連線發生例外狀況：{e}"

    return result


# --- Streamlit 主介面配置 ---
st.set_page_config(page_title="多維度圖譜安全與影響力分析儀表板", layout="wide")

tab1, tab2 = st.tabs(["🕸️ 網頁 PageRank 分析", "📸 IG 英雄榜：大數據社交死網測謊儀"])


# --- TAB 1: 網頁影響力分析系統 ---
with tab1:
    st.title("🕸️ 網頁影響力 PageRank 分析系統")

    if "G" not in st.session_state:
        st.session_state.G = None
    if "df" not in st.session_state:
        st.session_state.df = None

    with st.sidebar:
        st.header("⚙️ 分析設定")
        start_url = st.text_input("起始網址", value="https://zh.wikipedia.org")
        max_links = st.slider("每層爬取上限", 5, 100, 25)
        max_layers = st.slider("搜尋層數 (深度)", 1, 3, 2)
        alpha = st.slider("阻尼係數 (Alpha)", 0.0, 1.0, 0.85)
        analyze_btn = st.button("開始執行深度分析")

    if analyze_btn:
        with st.spinner("正在進行高效並行爬取..."):
            G_res = crawl_web_parallel(start_url, max_links, max_layers)
            pagerank_scores = nx.pagerank(G_res, alpha=alpha)

            df_res = pd.DataFrame(
                [
                    {"網址": cc.convert(k), "權重值": v}
                    for k, v in pagerank_scores.items()
                ]
            )
            df_res = df_res.sort_values(by="權重值", ascending=False).reset_index(drop=True)

            st.session_state.G = G_res
            st.session_state.df = df_res

    if st.session_state.df is not None:
        df = st.session_state.df
        G = st.session_state.G

        st.divider()
        st.title("🎯 PageRank 數據深度分析報告")

        st.header("🏆 全域權重特徵")
        st.subheader("📌 全域權重值排名 (Top 10)")
        display_df = df.head(10).copy()
        display_df.insert(0, "網頁名稱", display_df["網址"].apply(get_short_label))
        st.dataframe(display_df[["網頁名稱", "權重值"]], use_container_width=True)

        st.subheader("📊 全域權重分佈佔比")
        pie_df = df.head(15).copy()
        pie_df["網頁名稱"] = pie_df["網址"].apply(get_short_label)
        fig_pie = px.pie(pie_df, values="權重值", names="網頁名稱", hole=0.4)
        st.plotly_chart(fig_pie, use_container_width=True)

        st.divider()
        st.header("🔍 核心節點深入追蹤")
        selected_site = st.selectbox("請選擇欲分析的網頁：", df["網址"].tolist())

        current_weight = df.loc[df["網址"] == selected_site, "權重值"].values[0]
        draw_influence_bar(current_weight, df)

        st.subheader("📍 下游連結權重分佈")
        chart_type = st.radio("選擇統計圖表類型：", ["直方圖", "折線圖", "圓餅圖"], horizontal=True, key="web_chart")

        successors = list(G.successors(selected_site)) if selected_site in G else []
        if successors:
            sub_df = df[df["網址"].isin([cc.convert(s) for s in successors])].copy()
            sub_df["顯示名稱"] = sub_df["網址"].apply(get_short_label)
            sub_df = sub_df.sort_values(by="權重值", ascending=False)

            if chart_type == "直方圖":
                fig_sub = px.bar(sub_df, x="顯示名稱", y="權重值", title="下游網頁權重直方圖", color="權重值", color_continuous_scale="Blues")
            elif chart_type == "折線圖":
                fig_sub = px.line(sub_df, x="顯示名稱", y="權重值", title="下游網頁權重趨勢圖", markers=True)
            else:
                fig_sub = px.pie(sub_df, values="權重值", names="顯示名稱", title="下游網頁權重佔比圓餅圖", hole=0.3)

            st.plotly_chart(fig_sub, use_container_width=True)


# --- TAB 2: IG 帳號健康度與死網拓樸演示系統（真實前端 + 演算法模擬後台） ---
with tab2:
    st.title("📸 Instagram 帳號健康度圖譜分析儀")
    st.markdown("""
    ### 🛡️ 雙層架構：真實公開數據 × PageRank 拓樸模擬
    - **第一層（✅ 真實數據）**：透過合法、免金鑰的方式解析該帳號 Instagram 公開頁面的基本特徵（粉絲規模、頭像狀態、簡介長度等）。
    - **第二層（🧪 演算法模擬）**：以第一層抓到的真實特徵作為「種子」，推導出對應規模的下游粉絲拓樸網絡，用於展示 PageRank 死網識別演算法的運作邏輯。
    """)
    st.caption("⚠️ 本系統下半部分的「下游粉絲拓樸圖」為演算法概念演示，並非真實爬取個別粉絲帳號，僅供教學與技術展示使用。")

    raw_user_input = st.text_input("請輸入要稽核的 Instagram 帳號 ID（不論是否加 @ 均可）：", value="instagram")
    clean_user_id = raw_user_input.strip().replace("@", "").replace(" ", "")

    with st.expander("🔧 進階選項：若自動抓取失敗，可手動輸入真實數據作為備援"):
        st.caption("若上方帳號因 IG 限流（HTTP 429）等原因無法自動取得資料，可在此手動填入你從 App 上看到的真實數值。填入「粉絲數」大於 0 時，系統將優先使用此處的手動數據。")
        manual_followers = st.number_input("粉絲數", min_value=0, value=0, step=1, key="manual_followers")
        manual_following = st.number_input("追蹤中數", min_value=0, value=0, step=1, key="manual_following")
        manual_posts = st.number_input("貼文數", min_value=0, value=0, step=1, key="manual_posts")
        manual_bio_length = st.number_input("簡介字數", min_value=0, value=0, step=1, key="manual_bio_length")
        manual_avatar_choice = st.radio("是否設有自訂頭像？", ["自動判斷", "有頭像", "無頭像"], horizontal=True, key="manual_avatar")

    if st.button("🚀 開始分析", type="primary"):
        if not clean_user_id:
            st.warning("請先輸入有效的 Instagram 帳號。")
        else:
            # --- 第一層：真實公開資料抓取 ---
            with st.spinner("正在連線 Instagram 解析公開頁面資訊..."):
                real_data = fetch_real_ig_public_data(clean_user_id)

            # 若自動抓取失敗，且使用者填入了手動數據（粉絲數>0），則以手動數據覆寫
            used_manual_data = False
            if not real_data["fetch_success"] and manual_followers > 0:
                real_data["raw_followers"] = int(manual_followers)
                real_data["raw_following"] = int(manual_following)
                real_data["raw_posts"] = int(manual_posts)
                real_data["bio_length"] = int(manual_bio_length)
                if manual_avatar_choice == "有頭像":
                    real_data["has_avatar"] = True
                elif manual_avatar_choice == "無頭像":
                    real_data["has_avatar"] = False
                real_data["fetch_success"] = True
                used_manual_data = True

            st.divider()
            st.header("✅ 第一層：真實公開資料")

            if real_data["fetch_success"]:
                if used_manual_data:
                    st.success("資料來源：自動抓取失敗，已採用使用者手動輸入的真實數據。")
                else:
                    st.success(f"資料來源：{real_data['source_note']}")
                rc1, rc2, rc3, rc4 = st.columns(4)
                rc1.metric("真實粉絲數", f"{real_data['raw_followers']:,}")
                rc2.metric("真實追蹤數", f"{real_data['raw_following']:,}")
                rc3.metric("真實貼文數", f"{real_data['raw_posts']:,}")
                rc4.metric("簡介字數", f"{real_data['bio_length']} 字")
                base_followers = real_data["raw_followers"] or 0
                base_following = real_data["raw_following"] or 0
                has_avatar_real = real_data["has_avatar"]
                seed_text = f"{clean_user_id}_{base_followers}"
            else:
                st.warning(f"⚠️ {real_data['source_note']} 你也可以展開上方「進階選項」手動輸入真實粉絲數等資料，系統會優先採用手動數據；若未填寫，後續特徵將改用帳號名稱推算（拓樸圖仍為模擬資料）。")
                base_followers = None
                base_following = None
                has_avatar_real = real_data["has_avatar"]
                seed_text = clean_user_id

            st.divider()
            st.header("🧪 第二層：PageRank 下游拓樸模擬")

            with st.spinner("正在以真實特徵為種子，推導下游粉絲拓樸網絡..."):
                time.sleep(0.8)

                # 用真實資料（若有）或帳號名稱作為穩定隨機種子
                hash_seed = int(hashlib.md5(seed_text.encode("utf-8")).hexdigest(), 16) % (10**8)
                random.seed(hash_seed)

                # 若有真實粉絲數則直接採用，否則才用隨機估算
                if base_followers is None:
                    base_followers = random.randint(1000, 200000)
                if base_following is None:
                    base_following = random.randint(100, 5000)

                ff_ratio = base_following / (base_followers / 100) if base_followers > 0 else 999

                # 用真實 ff_ratio、頭像狀態作為異常判定的引子，而非純隨機
                anomaly_score_seed = 0
                if ff_ratio > 25:
                    anomaly_score_seed += 1
                if has_avatar_real is False:
                    anomaly_score_seed += 1
                if real_data.get("bio_length", 0) == 0:
                    anomaly_score_seed += 1

                is_malicious = anomaly_score_seed >= 2 or (anomaly_score_seed == 0 and hash_seed % 5 == 0)

                if is_malicious:
                    base_er = round(random.uniform(0.15, 0.65), 2)
                    p_private = random.randint(60, 85)
                    p_no_avatar = random.randint(40, 75)
                    p_bot_comment = random.randint(50, 80)
                else:
                    base_er = round(random.uniform(2.1, 5.8), 2)
                    p_private = random.randint(15, 30)
                    p_no_avatar = random.randint(4, 15)
                    p_bot_comment = random.randint(2, 10)

                # --- 反舞弊演算法特徵判定 ---
                risk_score = 0
                risk_details = []

                if ff_ratio > 25:
                    risk_score += 25
                    risk_details.append("❌ **結構拓樸反常**：該帳號的「追蹤中」與粉絲比值嚴重失衡，具備強烈互粉集團特徵。")

                if base_followers > 50000 and base_er < 0.9:
                    risk_score += 30
                    risk_details.append(f"❌ **動態黏著度低落**：相較於其高達 {base_followers:,} 的粉絲規模，模擬互動率僅有 {base_er}%，研判下游存在大量殭屍帳號。")
                elif base_followers <= 50000 and base_er < 1.3:
                    risk_score += 25
                    risk_details.append(f"❌ **動態黏著度低落**：中小型創作者模擬互動率僅有 {base_er}%，未達健康基礎線 1.3%。")

                if p_private > 55:
                    risk_score += 15
                    risk_details.append(f"❌ **高密度隱私屏蔽**：模擬下游抽樣中，高達 {p_private}% 為私密帳號。")
                if p_no_avatar > 25:
                    risk_score += 20
                    risk_details.append(f"❌ **幽靈集群密集**：模擬下游節點有 {p_no_avatar}% 屬於無大頭貼、亂碼 ID 的低階自動化帳號特徵。")
                if p_bot_comment > 35:
                    risk_score += 10
                    risk_details.append(f"❌ **語意罐頭化**：模擬留言區高達 {p_bot_comment}% 充斥無意義極短字眼。")

                if has_avatar_real is False:
                    risk_score += 10
                    risk_details.append("❌ **主帳號未設定頭像**：真實抓取結果顯示該帳號無自訂大頭貼，常見於低活躍度或新建帳號。")

                risk_score = min(risk_score, 100)

                # --- 模擬下游帳號名單生成 ---
                first_names = ['vicky', 'kevin', 'jason', 'crypto', 'travel', 'daily', 'amy', 'sharon', 'alex', 'lucas', 'tom', 'emily', 'yuki', 'hannah', 'jack', 'peter', 'lisa']
                last_words = ['_shop', '99', '_official', 'king', '_life', '1024', '_deal', 'beauty', '888', '_fan', 'studio', '_tech', '01', 'mx']
                random_letters = ['abc', 'zxcv', 'qwerty', 'asd', 'dfgh']

                total_sample_count = 30
                bot_count = int(p_no_avatar / 100 * total_sample_count)
                bot_count = max(1, min(bot_count, 25))
                normal_count = total_sample_count - bot_count

                all_followers_names = []

                for _ in range(bot_count):
                    prefix = random.choice(random_letters)
                    main_n = random.choice(first_names)
                    suffix = random.choice(last_words)
                    num = random.randint(100, 999)
                    b_name = f"{prefix}_{main_n}{num}{suffix}"
                    all_followers_names.append((b_name, "模擬高危特徵"))

                for _ in range(normal_count):
                    main_n = random.choice(first_names)
                    num_or_word = random.choice([str(random.randint(10, 99)), random.choice(last_words)])
                    connector = random.choice(['_', '.', ''])
                    n_name = f"{main_n}{connector}{num_or_word}"
                    all_followers_names.append((n_name, "模擬正常用戶"))

                # --- 圖論關係拓樸建構 ---
                IG_G = nx.DiGraph()
                main_node_display = f"@{clean_user_id}"

                bot_only_list = [item[0] for item in all_followers_names if item[1] == "模擬高危特徵"]

                for f_name, f_type in all_followers_names:
                    IG_G.add_edge(f_name, main_node_display)
                    if f_type == "模擬高危特徵" and len(bot_only_list) > 1 and random.random() > 0.4:
                        target_b = random.choice(bot_only_list)
                        if f_name != target_b:
                            IG_G.add_edge(f_name, target_b)

                ig_pagerank = nx.pagerank(IG_G, alpha=0.85)

                type_map = {main_node_display: "主審查標的（真實帳號）"}
                for name, t_type in all_followers_names:
                    type_map[name] = t_type

                ig_df = pd.DataFrame([
                    {
                        "節點": k,
                        "PageRank 權重值": v,
                        "節點屬性": type_map.get(k, "未知節點")
                    } for k, v in ig_pagerank.items()
                ])
                ig_df = ig_df.sort_values(by="PageRank 權重值", ascending=False).reset_index(drop=True)

            # --- 數據儀表板前端呈現 ---
            st.subheader(f"📊 綜合分析報告：`@{clean_user_id}`")

            m1, m2, m3 = st.columns(3)
            m1.metric("粉絲規模（真實/估算）", f"{base_followers:,} Followers")
            m2.metric("追蹤中數（真實/估算）", f"{base_following:,}")
            m3.metric("模擬互動率（演示）", f"{base_er}%")

            if risk_score >= 70:
                st.error(f"🚨 **判定結果：高風險特徵帳號** | 綜合風險指數（含模擬演示成分）：{risk_score}%")
            elif risk_score >= 40:
                st.warning(f"⚠️ **判定結果：中度異常特徵** | 綜合風險指數（含模擬演示成分）：{risk_score}%")
            else:
                st.success(f"✅ **判定結果：特徵表現正常** | 綜合風險指數（含模擬演示成分）：{risk_score}%")
            st.progress(risk_score / 100)
            st.caption("註：風險指數結合「真實抓取特徵」（如頭像、簡介、粉絲/追蹤比）與「演算法模擬特徵」（如互動率、下游殭屍帳號比例）。前者真實，後者為演示推導值，請勿作為實際商業決策依據。")

            col_left, col_right = st.columns([1, 1])

            with col_left:
                st.subheader("🔬 判定依據細項")
                if risk_details:
                    for detail in risk_details:
                        st.markdown(detail)
                else:
                    st.markdown("✨ **全指標表現正常**：各項真實與模擬維度均符合常態分佈。")

                st.subheader("🏆 模擬下游節點 PageRank 排行")
                st.dataframe(ig_df.head(10), use_container_width=True)

            with col_right:
                st.subheader("📊 模擬節點屬性權重佔比")
                fig_ig_pie = px.pie(
                    ig_df,
                    values="PageRank 權重值",
                    names="節點屬性",
                    hole=0.4,
                    color="節點屬性",
                    color_discrete_map={
                        "主審查標的（真實帳號）": "#ef4444",
                        "模擬高危特徵": "#ffb74d",
                        "模擬正常用戶": "#60a5fa"
                    }
                )
                st.plotly_chart(fig_ig_pie, use_container_width=True)

            st.subheader("🌳 模擬下游關係拓樸網絡圖")
            st.info("💡 🔴 紅色為輸入的真實帳號；🟠 橘色為演算法模擬出的「高危特徵節點」；🔵 藍色為「模擬正常節點」。此圖為演算法概念演示。")

            net_ig = Network(height="550px", width="100%", bgcolor="#f8fafc", font_color="#1e293b", directed=True)

            for _, row in ig_df.iterrows():
                node_id = row["節點"]
                attr = row["節點屬性"]

                n_color = "#ef4444" if "主審查" in attr else ("#ffb74d" if "高危" in attr else "#60a5fa")
                n_size = 40 if "主審查" in attr else 20
                net_ig.add_node(node_id, label=node_id, size=n_size, color=n_color)

            for u, v in IG_G.edges():
                net_ig.add_edge(u, v, color="#cbd5e1", arrows="to")

            net_ig.toggle_physics(True)
            net_ig.set_options('{"physics": {"forceAtlas2Based": {"gravitationalConstant": -60, "centralGravity": 0.015, "springLength": 100}, "solver": "forceAtlas2Based"}}')

            try:
                net_ig.save_graph("ig_graph.html")
                with open("ig_graph.html", "r", encoding="utf-8") as f:
                    components.html(f.read(), height=570)
            except Exception:
                st.error("拓樸圖渲染失敗。")

            st.markdown("""
            ---
            💡 **方法論說明**：本系統第一層特徵（粉絲/追蹤比、頭像狀態、簡介內容）來自 Instagram 公開頁面的真實資料；
            第二層下游拓樸圖則以這些真實特徵作為隨機種子，透過 PageRank 演算法推導出「符合該帳號規模特徵」的可能死網結構，
            用於展示圖論在社交網絡異常偵測中的應用方式，**並非對個別粉絲帳號的真實調查**。
            """)
